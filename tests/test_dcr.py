"""[contract] Dynamic Client Registration binds redirect_uris to the issued client_id.

DCR stays stateless: /oauth/register validates the redirect_uris and signs them into the
client_id, /oauth/authorize only sends a code to a redirect_uri registered for that
client_id, and /oauth/token only exchanges a code for the client_id and redirect_uri it
was minted with. Bare pre-signature client_ids keep working only for the known
connector callbacks (a transition path).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
EVIL = "https://evil.example/cb"
LEGACY_CLIENT_ID = "loci_client_0123456789abcdef"
SERVER_SECRET = "root-token"  # the live_server signs with its LOCI_PATH_TOKEN
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode("ascii")).digest())
    .decode("ascii")
    .rstrip("=")
)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * ((4 - len(text) % 4) % 4))


def _new_space(server: str, name: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{server}/api/spaces",
        json={"name": name},
        headers={"Authorization": "Bearer root-token"},
    )
    assert resp.status_code == 201
    body: dict[str, Any] = resp.json()
    return body


def _register(
    server: str, *redirect_uris: str, client_name: str = "Test Connector"
) -> str:
    resp = httpx.post(
        f"{server}/oauth/register",
        json={"client_name": client_name, "redirect_uris": list(redirect_uris)},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["redirect_uris"] == list(redirect_uris)
    client_id: str = resp.json()["client_id"]
    return client_id


def _params(
    client_id: str, redirect_uri: str = CALLBACK, **extra: str
) -> dict[str, str]:
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": "st",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        **extra,
    }


def _authorize_post(
    server: str, client_id: str, redirect_uri: str, space_token: str
) -> httpx.Response:
    return httpx.post(
        f"{server}/oauth/authorize",
        data=_params(
            client_id, redirect_uri, space_token=space_token, subject="tech@example.com"
        ),
        follow_redirects=False,
    )


def _assert_refused(
    server: str, client_id: str, redirect_uri: str, space_token: str
) -> None:
    """GET shows no consent form and POST mints no code: both are a plain HTML 400."""
    page = httpx.get(
        f"{server}/oauth/authorize", params=_params(client_id, redirect_uri)
    )
    assert page.status_code == 400, page.text
    assert "<form" not in page.text

    minted = _authorize_post(server, client_id, redirect_uri, space_token)
    assert minted.status_code == 400, minted.text
    assert "location" not in minted.headers
    assert "code=" not in minted.text


def _code(server: str, client_id: str, redirect_uri: str, space_token: str) -> str:
    minted = _authorize_post(server, client_id, redirect_uri, space_token)
    assert minted.status_code == 302, minted.text
    location = minted.headers["location"]
    assert location.startswith(redirect_uri + "?")
    return location.split("code=", 1)[1].split("&", 1)[0]


def _exchange(server: str, code: str, **fields: str | None) -> httpx.Response:
    data: dict[str, str | None] = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": VERIFIER,
        **fields,
    }
    return httpx.post(
        f"{server}/oauth/token", data={k: v for k, v in data.items() if v is not None}
    )


def _assert_invalid_grant(resp: httpx.Response) -> None:
    assert resp.status_code == 400, (
        resp.status_code
    )  # never print a body that may hold a token
    assert resp.json()["error"] == "invalid_grant"
    assert "access_token" not in resp.text


# ---------------------------------------------------------------- authorize honours the registration


def test_contract_authorize_rejects_unregistered_redirect(live_server: str) -> None:
    sp = _new_space(live_server, "DCR Unregistered Redirect")
    client_id = _register(live_server, CALLBACK)

    _assert_refused(live_server, client_id, EVIL, sp["capability_token"])
    # Exact string match: a sibling path or an extra query on the registered host is not it.
    _assert_refused(live_server, client_id, CALLBACK + "/x", sp["capability_token"])
    _assert_refused(
        live_server, client_id, CALLBACK + "?next=1", sp["capability_token"]
    )

    page = httpx.get(f"{live_server}/oauth/authorize", params=_params(client_id))
    assert page.status_code == 200
    assert "<form" in page.text
    assert _code(live_server, client_id, CALLBACK, sp["capability_token"]).startswith(
        "loci_ac_"
    )


def test_consent_page_shows_the_escaped_client_name_beside_the_host(
    live_server: str,
) -> None:
    client_id = _register(live_server, CALLBACK, client_name="<b>Acme</b> Connector")
    page = httpx.get(f"{live_server}/oauth/authorize", params=_params(client_id))
    assert page.status_code == 200
    assert "&lt;b&gt;Acme&lt;/b&gt; Connector" in page.text
    assert "<b>Acme</b>" not in page.text
    assert "claude.ai" in page.text


# ---------------------------------------------------------------- register validates its input


_LONG_URI = "https://claude.ai/" + "a" * (600 - len("https://claude.ai/"))

BAD_REGISTRATIONS: dict[str, tuple[Any, str]] = {
    "no_redirect_uris": ({"client_name": "x"}, "invalid_redirect_uri"),
    "empty_list": ({"redirect_uris": []}, "invalid_redirect_uri"),
    "non_list": ({"redirect_uris": CALLBACK}, "invalid_redirect_uri"),
    "null_list": ({"redirect_uris": None}, "invalid_redirect_uri"),
    "six_uris": (
        {"redirect_uris": [f"{CALLBACK}/{i}" for i in range(6)]},
        "invalid_redirect_uri",
    ),
    "http_non_loopback": (
        {"redirect_uris": ["http://evil.example/cb"]},
        "invalid_redirect_uri",
    ),
    "javascript_uri": (
        {"redirect_uris": ["javascript:alert(1)"]},
        "invalid_redirect_uri",
    ),
    "600_char_uri": ({"redirect_uris": [_LONG_URI]}, "invalid_redirect_uri"),
    "non_string_entry": ({"redirect_uris": [CALLBACK, 7]}, "invalid_redirect_uri"),
    "one_bad_among_good": (
        {"redirect_uris": [CALLBACK, "data:text/html,x"]},
        "invalid_redirect_uri",
    ),
    "malformed_json": ("{not json", "invalid_client_metadata"),
    "empty_body": ("", "invalid_client_metadata"),
    "array_body": (json.dumps([CALLBACK]), "invalid_client_metadata"),
    "non_utf8_body": (b"\xff\xfe{", "invalid_client_metadata"),
    "deeply_nested_body": ("[" * 100_000 + "]" * 100_000, "invalid_client_metadata"),
    "non_string_client_name": (
        {"redirect_uris": [CALLBACK], "client_name": ["x"]},
        "invalid_client_metadata",
    ),
}


@pytest.mark.parametrize(
    "body,error", list(BAD_REGISTRATIONS.values()), ids=list(BAD_REGISTRATIONS)
)
def test_contract_register_validates_redirect_uris(
    live_server: str, body: Any, error: str
) -> None:
    url = f"{live_server}/oauth/register"
    if isinstance(body, dict):
        resp = httpx.post(url, json=body)
    else:
        resp = httpx.post(
            url, content=body, headers={"Content-Type": "application/json"}
        )
    assert resp.status_code == 400, resp.text
    out = resp.json()
    assert out["error"] == error
    assert isinstance(out["error_description"], str) and out["error_description"]
    assert "client_id" not in out


def test_register_returns_the_validated_registration(live_server: str) -> None:
    uris = [
        CALLBACK,
        "http://127.0.0.1:33418/callback",
        "https://chatgpt.com/connector/cb",
    ]
    resp = httpx.post(
        f"{live_server}/oauth/register",
        json={
            "client_name": "N" * 250,
            "redirect_uris": uris,
            "extra": {"ignored": True},
        },
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["redirect_uris"] == uris
    assert out["client_name"] == "N" * 100
    assert out["client_id"].startswith("loci_client_")
    assert out["token_endpoint_auth_method"] == "none"

    sp = _new_space(live_server, "DCR Every Registered URI")
    for uri in uris:
        assert _code(live_server, out["client_id"], uri, sp["capability_token"])


def test_register_accepts_a_512_char_uri_and_a_missing_client_name(
    live_server: str,
) -> None:
    uri = "https://claude.ai/" + "a" * (512 - len("https://claude.ai/"))
    resp = httpx.post(f"{live_server}/oauth/register", json={"redirect_uris": [uri]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["redirect_uris"] == [uri]


# ---------------------------------------------------------------- the client_id cannot be forged


def _registered_payload(client_id: str) -> bytes:
    body = client_id[len("loci_client_") :].split(".", 1)[0]
    return _b64u_decode(body)


def _resign(client_id: str, key: bytes) -> str:
    raw = _registered_payload(client_id)
    return (
        f"loci_client_{_b64u(raw)}.{_b64u(hmac.new(key, raw, hashlib.sha256).digest())}"
    )


def _flip_signature_byte(client_id: str) -> str:
    body, sig = client_id[len("loci_client_") :].split(".", 1)
    raw_sig = bytearray(_b64u_decode(sig))
    raw_sig[0] ^= 0x01
    return f"loci_client_{body}.{_b64u(bytes(raw_sig))}"


def test_contract_client_id_is_not_forgeable(live_server: str) -> None:
    sp = _new_space(live_server, "DCR Forgery")
    token = sp["capability_token"]
    client_id = _register(live_server, CALLBACK)
    assert _code(live_server, client_id, CALLBACK, token)  # the genuine one works

    access_key = hashlib.sha256(
        f"loci-oauth21:{SERVER_SECRET}".encode("utf-8")
    ).digest()
    access_token = httpx.post(
        f"{live_server}/oauth/token",
        data={"grant_type": "client_credentials", "client_secret": token},
    ).json()["access_token"]
    auth_code = _code(live_server, client_id, CALLBACK, token)

    forged = {
        "flipped_signature_byte": _flip_signature_byte(client_id),
        "resigned_with_access_token_key": _resign(client_id, access_key),
        "unsigned_payload": client_id.split(".", 1)[0],
        "access_token": access_token,
        "access_token_relabelled": "loci_client_" + access_token[len("loci_at_") :],
        "authorization_code": auth_code,
        "authorization_code_relabelled": "loci_client_" + auth_code[len("loci_ac_") :],
        "empty": "",
        "bare_prefix": "loci_client_",
        "not_base64": "loci_client_%%%.%%%",
        "legacy_too_short": "loci_client_0123456789abcde",
        "legacy_uppercase": "loci_client_0123456789ABCDEF",
        "legacy_with_signature": LEGACY_CLIENT_ID + ".AAAA",
        "non_ascii": "loci_client_éé.☃",
        "oversized": "loci_client_" + "A" * 8000 + ".AAAA",
    }
    for name, bad in forged.items():
        try:
            _assert_refused(live_server, bad, CALLBACK, token)
        except AssertionError as exc:
            raise AssertionError(f"{name} was not refused") from exc


@pytest.fixture
def server_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mint and verify in-process with the same secret the live_server subprocess uses."""
    for key in ("LOCI_AUTH_SECRET", "K_SERVICE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCI_PATH_TOKEN", SERVER_SECRET)


