"""OAuth 2.1 Authorization Server (PKCE S256 + Dynamic Client Registration) and Space Token Auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_DEFAULT_SECRET_FILE = Path(__file__).parent / ".loci-auth-secret"
_MIN_SECRET_LEN = 32


def _env(name: str) -> str | None:
    """An env var's value, with blank or whitespace-only treated as unset."""
    value = os.environ.get(name, "").strip()
    return value or None


def _secret_file() -> Path:
    return Path(_env("LOCI_AUTH_SECRET_FILE") or _DEFAULT_SECRET_FILE)


def _signing_secret() -> str:
    """The HMAC secret behind OAuth codes and access tokens.

    On Cloud Run (K_SERVICE set) only LOCI_AUTH_SECRET counts. Local runs use
    LOCI_AUTH_SECRET, then LOCI_PATH_TOKEN, then a random per-install secret
    persisted 0600 next to the code. There is no built-in constant: a known
    default would let anyone mint a token for any space.
    """
    if _env("K_SERVICE"):
        return _cloud_run_signing_secret()
    if secret := _env("LOCI_AUTH_SECRET") or _env("LOCI_PATH_TOKEN"):
        return secret
    return _local_file_secret()


_CLOUD_RUN_SECRET_HINT = (
    "Set LOCI_AUTH_SECRET on Cloud Run from the loci-auth-secret Secret Manager secret"
)


def _cloud_run_signing_secret() -> str:
    """LOCI_AUTH_SECRET only: the root URL token must never be the token-minting key.

    Errors name the env var and the Secret Manager secret, never either value.
    """
    secret = _env("LOCI_AUTH_SECRET")
    if secret is None:
        raise RuntimeError(f"{_CLOUD_RUN_SECRET_HINT}: it is unset or blank.")
    if len(secret) < _MIN_SECRET_LEN:
        raise RuntimeError(
            f"{_CLOUD_RUN_SECRET_HINT}: it is shorter than {_MIN_SECRET_LEN} chars."
        )
    path_token = _env("LOCI_PATH_TOKEN") or ""
    if hmac.compare_digest(secret.encode("utf-8"), path_token.encode("utf-8")):
        raise RuntimeError(
            f"{_CLOUD_RUN_SECRET_HINT}: it must differ from LOCI_PATH_TOKEN."
        )
    return secret


def _local_file_secret() -> str:
    """A random per-install secret, created once and persisted 0600."""
    path = _secret_file()
    if not path.exists():
        # Write a private temp file, then hard-link it into place: the link is atomic
        # and fails if another process won the race, so no reader sees an empty file.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(48))
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        finally:
            tmp.unlink()
    secret = path.read_text().strip()
    if len(secret) < _MIN_SECRET_LEN:
        raise RuntimeError(
            f"{path} holds a signing secret shorter than {_MIN_SECRET_LEN} chars"
        )
    return secret


def assert_signing_configured() -> None:
    """Fail fast at startup instead of on the first OAuth request."""
    _signing_secret()


def _signing_key() -> bytes:
    return hashlib.sha256(f"loci-oauth21:{_signing_secret()}".encode("utf-8")).digest()


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
        "typ": "at",
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
        # Codes and tokens share a signing key and the prefix is unsigned: check the signed type.
        if data.get("typ", "at") != "at" or "cc" in data:
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


# OAuth 2.1 makes PKCE mandatory and this server accepts only S256: a challenge is the
# unpadded base64url SHA-256 (43 chars), a verifier is RFC 7636's 43-128 unreserved chars.
_S256_CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_PKCE_VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}")


def valid_s256_challenge(code_challenge: str, code_challenge_method: str) -> bool:
    """True only for an S256 method and a well-formed S256 code_challenge."""
    return code_challenge_method == "S256" and bool(
        _S256_CHALLENGE_RE.fullmatch(code_challenge)
    )


def _pkce_verifier_matches(data: dict[str, Any], code_verifier: str) -> bool:
    """A code exchanges only with an S256 challenge and the verifier that hashes to it."""
    cc = data.get("cc")
    if not isinstance(cc, str) or not valid_s256_challenge(
        cc, str(data.get("ccm", ""))
    ):
        return False
    if not _PKCE_VERIFIER_RE.fullmatch(code_verifier):
        return False
    computed = _b64u_encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
    return hmac.compare_digest(computed, cc)


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
        "typ": "ac",
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
        if data.get("typ", "ac") != "ac" or "cc" not in data:
            return None
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        # Both are required and must be exactly what the code was minted for.
        if not client_id or data.get("cid") != client_id:
            return None
        if not redirect_uri or data.get("uri") != redirect_uri:
            return None
        if not _pkce_verifier_matches(data, code_verifier):
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


_CLIENT_ID_PREFIX = "loci_client_"


def _dcr_key() -> bytes:
    """Its own domain prefix: a client_id never verifies as a code or token, nor they as it."""
    return hashlib.sha256(f"loci-dcr:{_signing_secret()}".encode("utf-8")).digest()


@dataclass(frozen=True)
class RegisteredClient:
    redirect_uris: list[str]
    name: str


def mint_client_id(redirect_uris: list[str], client_name: str = "") -> str:
    """A stateless client_id that carries its own signed registration (no DB row)."""
    payload = {
        "typ": "ci",
        "ru": list(redirect_uris),
        "name": client_name,
        "iat": int(time.time()),
        "jti": secrets.token_hex(
            6
        ),  # two identical registrations still get distinct ids
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_dcr_key(), raw, hashlib.sha256).digest()
    return f"{_CLIENT_ID_PREFIX}{_b64u_encode(raw)}.{_b64u_encode(sig)}"


def _signed_client_payload(client_id: str) -> Any:
    """The decoded payload of a correctly signed client_id, else None."""
    if not client_id.startswith(_CLIENT_ID_PREFIX) or "." not in client_id:
        return None
    body_part, sig_part = client_id[len(_CLIENT_ID_PREFIX) :].split(".", 1)
    try:
        raw = _b64u_decode(body_part)
        sig = _b64u_decode(sig_part)
        expected = hmac.new(_dcr_key(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def registered_client(client_id: str) -> RegisteredClient | None:
    """The registration signed into client_id, or None if forged, mistyped or malformed."""
    data = _signed_client_payload(client_id)
    if not isinstance(data, dict) or data.get("typ") != "ci":
        return None
    uris = data.get("ru")
    if not isinstance(uris, list) or not all(isinstance(u, str) for u in uris):
        return None
    name = data.get("name")
    return RegisteredClient(
        redirect_uris=uris, name=name if isinstance(name, str) else ""
    )


def verify_client_id(client_id: str) -> list[str] | None:
    """The redirect_uris registered for client_id, or None if it does not verify."""
    client = registered_client(client_id)
    return client.redirect_uris if client is not None else None


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
        "code_challenge_methods_supported": ["S256"],
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
