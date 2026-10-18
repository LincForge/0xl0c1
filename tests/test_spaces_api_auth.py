"""[contract] The spaces API and the landing page do not leak or create tenants without a credential.

GET /api/spaces lists every space only to the operator (root token); a tenant credential
sees its own space; no credential gets 401. POST /api/spaces (create) is operator-only.
Every test here fails on the pre-change code and passes after it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import pytest

ROOT_TOKEN = "root-token"  # LOCI_PATH_TOKEN in the live_server fixture
LEGACY_CONSTANT = "loci-dev-signing-secret"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(server: str, name: str) -> dict[str, Any]:
    resp = httpx.post(f"{server}/api/spaces", json={"name": name}, headers=_auth(ROOT_TOKEN))
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _all_names(server: str) -> set[str]:
    resp = httpx.get(f"{server}/api/spaces", headers=_auth(ROOT_TOKEN))
    assert resp.status_code == 200, resp.text
    return {str(s["name"]) for s in resp.json()["spaces"]}


def _forged_access_token(space_id: str) -> str:
    def b64u(data: bytes) -> str:
        import base64

        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    key = hashlib.sha256(f"loci-oauth21:{LEGACY_CONSTANT}".encode("utf-8")).digest()
    raw = json.dumps(
        {"space_id": space_id, "sub": "x", "role": "writer", "exp": int(time.time()) + 600, "jti": "0"},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"loci_at_{b64u(raw)}.{b64u(hmac.new(key, raw, hashlib.sha256).digest())}"


def test_contract_list_spaces_requires_a_credential(live_server: str) -> None:
    _create(live_server, "Secret Garage Alpha")
    resp = httpx.get(f"{live_server}/api/spaces")
    assert resp.status_code == 401
    assert "Secret Garage Alpha" not in resp.text


def test_contract_tenant_space_token_lists_only_its_own_space(live_server: str) -> None:
    mine = _create(live_server, "Mine Via Space Token")
    _create(live_server, "Someone Else Bravo")
    resp = httpx.get(f"{live_server}/api/spaces", headers=_auth(mine["capability_token"]))
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["spaces"]] == [mine["space_id"]]


def test_contract_tenant_oauth_token_lists_only_its_own_space(live_server: str) -> None:
    mine = _create(live_server, "Mine Via OAuth")
    _create(live_server, "Someone Else Charlie")
    tok = httpx.post(
        f"{live_server}/oauth/token",
        data={"grant_type": "client_credentials", "client_secret": mine["capability_token"]},
    )
    assert tok.status_code == 200, tok.text
    resp = httpx.get(f"{live_server}/api/spaces", headers=_auth(tok.json()["access_token"]))
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["spaces"]] == [mine["space_id"]]


def test_operator_root_token_lists_every_space(live_server: str) -> None:
    _create(live_server, "Operator Sees Delta")
    _create(live_server, "Operator Sees Echo")
    assert {"Operator Sees Delta", "Operator Sees Echo"} <= _all_names(live_server)


def test_contract_create_space_without_credential_is_refused(live_server: str) -> None:
    resp = httpx.post(f"{live_server}/api/spaces", json={"name": "Anon Signup Foxtrot"})
    assert resp.status_code == 401
    assert "capability_token" not in resp.text
    assert "Anon Signup Foxtrot" not in _all_names(live_server)


def test_contract_tenant_cannot_create_spaces(live_server: str) -> None:
    mine = _create(live_server, "Tenant Golf")
    resp = httpx.post(
        f"{live_server}/api/spaces",
        json={"name": "Tenant Spawned Hotel"},
        headers=_auth(mine["capability_token"]),
    )
    assert resp.status_code == 403
    assert "capability_token" not in resp.text
    assert "Tenant Spawned Hotel" not in _all_names(live_server)


def test_contract_landing_page_does_not_list_tenants(live_server: str) -> None:
    _create(live_server, "Private Workshop India")
    resp = httpx.get(f"{live_server}/")
    assert resp.status_code == 200
    assert "Private Workshop India" not in resp.text


@pytest.mark.parametrize(
    "header",
    [
        b"",
        b"Bearer",
        b"Basic " + ROOT_TOKEN.encode(),
        b"Bearer " + ROOT_TOKEN.encode() + b"x",
        b"Bearer sha256:" + hashlib.sha256(b"root-token").hexdigest().encode(),
        b"Bearer loci_at_\xc3\xa9.\xc3\xa9",
        b"Bearer \xc3\xa9\xc3\xa9\xc3\xa9",
    ],
    ids=["empty", "blank-bearer", "basic-scheme", "near-miss", "stored-digest", "non-ascii-at", "non-ascii"],
)
def test_contract_hostile_credentials_get_401_not_data_or_500(live_server: str, header: bytes) -> None:
    headers = [(b"Authorization", header)] if header else []
    got = httpx.get(f"{live_server}/api/spaces", headers=headers)
    assert got.status_code == 401
    made = httpx.post(f"{live_server}/api/spaces", json={"name": "Hostile Juliet"}, headers=headers)
    assert made.status_code == 401
    assert "Hostile Juliet" not in _all_names(live_server)


def test_contract_forged_legacy_signed_token_gets_401(live_server: str) -> None:
    resp = httpx.get(f"{live_server}/api/spaces", headers=_auth(_forged_access_token("default")))
    assert resp.status_code == 401
