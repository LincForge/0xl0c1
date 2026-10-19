"""[contract] PKCE is mandatory and S256-only (OAuth 2.1).

The authorize endpoint refuses a missing, non-S256 or malformed code_challenge, a code
without an S256 challenge never exchanges, and a hostile code_verifier is a clean
invalid_grant, never a 500.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
CLIENT_ID = "loci_client_pkce"  # in-process only; the live server needs a registered one
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode("ascii")).digest())
    .decode("ascii")
    .rstrip("=")
)


def _new_space(server: str, name: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{server}/api/spaces", json={"name": name}, headers={"Authorization": "Bearer root-token"}
    )
    assert resp.status_code == 201
    body: dict[str, Any] = resp.json()
    return body


@functools.cache
def _client_id(server: str) -> str:
    """A client registered for CALLBACK, so authorize refuses only for the PKCE reason."""
    resp = httpx.post(f"{server}/oauth/register", json={"redirect_uris": [CALLBACK]})
    assert resp.status_code == 201, resp.text
    client_id: str = resp.json()["client_id"]
    return client_id


def _params(server: str, **overrides: str | None) -> dict[str, str]:
    params: dict[str, str | None] = {
        "client_id": _client_id(server),
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "state": "st",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        **overrides,
    }
    return {k: v for k, v in params.items() if v is not None}


def _token(server: str, code: str, verifier: str | None) -> httpx.Response:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": _client_id(server),
        "redirect_uri": CALLBACK,
    }
    if verifier is not None:
        data["code_verifier"] = verifier
    return httpx.post(f"{server}/oauth/token", data=data)


def _assert_invalid_grant(resp: httpx.Response) -> None:
    assert resp.status_code == 400, resp.status_code
    assert resp.json()["error"] == "invalid_grant"
    assert "access_token" not in resp.text


BAD_CHALLENGES = {
    "no_challenge": {"code_challenge": None},
    "empty_challenge": {"code_challenge": ""},
    "plain_method": {"code_challenge": VERIFIER, "code_challenge_method": "plain"},
    "missing_method": {"code_challenge_method": None},
    "empty_method": {"code_challenge_method": ""},
    "lowercase_method": {"code_challenge_method": "s256"},
    "unknown_method": {"code_challenge_method": "S512"},
    "42_char_challenge": {"code_challenge": CHALLENGE[:42]},
    "44_char_challenge": {"code_challenge": CHALLENGE + "A"},
    "padded_challenge": {"code_challenge": CHALLENGE[:42] + "="},
    "non_base64url_challenge": {"code_challenge": CHALLENGE[:42] + "+"},
    "non_ascii_challenge": {"code_challenge": CHALLENGE[:42] + "é"},
    "oversized_challenge": {"code_challenge": "A" * 4096},
}


@pytest.mark.parametrize("overrides", list(BAD_CHALLENGES.values()), ids=list(BAD_CHALLENGES))
def test_contract_authorize_requires_s256_challenge(
    live_server: str, overrides: dict[str, str | None]
) -> None:
    sp = _new_space(live_server, "PKCE Authorize")

    page = httpx.get(f"{live_server}/oauth/authorize", params=_params(live_server, **overrides))
    assert page.status_code == 400
    assert "<form" not in page.text
    assert "S256 PKCE is required" in page.text  # refused for PKCE, not the client binding

    minted = httpx.post(
        f"{live_server}/oauth/authorize",
        data=_params(live_server, **overrides, space_token=sp["capability_token"], subject="tech@example.com"),
        follow_redirects=False,
    )
    assert minted.status_code == 400
    assert "location" not in minted.headers
    assert "code=" not in minted.text
    assert "S256 PKCE is required" in minted.text


def test_authorize_accepts_a_well_formed_s256_challenge(live_server: str) -> None:
    sp = _new_space(live_server, "PKCE Happy")
    page = httpx.get(f"{live_server}/oauth/authorize", params=_params(live_server))
    assert page.status_code == 200
    assert "<form" in page.text

    minted = httpx.post(
        f"{live_server}/oauth/authorize",
        data=_params(live_server, space_token=sp["capability_token"], subject="tech@example.com"),
        follow_redirects=False,
    )
    assert minted.status_code == 302
    code = minted.headers["location"].split("code=", 1)[1].split("&", 1)[0]
    ok = _token(live_server, code, VERIFIER)
    assert ok.status_code == 200
    assert ok.json()["space_id"] == sp["space_id"]


@pytest.fixture
def server_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mint codes in-process with the same secret the live_server subprocess uses."""
    monkeypatch.setenv("LOCI_PATH_TOKEN", "root-token")