def _signed_client_id(payload: Any) -> str:
    import auth

    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(auth._dcr_key(), raw, hashlib.sha256).digest()
    return f"loci_client_{_b64u(raw)}.{_b64u(sig)}"


MALFORMED_REGISTRATIONS: dict[str, Any] = {
    "not_an_object": [CALLBACK],
    "wrong_typ": {"typ": "at", "ru": [CALLBACK], "name": "x", "iat": 0},
    "missing_typ": {"ru": [CALLBACK], "name": "x", "iat": 0},
    "ru_not_a_list": {"typ": "ci", "ru": CALLBACK, "name": "x", "iat": 0},
    "ru_missing": {"typ": "ci", "name": "x", "iat": 0},
    "ru_non_string_entry": {"typ": "ci", "ru": [CALLBACK, 7], "name": "x", "iat": 0},
}


@pytest.mark.parametrize(
    "payload", list(MALFORMED_REGISTRATIONS.values()), ids=list(MALFORMED_REGISTRATIONS)
)
def test_contract_verify_client_id_rejects_a_validly_signed_malformed_payload(
    server_signing_env: None, payload: Any
) -> None:
    from auth import verify_client_id

    assert verify_client_id(_signed_client_id(payload)) is None


def test_client_id_round_trips_and_is_not_a_code_or_token(
    server_signing_env: None,
) -> None:
    from auth import (
        exchange_authorization_code,
        mint_client_id,
        verify_access_token,
        verify_client_id,
    )

    client_id = mint_client_id([CALLBACK], "Round Trip")
    assert verify_client_id(client_id) == [CALLBACK]
    body = client_id[len("loci_client_") :]
    assert verify_access_token("loci_at_" + body) is None
    assert (
        exchange_authorization_code(
            code="loci_ac_" + body,
            code_verifier=VERIFIER,
            client_id=client_id,
            redirect_uri=CALLBACK,
        )
        is None
    )
    data = json.loads(_registered_payload(client_id))
    assert data["typ"] == "ci" and data["name"] == "Round Trip"
    assert abs(int(data["iat"]) - time.time()) < 60


