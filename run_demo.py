"""One-command launcher for the FinSight + TokenOps demo.

Starts, in order:
  1. TokenOps control plane       (separate process, own SQLite ledger)   :8800
  2. Four MCP servers             (streamable HTTP)                       :8101-8104
  3. FinSight web app / agent     (FastAPI + LangGraph)                   :8000

Usage (from the project root, with the 'tokenops' venv):
    .\\tokenops\\Scripts\\python.exe run_demo.py
    .\\tokenops\\Scripts\\python.exe run_demo.py --no-plane   # plane already running elsewhere
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from app.config import ROOT_DIR, settings
from app.data_gen import ensure_data

PY = sys.executable
SCRIPTS = Path(PY).parent


def _control_plane_exe() -> str:
    for name in ("control-plane.exe", "control-plane"):
        if (SCRIPTS / name).exists():
            return str(SCRIPTS / name)
    found = shutil.which("control-plane")
    if not found:
        sys.exit("control-plane CLI not found - run: pip install -r requirements.txt")
    return found


def _wait(url: str, name: str, timeout: float = 40) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            httpx.get(url, timeout=1.5)
            print(f"  [ok] {name:<22} {url}")
            return
        except Exception:
            time.sleep(0.4)
    print(f"  [!!] {name} did not respond at {url}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-plane", action="store_true", help="do not start the control plane")
    args = ap.parse_args()

    ensure_data()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(ROOT_DIR)}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    procs: list[tuple[str, subprocess.Popen]] = []

    def start(name: str, cmd: list[str]) -> None:
        procs.append((name, subprocess.Popen(cmd, cwd=ROOT_DIR, env=env, creationflags=flags)))

    try:
        print("Starting FinSight demo stack")
        if not args.no_plane:
            settings.control_plane_db.parent.mkdir(parents=True, exist_ok=True)
            start("control-plane", [_control_plane_exe(), "serve", "--host", settings.control_plane_host,
                                    "--port", str(settings.control_plane_port), "--db", str(settings.control_plane_db)])
            _wait(settings.control_plane_url + "/health", "TokenOps control plane")

        for spec in settings.mcp_servers:
            start(f"mcp:{spec.name}", [PY, "-m", spec.module])
        for spec in settings.mcp_servers:
            # a GET on /mcp returns 4xx/405 once the server is listening - good enough as a probe
            _wait(settings.mcp_url(spec), f"MCP {spec.name}")

        start("finsight-app", [PY, "-m", "uvicorn", "app.server:app", "--host", settings.app_host,
                               "--port", str(settings.app_port), "--log-level", settings.log_level.lower()])
        _wait(f"http://{settings.app_host}:{settings.app_port}/api/config", "FinSight app", 90)

        print(f"\n  Dashboard      http://{settings.app_host}:{settings.app_port}/")
        print(f"  Control plane  {settings.control_plane_url}/")
        print("  Press Ctrl+C to stop everything.\n")
        while True:
            for name, p in procs:
                if p.poll() is not None:
                    raise SystemExit(f"{name} exited with code {p.returncode}")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping...")
        for _, p in reversed(procs):
            if p.poll() is None:
                try:
                    p.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                except Exception:
                    p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
