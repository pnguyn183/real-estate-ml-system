"""Paired, rate-only trials of the real FeedbackAgent on the stress pipeline.

Offered arrivals, publication, completion polling, telemetry and LLM calls run
independently. Neither a slow provider nor a closed gate erases offered demand.
Only synthetic stress records are sent; existing data/offsets are never reset.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import signal
from threading import Event, Thread
import time
from uuid import uuid4

from agents.control_agent import (ControlSettings, FeedbackAgent, atomic_json,
    control_session, finite, host_snapshot, observation_problem)
from agents.runtime_policy import RuntimePolicy
from research.run_experiment import code_identity, experiment_lock, preflight
from research.telemetry import TelemetryCollector

TOPIC = "real_estate_stress_raw"
GROUP = "real_estate_training_pipeline"
BOOTSTRAP = "localhost:9092,localhost:9093,localhost:9094"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_config(config):
    """Bound time, demand and queue size before touching Docker or calling APIs."""
    cfg = dict(config)
    bounds = {
        "pairs": (1, 10), "seed": (0, 2**31 - 1), "baseline_rate": (1, 10000), "sample_seconds": (1, 30),
        "decision_seconds": (5, 120), "timeout_seconds": (1, 60),
        "lease_seconds": (10, 300), "max_sample_age_seconds": (5, 120),
        "target_latency_seconds": (.05, 60), "min_memory_available_percent": (10, 50),
        "hard_lag": (100, 50000), "max_records": (1, 1000000), "max_queue": (1, 10000),
        "max_wait_seconds": (1, 120), "drain_seconds": (5, 600),
        "completion_poll_seconds": (.1, 2), "calibration_stage_seconds": (20, 120),
        "light_seconds": (20, 300), "overload_seconds": (30, 600), "recovery_seconds": (20, 300),
    }
    integers = {"pairs", "seed", "max_records", "max_queue", "hard_lag"}
    for key, (low, high) in bounds.items():
        val = cfg.get(key)
        if (isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val)
                or not low <= val <= high or (key in integers and type(val) is not int)):
            raise ValueError("Invalid experiment setting: " + key)
    rates = cfg.get("calibration_rates")
    if (not isinstance(rates, list) or not 1 <= len(rates) <= 12
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 10000 for v in rates)
            or rates != sorted(set(rates))):
        raise ValueError("calibration_rates must be increasing positive rates <= 10000")
    if cfg["sample_seconds"] > cfg["decision_seconds"] or cfg["lease_seconds"] <= cfg["decision_seconds"] + cfg["timeout_seconds"]:
        raise ValueError("Lease must exceed decision cadence plus model timeout")
    if cfg["max_sample_age_seconds"] <= cfg["timeout_seconds"]:
        raise ValueError("Telemetry lifetime must exceed model timeout")
    if cfg["calibration_stage_seconds"] < 4 * cfg["sample_seconds"]:
        raise ValueError("Calibration stages need room for three full telemetry intervals")
    if cfg["overload_seconds"] < cfg["decision_seconds"] + cfg["timeout_seconds"] + 3 * cfg["sample_seconds"]:
        raise ValueError("Overload stage must allow a model decision and sustained recovery observations")
    if cfg.get("profile") is not None:
        validate_profile(cfg["profile"], cfg["max_records"])
        if max(s["rate"] for s in cfg["profile"]) > cfg["baseline_rate"]:
            raise ValueError("baseline_rate must cover offered demand")
        if ([s["name"] for s in cfg["profile"]] != ["light", "overload", "recovery"]
                or any(s["rate"] >= cfg["profile"][1]["rate"] for s in (cfg["profile"][0], cfg["profile"][-1]))):
            raise ValueError("Explicit profiles require light, overload, recovery with lower light/recovery demand")
        if cfg["profile"][1]["seconds"] < cfg["decision_seconds"] + cfg["timeout_seconds"] + 3 * cfg["sample_seconds"]:
            raise ValueError("Explicit overload stage is too short to observe model response")
    if max(rates) > cfg["baseline_rate"]:
        raise ValueError("baseline_rate must cover calibration demand")
    validate_profile(calibration_profile(cfg), cfg["max_records"])
    return cfg


def validate_profile(profile, max_records):
    if not isinstance(profile, list) or not 1 <= len(profile) <= 12:
        raise ValueError("Invalid load profile")
    for row in profile:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]:
            raise ValueError("Every stage needs a name")
        for key, upper in (("rate", 10000), ("seconds", 600)):
            value = row.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= upper:
                raise ValueError("Invalid stage " + key)
    if sum(math.ceil(s["rate"] * s["seconds"]) for s in profile) > max_records:
        raise ValueError("Profile exceeds max_records; it will not be silently truncated")
    if sum(s["seconds"] for s in profile) > 3600:
        raise ValueError("Profile exceeds one hour")


def calibration_profile(cfg):
    return [{"name": "calibration_" + str(i), "rate": r, "seconds": cfg["calibration_stage_seconds"]}
            for i, r in enumerate(cfg["calibration_rates"])]


def stage_at(profile, elapsed):
    end = 0.0
    for stage in profile:
        end += stage["seconds"]
        if elapsed < end:
            return stage["name"], stage["rate"]
    return "drain", 0.0


def overload_evidence(samples, target, count=3):
    """A measured workload limit, never a claim of absolute machine capacity."""
    for start in range(len(samples) - count + 1):
        window = samples[start:start + count]
        if (len({r.get("stage") for r in window}) != 1
                or any(r.get("errors") or r.get("instrumentation_ready") is not True for r in window)):
            continue
        rates = [r.get("incoming_rate", 0) or 0 for r in window]
        demand = window[-1].get("offered_rate", 0)
        # An underpowered load generator is not pipeline saturation.
        if (not finite(demand) or demand <= 0 or any(not finite(rate) or rate < .8 * demand for rate in rates)
                or any(r.get("offered_rate") != demand for r in window)):
            continue
        if any(not finite(r.get("timestamp")) for r in window):
            continue
        if any(not 0 < b["timestamp"] - a["timestamp"] <= 2.5 * (b.get("interval_seconds") or 5)
               for a, b in zip(window, window[1:])):
            continue
        latencies = [r.get("latency_p95_seconds") for r in window]
        lags = [r.get("kafka_lag") for r in window]
        slow = all(finite(v) and v > target for v in latencies)
        growing = (all(finite(v) for v in lags)
                   and lags[-1] - lags[0] >= 20 and all(b > a for a, b in zip(lags, lags[1:])))
        if slow or growing:
            return {"rate": demand, "stage": window[-1]["stage"], "timestamp": window[-1]["timestamp"],
                    "reason": "sustained_latency" if slow else "growing_backlog", "samples": count}
    return None


def settings(cfg, profile):
    return ControlSettings(source_topic=TOPIC, max_rate=cfg["baseline_rate"],
        **{k: cfg[k] for k in ("sample_seconds", "decision_seconds", "timeout_seconds", "lease_seconds",
            "max_sample_age_seconds", "min_memory_available_percent", "target_latency_seconds")})


class CompletionObserver:
    """Read committed offsets without joining the processing consumer group."""
    def __init__(self, bootstrap, driver, interval):
        self.bootstrap, self.driver, self.interval = bootstrap, driver, interval
        self.stop_event = Event()
        self.errors = []
        self.max_poll_gap_seconds = 0.0
        self.thread = Thread(target=self._run, name="completion-observer", daemon=True)

    def _run(self):
        consumer = None
        try:
            from confluent_kafka import Consumer, TopicPartition
            consumer = Consumer({"bootstrap.servers": self.bootstrap, "group.id": GROUP,
                "enable.auto.commit": False, "enable.auto.offset.store": False, "socket.timeout.ms": 2000})
            partitions = [TopicPartition(TOPIC, i) for i in range(3)]
            previous = time.monotonic()
            while not self.stop_event.is_set():
                try:
                    offsets = consumer.committed(partitions, timeout=2)
                    now = time.monotonic()
                    self.max_poll_gap_seconds = max(self.max_poll_gap_seconds, now - previous)
                    previous = now
                    committed = {}
                    for partition in offsets:
                        # Kafka may return successful and failed partitions in
                        # one response without raising a top-level exception.
                        # Never credit a failed partition's offset as completion.
                        if getattr(partition, "error", None) is not None:
                            if len(self.errors) < 100:
                                self.errors.append({"timestamp": time.time(),
                                    "error_type": "KafkaPartitionError", "partition": partition.partition})
                        elif partition.offset >= 0:
                            committed[partition.partition] = partition.offset
                    self.driver.observe_committed(committed, time.time())
                except Exception as exc:
                    if len(self.errors) < 100:
                        self.errors.append({"timestamp": time.time(), "error_type": type(exc).__name__})
                self.stop_event.wait(self.interval)
        except Exception as exc:
            self.errors.append({"timestamp": time.time(), "error_type": type(exc).__name__})
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception as exc:
                    self.errors.append({"timestamp": time.time(), "error_type": type(exc).__name__})

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(5)
        if self.thread.is_alive():
            raise RuntimeError("Completion observer did not stop")


def collect(collector, runtime=None):
    row = collector.sample()
    row["host"] = host_snapshot()
    if runtime is not None:
        from research.llm_runtime import verify_runtime
        try:
            verify_runtime(runtime, deep_artifact_check=False)
        except RuntimeError:
            row.setdefault("errors", []).append("runtime_allocation_or_isolation_changed")
    return row


def offsets_total(sample):
    return sum(p["high_offset"] for p in sample["partitions"].values())


def append_jsonl(path, row):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, allow_nan=False, ensure_ascii=True) + "\n")


def safe_failure_reason(exc):
    """Expose locally authored validation reasons, never transport/API payloads."""
    frames = []
    trace = exc.__traceback__
    while trace is not None:
        frames.append(Path(trace.tb_frame.f_code.co_filename).name)
        trace = trace.tb_next
    if (isinstance(exc, (RuntimeError, ValueError)) and frames
            and frames[-1] in {"llm_runtime.py", "llm_experiment.py", "run_experiment.py"}):
        return str(exc)[:400]
    return type(exc).__name__


def idle_preflight(collector, cfg, bootstrap, stop):
    preflight(bootstrap, TOPIC)
    deadline = time.monotonic() + cfg["drain_seconds"]
    collect(collector)  # Counter derivatives need a previous observation.
    idle = 0
    while time.monotonic() < deadline and not stop.is_set():
        sample = collect(collector)
        problem = observation_problem(sample, time.time(), settings(cfg, [{"rate": 1}]))
        if problem:
            raise RuntimeError("Preflight: " + problem)
        if sample["incoming_rate"] > 0:
            raise RuntimeError("Preflight found another stress producer")
        idle = idle + 1 if sample["kafka_lag"] == 0 else 0
        if idle >= 2:
            return sample
        stop.wait(1)
    raise RuntimeError("Preflight queue did not drain or was cancelled")


def run_trial(mode, pair_id, cfg, profile, output, router, actuator, runtime, stop, *, calibration=False):
    from confluent_kafka import Producer
    from research.llm_load import LoadDriver
    from research.llm_runtime import verify_runtime
    if mode not in {"baseline", "llm"} or (calibration and mode != "baseline"):
        raise ValueError("Use baseline for calibration, and baseline or llm for paired trials")
    output.mkdir(parents=True, exist_ok=False)
    policy_path = output / "control" / "policy.json"
    control = settings(cfg, profile)
    agent = FeedbackAgent(control, router, actuator, policy_path, apply=True)
    reader = RuntimePolicy(True, policy_path, TOPIC)
    run_id = "llmproof-" + mode + "-" + uuid4().hex[:12]
    result = {"schema_version": 1, "run_id": run_id, "pair_id": pair_id, "mode": mode,
        "status": "preparing", "profile": profile, "initial_rate": control.max_rate,
        "sample_seconds": cfg["sample_seconds"], "training_disabled": True,
        "config_fingerprint": fingerprint({"profile": profile, "seed": cfg["seed"] + pair_id,
            "max_queue": cfg["max_queue"], "max_wait_seconds": cfg["max_wait_seconds"],
            "sample_seconds": cfg["sample_seconds"], "target_latency_seconds": cfg["target_latency_seconds"]}),
        "samples": [], "actions": [], "load_started_at": None, "load_ended_at": None,
        "semantics": {"committed": "Consumer-group offset observed past record; not producer ACK or a per-record success verdict",
            "offered_latency": "Scheduled arrival to first observed commit, an upper bound including admission wait",
            "pipeline_latency": "Worker histogram from actual Kafka send through processing commit",
            "training": "Stopped for both arms; model permission cannot start a fit", "resources": "Fixed in both arms"}}
    collector = driver = observer = None
    driver_started = observer_started = False
    future = sample_future = None
    model_pool = sample_pool = None
    session = ExitStack()
    atomic_json(output / "run.json", result)

    def action(receipt):
        result["actions"].append(receipt)
        append_jsonl(output / "actions.jsonl", receipt)

    def halt(reason, status="guard_stopped"):
        if result["status"] not in {"guard_stopped", "invalid", "failed", "cancelled"}:
            result["status"], result["stop_reason"] = status, reason
            # Stop local admission first: an in-flight model may still publish
            # a lease, but it can no longer restart this driver.
            driver.stop(reason)
            action(agent.pause(reason))

    try:
        session.enter_context(control_session(agent))
        collector = TelemetryCollector(bootstrap_servers=cfg.get("bootstrap_servers", BOOTSTRAP))
        producer = Producer({"bootstrap.servers": cfg.get("bootstrap_servers", BOOTSTRAP), "acks": "all",
            "enable.idempotence": True, "linger.ms": 5, "delivery.timeout.ms": 10000,
            "request.timeout.ms": 5000, "queue.buffering.max.messages": 10000})
        driver = LoadDriver(profile, cfg["seed"] + pair_id, run_id, producer, lambda: reader.read(), output,
            topic=TOPIC, max_queue=cfg["max_queue"], max_wait_seconds=cfg["max_wait_seconds"],
            max_records=cfg["max_records"])
        observer = CompletionObserver(cfg.get("bootstrap_servers", BOOTSTRAP), driver, cfg["completion_poll_seconds"])
        model_pool = ThreadPoolExecutor(max_workers=1)
        sample_pool = ThreadPoolExecutor(max_workers=1)
        result["resource_fingerprint"] = verify_runtime(runtime)["fingerprint"]
        idle = idle_preflight(collector, cfg, cfg.get("bootstrap_servers", BOOTSTRAP), stop)
        initial_offsets = offsets_total(idle)
        atomic_json(policy_path.with_name("training.json"), {"status": "experiment_disabled", "updated_at": time.time()})
        agent.publish(control.max_rate, False, "fixed-initial-" + run_id)
        action({"timestamp": time.time(), "status": "applied", "action_origin": "baseline",
            "decision": {"rate_per_second": control.max_rate}, "reason": "Common initial limit; no LLM decision yet"})
        start = time.time() + .25
        result["load_started_at"] = start
        result["load_ended_at"] = start + sum(s["seconds"] for s in profile)
        driver_started = True
        driver.start(start_at=start)
        observer_started = True
        observer.start()
        result["status"] = "running"
        atomic_json(output / "run.json", result)
        next_sample = time.monotonic()
        next_decision = start + cfg["decision_seconds"]
        deadline = result["load_ended_at"] + cfg["max_wait_seconds"] + cfg["drain_seconds"]
        previous_rejected = 0
        consecutive_drained = 0
        latest_sample = idle
        while time.time() < deadline and not stop.is_set():
            if future is not None and future.done():
                receipt = future.result()
                action(receipt)
                future = None
                if receipt["status"] in {"paused", "would_pause"}:
                    halt(receipt.get("reason", "model_decision_unavailable"))
                elif result["status"] in {"guard_stopped", "invalid"}:
                    action(agent.pause("experiment_stopped"))
            if sample_future is None and time.monotonic() >= next_sample:
                sample_future = sample_pool.submit(collect, collector, runtime)
                next_sample = time.monotonic() + cfg["sample_seconds"]
            if sample_future is not None and sample_future.done():
                sample = sample_future.result()
                sample_future = None
                counts = driver.snapshot()
                latest_sample = sample
                stage, offered = stage_at(profile, sample["timestamp"] - start)
                interval = sample.get("interval_seconds") or cfg["sample_seconds"]
                sample.update(stage=stage, offered_rate=offered, pending_offered=counts.get("queued", 0),
                    pending=counts.get("pending", 0), admission_limit=reader.read().rate_per_second,
                    rejected_rate=(counts["rejected"] - previous_rejected) / interval, accounting=counts)
                previous_rejected = counts["rejected"]
                result["samples"].append(sample)
                append_jsonl(output / "telemetry.jsonl", sample)
                problem = observation_problem(sample, time.time(), control)
                if sample.get("kafka_lag", 0) is not None and sample["kafka_lag"] >= cfg["hard_lag"]:
                    problem = "hard_lag_budget"
                if problem and result["status"] not in {"guard_stopped", "invalid"}:
                    halt(problem)
                if result["status"] == "running":
                    if mode == "baseline":
                        agent.publish(control.max_rate, False, "baseline-" + run_id)
                    elif future is None:
                        if time.time() >= next_decision:
                            future = model_pool.submit(agent.tick, sample, force=True)
                            next_decision = time.time() + cfg["decision_seconds"]
                        else:
                            # Keep measured history without provoking an early model call.
                            agent.last_decision = time.time()
                            receipt = agent.tick(sample)
                            if receipt["status"] != "observed":
                                action(receipt)
                                if receipt["status"] in {"paused", "would_pause"}:
                                    halt(receipt.get("reason", "observation_rejected"))
                    if calibration:
                        evidence = overload_evidence(result["samples"], cfg["target_latency_seconds"])
                        if evidence:
                            result["calibration_evidence"] = evidence
                            result["status"] = "calibration_complete"
                            result["load_ended_at"] = time.time()
                            driver.stop("calibration_complete")
                print(json.dumps({"run": run_id, "mode": mode, "stage": stage,
                    "offered": counts["offered"], "admitted": counts["admitted"], "committed": counts["committed"],
                    "lag": sample.get("kafka_lag"), "p95": sample.get("latency_p95_seconds"),
                    "limit": sample["admission_limit"], "status": result["status"]}), flush=True)
                done = driver.sending_done.is_set() and counts.get("pending", 0) == 0 and sample.get("kafka_lag") == 0
                consecutive_drained = consecutive_drained + 1 if done else 0
                if consecutive_drained >= 2:
                    if result["status"] == "running":
                        result["status"] = "completed"
                    break
            # Memory check stays responsive even during a slow telemetry/model call.
            if result["status"] == "running":
                import psutil
                mem = psutil.virtual_memory()
                if 100 * mem.available / mem.total <= cfg["min_memory_available_percent"]:
                    halt("emergency_host_memory")
                elif not reader.read().valid:
                    halt("policy_unavailable")
                elif observation_problem(latest_sample, time.time(), control):
                    halt(observation_problem(latest_sample, time.time(), control))
                elif observer.errors:
                    halt("completion_observation_errors", "invalid")
                else:
                    health = driver.health()
                    if health["errors"] or health["rate_read_errors"]:
                        halt("load_generator_errors", "invalid")
            if result["status"] in {"guard_stopped", "invalid"}:
                # Continue observation of bounded drain; never resume the load automatically.
                deadline = min(deadline, time.time() + cfg["drain_seconds"])
            stop.wait(.1)
        if stop.is_set():
            result["status"] = "cancelled"
        elif result["status"] == "running":
            result["status"] = "drain_timeout"
        driver.stop("trial_finished")
        if not driver.join(15):
            raise RuntimeError("Load threads failed to stop")
        if future is not None:
            action(future.result(timeout=cfg["timeout_seconds"] + 30))
            future = None
        if sample_future is not None:
            sample_future.result(timeout=30)
            sample_future = None
        final_sample = collect(collector)
        final_problem = observation_problem(final_sample, time.time(), control)
        if final_problem and result["status"] in {"completed", "calibration_complete"}:
            result["status"], result["stop_reason"] = "invalid", "final_" + final_problem
        elif final_sample.get("kafka_lag") != 0 and result["status"] in {"completed", "calibration_complete"}:
            result["status"], result["stop_reason"] = "invalid", "final_queue_not_drained"
        result["offset_growth"] = offsets_total(final_sample) - initial_offsets
        counts = driver.snapshot()
        if result["offset_growth"] != counts.get("acknowledged", counts.get("delivery_acknowledged", 0)):
            result["status"], result["stop_reason"] = "invalid", "concurrent_traffic_or_unconfirmed_delivery"
        if verify_runtime(runtime)["fingerprint"] != result["resource_fingerprint"]:
            result["status"], result["stop_reason"] = "invalid", "resource_allocation_changed"
    except BaseException as exc:
        result["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
        result["error_type"] = type(exc).__name__
        # Keep expected local validation reasons, never arbitrary provider/transport payloads.
        result["stop_reason"] = safe_failure_reason(exc)
    finally:
        cleanup_errors = []
        def cleanup(label, function):
            try:
                function()
            except BaseException as exc:
                cleanup_errors.append({"step": label, "error_type": type(exc).__name__})
        if driver is not None:
            cleanup("stop_load", lambda: driver.stop("trial_finished"))
        if driver_started:
            def join_load():
                if not driver.join(15):
                    raise RuntimeError("Load threads failed to stop")
            cleanup("join_load", join_load)
        if model_pool is not None:
            cleanup("model_pool", lambda: model_pool.shutdown(wait=True, cancel_futures=True))
        if sample_pool is not None:
            cleanup("sample_pool", lambda: sample_pool.shutdown(wait=True, cancel_futures=True))
        if future is not None and future.done() and not future.cancelled():
            cleanup("last_decision", lambda: action(future.result()))
        # Hold the controller lock until in-flight LLM calls finish, then revoke.
        cleanup("controller_session", session.close)
        # control_session revokes on all exits. Include this receipt, as well as
        # any guard logged just before an exception, in the durable action trace.
        def reconcile_actions():
            path = policy_path.with_name("decisions.jsonl")
            if path.exists():
                known = {row.get("decision_id") for row in result["actions"]}
                for line in path.read_text(encoding="utf-8").splitlines():
                    receipt = json.loads(line)
                    if receipt.get("decision_id") not in known:
                        action(receipt)
                        known.add(receipt.get("decision_id"))
            result["actions"].sort(key=lambda row: row["timestamp"])
        cleanup("action_receipts", reconcile_actions)
        if observer_started:
            cleanup("completion_observer", observer.close)
        if collector is not None:
            cleanup("collector", collector.close)
        result["cleanup_errors"] = cleanup_errors
        if cleanup_errors:
            result["status"] = "failed"
        result["accounting"] = driver.snapshot() if driver is not None else {}
        result["completion_observer"] = {"errors": observer.errors if observer else [],
            "max_poll_gap_seconds": observer.max_poll_gap_seconds if observer else None}
        if observer is not None and observer.errors and result["status"] in {"completed", "calibration_complete"}:
            result["status"], result["stop_reason"] = "invalid", "completion_observation_errors"
        if result["accounting"].get("errors") or result["accounting"].get("rate_read_errors"):
            result["status"], result["stop_reason"] = "invalid", "load_generator_errors"
        result["finished_at"] = time.time()
        if driver is not None:
            cleanup("load_artifacts", driver.write_artifacts)
        if cleanup_errors:
            result["status"] = "failed"
        atomic_json(output / "run.json", result)
    return result


def calibrated_profile(run, cfg):
    evidence = run.get("calibration_evidence")
    if run["status"] not in {"completed", "calibration_complete"} or not evidence:
        return None
    high = evidence["rate"]
    profile = [{"name": "light", "rate": min(10, high / 4), "seconds": cfg["light_seconds"]},
        {"name": "overload", "rate": high, "seconds": cfg["overload_seconds"]},
        {"name": "recovery", "rate": min(10, high / 4), "seconds": cfg["recovery_seconds"]}]
    validate_profile(profile, cfg["max_records"])
    return profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("research/llm_experiment.json"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--calibrate-only", action="store_true")
    parser.add_argument("--restore", type=Path, help="Restore exact containers from an interrupted isolation manifest")
    parser.add_argument("--fallback-model", choices=["gemini-3.5-flash-lite"], help="Session-only fallback using existing Gemini key")
    args = parser.parse_args(argv)
    from research.llm_runtime import isolated_runtime, restore_runtime
    if args.restore:
        with experiment_lock():
            restore_runtime(args.restore)
        return 0
    cfg = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    from dotenv import load_dotenv
    load_dotenv(override=False)
    if args.fallback_model:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            parser.error("--fallback-model requires GEMINI_API_KEY")
        for suffix, value in {"PROVIDER": "gemini", "BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai",
                              "MODEL": args.fallback_model, "API_KEY": key}.items():
            os.environ["CONTROL_LLM_FALLBACK_" + suffix] = value
    from agents.model_router import build_router_from_env
    from agents.compose_resources import ComposeResourceActuator, ComposeResourceConfig
    from research.llm_report import write_report
    from research.runtime import keep_system_awake
    router = build_router_from_env(prefix="CONTROL_LLM", fallback_to_llm=True)
    if not args.calibrate_only and not router.enabled:
        parser.error("Configure a CONTROL_LLM/LLM model and key before paired trials")
    output = args.output or Path("runtime/research/llm-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "config.json", cfg)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    runs = []
    session = {"status": "preparing", "started_at": time.time(), "topic": TOPIC, "rate_only": True}
    try:
        with ExitStack() as stack:
            stack.enter_context(experiment_lock())
            stack.enter_context(keep_system_awake())
            runtime = stack.enter_context(isolated_runtime(output / "isolation.json", stop_requested=stop.is_set,
                min_memory_available_percent=cfg["min_memory_available_percent"],
                on_wait=lambda *a: print("Waiting for host RAM above emergency floor", flush=True)))
            identity = code_identity()
            identity["llm_sources"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                [Path("agents/control_agent.py"), *Path("research").glob("llm_*.py")]}
            atomic_json(output / "identity.json", identity)
            actuator = ComposeResourceActuator(ComposeResourceConfig(compose_path="docker-compose.yml",
                project_name="real-estate-ml-system", enabled=False,
                allowed_services=("processor", "processor-2", "processor-3")))
            profile = cfg.get("profile")
            if profile is None or args.calibrate_only:
                calibration = run_trial("baseline", 0, cfg, calibration_profile(cfg), output / "calibration",
                    router, actuator, runtime, stop, calibration=True)
                profile = calibrated_profile(calibration, cfg)
                atomic_json(output / "calibration.json", {"status": calibration["status"],
                    "evidence": calibration.get("calibration_evidence"), "selected_profile": profile,
                    "scope": "First sustained overload observed for this workload and allocation, not absolute capacity"})
                if profile is None or args.calibrate_only:
                    session["status"] = "calibrated" if profile else "calibration_inconclusive"
                    return 0 if profile else 2
            atomic_json(output / "selected_profile.json", profile)
            for pair in range(cfg["pairs"]):
                order = ("baseline", "llm") if pair % 2 == 0 else ("llm", "baseline")
                for mode in order:
                    if stop.is_set():
                        break
                    run = run_trial(mode, pair + 1, cfg, profile, output / f"pair-{pair + 1}-{mode}",
                        router, actuator, runtime, stop)
                    runs.append(run)
                    write_report(runs, output, target_latency_seconds=cfg["target_latency_seconds"])
                    if run["status"] != "completed":
                        stop.set()
                        break
                if stop.is_set():
                    break
            session["status"] = "completed" if len(runs) == 2 * cfg["pairs"] and not stop.is_set() else "incomplete"
    except Exception as exc:
        session.update(status="blocked", error_type=type(exc).__name__, reason=safe_failure_reason(exc))
        print("Experiment blocked: " + session["reason"], flush=True)
    finally:
        session["finished_at"] = time.time()
        atomic_json(output / "session.json", session)
        write_report(runs, output, target_latency_seconds=cfg["target_latency_seconds"])
        print("Evidence: " + str(output), flush=True)
    return 0 if session["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
