"""Accounting tests are unit fixtures, never live overload evidence."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from research.llm_load import EventLedger, LoadDriver, ScheduledOffer, offered_schedule


def test_schedule_keeps_all_absolute_arrivals_across_stage_boundaries():
    schedule = offered_schedule([{"name": "light", "rate": 2, "seconds": 1},
                                 {"name": "overload", "rate": 3, "seconds": .5}])
    assert [event.sequence for event in schedule] == [1, 2, 3, 4]
    assert [event.offset_seconds for event in schedule] == pytest.approx([0, .5, 1, 1 + 1 / 3])
    assert [event.stage for event in schedule] == ["light", "light", "overload", "overload"]
    assert len(offered_schedule([{"rate": .1, "seconds": .1}])) == 1


@pytest.mark.parametrize("profile", [[], [{"rate": 0, "seconds": 1}],
                                   [{"rate": float("nan"), "seconds": 1}],
                                   [{"rate": 1, "seconds": float("inf")}],
                                   [{"rate": 10001, "seconds": 1}]])
def test_unbounded_or_invalid_schedule_is_rejected(profile):
    with pytest.raises(ValueError):
        offered_schedule(profile)


def test_schedule_cannot_silently_truncate_demand_at_record_budget():
    with pytest.raises(ValueError, match="max_records"):
        offered_schedule([{"rate": 10, "seconds": 2}], max_records=19)


def offered(ledger, sequence=1, at=100):
    ledger.offer(ScheduledOffer(sequence, "overload", sequence - 1), at, at + .2)


def test_commit_latency_includes_gate_wait_and_poll_delay_ack_is_not_completion():
    ledger = EventLedger(1)
    offered(ledger)
    ledger.defer(1)
    ledger.admit(1, 105)
    ledger.acknowledge(1, partition=0, offset=20, timestamp=106)
    assert ledger.snapshot()["acknowledged"] == 1
    assert ledger.snapshot()["committed"] == 0
    ledger.observe_committed({0: 20}, timestamp=106.5)
    assert ledger.snapshot()["committed"] == 0  # committed is the next offset.
    ledger.observe_committed({0: 21}, timestamp=107)
    snapshot = ledger.snapshot()
    assert snapshot["offered_to_commit_p95_upper_seconds"] == 7
    assert snapshot["admission_wait_p95_seconds"] == 5
    assert snapshot["deferred"] == 1
    assert snapshot["pending"] == 0
    assert snapshot["completion_coverage"] == 1


def test_commit_before_delivery_callback_is_counted_without_second_poll():
    ledger = EventLedger(1)
    offered(ledger)
    ledger.admit(1, 101)
    ledger.observe_committed({2: 8}, timestamp=102)
    ledger.observe_committed({2: 8}, timestamp=104)
    ledger.observe_committed({2: 7}, timestamp=105)  # stale poll cannot regress it.
    ledger.acknowledge(1, partition=2, offset=7, timestamp=106)
    assert ledger.events()[0]["committed_observed_at"] == 102
    assert ledger.snapshot()["offered_to_commit_p95_upper_seconds"] == 2


def test_pending_failures_and_rejections_form_complete_nonoverlapping_outcomes():
    ledger = EventLedger(7)
    for sequence in range(1, 8):
        offered(ledger, sequence)
    ledger.reject(1, "queue_full", 101)
    for sequence in range(2, 7):
        ledger.admit(sequence, 102)
    ledger.enqueue_failed(2, 102, "BufferError")
    ledger.delivery_failed(3, 103, "KafkaError")
    ledger.acknowledge(4, 0, 1, 103)
    ledger.acknowledge(5, 0, 2, 103)
    ledger.observe_committed({0: 2}, 104)
    snapshot = ledger.snapshot()
    assert snapshot["offered"] == snapshot["admitted"] + snapshot["rejected"] + snapshot["queued"]
    assert snapshot["admitted"] == (snapshot["enqueue_failed"] + snapshot["delivery_failed"]
                                     + snapshot["acknowledged"] + snapshot["awaiting_delivery"])
    assert snapshot["acknowledged"] == snapshot["committed"] + snapshot["awaiting_commit"]
    assert snapshot["pending"] == 3
    assert snapshot["latency_count"] == 1
    assert snapshot["latency_missing"] == 6
    assert snapshot["rejected_by_reason"] == {"queue_full": 1}
    ledger.acknowledge(2, 0, 100, 110)  # failed enqueue cannot become a phantom ACK.
    ledger.reject(4, "stopped", 110)  # admitted events cannot become rejected demand.
    assert ledger.snapshot() == snapshot


class Producer:
    def __init__(self, *, produce_delay=0, fail=False):
        self.messages, self.callbacks, self.times = [], [], []
        self.produce_delay, self.fail = produce_delay, fail

    def produce(self, topic, key, value, callback):
        if self.produce_delay:
            time.sleep(self.produce_delay)
        if self.fail:
            raise BufferError("fixture queue full")
        self.messages.append(json.loads(value))
        self.times.append(time.perf_counter())
        offset = len(self.messages) - 1
        self.callbacks.append((callback, SimpleNamespace(partition=lambda: 0, offset=lambda: offset)))

    def poll(self, timeout):
        pending, self.callbacks = self.callbacks, []
        for callback, message in pending:
            callback(None, message)

    def flush(self, timeout):
        self.poll(0)
        return 0


def driver(tmp_path: Path, rate_supplier, *, producer=None, **kwargs):
    return LoadDriver([{"name": "overload", "rate": 200, "seconds": .05}], 42,
                      "fixture", producer or Producer(), rate_supplier, tmp_path, **kwargs)


def test_zero_gate_never_hides_offered_demand_or_hangs(tmp_path):
    load = driver(tmp_path, lambda: 0, max_wait_seconds=.04, max_queue=2).start()
    assert load.join(2)
    snapshot = load.write_artifacts()
    assert snapshot["offered"] == snapshot["planned"] == 10
    assert snapshot["admitted"] == snapshot["acknowledged"] == snapshot["committed"] == 0
    assert snapshot["rejected"] == 10
    assert snapshot["rejected_by_reason"]["queue_full"] > 0
    assert snapshot["rejected_by_reason"]["wait_deadline"] > 0
    assert snapshot["pending"] == 0
    assert snapshot["offered_to_commit_p95_upper_seconds"] is None
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 10


def test_sender_blocking_does_not_change_arrival_count_or_payload_sequence(tmp_path):
    # Producer itself is slow; scheduler must still offer/reject the whole load.
    slow = driver(tmp_path / "slow", lambda: 10000, producer=Producer(produce_delay=.03),
                  max_queue=2, max_wait_seconds=.2).start()
    fast = driver(tmp_path / "fast", lambda: 10000, max_wait_seconds=.2).start()
    assert slow.join(3) and fast.join(3)
    slow_snapshot, fast_snapshot = slow.snapshot(), fast.snapshot()
    assert slow_snapshot["offered"] == fast_snapshot["offered"] == 10
    assert slow_snapshot["rejected"] > 0
    assert slow_snapshot["workload_fingerprint"] == fast_snapshot["workload_fingerprint"]
    assert [event["sequence"] for event in slow.ledger.events()] == list(range(1, 11))


def test_baseline_and_llm_get_same_logical_payload_but_isolated_urls(tmp_path):
    loads = []
    for run_id in ("baseline", "llm"):
        load = LoadDriver([{"rate": 100, "seconds": .04}], 73, run_id, Producer(),
                          lambda: 10000, tmp_path / run_id, max_wait_seconds=1).start()
        assert load.join(2)
        loads.append(load)
    assert loads[0].snapshot()["workload_fingerprint"] == loads[1].snapshot()["workload_fingerprint"]
    assert loads[0].producer.messages[0]["url"] != loads[1].producer.messages[0]["url"]
    for left, right in zip(loads[0].producer.messages, loads[1].producer.messages):
        for key in set(left) - {"url", "run_id", "stress_sent_at"}:
            assert left[key] == right[key]


def test_missed_deadlines_count_every_arrival_and_measure_scheduler_lateness(tmp_path):
    # Starting in the past emulates a stalled scheduler without a slow live test.
    load = driver(tmp_path, lambda: 10000, max_wait_seconds=.05).start(time.time() - 1)
    assert load.join(2)
    snapshot = load.snapshot()
    assert snapshot["offered"] == 10
    assert snapshot["rejected"] == 10
    assert snapshot["admitted"] == 0
    assert snapshot["scheduler_lateness_p95_seconds"] > .9


def test_stop_reports_cancelled_future_arrivals_and_disposes_existing_queue(tmp_path):
    load = LoadDriver([{"rate": 10, "seconds": 10}], 42, "fixture", Producer(),
                      lambda: 0, tmp_path).start()
    deadline = time.monotonic() + 2
    while load.snapshot()["offered"] == 0 and time.monotonic() < deadline:
        time.sleep(.005)
    load.stop("fixture_stop")
    assert load.join(2)
    snapshot = load.snapshot()
    assert snapshot["offered"] + snapshot["cancelled_future_offers"] == 100
    assert snapshot["rejected"] == snapshot["offered"]
    assert snapshot["pending"] == 0
    assert snapshot["stop_reason"] == "fixture_stop"


def test_pacing_after_slow_produce_does_not_accumulate_burst_tokens(tmp_path):
    producer = Producer(produce_delay=.02)
    load = driver(tmp_path, lambda: 100, producer=producer, max_wait_seconds=1).start()
    assert load.join(3)
    assert len(producer.times) == 10
    # Each next actual enqueue follows a new 10ms interval plus producer delay.
    assert min(right - left for left, right in zip(producer.times, producer.times[1:])) >= .028


def test_invalid_policy_rate_fails_closed_with_visible_diagnostic(tmp_path):
    load = driver(tmp_path, lambda: float("nan"), max_wait_seconds=.04).start()
    assert load.join(2)
    assert load.snapshot()["admitted"] == 0
    assert load.snapshot()["rate_read_errors"] > 0


def test_policy_read_stall_cannot_admit_after_waiting_deadline(tmp_path):
    def slow_policy():
        time.sleep(.06)
        return 10000
    load = driver(tmp_path, slow_policy, max_wait_seconds=.04).start()
    assert load.join(2)
    assert load.snapshot()["admitted"] == 0
    assert load.snapshot()["rejected"] == load.snapshot()["offered"] == 10
    assert load.snapshot()["rejected_by_reason"] == {"wait_deadline": 10}


def test_watchdog_health_does_not_scan_or_sort_event_accounting(tmp_path, monkeypatch):
    load = driver(tmp_path, lambda: float("nan"))
    assert load._rate() == 0
    def accounting_not_permitted():
        raise AssertionError("watchdog must not scan the ledger")
    monkeypatch.setattr(load.ledger, "snapshot", accounting_not_permitted)
    assert load.health() == {"errors": [], "rate_read_errors": 1}


def test_pacing_does_not_depend_on_windows_low_resolution_monotonic_clock(tmp_path, monkeypatch):
    # Windows Python <=3.12 may repeat this clock value for a full 15.6ms.
    # Absolute offers and sender spacing must use QPC/perf_counter instead.
    monkeypatch.setattr(time, "monotonic", lambda: 100)
    load = driver(tmp_path, lambda: 1000, max_wait_seconds=.3).start()
    assert load.join(2)
    assert load.snapshot()["admitted"] == load.snapshot()["planned"] == 10


def test_enqueue_failures_count_admitted_and_never_acknowledged(tmp_path):
    load = driver(tmp_path, lambda: 10000, producer=Producer(fail=True), max_wait_seconds=1).start()
    assert load.join(2)
    assert load.snapshot()["admitted"] == load.snapshot()["enqueue_failed"] == 10
    assert load.snapshot()["acknowledged"] == 0
    assert load.snapshot()["pending"] == 0


def test_ledger_concurrent_poll_and_delivery_callbacks_do_not_lose_commits():
    ledger = EventLedger(100)
    for sequence in range(1, 101):
        offered(ledger, sequence)
        ledger.admit(sequence, 101)
    threads = [threading.Thread(target=lambda: [ledger.acknowledge(i, 0, i, 102)
                                                for i in range(1, 101)]),
               threading.Thread(target=lambda: [ledger.observe_committed({0: i + 1}, 103)
                                                for i in range(1, 101)])]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert ledger.snapshot()["committed"] == 100
    assert ledger.snapshot()["pending"] == 0
