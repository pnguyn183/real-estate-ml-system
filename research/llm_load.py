"""Independent offered demand and honest accounting for the LLM experiment.

Kafka acknowledgement is NOT completion. ``committed`` means the processor's
consumer-group offset passed an event, as observed by a separate read-only poll.
Its latency includes time before the admission gate and the polling delay, so it
is an upper bound on time to commit, not an exact MongoDB persistence timestamp.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from typing import Callable, Iterable, Mapping

from agents.generator import ListingGenerator


@dataclass(frozen=True)
class ScheduledOffer:
    sequence: int
    stage: str
    offset_seconds: float


def offered_schedule(profile: Iterable[Mapping], *, max_records: int = 1_000_000) -> tuple[ScheduledOffer, ...]:
    """Include every arrival in each half-open stage, independently of any gate."""
    result: list[ScheduledOffer] = []
    start = 0.0
    for index, stage in enumerate(profile):
        rate, seconds = float(stage["rate"]), float(stage["seconds"])
        if not math.isfinite(rate) or not 0 < rate <= 10000:
            raise ValueError("profile rate must be finite, positive and at most 10000")
        if not math.isfinite(seconds) or not 0 < seconds <= 3600:
            raise ValueError("profile seconds must be finite, positive and at most 3600")
        # Subtract a scale-aware rounding tolerance at exact integer boundaries.
        count = max(1, math.ceil(rate * seconds - 1e-12 * max(1.0, rate * seconds)))
        if len(result) + count > max_records:
            raise ValueError("offered schedule exceeds max_records")
        name = str(stage.get("name", f"stage-{index + 1}"))
        first_sequence = len(result) + 1
        result.extend(ScheduledOffer(first_sequence + offset, name, start + offset / rate)
                      for offset in range(count))
        start += seconds
    if not result:
        raise ValueError("profile must contain at least one arrival")
    return tuple(result)


def percentile(values: Iterable[float], fraction: float = .95) -> float | None:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def _interruptible_sleep(stop: threading.Event, seconds: float) -> bool:
    """Use Python's high-resolution sleep for short Windows pacing intervals."""
    deadline = time.perf_counter() + max(0.0, seconds)
    while not stop.is_set():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return False
        if remaining <= .02:
            time.sleep(remaining)
        else:
            stop.wait(min(.02, remaining - .01))
    return True


