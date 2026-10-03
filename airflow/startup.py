"""Remove a stale local webserver PID before starting Airflow standalone.

AIRFLOW_HOME is persistent, but container process IDs are not. Never remove
the PID file of a running webserver or modify Airflow metadata/credentials.
"""
from pathlib import Path
import os


def clean_webserver_pid(home: Path, proc_root: Path = Path('/proc')) -> str:
    path = home / 'airflow-webserver.pid'
    if not path.exists():
        return 'no_webserver_pid'
    value = path.read_text().strip()
    if not value.isdigit() or int(value) <= 0:
        raise RuntimeError('invalid_webserver_pid_requires_review')
    process = proc_root / value
    if process.exists():
        # A recycled PID can now belong to standalone or an unrelated process.
        # Refuse to remove a PID still identifying an actual webserver.
        command = (process / 'cmdline').read_bytes().replace(b'\0', b' ').lower()
        if b'webserver' in command or b'gunicorn' in command:
            raise RuntimeError('webserver_already_running')
    path.unlink()
    return 'removed_stale_webserver_pid'


if __name__ == '__main__':
    print(clean_webserver_pid(Path(os.environ.get('AIRFLOW_HOME', '/opt/airflow'))))