@pytest.mark.parametrize(
    ("challenge", "method"),
    [("", "S256"), ("", ""), (VERIFIER, "plain"), (CHALLENGE, "plain"), (CHALLENGE, "s256")],
    ids=["empty_s256", "empty_empty", "plain_verifier", "plain_challenge", "lowercase_s256"],
)
@pytest.mark.parametrize("verifier", [None, "", VERIFIER], ids=["no_verifier", "empty_verifier", "verifier"])
def test_contract_code_without_challenge_does_not_exchange(
    live_server: str,
    server_signing_env: None,
    challenge: str,
    method: str,
    verifier: str | None,
) -> None:
    from auth import mint_authorization_code

    sp = _new_space(live_server, "PKCE Direct Mint")
    code = mint_authorization_code(
        client_id=_client_id(live_server),
        redirect_uri=CALLBACK,
        code_challenge=challenge,
        code_challenge_method=method,
        space_id=sp["space_id"],
        subject="tech@example.com",
    )
    _assert_invalid_grant(_token(live_server, code, verifier))


def test_a_directly_minted_s256_code_still_exchanges(live_server: str, server_signing_env: None) -> None:
    """Guards the test above: its codes fail on PKCE, not on a signing-key mismatch."""
    from auth import mint_authorization_code

    sp = _new_space(live_server, "PKCE Direct Mint OK")
    code = mint_authorization_code(
        client_id=_client_id(live_server),
        redirect_uri=CALLBACK,
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        space_id=sp["space_id"],
        subject="tech@example.com",
    )
    assert _token(live_server, code, VERIFIER).status_code == 200


HOSTILE_VERIFIERS = {
    "empty": "",
    "oversized_200": "a" * 200,
    "non_ascii": VERIFIER[:42] + "é",
    "non_ascii_only": "☃" * 43,
    "too_short_42": VERIFIER[:42],
    "too_long_129": "a" * 129,
    "outside_charset": VERIFIER[:42] + "+",
    "the_challenge_itself": CHALLENGE,
}


@pytest.mark.parametrize("verifier", list(HOSTILE_VERIFIERS.values()), ids=list(HOSTILE_VERIFIERS))
def test_contract_hostile_verifier_is_invalid_grant(live_server: str, verifier: str) -> None:
    sp = _new_space(live_server, "PKCE Hostile Verifier")
    minted = httpx.post(
        f"{live_server}/oauth/authorize",
        data=_params(live_server, space_token=sp["capability_token"], subject="tech@example.com"),
        follow_redirects=False,
    )
    assert minted.status_code == 302
    code = minted.headers["location"].split("code=", 1)[1].split("&", 1)[0]
    _assert_invalid_grant(_token(live_server, code, verifier))


@pytest.mark.parametrize(
    "verifier",
    ["", "a" * 200, "é" * 43, VERIFIER[:42] + "+", VERIFIER[:42]],
    ids=["empty", "oversized", "non_ascii", "outside_charset", "too_short"],
)
def test_contract_exchange_rejects_hostile_verifier_without_raising(
    server_signing_env: None, verifier: str
) -> None:
    from auth import exchange_authorization_code, mint_authorization_code

    code = mint_authorization_code(
        client_id=CLIENT_ID,
        redirect_uri=CALLBACK,
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        space_id="sp_x",
        subject="u",
    )
    assert exchange_authorization_code(
        code=code, code_verifier=verifier, client_id=CLIENT_ID, redirect_uri=CALLBACK
    ) is None


def test_metadata_advertises_only_s256(live_server: str) -> None:
    for path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        meta = httpx.get(f"{live_server}{path}")
        assert meta.status_code == 200
        assert meta.json()["code_challenge_methods_supported"] == ["S256"]
