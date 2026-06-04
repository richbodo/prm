"""vCard parser: ``.vcf`` bytes/text -> ``ParsedRecord``s.

This is the **only** module that imports the vCard library. We use ``vobjectx`` (the maintained fork
of ``vobject``) for its lexer — line unfolding, ``itemN`` grouping, real-world quirk tolerance — and
fall back to the API-compatible ``vobject`` if the fork isn't installed. Field *semantics* are ours
(mapped in ``cli.normalize``); the library only tokenizes.

Inbound is vCard 3.0-dominant (Google Takeout, iCloud, Outlook all emit 3.0). Structured values
(``N``, ``ADR``, ``ORG``) and multi-valued ones (``CATEGORIES``) are flattened to plain ``str`` /
``list[str]`` here so nothing downstream sees a vobject object.
"""

from __future__ import annotations

from typing import Any

from .base import ParsedRecord, RawField

try:  # prefer the maintained fork; fall back to the API-compatible original
    import vobjectx as _vobject  # type: ignore
except ImportError:  # pragma: no cover - depends on which is installed
    import vobject as _vobject  # type: ignore


def _flatten_value(value: Any) -> Any:
    """vobject value -> plain str | list[str]. Handles Name/Address structs and lists."""
    # Structured name: N -> [family, given, additional, prefix, suffix]
    if hasattr(value, "family") and hasattr(value, "given"):
        return [value.family, value.given, value.additional, value.prefix, value.suffix]
    # Structured address: ADR -> [po-box, extended, street, locality, region, code, country]
    if hasattr(value, "street") and hasattr(value, "city"):
        return [value.box, value.extended, value.street, value.city, value.region, value.code, value.country]
    if isinstance(value, list):
        return [str(v) for v in value]
    return str(value)


def _line_to_field(line) -> RawField:
    params = {k.upper(): [str(v) for v in vals] for k, vals in line.params.items()}
    return RawField(
        name=line.name.upper(),
        value=_flatten_value(line.value),
        params=params,
        group=getattr(line, "group", None),
    )


def parse(raw: str | bytes, source: str) -> list[ParsedRecord]:
    """Parse one-or-many vCards into ``ParsedRecord``s tagged with ``source``."""
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    records: list[ParsedRecord] = []
    for card in _vobject.readComponents(text):
        fields = [_line_to_field(ln) for ln in card.lines()]
        try:
            raw_text = card.serialize()
        except Exception:  # serialization is best-effort; never block a parse on it
            raw_text = ""
        records.append(ParsedRecord(source=source, fields=fields, raw_text=raw_text))
    return records
