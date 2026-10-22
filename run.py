#!/usr/bin/env python3
"""
run.py – Start both the MCP server and the FastAPI app with one command.

Usage:
    python run.py

Environment variables (all optional):
  VOICEGATE_SECRET     – HMAC signing secret (random per start if unset)
  VOICEGATE_THRESHOLD  – cosine similarity threshold (default 0.40)
  VOICEGATE_LLM        – 'openai' | 'anthropic' | 'none' (default none)
  VOICEGATE_LIVENESS   – '1' to enable challenge-phrase liveness check
  MCP_PORT             – MCP server port (default 8765)
  MCP_HOST             – MCP server host (default 127.0.0.1)
  APP_PORT             – FastAPI app port (default 8000)
  APP_HOST             – FastAPI app host (default 0.0.0.0)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import threading
import signal

import secrets

from pathlib import Path

APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")
APP_PORT = int(os.environ.get("APP_PORT", "8000"))
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8765"))

# Ensure both MCP server and FastAPI app share the same HMAC secret
if not os.environ.get("VOICEGATE_SECRET"):
    os.environ["VOICEGATE_SECRET"] = secrets.token_hex(32)

# Auto-detect local virtualenv interpreter if run with system Python
PYTHON = sys.executable
base_dir = Path(__file__).resolve().parent
for cand in [
    base_dir / ".venv" / "Scripts" / "python.exe",
    base_dir / "venv" / "Scripts" / "python.exe",
    base_dir / ".venv" / "bin" / "python",
    base_dir / "venv" / "bin" / "python",
]:
    if cand.is_file():
        PYTHON = str(cand)
        break

procs: list[subprocess.Popen] = []
_shutting_down = threading.Event()


def check_dependencies(py_bin: str) -> bool:
    try:
        res = subprocess.run(
            [py_bin, "-c", "import uvicorn, fastapi, mcp"],
            capture_output=True,
            text=True,
        )
        return res.returncode == 0
    except Exception:
        return False


def start_process(cmd: list[str], name: str) -> subprocess.Popen:
    print(f"[run.py] Starting {name} ({PYTHON}): {' '.join(cmd[3:])}", flush=True)
    proc = subprocess.Popen(cmd, env=os.environ.copy())
    procs.append(proc)
    return proc


def shutdown(sig=None, frame=None):
    if _shutting_down.is_set():
        return
    _shutting_down.set()
    print("\n[run.py] Shutting down servers...", flush=True)
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    sys.exit(0)


def monitor(proc: subprocess.Popen, name: str):
    proc.wait()
    if not _shutting_down.is_set():
        print(f"[run.py] {name} exited with code {proc.returncode}", flush=True)


if __name__ == "__main__":
    if not check_dependencies(PYTHON):
        print(
            f"\n[run.py] ERROR: Required modules (uvicorn, fastapi, mcp) not found in:\n"
            f"         {PYTHON}\n\n"
            f"Please install dependencies into your environment:\n"
            f"    {PYTHON} -m pip install -r requirements.txt\n",
            flush=True,
        )
        sys.exit(1)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start MCP server
    mcp_proc = start_process(
        [PYTHON, "-m", "uvicorn", "mcp_server.server:app",
         "--host", MCP_HOST, "--port", str(MCP_PORT), "--log-level", "info"],
        "MCP Server",
    )
    threading.Thread(target=monitor, args=(mcp_proc, "MCP Server"), daemon=True).start()

    # Brief pause to let the MCP server bind its port
    time.sleep(1.5)

    # Start FastAPI app
    app_proc = start_process(
        [PYTHON, "-m", "uvicorn", "app.server:app",
         "--host", APP_HOST, "--port", str(APP_PORT), "--log-level", "info"],
        "FastAPI App",
    )
    threading.Thread(target=monitor, args=(app_proc, "FastAPI App"), daemon=True).start()

    print(
        f"\n[run.py] Voice Gate running!\n"
        f"  Web UI  : http://localhost:{APP_PORT}/\n"
        f"  API     : http://localhost:{APP_PORT}/docs\n"
        f"  MCP     : http://{MCP_HOST}:{MCP_PORT}/mcp\n"
        "\nPress Ctrl+C to stop.\n",
        flush=True,
    )

    # Keep the main thread alive; monitor children
    try:
        while not _shutting_down.is_set():
            for p in procs:
                if p.poll() is not None:
                    shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()
