"""OAuth 2.1 Authorization Server (PKCE S256 + Dynamic Client Registration) and Space Token Auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _signing_key() -> bytes:
    secret = (
        os.environ.get("LOCI_AUTH_SECRET")
        or os.environ.get("LOCI_PATH_TOKEN")
        or "loci-dev-signing-secret"
    )
    return hashlib.sha256(f"loci-oauth21:{secret}".encode("utf-8")).digest()


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + pad)


@dataclass(frozen=True)
class AuthClaims:
    space_id: str
    subject: str
    role: str
    exp: int


def mint_access_token(
    *,
    space_id: str,
    subject: str = "oauth-user",
    role: str = "writer",
    ttl_seconds: int = 86400 * 30,
) -> str:
    payload = {
        "space_id": space_id,
        "sub": subject,
        "role": role,
        "exp": int(time.time()) + ttl_seconds,
        "jti": secrets.token_hex(6),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
    return f"loci_at_{_b64u_encode(raw)}.{_b64u_encode(sig)}"


def verify_access_token(token: str) -> AuthClaims | None:
    if not token.startswith("loci_at_") or "." not in token:
        return None
    body_part, sig_part = token[len("loci_at_") :].split(".", 1)
    try:
        raw = _b64u_decode(body_part)
        sig = _b64u_decode(sig_part)
        expected = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        exp = int(data.get("exp", 0))
        if exp < int(time.time()):
            return None
        return AuthClaims(
            space_id=str(data["space_id"]),
            subject=str(data.get("sub", "oauth-user")),
            role=str(data.get("role", "writer")),
            exp=exp,
        )
    except Exception:  # noqa: BLE001
        return None


def mint_authorization_code(
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    space_id: str,
    subject: str,
    ttl_seconds: int = 600,
) -> str:
    payload = {
        "cid": client_id,
        "uri": redirect_uri,
        "cc": code_challenge,
        "ccm": code_challenge_method,
        "space_id": space_id,
        "sub": subject,
        "exp": int(time.time()) + ttl_seconds,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
    return f"loci_ac_{_b64u_encode(raw)}.{_b64u_encode(sig)}"


def exchange_authorization_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str = "",
    redirect_uri: str = "",
) -> dict[str, Any] | None:
    if not code.startswith("loci_ac_") or "." not in code:
        return None
    body_part, sig_part = code[len("loci_ac_") :].split(".", 1)
    try:
        raw = _b64u_decode(body_part)
        sig = _b64u_decode(sig_part)
        expected = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        if client_id and data.get("cid") and str(data["cid"]) != client_id:
            return None
        if redirect_uri and data.get("uri") and str(data["uri"]) != redirect_uri:
            return None
        cc = str(data.get("cc", ""))
        ccm = str(data.get("ccm", "S256")).upper()
        if cc:
            if ccm == "S256":
                computed = _b64u_encode(
                    hashlib.sha256(code_verifier.encode("ascii")).digest()
                )
            else:
                computed = code_verifier
            if not hmac.compare_digest(computed, cc):
                return None
        space_id = str(data["space_id"])
        subject = str(data.get("sub", "oauth-user"))
        access_token = mint_access_token(
            space_id=space_id, subject=subject, role="writer"
        )
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 86400 * 30,
            "scope": "mcp:tools loci:read loci:write",
            "space_id": space_id,
            "subject": subject,
        }
    except Exception:  # noqa: BLE001
        return None


def oauth_server_metadata(base_url: str) -> dict[str, Any]:
    root = base_url.rstrip("/")
    return {
        "issuer": root,
        "authorization_endpoint": f"{root}/oauth/authorize",
        "token_endpoint": f"{root}/oauth/token",
        "registration_endpoint": f"{root}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": ["mcp:tools", "loci:read", "loci:write"],
    }


def oauth_protected_resource_metadata(base_url: str) -> dict[str, Any]:
    root = base_url.rstrip("/")
    return {
        "resource": f"{root}/mcp",
        "authorization_servers": [root],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp:tools", "loci:read", "loci:write"],
    }
