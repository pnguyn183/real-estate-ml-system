"""Group-based IQR price anomaly detection for normalized property listings.

The detector intentionally operates on historical ``training_features`` records
before the currently consumed listing is persisted.  This keeps a new listing
from influencing the threshold used to classify itself.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from numbers import Real
from typing import Any, Iterable, Mapping, Sequence

from utils.logging_utils import log_structured


PRICE_PER_M2_FIELD = "price_per_m2_vnd"
TRAINING_POLICIES = {"KEEP", "FLAG", "EXCLUDE"}


def _parse_columns(value: str, default: Sequence[str]) -> tuple[str, ...]:
    columns = tuple(item.strip() for item in value.split(",") if item.strip())
    return columns or tuple(default)


def _is_finite_positive(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0


def safe_price_per_m2(total_price_vnd: Any, area_m2: Any) -> float | None:
    """Return a finite positive VND/m² value, or ``None`` for invalid inputs."""
    if not _is_finite_positive(total_price_vnd) or not _is_finite_positive(area_m2):
        return None
    value = float(total_price_vnd) / float(area_m2)
    return value if math.isfinite(value) and value > 0 else None


def get_anomaly_training_policy(value: str | None = None) -> str:
    """Read and validate the policy used when anomaly-marked listings are trained."""
    policy = (value or os.environ.get("PRICE_ANOMALY_TRAINING_POLICY", "FLAG")).strip().upper()
    if policy not in TRAINING_POLICIES:
        raise ValueError(f"PRICE_ANOMALY_TRAINING_POLICY must be one of {sorted(TRAINING_POLICIES)}")
    return policy


def add_anomaly_training_filter(query: Mapping[str, Any], policy: str | None = None) -> dict[str, Any]:
    """Add the opt-in exclusion predicate while keeping older documents usable."""
    filtered_query = dict(query)
    if get_anomaly_training_policy(policy) == "EXCLUDE":
        filtered_query["is_price_anomaly"] = {"$ne": True}
    return filtered_query


@dataclass(frozen=True)
class PriceAnomalyConfig:
    """Runtime configuration for historical group-based IQR detection."""

    iqr_multiplier: float = 1.5
    min_group_size: int = 30
    primary_group_columns: tuple[str, ...] = ("province_slug", "district_slug", "property_type")
    fallback_group_columns: tuple[str, ...] = ("province_slug", "property_type")
    refresh_seconds: int = 300

    @classmethod
    def from_env(cls) -> "PriceAnomalyConfig":
        config = cls(
            iqr_multiplier=float(os.environ.get("PRICE_ANOMALY_IQR_MULTIPLIER", "1.5")),
            min_group_size=int(os.environ.get("PRICE_ANOMALY_MIN_GROUP_SIZE", "30")),
            primary_group_columns=_parse_columns(
                os.environ.get("PRICE_ANOMALY_GROUP_COLUMNS", "province_slug,district_slug,property_type"),
                cls.primary_group_columns,
            ),
            fallback_group_columns=_parse_columns(
                os.environ.get("PRICE_ANOMALY_FALLBACK_GROUP_COLUMNS", "province_slug,property_type"),
                cls.fallback_group_columns,
            ),
            refresh_seconds=int(os.environ.get("PRICE_ANOMALY_REFRESH_SECONDS", "300")),
        )
        if config.iqr_multiplier <= 0:
            raise ValueError("PRICE_ANOMALY_IQR_MULTIPLIER must be positive")
        if config.min_group_size < 2:
            raise ValueError("PRICE_ANOMALY_MIN_GROUP_SIZE must be at least 2")
        if config.refresh_seconds < 0:
            raise ValueError("PRICE_ANOMALY_REFRESH_SECONDS must be non-negative")
        return config

    @property
    def config_id(self) -> str:
        payload = {
            "iqr_multiplier": self.iqr_multiplier,
            "min_group_size": self.min_group_size,
            "primary_group_columns": self.primary_group_columns,
            "fallback_group_columns": self.fallback_group_columns,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class GroupThreshold:
    group_columns: tuple[str, ...]
    group_values: tuple[str, ...]
    sample_size: int
    q1: float
    q3: float
    iqr: float
    lower_bound: float | None
    upper_bound: float | None
    status: str
    calculated_at: str

    @property
    def key(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self.group_columns, self.group_values

    def to_document(self, config_id: str) -> dict[str, Any]:
        return {
            "config_id": config_id,
            "group_key": self.group_key,
            "group_columns": list(self.group_columns),
            "group_values": dict(zip(self.group_columns, self.group_values)),
            "sample_size": self.sample_size,
            "q1": self.q1,
            "q3": self.q3,
            "iqr": self.iqr,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "status": self.status,
            "calculated_at": self.calculated_at,
        }

    @property
    def group_key(self) -> str:
        return json.dumps(dict(zip(self.group_columns, self.group_values)), ensure_ascii=False, sort_keys=True)


@dataclass
class BaselineSummary:
    records_processed: int = 0
    valid_price_per_m2_records: int = 0
    groups_seen: int = 0
    usable_groups: int = 0
    insufficient_groups: int = 0
    zero_iqr_groups: int = 0
    refreshed_at: str | None = None


@dataclass
class PriceAnomalyDetector:
    """Build historical IQR thresholds and annotate newly normalized listings."""

    config: PriceAnomalyConfig = field(default_factory=PriceAnomalyConfig.from_env)
    thresholds: dict[tuple[tuple[str, ...], tuple[str, ...]], GroupThreshold] = field(default_factory=dict)
    baseline_summary: BaselineSummary = field(default_factory=BaselineSummary)
    processed_records: int = 0
    valid_price_per_m2_records: int = 0
    anomaly_count: int = 0
    skipped_records: int = 0
    _last_refresh_monotonic: float | None = None

    def fit(self, reference_records: Iterable[Mapping[str, Any]], exclude_url: str | None = None) -> BaselineSummary:
        """Calculate thresholds from historical records, excluding the incoming URL."""
        records = [record for record in reference_records if not exclude_url or record.get("url") != exclude_url]
        summary = BaselineSummary(records_processed=len(records), refreshed_at=self._timestamp())
        self.thresholds.clear()
        if not records:
            self.baseline_summary = summary
            self._last_refresh_monotonic = time.monotonic()
            self._log_baseline_summary(summary)
            return summary

        grouped_values: dict[tuple[str, ...], dict[tuple[str, ...], list[float]]] = {
            columns: {} for columns in self._grouping_levels()
        }
        # A single O(N × grouping-levels) baseline pass is cached for refresh_seconds;
        # sorting occurs only per group to calculate exact linear percentiles.
        for record in records:
            price_per_m2 = safe_price_per_m2(record.get("price_vnd"), record.get("area_m2"))
            if price_per_m2 is None:
                continue
            summary.valid_price_per_m2_records += 1
            for columns, groups in grouped_values.items():
                values = self._group_values(record, columns)
                if values is not None:
                    groups.setdefault(values, []).append(price_per_m2)

        for columns, groups in grouped_values.items():
            self._build_thresholds(groups, columns, summary)

        self.baseline_summary = summary
        self._last_refresh_monotonic = time.monotonic()
        self._log_baseline_summary(summary)
        return summary

    def refresh_if_needed(self, feature_collection: Any, threshold_collection: Any, exclude_url: str | None = None) -> bool:
        """Refresh cache from Mongo only when stale, then persist thresholds for audit."""
        now = time.monotonic()
        if self._last_refresh_monotonic is not None and now - self._last_refresh_monotonic < self.config.refresh_seconds:
            return False

        projection = {"_id": 0, "url": 1, "price_vnd": 1, "area_m2": 1}
        for column in set(self.config.primary_group_columns + self.config.fallback_group_columns):
            projection[column] = 1
        records = list(feature_collection.find({"price_vnd": {"$gt": 0}, "area_m2": {"$gt": 0}}, projection))
        self.fit(records, exclude_url=exclude_url)
        self.persist_thresholds(threshold_collection)
        return True

    def persist_thresholds(self, threshold_collection: Any) -> None:
        """Persist current group statistics as auditable baseline snapshots."""
        for threshold in self.thresholds.values():
            document = threshold.to_document(self.config.config_id)
            threshold_collection.update_one(
                {"config_id": self.config.config_id, "group_key": threshold.group_key},
                {"$set": document},
                upsert=True,
            )

    def annotate(self, record: dict[str, Any]) -> dict[str, Any]:
        """Add explainable anomaly fields.  Records are never deleted or mutated in raw storage."""
        self.processed_records += 1
        price_per_m2 = safe_price_per_m2(record.get("price_vnd"), record.get("area_m2"))
        record[PRICE_PER_M2_FIELD] = price_per_m2
        metadata = self._base_metadata(price_per_m2)
        if price_per_m2 is None:
            self.skipped_records += 1
            metadata.update(
                {
                    "price_anomaly_status": "UNAVAILABLE",
                    "price_anomaly_reason": "invalid_price_or_area",
                }
            )
            record.update(metadata)
            return record

        self.valid_price_per_m2_records += 1
        group = self._matching_threshold(record)
        if group is None:
            self.skipped_records += 1
            metadata.update(
                {
                    "price_anomaly_status": "UNAVAILABLE",
                    "price_anomaly_reason": self._unavailable_reason(record),
                }
            )
            record.update(metadata)
            return record

        metadata.update(self._threshold_metadata(group))
        if group.status == "zero_iqr":
            self.skipped_records += 1
            metadata.update(
                {
                    "price_anomaly_status": "UNAVAILABLE",
                    "price_anomaly_reason": "zero_iqr_baseline",
                }
            )
            record.update(metadata)
            return record

        if group.status != "ready" or group.lower_bound is None or group.upper_bound is None:
            self.skipped_records += 1
            metadata.update(
                {
                    "price_anomaly_status": "UNAVAILABLE",
                    "price_anomaly_reason": "insufficient_baseline_group",
                }
            )
            record.update(metadata)
            return record

        anomaly_type = "HIGH" if price_per_m2 > group.upper_bound else "LOW" if price_per_m2 < group.lower_bound else None
        if anomaly_type:
            self.anomaly_count += 1
            boundary = group.upper_bound if anomaly_type == "HIGH" else group.lower_bound
            metadata.update(
                {
                    "is_price_anomaly": True,
                    "price_anomaly_status": "FLAGGED",
                    "price_anomaly_type": anomaly_type,
                    "price_anomaly_score": (abs(price_per_m2 - boundary) / group.iqr) if group.iqr > 0 else None,
                    "price_anomaly_reason": f"price_per_m2_{anomaly_type.lower()}_outside_iqr_bounds",
                }
            )
        else:
            metadata.update({"price_anomaly_status": "NORMAL", "price_anomaly_reason": None})
        record.update(metadata)
        return record

    def metrics(self) -> dict[str, float | int]:
        anomaly_percentage = (self.anomaly_count / self.valid_price_per_m2_records * 100) if self.valid_price_per_m2_records else 0.0
        return {
            "records_processed": self.processed_records,
            "valid_price_per_m2_records": self.valid_price_per_m2_records,
            "groups": self.baseline_summary.groups_seen,
            "anomalies": self.anomaly_count,
            "anomaly_percentage": round(anomaly_percentage, 4),
            "skipped_records": self.skipped_records,
            "insufficient_groups": self.baseline_summary.insufficient_groups,
            "zero_iqr_groups": self.baseline_summary.zero_iqr_groups,
        }

    def _build_thresholds(
        self,
        grouped_values: Mapping[tuple[str, ...], list[float]],
        columns: tuple[str, ...],
        summary: BaselineSummary,
    ) -> None:
        for group_values, prices in grouped_values.items():
            ordered_prices = sorted(prices)
            sample_size = len(ordered_prices)
            q1 = self._linear_quantile(ordered_prices, 0.25)
            q3 = self._linear_quantile(ordered_prices, 0.75)
            iqr = q3 - q1
            status = "ready"
            lower_bound: float | None = None
            upper_bound: float | None = None
            if sample_size < self.config.min_group_size:
                status = "insufficient_data"
                summary.insufficient_groups += 1
            elif iqr <= 0:
                status = "zero_iqr"
                summary.zero_iqr_groups += 1
            else:
                lower_bound = q1 - self.config.iqr_multiplier * iqr
                upper_bound = q3 + self.config.iqr_multiplier * iqr
                summary.usable_groups += 1
            threshold = GroupThreshold(
                group_columns=columns,
                group_values=group_values,
                sample_size=sample_size,
                q1=q1,
                q3=q3,
                iqr=iqr,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                status=status,
                calculated_at=summary.refreshed_at or self._timestamp(),
            )
            self.thresholds[threshold.key] = threshold
            summary.groups_seen += 1

    @staticmethod
    def _linear_quantile(sorted_values: Sequence[float], quantile: float) -> float:
        """Match the common linear percentile definition without a numerical dependency."""
        position = (len(sorted_values) - 1) * quantile
        lower_index = math.floor(position)
        upper_index = math.ceil(position)
        if lower_index == upper_index:
            return float(sorted_values[lower_index])
        lower = sorted_values[lower_index]
        upper = sorted_values[upper_index]
        return float(lower + (upper - lower) * (position - lower_index))

    def _grouping_levels(self) -> tuple[tuple[str, ...], ...]:
        levels = [self.config.primary_group_columns]
        if self.config.fallback_group_columns != self.config.primary_group_columns:
            levels.append(self.config.fallback_group_columns)
        return tuple(levels)

    def _matching_threshold(self, record: Mapping[str, Any]) -> GroupThreshold | None:
        unavailable_threshold: GroupThreshold | None = None
        for columns in self._grouping_levels():
            values = self._group_values(record, columns)
            if values is not None:
                threshold = self.thresholds.get((columns, values))
                if threshold is not None:
                    if threshold.status == "ready":
                        return threshold
                    # A wider group can still provide a reliable reference when the more
                    # specific district group is too small or has zero variance.
                    unavailable_threshold = unavailable_threshold or threshold
        return unavailable_threshold

    @staticmethod
    def _group_values(record: Mapping[str, Any], columns: tuple[str, ...]) -> tuple[str, ...] | None:
        values = []
        for column in columns:
            value = record.get(column)
            if value is None or not str(value).strip():
                return None
            values.append(str(value))
        return tuple(values)

    def _unavailable_reason(self, record: Mapping[str, Any]) -> str:
        if self._group_values(record, self.config.primary_group_columns) is None and self._group_values(record, self.config.fallback_group_columns) is None:
            return "missing_group_values"
        return "insufficient_baseline_group"

    def _base_metadata(self, price_per_m2: float | None) -> dict[str, Any]:
        return {
            "is_price_anomaly": False,
            "price_anomaly_type": None,
            "price_anomaly_score": None,
            "price_anomaly_detection_available": False,
            "price_anomaly_group": None,
            "price_anomaly_group_columns": None,
            "price_anomaly_baseline_size": None,
            "price_anomaly_q1": None,
            "price_anomaly_q3": None,
            "price_anomaly_iqr": None,
            "price_anomaly_lower_bound": None,
            "price_anomaly_upper_bound": None,
            "price_anomaly_baseline_updated_at": self.baseline_summary.refreshed_at,
            PRICE_PER_M2_FIELD: price_per_m2,
        }

    @staticmethod
    def _threshold_metadata(threshold: GroupThreshold) -> dict[str, Any]:
        return {
            "price_anomaly_detection_available": threshold.status == "ready",
            "price_anomaly_group": dict(zip(threshold.group_columns, threshold.group_values)),
            "price_anomaly_group_columns": list(threshold.group_columns),
            "price_anomaly_baseline_size": threshold.sample_size,
            "price_anomaly_q1": threshold.q1,
            "price_anomaly_q3": threshold.q3,
            "price_anomaly_iqr": threshold.iqr,
            "price_anomaly_lower_bound": threshold.lower_bound,
            "price_anomaly_upper_bound": threshold.upper_bound,
            "price_anomaly_baseline_updated_at": threshold.calculated_at,
        }

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _log_baseline_summary(summary: BaselineSummary) -> None:
        log_structured(
            logging.INFO,
            "price_anomaly_baseline_refreshed",
            records_processed=summary.records_processed,
            valid_price_per_m2_records=summary.valid_price_per_m2_records,
            groups=summary.groups_seen,
            usable_groups=summary.usable_groups,
            insufficient_groups=summary.insufficient_groups,
            zero_iqr_groups=summary.zero_iqr_groups,
        )
