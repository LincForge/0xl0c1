"""Ambiguous Sheets append-only event store and Loci's event read model.

The provider boundary deliberately knows nothing about MCP.  Keeping it small makes the
real HTTP client replaceable by a deterministic fake in tests and prevents credentials
from leaking into tool responses.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import httpx

EVENT_COLUMNS = (
    "schema_version",
    "event_id",
    "event_type",
    "recorded_at",
    "object_id",
    "place_label",
    "canonical_class",
    "material",
    "mounting",
    "visible_verbatim_text",
    "visible_tag_code",
    "user_label",
    "payload_json",
)


class Transport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


@dataclass(frozen=True)
class StoreResult:
    ok: bool
    error: str | None = None


class AmbiguousEventStore:
    """A narrow, fail-closed adapter for one configured Ambiguous workbook."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        sheet_id: str | None = None,
        base_url: str | None = None,
        transport: Transport | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.environ.get("AMBIGUOUS_API_KEY")
        )
        self.sheet_id = (
            sheet_id
            if sheet_id is not None
            else os.environ.get("LOCI_AMBIGUOUS_SHEET_ID")
        )
        self.base_url = (
            base_url
            or os.environ.get("AMBIGUOUS_API_BASE")
            or "https://app.ambiguous.ai/api"
        ).rstrip("/")
        self.transport = transport or httpx.Client()
        self.timeout = timeout

    def _configuration_error(self) -> str | None:
        if not self.api_key:
            return "ambiguous_not_configured"
        if not self.sheet_id:
            return "ambiguous_sheet_not_configured"
        return None

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.transport.request(
            method,
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            timeout=self.timeout,
            **kwargs,
        )

    def append(self, event: dict[str, Any]) -> StoreResult:
        if error := self._configuration_error():
            return StoreResult(False, error)
        row = [event.get(column, "") for column in EVENT_COLUMNS]
        try:
            response = self._request(
                "POST", f"/sheets/{self.sheet_id}/values/append", json={"values": [row]}
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError, OSError):
            return StoreResult(False, "ambiguous_append_failed")
        if not isinstance(body, dict):
            return StoreResult(False, "ambiguous_invalid_append_response")
        return StoreResult(True)

    def read(self) -> tuple[list[dict[str, Any]] | None, StoreResult]:
        if error := self._configuration_error():
            return None, StoreResult(False, error)
        try:
            response = self._request("GET", f"/sheets/{self.sheet_id}/data")
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError, OSError):
            return None, StoreResult(False, "ambiguous_read_failed")
        rows = _event_rows(body)
        if rows is None:
            return None, StoreResult(False, "ambiguous_invalid_read_response")
        return rows, StoreResult(True)


def new_event(event_type: str, object_id: str, **fields: Any) -> dict[str, Any]:
    """Make a schema-versioned immutable event with a client-generated id."""
    payload = fields.pop("payload", {})
    return {
        "schema_version": "1",
        "event_id": str(uuid4()),
        "event_type": event_type,
        "recorded_at": datetime.now(UTC).isoformat(),
        "object_id": object_id,
        "place_label": fields.pop("place_label", ""),
        "canonical_class": fields.pop("canonical_class", ""),
        "material": fields.pop("material", ""),
        "mounting": fields.pop("mounting", ""),
        "visible_verbatim_text": fields.pop("visible_verbatim_text", ""),
        "visible_tag_code": fields.pop("visible_tag_code", ""),
        "user_label": fields.pop("user_label", ""),
        "payload_json": json.dumps({"version": 1, **payload}, separators=(",", ":")),
    }


def _event_rows(body: Any) -> list[dict[str, Any]] | None:
    """Accept the documented structured workbook form plus common table wrappers."""
    if not isinstance(body, dict):
        return None
    raw: Any = body.get("data", body.get("values", body.get("sheets")))
    if isinstance(raw, dict):
        raw = raw.get("Events", raw.get("events", raw.get("values", raw.get("rows"))))
    if not isinstance(raw, list):
        return None
    if raw and isinstance(raw[0], dict):
        candidates = raw
    elif raw and isinstance(raw[0], list):
        header, *values = raw
        if not isinstance(header, list) or [str(item) for item in header] != list(
            EVENT_COLUMNS
        ):
            return None
        candidates = [dict(zip(EVENT_COLUMNS, value, strict=False)) for value in values]
    elif not raw:
        candidates = []
    else:
        return None
    events: list[dict[str, Any]] = []
    for row in candidates:
        if not isinstance(row, dict) or set(EVENT_COLUMNS) - set(row):
            return None
        if str(row["schema_version"]) != "1" or row["event_type"] not in {
            "object_observed",
            "lesson_committed",
        }:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        event: dict[str, Any] = {column: str(row[column]) for column in EVENT_COLUMNS}
        event["payload"] = payload
        events.append(event)
    return events


def project(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Fold immutable events into the one read model used by ask and the viewer."""
    objects: dict[str, dict[str, Any]] = {}
    lessons: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for event in events:
        if event["event_type"] == "object_observed":
            objects[event["object_id"]] = {
                "id": event["object_id"],
                "label": event["user_label"] or event["canonical_class"],
                "place": event["place_label"],
                "tag_code": event["visible_tag_code"],
                "verbatim_text": event["visible_verbatim_text"],
                "canonical_class": event["canonical_class"],
                "material": event["material"],
                "mounting": event["mounting"],
                "description": str(event["payload"].get("description", "")),
            }
        elif event["event_type"] == "lesson_committed":
            payload = event["payload"]
            lesson = {
                "id": event["event_id"],
                "title": payload.get("title", ""),
                "object_id": event["object_id"],
                "object": objects.get(event["object_id"], {}).get("label", ""),
                "next_question": payload.get("next_question", ""),
                "intent": payload.get("intent", ""),
                "is_cursor_active": bool(payload.get("next_question")),
            }
            lessons.append(lesson)
            for claim in payload.get("claims", []):
                if isinstance(claim, dict):
                    claims.append(
                        {
                            "id": str(uuid4()),
                            "lesson_id": event["event_id"],
                            "text": str(claim.get("text", "")),
                            "confidence": claim.get("confidence", ""),
                            "status": "untrusted",
                        }
                    )
    places = [
        {"id": label, "label": label, "path": label}
        for label in sorted({obj["place"] for obj in objects.values() if obj["place"]})
    ]
    return {
        "places": places,
        "objects": list(objects.values()),
        "lessons": lessons,
        "claims": claims,
    }
