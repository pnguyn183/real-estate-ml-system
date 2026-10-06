"""User-controlled finite traffic-history sessions using the real Kafka experiment.

Run from the repository root: python -m research.collect_session --help.
Durations cover offered traffic; preflight, queue drain and the quality audit add
wall time. This collects synthetic offered demand and real system telemetry,
not natural production traffic. No model score determines the workload.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import signal
import threading
import time
from uuid import uuid4

from agents.stress import stress_topic
from research.run_experiment import experiment_lock, run_mode, validate_config
from research.runtime import keep_system_awake
from research.lean_runtime import OPTIONAL_SERVICES, lean_environment

DEFAULT_ROOT = Path("runtime/research/traffic-sessions")
MAX_DURATION_SECONDS = 8 * 3600
ACTIVE_STATES = {"starting", "running", "recovering", "draining", "auditing"}
DEFAULT_BOOTSTRAP = "localhost:9092,localhost:9093,localhost:9094"
DEFAULT_TOPIC = "real_estate_stress_raw"
DEFAULT_GROUP = "real_estate_training_pipeline"
FAILURE_HINTS = {
    "preparing_runtime": "Could not prepare the session runtime. Check Docker availability and environment_path if lean mode was enabled; the workload may not have started.",
    "running_workload": "The workload did not complete normally. Inspect report_path for preflight, telemetry and delivery evidence; do not assume the requested duration was collected.",
    "restoring_runtime": "Runtime restoration failed. Inspect environment_path and verify the recorded optional containers before starting another session.",
    "auditing": "The session history quality audit failed. Existing observations are preserved; inspect history_audit.json if present before exporting or training.",
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value):
    """Readers see the previous complete document or the new complete document."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_profile(duration_seconds: float, min_rate=5, max_rate=60, seed=1):
    """Seeded smooth waves with varying period/amplitude and bounded mild jitter."""
    if not math.isfinite(duration_seconds) or not 60 <= duration_seconds <= MAX_DURATION_SECONDS:
        raise ValueError("duration must be between 1 minute and 8 hours")
    if not all(math.isfinite(rate) for rate in (min_rate, max_rate)) or not 0 < min_rate < max_rate <= 10000:
        raise ValueError("rates must satisfy 0 < min_rate < max_rate <= 10000")
    rng = random.Random(seed)
    profile, elapsed = [], 0.0
    wave_start, period = 0.0, rng.uniform(720, 1080)
    floor, amplitude = rng.uniform(0, .12), rng.uniform(.72, .88)
    while elapsed < duration_seconds:
        while elapsed >= wave_start + period:
            wave_start += period
            period = rng.uniform(720, 1080)
            floor, amplitude = rng.uniform(0, .12), rng.uniform(.72, .88)
        phase = (elapsed - wave_start) / period
        fraction = min(1, max(0, floor + amplitude * (1 - math.cos(2 * math.pi * phase)) / 2 + rng.uniform(-.035, .035)))
        seconds = min(60.0, duration_seconds - elapsed)
        rate = min(max_rate, max(min_rate, round(min_rate + (max_rate - min_rate) * fraction, 4)))
        profile.append({"rate": rate, "seconds": seconds})
        elapsed += seconds
    return profile


