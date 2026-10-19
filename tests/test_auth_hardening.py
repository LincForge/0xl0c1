"""[contract] Auth hardening: signing secret, tokens at rest, OAuth bound to space ownership.

Every test here fails on the pre-hardening code and passes after it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import stat
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LEGACY_CONSTANT = "loci-dev-signing-secret"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode("ascii")).digest())
    .decode("ascii")
    .rstrip("=")
)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _forge_access_token(secret: str, space_id: str) -> str:
    key = hashlib.sha256(f"loci-oauth21:{secret}".encode("utf-8")).digest()
    payload = {
        "space_id": space_id,
        "sub": "attacker",
        "role": "writer",
        "exp": int(time.time()) + 3600,
        "jti": "000000000000",
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(key, raw, hashlib.sha256).digest()
    return f"loci_at_{_b64u(raw)}.{_b64u(sig)}"


# ---------------------------------------------------------------- signing secret


@pytest.fixture
def no_env_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for key in ("LOCI_AUTH_SECRET", "LOCI_PATH_TOKEN", "K_SERVICE"):
        monkeypatch.delenv(key, raising=False)
    secret_file = tmp_path / "auth-secret"
    monkeypatch.setenv("LOCI_AUTH_SECRET_FILE", str(secret_file))
    return secret_file


def test_contract_legacy_constant_secret_cannot_forge_tokens(no_env_secret: Path) -> None:
    from auth import verify_access_token

    assert verify_access_token(_forge_access_token(LEGACY_CONSTANT, "default")) is None


def test_contract_whitespace_secret_env_is_treated_as_unset(
    no_env_secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auth import verify_access_token

    monkeypatch.setenv("LOCI_AUTH_SECRET", "   ")
    assert verify_access_token(_forge_access_token("   ", "default")) is None


def test_contract_fallback_secret_is_random_private_and_stable(no_env_secret: Path) -> None:
    from auth import mint_access_token, verify_access_token

    token = mint_access_token(space_id="sp_a")
    assert no_env_secret.exists()
    assert stat.S_IMODE(no_env_secret.stat().st_mode) == 0o600
    assert len(no_env_secret.read_text().strip()) >= 32
    claims = verify_access_token(token)
    assert claims is not None and claims.space_id == "sp_a"


def test_contract_cloud_run_without_secret_fails_fast(
    no_env_secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auth import assert_signing_configured, mint_access_token

    monkeypatch.setenv("K_SERVICE", "loci")
    with pytest.raises(RuntimeError):
        assert_signing_configured()
    with pytest.raises(RuntimeError):
        mint_access_token(space_id="sp_a")
    assert not no_env_secret.exists()


# ---------------------------------------------------------------- tokens at rest


@pytest.fixture
def sqlite_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for key in ("LOCI_DATABASE_URL", "LOCI_DB_HOST"):
        monkeypatch.delenv(key, raising=False)
    db_file = tmp_path / "rest.db"
    monkeypatch.setenv("LOCI_DB", str(db_file))
    monkeypatch.setenv("LOCI_PATH_TOKEN", "root-token-at-rest")
    return db_file


def _stored_tokens(db_file: Path) -> dict[str, str]:
    with sqlite3.connect(db_file) as conn:
        return dict(conn.execute("SELECT id, capability_token FROM space").fetchall())


def test_contract_spaces_store_no_plaintext_token(sqlite_db: Path) -> None:
    import db

    sp = db.create_space(name="Bench")
    stored = _stored_tokens(sqlite_db)
    assert sp["capability_token"] not in stored.values()
    assert "root-token-at-rest" not in stored.values()
    assert all(v.startswith("sha256:") for v in stored.values())
    found = db.get_space_by_token(sp["capability_token"])
    assert found is not None and found["id"] == sp["id"]


def test_contract_stored_value_or_blank_does_not_authenticate(sqlite_db: Path) -> None:
    import db

    sp = db.create_space(name="Bench")
    stored = _stored_tokens(sqlite_db)[sp["id"]]
    assert db.get_space_by_token(stored) is None
    assert db.get_space_by_token("") is None
    assert db.get_space_by_token("   ") is None


def test_contract_init_db_redacts_legacy_plaintext_rows(sqlite_db: Path) -> None:
    import db

    db.init_db()
    with sqlite3.connect(sqlite_db) as conn:
        conn.execute(
            "INSERT INTO space (id, slug, name, tier, capability_token, capability_token_hash, tag_prefix, created_at) "
            "VALUES ('sp_legacy', 'legacy', 'Legacy', 'free', 'legacy-plain-tok', ?, '', '2026-01-01')",
            (hashlib.sha256(b"legacy-plain-tok").hexdigest(),),
        )
    db.init_db()
    assert _stored_tokens(sqlite_db)["sp_legacy"] != "legacy-plain-tok"
    found = db.get_space_by_token("legacy-plain-tok")
    assert found is not None and found["id"] == "sp_legacy"


# ---------------------------------------------------------------- OAuth binds to space ownership


def _new_space(server: str, name: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{server}/api/spaces", json={"name": name}, headers={"Authorization": "Bearer root-token"}
    )
    assert resp.status_code == 201
    body: dict[str, Any] = resp.json()
    return body


def test_contract_client_credentials_requires_the_space_token(live_server: str) -> None:
    mine = _new_space(live_server, "CC Mine")
    other = _new_space(live_server, "CC Other")
    url = f"{live_server}/oauth/token"

    no_secret = httpx.post(url, data={"grant_type": "client_credentials", "space_id": other["space_id"]})
    assert no_secret.status_code == 401
    assert "access_token" not in no_secret.text

    wrong = httpx.post(
        url,
        data={"grant_type": "client_credentials", "space_id": other["space_id"], "client_secret": "nope"},
    )
    assert wrong.status_code == 401

    mismatch = httpx.post(
        url,
        data={
            "grant_type": "client_credentials",
            "space_id": other["space_id"],
            "client_secret": mine["capability_token"],
        },
    )
    assert mismatch.status_code == 400
    assert "access_token" not in mismatch.text

    ok = httpx.post(
        url, data={"grant_type": "client_credentials", "client_secret": mine["capability_token"]}
    )
    assert ok.status_code == 200
    assert ok.json()["space_id"] == mine["space_id"]


def _authorize(server: str, **extra: str) -> httpx.Response:
    data = {
        "client_id": "loci_client_test",
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "state": "st",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "subject": "tech@example.com",
        **extra,
    }
    return httpx.post(f"{server}/oauth/authorize", data=data, follow_redirects=False)


def test_contract_authorize_page_does_not_list_tenants(live_server: str) -> None:
    sp = _new_space(live_server, "Hidden Tenant Name")
    page = httpx.get(
        f"{live_server}/oauth/authorize",
        params={
            "client_id": "c",
            "redirect_uri": CALLBACK,
            "state": "s",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
    )
    assert page.status_code == 200
    assert "Hidden Tenant Name" not in page.text
    assert sp["space_id"] not in page.text
    assert "claude.ai" in page.text  # the consent page names where the code goes


def test_contract_authorize_requires_the_space_token(live_server: str) -> None:
    sp = _new_space(live_server, "Authz Target")

    by_id_only = _authorize(live_server, space_id=sp["space_id"])
    assert by_id_only.status_code not in (301, 302, 303, 307)
    assert "code=" not in by_id_only.headers.get("location", "")

    wrong = _authorize(live_server, space_token="nope")
    assert wrong.status_code == 401
    assert "code=" not in wrong.headers.get("location", "")

    ok = _authorize(live_server, space_token=sp["capability_token"])
    assert ok.status_code in (302, 303)
    code = ok.headers["location"].split("code=", 1)[1].split("&", 1)[0]
    tok = httpx.post(
        f"{live_server}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "loci_client_test",
            "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER,
        },
    )
    assert tok.status_code == 200
    assert tok.json()["space_id"] == sp["space_id"]


@pytest.mark.parametrize(
    "redirect_uri",
    ["javascript:alert(1)", "http://evil.example/cb", "", "data:text/html,x", "https://"],
)
def test_contract_authorize_rejects_unsafe_redirect_uri(live_server: str, redirect_uri: str) -> None:
    sp = _new_space(live_server, f"Redirect {abs(hash(redirect_uri))}")
    resp = _authorize(live_server, space_token=sp["capability_token"], redirect_uri=redirect_uri)
    assert resp.status_code == 400
    assert "location" not in resp.headers


# ---------------------------------------------------------------- tools stay inside the bound space


def _mcp_call(
    url: str, tool: str, args: dict[str, Any], bearer: str = ""
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    resp = httpx.post(
        url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert not result.get("isError"), result
    structured = result.get("structuredContent")
    body: dict[str, Any] = (
        structured if isinstance(structured, dict) else json.loads(result["content"][0]["text"])
    )
    return body


def test_contract_tenant_cannot_reach_another_space_via_space_id(live_server: str) -> None:
    attacker = _new_space(live_server, "Tenant Attacker")
    victim = _new_space(live_server, "Tenant Victim")
    url = f"{live_server}/loci-{attacker['capability_token']}/mcp"

    for tool, args in (
        ("ask", {"visible_tag_code": "K9-4B"}),
        (
            "observe",
            {
                "place_label": "Closet",
                "canonical_class": "valve",
                "material": "metal_brass_or_bronze",
                "mounting": "wall_mounted",
                "visible_verbatim_text": "",
                "description": "probe",
            },
        ),
        (
            "commit",
            {
                "object_id": "obj_probe",
                "title": "probe",
                "claims": [{"text": "probe", "confidence": "high"}],
                "save": True,
            },
        ),
    ):
        out = _mcp_call(url, tool, {**args, "space_id": victim["space_id"]})
        assert out.get("error") == "space_forbidden", (tool, out)

    own = _mcp_call(url, "ask", {"visible_tag_code": "K9-4B", "space_id": attacker["space_id"]})
    assert own.get("error") != "space_forbidden"

    listed = _mcp_call(url, "list_spaces", {})
    ids = {s["id"] for s in listed["spaces"]}
    assert ids == {attacker["space_id"]}
    assert "Tenant Victim" not in json.dumps(listed)


def test_contract_bearer_tenant_cannot_reach_another_space(live_server: str) -> None:
    mine = _new_space(live_server, "Bearer Mine")
    other = _new_space(live_server, "Bearer Other")
    tok = httpx.post(
        f"{live_server}/oauth/token",
        data={"grant_type": "client_credentials", "client_secret": mine["capability_token"]},
    ).json()["access_token"]
    out = _mcp_call(
        f"{live_server}/mcp", "ask", {"visible_tag_code": "K9-4B", "space_id": other["space_id"]}, bearer=tok
    )
    assert out.get("error") == "space_forbidden"


def test_operator_root_token_still_spans_spaces(live_server: str) -> None:
    sp = _new_space(live_server, "Operator Visible")
    listed = _mcp_call(f"{live_server}/loci-root-token/mcp", "list_spaces", {})
    assert sp["space_id"] in {s["id"] for s in listed["spaces"]}


@pytest.mark.parametrize("token", ["\u00e9", "caf\u00e9-token", "\u2603"])
def test_contract_non_ascii_tokens_are_rejected_not_crashed(live_server: str, token: str) -> None:
    resp = httpx.post(
        f"{live_server}/oauth/token",
        data={"grant_type": "client_credentials", "client_secret": token},
    )
    assert resp.status_code == 401
    state = httpx.get(f"{live_server}/loci-{token}/api/state")
    assert state.status_code == 404
    authz = _authorize(live_server, space_token=token)
    assert authz.status_code == 401


# ---------------------------------------------------------------- codes and access tokens are not interchangeable


def _code_for(server: str, space_token: str) -> str:
    resp = _authorize(server, space_token=space_token)
    assert resp.status_code in (302, 303), resp.text
    return resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]


def test_contract_authorization_code_is_not_an_access_token(live_server: str) -> None:
    sp = _new_space(live_server, "Code Not Token")
    code = _code_for(live_server, sp["capability_token"])
    relabelled = "loci_at_" + code[len("loci_ac_") :]
    listed = httpx.get(f"{live_server}/api/spaces", headers={"Authorization": f"Bearer {relabelled}"})
    assert listed.status_code == 401
    mcp = httpx.post(
        f"{live_server}/mcp",
        headers={
            "Authorization": f"Bearer {relabelled}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_spaces", "arguments": {}}},
    )
    assert mcp.status_code == 401


def test_contract_access_token_is_not_an_authorization_code(live_server: str) -> None:
    sp = _new_space(live_server, "Token Not Code")
    issued = httpx.post(
        f"{live_server}/oauth/token",
        data={"grant_type": "client_credentials", "client_secret": sp["capability_token"]},
    )
    assert issued.status_code == 200, issued.text
    relabelled = "loci_ac_" + issued.json()["access_token"][len("loci_at_") :]
    swapped = httpx.post(
        f"{live_server}/oauth/token",
        data={"grant_type": "authorization_code", "code": relabelled, "code_verifier": "anything"},
    )
    assert swapped.status_code == 400
    assert "access_token" not in swapped.text


def test_access_tokens_minted_before_the_type_claim_still_verify(no_env_secret: Path) -> None:
    import auth

    payload = {"space_id": "sp_x", "sub": "u", "role": "writer", "exp": int(time.time()) + 600, "jti": "abc"}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(auth._signing_key(), raw, hashlib.sha256).digest()
    claims = auth.verify_access_token(f"loci_at_{_b64u(raw)}.{_b64u(sig)}")
    assert claims is not None and claims.space_id == "sp_x"
