"""[contract] Gate metrics: handoff_count never falls back to lesson_count; external_handoff_count excludes internal, drill, and anonymous subjects."""

from __future__ import annotations

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


def _client_and_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "Gate Metrics Space"
) -> tuple[TestClient, str, str]:
    monkeypatch.setenv("LOCI_DB", str(tmp_path / "gate_metrics.db"))
    monkeypatch.delenv("LOCI_DATABASE_URL", raising=False)
    monkeypatch.delenv("LOCI_DB_HOST", raising=False)
    monkeypatch.delenv("AMBIGUOUS_API_KEY", raising=False)
    monkeypatch.delenv("LOCI_AMBIGUOUS_SHEET_ID", raising=False)

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
    client = TestClient(app)
    sp = client.post(
        "/api/spaces",
        json={"name": name, "tier": "team"},
        headers={"Authorization": f"Bearer {server.TOKEN}"},
    ).json()
    return client, str(sp["space_id"]), str(sp["capability_token"])


def _observe_object(space_id: str, tag_code: str = "GM-2B") -> str:
    import server

    tok = server._CURRENT_SUBJECT.set("pilot@fieldops.io")
    try:
        obs = server.observe(
            place_label="mechanical room",
            canonical_class="valve",
            material="metal_brass_or_bronze",
            mounting="wall_mounted",
            visible_verbatim_text=f"VALVE-{tag_code}",
            description=f"isolation valve {tag_code}",
            visible_tag_code=tag_code,
            user_label=f"valve {tag_code}",
            space_id=space_id,
        )
        assert obs["status"] == "ok"
        return str(obs["object_id"])
    finally:
        server._CURRENT_SUBJECT.reset(tok)


def _commit_lesson(
    space_id: str, object_id: str, author_subject: str, title: str
) -> None:
    import db
    import server

    tok = server._CURRENT_SUBJECT.set(author_subject)
    try:
        res = server.commit(
            object_id=object_id,
            title=title,
            claims=[{"text": f"claim for {title}", "confidence": "high"}],
            save=True,
            intent="record maintenance",
            next_question="verify pressure",
            space_id=space_id,
        )
        assert res["status"] in {"ok", "committed"}
        if author_subject == "":
            # server.commit defaults empty ContextVar to 'anonymous'; force '' in DB to test raw empty subject
            with db.tx(space_id=space_id) as conn:
                conn.execute(
                    db.q(
                        "UPDATE lesson SET author_subject = ? WHERE space_id = ? AND id = ?"
                    ),
                    ("", space_id, str(res["lesson_id"])),
                )
    finally:
        server._CURRENT_SUBJECT.reset(tok)


