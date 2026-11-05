"""Pure handoff gate metrics shared by db.list_all_spaces and server._state_for_space."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

INTERNAL_DOMAINS: tuple[str, ...] = (
    "lincinnovations.com",
    "lincforge.com",
    "sniperpulse.com",
    "lincspace.ai",
)

SYNTHETIC_TLDS: tuple[str, ...] = (
    "example",
    "test",
    "invalid",
    "localhost",
)

SYNTHETIC_DOMAINS: tuple[str, ...] = (
    "example.com",
    "example.net",
    "example.org",
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


def parse_internal_domains(
    raw: str | Iterable[str] | None = None,
) -> set[str]:
    """Parse comma-separated internal domains (defaults to env LOCI_GATE_INTERNAL_DOMAINS),
    extending INTERNAL_DOMAINS without ever replacing the defaults.
    """
    source = os.environ.get("LOCI_GATE_INTERNAL_DOMAINS", "") if raw is None else raw
    parts: Iterable[str] = source.split(",") if isinstance(source, str) else source
    out: set[str] = set(INTERNAL_DOMAINS)
    for item in parts:
        cleaned = str(item).strip().lower().lstrip(".")
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


def _is_synthetic_domain(domain: str) -> bool:
    """True for RFC 2606 / RFC 6761 reserved TLDs and documentation domains."""
    if any(domain == tld or domain.endswith(f".{tld}") for tld in SYNTHETIC_TLDS):
        return True
    return any(domain == syn or domain.endswith(f".{syn}") for syn in SYNTHETIC_DOMAINS)


def _is_internal_domain(
    domain: str,
    internal_domains: Iterable[str] = INTERNAL_DOMAINS,
) -> bool:
    return any(
        domain == internal or domain.endswith(f".{internal}")
        for internal in internal_domains
    )


def is_synthetic_subject(subject: str | None) -> bool:
    """Return True if subject is an attributable email under a reserved/synthetic domain."""
    cleaned = normalize_subject(subject)
    if not _is_attributable_email(cleaned):
        return False
    return _is_synthetic_domain(cleaned.rpartition("@")[2])


def is_internal_subject(
    subject: str | None,
    *,
    drill_subjects: str | Iterable[str] | None = None,
    internal_domains: str | Iterable[str] | None = None,
) -> bool:
    """Return True if subject is excluded from external handoffs.

    Excluded: empty, 'anonymous', drill-listed, unattributable (no '@', empty local part
    or empty domain, e.g. machine clients 'service-client' / 'oauth-user'), reserved/synthetic
    RFC 2606/6761 domains, or under an internal domain (exact match or subdomain).
    Unknown attribution is never external.
    """
    cleaned = normalize_subject(subject)
    if not cleaned or cleaned == "anonymous":
        return True
    if cleaned in parse_drill_subjects(drill_subjects):
        return True
    if not _is_attributable_email(cleaned):
        return True
    domain = cleaned.rpartition("@")[2]
    if _is_synthetic_domain(domain):
        return True
    return _is_internal_domain(domain, parse_internal_domains(internal_domains))


def _coerce_obj_attr(raw_item: Any) -> dict[str, Any]:
    """Safely coerce an attrs entry (mapping or JSON string) into a dict without raising."""
    if isinstance(raw_item, str):
        try:
            parsed = json.loads(raw_item)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw_item, Mapping):
        return {}
    out: dict[str, Any] = {}
    nested_raw = raw_item.get("attrs_json")
    if isinstance(nested_raw, Mapping):
        out.update(nested_raw)
    elif isinstance(nested_raw, str) and nested_raw:
        try:
            parsed_nested = json.loads(nested_raw)
            if isinstance(parsed_nested, Mapping):
                out.update(parsed_nested)
        except (ValueError, TypeError):
            pass
    for k, v in raw_item.items():
        if k != "attrs_json":
            out[k] = v
    return out


def is_drill_object(raw_attr: Any) -> bool:
    """Return True only when attrs carry JSON boolean drill=true."""
    return _coerce_obj_attr(raw_attr).get("drill") is True


def _safe_nonneg_int(val: Any) -> int:
    if val is None or isinstance(val, bool):
        return 0
    try:
        return max(0, int(val))
    except (ValueError, TypeError):
        return 0


def compute_handoff_metrics(
    lessons: Sequence[Mapping[str, Any]],
    obj_attrs: Sequence[Mapping[str, Any]] = (),
    *,
    drill_subjects: str | Iterable[str] | None = None,
    internal_domains: str | Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compute cross-actor and external handoff gate metrics for a Space."""
    drill_set = parse_drill_subjects(drill_subjects)
    internal_domain_set = parse_internal_domains(internal_domains)

    distinct_lesson_oids: list[str] = []
    seen_oids: set[str] = set()
    for les in lessons:
        if not isinstance(les, Mapping):
            continue
        oid_candidate = str(les.get("object_id") or "").strip()
        if oid_candidate and oid_candidate not in seen_oids:
            seen_oids.add(oid_candidate)
            distinct_lesson_oids.append(oid_candidate)

    raw_items: Iterable[Any]
    if isinstance(obj_attrs, (str, bytes, Mapping)):
        raw_items = [obj_attrs]
    elif isinstance(obj_attrs, Iterable):
        raw_items = obj_attrs
    else:
        raw_items = ()

    ask_count = 0
    cross_actor_ask_count = 0
    drill_object_count = 0
    drill_object_ids: set[str] = set()

    for idx, raw_item in enumerate(raw_items):
        coerced = _coerce_obj_attr(raw_item)
        ask_count += _safe_nonneg_int(coerced.get("ask_count"))
        cross_actor_ask_count += _safe_nonneg_int(coerced.get("cross_actor_ask_count"))
        if coerced.get("drill") is True:
            drill_object_count += 1
            oid = str(coerced.get("object_id") or coerced.get("id") or "").strip()
            if not oid and idx < len(distinct_lesson_oids):
                oid = distinct_lesson_oids[idx]
            if oid:
                drill_object_ids.add(oid)

    authors_by_obj: dict[str, set[str]] = {}
    external_authors_by_obj: dict[str, set[str]] = {}
    excluded_subjects: set[str] = set()
    synthetic_subjects: set[str] = set()

    for les in lessons:
        if not isinstance(les, Mapping):
            continue
        oid_str = str(les.get("object_id") or "").strip()
        subj_str = str(les.get("author_subject") or "").strip()
        norm_subj = normalize_subject(subj_str)
        if is_synthetic_subject(subj_str):
            synthetic_subjects.add(norm_subj)
        internal = is_internal_subject(
            subj_str,
            drill_subjects=drill_set,
            internal_domains=internal_domain_set,
        )
        if internal:
            excluded_subjects.add(norm_subj)
        if oid_str and subj_str:
            authors_by_obj.setdefault(oid_str, set()).add(subj_str)
            if not internal and oid_str not in drill_object_ids:
                external_authors_by_obj.setdefault(oid_str, set()).add(norm_subj)

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
        "synthetic_subject_count": len(synthetic_subjects),
        "drill_object_count": drill_object_count,
        "external_ask_attribution": "unavailable",
    }
