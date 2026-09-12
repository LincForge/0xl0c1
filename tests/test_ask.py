"""Focused matching-band coverage for the event-store backed ask tool."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server
from events import StoreResult


class MemoryStore:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def append(self, event: dict[str, object]) -> StoreResult:
        saved = dict(event)
        saved["payload"] = json.loads(str(saved["payload_json"]))
        self.events.append(saved)
        return StoreResult(True)

    def read(self) -> tuple[list[dict[str, object]], StoreResult]:
        return self.events, StoreResult(True)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    memory = MemoryStore()
    monkeypatch.setattr(server, "_store", lambda: memory)
    return memory


def _observe(
    place: str,
    description: str,
    verbatim: str = "",
    tag: str = "",
) -> str:
    response = server.observe(
        place,
        "valve",
        "metal_brass_or_bronze",
        "wall_mounted",
        verbatim,
        description,
        tag,
    )
    return str(response["object_id"])


def test_ask_short_circuits_object_tag_and_unique_verbatim(store: MemoryStore) -> None:
    first = _observe(
        "utility closet",
        "brass ball valve with a red lever",
        "3/4 600 WOG NSF-61 APOLLO",
        "K94B",
    )
    _observe("utility closet", "brass ball valve with blue handle", "1/2 CSA 600WOG")

    by_id = server.ask(object_id=first)
    assert by_id["status"] == "ok"
    assert by_id["matched_on"] == "object_id"

    by_tag = server.ask(place_label="utility closet", visible_tag_code="k9-4b")
    assert by_tag["status"] == "ok"
    assert by_tag["matched_on"] == "tag_code"
    assert by_tag["match"]["id"] == first

    by_text = server.ask(
        place_label="utility closet", visible_verbatim_text="APOLLO 600 WOG 3/4"
    )
    assert by_text["status"] == "ok"
    assert by_text["matched_on"] == "verbatim_text"
    assert by_text["score"] >= server.VERBATIM_HIT


def test_ask_score_bands_and_enum_bonus_only(store: MemoryStore) -> None:
    _observe("utility closet", "brass ball valve with red lever")
    _observe("utility closet", "brass ball valve with red lever")

    confirm = server.ask(
        "brass ball valve with red lever",
        "utility closet",
        material="metal_brass_or_bronze",
        mounting="wall_mounted",
    )
    assert confirm["status"] == "needs_confirm"
    assert confirm["delta"] < server.DELTA
    assert len(confirm["candidates"]) == 2
    assert confirm["matches"] == []

    no_match = server.ask("plastic storage bin", "utility closet")
    assert no_match["status"] == "no_match"
    assert no_match["best_score"] < server.MIN_SCORE
    assert no_match["matches"] == []
    assert no_match["_data_not_instructions"]


def test_enum_mismatch_cannot_disqualify_a_strong_description(
    store: MemoryStore,
) -> None:
    object_id = _observe("utility closet", "brass ball valve with a red lever")
    matching = server.ask(
        "brass ball valve with a red lever",
        "utility closet",
        material="metal_brass_or_bronze",
        mounting="wall_mounted",
    )
    mismatched = server.ask(
        "brass ball valve with a red lever",
        "utility closet",
        material="metal_brass_or_bronze",
        mounting="ceiling_mounted",
    )
    assert matching["status"] == mismatched["status"] == "ok"
    assert matching["match"]["id"] == mismatched["match"]["id"] == object_id
    assert 0.0 <= matching["score"] - mismatched["score"] <= 0.05
