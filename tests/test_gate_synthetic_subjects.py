"""[contract] Gate metric: reserved/synthetic domains and lincspace.ai never count as external; seeded objects carry drill=true and are excluded."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hypothesis import given, settings, strategies as st

    _HAS_HYPOTHESIS = True
except ModuleNotFoundError:  # pragma: no cover
    _HAS_HYPOTHESIS = False


def _app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LOCI_DB", str(tmp_path / "gate_synthetic.db"))
    monkeypatch.delenv("LOCI_DATABASE_URL", raising=False)
    monkeypatch.delenv("LOCI_DB_HOST", raising=False)
    monkeypatch.delenv("AMBIGUOUS_API_KEY", raising=False)
    monkeypatch.delenv("LOCI_AMBIGUOUS_SHEET_ID", raising=False)
    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    monkeypatch.delenv("LOCI_GATE_INTERNAL_DOMAINS", raising=False)

    import db
    import server

    db.init_db()
    app = Starlette(
        routes=[
            Route("/api/spaces", server.spaces_api_route, methods=["GET", "POST"]),
            Route(
                "/loci-{space_token}/api/state",
                server.space_state_route,
                methods=["GET"],
            ),
        ]
    )
    return TestClient(app)


def test_seed_showcase_spaces_have_zero_external_handoffs_and_drill_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[contract] seed_showcase_spaces() on a fresh tmp SQLite DB, then GET /api/spaces with the operator token -> external_handoff_count == 0 for EVERY seeded Space."""
    client = _app_client(tmp_path, monkeypatch)
    import db
    import server

    seeded = server.seed_showcase_spaces()
    assert len(seeded) == 5

    resp = client.get(
        "/api/spaces",
        headers={"Authorization": f"Bearer {server.TOKEN}"},
    )
    assert resp.status_code == 200
    spaces = resp.json()["spaces"]
    by_slug = {str(s["slug"]): s for s in spaces}

    seeded_slugs = {str(item["slug"]) for item in seeded}
    assert seeded_slugs.issubset(by_slug.keys())

    for slug in seeded_slugs:
        sp = by_slug[slug]
        assert sp["external_handoff_count"] == 0, f"{slug} had external handoffs: {sp}"
        assert sp["drill_object_count"] == sp["object_count"]
        assert sp["drill_object_count"] >= 1
        assert (
            0
            <= sp["external_handoff_count"]
            <= sp["cross_actor_handoff_count"]
            == sp["handoff_count"]
        )

    cm = by_slug["commercial-mechanical-handoff"]
    assert cm["cross_actor_handoff_count"] == 1
    assert cm["handoff_count"] == 1
    assert cm["external_handoff_count"] == 0
    assert cm["synthetic_subject_count"] == 2
    assert cm["excluded_subject_count"] == 2
    assert cm["drill_object_count"] == 2

    at = by_slug["ai-tinkerers-seattle-pilot"]
    assert at["external_handoff_count"] == 0
    assert at["excluded_subject_count"] == 1
    assert at["synthetic_subject_count"] == 0
    assert at["drill_object_count"] == 1

    # Verify every seeded object row in SQLite carries "drill": true in attrs_json
    with db.tx() as conn:
        for item in seeded:
            sid = str(item["space_id"])
            rows = conn.execute(
                db.q("SELECT attrs_json FROM object WHERE space_id = ?"),
                (sid,),
            ).fetchall()
            assert len(rows) >= 1
            for r in rows:
                parsed = json.loads(str(dict(r)["attrs_json"]))
                assert parsed.get("drill") is True


