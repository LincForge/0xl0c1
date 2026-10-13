"""Multi-tenant, OAuth 2.1, Vertex AI embedding, label generator, and console contract tests."""

from __future__ import annotations

import base64
import hashlib
import re
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _tables(sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))


def test_six_table_schema_and_postgres_rls_policies() -> None:
    sqlite_sql = (ROOT / "schema.sql").read_text()
    pg_sql = (ROOT / "schema.postgres.sql").read_text()
    expected = {"space", "membership", "place", "object", "lesson", "claim"}
    assert _tables(sqlite_sql) == expected
    assert _tables(pg_sql) == expected
    for table in ("place", "object", "lesson", "claim"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in pg_sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in pg_sql
    assert "current_setting('loci.current_space_id', true)" in pg_sql


def test_crockford_base32_generator_excludes_homoglyphs_and_renders_svg() -> None:
    from labels import CROCKFORD_ALPHABET, generate_tag_codes, render_label_sheet_svg

    for forbidden in ("0", "O", "1", "I", "L"):
        assert forbidden not in CROCKFORD_ALPHABET

    codes = generate_tag_codes(space_id="space-alpha", count=24, existing={"K94B"})
    assert len(codes) == 24
    assert len(set(codes)) == 24
    for code in codes:
        assert re.fullmatch(
            r"[23456789ABCDEFGHJKMNPQRSTVWXYZ]{2}-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{2}",
            code,
        )
        assert code.replace("-", "") != "K94B"

    svg = render_label_sheet_svg(
        space_name="LINC Hardware Bench",
        labels=[
            {
                "tag_code": "K9-4B",
                "label": "Main Shutoff Valve",
                "place": "Utility Closet",
            },
            {"tag_code": codes[0], "label": "Unassigned Tag", "place": "Scan to bind"},
        ],
    )
    assert svg.startswith("<svg")
    assert "K9-4B" in svg
    assert "Main Shutoff Valve" in svg
    assert codes[0] in svg


def test_vertex_and_deterministic_embedding_adapters() -> None:
    from embeddings import (
        DeterministicEmbeddingProvider,
        VertexAIEmbeddingProvider,
        cosine_similarity,
    )

    det = DeterministicEmbeddingProvider()
    v_brass_1 = det.embed(
        "valve | metal_brass_or_bronze | brass shutoff ball valve under sink"
    )
    v_brass_2 = det.embed(
        "valve | metal_brass_or_bronze | brass isolation valve under dish sink"
    )
    v_wood = det.embed("shelf | wood_finished | oak bookshelf in living room")
    assert cosine_similarity(v_brass_1, v_brass_2) > cosine_similarity(
        v_brass_1, v_wood
    )

    calls: list[tuple[str, str, dict[str, object]]] = []

    class FakeVertexTransport:
        def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            calls.append((method, url, kwargs))
            return httpx.Response(
                200,
                json={
                    "predictions": [
                        {"embeddings": {"values": [3.0, 4.0, 0.0]}},
                    ]
                },
                request=httpx.Request(method, url),
            )

    vertex = VertexAIEmbeddingProvider(
        project="loci-0xl0c1",
        location="us-west1",
        access_token="ya29.fake-token",
        transport=FakeVertexTransport(),
    )
    vec = vertex.embed("brass ball valve", task_type="RETRIEVAL_QUERY")
    assert len(calls) == 1
    assert (
        "projects/loci-0xl0c1/locations/us-west1/publishers/google/models/text-embedding-004:predict"
        in calls[0][1]
    )
    assert abs(vec[0] - 0.6) < 1e-6
    assert abs(vec[1] - 0.8) < 1e-6


def test_tenant_isolation_across_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCI_DB", str(tmp_path / "isolation.db"))
    monkeypatch.delenv("LOCI_DATABASE_URL", raising=False)
    monkeypatch.delenv("LOCI_DB_HOST", raising=False)
    monkeypatch.delenv("AMBIGUOUS_API_KEY", raising=False)
    monkeypatch.delenv("LOCI_AMBIGUOUS_SHEET_ID", raising=False)

    import db
    import server

    db.init_db()
    space_a = db.create_space(name="Tenant Alpha", slug="tenant-alpha")
    space_b = db.create_space(name="Tenant Bravo", slug="tenant-bravo")

    obs_a = server.observe(
        place_label="boiler room",
        canonical_class="valve",
        material="metal_brass_or_bronze",
        mounting="wall_mounted",
        visible_verbatim_text="APOLLO 600 WOG 3/4",
        description="corroded brass isolation ball valve with yellow handle",
        visible_tag_code="K9-4B",
        user_label="main boiler shutoff",
        space_id=str(space_a["id"]),
    )
    assert obs_a["status"] == "ok"
    obj_a_id = str(obs_a["object_id"])

    commit_a = server.commit(
        object_id=obj_a_id,
        title="Checked packing nut",
        claims=[{"text": "Needs 3/4 inch PTFE stem packing kit", "confidence": "high"}],
        save=True,
        intent="Stop slow weep",
        next_question="Verify pressure holds at 60 PSI after repacking",
        space_id=str(space_a["id"]),
    )
    assert commit_a["status"] in {"ok", "committed"}

    # Tenant B must NEVER see Tenant A's object by ID, tag_code, verbatim_text, or description
    assert (
        server.ask(object_id=obj_a_id, space_id=str(space_b["id"]))["status"]
        == "no_match"
    )
    assert (
        server.ask(visible_tag_code="K9-4B", space_id=str(space_b["id"]))["status"]
        == "no_match"
    )
    assert (
        server.ask(
            place_label="boiler room",
            visible_verbatim_text="APOLLO 600 WOG 3/4",
            space_id=str(space_b["id"]),
        )["status"]
        == "no_match"
    )
    assert (
        server.ask(
            description="corroded brass isolation ball valve with yellow handle",
            place_label="boiler room",
            space_id=str(space_b["id"]),
        )["status"]
        == "no_match"
    )

    # Tenant B cannot commit a lesson to Tenant A's object_id
    cross_commit = server.commit(
        object_id=obj_a_id,
        title="Cross-tenant write attempt",
        claims=[{"text": "Should fail", "confidence": 0.9}],
        save=True,
        space_id=str(space_b["id"]),
    )
    assert cross_commit["status"] in {"error", "unknown_object"}

    # Tenant B CAN use the same tag_code K9-4B in its own space without collision
    obs_b = server.observe(
        place_label="kitchen",
        canonical_class="valve",
        material="metal_chrome_or_steel",
        mounting="wall_mounted",
        visible_verbatim_text="DAHL 1/2 PEX",
        description="chrome quarter-turn angle stop under kitchen sink",
        visible_tag_code="K9-4B",
        user_label="dishwasher angle stop",
        space_id=str(space_b["id"]),
    )
    assert obs_b["status"] == "ok"
    assert obs_b["object_id"] != obj_a_id

    # Meanwhile duplicate tag_code inside Tenant A DOES trigger tag_conflict
    dup_a = server.observe(
        place_label="boiler room",
        canonical_class="valve",
        material="metal_brass_or_bronze",
        mounting="wall_mounted",
        visible_verbatim_text="OTHER",
        description="another valve",
        visible_tag_code="K9-4B",
        space_id=str(space_a["id"]),
    )
    assert dup_a["status"] == "tag_conflict"

    # Both tenants resolve their own K9-4B cleanly
    res_a = server.ask(visible_tag_code="K9-4B", space_id=str(space_a["id"]))
    res_b = server.ask(visible_tag_code="K9-4B", space_id=str(space_b["id"]))
    assert res_a["match"]["id"] == obj_a_id
    assert res_b["match"]["id"] == obs_b["object_id"]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory):
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


