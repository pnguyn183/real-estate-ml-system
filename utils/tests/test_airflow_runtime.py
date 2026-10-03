"""Offline helper/command tests; these do NOT claim an Airflow runtime test.

The actual DAG import is separately verified inside the Airflow Docker image.
"""

import ast
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("airflow_runtime_checks", ROOT / "airflow/runtime_checks.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class FakeClock:
    now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class OffsetReader:
    def __init__(self, targets=(3, 5, 8), commits=((3, 5, 8),)):
        self.targets = targets
        self.commits = iter(commits)
        self.last = commits[-1]
        self.watermark_calls = 0

    def list_topics(self, topic, timeout):
        return SimpleNamespace(topics={topic: SimpleNamespace(error=None, partitions=dict.fromkeys(range(len(self.targets))))})

    def get_watermark_offsets(self, partition, **kwargs):
        self.watermark_calls += 1
        return 0, self.targets[partition.partition]

    def committed(self, partitions, timeout):
        self.last = next(self.commits, self.last)
        return [SimpleNamespace(partition=p.partition, offset=self.last[p.partition], error=None) for p in partitions]


def test_processing_barrier_waits_for_committed_offsets_only():
    consumer = OffsetReader(commits=((0, 1, 1), (3, 5, 8)))
    clock = FakeClock()
    assert runtime.wait_for_processing(consumer, "real_estate_raw", clock=clock, sleep=clock.sleep) == {0: 3, 1: 5, 2: 8}
    assert clock.now == 5
    # It takes one watermark snapshot, not a moving target under ongoing load.
    assert consumer.watermark_calls == 3


def test_empty_topic_needs_no_preexisting_group_offsets():
    assert runtime.wait_for_processing(OffsetReader((0, 0, 0), ((-1001, -1001, -1001),)), "raw") == {0: 0, 1: 0, 2: 0}


def test_uncommitted_existing_records_do_not_look_drained():
    clock = FakeClock()
    with pytest.raises(TimeoutError, match="snapshot"):
        runtime.wait_for_processing(OffsetReader((1,), ((-1001,),)), "raw", timeout_seconds=7, clock=clock, sleep=clock.sleep)
    assert clock.now == 7


@pytest.mark.parametrize("timeout,poll", [(0, 5), (-1, 5), (5, 0)])
def test_barrier_rejects_invalid_limits(timeout, poll):
    with pytest.raises(ValueError):
        runtime.wait_for_processing(OffsetReader(), "raw", timeout_seconds=timeout, poll_seconds=poll)


def test_barrier_rejects_missing_topic():
    consumer = OffsetReader()
    consumer.list_topics = lambda **kwargs: SimpleNamespace(topics={})
    with pytest.raises(RuntimeError, match="metadata"):
        runtime.wait_for_processing(consumer, "missing")


def test_barrier_does_not_treat_incomplete_offsets_as_success():
    consumer = OffsetReader()
    consumer.committed = lambda *args, **kwargs: []
    with pytest.raises(RuntimeError, match="every requested"):
        runtime.wait_for_processing(consumer, "raw")


def task_functions():
    """Load only stdlib command-wrapper functions, not a stubbed DAG runtime."""
    parsed = ast.parse((ROOT / "airflow/dags/real_estate_pipeline.py").read_text(encoding="utf-8"))
    functions = ast.Module(body=[node for node in parsed.body if isinstance(node, ast.FunctionDef)], type_ignores=[])
    namespace = {"os": os, "subprocess": subprocess}
    for name in ("AirflowException", "AirflowFailException", "AirflowSkipException"):
        namespace[name] = type(name, (Exception,), {})
    exec(compile(functions, "airflow-task-functions-unit-test", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("code,exception_name", [
    (20, "AirflowFailException"), (99, "AirflowSkipException"), (1, "AirflowException"),
])
def test_task_exit_codes_distinguish_access_denial_skip_and_retry(monkeypatch, code, exception_name):
    functions = task_functions()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=code))
    with pytest.raises(functions[exception_name]):
        functions["run_application"](["scraper/kafka_producer.py"])


def test_task_uses_explicit_application_interpreter_and_workdir(monkeypatch):
    functions = task_functions()
    calls = []
    monkeypatch.setenv("APPLICATION_PYTHON", "/app-venv/python")
    monkeypatch.setenv("APPLICATION_ROOT", "/project")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    functions["run_application"](["airflow/runtime_checks.py", "train"])
    assert calls == [((["/app-venv/python", "airflow/runtime_checks.py", "train"],), {"cwd": "/project", "check": False})]


def test_crawler_limits_are_arguments_not_shell_interpolation(monkeypatch):
    monkeypatch.setenv("CRAWL_ENABLED", "true")
    functions = task_functions()
    calls = []
    functions["run_application"] = calls.append
    monkeypatch.setenv("SCRAPE_LIMIT", "5")
    monkeypatch.setenv("SCRAPE_MAX_PAGES", "1")
    functions["run_crawler"]()
    command = calls[0]
    assert command[command.index("--limit") + 1] == "5"
    assert command[command.index("--max-pages") + 1] == "1"
    assert "--fresh-start" not in command