def _space_row_and_state(
    client: TestClient, space_id: str, cap_tok: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    import server

    spaces = client.get(
        "/api/spaces",
        headers={"Authorization": f"Bearer {server.TOKEN}"},
    ).json()["spaces"]
    row = next(s for s in spaces if s["id"] == space_id)
    state = client.get(f"/loci-{cap_tok}/api/state").json()
    return row, state


def test_single_author_five_lessons_zero_asks_has_zero_handoffs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[contract] A Space with 5 lessons by one author and 0 asks -> handoff_count 0 (not 5) from both /api/spaces and per-space state."""
    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    client, sid, cap_tok = _client_and_space(tmp_path, monkeypatch, "Single Author 5")
    oid = _observe_object(sid, "GM-2B")
    for i in range(5):
        _commit_lesson(sid, oid, "pilot@fieldops.io", f"Lesson {i + 1}")

    row, state = _space_row_and_state(client, sid, cap_tok)
    assert row["lesson_count"] == 5
    for surface in (row, state):
        assert surface["ask_count"] == 0
        assert surface["cross_actor_handoff_count"] == 0
        assert surface["handoff_count"] == 0
        assert surface["external_handoff_count"] == 0
        assert surface["excluded_subject_count"] == 0
        assert surface["external_ask_attribution"] == "unavailable"


def test_internal_and_external_author_pair_counts_cross_actor_but_not_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[contract] Two authors owner@lincinnovations.com and pilot@example.com on one object -> cross_actor 1, external 0."""
    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    client, sid, cap_tok = _client_and_space(tmp_path, monkeypatch, "Mixed Pair")
    oid = _observe_object(sid, "GM-3C")
    _commit_lesson(sid, oid, "owner@lincinnovations.com", "Internal setup")
    _commit_lesson(sid, oid, "pilot@fieldops.io", "External follow-up")

    row, state = _space_row_and_state(client, sid, cap_tok)
    for surface in (row, state):
        assert surface["cross_actor_handoff_count"] == 1
        assert surface["handoff_count"] == 1
        assert surface["external_handoff_count"] == 0
        assert surface["excluded_subject_count"] == 1
        assert surface["external_ask_attribution"] == "unavailable"


def test_two_external_authors_on_one_object_counts_external_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[contract] Two external authors on one object -> external 1."""
    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    client, sid, cap_tok = _client_and_space(tmp_path, monkeypatch, "External Pair")
    oid = _observe_object(sid, "GM-4D")
    _commit_lesson(sid, oid, "pilot1@fieldops.io", "Shift 1 inspection")
    _commit_lesson(sid, oid, "pilot2@fieldops.io", "Shift 2 repair")

    row, state = _space_row_and_state(client, sid, cap_tok)
    for surface in (row, state):
        assert surface["cross_actor_handoff_count"] == 1
        assert surface["handoff_count"] == 1
        assert surface["external_handoff_count"] == 1
        assert surface["excluded_subject_count"] == 0
        assert surface["external_ask_attribution"] == "unavailable"


def test_drill_subjects_anonymous_empty_and_internal_subdomains_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[contract] LOCI_GATE_DRILL_SUBJECTS, 'anonymous', '', Ops@LincForge.com, and x@eu.sniperpulse.com are excluded."""
    monkeypatch.setenv(
        "LOCI_GATE_DRILL_SUBJECTS", "drill.runner@contractor.io, synthetic@pilot.org"
    )
    client, sid, cap_tok = _client_and_space(tmp_path, monkeypatch, "Exclusions")
    oid = _observe_object(sid, "GM-5F")

    for subj in (
        "drill.runner@contractor.io",
        "anonymous",
        "",
        "Ops@LincForge.com",
        "x@eu.sniperpulse.com",
        "pilot@fieldops.io",
    ):
        _commit_lesson(sid, oid, subj, f"Lesson by {subj or 'empty'}")

    row, state = _space_row_and_state(client, sid, cap_tok)
    for surface in (row, state):
        # 5 non-empty distinct subjects on oid -> 4 cross-actor commit handoffs
        assert surface["cross_actor_handoff_count"] == 4
        assert surface["handoff_count"] == 4
        # Only 1 external author (pilot@example.com) -> 0 external handoffs
        assert surface["external_handoff_count"] == 0
        # 5 excluded subjects: drill.runner@contractor.io, anonymous, '', ops@lincforge.com, x@eu.sniperpulse.com
        assert surface["excluded_subject_count"] == 5


def test_hostile_subjects_and_drill_env_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[contract] Hostile: whitespace, no '@' (excluded), domain lookalikes, and LOCI_GATE_DRILL_SUBJECTS edge cases."""
    from gate_metrics import compute_handoff_metrics, is_internal_subject

    # 1. LOCI_GATE_DRILL_SUBJECTS unset, empty, or with stray commas -> no crash
    for env_val in (None, "", "   ", ",,,", " , , drill-actor@fieldops.io , , "):
        if env_val is None:
            monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
        else:
            monkeypatch.setenv("LOCI_GATE_DRILL_SUBJECTS", env_val)
        res = compute_handoff_metrics(
            [
                {"object_id": "o1", "author_subject": "drill-actor@fieldops.io"},
                {"object_id": "o1", "author_subject": "pilot@fieldops.io"},
            ]
        )
        if env_val and "drill-actor@fieldops.io" in env_val:
            assert res["external_handoff_count"] == 0
            assert res["excluded_subject_count"] == 1
        else:
            assert res["external_handoff_count"] == 1
            assert res["excluded_subject_count"] == 0

    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)

    # 2. Internal exact domains & subdomains (with surrounding whitespace and mixed case)
    for internal_subj in (
        "owner@lincinnovations.com",
        "  owner@lincinnovations.com  ",
        "\tOps@LincForge.com\n",
        "x@eu.sniperpulse.com",
        "tech@bench.lincinnovations.com",
        "ci@sub.lincforge.com",
        "anonymous",
        "  ANONYMOUS  ",
        "",
        "   ",
    ):
        assert is_internal_subject(internal_subj) is True, internal_subj

    # 3. Domain lookalikes -> treated as external (exact-domain/subdomain rule only)
    for external_subj in (
        "pilot@fieldops.io",
        "  pilot@fieldops.io  ",
        "user@lincinnovations.com.evil.io",
        "user@notlincforge.com",
        "user@sniperpulse.com.attacker.io",
        "user@fakelincinnovations.com",
    ):
        assert is_internal_subject(external_subj) is False, external_subj

    # 3b. Unattributable subjects (no '@', empty local part, empty domain) -> excluded, never external.
    #     Machine clients ('service-client', client_id) and OAuth fallback ('oauth-user') are not humans.
    for unattributable_subj in (
        "oauth-user",
        "service-client",
        "claude-ai-connector",
        "@example.com",
        "pilot@",
        "  @  ",
        "field-tech-no-at-sign",
        "lincinnovations.com",
    ):
        assert is_internal_subject(unattributable_subj) is True, unattributable_subj

    # 4. Lookalikes count as external; no-'@' subjects are excluded and reported
    lookalike_metrics = compute_handoff_metrics(
        [
            {"object_id": "o1", "author_subject": "  u1@lincinnovations.com.evil.io "},
            {"object_id": "o1", "author_subject": "u2@notlincforge.com"},
            {"object_id": "o1", "author_subject": "field-tech-no-at"},
        ]
    )
    assert lookalike_metrics["cross_actor_handoff_count"] == 2
    assert lookalike_metrics["handoff_count"] == 2
    assert lookalike_metrics["external_handoff_count"] == 1
    assert lookalike_metrics["excluded_subject_count"] == 1

    # 4b. Two machine clients / connectors on one object -> no external handoff (no false gate pass)
    machine_metrics = compute_handoff_metrics(
        [
            {"object_id": "o1", "author_subject": "oauth-user"},
            {"object_id": "o1", "author_subject": "service-client"},
            {"object_id": "o1", "author_subject": "claude-ai-connector"},
            {"object_id": "o1", "author_subject": "@example.com"},
            {"object_id": "o1", "author_subject": "pilot@"},
        ]
    )
    assert machine_metrics["external_handoff_count"] == 0
    assert machine_metrics["excluded_subject_count"] == 5

    # 5. Same external author with surrounding whitespace does not self-handoff
    whitespace_dup = compute_handoff_metrics(
        [
            {"object_id": "o1", "author_subject": "pilot@fieldops.io"},
            {"object_id": "o1", "author_subject": "  pilot@fieldops.io\n"},
        ]
    )
    assert whitespace_dup["cross_actor_handoff_count"] == 0
    assert whitespace_dup["handoff_count"] == 0
    assert whitespace_dup["external_handoff_count"] == 0


def test_external_authors_are_case_and_whitespace_normalized() -> None:
    """[contract] 'Pilot@FieldOps.io' and 'pilot@fieldops.io' on one object are ONE external author."""
    from gate_metrics import compute_handoff_metrics

    res = compute_handoff_metrics(
        [
            {"object_id": "o1", "author_subject": "Pilot@FieldOps.io"},
            {"object_id": "o1", "author_subject": "pilot@fieldops.io"},
            {"object_id": "o1", "author_subject": "  PILOT@FIELDOPS.IO\t"},
        ]
    )
    assert res["external_handoff_count"] == 0
    assert res["excluded_subject_count"] == 0

    # A genuinely distinct second human still counts once.
    res2 = compute_handoff_metrics(
        [
            {"object_id": "o1", "author_subject": "Pilot@FieldOps.io"},
            {"object_id": "o1", "author_subject": "pilot@fieldops.io"},
            {"object_id": "o1", "author_subject": "Tech@FieldOps.io"},
        ]
    )
    assert res2["external_handoff_count"] == 1


def test_machine_clients_on_one_object_do_not_pass_external_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[contract] Two machine clients ('service-client', 'oauth-user') on one object -> external 0 on both surfaces."""
    monkeypatch.delenv("LOCI_GATE_DRILL_SUBJECTS", raising=False)
    client, sid, cap_tok = _client_and_space(tmp_path, monkeypatch, "Machine Pair")
    oid = _observe_object(sid, "GM-6G")
    _commit_lesson(sid, oid, "service-client", "Connector write")
    _commit_lesson(sid, oid, "oauth-user", "OAuth fallback write")

    row, state = _space_row_and_state(client, sid, cap_tok)
    for surface in (row, state):
        assert surface["external_handoff_count"] == 0
        assert surface["excluded_subject_count"] == 2


def test_asks_excluded_from_external_handoff_count_when_unattributed() -> None:
    """[contract] Asks are excluded from external_handoff_count and external_ask_attribution is 'unavailable'."""
    from gate_metrics import compute_handoff_metrics

    res = compute_handoff_metrics(
        [{"object_id": "o1", "author_subject": "pilot1@example.com"}],
        [{"ask_count": 3, "cross_actor_ask_count": 2}],
    )
    assert res["ask_count"] == 3
    assert res["cross_actor_handoff_count"] == 2
    assert res["handoff_count"] == 2
    assert res["external_handoff_count"] == 0
    assert res["external_ask_attribution"] == "unavailable"


def test_viewer_html_shows_external_handoff_count() -> None:
    viewer = (ROOT / "viewer.html").read_text()
    assert (
        "Cross-Actor Handoffs: ${s.handoff_count ?? 0} (external: ${s.external_handoff_count ?? 0})"
        in viewer
    )


def _assert_handoff_properties(
    lessons: list[dict[str, str]], cross_actor_asks: int
) -> None:
    from gate_metrics import compute_handoff_metrics

    obj_attrs = [
        {"ask_count": cross_actor_asks, "cross_actor_ask_count": cross_actor_asks}
    ]
    res = compute_handoff_metrics(
        lessons,
        obj_attrs,
        drill_subjects="drill@example.com",
    )
    assert (
        0
        <= res["external_handoff_count"]
        <= res["cross_actor_handoff_count"]
        == res["handoff_count"]
    )

    authors_by_obj: dict[str, set[str]] = {}
    for les in lessons:
        oid = str(les.get("object_id") or "").strip()
        subj = str(les.get("author_subject") or "").strip()
        if oid and subj:
            authors_by_obj.setdefault(oid, set()).add(subj)
    real_handoffs = cross_actor_asks + sum(
        max(0, len(authors) - 1) for authors in authors_by_obj.values()
    )
    assert res["handoff_count"] == real_handoffs
    if real_handoffs != len(lessons):
        assert res["handoff_count"] != len(lessons)


_SUBJECT_POOL = [
    "",
    "   ",
    "anonymous",
    "owner@lincinnovations.com",
    "Ops@LincForge.com",
    "x@eu.sniperpulse.com",
    "drill@example.com",
    "pilot1@example.com",
    "pilot2@example.com",
    "  pilot1@example.com  ",
    "tech@lincinnovations.com.evil.io",
    "tech@notlincforge.com",
    "bare-subject-no-at",
    "oauth-user",
    "service-client",
    "@example.com",
    "pilot@",
    "Pilot1@Example.com",
    "PILOT2@EXAMPLE.COM",
]
_OBJECT_POOL = ["obj_1", "obj_2", "obj_3", ""]


if _HAS_HYPOTHESIS:

    @settings(max_examples=150, deadline=None)
    @given(
        lessons=st.lists(
            st.fixed_dictionaries(
                {
                    "object_id": st.sampled_from(_OBJECT_POOL),
                    "author_subject": st.sampled_from(_SUBJECT_POOL),
                }
            ),
            min_size=0,
            max_size=25,
        ),
        cross_actor_asks=st.integers(min_value=0, max_value=5),
    )
    def test_property_handoff_invariants(
        lessons: list[dict[str, str]], cross_actor_asks: int
    ) -> None:
        _assert_handoff_properties(lessons, cross_actor_asks)

else:

    def test_property_handoff_invariants() -> None:
        rng = random.Random(0x10C1)
        for _ in range(200):
            size = rng.randint(0, 25)
            lessons = [
                {
                    "object_id": rng.choice(_OBJECT_POOL),
                    "author_subject": rng.choice(_SUBJECT_POOL),
                }
                for _ in range(size)
            ]
            cross_actor_asks = rng.choice([0, 0, 0, 1, 2, 5])
            _assert_handoff_properties(lessons, cross_actor_asks)