def test_reserved_synthetic_domains_lincspace_and_internal_domains_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[contract] Reserved/synthetic domains and lincspace.ai are internal/synthetic; lookalikes are external; LOCI_GATE_INTERNAL_DOMAINS extends defaults."""
    from gate_metrics import (
        compute_handoff_metrics,
        is_internal_subject,
        is_synthetic_subject,
    )

    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    monkeypatch.delenv("LOCI_GATE_INTERNAL_DOMAINS", raising=False)

    synthetic_subjects = (
        "a@pacificmech.example",
        "b@x.test",
        "c@y.invalid",
        "d@localhost",
        "e@example.com",
        "f@sub.example.org",
        "g@example.net",
        "h@deep.sub.example.net",
        "i@dev.localhost",
    )
    for subj in synthetic_subjects:
        assert is_internal_subject(subj) is True, subj
        assert is_synthetic_subject(subj) is True, subj

    internal_only_subjects = (
        "demo-host@lincspace.ai",
        "ops@eu.lincspace.ai",
        "owner@lincinnovations.com",
        "ops@lincforge.com",
        "x@sniperpulse.com",
    )
    for subj in internal_only_subjects:
        assert is_internal_subject(subj) is True, subj
        assert is_synthetic_subject(subj) is False, subj

    lookalike_external_subjects = (
        "a@example.company.com",
        "b@notlincspace.ai",
        "c@lincspace.ai.evil.io",
        "d@testing.io",
        "e@localhost.company.io",
        "f@invalid.org",
        "g@example-corp.com",
    )
    for subj in lookalike_external_subjects:
        assert is_internal_subject(subj) is False, subj
        assert is_synthetic_subject(subj) is False, subj

    # Metrics over synthetic + internal subjects yield 0 external handoffs and track synthetic_subject_count
    metrics = compute_handoff_metrics(
        [
            {"object_id": "o1", "author_subject": "a@pacificmech.example"},
            {"object_id": "o1", "author_subject": "e@example.com"},
            {"object_id": "o1", "author_subject": "demo-host@lincspace.ai"},
        ]
    )
    assert metrics["cross_actor_handoff_count"] == 2
    assert metrics["handoff_count"] == 2
    assert metrics["external_handoff_count"] == 0
    assert metrics["excluded_subject_count"] == 3
    assert metrics["synthetic_subject_count"] == 2
    assert metrics["drill_object_count"] == 0

    # LOCI_GATE_INTERNAL_DOMAINS="acme.io, ," adds acme.io (and subdomains) while keeping defaults
    monkeypatch.setenv("LOCI_GATE_INTERNAL_DOMAINS", "acme.io, ,")
    assert is_internal_subject("pilot@acme.io") is True
    assert is_internal_subject("pilot@eu.acme.io") is True
    assert is_internal_subject("demo-host@lincspace.ai") is True
    assert is_internal_subject("owner@lincinnovations.com") is True
    assert is_internal_subject("ops@lincforge.com") is True
    assert is_internal_subject("x@sniperpulse.com") is True
    assert is_internal_subject("pilot@notacme.io") is False
    assert is_internal_subject("a@example.company.com") is False


def test_hostile_attrs_and_drill_object_filtering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[contract] Hostile attrs never mark an object drill and never raise; drill=true on one of two objects removes only that object's external commit handoffs."""
    from gate_metrics import compute_handoff_metrics

    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    monkeypatch.delenv("LOCI_GATE_INTERNAL_DOMAINS", raising=False)

    lessons_one_obj = [
        {"object_id": "o1", "author_subject": "tech1@acme.io"},
        {"object_id": "o1", "author_subject": "tech2@acme.io"},
    ]

    hostile_attr_cases: list[Any] = [
        {"object_id": "o1", "drill": "true"},
        {"object_id": "o1", "drill": 1},
        {"object_id": "o1", "drill": None},
        {"object_id": "o1", "drill": []},
        {"object_id": "o1", "drill": {}},
        {"object_id": "o1", "drill": False},
        {"object_id": "o1", "attrs_json": "{not-valid-json"},
        "{not-valid-json",
        '{"drill": "true"}',
        '{"drill": 1}',
        '{"drill": null}',
        '{"drill": []}',
        '{"drill": {}}',
        "[]",
        "null",
        None,
        1,
        [],
        {},
    ]
    for bad_attr in hostile_attr_cases:
        res = compute_handoff_metrics(lessons_one_obj, [bad_attr])  # type: ignore[list-item]
        assert res["drill_object_count"] == 0, f"Marked drill for {bad_attr!r}"
        assert res["external_handoff_count"] == 1, (
            f"Lost external handoff for {bad_attr!r}"
        )
        assert res["cross_actor_handoff_count"] == 1

    # Two objects: drill=True on o1 removes ONLY o1's external commit handoffs; o2 still counts
    lessons_two_objs = [
        {"object_id": "o1", "author_subject": "tech1@acme.io"},
        {"object_id": "o1", "author_subject": "tech2@acme.io"},
        {"object_id": "o2", "author_subject": "tech3@acme.io"},
        {"object_id": "o2", "author_subject": "tech4@acme.io"},
    ]
    res_explicit = compute_handoff_metrics(
        lessons_two_objs,
        [
            {"object_id": "o1", "drill": True},
            {"object_id": "o2", "drill": False},
        ],
    )
    assert res_explicit["cross_actor_handoff_count"] == 2
    assert res_explicit["handoff_count"] == 2
    assert res_explicit["external_handoff_count"] == 1
    assert res_explicit["drill_object_count"] == 1

    # Also test positional attrs without explicit object_id
    res_positional = compute_handoff_metrics(
        lessons_two_objs,
        [
            {"drill": True},
            {"drill": "true"},
        ],
    )
    assert res_positional["cross_actor_handoff_count"] == 2
    assert res_positional["external_handoff_count"] == 1
    assert res_positional["drill_object_count"] == 1

    # End-to-end via SQLite + /api/spaces and /loci-{token}/api/state
    client = _app_client(tmp_path, monkeypatch)
    import db
    import server

    sp = client.post(
        "/api/spaces",
        json={"name": "Drill Object Space", "tier": "team"},
        headers={"Authorization": f"Bearer {server.TOKEN}"},
    ).json()
    sid = str(sp["space_id"])
    cap_tok = str(sp["capability_token"])

    obs_fn = getattr(server.observe, "fn", server.observe)
    commit_fn = getattr(server.commit, "fn", server.commit)

    oid1 = str(
        obs_fn(
            place_label="Room 1",
            canonical_class="valve",
            material="metal_brass_or_bronze",
            mounting="wall_mounted",
            visible_verbatim_text="V-01",
            description="Valve 1",
            visible_tag_code="DR-1A",
            user_label="Valve 1",
            space_id=sid,
        )["object_id"]
    )
    oid2 = str(
        obs_fn(
            place_label="Room 2",
            canonical_class="valve",
            material="metal_brass_or_bronze",
            mounting="wall_mounted",
            visible_verbatim_text="V-02",
            description="Valve 2",
            visible_tag_code="DR-2B",
            user_label="Valve 2",
            space_id=sid,
        )["object_id"]
    )
    oid3 = str(
        obs_fn(
            place_label="Room 3",
            canonical_class="valve",
            material="metal_brass_or_bronze",
            mounting="wall_mounted",
            visible_verbatim_text="V-03",
            description="Valve 3",
            visible_tag_code="DR-3C",
            user_label="Valve 3",
            space_id=sid,
        )["object_id"]
    )

    for oid, authors in (
        (oid1, ("a1@acme.io", "a2@acme.io")),
        (oid2, ("b1@acme.io", "b2@acme.io")),
    ):
        for idx, author in enumerate(authors):
            tok = server._CURRENT_SUBJECT.set(author)
            try:
                commit_fn(
                    object_id=oid,
                    title=f"Lesson {idx}",
                    claims=[{"text": "ok", "confidence": "high"}],
                    save=True,
                    intent="check",
                    next_question="",
                    space_id=sid,
                )
            finally:
                server._CURRENT_SUBJECT.reset(tok)

    # Mark oid1 as drill=True, and put hostile non-JSON attrs_json on oid3
    with db.tx(space_id=sid) as conn:
        conn.execute(
            db.q("UPDATE object SET attrs_json = ? WHERE space_id = ? AND id = ?"),
            (json.dumps({"drill": True}), sid, oid1),
        )
        conn.execute(
            db.q("UPDATE object SET attrs_json = ? WHERE space_id = ? AND id = ?"),
            ("not-valid-json{{{", sid, oid3),
        )

    spaces = client.get(
        "/api/spaces",
        headers={"Authorization": f"Bearer {server.TOKEN}"},
    ).json()["spaces"]
    row = next(s for s in spaces if s["id"] == sid)
    state = client.get(f"/loci-{cap_tok}/api/state").json()

    for surface in (row, state):
        assert surface["cross_actor_handoff_count"] == 2
        assert surface["handoff_count"] == 2
        assert surface["external_handoff_count"] == 1
        assert surface["drill_object_count"] == 1
        assert surface["synthetic_subject_count"] == 0