# ---------------------------------------------------------------- the token endpoint checks the binding


def test_contract_token_requires_matching_client_and_redirect(live_server: str) -> None:
    second = "https://claude.ai/api/mcp/auth_callback2"
    sp = _new_space(live_server, "DCR Token Binding")
    client_id = _register(live_server, CALLBACK, second)
    other_client = _register(live_server, CALLBACK, second)
    assert (
        other_client != client_id
    )  # an identical registration is still a distinct client
    code = _code(live_server, client_id, CALLBACK, sp["capability_token"])

    mismatches: dict[str, dict[str, str | None]] = {
        "missing_client_id": {"client_id": None, "redirect_uri": CALLBACK},
        "empty_client_id": {"client_id": "", "redirect_uri": CALLBACK},
        "different_client_id": {"client_id": other_client, "redirect_uri": CALLBACK},
        "missing_redirect_uri": {"client_id": client_id, "redirect_uri": None},
        "empty_redirect_uri": {"client_id": client_id, "redirect_uri": ""},
        "different_redirect_uri": {"client_id": client_id, "redirect_uri": second},
        "both_missing": {"client_id": None, "redirect_uri": None},
    }
    for name, fields in mismatches.items():
        try:
            _assert_invalid_grant(_exchange(live_server, code, **fields))
        except AssertionError as exc:
            raise AssertionError(f"{name} exchanged") from exc

    # Codes are stateless, so the same code still exchanges with the right binding.
    ok = _exchange(live_server, code, client_id=client_id, redirect_uri=CALLBACK)
    assert ok.status_code == 200, ok.text
    assert ok.json()["space_id"] == sp["space_id"]