def build_config(duration_seconds: float, min_rate=5, max_rate=60, seed=None, sample_seconds=5,
                 *, recovery_seconds=60, recovery_errors=5, preflight_seconds=60):
    config = json.loads(Path(__file__).with_name("forecast_history.json").read_text(encoding="utf-8-sig"))
    seed = random.SystemRandom().randrange(2**32) if seed is None else seed
    config.update(profile=build_profile(duration_seconds, min_rate, max_rate, seed), seed=seed,
                  sample_seconds=sample_seconds, decision_seconds=max(10, sample_seconds))
    projected = sum(math.ceil(stage["rate"] * stage["seconds"]) for stage in config["profile"])
    if projected > 1_000_000:
        raise ValueError("profile exceeds 1,000,000 records; reduce duration or maximum rate")
    config["max_records"] = projected
    config["resource_scaling"]["enabled"] = False
    config["telemetry_recovery"] = {"max_consecutive_errors": recovery_errors, "max_recovery_seconds": recovery_seconds,
                                    "healthy_samples_to_resume": 2, "allow_core_transport_recovery": True}
    config["preflight_seconds"] = preflight_seconds
    config["vm_memory_safety"] = {"min_available_percent": 10, "min_free_percent_when_swap_full": 5,
                                   "max_swap_used_percent": 95}
    # A fixed admission ceiling above the offered profile preserves target demand.
    # Existing hard CPU/RAM/lag/telemetry guards remain active in the runner.
    config["control"].update(min_rate=min(1, min_rate), max_rate=max_rate, initial_rate=max_rate)
    config["protocol"] = {
        "purpose": "Controlled traffic history from a finite user-started session",
        "duration_seconds": duration_seconds, "minimum_requested_rate": min_rate,
        "maximum_requested_rate": max_rate, "stage_seconds": 60,
        "wave_period_range_seconds": [720, 1080], "rate_jitter_seed": seed,
        "selection": "Seeded waves declared before measurement, independent of model scores",
        "forecast_target": "requested_rate", "workload_kind": "controlled_synthetic",
        "limitation": "Real Kafka/processing measurements under synthetic offered demand; not natural traffic",
    }
    validate_config(config, max_duration_seconds=MAX_DURATION_SECONDS)
    return config


def _resolve_session(session, output_root):
    if session is not None:
        selected = Path(session).resolve()
    else:
        candidates = list(Path(output_root).glob("session-*/session.json"))
        if not candidates:
            raise ValueError("no saved sessions; start one or specify --session")
        selected = max(candidates, key=lambda path: path.parent.name).parent.resolve()
    if not (selected / "session.json").is_file():
        raise ValueError("session directory must contain session.json")
    return selected


def _lock_held(path: Path):
    """Probe this session's OS lock, never trust a potentially reused PID."""
    if not path.exists():
        return False
    with path.open("r+b") as stream:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return False


def session_status(session=None, output_root=DEFAULT_ROOT):
    selected = _resolve_session(session, output_root)
    metadata = json.loads((selected / "session.json").read_text(encoding="utf-8-sig"))
    active = _lock_held(selected / "session.lock")
    state = metadata.get("status", "unknown")
    observed = "stale" if state in ACTIVE_STATES and not active else state
    return {**metadata, "session_path": str(selected), "persisted_status": state,
            "status": observed, "active": active,
            "stop_requested": bool(metadata.get("stop_requested")) or (selected / "STOP").exists()}


def request_stop(session=None, output_root=DEFAULT_ROOT):
    status = session_status(session, output_root)
    if not status["active"] or status["persisted_status"] not in ACTIVE_STATES:
        raise ValueError("selected session is not active; no stop request was written")
    selected = Path(status["session_path"])
    # The collector alone stops its producer and drains the queue; no PID kill or
    # Docker shutdown. Repeated requests are intentionally harmless.
    _atomic_json(selected / "STOP", {"requested_at": _utc_now(), "reason": "user_request"})
    return {"session_path": str(selected), "status": "stop_requested",
            "message": "Offered traffic will stop; queued records are drained before exit."}


