"""Regenerate measured traffic comparisons: python -m research.report --input RUN.

Missing observations are gaps, never interpolated zeros. Capacity conclusions
require completed stages with enough complete telemetry; rejected offered demand
cannot be counted as sustainable input. Broker ingress is leader log-offset
growth on the stress topic, not replication-inclusive Kafka/JMX traffic.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any


MODES = ("baseline", "adaptive")
REQUIRED = ("throughput", "accepted_rate", "cpu_percent", "ram_percent", "kafka_lag",
            "latency_p95_seconds", "error_rate")


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def average(records: list[dict], key: str) -> float | None:
    selected = [r for r in records if finite(r.get(key))]
    if not selected:
        return None
    weights = [r.get("interval_seconds") for r in selected]
    if all(finite(v) and v > 0 for v in weights):
        return sum(r[key] * w for r, w in zip(selected, weights)) / sum(weights)
    return mean(r[key] for r in selected)


def maximum(records: list[dict], key: str) -> float | None:
    values = [r[key] for r in records if finite(r.get(key))]
    return max(values) if values else None


def slope(records: list[dict], key: str) -> float | None:
    pairs = [(r["elapsed_seconds"], r[key]) for r in records
             if finite(r.get("elapsed_seconds")) and finite(r.get(key))]
    if len(pairs) < 2:
        return None
    mx, my = mean(p[0] for p in pairs), mean(p[1] for p in pairs)
    denominator = sum((x - mx) ** 2 for x, _ in pairs)
    return sum((x - mx) * (y - my) for x, y in pairs) / denominator if denominator else None


def broker_summary(records: list[dict]) -> dict:
    complete, totals = [], {str(i): 0.0 for i in (1, 2, 3)}
    for row in records:
        interval = row.get("kafka_interval_seconds", row.get("interval_seconds"))
        values = {i: row.get("brokers", {}).get(i, {}).get("leader_incoming_rate") for i in totals}
        if not finite(interval) or interval <= 0 or not all(finite(v) and v >= 0 for v in values.values()):
            continue
        complete.append(row)
        for key, value in values.items():
            totals[key] += value * interval
    total = sum(totals.values())
    loads = list(totals.values())
    avg = mean(loads) if total else None
    per_broker = {}
    for key in totals:
        points = [{**r.get("brokers", {}).get(key, {}), "interval_seconds": r.get("interval_seconds")}
                  for r in records]
        per_broker[key] = {"leader_offset_growth": totals[key] if complete else None,
                           "leader_load_share": totals[key] / total if total else None,
                           **{f"mean_{name}": average(points, name) for name in (
                               "cpu_core_percent", "memory_bytes", "network_rx_bytes_per_second",
                               "network_tx_bytes_per_second", "disk_read_bytes_per_second",
                               "disk_write_bytes_per_second")}}
    return {"semantics": "stress-topic leader log-offset growth; network includes replication and other traffic",
            "complete_intervals": len(complete), "missing_intervals": len(records) - len(complete),
            "max_over_mean": max(loads) / avg if avg else None,
            "coefficient_of_variation": pstdev(loads) / avg if avg else None, "brokers": per_broker}


def stage_summary(stage: dict, records: list[dict], config: dict) -> dict:
    criteria, step = config["stability"], config["sample_seconds"]
    end = stage["start_elapsed"] + stage["seconds"]
    tail = min(criteria["tail_seconds"], stage["seconds"])
    start = end - tail
    selected = sorted((r for r in records if r.get("phase") == "load"
                       and r.get("stage") == stage["index"]
                       and finite(r.get("elapsed_seconds")) and start <= r["elapsed_seconds"] <= end),
                      key=lambda r: r["elapsed_seconds"])
    missing = sorted({key for r in selected for key in REQUIRED if not finite(r.get(key))})
    times = [r["elapsed_seconds"] for r in selected]
    coverage = bool(times and times[0] <= start + step * 1.5 and times[-1] >= end - step * 1.5
                    and times[-1] - times[0] >= max(0, tail - step * 1.5)
                    and all(b - a <= step * 1.5 for a, b in zip(times, times[1:])))
    checks = {"stage_completed": stage.get("completed") is True,
              "minimum_samples": len(selected) >= criteria["minimum_samples"],
              "tail_coverage": coverage, "complete_metrics": bool(selected) and not missing,
              "no_collection_errors": not any(r.get("errors") for r in selected)}
    evidence_complete = all(checks.values())
    lag_slope = slope(selected, "kafka_lag")
    observed = {f"mean_{key}": average(selected, key) for key in REQUIRED}
    observed.update({f"max_{key}": maximum(selected, key) for key in REQUIRED})
    observed["lag_slope_per_second"] = lag_slope
    limits = (("kafka_lag", "max_lag"), ("latency_p95_seconds", "max_latency_seconds"),
              ("cpu_percent", "max_cpu_percent"), ("ram_percent", "max_ram_percent"),
              ("error_rate", "max_error_rate"))
    for key, name in limits:
        value = observed[f"max_{key}"]
        checks[name] = value is not None and value <= criteria[name]
    checks["lag_not_growing"] = lag_slope is not None and lag_slope <= criteria["max_lag_slope"]
    offered, admitted = stage.get("offered", 0), stage.get("admitted", 0)
    served_fraction = admitted / offered if offered else None
    actual_offered_rate = offered / stage["seconds"]
    processed, accepted = observed["mean_throughput"], observed["mean_accepted_rate"]
    required_fraction = criteria["minimum_served_fraction"]
    checks["offered_schedule_realized"] = actual_offered_rate >= stage["requested_rate"] * required_fraction
    checks["offered_demand_admitted"] = served_fraction is not None and served_fraction >= required_fraction
    checks["offered_demand_processed"] = processed is not None and processed >= stage["requested_rate"] * required_fraction
    checks["admitted_demand_processed"] = (processed is not None and accepted is not None and accepted > 0
                                            and processed >= accepted * required_fraction)
    # A late observer can attribute one interval to a different stage; retain
    # the limitation rather than interpreting accumulated backlog as capacity.
    safe_keys = [name for _, name in limits] + ["lag_not_growing", "admitted_demand_processed"]
    admitted_stable = evidence_complete and all(checks[key] for key in safe_keys)
    offered_stable = admitted_stable and all(checks[key] for key in (
        "offered_schedule_realized", "offered_demand_admitted", "offered_demand_processed"))
    classification = ("inconclusive" if not evidence_complete else "stable_offered" if offered_stable
                      else "stable_admitted_only" if admitted_stable else "unstable")
    return {"index": stage["index"], "requested_rate": stage["requested_rate"],
            "classification": classification, "stable_offered": offered_stable,
            "stable_admitted": admitted_stable, "checks": checks,
            "failed_criteria": [name for name, okay in checks.items() if not okay],
            "sample_count": len(selected), "tail_start_seconds": start, "tail_end_seconds": end,
            "missing_metrics": missing, "served_fraction": served_fraction,
            "actual_offered_rate": actual_offered_rate, **observed}


def mode_summary(run: dict, records: list[dict], actions: list[dict], config: dict) -> dict:
    load = [r for r in records if r.get("phase") == "load"]
    stages = [stage_summary(s, records, config) for s in run.get("stages", [])]
    stable = [s for s in stages if s["stable_offered"]]
    admitted = [s for s in stages if s["stable_admitted"]]
    evaluated = [s for s in stages if s["classification"] != "inconclusive"]
    unsustainable = next((s for s in evaluated if not s["stable_offered"]), None)
    unstable = next((s for s in evaluated if s["classification"] == "unstable"), None)
    plateau_input = config["stability"].get("plateau_min_input_growth_fraction", .1)
    plateau_output = config["stability"].get("plateau_max_throughput_growth_fraction", .05)
    plateau = next((right for left, right in zip(evaluated, evaluated[1:])
                    if right["requested_rate"] > left["requested_rate"] * (1 + plateau_input)
                    and finite(left["mean_throughput"]) and finite(right["mean_throughput"])
                    and right["mean_throughput"] <= left["mean_throughput"] * (1 + plateau_output)), None)
    episodes = [{**episode, "recovery_phase": (
        "drain" if episode.get("t3") is not None and episode["t3"] > run.get("load_ended_at", math.inf)
        else "load" if episode.get("t3") is not None else None)} for episode in run.get("episodes", [])]
    seconds = run.get("load_seconds")
    metrics = {f"mean_{key}": average(load, key) for key in REQUIRED}
    metrics.update({f"peak_{key}": maximum(load, key) for key in ("cpu_percent", "ram_percent", "kafka_lag", "latency_p95_seconds")})
    metrics.update({"actual_offered_rate": run["offered"] / seconds if finite(run.get("offered")) and finite(seconds) and seconds > 0 else None,
                    "admitted_rate": run["admitted"] / seconds if finite(run.get("admitted")) and finite(seconds) and seconds > 0 else None,
                    "served_fraction": run["admitted"] / run["offered"] if finite(run.get("admitted")) and finite(run.get("offered")) and run["offered"] > 0 else None,
                    "rejected_messages": run.get("rejected"), "acknowledged_messages": run.get("acknowledged"),
                    "final_lag": run.get("final_lag")})
    return {"status": run.get("status"), "metrics": metrics, "load_samples": len(load),
            "missing_samples_by_metric": {key: sum(not finite(r.get(key)) for r in load) for key in REQUIRED},
            "stages": stages, "max_sustainable_offered_rate": max((s["requested_rate"] for s in stable), default=None),
            "max_stable_admitted_rate": max((s["mean_accepted_rate"] for s in admitted), default=None),
            "first_unsustainable_offered_stage": unsustainable,
            "first_observed_unstable_stage": unstable, "throughput_plateau_candidate": plateau,
            "plateau_heuristic": {"minimum_input_growth_fraction": plateau_input,
                                  "maximum_throughput_growth_fraction": plateau_output},
            "capacity_caveat": "Finite tested workload only; an admission ceiling or plateau alone does not establish infrastructure saturation.",
            "broker_load": broker_summary(load), "episodes": episodes,
            "changed_actions": sum(a.get("current_limit") != a.get("new_limit") for a in actions)}


def comparison(summaries: dict) -> list[dict]:
    if not all(mode in summaries for mode in MODES):
        return []
    before, after = (summaries[mode]["metrics"] for mode in MODES)
    return [{"metric": key, "before_agent": value, "after_agent": after[key],
             "difference": after[key] - value} for key, value in before.items()
            if finite(value) and finite(after.get(key))]


def common_window_summary(records: list[dict], seconds: float) -> dict:
    """Compare only complete observed intervals ending inside the shared load window.

    There is no interpolation at the boundary. Actual covered endpoints and
    missing samples remain visible because independent collectors need not align.
    Final run totals/drain backlog are deliberately absent from this comparison.
    """
    load = [r for r in records if r.get("phase") == "load" and finite(r.get("elapsed_seconds"))
            and 0 <= r["elapsed_seconds"] <= seconds
            and (not finite(r.get("interval_seconds")) or r["elapsed_seconds"] >= r["interval_seconds"])]
    keys = (*REQUIRED, "requested_rate", "incoming_rate", "acknowledged_rate", "current_limit")
    metrics = {f"mean_{key}": average(load, key) for key in keys}
    metrics.update({f"peak_{key}": maximum(load, key) for key in
                    ("cpu_percent", "ram_percent", "kafka_lag", "latency_p95_seconds")})
    return {"metrics": metrics, "samples": len(load),
            "first_observed_endpoint_seconds": min((r["elapsed_seconds"] for r in load), default=None),
            "last_observed_endpoint_seconds": max((r["elapsed_seconds"] for r in load), default=None),
            "missing_samples_by_metric": {key: sum(not finite(r.get(key)) for r in load) for key in keys}}


def annotations(axis, run):
    started = run.get("load_started_at")
    if not finite(started):
        return
    for episode in run.get("episodes", []):
        for key, label, color in (("t0", "risk", "#b91c1c"), ("t1", "detect", "#c2410c"),
                                  ("t2", "adjust", "#6d28d9"), ("t3", "recover", "#15803d")):
            if finite(episode.get(key)):
                x = episode[key] - started
                axis.axvline(x, alpha=.4, color=color, linestyle="--", linewidth=.8)
                axis.text(x, .98, label, color=color, fontsize=7, rotation=90, va="top",
                          transform=axis.get_xaxis_transform())


def generate_charts(loaded: dict, output: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = []
    plots = (("throughput", "Committed throughput (messages/s)", "throughput.png"),
             ("cpu_percent", "Selected pipeline CPU (% Docker-engine capacity)", "cpu.png"),
             ("ram_percent", "Selected pipeline working-set RAM (% engine memory)", "ram.png"),
             ("kafka_lag", "Stress-topic consumer lag (messages)", "kafka_lag.png"),
             ("latency_p95_seconds", "Interval p95 enqueue-to-commit latency (s)", "latency.png"),
             ("current_limit", "Admission limit (messages/s)", "rate_limit.png"),
             ("error_rate", "Invalid/DLQ/failed fraction", "error_rate.png"))
    colors = {"baseline": "#2563eb", "adaptive": "#e85d04"}
    for key, label, filename in plots:
        fig, axis = plt.subplots(figsize=(10, 4))
        has_data = False
        for mode, data in loaded.items():
            points = data["observations"]
            if key == "current_limit":
                manifest = data["manifest"]
                initial = manifest.get("control", {}).get("initial_rate")
                action_points = [(0, initial)] + [(a.get("elapsed_seconds"), a.get("new_limit"))
                                                for a in data["actions"] if finite(a.get("elapsed_seconds"))]
                if points:
                    action_points.append((points[-1]["elapsed_seconds"], points[-1].get("current_limit")))
                if points and any(finite(y) for _, y in action_points):
                    axis.step([x for x, _ in action_points], [y if finite(y) else math.nan for _, y in action_points],
                              where="post", label=mode, color=colors[mode])
                    has_data = True
            elif any(finite(p.get(key)) for p in points):
                axis.plot([p["elapsed_seconds"] for p in points],
                          [p[key] if finite(p.get(key)) else math.nan for p in points],
                          label=mode, color=colors[mode])
                has_data = True
            if mode == "adaptive":
                annotations(axis, data["report"])
        if has_data:
            axis.set(xlabel="Elapsed from load start (s)", ylabel=label)
            axis.legend()
            axis.grid(alpha=.2)
            fig.tight_layout()
            fig.savefig(output / filename, dpi=150)
            paths.append(str(output / filename))
        plt.close(fig)

    if any(data["actions"] for data in loaded.values()):
        fig, axis = plt.subplots(figsize=(10, 4))
        labels = {"decrease": -1, "hold": 0, "increase": 1}
        has_data = False
        for mode, data in loaded.items():
            events = [a for a in data["actions"] if a.get("action") in labels and finite(a.get("elapsed_seconds"))]
            if events:
                axis.scatter([a["elapsed_seconds"] for a in events], [labels[a["action"]] for a in events],
                             label=mode, color=colors[mode], alpha=.8)
                has_data = True
            if mode == "adaptive":
                annotations(axis, data["report"])
        if has_data:
            axis.set(xlabel="Elapsed from load start (s)", ylabel="Observed agent decision")
            axis.set_yticks(list(labels.values()), list(labels))
            axis.legend()
            axis.grid(alpha=.2)
            fig.tight_layout()
            fig.savefig(output / "agent_actions.png", dpi=150)
            paths.append(str(output / "agent_actions.png"))
        plt.close(fig)

    if loaded:
        fig, axes = plt.subplots(len(loaded), 1, figsize=(10, 4 * len(loaded)), squeeze=False)
        has_data = False
        for axis, (mode, data) in zip(axes[:, 0], loaded.items()):
            points = data["observations"]
            for key, label in (("requested_rate", "offered schedule"), ("accepted_rate", "admitted"), ("throughput", "committed")):
                if any(finite(p.get(key)) for p in points):
                    axis.plot([p["elapsed_seconds"] for p in points], [p[key] if finite(p.get(key)) else math.nan for p in points], label=label)
                    has_data = True
            axis.set(title=mode, xlabel="Elapsed (s)", ylabel="Messages/s")
            if axis.lines:
                axis.legend()
            axis.grid(alpha=.2)
        if has_data:
            fig.tight_layout()
            fig.savefig(output / "input_vs_throughput.png", dpi=150)
            paths.append(str(output / "input_vs_throughput.png"))
        plt.close(fig)

        fig, axes = plt.subplots(len(loaded), 1, figsize=(10, 4 * len(loaded)), squeeze=False)
        has_data = False
        for axis, (mode, data) in zip(axes[:, 0], loaded.items()):
            points = data["observations"]
            for broker in ("1", "2", "3"):
                values = [p.get("brokers", {}).get(broker, {}).get("leader_incoming_rate") for p in points]
                if any(finite(value) for value in values):
                    axis.plot([p["elapsed_seconds"] for p in points], [v if finite(v) else math.nan for v in values], label=f"Broker {broker}")
                    has_data = True
            axis.set(title=mode, xlabel="Elapsed (s)", ylabel="Stress-topic leader log growth/s")
            if axis.lines:
                axis.legend()
            axis.grid(alpha=.2)
        if has_data:
            fig.tight_layout()
            fig.savefig(output / "broker_load.png", dpi=150)
            paths.append(str(output / "broker_load.png"))
        plt.close(fig)
    return paths


def generate_report(input_path: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    loaded, summaries = {}, {}
    for mode in MODES:
        directory = input_path / mode
        if not (directory / "report.json").exists() or not (directory / "manifest.json").exists():
            continue
        data = {"report": json.loads((directory / "report.json").read_text(encoding="utf-8")),
                "manifest": json.loads((directory / "manifest.json").read_text(encoding="utf-8")),
                "observations": rows(directory / "observations.jsonl"), "actions": rows(directory / "actions.jsonl")}
        loaded[mode] = data
        summaries[mode] = mode_summary(data["report"], data["observations"], data["actions"], data["manifest"]["config"])
    paired = all(mode in loaded for mode in MODES)
    matched = paired and all(loaded["baseline"]["manifest"]["config"].get(key) == loaded["adaptive"]["manifest"]["config"].get(key)
                             for key in ("profile", "seed", "sample_seconds", "decision_seconds", "control", "stability"))
    durations = [loaded[m]["report"].get("load_seconds") for m in MODES] if paired else []
    common_seconds = min(durations) if matched and all(finite(d) and d > 0 for d in durations) else None
    common = {m: common_window_summary(loaded[m]["observations"], common_seconds) for m in MODES} if common_seconds is not None else {}
    result = {"source": str(input_path), "modes": summaries, "paired_configuration_matches": matched,
              "paired_runs_completed": paired and all(loaded[m]["report"].get("status") == "completed" for m in MODES),
              "common_load_seconds": common_seconds, "common_window": common,
              "before_after": comparison(common) if common else [],
              "limitations": ["Interval p95 is estimated from real histogram buckets, not exact raw-event quantiles.",
                              "Resource scope is selected pipeline containers, not total host utilization.",
                              "Different admitted traffic is a controller action; rejected offered demand remains visible.",
                              "Before/after means use complete observed intervals within the common load duration, with no boundary interpolation; coverage and missing endpoints remain visible.",
                              "Censored runs and a single pair do not establish a causal improvement or a robust capacity limit.",
                              "Mean interval p95 is not a global event p95. Collection intervals can cross stage boundaries.",
                              "T0 is a sampled crossing. Drain-phase recovery does not prove recovery under continued offered load.",
                              "No unobserved stage or missing measurement is filled or extrapolated."],
              "charts": generate_charts(loaded, output)}
    (output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    lines = ["# Measured traffic experiment", "", f"Paired configuration matches: {matched}.",
             f"Common load window: {common_seconds} seconds; complete observed intervals only.", "",
             "| Metric | Before agent | After agent | Difference |", "|---|---:|---:|---:|"]
    lines += [f"| {r['metric']} | {r['before_agent']:.6g} | {r['after_agent']:.6g} | {r['difference']:.6g} |" for r in result["before_after"]]
    lines += ["", "Missing values are omitted from the comparison. See summary.json for coverage, stability checks, reaction episodes, and broker shares."]
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = generate_report(args.input, args.output or args.input / "analysis")
    print(json.dumps({"modes": list(result["modes"]), "charts": result["charts"]}, indent=2))


if __name__ == "__main__":
    main()
