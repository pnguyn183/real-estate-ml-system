import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('airflow_startup', Path(__file__).resolve().parents[2] / 'airflow/startup.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


@pytest.mark.parametrize('command', [None, b'airflow\0standalone\0'])
def test_stale_or_recycled_pid_is_removed_without_touching_metadata(tmp_path, command):
    pid = tmp_path / 'airflow-webserver.pid'
    pid.write_text('56')
    metadata = tmp_path / 'airflow.db'
    metadata.write_bytes(b'metadata-preserved')
    proc = tmp_path / 'proc'
    if command is not None:
        (proc / '56').mkdir(parents=True)
        (proc / '56' / 'cmdline').write_bytes(command)
    assert startup.clean_webserver_pid(tmp_path, proc) == 'removed_stale_webserver_pid'
    assert not pid.exists()
    assert metadata.read_bytes() == b'metadata-preserved'


@pytest.mark.parametrize('command', [b'airflow\0webserver\0', b'gunicorn: master'])
def test_live_webserver_is_never_removed(tmp_path, command):
    pid = tmp_path / 'airflow-webserver.pid'
    pid.write_text('56')
    proc = tmp_path / 'proc'
    (proc / '56').mkdir(parents=True)
    (proc / '56' / 'cmdline').write_bytes(command)
    with pytest.raises(RuntimeError, match='already_running'):
        startup.clean_webserver_pid(tmp_path, proc)
    assert pid.exists()


def test_absent_and_malformed_pid(tmp_path):
    assert startup.clean_webserver_pid(tmp_path) == 'no_webserver_pid'
    pid = tmp_path / 'airflow-webserver.pid'
    pid.write_text('not-a-pid')
    with pytest.raises(RuntimeError, match='requires_review'):
        startup.clean_webserver_pid(tmp_path)
    assert pid.exists()