@contextmanager
def _cooperative_interrupt(event):
    previous = {}
    if threading.current_thread() is threading.main_thread():
        def stop(signum, frame):
            event.set()
        for name in ("SIGINT", "SIGBREAK"):
            if hasattr(signal, name):
                signum = getattr(signal, name)
                previous[signum] = signal.signal(signum, stop)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _audit_session(selected, sample_seconds):
    from research.data_audit import json_value
    from research.history_audit import audit_history, history_plot
    source = selected / "baseline" / "observations.jsonl"
    payload = source.read_bytes() if source.exists() else b""
    records = [json.loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
    report = audit_history(records, target="requested_rate", interval_seconds=sample_seconds)
    report.update(input_path=str(source), input_sha256=hashlib.sha256(payload).hexdigest(),
                  workload_kind="controlled_synthetic",
                  forecast_readiness="Not established; chronological horizon evaluation is required.")
    report["chart"] = history_plot(records, selected / "history.png")
    _atomic_json(selected / "history_audit.json", json_value(report))
    return {"status": report["status"], "rows": report["rows"],
            "healthy_target_rows": report.get("healthy_target_rows", 0),
            "longest_healthy_segment_seconds": report.get("longest_healthy_segment_seconds", 0),
            "path": str(selected / "history_audit.json")}


def start_session(config, *, output_root=DEFAULT_ROOT, name=None, bootstrap=DEFAULT_BOOTSTRAP,
                  topic=DEFAULT_TOPIC, group=DEFAULT_GROUP, lean=False):
    """Run in the foreground. A stop request ends demand, then retains drain data."""
    stress_topic(topic)
    validate_config(config, max_duration_seconds=MAX_DURATION_SECONDS)
    if config.get("resource_scaling", {}).get("enabled"):
        raise ValueError("history sessions must not enable resource scaling")
    if config.get("protocol", {}).get("forecast_target") != "requested_rate":
        raise ValueError("history sessions require an explicit requested_rate target")
    output_root = Path(output_root)
    session_id = f"session-{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%fZ}-{uuid4().hex[:8]}"
    selected = (output_root / session_id).resolve()
    event, last_probe, file_stop, last_progress = threading.Event(), 0.0, False, -math.inf
    last_progress_state = None
    metadata = {"schema_version": 1, "session_id": session_id, "session_path": str(selected),
                "name": name, "status": "starting", "created_at": _utc_now(), "pid": os.getpid(),
                "duration_seconds": sum(s["seconds"] for s in config["profile"]),
                "sample_seconds": config["sample_seconds"], "seed": config["seed"],
                "topic": topic, "group_id": group, "forecast_target": "requested_rate",
                "workload_kind": "controlled_synthetic", "policy": "fixed_baseline",
                "observations": 0, "stop_requested": False, "lean_mode": lean,
                "report_path": str(selected / "baseline" / "report.json"),
                "environment_path": str(selected / "environment.json") if lean else None,
                "session_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest()}

    def stop_requested():
        nonlocal last_probe, file_stop
        now = time.monotonic()
        if now - last_probe >= .25:
            last_probe, file_stop = now, (selected / "STOP").exists()
        return event.is_set() or file_stop

    def on_observation(row):
        nonlocal last_progress, last_progress_state
        elapsed = row.get("elapsed_seconds") or 0
        metadata.update(status="draining" if row.get("phase") == "drain" else "recovering" if row.get("admission_paused") else "running",
                        run_id=row.get("run_id"), updated_at=_utc_now(),
                        remaining_load_seconds=max(0, metadata["duration_seconds"] - elapsed) if row.get("phase") == "load" else 0,
                        load_progress_percent=min(100, 100 * elapsed / metadata["duration_seconds"]),
                        observations=metadata["observations"] + 1, stop_requested=stop_requested(),
                        last_observation={key: row.get(key) for key in (
                            "timestamp", "elapsed_seconds", "phase", "requested_rate", "incoming_rate",
                            "throughput", "kafka_lag", "cpu_percent", "ram_percent", "instrumentation_ready",
                            "vm_memory_available_percent", "vm_memory_free_percent", "vm_swap_used_percent",
                            "telemetry_recovery_state", "admission_paused", "telemetry_withheld_total")},
                        error_row_count=metadata.get("error_row_count", 0) + bool(row.get("errors")))
        _atomic_json(selected / "session.json", metadata)
        if time.monotonic() - last_progress >= 30 or metadata["status"] != last_progress_state:
            last_progress = time.monotonic()
            last_progress_state = metadata["status"]
            print(json.dumps({"status": metadata["status"], "observations": metadata["observations"],
                              "remaining_load_seconds": metadata["remaining_load_seconds"],
                              **metadata["last_observation"]}, allow_nan=False), flush=True)

    with experiment_lock():
        selected.mkdir(parents=True, exist_ok=False)
        with experiment_lock(selected / "session.lock"), _cooperative_interrupt(event):
            _atomic_json(selected / "config.json", config)
            _atomic_json(selected / "session.json", metadata)
            print(json.dumps({"session_path": str(selected), "status": "starting",
                              "planned_load_seconds": metadata["duration_seconds"], "seed": config["seed"]}), flush=True)
            failure_phase = "preparing_runtime"
            runtime_receipt = None
            try:
                with keep_system_awake(), lean_environment(lean, selected / "environment.json") as runtime_receipt:
                    failure_phase = "running_workload"
                    report = run_mode("baseline", config, selected / "baseline", bootstrap, topic, group,
                                      stop_requested=stop_requested, on_observation=on_observation)
                    # Preserve workload outcome even if restoring optional services
                    # fails on context exit. Neither arbitrary exception strings
                    # nor Docker output are safe session-status fields.
                    metadata.update(run_id=report.get("run_id"), experiment_status=report["status"],
                                    stop_requested=stop_requested(), stop_reason=report.get("stop_reason"),
                                    telemetry_recovery=report.get("telemetry_recovery"),
                                    preflight_attempt_count=len(report.get("preflight_attempts", [])),
                                    acknowledged=report.get("acknowledged"), final_lag=report.get("final_lag"))
                    if report["status"] not in {"completed", "stopped"}:
                        metadata.update(failure_phase=failure_phase, error_hint=FAILURE_HINTS[failure_phase])
                    failure_phase = "restoring_runtime"
                failure_phase = "auditing"
                metadata["status"] = "auditing"
                _atomic_json(selected / "session.json", metadata)
                try:
                    metadata["quality_audit"] = _audit_session(selected, config["sample_seconds"])
                except Exception as exc:
                    metadata["quality_audit"] = {"status": "failed", "error_type": type(exc).__name__}
                    metadata.setdefault("failure_phase", failure_phase)
                    metadata.setdefault("error_hint", FAILURE_HINTS[failure_phase])
                metadata["status"] = report["status"]
            except Exception as exc:
                # Existing runner writes its detailed report. Metadata/console
                # deliberately omit arbitrary exception strings and environment.
                if runtime_receipt is not None and runtime_receipt.get("status") == "restore_failed":
                    failure_phase = "restoring_runtime"
                metadata.update(status="failed", error_type=type(exc).__name__,
                                failure_phase=failure_phase, error_hint=FAILURE_HINTS[failure_phase])
            finally:
                metadata.update(ended_at=_utc_now(), updated_at=_utc_now())
                _atomic_json(selected / "session.json", metadata)
    return metadata


def _timestamp_key(row):
    value = row.get("timestamp")
    try:
        if isinstance(value, bool):
            raise ValueError("boolean timestamp")
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timestamp requires a timezone")
            number = stamp.timestamp()
        return number if math.isfinite(number) else math.inf
    except (ValueError, TypeError, OverflowError):
        return math.inf  # Preserve invalid observations at the end; never repair.


def export_sessions(sessions, output):
    """Merge finalized sessions, retaining errors, drain, gaps and run boundaries."""
    output = Path(output).resolve()
    manifest_path = output.with_suffix(".manifest.json")
    if output == manifest_path or output.exists() or manifest_path.exists():
        raise ValueError("output and companion manifest must be new distinct files")
    if not sessions:
        raise ValueError("select at least one session")
    selected_paths, seen_runs, records, inputs, common = set(), set(), [], [], None
    for session in sessions:
        status = session_status(session)
        selected = Path(status["session_path"])
        if selected in selected_paths:
            raise ValueError("duplicate session input")
        selected_paths.add(selected)
        if status["active"] or status["status"] == "stale" or not status.get("ended_at"):
            raise ValueError("only finalized inactive sessions may be exported")
        signature = (status.get("topic"), status.get("forecast_target"), status.get("workload_kind"), status.get("sample_seconds"))
        if not all(signature[:3]) or signature[1] != "requested_rate":
            raise ValueError("session must declare topic, requested_rate target and workload kind")
        if common is not None and signature != common:
            raise ValueError("session topic, target, workload kind and sampling interval must match")
        common = signature
        source = selected / "baseline" / "observations.jsonl"
        payload = source.read_bytes()
        rows = [json.loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
        run_ids = {row.get("run_id") for row in rows}
        if not run_ids or any(not isinstance(run, str) or not run for run in run_ids):
            raise ValueError("every nonempty session must preserve run_id on each observation")
        if run_ids != {status.get("run_id")}:
            raise ValueError("observation run_id must match its session metadata")
        if seen_runs & run_ids:
            raise ValueError("duplicate run_id across selected sessions")
        if any(row.get("topic") != common[0] for row in rows):
            raise ValueError("each observation topic must match its session")
        if any("requested_rate" not in row for row in rows):
            raise ValueError("each observation must retain the requested_rate target")
        seen_runs.update(run_ids)
        records.extend(rows)
        inputs.append({"session_path": str(selected), "session_id": status.get("session_id"),
                       "status": status["status"], "quality_audit": status.get("quality_audit"),
                       "input_path": str(source), "input_sha256": hashlib.sha256(payload).hexdigest(),
                       "rows": len(rows), "run_ids": sorted(run_ids)})
    records.sort(key=_timestamp_key)  # stable for equal/malformed timestamps
    manifest = {"schema_version": 1, "created_at": _utc_now(), "output": str(output),
                "rows": len(records), "topic": common[0], "forecast_target": common[1],
                "workload_kind": common[2], "sample_seconds": common[3], "inputs": inputs,
                "semantics": "Original rows retained; stable timestamp sort only. No imputation, filtering or run concatenation.",
                "forecast_readiness": "Not established; audit and chronological horizon evaluation are required."}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation never overwrites an existing export. Write the JSONL
    # before its completion manifest so an interrupted export is identifiable.
    digest = hashlib.sha256()
    with output.open("xb") as stream:
        for row in records:
            line = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            stream.write(line)
            digest.update(line)
    manifest["output_sha256"] = digest.hexdigest()
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="collect one finite variable-load session in the foreground")
    duration = start.add_mutually_exclusive_group()
    duration.add_argument("--minutes", type=float)
    duration.add_argument("--hours", type=float)
    start.add_argument("--min-rate", type=float, default=5)
    start.add_argument("--max-rate", type=float, default=60)
    start.add_argument("--seed", type=int)
    start.add_argument("--name")
    start.add_argument("--sample-seconds", type=float, default=5)
    start.add_argument("--recovery-seconds", type=float, default=60, help="Maximum transport recovery budget, 1..120 seconds")
    start.add_argument("--recovery-errors", type=int, default=5, help="Stop at this many consecutive failed measurements, 1..10")
    start.add_argument("--preflight-seconds", type=float, default=60, help="Startup transport retry budget, 0..120 seconds")
    start.add_argument("--lean", action="store_true", help="Temporarily stop optional services to free VM memory; restore them after this session")
    start.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    start.add_argument("--bootstrap", default=DEFAULT_BOOTSTRAP)
    start.add_argument("--topic", default=DEFAULT_TOPIC)
    start.add_argument("--group", default=DEFAULT_GROUP)
    start.add_argument("--dry-run", action="store_true", help="print validated schedule without writing files or starting traffic")
    for command in ("status", "stop"):
        child = commands.add_parser(command)
        child.add_argument("--session", type=Path, help="session directory; default latest under output root")
        child.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    export = commands.add_parser("export", help="merge finalized selected sessions without repairing missing history")
    export.add_argument("--sessions", type=Path, nargs="+", required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            seconds = args.hours * 3600 if args.hours is not None else (args.minutes if args.minutes is not None else 120) * 60
            config = build_config(seconds, args.min_rate, args.max_rate, args.seed, args.sample_seconds,
                                  recovery_seconds=args.recovery_seconds, recovery_errors=args.recovery_errors,
                                  preflight_seconds=args.preflight_seconds)
            stress_topic(args.topic)
            if args.dry_run:
                result = {"status": "dry_run", "planned_load_seconds": seconds,
                          "projected_records": config["max_records"], "lean_mode": args.lean,
                          "optional_services_to_stop_if_running": sorted(OPTIONAL_SERVICES) if args.lean else [], "config": config}
            else:
                result = start_session(config, output_root=args.output_root, name=args.name,
                                       bootstrap=args.bootstrap, topic=args.topic, group=args.group, lean=args.lean)
        elif args.command == "status":
            result = session_status(args.session, args.output_root)
        elif args.command == "stop":
            result = request_stop(args.session, args.output_root)
        else:
            result = export_sessions(args.sessions, args.output)
    except (ValueError, OSError, RuntimeError) as exc:
        # Validation errors contain our own bounded descriptions, never env or
        # provider payloads. Unexpected I/O/runtime failures disclose type only.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        if isinstance(exc, RuntimeError) and str(exc).startswith("another traffic experiment holds the lock"):
            message = "Another traffic session/experiment is active; wait for its drain to finish."
        print(json.dumps({"status": "error", "error": message}), flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    if args.command == "start" and not args.dry_run:
        return int(result["status"] not in {"completed", "stopped"} or result.get("quality_audit", {}).get("status") == "failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
