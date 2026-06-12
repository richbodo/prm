"""vCard 3.0 emitter: canonical jCard arrays -> RFC 2426 ``.vcf`` text.

The inverse of ``cli/parsers/vcard.py``: that module *lexes* real-world vCards into jCard; this one
*serializes* PRM's merged jCard back out to a portable ``.vcf``. **Dependency-free** — the mapping is
mechanical (RFC 7095 jCard <-> RFC 2426 vCard; see ``plans/v0.1-implementation-plan.md`` §5), so the
lexer's quirk-tolerance is only ever needed inbound, never here.

Emits vCard **3.0** — the most widely-importable version (Google, Apple, and Outlook all read it),
matching PRM's dominant inbound format. The input is the jCard array form
(``["vcard", [[name, params, type, *values], ...]]``) produced by ``core.projection.export_jcards``.
"""

from __future__ import annotations

from typing import Any, Iterable

_MAX_OCTETS = 75                       # RFC 2426 line length before folding
# vCard data-model properties this emitter never writes from source values: VERSION is fixed at 3.0
# below; X-ABLABEL is the vendor label-grouping noise the projection already drops.
_SKIP = {"version", "x-ablabel"}


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _escape(text: str) -> str:
    """Escape a vCard text value (RFC 2426 §5): backslash, newline, comma, semicolon."""
    return (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _format_value(values: list[Any]) -> str:
    """jCard value element(s) -> a vCard value string.

    A single structured value (``n``/``adr``/``org`` carry one list) joins its components with a
    *literal* ``;`` (each component escaped); a multi-valued property (``categories``) joins its
    elements with a literal ``,``; a scalar is escaped as-is.
    """
    if len(values) == 1 and isinstance(values[0], list):
        return ";".join(_escape(_as_str(c)) for c in values[0])
    if len(values) == 1:
        return _escape(_as_str(values[0]))
    return ",".join(_escape(_as_str(v)) for v in values)


def _format_params(params: dict[str, Any]) -> str:
    """jCard params object -> the ``;KEY=VAL`` segment. ``group`` is consumed as a line prefix, not a
    param; everything else (``type``, ``pref``, …) emits with an uppercased key."""
    out = []
    for key, val in (params or {}).items():
        if key == "group":
            continue
        vals = val if isinstance(val, list) else [val]
        out.append(f"{key.upper()}=" + ",".join(_as_str(v) for v in vals))
    return ("" if not out else ";" + ";".join(out))


def _fold(line: str) -> str:
    """Fold a logical line to <=75 octets per physical line (RFC 2426), continuations led by a space.
    Folds on character boundaries so a multi-byte UTF-8 sequence is never split."""
    physical, cur, cur_octets = [], "", 0
    for ch in line:
        octets = len(ch.encode("utf-8"))
        if cur_octets + octets > _MAX_OCTETS and cur:
            physical.append(cur)
            cur, cur_octets = " " + ch, 1 + octets       # continuation line begins with a space
        else:
            cur += ch
            cur_octets += octets
    physical.append(cur)
    return "\r\n".join(physical)


def _format_line(prop: list[Any]) -> str:
    """One jCard property array -> one (unfolded) vCard content line."""
    name, params, _type, *values = prop
    group = (params or {}).get("group")
    head = (f"{group}." if group else "") + name.upper() + _format_params(params)
    return f"{head}:{_format_value(values)}"


def _card(properties: Iterable[list[Any]]) -> str:
    """One contact's properties -> a ``BEGIN:VCARD … END:VCARD`` block (CRLF-terminated, folded)."""
    props = list(properties)
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    has_fn = False
    for prop in props:
        if prop[0].lower() in _SKIP:
            continue
        if prop[0].lower() == "fn":
            has_fn = True
        lines.append(_format_line(prop))
    if not has_fn:
        lines.insert(2, "FN:")          # vCard 3.0 requires FN; emit an empty one for a name-less contact
    lines.append("END:VCARD")
    return "".join(_fold(line) + "\r\n" for line in lines)


def write_vcards(jcards: Iterable[list[Any]]) -> str:
    """Serialize an iterable of jCard arrays (``["vcard", [props]]``) to a single vCard 3.0 document."""
    out = []
    for jc in jcards:
        if not (isinstance(jc, list) and len(jc) == 2 and jc[0] == "vcard"):
            raise ValueError("not a jCard array: expected ['vcard', [...]]")
        out.append(_card(jc[1]))
    return "".join(out)
