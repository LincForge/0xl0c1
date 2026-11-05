"""Stripe Payment Link validation and checkout payload builder for the $29 tag pack."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

log = logging.getLogger("loci")

STRIPE_TAG_PACK_LINK_ENV = "LOCI_STRIPE_TAG_PACK_LINK"
BILLING_CONTACT_EMAIL = "faust@lincinnovations.com"

_STRIPE_LINK_RE = re.compile(r"^https://buy\.stripe\.com/(test_)?[A-Za-z0-9]{8,64}$")
_SPACE_ID_RE = re.compile(r"^(default|sp_[0-9a-f]{12})$")

PRICES: dict[str, dict[str, Any]] = {
    "free": {"amount_usd": 0, "label": "Maker / Home Free Tier"},
    "pro": {"amount_usd": 12, "label": "Pro Shop / Multi-Property ($12/mo)"},
    "team": {"amount_usd": 49, "label": "Team & Field-Service Handoff ($49/mo)"},
    "tags": {
        "amount_usd": 29,
        "label": "50x Weatherproof Visual Base32 Tag Pack ($29)",
    },
}

_last_warned_env_value: str | None = None


def resolve_tag_pack_link() -> tuple[str, str] | None:
    """Read and validate LOCI_STRIPE_TAG_PACK_LINK per request.

    Returns (url, stripe_mode) when valid, or None when unset/invalid.
    Invalid values are logged once with the variable NAME only (never the value).
    """
    global _last_warned_env_value
    raw = os.environ.get(STRIPE_TAG_PACK_LINK_ENV)
    if raw is None:
        _last_warned_env_value = None
        return None
    match = _STRIPE_LINK_RE.fullmatch(raw)
    if match is None:
        if _last_warned_env_value != raw:
            _last_warned_env_value = raw
            log.warning(
                "Invalid %s configuration; ignoring and treating as unset.",
                STRIPE_TAG_PACK_LINK_ENV,
            )
        return None
    _last_warned_env_value = None
    stripe_mode = "test" if match.group(1) else "live"
    return raw, stripe_mode


def _is_valid_email(value: str) -> bool:
    if not (1 <= len(value) <= 254):
        return False
    if value.count("@") != 1:
        return False
    if any(
        ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch in {",", ";", "<", ">"}
        for ch in value
    ):
        return False
    local, _, domain = value.partition("@")
    if not local or not domain:
        return False
    return not (
        local.startswith(".")
        or local.endswith(".")
        or domain.startswith(".")
        or domain.endswith(".")
    )


def validate_checkout_body(
    body: Mapping[str, Any] | None,
) -> tuple[str, str, str | None] | None:
    """Validate checkout request body: returns (tier, space_id, email) or None."""
    if not isinstance(body, Mapping):
        return None
    raw_tier = body.get("tier")
    if not isinstance(raw_tier, str) or raw_tier not in PRICES:
        return None

    raw_space_id = body.get("space_id")
    if raw_space_id is None or raw_space_id == "":
        space_id = "default"
    elif isinstance(raw_space_id, str) and _SPACE_ID_RE.fullmatch(raw_space_id):
        space_id = raw_space_id
    else:
        return None

    raw_email = body.get("email")
    if raw_email is None or raw_email == "":
        email: str | None = None
    elif isinstance(raw_email, str) and _is_valid_email(raw_email):
        email = raw_email
    else:
        return None

    return raw_tier, space_id, email


def build_checkout_payload(
    tier: str, space_id: str, email: str | None
) -> dict[str, Any]:
    """Build the /api/billing/checkout response payload for validated inputs."""
    selected = PRICES[tier]
    if tier == "tags":
        resolved = resolve_tag_pack_link()
        if resolved is not None:
            link, stripe_mode = resolved
            params: list[tuple[str, str]] = [("client_reference_id", space_id)]
            if email is not None:
                params.append(("prefilled_email", email))
            return {
                "ok": True,
                "tier": tier,
                "space_id": space_id,
                "plan": selected,
                "payment": "stripe_payment_link",
                "stripe_mode": stripe_mode,
                "checkout_url": f"{link}?{urlencode(params)}",
            }
    return {
        "ok": True,
        "tier": tier,
        "space_id": space_id,
        "plan": selected,
        "payment": "unavailable",
        "contact": BILLING_CONTACT_EMAIL,
    }
