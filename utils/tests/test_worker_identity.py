"""Deployed worker provenance; subprocesses are mocked, never live evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from research import run_experiment as runner


SOURCES = ("processing/kafka_to_mongo.py", "processing/price_anomaly.py",
           "processing/feature_engineering.py")
SERVICES = ("processor", "processor-2", "processor-3")


@pytest.fixture
def deployed(monkeypatch):
    outputs = {service: {source: str(index + 1) * 64 for index, source in enumerate(SOURCES)}
               for service in SERVICES}
    calls = []

    def check_output(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["git", "rev-parse", "HEAD"]:
            return "fixture-head\n"
        if command[:2] == ["docker", "ps"]:
            return "worker fixture-image fixture-id\n"
        assert command[:4] == ["docker", "compose", "exec", "-T"]
        value = outputs[command[4]]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)

    monkeypatch.setattr(runner.subprocess, "check_output", check_output)
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda name: "fixture-version")
    return outputs, calls


def test_records_local_and_deployed_dependencies_without_changing_legacy_hashes(deployed):
    outputs, calls = deployed
    outputs["processor-2"][SOURCES[1]] = "e" * 64
    result = runner.code_identity()
    for source in SOURCES:
        assert result[str(Path(source))] == hashlib.sha256(Path(source).read_bytes()).hexdigest()
    for service in SERVICES:
        assert result["worker_source_hashes"][service] == outputs[service][SOURCES[0]]
        assert result["worker_dependency_source_hashes"][service] == {
            source: outputs[service][source] for source in SOURCES[1:]
        }
    remote_calls = [(command, kwargs) for command, kwargs in calls if command[:2] == ["docker", "compose"]]
    assert len(remote_calls) == 3
    assert {command[4] for command, _ in remote_calls} == set(SERVICES)
    for command, kwargs in remote_calls:
        assert command[5:7] == ["python", "-c"]
        assert all(source in command[7] for source in SOURCES)
        assert kwargs == {"text": True, "timeout": 10}


def test_missing_deployed_dependency_is_explicit_and_keeps_other_hashes(deployed):
    outputs, _ = deployed
    outputs["processor"][SOURCES[1]] = {"error": "FileNotFoundError"}
    result = runner.code_identity()
    assert result["worker_source_hashes"]["processor"] == "1" * 64
    assert result["worker_dependency_source_hashes"]["processor"] == {
        SOURCES[1]: {"error": "FileNotFoundError"}, SOURCES[2]: "3" * 64,
    }


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("invalid", ["A" * 64, "a" * 63, "g" * 64, None])
def test_rejects_malformed_hash_for_every_deployed_source(deployed, source, invalid):
    outputs, _ = deployed
    outputs["processor"][source] = invalid
    result = runner.code_identity()
    assert result["worker_source_hashes"]["processor"] == {"error": "ValueError"}
    assert result["worker_dependency_source_hashes"]["processor"] == {
        dependency: {"error": "ValueError"} for dependency in SOURCES[1:]
    }
    assert result["worker_source_hashes"]["processor-2"] == "1" * 64


@pytest.mark.parametrize("invalid", ["not json", "[]", "{}", '{"secret": "not recorded"}'])
def test_invalid_json_shape_records_error_without_raw_output(deployed, invalid):
    outputs, _ = deployed
    outputs["processor"] = invalid
    result = runner.code_identity()
    error = "JSONDecodeError" if invalid == "not json" else "ValueError"
    assert result["worker_source_hashes"]["processor"] == {"error": error}
    assert "not recorded" not in json.dumps(result)


def test_invalid_remote_error_diagnostic_is_not_copied(deployed):
    outputs, _ = deployed
    outputs["processor"][SOURCES[1]] = {"error": "FileNotFoundError: secret detail"}
    result = runner.code_identity()
    assert result["worker_dependency_source_hashes"]["processor"][SOURCES[1]] == {"error": "ValueError"}
    assert "secret detail" not in json.dumps(result)


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("docker", 10),
                                     subprocess.CalledProcessError(1, "docker"),
                                     FileNotFoundError("secret path")])
def test_unavailable_worker_records_errors_without_substituting_local_hashes(deployed, failure):
    outputs, _ = deployed
    other_worker = deepcopy(outputs["processor-2"])
    outputs["processor"] = failure
    result = runner.code_identity()
    error = {"error": type(failure).__name__}
    assert result["worker_source_hashes"]["processor"] == error
    assert result["worker_dependency_source_hashes"]["processor"] == {
        source: error for source in SOURCES[1:]
    }
    assert result["worker_source_hashes"]["processor-2"] == other_worker[SOURCES[0]]
    assert "secret path" not in json.dumps(result)