class EventLedger:
    """Thread-safe event transitions, including commit-before-ACK poll races."""

    def __init__(self, planned: int = 0):
        self.planned = planned
        self._events: dict[int, dict] = {}
        self._commits: dict[int, tuple[int, float]] = {}
        self._lock = threading.RLock()
        self._fingerprint = hashlib.sha256()

    def offer(self, offer: ScheduledOffer, scheduled_at: float, offered_at: float,
              payload_fingerprint: str = "") -> None:
        with self._lock:
            if offer.sequence in self._events:
                raise ValueError("arrival already recorded")
            self._events[offer.sequence] = {
                "sequence": offer.sequence, "stage": offer.stage,
                "scheduled_at": scheduled_at, "offered_at": offered_at,
                "scheduler_lateness_seconds": max(0.0, offered_at - scheduled_at),
                "payload_fingerprint": payload_fingerprint,
                "deferred": False, "status": "queued",
            }
            self._fingerprint.update(json.dumps(
                [offer.sequence, offer.stage, offer.offset_seconds, payload_fingerprint],
                separators=(",", ":"), ensure_ascii=True).encode() + b"\n")

    def defer(self, sequence: int) -> None:
        with self._lock:
            if self._events[sequence]["status"] == "queued":
                self._events[sequence]["deferred"] = True

    def reject(self, sequence: int, reason: str, at: float) -> None:
        with self._lock:
            event = self._events[sequence]
            if event["status"] != "queued":
                return
            event.update(status="rejected", rejected_reason=reason, rejected_at=at)

    def admit(self, sequence: int, at: float) -> None:
        with self._lock:
            event = self._events[sequence]
            if event["status"] != "queued":
                raise ValueError("only queued events may be admitted")
            event.update(status="inflight", admitted_at=at,
                         admission_wait_seconds=max(0.0, at - event["scheduled_at"]))

    def enqueue_failed(self, sequence: int, at: float, reason: str) -> None:
        with self._lock:
            event = self._events[sequence]
            if event["status"] == "inflight":
                event.update(status="enqueue_failed", enqueue_failed_at=at, failure_reason=reason)

    def delivery_failed(self, sequence: int, at: float, reason: str) -> None:
        with self._lock:
            event = self._events[sequence]
            if event["status"] == "inflight":
                event.update(status="delivery_failed", delivery_failed_at=at, failure_reason=reason)

    def acknowledge(self, sequence: int, partition: int, offset: int, timestamp: float) -> None:
        with self._lock:
            event = self._events[sequence]
            if event["status"] != "inflight":
                return
            event.update(status="acknowledged", acknowledged_at=timestamp,
                         partition=partition, offset=offset)
            self._maybe_committed(event)

    def _maybe_committed(self, event: dict) -> None:
        observation = self._commits.get(event.get("partition"))
        if event["status"] == "acknowledged" and observation and observation[0] > event["offset"]:
            observed_at = max(observation[1], event["admitted_at"])
            event.update(status="committed", committed_observed_at=observed_at,
                         offered_to_commit_upper_seconds=max(0.0, observed_at - event["scheduled_at"]))

    def observe_committed(self, offsets: Mapping[int, int], timestamp: float) -> None:
        with self._lock:
            for partition, offset in offsets.items():
                if offset < 0:
                    continue
                previous = self._commits.get(int(partition))
                # Keep the earliest observation of a given offset. Repeated polls
                # must not inflate a delayed callback's completion bound.
                if previous is None or offset > previous[0]:
                    self._commits[int(partition)] = (int(offset), timestamp)
            for event in self._events.values():
                self._maybe_committed(event)

    def events(self) -> list[dict]:
        with self._lock:
            return [dict(event) for event in self._events.values()]

    def snapshot(self) -> dict:
        with self._lock:
            events = list(self._events.values())
            counts = Counter(event["status"] for event in events)
            acknowledged = counts["acknowledged"] + counts["committed"]
            admitted = sum("admitted_at" in event for event in events)
            latencies = [event["offered_to_commit_upper_seconds"] for event in events
                         if event["status"] == "committed"]
            waits = [event["admission_wait_seconds"] for event in events if "admitted_at" in event]
            late = [event["scheduler_lateness_seconds"] for event in events]
            p95 = percentile(latencies)
            return {
                "planned": self.planned, "offered": len(events), "admitted": admitted,
                "deferred": sum(event["deferred"] for event in events),
                "rejected": counts["rejected"],
                "rejected_by_reason": dict(Counter(event["rejected_reason"] for event in events
                                                  if event["status"] == "rejected")),
                "enqueue_failed": counts["enqueue_failed"], "delivery_failed": counts["delivery_failed"],
                "delivery_acknowledged": acknowledged, "acknowledged": acknowledged,
                "committed": counts["committed"], "queued": counts["queued"],
                "awaiting_delivery": counts["inflight"], "awaiting_commit": counts["acknowledged"],
                "inflight": counts["inflight"] + counts["acknowledged"],
                "pending": counts["queued"] + counts["inflight"] + counts["acknowledged"],
                "latency_count": len(latencies), "latency_missing": len(events) - len(latencies),
                "completion_coverage": counts["committed"] / len(events) if events else None,
                "offered_to_commit_p95_upper_seconds": p95,
                "offered_to_commit_observed_seconds": {"p50": percentile(latencies, .5), "p95": p95,
                                                        "max": max(latencies, default=None)},
                "admission_wait_p95_seconds": percentile(waits),
                "scheduler_lateness_p95_seconds": percentile(late),
                "scheduler_lateness_max_seconds": max(late, default=None),
                "workload_fingerprint": self._fingerprint.hexdigest(),
                "completion_semantics": "Consumer-group commit observed by polling; latency from scheduled offer is an upper bound, not exact persistence latency.",
                "deferred_semantics": "Events queued behind another offer or observed waiting at a closed/pacing gate; overlaps admitted/rejected/queued outcomes.",
            }


