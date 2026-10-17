"""Shared fixtures: a real server subprocess on a private SQLite DB."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    import os
    import socket
    import subprocess
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    env = {
        **os.environ,
        "LOCI_PORT": str(port),
        "LOCI_PATH_TOKEN": "root-token",
        "LOCI_DB": str(tmp_path_factory.mktemp("mt_db") / "mt.db"),
    }
    for k in ("LOCI_DATABASE_URL", "LOCI_DB_HOST"):
        env.pop(k, None)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            proc.kill()
            raise RuntimeError("server failed to start")
        yield base
    finally:
        proc.kill()