def test_oauth21_and_web_console_http_endpoints(live_server: str) -> None:
    server = live_server
    # 1. OAuth 2.1 Discovery Metadata
    meta = httpx.get(f"{server}/.well-known/oauth-authorization-server")
    assert meta.status_code == 200
    meta_json = meta.json()
    assert "authorization_endpoint" in meta_json
    assert "token_endpoint" in meta_json
    assert "registration_endpoint" in meta_json
    assert "S256" in meta_json["code_challenge_methods_supported"]

    resource_meta = httpx.get(f"{server}/.well-known/oauth-protected-resource")
    assert resource_meta.status_code == 200

    # 2. Dynamic Client Registration
    reg = httpx.post(
        f"{server}/oauth/register",
        json={
            "client_name": "Claude MCP Connector",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        },
    )
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]

    # 3. Create two isolated spaces via API
    sp1 = httpx.post(
        f"{server}/api/spaces", json={"name": "Bench Space 1", "tier": "pro"}
    ).json()
    sp2 = httpx.post(
        f"{server}/api/spaces", json={"name": "Field Space 2", "tier": "team"}
    ).json()
    assert sp1["space_id"] != sp2["space_id"]
    assert sp1["capability_token"] != sp2["capability_token"]

    # 4. OAuth 2.1 Authorization Code + PKCE Flow for Space 1
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    auth_resp = httpx.post(
        f"{server}/oauth/authorize",
        data={
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "response_type": "code",
            "state": "xyz123",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "space_id": sp1["space_id"],
            "subject": "tech1@lincinnovations.com",
        },
        follow_redirects=False,
    )
    assert auth_resp.status_code in (302, 303)
    location = auth_resp.headers["location"]
    code_match = re.search(r"code=([^&]+)", location)
    assert code_match is not None
    code = code_match.group(1)

    tok_resp = httpx.post(
        f"{server}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_verifier": verifier,
        },
    )
    assert tok_resp.status_code == 200
    access_token = tok_resp.json()["access_token"]
    assert tok_resp.json()["space_id"] == sp1["space_id"]

    # 5. MCP over OAuth 2.1 Bearer token on /mcp AND per-space capability path /loci-<token>/mcp
    mcp_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {access_token}",
    }
    init_oauth = httpx.post(
        f"{server}/mcp",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "claude-mcp", "version": "1.0"},
            },
        },
    )
    assert init_oauth.status_code == 200, init_oauth.text

    init_sp2 = httpx.post(
        f"{server}/loci-{sp2['capability_token']}/mcp",
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
                "clientInfo": {"name": "chatgpt-mcp", "version": "1.0"},
            },
        },
    )
    assert init_sp2.status_code == 200, init_sp2.text

    # 6. Printable Crockford Base32 SVG endpoint & Pricing / Checkout endpoints
    svg_resp = httpx.get(f"{server}/loci-{sp1['capability_token']}/labels.svg")
    assert svg_resp.status_code == 200
    assert "<svg" in svg_resp.text

    pricing_resp = httpx.get(f"{server}/pricing")
    assert pricing_resp.status_code == 200
    assert "Field Service" in pricing_resp.text

    checkout_resp = httpx.post(
        f"{server}/api/billing/checkout",
        json={"tier": "team", "space_id": sp2["space_id"], "email": "ops@example.com"},
    )
    assert checkout_resp.status_code == 200
    assert "checkout_url" in checkout_resp.json()

    # 7. Root-token seed endpoint seeds the 5 showcase spaces idempotently
    seed1 = httpx.post(f"{server}/loci-root-token/api/seed")
    assert seed1.status_code == 200
    assert len(seed1.json()["spaces"]) == 5
    assert all(s["seeded_new"] is True for s in seed1.json()["spaces"])

    seed2 = httpx.post(f"{server}/loci-root-token/api/seed")
    assert seed2.status_code == 200
    assert all(s["seeded_new"] is False for s in seed2.json()["spaces"])
