"""[contract] Separate read-only viewer token per Space.

A viewer token opens only the console (/loci-<v>/), /api/state (with access="viewer"),
and /labels.svg. MCP (/loci-<v>/mcp), OAuth (/oauth/authorize, /oauth/token),
Bearer API (/api/spaces, rotate-token, viewer-token) and any write refuse it.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROOT_TOKEN = "root-token"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode("ascii")).digest())
    .decode("ascii")
    .rstrip("=")
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_space(server: str, name: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{server}/api/spaces",
        json={"name": name, "tier": "pro"},
        headers=_auth(ROOT_TOKEN),
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


@functools.cache
def _client_id(server: str) -> str:
    resp = httpx.post(f"{server}/oauth/register", json={"redirect_uris": [CALLBACK]})
    assert resp.status_code == 201, resp.text
    client_id: str = resp.json()["client_id"]
    return client_id


def _mcp_initialize(server: str, token: str) -> httpx.Response:
    return httpx.post(
        f"{server}/loci-{token}/mcp",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "viewer-token-test", "version": "1.0"},
            },
        },
    )


def _authorize(server: str, space_token: str) -> httpx.Response:
    return httpx.post(
        f"{server}/oauth/authorize",
        data={
            "client_id": _client_id(server),
            "redirect_uri": CALLBACK,
            "response_type": "code",
            "state": "st",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "space_token": space_token,
            "subject": "tech@lincinnovations.com",
        },
        follow_redirects=False,
    )


def _client_credentials(server: str, secret: str) -> httpx.Response:
    return httpx.post(
        f"{server}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_secret": secret,
        },
    )


def test_contract_viewer_token_allows_reads_and_refuses_mcp_oauth_and_bearer(
    live_server: str,
) -> None:
    sp = _create_space(live_server, "Viewer Contract Alpha")
    writer = str(sp["capability_token"])
    viewer = str(sp["viewer_token"])
    assert viewer and viewer != writer
    assert sp["viewer_console_url"].endswith(f"/loci-{viewer}/")
    assert sp["console_url"].endswith(f"/loci-{writer}/")

    # Read routes succeed with viewer token and mark access="viewer"
    console_resp = httpx.get(f"{live_server}/loci-{viewer}/")
    assert console_resp.status_code == 200
    assert "Read-only view" in console_resp.text

    state_resp = httpx.get(f"{live_server}/loci-{viewer}/api/state")
    assert state_resp.status_code == 200
    state_body = state_resp.json()
    assert state_body["access"] == "viewer"
    assert state_body["space"]["id"] == sp["space_id"]

    labels_resp = httpx.get(f"{live_server}/loci-{viewer}/labels.svg")
    assert labels_resp.status_code == 200
    assert labels_resp.text.startswith("<svg")

    # POST /loci-<v>/mcp (initialize) -> explicit 403 (not generic 404)
    mcp_resp = _mcp_initialize(live_server, viewer)
    assert mcp_resp.status_code == 403
    assert mcp_resp.json() == {
        "error": "forbidden",
        "error_description": "viewer token is read-only",
    }

    # Bearer <v> on GET /api/spaces, POST /api/spaces, and rotate-token -> 401
    list_resp = httpx.get(f"{live_server}/api/spaces", headers=_auth(viewer))
    assert list_resp.status_code == 401

    create_resp = httpx.post(
        f"{live_server}/api/spaces",
        json={"name": "Viewer Spawned"},
        headers=_auth(viewer),
    )
    assert create_resp.status_code == 401

    rot_resp = httpx.post(
        f"{live_server}/api/spaces/{sp['space_id']}/rotate-token",
        headers=_auth(viewer),
    )
    assert rot_resp.status_code == 401

    # /oauth/authorize with space_token=<v> refused (no code issued)
    authz_resp = _authorize(live_server, viewer)
    assert authz_resp.status_code == 401
    assert "location" not in authz_resp.headers
    assert "code=" not in authz_resp.text

    # client_credentials with client_secret=<v> refused
    cc_resp = _client_credentials(live_server, viewer)
    assert cc_resp.status_code == 401
    assert "access_token" not in cc_resp.text

    # Seed route also refuses viewer token
    seed_resp = httpx.post(f"{live_server}/loci-{viewer}/api/seed")
    assert seed_resp.status_code == 403


def test_contract_writer_regression_and_second_viewer_token_invalidates_first(
    live_server: str,
) -> None:
    sp = _create_space(live_server, "Writer Regression Bravo")
    sid = str(sp["space_id"])
    writer = str(sp["capability_token"])
    viewer_1 = str(sp["viewer_token"])

    # Writer token still does all read + MCP + Bearer + OAuth operations
    assert httpx.get(f"{live_server}/loci-{writer}/").status_code == 200
    writer_state = httpx.get(f"{live_server}/loci-{writer}/api/state")
    assert writer_state.status_code == 200
    assert writer_state.json()["access"] == "writer"
    assert httpx.get(f"{live_server}/loci-{writer}/labels.svg").status_code == 200
    assert _mcp_initialize(live_server, writer).status_code == 200

    spaces_resp = httpx.get(f"{live_server}/api/spaces", headers=_auth(writer))
    assert spaces_resp.status_code == 200
    assert [s["id"] for s in spaces_resp.json()["spaces"]] == [sid]

    authz_ok = _authorize(live_server, writer)
    assert authz_ok.status_code in (302, 303)
    assert "code=" in authz_ok.headers["location"]

    cc_ok = _client_credentials(live_server, writer)
    assert cc_ok.status_code == 200
    assert cc_ok.json()["space_id"] == sid

    # Minting a second viewer token (via space writer credential) replaces the first
    mint_resp = httpx.post(
        f"{live_server}/api/spaces/{sid}/viewer-token",
        headers=_auth(writer),
    )
    assert mint_resp.status_code == 200, mint_resp.text
    mint_body = mint_resp.json()
    viewer_2 = str(mint_body["viewer_token"])
    assert viewer_2 and viewer_2 != viewer_1 and viewer_2 != writer
    assert mint_body["viewer_console_url"].endswith(f"/loci-{viewer_2}/")

    # First viewer token now 404s on every read route
    for suffix in ("/", "/api/state", "/labels.svg"):
        assert httpx.get(f"{live_server}/loci-{viewer_1}{suffix}").status_code == 404, (
            suffix
        )

    # Second viewer token succeeds on every read route with access="viewer"
    assert httpx.get(f"{live_server}/loci-{viewer_2}/").status_code == 200
    v2_state = httpx.get(f"{live_server}/loci-{viewer_2}/api/state")
    assert v2_state.status_code == 200
    assert v2_state.json()["access"] == "viewer"
    assert httpx.get(f"{live_server}/loci-{viewer_2}/labels.svg").status_code == 200

    # Operator root token can also mint a viewer token, replacing viewer_2
    mint_op = httpx.post(
        f"{live_server}/api/spaces/{sid}/viewer-token",
        headers=_auth(ROOT_TOKEN),
    )
    assert mint_op.status_code == 200, mint_op.text
    viewer_3 = str(mint_op.json()["viewer_token"])
    assert viewer_3 and viewer_3 != viewer_2
    assert httpx.get(f"{live_server}/loci-{viewer_2}/api/state").status_code == 404
    assert httpx.get(f"{live_server}/loci-{viewer_3}/api/state").status_code == 200

    # Rotating the writer token still works and keeps the current viewer token working
    rot_w = httpx.post(
        f"{live_server}/api/spaces/{sid}/rotate-token",
        headers=_auth(writer),
    )
    assert rot_w.status_code == 200
    assert httpx.get(f"{live_server}/loci-{viewer_3}/api/state").status_code == 200


def test_contract_hostile_viewer_token_route_and_malformed_tokens(
    live_server: str,
) -> None:
    sp_a = _create_space(live_server, "Hostile Space A")
    sp_b = _create_space(live_server, "Hostile Space B")
    sid_a = str(sp_a["space_id"])
    writer_b = str(sp_b["capability_token"])
    viewer_a = str(sp_a["viewer_token"])

    # viewer-token route called with a viewer token -> 403
    by_viewer = httpx.post(
        f"{live_server}/api/spaces/{sid_a}/viewer-token",
        headers=_auth(viewer_a),
    )
    assert by_viewer.status_code == 403
    assert "viewer_token" not in by_viewer.text

    # viewer-token route called with another Space's writer token -> 403
    by_other_writer = httpx.post(
        f"{live_server}/api/spaces/{sid_a}/viewer-token",
        headers=_auth(writer_b),
    )
    assert by_other_writer.status_code == 403
    assert "viewer_token" not in by_other_writer.text

    # viewer-token route called without credential -> 401
    no_auth = httpx.post(f"{live_server}/api/spaces/{sid_a}/viewer-token")
    assert no_auth.status_code == 401

    # unknown space_id -> 404
    unknown = httpx.post(
        f"{live_server}/api/spaces/sp_nonexistent_space/viewer-token",
        headers=_auth(ROOT_TOKEN),
    )
    assert unknown.status_code == 404

    # Viewer token on A still works after failed hostile attempts
    assert httpx.get(f"{live_server}/loci-{viewer_a}/api/state").status_code == 200

    # Hostile tokens: whitespace, empty, 10 KB long, non-URL-safe chars -> 404/401, no 500
    import db

    hostile_tokens = [
        "",
        "   ",
        f" {viewer_a}",
        f"{viewer_a} ",
        f"{viewer_a}\t",
        "tok with spaces",
        "a" * 10240,
        "non+url=safe",
        "caf\u00e9-token",
        "\u2603",
        f"sha256:{hashlib.sha256(viewer_a.encode()).hexdigest()}",
    ]
    for bad in hostile_tokens:
        assert db.get_space_by_viewer_token(bad) is None
        assert db.get_space_by_token(bad) is None
        # OAuth form endpoints accept arbitrary strings and must return 401, never 500
        assert _authorize(live_server, bad).status_code == 401
        assert _client_credentials(live_server, bad).status_code == 401

    # Bearer header hostile tokens (h11 forbids trailing whitespace in raw header values)
    bearer_headers = [
        b"",
        b"Bearer",
        f"Bearer  {viewer_a}".encode("ascii"),
        f"Bearer {viewer_a} extra".encode("ascii"),
        b"Bearer " + (b"a" * 10240),
        b"Bearer non+url=safe",
        "Bearer caf\u00e9-token".encode("utf-8"),
        f"Bearer sha256:{hashlib.sha256(viewer_a.encode()).hexdigest()}".encode(
            "ascii"
        ),
    ]
    for raw_hdr in bearer_headers:
        hdrs = [(b"Authorization", raw_hdr)] if raw_hdr else []
        for method, path in (
            ("GET", "/api/spaces"),
            ("POST", f"/api/spaces/{sid_a}/viewer-token"),
            ("POST", f"/api/spaces/{sid_a}/rotate-token"),
        ):
            r = httpx.request(method, f"{live_server}{path}", headers=hdrs)
            assert r.status_code == 401, (raw_hdr[:32], path, r.status_code)

    # URL path hostile tokens -> 404, never 500
    url_hostile = [
        "",
        "%20",
        f"%20{viewer_a}",
        f"{viewer_a}%20",
        "tok%20space",
        "a" * 10240,
        "non+url=safe",
        "caf%C3%A9",
    ]
    for bad_url in url_hostile:
        for suffix in ("/", "/api/state", "/labels.svg"):
            r_read = httpx.get(f"{live_server}/loci-{bad_url}{suffix}")
            assert r_read.status_code == 404, (bad_url[:32], suffix, r_read.status_code)
        r_mcp = httpx.post(f"{live_server}/loci-{bad_url}/mcp", json={})
        assert r_mcp.status_code == 404, (bad_url[:32], r_mcp.status_code)


def test_migration_adds_viewer_token_hash_to_origin_main_sqlite_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "legacy_main.db"
    monkeypatch.setenv("LOCI_DB", str(db_file))
    monkeypatch.setenv("LOCI_PATH_TOKEN", "migrated-root-token")
    monkeypatch.delenv("LOCI_DATABASE_URL", raising=False)
    monkeypatch.delenv("LOCI_DB_HOST", raising=False)

    # Create a SQLite DB using origin/main's 6-table schema (no viewer_token_hash column)
    origin_main_schema = """
    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS space (
        id                    TEXT PRIMARY KEY,
        slug                  TEXT NOT NULL UNIQUE,
        name                  TEXT NOT NULL,
        tier                  TEXT NOT NULL DEFAULT 'free',
        capability_token      TEXT NOT NULL UNIQUE,
        capability_token_hash TEXT NOT NULL UNIQUE,
        tag_prefix            TEXT NOT NULL DEFAULT '',
        created_at            TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS membership (
        id              TEXT PRIMARY KEY,
        space_id        TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
        subject         TEXT NOT NULL,
        role            TEXT NOT NULL DEFAULT 'writer',
        token_hash      TEXT UNIQUE,
        created_at      TEXT NOT NULL,
        UNIQUE(space_id, subject)
    );
    CREATE TABLE IF NOT EXISTS place (
        id              TEXT PRIMARY KEY,
        space_id        TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
        label           TEXT NOT NULL,
        path            TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        UNIQUE(space_id, id),
        UNIQUE(space_id, path)
    );
    CREATE TABLE IF NOT EXISTS object (
        id              TEXT PRIMARY KEY,
        space_id        TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
        label           TEXT NOT NULL,
        canonical_class TEXT NOT NULL DEFAULT '',
        material        TEXT NOT NULL DEFAULT 'composite_or_other',
        mounting        TEXT NOT NULL DEFAULT 'handheld_portable',
        aliases_json    TEXT NOT NULL DEFAULT '[]',
        attrs_json      TEXT NOT NULL DEFAULT '{}',
        place_id        TEXT,
        tag_code        TEXT,
        verbatim_text   TEXT NOT NULL DEFAULT '',
        description     TEXT NOT NULL DEFAULT '',
        embedding_model TEXT NOT NULL DEFAULT 'deterministic-64d',
        embedding_json  TEXT NOT NULL DEFAULT '[]',
        first_seen_at   TEXT NOT NULL,
        last_seen_at    TEXT NOT NULL,
        UNIQUE(space_id, id),
        UNIQUE(space_id, tag_code),
        FOREIGN KEY (space_id, place_id) REFERENCES place(space_id, id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS lesson (
        id               TEXT PRIMARY KEY,
        space_id         TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
        object_id        TEXT NOT NULL,
        author_subject   TEXT NOT NULL DEFAULT 'anonymous',
        title            TEXT NOT NULL,
        intent           TEXT,
        next_question    TEXT,
        is_cursor_active INTEGER NOT NULL DEFAULT 1,
        created_at       TEXT NOT NULL,
        UNIQUE(space_id, id),
        FOREIGN KEY (space_id, object_id) REFERENCES object(space(id), id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS claim (
        id              TEXT PRIMARY KEY,
        space_id        TEXT NOT NULL REFERENCES space(id) ON DELETE CASCADE,
        lesson_id       TEXT NOT NULL,
        text            TEXT NOT NULL,
        confidence      REAL,
        status          TEXT NOT NULL DEFAULT 'asserted',
        FOREIGN KEY (space_id, lesson_id) REFERENCES lesson(space_id, id) ON DELETE CASCADE
    );
    """.replace("object(space(id), id)", "object(space_id, id)")

    existing_writer = "existing-writer-token-12345"
    existing_hash = hashlib.sha256(existing_writer.encode("utf-8")).hexdigest()
    with sqlite3.connect(db_file) as conn:
        conn.executescript(origin_main_schema)
        conn.execute(
            "INSERT INTO space (id, slug, name, tier, capability_token, capability_token_hash, tag_prefix, created_at) "
            "VALUES ('sp_preexisting', 'preexisting', 'Preexisting Space', 'pro', ?, ?, 'PR', '2026-01-01T00:00:00Z')",
            (f"sha256:{existing_hash}", existing_hash),
        )
        cols_before = {
            str(r[1]) for r in conn.execute("PRAGMA table_info(space)").fetchall()
        }
        assert "viewer_token_hash" not in cols_before

    import db

    db.init_db()

    with sqlite3.connect(db_file) as conn:
        cols_after = {
            str(r[1]) for r in conn.execute("PRAGMA table_info(space)").fetchall()
        }
        assert "viewer_token_hash" in cols_after

    # Existing writer token still resolves
    found_writer = db.get_space_by_token(existing_writer)
    assert found_writer is not None
    assert found_writer["id"] == "sp_preexisting"

    # Minting a viewer token on the migrated space stores only the digest and resolves cleanly
    vtok = db.mint_viewer_token("sp_preexisting")
    assert isinstance(vtok, str) and len(vtok) >= 24
    found_viewer = db.get_space_by_viewer_token(vtok)
    assert found_viewer is not None
    assert found_viewer["id"] == "sp_preexisting"
    assert db.get_space_by_token(vtok) is None

    with sqlite3.connect(db_file) as conn:
        raw_dump = str(conn.execute("SELECT * FROM space").fetchall())
        assert vtok not in raw_dump
