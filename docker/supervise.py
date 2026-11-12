"""Run the web app and its private renderer as one supervised service."""

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    app_env = os.environ.copy()
    app_env["RENDERER_URL"] = "http://127.0.0.1:3100"
    renderer_env = app_env.copy()
    renderer_env.update(HOST="127.0.0.1", PORT="3100")
    renderer = subprocess.Popen(["node", "/renderer/dist/cli.js", "serve"], env=renderer_env)
    app = subprocess.Popen(sys.argv[1:], env=app_env)
    processes = (app, renderer)
    stopping = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        for process in processes:
            if process.poll() is None:
                process.send_signal(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping and all(p.poll() is None for p in processes):
            time.sleep(0.25)
        if not stopping:
            failed = "app" if app.poll() is not None else "renderer"
            print(f"{failed} stopped; shutting down combined service", flush=True)
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0 if stopping else 1


if __name__ == "__main__":
    raise SystemExit(main())
