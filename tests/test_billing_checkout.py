"""[contract] Billing checkout & /pricing Stripe Payment Link tests (bd_0xl0c1-wmi)."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _client() -> TestClient:
    import server

    app = Starlette(
        routes=[
            Route(
                "/api/billing/checkout",
                server.billing_checkout_route,
                methods=["POST"],
            ),
            Route("/pricing", server.pricing_page_route, methods=["GET"]),
        ]
    )
    return TestClient(app)


def test_contract_env_unset_tier_tags_returns_unavailable_without_checkout_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCI_STRIPE_TAG_PACK_LINK", raising=False)
    client = _client()
    resp = client.post(
        "/api/billing/checkout",
        json={"tier": "tags", "space_id": "default", "email": "buyer@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["payment"] == "unavailable"
    assert data["contact"] == "faust@lincinnovations.com"
    assert "checkout_url" not in data
    assert "checkout=ready" not in resp.text


def test_contract_valid_test_and_live_stripe_link_builds_checkout_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LOCI_STRIPE_TAG_PACK_LINK", "https://buy.stripe.com/test_ABCdef1234"
    )
    client = _client()
    resp = client.post(
        "/api/billing/checkout",
        json={
            "tier": "tags",
            "space_id": "sp_0123456789ab",
            "email": "a+b@x.io",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["payment"] == "stripe_payment_link"
    assert data["stripe_mode"] == "test"
    assert (
        data["checkout_url"]
        == "https://buy.stripe.com/test_ABCdef1234?client_reference_id=sp_0123456789ab&prefilled_email=a%2Bb%40x.io"
    )

    # Without email and without space_id -> defaults space_id to "default", omits prefilled_email, live mode
    monkeypatch.setenv(
        "LOCI_STRIPE_TAG_PACK_LINK", "https://buy.stripe.com/LiveLink89ab"
    )
    resp_live = client.post("/api/billing/checkout", json={"tier": "tags"})
    assert resp_live.status_code == 200
    data_live = resp_live.json()
    assert data_live["ok"] is True
    assert data_live["payment"] == "stripe_payment_link"
    assert data_live["stripe_mode"] == "live"
    assert (
        data_live["checkout_url"]
        == "https://buy.stripe.com/LiveLink89ab?client_reference_id=default"
    )


@pytest.mark.parametrize(
    "hostile_env",
    [
        "http://buy.stripe.com/x",
        "http://buy.stripe.com/test_ABCdef1234",
        "https://buy.stripe.com.evil.io/x",
        "https://evil.io/buy.stripe.com/x",
        "https://buy.stripe.com/test_ABCdef1234\n",
        "https://buy.stripe.com/test_ABCdef1234?a=b",
        "https://buy.stripe.com/test_ABCdef1234/../x",
        "https://buy.stripe.com/short7",
        "https://buy.stripe.com/" + ("A" * 65),
        "",
        "   ",
    ],
)
def test_contract_hostile_env_treated_as_unset_and_logs_name_only(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    hostile_env: str,
) -> None:
    monkeypatch.setenv("LOCI_STRIPE_TAG_PACK_LINK", hostile_env)
    client = _client()
    with caplog.at_level(logging.WARNING):
        resp1 = client.post(
            "/api/billing/checkout",
            json={"tier": "tags", "space_id": "default"},
        )
        resp2 = client.post(
            "/api/billing/checkout",
            json={"tier": "tags", "space_id": "default"},
        )
    for resp in (resp1, resp2):
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["payment"] == "unavailable"
        assert data["contact"] == "faust@lincinnovations.com"
        assert "checkout_url" not in data

    env_records = [
        r for r in caplog.records if "LOCI_STRIPE_TAG_PACK_LINK" in r.getMessage()
    ]
    assert len(env_records) == 1
    if hostile_env.strip():
        assert hostile_env.strip() not in caplog.text


@pytest.mark.parametrize(
    ("payload", "forbidden_echo"),
    [
        ({"tier": "PRO "}, "PRO "),
        ({"tier": "enterprise"}, "enterprise"),
        ({"tier": 5}, None),
        ({"tier": None}, None),
        ({}, None),
        ({"tier": "tags", "space_id": "sp_x"}, "sp_x"),
        ({"tier": "tags", "space_id": 'default"><img>'}, 'default"><img>'),
        ({"tier": "tags", "space_id": "x" * 1024}, "x" * 64),
        ({"tier": "tags", "space_id": "sp_0123456789AB"}, "sp_0123456789AB"),
        ({"tier": "tags", "space_id": 42}, None),
        ({"tier": "tags", "email": "a@b@c"}, "a@b@c"),
        ({"tier": "tags", "email": ("a" * 290) + "@x.io"}, "a" * 50),
        ({"tier": "tags", "email": "no-at-sign"}, "no-at-sign"),
        ({"tier": "tags", "email": "@missing-local.io"}, "@missing-local.io"),
        ({"tier": "tags", "email": "missing-domain@"}, "missing-domain@"),
        ({"tier": "tags", "email": ".leading-dot@x.io"}, ".leading-dot@x.io"),
        ({"tier": "tags", "email": "user@trailing-dot."}, "user@trailing-dot."),
        ({"tier": "tags", "email": "two@x.io addresses@x.io"}, "addresses@x.io"),
        ({"tier": "tags", "email": 99}, None),
    ],
)
def test_contract_hostile_body_gets_400_without_reflecting_input(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    forbidden_echo: str | None,
) -> None:
    monkeypatch.setenv(
        "LOCI_STRIPE_TAG_PACK_LINK", "https://buy.stripe.com/test_ABCdef1234"
    )
    client = _client()
    resp = client.post("/api/billing/checkout", json=payload)
    assert resp.status_code == 400
    assert resp.json().get("error") == "invalid_request"
    if forbidden_echo is not None:
        assert forbidden_echo not in resp.text


@pytest.mark.parametrize(
    "raw_body",
    [
        b"",
        b"{malformed json",
        b"[1, 2, 3]",
        b'"string-body"',
        b"123",
        b"null",
    ],
)
def test_contract_non_object_or_malformed_json_gets_400(raw_body: bytes) -> None:
    client = _client()
    resp = client.post(
        "/api/billing/checkout",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json().get("error") == "invalid_request"


@pytest.mark.parametrize("other_tier", ["free", "pro", "team"])
def test_contract_non_tag_tiers_keep_payment_unavailable_even_when_link_set(
    monkeypatch: pytest.MonkeyPatch,
    other_tier: str,
) -> None:
    monkeypatch.setenv(
        "LOCI_STRIPE_TAG_PACK_LINK", "https://buy.stripe.com/test_ABCdef1234"
    )
    client = _client()
    resp = client.post(
        "/api/billing/checkout",
        json={"tier": other_tier, "space_id": "default", "email": "ops@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["payment"] == "unavailable"
    assert data["contact"] == "faust@lincinnovations.com"
    assert "checkout_url" not in data


def test_pricing_html_removes_unmeasured_claims_and_innerhtml_reflection() -> None:
    client = _client()
    resp = client.get("/pricing")
    assert resp.status_code == 200
    html = resp.text

    assert "Plan Reserved" not in html
    assert "checkout=ready" not in html
    assert "innerHTML = '<div" not in html
    assert "innerHTML" not in html
    assert "Readable by any frontier VLM at 1.5m distance" not in html
    assert "1.5m" not in html

    # Material / count lines remain on the tag-pack card for CEO review
    assert "50x matte-black / signal-white vinyl stickers" in html
    assert "20-char homoglyph-free alphabet + Mod-30 check" in html
    assert "Includes instant SVG sheet download" in html

    # Safe DOM result rendering with Stripe button, test-mode note, and unavailable notice
    assert "Pay $29 on Stripe" in html
    assert "TEST MODE: no real charge" in html
    assert "Online payment is not open yet" in html
    assert "noopener" in html
    assert "textContent" in html


def test_contract_repo_hygiene_no_stripe_secrets_or_prod_links_outside_tests() -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    secret_re = re.compile(r"\b(?:[sr]k_(?:live|test)|whsec)_[A-Za-z0-9_]*")
    stripe_link_re = re.compile(r"https?://buy\.stripe\.com/[A-Za-z0-9_]+")

    for rel in tracked:
        path = ROOT / rel
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        assert secret_re.search(content) is None, (
            f"Stripe secret/restricted key pattern found in {rel}"
        )
        if not rel.startswith("tests/"):
            assert stripe_link_re.search(content) is None, (
                f"Hardcoded buy.stripe.com link found outside tests in {rel}"
            )
