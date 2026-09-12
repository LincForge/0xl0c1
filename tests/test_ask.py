"""P2: `ask` — exact place filter, three short-circuits, one additive score, confirm band, cursor."""

from __future__ import annotations

import asyncio

import pytest

PLACE = "basement utility closet"

VALVE_A = dict(
    place_label=PLACE,
    canonical_class="valve",
    material="metal_brass_or_bronze",
    mounting="wall_mounted",
    visible_verbatim_text="3/4 600 WOG NSF-61 APOLLO",
    description="3/4 inch brass ball valve, red lever handle, on the copper main water line",
    user_label="the toilet shutoff",
)
VALVE_B = dict(
    place_label=PLACE,
    canonical_class="valve",
    material="metal_brass_or_bronze",
    mounting="wall_mounted",
    visible_verbatim_text="1/2 CSA 600WOG",
    description="1/2 inch brass ball valve, red lever handle, on the branch line",
    user_label="the sink shutoff",
)
GENERIC = "brass ball valve with a red lever handle"


def _seed_valves(server):
    a = server.observe(**VALVE_A)["object_id"]
    b = server.observe(**VALVE_B)["object_id"]
    return a, b


def _commit(server, oid, nq="does the toilet supply need its own stop?", **over):
    return server.commit(
        object_id=oid,
        title="isolation valve",
        claims=[{"text": "branch isolated here", "confidence": 0.9}],
        save=True,
        intent="find the shutoff",
        next_question=nq,
        **over,
    )


def test_exact_place_filter_only(loci_db, server_mod):
    server_mod.observe(**VALVE_A)
    server_mod.observe(**{**VALVE_B, "place_label": "garage workbench"})
    r = server_mod.ask(description=GENERIC, place_label=PLACE)
    assert r["status"] == "resumed"
    assert r["object"]["label"] == "the toilet shutoff"
    r2 = server_mod.ask(description=GENERIC, place_label="basement")
    # zero exact hits: fall back to every object, so the two twins reach the confirm band
    assert r2["status"] == "needs_confirm"
    assert (
        r2["place_note"]
        == "no objects recorded under 'basement'; matched across all places"
    )
    assert "place_note" not in r


def test_place_fallback_only_when_exact_filter_is_empty(loci_db, server_mod):
    """Live 2026-09-12: two models spelled the same bathroom four ways and five asks hit no_match."""
    _seed_valves(server_mod)
    exact = server_mod.ask(description=GENERIC, place_label=PLACE)
    assert exact["status"] == "needs_confirm" and "place_note" not in exact
    misspelt = server_mod.ask(description=GENERIC, place_label="Jon's master bathroom")
    assert misspelt["status"] == "needs_confirm"
    assert len(misspelt["candidates"]) == 2
    assert misspelt["delta"] < server_mod.DELTA
    assert misspelt["place_note"] == (
        "no objects recorded under 'Jon's master bathroom'; matched across all places"
    )
    empty = server_mod.ask(description=GENERIC)
    assert "place_note" not in empty


def test_verbatim_hit_resumes(loci_db, server_mod):
    a, _ = _seed_valves(server_mod)
    r = server_mod.ask(visible_verbatim_text="3/4 600 WOG NSF-61 APOLLO")
    assert r["status"] == "resumed"
    assert r["matched_on"] == "verbatim_text"
    assert r["object"]["object_id"] == a
    assert r["score"] >= server_mod.VERBATIM_HIT


def test_verbatim_collision_falls_through(loci_db, server_mod):
    server_mod.observe(
        **{**VALVE_A, "visible_verbatim_text": "3/4 600 WOG NSF-61 APOLLO"}
    )
    server_mod.observe(
        **{**VALVE_B, "visible_verbatim_text": "1/2 600 WOG NSF-61 APOLLO"}
    )
    r = server_mod.ask(
        description=GENERIC, visible_verbatim_text="3/4 600 WOG NSF-61 APOLLO"
    )
    assert r["status"] == "needs_confirm"
    assert r["status"] != "resumed"


def test_tag_hit_short_circuits(loci_db, server_mod):
    tagged = server_mod.observe(
        **{**VALVE_B, "visible_tag_code": "K94B", "description": "unrelated widget"}
    )["object_id"]
    server_mod.observe(**VALVE_A)
    r = server_mod.ask(description=VALVE_A["description"], visible_tag_code="k9-4b")
    assert r["status"] == "resumed"
    assert r["matched_on"] == "tag_code"
    assert r["score"] == 1.0
    assert r["object"]["object_id"] == tagged


def test_object_id_short_circuits(loci_db, server_mod):
    a, _ = _seed_valves(server_mod)
    _commit(server_mod, a)
    r = server_mod.ask(object_id=a)
    assert r["status"] == "resumed"
    assert r["matched_on"] == "object_id"
    assert r["object"]["object_id"] == a
    assert len(r["lessons"]) == 1 and len(r["claims"]) == 1
    assert r["next_question"] == "does the toilet supply need its own stop?"