_PROP_SUBJECT_POOL = [
    "",
    "anonymous",
    "owner@lincinnovations.com",
    "demo-host@lincspace.ai",
    "ops@eu.lincspace.ai",
    "a@pacificmech.example",
    "b@x.test",
    "c@y.invalid",
    "d@localhost",
    "e@example.com",
    "f@sub.example.org",
    "a@example.company.com",
    "b@notlincspace.ai",
    "c@lincspace.ai.evil.io",
    "d@testing.io",
    "pilot1@acme.io",
    "pilot2@acme.io",
    "tech@sub.acme.io",
    "contractor@fieldops.io",
]
_PROP_OBJECT_POOL = ["obj_1", "obj_2", "obj_3", ""]
_PROP_DRILL_VALUES: list[Any] = [True, False, "true", 1, None, [], {}]


def _assert_synthetic_and_monotonic_properties(
    lessons: list[dict[str, str]],
    drill_flags: list[Any],
    cross_actor_asks: int,
    extra_drill_subject: str,
    extra_internal_domain: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gate_metrics import compute_handoff_metrics

    obj_attrs: list[dict[str, Any]] = []
    for idx, flag in enumerate(drill_flags):
        oid = f"obj_{idx + 1}"
        obj_attrs.append(
            {
                "object_id": oid,
                "drill": flag,
                "ask_count": cross_actor_asks if idx == 0 else 0,
                "cross_actor_ask_count": cross_actor_asks if idx == 0 else 0,
            }
        )

    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    monkeypatch.delenv("LOCI_GATE_INTERNAL_DOMAINS", raising=False)
    base = compute_handoff_metrics(lessons, obj_attrs)

    assert (
        0
        <= base["external_handoff_count"]
        <= base["cross_actor_handoff_count"]
        == base["handoff_count"]
    )
    assert 0 <= base["synthetic_subject_count"] <= base["excluded_subject_count"]
    expected_drills = sum(1 for flag in drill_flags if flag is True)
    assert base["drill_object_count"] == expected_drills

    # Adding a subject to LOCI_GATE_DRILL_SUBJECTS never raises external_handoff_count
    monkeypatch.setenv("LOCI_GATE_DRILL_SUBJECTS", extra_drill_subject)
    with_drill_subj = compute_handoff_metrics(lessons, obj_attrs)
    assert (
        0 <= with_drill_subj["external_handoff_count"] <= base["external_handoff_count"]
    )
    assert (
        with_drill_subj["cross_actor_handoff_count"]
        == base["cross_actor_handoff_count"]
    )

    # Adding a domain to LOCI_GATE_INTERNAL_DOMAINS never raises external_handoff_count
    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    monkeypatch.setenv("LOCI_GATE_INTERNAL_DOMAINS", extra_internal_domain)
    with_internal_dom = compute_handoff_metrics(lessons, obj_attrs)
    assert (
        0
        <= with_internal_dom["external_handoff_count"]
        <= base["external_handoff_count"]
    )
    assert (
        with_internal_dom["cross_actor_handoff_count"]
        == base["cross_actor_handoff_count"]
    )


if _HAS_HYPOTHESIS:

    @settings(max_examples=120, deadline=None)
    @given(
        lessons=st.lists(
            st.fixed_dictionaries(
                {
                    "object_id": st.sampled_from(_PROP_OBJECT_POOL),
                    "author_subject": st.sampled_from(_PROP_SUBJECT_POOL),
                }
            ),
            min_size=0,
            max_size=25,
        ),
        drill_flags=st.lists(
            st.sampled_from(_PROP_DRILL_VALUES), min_size=1, max_size=3
        ),
        cross_actor_asks=st.integers(min_value=0, max_value=5),
        extra_drill_subject=st.sampled_from(_PROP_SUBJECT_POOL),
        extra_internal_domain=st.sampled_from(
            ["acme.io", "fieldops.io", "testing.io", "example.company.com", ""]
        ),
    )
    def test_property_synthetic_and_monotonic_invariants(
        lessons: list[dict[str, str]],
        drill_flags: list[Any],
        cross_actor_asks: int,
        extra_drill_subject: str,
        extra_internal_domain: str,
    ) -> None:
        mp = pytest.MonkeyPatch()
        try:
            _assert_synthetic_and_monotonic_properties(
                lessons,
                drill_flags,
                cross_actor_asks,
                extra_drill_subject,
                extra_internal_domain,
                mp,
            )
        finally:
            mp.undo()

else:

    def test_property_synthetic_and_monotonic_invariants(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rng = random.Random(0x10C1B83)
        domains = ["acme.io", "fieldops.io", "testing.io", "example.company.com", ""]
        for _ in range(200):
            size = rng.randint(0, 25)
            lessons = [
                {
                    "object_id": rng.choice(_PROP_OBJECT_POOL),
                    "author_subject": rng.choice(_PROP_SUBJECT_POOL),
                }
                for _ in range(size)
            ]
            drill_flags = [
                rng.choice(_PROP_DRILL_VALUES) for _ in range(rng.randint(1, 3))
            ]
            cross_actor_asks = rng.choice([0, 0, 1, 2, 5])
            extra_drill_subject = rng.choice(_PROP_SUBJECT_POOL)
            extra_internal_domain = rng.choice(domains)
            _assert_synthetic_and_monotonic_properties(
                lessons,
                drill_flags,
                cross_actor_asks,
                extra_drill_subject,
                extra_internal_domain,
                monkeypatch,
            )