# ---------------------------------------------------------------- transition for pre-signature client_ids


def test_legacy_client_id_is_accepted_only_for_known_connector_callbacks(
    live_server: str,
) -> None:
    sp = _new_space(live_server, "DCR Legacy")
    token = sp["capability_token"]

    page = httpx.get(f"{live_server}/oauth/authorize", params=_params(LEGACY_CLIENT_ID))
    assert page.status_code == 200
    assert "<form" in page.text
    code = _code(live_server, LEGACY_CLIENT_ID, CALLBACK, token)
    ok = _exchange(live_server, code, client_id=LEGACY_CLIENT_ID, redirect_uri=CALLBACK)
    assert ok.status_code == 200, ok.text

    for uri in (
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "http://localhost:6274/cb",
    ):
        assert _code(live_server, LEGACY_CLIENT_ID, uri, token)

    for uri in (
        EVIL,
        "https://claude.ai.evil.example/api/mcp/auth_callback",
        "https://evil.claude.ai/api/mcp/auth_callback",
        "http://claude.ai/api/mcp/auth_callback",
    ):
        _assert_refused(live_server, LEGACY_CLIENT_ID, uri, token)


# ---------------------------------------------------------------- in-process: the same code the live server runs


def _register_in_process(body: Any) -> httpx.Response:
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    import server

    app = Starlette(
        routes=[Route("/oauth/register", server.oauth_register_route, methods=["POST"])]
    )
    with TestClient(app) as client:
        if isinstance(body, dict):
            return client.post("/oauth/register", json=body)
        return client.post(
            "/oauth/register",
            content=body,
            headers={"Content-Type": "application/json"},
        )


