"""Pure handoff gate metrics shared by db.list_all_spaces and server._state_for_space."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

INTERNAL_DOMAINS: tuple[str, ...] = (
    "lincinnovations.com",
    "lincforge.com",
    "sniperpulse.com",
)


def parse_drill_subjects(
    raw: str | Iterable[str] | None = None,
) -> set[str]:
    """Parse comma-separated drill subjects (defaults to env LOCI_GATE_DRILL_SUBJECTS)."""
    source = os.environ.get("LOCI_GATE_DRILL_SUBJECTS", "") if raw is None else raw
    parts: Iterable[str] = source.split(",") if isinstance(source, str) else source
    out: set[str] = set()
    for item in parts:
        cleaned = str(item).strip().lower()
        if cleaned:
            out.add(cleaned)
    return out


def normalize_subject(subject: str | None) -> str:
    """Canonical author key: stripped and lower-cased (emails are case-insensitive here)."""
    return str(subject or "").strip().lower()


def _is_attributable_email(cleaned: str) -> bool:
    """True only for 'local@domain' with a non-empty local part and domain."""
    local, sep, domain = cleaned.rpartition("@")
    return bool(sep and local and domain)


def _is_internal_domain(domain: str) -> bool:
    return any(
        domain == internal or domain.endswith(f".{internal}")
        for internal in INTERNAL_DOMAINS
    )


def is_internal_subject(
    subject: str | None,
    *,
    drill_subjects: str | Iterable[str] | None = None,
) -> bool:
    """Return True if subject is excluded from external handoffs.

    Excluded: empty, 'anonymous', drill-listed, unattributable (no '@', empty local part
    or empty domain, e.g. machine clients 'service-client' / 'oauth-user'), or under an
    internal domain (exact match or subdomain). Unknown attribution is never external.
    """
    cleaned = normalize_subject(subject)
    if not cleaned or cleaned == "anonymous":
        return True
    if cleaned in parse_drill_subjects(drill_subjects):
        return True
    if not _is_attributable_email(cleaned):
        return True
    return _is_internal_domain(cleaned.rpartition("@")[2])


def compute_handoff_metrics(
    lessons: Sequence[Mapping[str, Any]],
    obj_attrs: Sequence[Mapping[str, Any]] = (),
    *,
    drill_subjects: str | Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compute cross-actor and external handoff gate metrics for a Space."""
    drill_set = parse_drill_subjects(drill_subjects)
    ask_count = sum(int(a.get("ask_count") or 0) for a in obj_attrs)
    cross_actor_ask_count = sum(
        int(a.get("cross_actor_ask_count") or 0) for a in obj_attrs
    )

    authors_by_obj: dict[str, set[str]] = {}
    external_authors_by_obj: dict[str, set[str]] = {}
    excluded_subjects: set[str] = set()

    for les in lessons:
        oid_str = str(les.get("object_id") or "").strip()
        subj_str = str(les.get("author_subject") or "").strip()
        internal = is_internal_subject(subj_str, drill_subjects=drill_set)
        if internal:
            excluded_subjects.add(normalize_subject(subj_str))
        if oid_str and subj_str:
            authors_by_obj.setdefault(oid_str, set()).add(subj_str)
            if not internal:
                external_authors_by_obj.setdefault(oid_str, set()).add(
                    normalize_subject(subj_str)
                )

    cross_commit_count = sum(
        max(0, len(authors) - 1) for authors in authors_by_obj.values()
    )
    external_commit_count = sum(
        max(0, len(authors) - 1) for authors in external_authors_by_obj.values()
    )
    cross_actor_handoff_count = cross_actor_ask_count + cross_commit_count

    return {
        "ask_count": ask_count,
        "cross_actor_handoff_count": cross_actor_handoff_count,
        "handoff_count": cross_actor_handoff_count,
        "external_handoff_count": external_commit_count,
        "excluded_subject_count": len(excluded_subjects),
        "external_ask_attribution": "unavailable",
    }
