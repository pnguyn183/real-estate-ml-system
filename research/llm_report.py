"""Conservative evidence for the real LLM rate-only experiment.

This module only reads receipts. A model's explanation is not proof that its
action worked. Demonstration requires three matched pairs, measured overload,
an applied model decrease, lower ingress, and recovery under unchanged demand.
The resulting verdict describes this workload and these Docker limits only.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any


COUNT_FIELDS = ("offered", "admitted", "deferred", "rejected", "acknowledged", "committed",
                "delivery_failed", "enqueue_failed", "pending")
NOTES = [
    "Results apply to the tested workload and fixed Docker limits, not an absolute machine capacity.",
    "Reduced broker load can trade rejected or deferred demand for stability; all offered work remains in the denominator.",
    "Kafka interval p95 excludes waiting before admission. Offered-to-commit p95 is a polling upper bound for observed completions only.",
    "Interval p95 values are not pooled into a global p95. Violation duration is estimated from sampled intervals.",
    "A chronological action/response chain supports attribution in matched trials; it does not establish superiority over a rule controller.",
]


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _timestamp(row: dict) -> float | None:
    value = row.get("applied_at", row.get("timestamp"))
    return float(value) if finite(value) else None


def _rows(run: dict) -> list[dict]:
    return sorted((row for row in run.get("samples", []) if finite(row.get("timestamp"))),
                  key=lambda row: row["timestamp"])


def _load(row: dict, run: dict) -> bool:
    stamp = row["timestamp"]
    return (row.get("phase", "load") == "load"
            and (not finite(run.get("load_started_at")) or stamp >= run["load_started_at"])
            and (not finite(run.get("load_ended_at")) or stamp <= run["load_ended_at"]))


def _overload_stage(row: dict) -> bool:
    return row.get("is_overload") is True or row.get("stage") == "overload"


def _usable(row: dict) -> bool:
    keys = ("kafka_lag", "incoming_rate", "throughput", "offered_rate")
    return (not row.get("errors") and not row.get("source_errors")
            and row.get("instrumentation_available", True) is not False
            and row.get("instrumentation_ready") is True and row.get("instrument_ready", True) is not False
            and all(finite(row.get(key)) and row[key] >= 0 for key in keys)
            and (row["throughput"] == 0 or
                 all(finite(row.get(key)) and row[key] >= 0 for key in ("latency_p95_seconds", "error_rate"))))


def _interval(row: dict, run: dict) -> float:
    value = row.get("interval_seconds", run.get("sample_seconds", 5.0))
    return float(value) if finite(value) and value > 0 else 5.0


def _windows(rows: list[dict], run: dict, size: int):
    for index in range(len(rows) - size + 1):
        window = rows[index:index + size]
        if not all(_usable(row) for row in window):
            continue
        if any(row.get("stage") != window[0].get("stage") for row in window):
            continue
        if any(not 0 < right["timestamp"] - left["timestamp"] <= 2.5 * _interval(right, run)
               for left, right in zip(window, window[1:])):
            continue
        yield window


def _stress_reason(window: list[dict], target: float) -> list[str]:
    reasons = []
    if all(finite(row.get("latency_p95_seconds")) and row["latency_p95_seconds"] > target
           for row in window):
        reasons.append("sustained_latency_violation")
    if (all(row["kafka_lag"] > 0 for row in window)
            and window[-1]["kafka_lag"] - window[0]["kafka_lag"] >= 20
            and all(right["kafka_lag"] > left["kafka_lag"] for left, right in zip(window, window[1:]))):
        reasons.append("sustained_growing_lag")
    return reasons


def _is_model(action: dict) -> bool:
    return (action.get("action_origin") == "llm" and action.get("status") == "applied"
            and bool(action.get("provider")) and bool(action.get("model"))
            and isinstance(action.get("decision"), dict)
            and finite(action["decision"].get("rate_per_second")))


def _is_guard(action: dict) -> bool:
    return (action.get("action_origin") != "shutdown"
            and (action.get("action_origin") == "guard" or action.get("status") in {"paused", "would_pause"}))


def _changes_control(action: dict) -> bool:
    """Unknown/manual/fixed actions can confound attribution just like a guard."""
    decision = action.get("decision")
    return (_is_guard(action) or action.get("action_origin") == "shutdown" or action.get("status") == "applied"
            or isinstance(decision, dict) and finite(decision.get("rate_per_second"))
            and action.get("status") not in {"proposed", "observed"})


def _recovery(window: list[dict], target: float) -> bool:
    return all(row["kafka_lag"] == 0 and row["incoming_rate"] > 0 and row["throughput"] > 0
               and finite(row.get("admission_limit")) and row["admission_limit"] > 0
               and finite(row.get("latency_p95_seconds")) and row["latency_p95_seconds"] <= target
               and row.get("error_rate") == 0 for row in window)


def adaptation_chains(run: dict, *, target_latency_seconds: float = 2.0,
                      sustained_samples: int = 3) -> list[dict]:
    """Find measured model decrease -> ingress reduction -> loaded recovery.

    All recovery samples must retain the offered rate present before the action.
    Recovery after a profile step-down, after shutdown, or behind a guard does
    not qualify. A zero-rate action can qualify only after positive service
    actually resumes, with no intervening guard.
    """
    rows = [row for row in _rows(run) if _load(row, run)]
    actions = sorted((action for action in run.get("actions", []) if _timestamp(action) is not None),
                     key=_timestamp)
    previous_rate = run.get("initial_rate")
    chains = []
    for action in actions:
        if not _is_model(action):
            if _changes_control(action):
                decision = action.get("decision")
                previous_rate = decision.get("rate_per_second") if isinstance(decision, dict) else None
            continue
        stamp, rate = _timestamp(action), action["decision"]["rate_per_second"]
        decreased = finite(previous_rate) and rate < previous_rate
        before_rate, previous_rate = previous_rate, rate
        if not decreased:
            continue
        before = [row for row in rows if row["timestamp"] <= stamp and _overload_stage(row)]
        if not before or not _usable(before[-1]):
            continue
        prior = before[-1]
        if stamp - prior["timestamp"] > 2.5 * _interval(prior, run):
            continue
        if not (prior["kafka_lag"] > 0 or
                (finite(prior.get("latency_p95_seconds")) and prior["latency_p95_seconds"] > target_latency_seconds)):
            continue
        offered = prior["offered_rate"]
        after = [row for row in rows if row["timestamp"] > stamp]
        # Stop at the first change in demand/stage; later equal rates are another episode.
        same_demand = []
        for row in after:
            if not _overload_stage(row) or row.get("stage") != prior.get("stage") or row.get("offered_rate") != offered:
                break
            same_demand.append(row)
        for window in _windows(same_demand, run, sustained_samples):
            end = window[-1]["timestamp"]
            intervening = [item for item in actions if item is not action and stamp <= _timestamp(item) <= end]
            if any(_changes_control(item) and not _is_model(item) for item in intervening):
                break
            # A closed gate cannot earn credit for residual messages already in
            # transit. It must have a subsequent model receipt that resumes it.
            if rate == 0 and not any(_is_model(item) and item["decision"]["rate_per_second"] > 0
                                     and _timestamp(item) <= window[0]["timestamp"] for item in intervening):
                continue
            if not _recovery(window, target_latency_seconds):
                continue
            after_ingress = mean(row["incoming_rate"] for row in window)
            # Beyond trivial numeric noise, ingress must demonstrably respond.
            if not 0 < after_ingress < prior["incoming_rate"] * .95:
                continue
            chains.append({"decision_id": action.get("decision_id"), "provider": action["provider"],
                           "model": action["model"], "reason": action["decision"].get("reason"),
                           "action_at": stamp, "rate_before": before_rate, "rate_after": rate,
                           "offered_rate": offered, "ingress_before": prior["incoming_rate"],
                           "ingress_after": after_ingress, "lag_before": prior["kafka_lag"],
                           "recovery_started_at": window[0]["timestamp"], "recovery_confirmed_at": end,
                           "recovery_seconds": window[0]["timestamp"] - stamp,
                           "followup_llm_decision_ids": [item.get("decision_id") for item in intervening if _is_model(item)],
                           "recovery_phase": "same_offered_overload"})
            break
    return chains


def summarize_run(run: dict, *, target_latency_seconds: float = 2.0,
                  sustained_samples: int = 3) -> dict:
    rows = _rows(run)
    high = [row for row in rows if _load(row, run) and _overload_stage(row)]
    episodes = []
    for window in _windows(high, run, sustained_samples):
        reasons = _stress_reason(window, target_latency_seconds)
        if run.get("mode") == "baseline" and not all(row["incoming_rate"] >= .8 * row["offered_rate"] > 0 for row in window):
            reasons = []
        if reasons:
            episodes.append({"start_at": window[0]["timestamp"], "confirmed_at": window[-1]["timestamp"],
                             "reasons": reasons})
    valid = [row for row in high if _usable(row)]
    latency = [row for row in valid if finite(row.get("latency_p95_seconds"))]
    actions = run.get("actions", [])
    guards = [action for action in actions if _is_guard(action)]
    under_load_guards = [action for action in guards if _timestamp(action) is not None
                        and finite(run.get("load_started_at")) and finite(run.get("load_ended_at"))
                        and run["load_started_at"] <= _timestamp(action) <= run["load_ended_at"]]
    account = run.get("accounting", {})
    counts = {key: account.get(key) for key in COUNT_FIELDS}
    for key in ("queued", "inflight", "awaiting_delivery", "awaiting_commit", "latency_count",
                "latency_missing", "cancelled_future_offers", "scheduler_shortfall"):
        if key in account:
            counts[key] = account[key]
    offered, committed = counts["offered"], counts["committed"]
    failures = (counts["delivery_failed"] + counts["enqueue_failed"]
                if finite(counts["delivery_failed"]) and finite(counts["enqueue_failed"]) else None)
    chains = adaptation_chains(run, target_latency_seconds=target_latency_seconds,
                               sustained_samples=sustained_samples)
    return {"pair_id": run.get("pair_id"), "run_id": run.get("run_id"), "mode": run.get("mode"),
            "status": run.get("status"), "accounting": counts,
            "load_generator": {key: account.get(key) for key in ("planned", "workload_fingerprint",
                                "scheduler_lateness_p95_seconds", "scheduler_lateness_max_seconds", "errors", "rate_read_errors")},
            "served_fraction": committed / offered if finite(offered) and offered > 0 and finite(committed) else None,
            "delivery_or_enqueue_failures": failures,
            "delivery_or_enqueue_failure_fraction": failures / offered if finite(offered) and offered > 0 and finite(failures) else None,
            "offered_to_commit_p95_upper_seconds": account.get("offered_to_commit_p95_upper_seconds"),
            "completion_observer": run.get("completion_observer", {}),
            "overload_observed": bool(episodes), "overload_windows": episodes,
            "overload_sample_count": len(high), "usable_overload_sample_count": len(valid),
            "overload_peak_lag": max((row["kafka_lag"] for row in valid), default=None),
            "overload_peak_interval_p95_seconds": max((row["latency_p95_seconds"] for row in latency), default=None),
            "overload_latency_violation_seconds": sum(_interval(row, run) for row in latency
                                                       if row["latency_p95_seconds"] > target_latency_seconds),
            "overload_latency_observed_seconds": sum(_interval(row, run) for row in latency),
            "overload_latency_missing_samples": len(high) - len(latency),
            "overload_peak_error_rate": max((row["error_rate"] for row in valid if finite(row.get("error_rate"))), default=None),
            "guard_count": len(guards), "guards_under_load": len(under_load_guards),
            "guard_reasons": sorted({str(action.get("reason", "unspecified")) for action in guards}),
            "applied_llm_decisions": sum(_is_model(action) for action in actions),
            "adaptation_chains": chains}


def _pair_reasons(baseline: dict, llm: dict, summaries: dict, sustained_samples: int) -> list[str]:
    reasons = []
    for mode, run in (("baseline", baseline), ("llm", llm)):
        summary = summaries[mode]
        if run.get("status") != "completed":
            reasons.append(f"{mode}_not_completed")
        if run.get("training_disabled") is not True:
            reasons.append(f"{mode}_training_not_verified_disabled")
        if not finite(run.get("load_started_at")) or not finite(run.get("load_ended_at")):
            reasons.append(f"{mode}_missing_load_times")
        elif run["load_ended_at"] <= run["load_started_at"]:
            reasons.append(f"{mode}_invalid_load_times")
        observer = run.get("completion_observer", {})
        if (observer.get("errors") != [] or not finite(observer.get("max_poll_gap_seconds"))
                or observer["max_poll_gap_seconds"] < 0):
            reasons.append(f"{mode}_incomplete_completion_observation")
        if run.get("cleanup_errors"):
            reasons.append(f"{mode}_cleanup_errors")
        if summary["guards_under_load"]:
            reasons.append(f"{mode}_guard_confounded")
        if summary["overload_sample_count"] < sustained_samples:
            reasons.append(f"{mode}_insufficient_overload_samples")
        if summary["usable_overload_sample_count"] != summary["overload_sample_count"]:
            reasons.append(f"{mode}_incomplete_telemetry")
        if summary["overload_latency_missing_samples"]:
            reasons.append(f"{mode}_incomplete_latency_coverage")
        high = [row for row in _rows(run) if _load(row, run) and _overload_stage(row)]
        if any(right["timestamp"] - left["timestamp"] > 2.5 * _interval(right, run)
               for left, right in zip(high, high[1:]) if left.get("stage") == right.get("stage")):
            reasons.append(f"{mode}_telemetry_gap")
        if any(right["timestamp"] <= left["timestamp"] for left, right in zip(high, high[1:])):
            reasons.append(f"{mode}_duplicate_telemetry_timestamps")
        if any(_is_guard(action) and _timestamp(action) is None for action in run.get("actions", [])):
            reasons.append(f"{mode}_undated_guard_receipt")
        if any(_changes_control(action) and _timestamp(action) is None for action in run.get("actions", [])):
            reasons.append(f"{mode}_undated_control_receipt")
        counts = summary["accounting"]
        generator = summary["load_generator"]
        if not finite(generator["planned"]) or generator["planned"] != counts["offered"]:
            reasons.append(f"{mode}_incomplete_planned_schedule")
        if generator["errors"] != [] or generator["rate_read_errors"] != 0:
            reasons.append(f"{mode}_load_generator_errors")
        lateness = generator["scheduler_lateness_p95_seconds"]
        interval = run.get("sample_seconds", 5)
        interval = interval if finite(interval) and interval > 0 else 5
        max_lateness = generator["scheduler_lateness_max_seconds"]
        if (not finite(lateness) or lateness < 0 or lateness > max(.1, .1 * interval)
                or not finite(max_lateness) or max_lateness < lateness or max_lateness > interval):
            reasons.append(f"{mode}_invalid_load_generator_timing")
        if any(not finite(counts[key]) or counts[key] < 0 or int(counts[key]) != counts[key] for key in COUNT_FIELDS):
            reasons.append(f"{mode}_incomplete_accounting")
        else:
            if counts["pending"] != 0:
                reasons.append(f"{mode}_pending_work")
            if counts["offered"] <= 0 or not 0 <= counts["committed"] <= counts["acknowledged"] <= counts["admitted"] <= counts["offered"]:
                reasons.append(f"{mode}_inconsistent_accounting")
            if counts["admitted"] != counts["acknowledged"] + counts["delivery_failed"] + counts["enqueue_failed"]:
                reasons.append(f"{mode}_unresolved_admissions")
            if counts["offered"] != counts["admitted"] + counts["rejected"] + counts["pending"]:
                reasons.append(f"{mode}_unaccounted_offers")
            if counts["acknowledged"] != counts["committed"]:
                reasons.append(f"{mode}_unobserved_commits")
            if counts["deferred"] > counts["offered"]:
                reasons.append(f"{mode}_inconsistent_deferred_count")
            if (counts["committed"] > 0 and (counts.get("latency_count") != counts["committed"]
                    or not finite(summary["offered_to_commit_p95_upper_seconds"])
                    or summary["offered_to_commit_p95_upper_seconds"] < 0)):
                reasons.append(f"{mode}_incomplete_offered_latency")
            if counts.get("cancelled_future_offers", 0) or counts.get("scheduler_shortfall", 0):
                reasons.append(f"{mode}_incomplete_offered_schedule")
        if any(action.get("status") == "applied" and action.get("decision", {}).get("resources")
               for action in run.get("actions", []) if isinstance(action.get("decision", {}), dict)):
            reasons.append(f"{mode}_resources_changed")
        if any(_changes_control(action) and not _is_model(action) and not _is_guard(action)
               and _timestamp(action) is not None
               and finite(run.get("load_started_at")) and finite(run.get("load_ended_at"))
               and run["load_started_at"] <= _timestamp(action) <= run["load_ended_at"]
               for action in run.get("actions", [])):
            reasons.append(f"{mode}_non_model_control_during_load")
    for field in ("config_fingerprint", "resource_fingerprint"):
        if not baseline.get(field) or baseline.get(field) != llm.get(field):
            reasons.append(f"mismatched_{field}")
    if summaries["baseline"]["accounting"]["offered"] != summaries["llm"]["accounting"]["offered"]:
        reasons.append("mismatched_offered_count")
    left, right = summaries["baseline"]["load_generator"], summaries["llm"]["load_generator"]
    if not left["workload_fingerprint"] or left["workload_fingerprint"] != right["workload_fingerprint"]:
        reasons.append("mismatched_workload_fingerprint")
    if all(finite(run.get(key)) for run in (baseline, llm) for key in ("load_started_at", "load_ended_at")):
        if max(baseline["load_started_at"], llm["load_started_at"]) < min(baseline["load_ended_at"], llm["load_ended_at"]):
            reasons.append("overlapping_trial_loads")
    return reasons


def build_report(runs: list[dict], *, target_latency_seconds: float = 2.0,
                 min_pairs: int = 3, sustained_samples: int = 3) -> dict:
    """Return JSON-serializable evidence; missing evidence never becomes success."""
    if not finite(target_latency_seconds) or target_latency_seconds <= 0 or min_pairs < 3 or sustained_samples < 3:
        raise ValueError("Positive latency target, at least three pairs and three sustained samples are required")
    grouped = defaultdict(list)
    for run in runs:
        grouped[run.get("pair_id")].append(run)
    pairs, flat = [], []
    for pair_id, members in grouped.items():
        summaries = {run.get("mode"): summarize_run(run, target_latency_seconds=target_latency_seconds,
                                                   sustained_samples=sustained_samples) for run in members}
        flat.extend(summaries.values())
        if pair_id is None or len(members) != 2 or {run.get("mode") for run in members} != {"baseline", "llm"}:
            pairs.append({"pair_id": pair_id, "eligible": False, "demonstrated": False,
                          "reasons": ["pair_requires_exactly_one_baseline_and_one_llm"]})
            continue
        by_mode = {run["mode"]: run for run in members}
        base, llm = summaries["baseline"], summaries["llm"]
        reasons = _pair_reasons(by_mode["baseline"], by_mode["llm"], summaries, sustained_samples)
        eligible = not reasons
        if not base["overload_observed"]:
            reasons.append("baseline_overload_not_observed")
        if not llm["adaptation_chains"]:
            reasons.append("no_model_decrease_with_ingress_response_and_loaded_recovery")
        compared = ("overload_peak_lag", "overload_latency_violation_seconds")
        complete_metrics = all(finite(item[key]) for item in (base, llm) for key in compared)
        improved = (complete_metrics and all(llm[key] <= base[key] for key in compared)
                    and any(llm[key] < base[key] for key in compared))
        if not improved:
            reasons.append("stability_not_improved_without_primary_metric_regression")
        if (base["overload_peak_error_rate"] is None or llm["overload_peak_error_rate"] is None
                or llm["overload_peak_error_rate"] > base["overload_peak_error_rate"]):
            reasons.append("error_rate_missing_or_worse")
        if (base["delivery_or_enqueue_failure_fraction"] is None or llm["delivery_or_enqueue_failure_fraction"] is None
                or llm["delivery_or_enqueue_failure_fraction"] > base["delivery_or_enqueue_failure_fraction"]):
            reasons.append("delivery_or_enqueue_failure_fraction_missing_or_worse")
        order = sorted(members, key=lambda run: run.get("load_started_at") if finite(run.get("load_started_at")) else math.inf)
        pairs.append({"pair_id": pair_id, "order": [run["mode"] for run in order], "eligible": eligible,
                      "demonstrated": not reasons, "reasons": reasons,
                      "baseline": base, "llm": llm,
                      "deltas_llm_minus_baseline": {
                          key: llm[key] - base[key] if finite(llm.get(key)) and finite(base.get(key)) else None
                          for key in (*compared, "served_fraction", "offered_to_commit_p95_upper_seconds")}})
    eligible_count = sum(pair["eligible"] for pair in pairs)
    demonstrated_count = sum(pair["demonstrated"] for pair in pairs)
    orders = {tuple(pair.get("order", [])) for pair in pairs if pair["eligible"]}
    reasons = []
    if eligible_count < min_pairs:
        reasons.append(f"need_at_least_{min_pairs}_eligible_pairs")
    if demonstrated_count < min_pairs:
        reasons.append(f"need_at_least_{min_pairs}_pairs_with_observed_model_stabilization")
    if not {("baseline", "llm"), ("llm", "baseline")} <= orders:
        reasons.append("both_AB_and_BA_orders_required")
    chronological_pairs = sorted((pair for pair in pairs if pair["eligible"]),
        key=lambda pair: min(run["load_started_at"] for run in grouped[pair["pair_id"]]))
    if any(left["order"] == right["order"] for left, right in zip(chronological_pairs, chronological_pairs[1:])):
        reasons.append("pair_orders_must_alternate")
    if eligible_count != len(pairs):
        reasons.append("ineligible_pairs_require_review")
    if any(pair["eligible"] and not pair["demonstrated"] for pair in pairs):
        reasons.append("not_all_eligible_pairs_support_the_claim")
    verdict = "demonstrated" if not reasons else ("not_demonstrated" if eligible_count >= min_pairs else "inconclusive")
    return {"schema_version": 1, "verdict": verdict, "reasons": reasons,
            "target_latency_seconds": target_latency_seconds, "required_pairs": min_pairs,
            "sustained_samples": sustained_samples, "eligible_pairs": eligible_count,
            "demonstrated_pairs": demonstrated_count, "pairs": pairs, "runs": flat, "notes": NOTES}


def _format(value: Any) -> str:
    if value is None:
        return "unobserved"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown_report(report: dict) -> str:
    lines = ["# LLM overload comparison", "", f"Verdict: **{report['verdict']}**.", "",
             f"Eligible pairs: {report['eligible_pairs']}; demonstrated pairs: {report['demonstrated_pairs']}; required: {report['required_pairs']}.", ""]
    if report["reasons"]:
        lines.extend(["Reasons: " + "; ".join(report["reasons"]) + ".", ""])
    lines.extend(["| Pair | Mode | Offered | Admitted | Deferred¹ | Rejected | Failed² | Committed | Served fraction | Offered→commit p95 upper (s) | Peak Kafka lag | p95 violation (s) | Guards during load |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for run in report["runs"]:
        values = [run["pair_id"], run["mode"], *(run["accounting"][key] for key in ("offered", "admitted", "deferred", "rejected")),
                  run["delivery_or_enqueue_failures"], run["accounting"]["committed"],
                  run["served_fraction"], run["offered_to_commit_p95_upper_seconds"], run["overload_peak_lag"],
                  run["overload_latency_violation_seconds"], run["guards_under_load"]]
        lines.append("| " + " | ".join(_format(value) for value in values) + " |")
    lines.extend(["", "¹ Deferred is a count of offers that waited; it can overlap admitted or rejected work.",
                  "² Failed includes enqueue and delivery failures. Committed means observed Kafka consumer commits; histogram errors are checked separately.", ""])
    for pair in report["pairs"]:
        lines.append(f"- Pair {_format(pair['pair_id'])}: " + ("demonstrated" if pair["demonstrated"] else "; ".join(pair["reasons"])) + ".")
        for chain in pair.get("llm", {}).get("adaptation_chains", []):
            lines.append(f"  - Model {_format(chain['model'])}: rate {_format(chain['rate_before'])} → {_format(chain['rate_after'])}/s; "
                         f"measured ingress {_format(chain['ingress_before'])} → {_format(chain['ingress_after'])}/s; "
                         f"recovery {_format(chain['recovery_seconds'])} s after action, at unchanged offered {_format(chain['offered_rate'])}/s.")
    lines.extend(["", *["- " + note for note in report["notes"]], ""])
    return "\n".join(lines)


def plot_runs(runs: list[dict], output: Path, *, target_latency_seconds: float = 2.0) -> dict:
    """Create standalone SVG and PNG; absence of matplotlib leaves honest JSON/MD."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return {"status": "unavailable", "reason": "matplotlib_not_installed"}
    if not runs:
        return {"status": "unavailable", "reason": "no_runs"}
    figure, axes = plt.subplots(4, len(runs), figsize=(max(7, len(runs) * 3.5), 11), squeeze=False, sharex="col")
    for column, run in enumerate(runs):
        rows = _rows(run)
        start = run.get("load_started_at")
        if not finite(start):
            start = rows[0]["timestamp"] if rows else 0
        times = [row["timestamp"] - start for row in rows]
        for key, label, style in (("offered_rate", "Offered", "--"), ("admission_limit", "Applied limit", "-."),
                                  ("incoming_rate", "Kafka ingress", "-"), ("throughput", "Committed/s", ":")):
            axes[0, column].plot(times, [row.get(key) if finite(row.get(key)) else math.nan for row in rows], style, label=label)
        for index, key, label in ((1, "kafka_lag", "Kafka lag"), (2, "latency_p95_seconds", "Kafka interval p95 (s)"), (3, "pending_offered", "Queued before gate")):
            axes[index, column].plot(times, [row.get(key) if finite(row.get(key)) else math.nan for row in rows], label=label)
            axes[index, column].set_ylabel(label)
        axes[0, column].set_title(f"Pair {run.get('pair_id')} / {run.get('mode')}\n{run.get('status')}")
        axes[0, column].set_ylabel("records/s")
        axes[2, column].axhline(target_latency_seconds, color="black", linestyle="--", linewidth=.8)
        for action in run.get("actions", []):
            stamp = _timestamp(action)
            if stamp is None or not (_is_model(action) or _is_guard(action)):
                continue
            color = "purple" if _is_model(action) else "red"
            for index in range(4):
                axes[index, column].axvline(stamp - start, color=color, alpha=.45, linewidth=.8)
        axes[0, column].legend(fontsize=7)
        axes[3, column].set_xlabel("Seconds from load start")
        for index in range(4):
            axes[index, column].grid(alpha=.2)
    figure.suptitle("Measured traffic / fixed Docker limits\nPurple: applied LLM action; red: guard pause. Missing telemetry remains a gap.", fontsize=11)
    figure.tight_layout(rect=(0, 0, 1, .96))
    paths = []
    for suffix in ("png", "svg"):
        path = output / f"comparison.{suffix}"
        figure.savefig(path, dpi=150)
        paths.append(str(path))
    plt.close(figure)
    return {"status": "created", "files": paths}


def write_report(runs: list[dict], output_dir: str | Path, **kwargs) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = build_report(runs, **kwargs)
    report["plot"] = plot_runs(runs, output, target_latency_seconds=report["target_latency_seconds"])
    (output / "comparison.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    (output / "comparison.md").write_text(markdown_report(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSON list of runs, or object containing runs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-latency-seconds", type=float, default=2.0)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    runs = data if isinstance(data, list) else data["runs"]
    report = write_report(runs, args.output, target_latency_seconds=args.target_latency_seconds)
    print(json.dumps({"verdict": report["verdict"], "reasons": report["reasons"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