@pytest.mark.parametrize(
    "body,error", list(BAD_REGISTRATIONS.values()), ids=list(BAD_REGISTRATIONS)
)
def test_register_route_rejects_bad_metadata_in_process(
    server_signing_env: None, body: Any, error: str
) -> None:
    resp = _register_in_process(body)
    assert resp.status_code == 400
    assert resp.json()["error"] == error


def test_register_route_signs_the_registration_in_process(
    server_signing_env: None,
) -> None:
    from auth import registered_client

    resp = _register_in_process({"redirect_uris": [CALLBACK], "client_name": None})
    assert resp.status_code == 201
    out = resp.json()
    assert out["client_name"] == "MCP Connector"
    client = registered_client(out["client_id"])
    assert client is not None
    assert client.redirect_uris == [CALLBACK] and client.name == "MCP Connector"


def test_signed_non_json_client_id_is_rejected(server_signing_env: None) -> None:
    import auth

    raw = b"\xff not json"
    sig = hmac.new(auth._dcr_key(), raw, hashlib.sha256).digest()
    assert auth.verify_client_id(f"loci_client_{_b64u(raw)}.{_b64u(sig)}") is None
    assert auth.verify_client_id("loci_client_" + "A" * 43) is None  # no signature part


def test_redirect_binding_helpers_in_process(server_signing_env: None) -> None:
    import server
    from auth import mint_client_id

    client_id = mint_client_id([CALLBACK], "<i>X</i>")
    assert server._client_redirect_allowed(client_id, CALLBACK)
    assert not server._client_redirect_allowed(client_id, EVIL)
    assert not server._client_redirect_allowed("loci_client_nope", CALLBACK)

    assert server._client_redirect_allowed(LEGACY_CLIENT_ID, CALLBACK)
    assert server._client_redirect_allowed(
        LEGACY_CLIENT_ID, "https://chat.openai.com/aip/cb"
    )
    assert server._client_redirect_allowed(LEGACY_CLIENT_ID, "http://127.0.0.1:9/cb")
    for uri in (EVIL, "http://claude.ai/cb", "javascript:alert(1)", "https://[::1", ""):
        assert not server._client_redirect_allowed(LEGACY_CLIENT_ID, uri), uri

    assert (
        server._authorize_precondition_error(client_id, CALLBACK, CHALLENGE, "S256")
        is None
    )
    unregistered = server._authorize_precondition_error(
        client_id, EVIL, CHALLENGE, "S256"
    )
    assert unregistered is not None and unregistered.status_code == 400
    no_pkce = server._authorize_precondition_error(client_id, CALLBACK, "", "S256")
    assert no_pkce is not None and b"S256 PKCE is required" in no_pkce.body

    assert server._consent_destination(client_id, CALLBACK) == (
        "<strong>&lt;i&gt;X&lt;/i&gt;</strong> at <strong>claude.ai</strong>"
    )
    unnamed = mint_client_id([CALLBACK], "")
    assert (
        server._consent_destination(unnamed, CALLBACK) == "<strong>claude.ai</strong>"
    )
    assert (
        server._consent_destination(LEGACY_CLIENT_ID, CALLBACK)
        == "<strong>claude.ai</strong>"
    )