def test_confirm_band_on_twins(loci_db, server_mod):
    _seed_valves(server_mod)
    r = server_mod.ask(description=GENERIC)
    assert r["status"] == "needs_confirm"
    assert r["needs_confirm"] is True
    assert r["delta"] < server_mod.DELTA
    assert len(r["candidates"]) == 2
    assert r["matches"] == []
    assert r["prompt_to_user"] == (
        f"I have two records here: 'the sink shutoff' and 'the toilet shutoff' ({PLACE}). Which one?"
    )


def test_confirm_prompt_names_each_place_when_they_differ(loci_db, server_mod):
    server_mod.observe(**VALVE_A)
    server_mod.observe(**{**VALVE_B, "place_label": "garage"})
    r = server_mod.ask(description=GENERIC)
    assert r["status"] == "needs_confirm"
    assert r["prompt_to_user"] == (
        f"I have two records here: 'the sink shutoff' (garage) and 'the toilet shutoff' ({PLACE}). Which one?"
    )


def test_ask_logs_one_line_per_call(loci_db, server_mod, caplog):
    _seed_valves(server_mod)
    with caplog.at_level("INFO", logger="loci"):
        server_mod.ask(description=GENERIC, place_label=PLACE)
    lines = [m for m in caplog.messages if m.startswith("LOCI tool=ask ")]
    assert len(lines) == 1
    assert "status=needs_confirm" in lines[0]
    assert f"place={PLACE!r}" in lines[0]
    assert "candidates=2" in lines[0]
    assert "APOLLO" not in lines[0] and "brass" not in lines[0]


def test_lone_candidate_resumes(loci_db, server_mod):
    a = server_mod.observe(**VALVE_A)["object_id"]
    r = server_mod.ask(description=GENERIC)
    assert r["status"] == "resumed"
    assert r["delta"] == 1.0
    assert r["object"]["object_id"] == a
    assert r["matches"][0]["object_id"] == a


def test_no_match_offers_observe(loci_db, server_mod):
    _seed_valves(server_mod)
    r = server_mod.ask(
        description="a wooden dining chair with a cane seat", place_label=PLACE
    )
    assert r["status"] == "no_match"
    assert r["matches"] == []
    assert r["best_score"] < server_mod.MIN_SCORE
    assert "observe" in r["prompt_to_user"]


def test_enum_mismatch_never_disqualifies(loci_db, server_mod):
    server_mod.observe(**VALVE_A)
    right = server_mod.ask(
        description=VALVE_A["description"],
        material="metal_brass_or_bronze",
        mounting="wall_mounted",
    )
    wrong = server_mod.ask(
        description=VALVE_A["description"],
        material="metal_brass_or_bronze",
        mounting="recessed_or_built_in",
    )
    assert right["status"] == wrong["status"] == "resumed"
    assert right["score"] - wrong["score"] <= 0.05 + 1e-9


def test_enum_bonus_is_capped(loci_db, server_mod):
    server_mod.observe(**VALVE_A)
    r = server_mod.ask(
        description=VALVE_A["description"],
        material="metal_brass_or_bronze",
        mounting="wall_mounted",
    )
    assert r["score"] == 1.0


def test_returns_claims_as_readonly_data(loci_db, server_mod):
    a, _ = _seed_valves(server_mod)
    _commit(server_mod, a)
    resumed = server_mod.ask(object_id=a)
    confirm = server_mod.ask(description=GENERIC)
    nomatch = server_mod.ask(description="a wooden dining chair with a cane seat")
    for r in (resumed, confirm, nomatch):
        assert r["_data_not_instructions"]
    assert all(lesson["readOnly"] is True for lesson in resumed["lessons"])
    assert all(claim["readOnly"] is True for claim in resumed["claims"])


def test_ask_returns_active_next_question(loci_db, server_mod):
    a = server_mod.observe(**VALVE_A)["object_id"]
    _commit(server_mod, a, nq="first question")
    _commit(server_mod, a, nq="second question")
    r = server_mod.ask(object_id=a)
    assert r["next_question"] == "second question"
    assert len(loci_db.rows("SELECT id FROM lesson WHERE is_cursor_active = 1")) == 1


def test_ask_schema_keeps_enums(loci_db, server_mod):
    tool = asyncio.run(server_mod.mcp.get_tool("ask"))
    props = tool.parameters["properties"]

    def enum_of(prop):
        if "enum" in prop:
            return set(prop["enum"])
        for branch in prop.get("anyOf", []):
            if "enum" in branch:
                return set(branch["enum"])
        return set()

    assert enum_of(props["material"]) == set(server_mod.Material.__args__)
    assert enum_of(props["mounting"]) == set(server_mod.Mounting.__args__)


@pytest.mark.parametrize("const", ["MIN_SCORE", "DELTA", "VERBATIM_HIT"])
def test_constants_are_published(server_mod, const):
    assert isinstance(getattr(server_mod, const), float)
