"""Contract tests for the brought stub's HTTP surface (starter code, not core functionality).

They pin what must hold on Saturday regardless of storage engine: a health route for App Runner,
the MCP endpoint mounted under a capability path, exactly three tools, the viewer state route, and a
Postgres DDL that mirrors the SQLite one table-for-table.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "testtok"
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    env = {
        **os.environ,
        "LOCI_PORT": str(port),
        "LOCI_PATH_TOKEN": TOKEN,
        "LOCI_DB": str(tmp_path_factory.mktemp("db") / "t.db"),
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
            if proc.poll() is not None:
                raise RuntimeError(proc.stdout.read().decode())
            time.sleep(0.25)
        else:
            proc.kill()
            raise RuntimeError(
                "server never became healthy:\n" + proc.stdout.read().decode()
            )
        yield base
    finally:
        proc.kill()


def _rpc(
    base: str, path: str, method: str, params: dict, id_: int = 1
) -> httpx.Response:
    return httpx.post(
        f"{base}{path}",
        timeout=5,
        headers=MCP_HEADERS,
        json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params},
    )


def _initialize(base: str, path: str) -> httpx.Response:
    return _rpc(
        base,
        path,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    )


def test_health_route(server):
    r = httpx.get(f"{server}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["backend"] == "sqlite" and body["db"] == "ok"


def test_mcp_lives_under_the_capability_path(server):
    r = _initialize(server, f"/loci-{TOKEN}/mcp")
    assert r.status_code == 200, r.text
    assert '"name":"0xL0C1"' in r.text.replace(" ", "")


def test_bare_mcp_path_is_not_served(server):
    assert _initialize(server, "/mcp").status_code == 404


def test_exactly_three_tools(server):
    r = _rpc(server, f"/loci-{TOKEN}/mcp", "tools/list", {}, id_=2)
    assert r.status_code == 200, r.text
    names = sorted(set(re.findall(r'"name":\s*"(observe|ask|commit)"', r.text)))
    assert names == ["ask", "commit", "observe"]


def test_viewer_state_route_under_token(server):
    r = httpx.get(f"{server}/loci-{TOKEN}/api/state")
    assert r.status_code == 200
    assert set(r.json()) == {"places", "objects", "lessons", "claims"}
    assert httpx.get(f"{server}/api/state").status_code == 404


def _tables(sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))


def test_postgres_schema_mirrors_sqlite_schema():
    sqlite = (ROOT / "schema.sql").read_text()
    pg = (ROOT / "schema.postgres.sql").read_text()
    assert _tables(pg) == _tables(sqlite) == {"place", "object", "lesson", "claim"}
    assert "PRAGMA" not in pg


def test_backend_selection_defaults_to_sqlite(monkeypatch):
    for k in ("LOCI_DATABASE_URL", "LOCI_DB_HOST"):
        monkeypatch.delenv(k, raising=False)
    sys.path.insert(0, str(ROOT))
    import db

    assert db.backend() == "sqlite"
    monkeypatch.setenv("LOCI_DATABASE_URL", "postgresql://u:p@h:5432/loci")
    assert db.backend() == "postgres"
