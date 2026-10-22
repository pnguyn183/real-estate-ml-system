"""Run API and web together, cleaning up both on Ctrl+C."""
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def load_local_auth_secret(root):
    if os.environ.get('AUTH_JWT_SECRET'):
        return
    env_file = root / '.env'
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        name, value = stripped.split('=', 1)
        if name.strip() != 'AUTH_JWT_SECRET':
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if len(value.encode('utf-8')) < 32:
            raise RuntimeError(
                'AUTH_JWT_SECRET trong .env phải có ít nhất 32 byte. '
                'Hãy tạo khóa bằng secrets.token_urlsafe(48).'
            )
        os.environ['AUTH_JWT_SECRET'] = value
        return


def main():
    root = Path(__file__).resolve().parent
    load_local_auth_secret(root)
    children = []
    try:
        if not os.environ.get('VALUATION_API_URL'):
            backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'api:app',
                                        '--host', '127.0.0.1', '--port', '8000'], cwd=root)
            children.append(backend)
            for _ in range(60):
                if backend.poll() is not None:
                    raise RuntimeError('API failed to start; check port 8000 and dependencies.')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=1):
                        break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError('API startup timed out.')
        frontend = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', 'web.py',
                                     '--server.address', '127.0.0.1', *sys.argv[1:]], cwd=root)
        children.append(frontend)
        while frontend.poll() is None:
            if len(children) == 2 and children[0].poll() is not None:
                raise RuntimeError('API stopped unexpectedly.')
            time.sleep(0.5)
        return frontend.returncode
    except KeyboardInterrupt:
        return 0
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    sys.exit(main())
