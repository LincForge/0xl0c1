"""P1: observe and commit actually write. All tests use the loci_db fixture (fresh SQLite file)."""

from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

VALVE = dict(
    canonical_class="valve",
    material="metal_brass_or_bronze",
    mounting="wall_mounted",
    visible_verbatim_text="3/4 600 WOG NSF-61 APOLLO",
    description="3/4 inch brass ball valve, red lever handle, on the copper main water line",
)


def _observe(server, **over):
    args = {"place_label": "basement utility closet", **VALVE, **over}
    return server.observe(**args)


def test_observe_creates_place_and_object(loci_db, server_mod):
    r = _observe(server_mod)
    assert r["status"] == "created"
    assert r["created"] is True
    assert loci_db.counts() == {"place": 1, "object": 1, "lesson": 0, "claim": 0}
    place = loci_db.rows("SELECT * FROM place")[0]
    obj = loci_db.rows("SELECT * FROM object")[0]
    assert r["place_id"] == place["id"]
    assert r["object_id"] == obj["id"]
    assert obj["first_seen_at"] == obj["last_seen_at"]
    assert ISO_Z.match(obj["first_seen_at"])
    datetime.strptime(obj["first_seen_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert r["needs_place"] is False and r["prompt_to_user"] is None
    assert r["echo"]["visible_verbatim_text"] == VALVE["visible_verbatim_text"]
    assert "_stub" not in r


@pytest.mark.parametrize(
    ("label", "path"),
    [
        ("upstairs hallway return", "house.upstairs.hallway.return"),
        ("Garage Workbench", "house.garage.workbench"),
        ("the Kitchen, sink", "house.kitchen.sink"),
        ("shop.bench", "shop.bench"),
    ],
)
def test_observe_derives_dotted_place_path(loci_db, server_mod, label, path):
    r = _observe(server_mod, place_label=label)
    assert r["place_path"] == path
    assert loci_db.rows("SELECT path FROM place")[0]["path"] == path


def test_observe_reuses_an_existing_place(loci_db, server_mod):
    a = _observe(server_mod)
    b = _observe(server_mod, visible_verbatim_text="1/2 CSA 600WOG")
    assert loci_db.counts()["place"] == 1
    assert loci_db.counts()["object"] == 2
    assert a["place_id"] == b["place_id"]


def test_observe_blank_place_writes_nothing(loci_db, server_mod):
    r = _observe(server_mod, place_label="   ")
    assert r["needs_place"] is True
    assert r["prompt_to_user"]
    assert r["status"] == "needs_place"
    assert r["created"] is False and r["object_id"] is None
    assert loci_db.counts() == {"place": 0, "object": 0, "lesson": 0, "claim": 0}


def test_observe_stores_enums_in_attrs_json(loci_db, server_mod):
    _observe(server_mod, material="plastic_molded", mounting="handheld_portable")
    attrs = json.loads(loci_db.rows("SELECT attrs_json FROM object")[0]["attrs_json"])
    assert attrs["material"] == "plastic_molded"
    assert attrs["mounting"] == "handheld_portable"
    assert attrs["canonical_class"] == "valve"


def test_observe_tag_conflict_writes_nothing(loci_db, server_mod):
    first = _observe(server_mod, visible_tag_code="K94B")
    r = _observe(server_mod, visible_tag_code="k9-4b", description="something else")
    assert r["status"] == "tag_conflict"
    assert r["created"] is False
    assert r["object_id"] == first["object_id"]
    assert r["prompt_to_user"]
    assert loci_db.counts()["object"] == 1


def test_observe_label_prefers_user_label(loci_db, server_mod):
    r = _observe(server_mod, user_label="the toilet shutoff")
    assert r["label"] == "the toilet shutoff"
    obj = loci_db.rows("SELECT label, aliases_json FROM object")[0]
    assert obj["label"] == "the toilet shutoff"
    assert json.loads(obj["aliases_json"]) == ["the toilet shutoff"]


def _commit(server, oid, **over):
    args = dict(
        object_id=oid,
        title="isolation valve on the branch",
        claims=[{"text": "branch isolated here", "confidence": 0.9}, {"text": "rest of house stays on"}],
        save=True,
        intent="find the shutoff",
        next_question="does the toilet supply need its own stop?",
    )
    args.update(over)
    return server.commit(**args)


def test_commit_writes_lesson_and_claims(loci_db, server_mod):
    oid = _observe(server_mod)["object_id"]
    r = _commit(server_mod, oid)
    assert r["status"] == "committed"
    assert r["object_id"] == oid
    assert r["lesson_id"]
    assert r["claims_persisted"] == 2 and r["claims_skipped"] == 0
    assert r["cursor_active"] is True
    assert loci_db.counts()["lesson"] == 1 and loci_db.counts()["claim"] == 2
    assert {c["status"] for c in loci_db.rows("SELECT status FROM claim")} == {"asserted"}
    assert "_stub" not in r


def test_commit_retires_prior_cursor(loci_db, server_mod):
    oid = _observe(server_mod)["object_id"]
    _commit(server_mod, oid, next_question="q1")
    second = _commit(server_mod, oid, next_question="q2")
    active = loci_db.rows("SELECT id, next_question FROM lesson WHERE is_cursor_active = 1")
    assert len(active) == 1
    assert active[0]["id"] == second["lesson_id"]
    assert active[0]["next_question"] == "q2"


def test_commit_without_next_question_preserves_cursor(loci_db, server_mod):
    oid = _observe(server_mod)["object_id"]
    first = _commit(server_mod, oid, next_question="q1")
    second = _commit(server_mod, oid, next_question="")
    assert second["cursor_active"] is False
    rows = {r["id"]: r["is_cursor_active"] for r in loci_db.rows("SELECT id, is_cursor_active FROM lesson")}
    assert rows[first["lesson_id"]] == 1
    assert rows[second["lesson_id"]] == 0
    assert sum(rows.values()) == 1


def test_commit_dry_run_writes_nothing(loci_db, server_mod):
    oid = _observe(server_mod)["object_id"]
    before = loci_db.counts()
    r = _commit(server_mod, oid, save=False)
    assert loci_db.counts() == before
    assert r["status"] == "dry_run"
    assert r["skipped"] is True
    assert r["lesson_id"] is None and r["claims_persisted"] == 0 and r["cursor_active"] is False
    w = r["would_have_written"]
    assert set(w) >= {"object_id", "title", "intent", "claims", "next_question"}
    assert w["object_id"] == oid


def test_commit_unknown_object_is_a_clean_failure(loci_db, server_mod):
    r = _commit(server_mod, "nope-not-an-id")
    assert r["status"] == "unknown_object"
    assert r["lesson_id"] is None and r["claims_persisted"] == 0 and r["cursor_active"] is False
    assert r["prompt_to_user"]
    assert loci_db.counts() == {"place": 0, "object": 0, "lesson": 0, "claim": 0}


def test_commit_skips_malformed_claims(loci_db, server_mod):
    oid = _observe(server_mod)["object_id"]
    r = _commit(server_mod, oid, claims=[{"text": "ok"}, {"nope": 1}, {"text": "  "}])
    assert r["claims_persisted"] == 1
    assert r["claims_skipped"] == 2


def test_q_rewrites_placeholders_for_postgres(loci_db, monkeypatch):
    import db

    assert db.q("SELECT ?") == "SELECT ?"
    monkeypatch.setenv("LOCI_DATABASE_URL", "postgresql://u:p@h:5432/loci")
    assert db.q("SELECT ?, ?") == "SELECT %s, %s"