class LoadDriver:
    """Two threads: absolute-time offers and independently paced Kafka admission.

    Queue overflow and waiting deadlines reject individual offers explicitly. A
    slow scheduler catches up every scheduled arrival; it never silently skips
    them. Stopping cancels future arrivals and reports their number separately.
    """

    def __init__(self, profile: Iterable[Mapping], seed: int, run_id: str, producer,
                 rate_supplier: Callable, output: Path, *, topic: str = "real_estate_stress_raw",
                 max_queue: int = 2000, max_wait_seconds: float = 30,
                 generated_at: str = "2026-01-01T00:00:00+00:00", max_records: int = 1_000_000):
        self.profile = [dict(stage) for stage in profile]
        self.schedule = offered_schedule(self.profile, max_records=max_records)
        if type(max_queue) is not int or not 1 <= max_queue <= 100000:
            raise ValueError("max_queue must be an integer between 1 and 100000")
        if not math.isfinite(max_wait_seconds) or not 0 < max_wait_seconds <= 600:
            raise ValueError("max_wait_seconds must be finite and between 0 and 600")
        self.producer, self.rate_supplier, self.output, self.topic = producer, rate_supplier, Path(output), topic
        self.max_queue, self.max_wait_seconds = max_queue, max_wait_seconds
        self.generator = ListingGenerator(run_id, "normal", seed, duplicate_ratio=0,
                                          unstructured_ratio=0, generated_at=generated_at)
        self.ledger = EventLedger(len(self.schedule))
        self.schedule_done, self.sending_done, self._stop = threading.Event(), threading.Event(), threading.Event()
        self._condition = threading.Condition()
        self._queue: deque[tuple[ScheduledOffer, dict]] = deque()
        self._threads: list[threading.Thread] = []
        self._stop_reason = "stopped"
        self._errors: list[str] = []
        self._rate_read_errors = 0
        self.start_at: float | None = None
        self.end_at: float | None = None
        self._start_mono = 0.0

    def start(self, start_at: float | None = None) -> "LoadDriver":
        if self._threads:
            raise RuntimeError("LoadDriver can only start once")
        now = time.time()
        self.start_at = now if start_at is None else float(start_at)
        if not math.isfinite(self.start_at):
            raise ValueError("start_at must be a finite wall-clock timestamp")
        # On Python <=3.12 Windows, monotonic() can use 15.6ms GetTickCount64;
        # perf_counter() is monotonic too and uses high-resolution QPC.
        self._start_mono = time.perf_counter() + (self.start_at - now)
        self._threads = [threading.Thread(target=self._offer, name="llm-offered-demand", daemon=True),
                         threading.Thread(target=self._send, name="llm-admission", daemon=True)]
        for thread in self._threads:
            thread.start()
        return self

    def _offer(self) -> None:
        try:
            for offer in self.schedule:
                delay = self._start_mono + offer.offset_seconds - time.perf_counter()
                if _interruptible_sleep(self._stop, delay):
                    break
                item = self.generator.next(offer.sequence)
                payload = dict(item.payload)
                canonical = {key: value for key, value in payload.items()
                             if key not in {"run_id", "url", "generated_at"}}
                fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True,
                                                          ensure_ascii=True).encode()).hexdigest()
                self.ledger.offer(offer, self.start_at + offer.offset_seconds, time.time(), fingerprint)
                with self._condition:
                    if self._stop.is_set():
                        self.ledger.reject(offer.sequence, self._stop_reason, time.time())
                    elif len(self._queue) >= self.max_queue:
                        self.ledger.reject(offer.sequence, "queue_full", time.time())
                    else:
                        if self._queue:
                            self.ledger.defer(offer.sequence)
                        self._queue.append((offer, payload))
                        self._condition.notify()
        except Exception as exc:
            self._errors.append("scheduler:" + type(exc).__name__)
            self.stop("scheduler_failed")
        finally:
            # Hold the profile's full duration even when its final scheduled
            # arrival precedes the stage boundary by a full interarrival period.
            if not self._stop.is_set():
                duration = sum(float(stage["seconds"]) for stage in self.profile)
                self._stop.wait(max(0.0, self._start_mono + duration - time.perf_counter()))
            self.schedule_done.set()
            with self._condition:
                self._condition.notify_all()

    def _rate(self) -> float:
        try:
            value = self.rate_supplier()
            rate = float(getattr(value, "rate_per_second", value))
            if not math.isfinite(rate) or not 0 <= rate <= 10000:
                raise ValueError("invalid admission rate")
            return rate
        except Exception:
            self._rate_read_errors += 1
            return 0.0

    def _send(self) -> None:
        last_grant: float | None = None
        try:
            while True:
                self.producer.poll(0)
                with self._condition:
                    if self._stop.is_set():
                        while self._queue:
                            offer, _ = self._queue.popleft()
                            self.ledger.reject(offer.sequence, self._stop_reason, time.time())
                        if self.schedule_done.is_set():
                            break
                        self._condition.wait(.01)
                        continue
                    if not self._queue:
                        if self.schedule_done.is_set():
                            break
                        self._condition.wait(.01)
                        continue
                    offer, payload = self._queue[0]
                now = time.perf_counter()
                deadline = self._start_mono + offer.offset_seconds + self.max_wait_seconds
                if now >= deadline:
                    self.ledger.reject(offer.sequence, "wait_deadline", time.time())
                    with self._condition:
                        self._queue.popleft()
                    continue
                rate = self._rate()
                # Reading a policy is normally local and fast, but a stalled
                # supplier must not admit an offer after its waiting deadline.
                now = time.perf_counter()
                if now >= deadline:
                    self.ledger.reject(offer.sequence, "wait_deadline", time.time())
                    with self._condition:
                        self._queue.popleft()
                    continue
                due = now if last_grant is None else last_grant + (1 / rate if rate > 0 else math.inf)
                if rate <= 0 or now < due:
                    self.ledger.defer(offer.sequence)
                    _interruptible_sleep(self._stop, min(.05, deadline - now,
                                                        max(.0001, due - now) if rate > 0 else .05))
                    continue
                with self._condition:
                    # Recheck after a concurrently requested stop.
                    if self._stop.is_set():
                        continue
                    self._queue.popleft()
                admitted_at = time.time()
                self.ledger.admit(offer.sequence, admitted_at)
                payload["stress_sent_at"] = admitted_at
                try:
                    self.producer.produce(self.topic, key=payload["listing_id"],
                                          value=json.dumps(payload, ensure_ascii=False).encode(),
                                          callback=self._callback(offer.sequence))
                except Exception as exc:
                    self.ledger.enqueue_failed(offer.sequence, time.time(), type(exc).__name__)
                # Anchor spacing after the actual enqueue attempt; a stall must
                # never accumulate tokens that then burst into Kafka.
                last_grant = time.perf_counter()
            self.producer.flush(12)
        except Exception as exc:
            self._errors.append("sender:" + type(exc).__name__)
            self.stop("sender_failed")
            with self._condition:
                while self._queue:
                    offer, _ = self._queue.popleft()
                    self.ledger.reject(offer.sequence, "sender_failed", time.time())
        finally:
            self.end_at = time.time()
            self.sending_done.set()

    def _callback(self, sequence: int):
        def delivered(error, message):
            if error is not None:
                self.ledger.delivery_failed(sequence, time.time(), type(error).__name__)
            else:
                self.ledger.acknowledge(sequence, int(message.partition()), int(message.offset()), time.time())
        return delivered

    def observe_committed(self, offsets: Mapping[int, int], timestamp: float | None = None) -> None:
        self.ledger.observe_committed(offsets, time.time() if timestamp is None else timestamp)

    def stop(self, reason: str = "stopped") -> None:
        self._stop_reason = reason
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def join(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.perf_counter() + timeout
        for thread in self._threads:
            thread.join(None if deadline is None else max(0.0, deadline - time.perf_counter()))
        return all(not thread.is_alive() for thread in self._threads)

    def snapshot(self) -> dict:
        result = self.ledger.snapshot()
        result.update(start_at=self.start_at, end_at=self.end_at,
                      schedule_done=self.schedule_done.is_set(), sending_done=self.sending_done.is_set(),
                      cancelled_future_offers=(result["planned"] - result["offered"]
                                               if self.schedule_done.is_set() and self._stop.is_set() else 0),
                      errors=list(self._errors), rate_read_errors=self._rate_read_errors,
                      stopped=self._stop.is_set(), stop_reason=self._stop_reason if self._stop.is_set() else None)
        return result

    def health(self) -> dict:
        """Constant-size diagnostics for frequent watchdog checks.

        Full accounting sorts event latency samples and is intended for the
        telemetry cadence, not the watchdog's much faster admission checks.
        """
        return {"errors": list(self._errors), "rate_read_errors": self._rate_read_errors}

    def write_artifacts(self) -> dict:
        """Write a consistent per-event snapshot; call again after the final poll."""
        self.output.mkdir(parents=True, exist_ok=True)
        with self.ledger._lock:
            events = self.ledger.events()
            summary = self.snapshot()
        with (self.output / "events.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")
        (self.output / "load-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary
