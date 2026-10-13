"""Printable high-contrast visual short-code tags (30-symbol homoglyph-free Base32 + mod-30 checksum)."""

from __future__ import annotations

import hashlib
import html
from collections.abc import Iterable

# Excludes 0, O, 1, I, L, U so VLM transcription never confuses 0/O or 1/I/L.
CROCKFORD_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


def compute_check_char(three_chars: str) -> str:
    """Compute a weighted mod-30 check character over a 3-symbol prefix."""
    weights = (1, 3, 7)
    total = 0
    for idx, ch in enumerate(three_chars[:3]):
        pos = CROCKFORD_ALPHABET.find(ch)
        if pos < 0:
            pos = ord(ch) % len(CROCKFORD_ALPHABET)
        total += weights[idx] * pos
    return CROCKFORD_ALPHABET[total % len(CROCKFORD_ALPHABET)]


def verify_tag_checksum(raw_tag: str) -> bool:
    """Return True if a 4-symbol tag matches the mod-30 check character."""
    cleaned = "".join(ch for ch in raw_tag.upper() if ch.isalnum())
    if len(cleaned) != 4 or any(ch not in CROCKFORD_ALPHABET for ch in cleaned):
        return False
    return compute_check_char(cleaned[:3]) == cleaned[3]


def generate_tag_codes(
    *,
    space_id: str,
    count: int = 24,
    existing: Iterable[str] = (),
    prefix: str = "",
) -> list[str]:
    """Deterministically generate collision-free XX-XC visual tag codes for a space."""
    taken = {
        "".join(ch for ch in code.upper() if ch.isalnum()) for code in existing if code
    }
    clean_prefix = "".join(ch for ch in prefix.upper() if ch in CROCKFORD_ALPHABET)[:2]
    results: list[str] = []
    counter = 0
    base_len = len(CROCKFORD_ALPHABET)
    while len(results) < count:
        digest = hashlib.sha256(f"{space_id}:{counter}".encode("utf-8")).digest()
        counter += 1
        c0 = (
            clean_prefix[0]
            if len(clean_prefix) >= 1
            else CROCKFORD_ALPHABET[digest[0] % base_len]
        )
        c1 = (
            clean_prefix[1]
            if len(clean_prefix) >= 2
            else CROCKFORD_ALPHABET[digest[1] % base_len]
        )
        c2 = CROCKFORD_ALPHABET[digest[2] % base_len]
        body = f"{c0}{c1}{c2}"
        c3 = compute_check_char(body)
        compact = f"{body}{c3}"
        if compact in taken:
            continue
        taken.add(compact)
        results.append(f"{c0}{c1}-{c2}{c3}")
    return results


def render_label_sheet_svg(
    *,
    space_name: str,
    labels: list[dict[str, str]],
    columns: int = 3,
) -> str:
    """Render a high-contrast industrial sticker sheet as standalone SVG."""
    col_count = max(1, columns)
    card_w = 240
    card_h = 110
    pad_x = 20
    pad_y = 20
    header_h = 56
    rows = max(1, (len(labels) + col_count - 1) // col_count)
    width = pad_x * 2 + col_count * card_w + (col_count - 1) * 16
    height = header_h + pad_y * 2 + rows * card_h + (rows - 1) * 16

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{pad_x}" y="34" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
        f'font-size="16" font-weight="700" fill="#0f172a">'
        f"0xL0C1 PHYSICAL MEMORY TAGS — {html.escape(space_name)}</text>",
        f'<text x="{pad_x}" y="52" font-family="system-ui, sans-serif" font-size="11" fill="#475569">'
        "Zero-OCR VLM fiducial short codes (point Claude, ChatGPT, or Gemini camera at tag to resume)</text>",
    ]

    for idx, item in enumerate(labels):
        r = idx // col_count
        c = idx % col_count
        x = pad_x + c * (card_w + 16)
        y = header_h + pad_y + r * (card_h + 16)
        raw_code = item.get("tag_code", "K9-4B")
        formatted = (
            f"{raw_code[:2]}-{raw_code[2:]}"
            if len(raw_code) == 4 and "-" not in raw_code
            else raw_code
        )
        obj_label = (item.get("label") or "Unassigned Object")[:28]
        place_label = (item.get("place") or "Bind on first observe")[:32]
        parts.extend(
            [
                f'<g transform="translate({x},{y})">',
                f'<rect x="2" y="2" width="{card_w - 4}" height="{card_h - 4}" rx="8" '
                'fill="#ffffff" stroke="#000000" stroke-width="4"/>',
                '<rect x="10" y="10" width="54" height="18" rx="3" fill="#000000"/>',
                '<text x="37" y="23" text-anchor="middle" '
                'font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
                'font-size="11" font-weight="800" fill="#ffffff">0xL0C1</text>',
                f'<text x="{card_w // 2}" y="64" text-anchor="middle" '
                'font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
                'font-size="36" font-weight="900" letter-spacing="2" fill="#000000">'
                f"{html.escape(formatted)}</text>",
                f'<text x="14" y="86" font-family="system-ui, sans-serif" '
                f'font-size="12" font-weight="700" fill="#0f172a">{html.escape(obj_label)}</text>',
                f'<text x="14" y="100" font-family="system-ui, sans-serif" '
                f'font-size="10" fill="#475569">{html.escape(place_label)}</text>',
                "</g>",
            ]
        )

    parts.append("</svg>")
    return "\n".join(parts)
