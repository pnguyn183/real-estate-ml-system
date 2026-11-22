"""Bounded research harness for repeated optional-container lifecycle checks.

The coordinator reuses :class:`ContainerLifecycleActuator`; it never bypasses
that actuator's identity, telemetry, dependency, drain, cooldown or memory
guards.  Dry-run is the default.  Its output is harness evidence only and must
not be reported as a live, LLM-selected or causal-RAM result without a separate
archived experiment.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import math
from pathlib import Path
import time
from uuid import uuid4

from agents.control_agent import atomic_json


MAX_CYCLES = 3
EVIDENCE_CLASSIFICATION = "research_harness_output_not_live_evidence"


class LifecycleCycleError(RuntimeError):
    """A bounded harness invariant or actuator precondition was not satisfied."""


def _bounded_number(value, name, *, low, high):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"{name} must be between {low} and {high}")
    return float(value)


def _service_view(actuator, service):
    observation = actuator.observe()
    if observation.get("status") != "observed":
        raise LifecycleCycleError("Container lifecycle observation is unavailable")
    view = observation.get("services", {}).get(service)
    if not isinstance(view, Mapping) or not isinstance(view.get("id"), str):
        raise LifecycleCycleError("The selected optional service is unavailable")
    return view


def _wait_for(actuator, service, container_id, predicate, *, clock, sleep,
              timeout_seconds, poll_seconds):
    # The iteration cap keeps injected/offline clocks bounded even if a broken
    # sleep implementation does not advance time.
    deadline = clock() + timeout_seconds
    polls = math.ceil(timeout_seconds / poll_seconds) + 2
    for _ in range(polls):
        view = _service_view(actuator, service)
        if view["id"] != container_id:
            raise LifecycleCycleError("Container identity changed during repeated lifecycle check")
        if predicate(view):
            return view
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(poll_seconds, remaining))
    raise LifecycleCycleError("Timed out waiting for a guarded lifecycle transition")


def _sample(sample_provider):
    value = sample_provider()
    if not isinstance(value, Mapping):
        raise LifecycleCycleError("Telemetry provider did not return a sample")
    return dict(value)


def _safe_error(exc):
    message = str(exc).replace("\r", " ").replace("\n", " ")[:300]
    return f"{type(exc).__name__}: {message}"


def _trainer_drain_verified(receipt):
    proof = receipt.get("drain")
    if not isinstance(proof, Mapping):
        return False
    acknowledged = proof.get("acknowledged_at")
    validated = proof.get("validated_at")
    expires = proof.get("expires_at")
    return bool(proof.get("kind") == "trainer_drain_acknowledgement"
                and isinstance(proof.get("request_id"), str) and proof["request_id"]
                and proof.get("hostname_matched") is True
                and proof.get("training_disabled_lease_verified") is True
                and isinstance(proof.get("training_policy_decision_id"), str)
                and bool(proof["training_policy_decision_id"])
                and isinstance(proof.get("source_topic"), str) and bool(proof["source_topic"])
                and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) for value in (
                            acknowledged, validated, expires,
                            proof.get("training_policy_expires_at")))
                and acknowledged <= validated < expires
                and validated < proof["training_policy_expires_at"])


def run_lifecycle_cycles(actuator, service: str, sample_provider: Callable[[], Mapping], *,
                         cycles: int = MAX_CYCLES, execute: bool = False,
                         timeout_seconds: float = 120.0, poll_seconds: float = 1.0,
                         clock: Callable[[], float] = time.monotonic,
                         wall_clock: Callable[[], float] = time.time,
                         sleep: Callable[[float], None] = time.sleep,
                         output_path: Path | None = None) -> dict:
    """Plan or execute at most three guarded stop/start cycles.

    ``execute=False`` performs one actuator dry-run and never represents three
    completed cycles.  Real execution requires an explicit ``execute=True`` and
    still retains the non-live evidence label: independent archival and review
    are required before any experiment claim is made.
    """
    if type(cycles) is not int or not 1 <= cycles <= MAX_CYCLES:
        raise ValueError(f"cycles must be an integer in 1..{MAX_CYCLES}")
    if type(execute) is not bool:
        raise ValueError("execute must be a boolean")
    if not isinstance(service, str) or service not in actuator.config.allowed_services:
        raise ValueError("service must be explicitly allowlisted by the actuator")
    timeout_seconds = _bounded_number(timeout_seconds, "timeout_seconds", low=1, high=900)
    poll_seconds = _bounded_number(poll_seconds, "poll_seconds", low=.01, high=10)
    if poll_seconds > timeout_seconds:
        raise ValueError("poll_seconds cannot exceed timeout_seconds")
    if not callable(sample_provider) or not callable(clock) or not callable(wall_clock) or not callable(sleep):
        raise TypeError("sample_provider and clocks must be callable")
    if output_path is not None:
        output_path = Path(output_path)

    run_id = uuid4().hex
    started_at = wall_clock()
    initial = _service_view(actuator, service)
    container_id = initial["id"]
    result = {
        "schema_version": 1,
        "kind": "bounded_container_lifecycle_cycles",
        "run_id": run_id,
        "service": service,
        "container_id": container_id,
        "requested_cycles": cycles,
        "completed_cycles": 0,
        "execute_requested": execute,
        "evidence_classification": EVIDENCE_CLASSIFICATION,
        "live_evidence": False,
        "llm_selected_actions": False,
        "causal_ram_effect_verified": False,
        "started_at": started_at,
        "cycles": [],
        "restore": None,
        "limitations": [
            "Harness output alone is not live experiment evidence.",
            "Actions are coordinator-selected, not LLM-selected.",
            "Repeated stop/start does not establish causal RAM improvement or overload stability.",
        ],
    }

    if not execute:
        try:
            _wait_for(actuator, service, container_id,
                      lambda view: "stop" in view.get("eligible_actions", []),
                      clock=clock, sleep=sleep, timeout_seconds=timeout_seconds,
                      poll_seconds=poll_seconds)
            receipt = actuator.apply({service: "stop"}, dry_run=True,
                                     sample=_sample(sample_provider),
                                     decision_id=f"research-{run_id}-dry-run")
            if receipt.get("status") != "dry_run":
                raise LifecycleCycleError("Actuator did not confirm a mutation-free lifecycle plan")
            result.update(status="dry_run", plan=receipt)
        except Exception as exc:
            result.update(status="failed", error=_safe_error(exc))
        result["finished_at"] = wall_clock()
        if output_path is not None:
            atomic_json(output_path, result)
        return result

    owned_stop = False
    try:
        for number in range(1, cycles + 1):
            row = {"cycle": number}
            result["cycles"].append(row)
            _wait_for(actuator, service, container_id,
                      lambda view: "stop" in view.get("eligible_actions", []),
                      clock=clock, sleep=sleep, timeout_seconds=timeout_seconds,
                      poll_seconds=poll_seconds)
            stopped = actuator.apply({service: "stop"}, dry_run=False,
                                     sample=_sample(sample_provider),
                                     decision_id=f"research-{run_id}-{number}-stop")
            row["stop"] = stopped
            owned_stop = bool(stopped.get("owned_stop"))
            if stopped.get("status") != "applied" or not owned_stop:
                raise LifecycleCycleError("Actuator did not confirm a controller-owned stop")
            if service == "trainer" and not _trainer_drain_verified(stopped):
                raise LifecycleCycleError("Trainer stop lacks a durable validated drain proof")

            _wait_for(actuator, service, container_id,
                      lambda view: "start" in view.get("eligible_actions", []),
                      clock=clock, sleep=sleep, timeout_seconds=timeout_seconds,
                      poll_seconds=poll_seconds)
            started = actuator.apply({service: "start"}, dry_run=False,
                                     sample=_sample(sample_provider),
                                     decision_id=f"research-{run_id}-{number}-start")
            row["start"] = started
            owned_stop = bool(started.get("owned_stop", owned_stop))
            if started.get("status") != "applied" or owned_stop:
                raise LifecycleCycleError("Actuator did not confirm restoration of its stopped container")
            _wait_for(actuator, service, container_id, lambda view: view.get("ready") is True,
                      clock=clock, sleep=sleep, timeout_seconds=timeout_seconds,
                      poll_seconds=poll_seconds)
            result["completed_cycles"] = number
        result["status"] = "completed"
    except Exception as exc:
        result.update(status="failed", error=_safe_error(exc))
    finally:
        if owned_stop:
            try:
                _wait_for(actuator, service, container_id,
                          lambda view: "start" in view.get("eligible_actions", []),
                          clock=clock, sleep=sleep, timeout_seconds=timeout_seconds,
                          poll_seconds=poll_seconds)
                restored = actuator.apply({service: "start"}, dry_run=False,
                    sample=_sample(sample_provider), decision_id=f"research-{run_id}-restore")
                result["restore"] = restored
                if restored.get("status") != "applied" or restored.get("owned_stop"):
                    result["status"] = "failed_restore"
            except Exception as exc:
                result["restore"] = {"status": "failed", "error": _safe_error(exc)}
                result["status"] = "failed_restore"
        result["finished_at"] = wall_clock()
        if output_path is not None:
            atomic_json(output_path, result)
    return result

