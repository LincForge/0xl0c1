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
sys.path.insert(0, str(ROOT))
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
    assert body == {
        "ok": True,
        "backend": "ambiguous_sheets",
        "configured": False,
        "available": False,
    }


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
    assert r.status_code == 503
    assert r.json()["error"] == "ambiguous_not_configured"
    assert httpx.get(f"{server}/api/state").status_code == 404


def _tables(sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))


def test_postgres_schema_mirrors_sqlite_schema():
    sqlite = (ROOT / "schema.sql").read_text()
    pg = (ROOT / "schema.postgres.sql").read_text()
    assert _tables(pg) == _tables(sqlite) == {"place", "object", "lesson", "claim"}
    assert "PRAGMA" not in pg


def test_old_db_backend_selection_defaults_to_sqlite(monkeypatch):
    for k in ("LOCI_DATABASE_URL", "LOCI_DB_HOST"):
        monkeypatch.delenv(k, raising=False)
    sys.path.insert(0, str(ROOT))
    import db

    assert db.backend() == "sqlite"
    monkeypatch.setenv("LOCI_DATABASE_URL", "postgresql://u:p@h:5432/loci")
    assert db.backend() == "postgres"


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.body, self.status_code = body, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "bad",
                request=httpx.Request("GET", "http://t"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.body


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_store_append_and_read_are_hermetic():
    from events import AmbiguousEventStore, EVENT_COLUMNS, new_event

    transport = FakeTransport(
        [
            FakeResponse({"updatedRows": 1}),
            FakeResponse({"data": [list(EVENT_COLUMNS)]}),
        ]
    )
    store = AmbiguousEventStore(
        api_key="secret",
        sheet_id="sheet",
        base_url="https://fake/api",
        transport=transport,
    )
    event = new_event("object_observed", "object", payload={"description": "valve"})
    assert store.append(event).ok
    rows, result = store.read()
    assert result.ok and rows == []
    assert transport.calls[0][0:2] == (
        "POST",
        "https://fake/api/sheets/sheet/values/append",
    )
    assert transport.calls[0][2]["json"]["values"][0][1] == event["event_id"]
    assert transport.calls[0][2]["json"]["range"] == "Events!A1:M1"


def test_store_reads_documented_sheet_envelope():
    from events import AmbiguousEventStore, EVENT_COLUMNS

    columns = [
        {"id": chr(65 + i), "name": name} for i, name in enumerate(EVENT_COLUMNS)
    ]
    values = [
        "1",
        "event",
        "object_observed",
        "now",
        "object",
        "bath",
        "valve",
        "metal_chrome_or_steel",
        "wall_mounted",
        "",
        "",
        "",
        '{"version":1,"description":"under sink"}',
    ]
    row = {chr(65 + i): value for i, value in enumerate(values)}
    transport = FakeTransport(
        [
            FakeResponse(
                {
                    "data": {
                        "sheets": [
                            {
                                "name": "Events",
                                "columns": columns,
                                "rows": [row],
                            }
                        ]
                    }
                }
            )
        ]
    )
    rows, result = AmbiguousEventStore(
        api_key="key", sheet_id="sheet", transport=transport
    ).read()
    assert result.ok
    assert rows and rows[0]["event_id"] == "event"


def test_probe_requires_an_exact_readback():
    from events import AmbiguousEventStore, EVENT_COLUMNS

    header = list(EVENT_COLUMNS)
    transport = FakeTransport(
        [FakeResponse({"updatedRows": 1}), FakeResponse({"data": [header]})]
    )
    result = AmbiguousEventStore(
        api_key="key", sheet_id="sheet", transport=transport
    ).probe()
    assert result.error == "ambiguous_probe_row_not_found"


def test_store_fails_closed_for_missing_configuration_and_invalid_data():
    from events import AmbiguousEventStore

    assert (
        AmbiguousEventStore(api_key="", sheet_id="sheet").append({}).error
        == "ambiguous_not_configured"
    )
    transport = FakeTransport([FakeResponse({"data": [["wrong"]]})])
    rows, result = AmbiguousEventStore(
        api_key="key", sheet_id="sheet", transport=transport
    ).read()
    assert rows is None and result.error == "ambiguous_invalid_read_response"


def test_observe_commit_preview_and_confirmed_resume(monkeypatch):
    import json

    import server
    from events import StoreResult

    class MemoryStore:
        def __init__(self):
            self.events = []
            self.append_calls = 0

        def append(self, event):
            self.append_calls += 1
            saved = dict(event)
            saved["payload"] = json.loads(saved["payload_json"])
            self.events.append(saved)
            return StoreResult(True)

        def read(self):
            return self.events, StoreResult(True)

    store = MemoryStore()
    monkeypatch.setattr(server, "_store", lambda: store)
    missing = server.observe(
        "", "valve", "metal_chrome_or_steel", "wall_mounted", "", "stop"
    )
    assert missing["status"] == "needs_place" and store.append_calls == 0
    observed = server.observe(
        "demo bath",
        "toilet shutoff",
        "metal_chrome_or_steel",
        "wall_mounted",
        "1/4 turn",
        "under toilet",
        "T-1",
    )
    object_id = observed["object_id"]
    assert observed["status"] == "ok" and store.append_calls == 1
    preview = server.commit(
        object_id,
        "replace packing",
        [{"text": "use 3/8 valve", "confidence": "high"}],
        False,
        "stop drip",
        "bring wrench",
    )
    assert preview["status"] == "preview" and store.append_calls == 1
    saved = server.commit(
        object_id,
        "replace packing",
        [{"text": "use 3/8 valve", "confidence": "high"}],
        True,
        "stop drip",
        "bring wrench",
    )
    assert saved["status"] == "ok" and saved["claims_persisted"] == 1
    resumed = server.ask(object_id=object_id)
    assert resumed["status"] == "ok" and resumed["match"]["id"] == object_id
    assert resumed["lessons"][0]["next_question"] == "bring wrench"
    assert resumed["claims"][0]["status"] == "untrusted"
