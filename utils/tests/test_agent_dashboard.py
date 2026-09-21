"""Offline dashboard/exporter contract checks; no external API or Kafka needed."""

import json
from pathlib import Path
import re
from unittest.mock import Mock

from prometheus_client import REGISTRY
import pytest

from agents.extraction import ExtractionConfig, ExtractionService
from agents.providers import DisabledProvider
from agents.stress_metrics import StressMetrics
from agents.worker import AIWorker, WorkerConfig
import processing.kafka_to_mongo  # Registers the existing Processor metrics.
import utils.metrics  # Registers the existing shared trainer/Processor metrics.


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "monitoring/grafana/dashboards/agent_operations.json"
SELECTOR = re.compile(r"\b([a-zA-Z_:][a-zA-Z0-9_:]*)\{([^{}]*)\}")


@pytest.fixture
def dashboard():
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def targets(dashboard):
    return [target for panel in dashboard["panels"] for target in panel.get("targets", [])]


def exported_names(registry):
    names = set()
    for family in registry.collect():
        names.update(sample.name for sample in family.samples)
        # Labelled counters have no samples before the first event, but their
        # descriptors still establish the exported metric contract.
        if family.type == "counter":
            names.add(family.name + "_total")
        elif family.type == "histogram":
            names.update(family.name + suffix for suffix in ("_bucket", "_count", "_sum"))
        else:
            names.add(family.name)
    return names


def test_dashboard_panels_have_unique_ids_and_real_metric_names(dashboard):
    worker = AIWorker(WorkerConfig(), ExtractionService(DisabledProvider()))
    stress = StressMetrics()
    names = {"up"}  # Added by Prometheus itself, not application exporters.
    for registry in (REGISTRY, worker.extraction.metrics.registry, stress.registry):
        names.update(exported_names(registry))
    panels = dashboard["panels"]
    assert len({panel["id"] for panel in panels}) == len(panels)
    for target in targets(dashboard):
        selectors = SELECTOR.findall(target["expr"])
        assert selectors, target["expr"]
        assert {name for name, _ in selectors} <= names, target["expr"]
        assert target["datasource"]["uid"] == "Prometheus"


def test_confidence_selectors_match_actual_exported_bucket_labels(dashboard):
    service = ExtractionService(DisabledProvider())
    buckets = {
        sample.labels["le"]
        for family in service.metrics.registry.collect()
        for sample in family.samples
        if sample.name == "ai_extraction_confidence_bucket"
    }
    selected = []
    for target in targets(dashboard):
        for name, labels in SELECTOR.findall(target["expr"]):
            if name == "ai_extraction_confidence_bucket":
                bound = re.search(r'le="([^"]+)"', labels)
                assert bound is not None
                selected.append(bound[1])
                assert bound[1] in buckets, target["expr"]
    assert {"0.25", "0.5", "0.75", "0.9", "1.0"} <= set(selected)


def test_dashboard_job_selectors_match_prometheus_targets(dashboard):
    config = (ROOT / "monitoring/prometheus.yml").read_text(encoding="utf-8")
    jobs = re.findall(r"job_name:\s*['\"]([^'\"]+)['\"]", config)
    assert jobs
    for target in targets(dashboard):
        for _, labels in SELECTOR.findall(target["expr"]):
            for operator, value in re.findall(r'job(=~|=)"([^"]+)"', labels):
                if operator == "=":
                    assert value in jobs, target["expr"]
                else:
                    assert any(re.fullmatch(value, job) for job in jobs), target["expr"]


def test_confidence_threshold_is_exported_from_configuration(monkeypatch, dashboard):
    monkeypatch.setenv("AI_ENABLED", "false")
    monkeypatch.setenv("AI_MIN_CONFIDENCE", "0.82")
    service = ExtractionService.from_env()
    assert service.metrics.registry.get_sample_value("ai_extraction_confidence_threshold") == .82
    assert any(target["expr"] == 'ai_extraction_confidence_threshold{job="ai-agent"}'
               for target in targets(dashboard))


@pytest.mark.parametrize("confidence,status,error", [
    (.74, "failed", "low_confidence"),
    (.75, "success", None),
    (1., "success", None),
    (1.1, "failed", "invalid_schema"),
])
def test_confidence_measurement_matches_acceptance_gate(confidence, status, error):
    provider = Mock(enabled=True, name="offline_dashboard_fixture", model="fixture")
    provider.extract.return_value = json.dumps({
        "fields": {"area_m2": 80.0}, "confidence": confidence,
        "evidence": {"area_m2": "80 m2"},
    })
    service = ExtractionService(provider, ExtractionConfig(confidence_threshold=.75))
    result = service.extract({"description": "Area: 80 m2"})
    assert result["status"] == status
    assert result["error_code"] == error
    # Schema-invalid output has no accepted confidence observation; schema-valid
    # low confidence is observed before rejection and must appear in the graph.
    assert service.metrics.registry.get_sample_value("ai_extraction_confidence_count") == (
        0 if error == "invalid_schema" else 1
    )
    if error == "low_confidence":
        assert service.metrics.registry.get_sample_value(
            "ai_extraction_failure_total", {"reason": "low_confidence"}
        ) == 1


def test_dashboard_does_not_present_missing_metrics_as_success(dashboard):
    for target in targets(dashboard):
        assert "or vector(0)" not in target["expr"]
    for panel in dashboard["panels"]:
        if panel["type"] == "stat":
            assert panel["fieldConfig"]["defaults"]["noValue"] == "No data"


def test_grafana_provisioning_uses_dashboard_datasource():
    source = (ROOT / "monitoring/grafana/provisioning/datasources/prometheus.yml").read_text(encoding="utf-8")
    provider = (ROOT / "monitoring/grafana/provisioning/dashboards/pipeline.yml").read_text(encoding="utf-8")
    assert "uid: Prometheus" in source
    assert "url: http://prometheus:9090" in source
    assert "path: /var/lib/grafana/dashboards" in provider
