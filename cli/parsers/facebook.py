"""Facebook parser: "Download Your Information" friends JSON -> ``ParsedRecord``s.

The thinnest source: each friend is **just a name and a connected-on timestamp** — no email, phone,
URL, or id. So a friend maps to ``FN`` + ``X-CONNECTED-ON`` (the connect date), and identity falls
all the way to a content hash over name + date + source (INV-5). This is the "reconciliation
checklist" case — kept as a first-class, origin-tagged contact, never silently merged (AC-PRM-B).

The friends list lives under ``friends`` (older) or ``friends_v2`` (current DYI). Real exports carry
a notorious **mojibake** bug: Facebook emits each UTF-8 byte as its own ``\\u00XX`` escape, so a JSON
parser yields e.g. ``"JosÃ©"`` for "José". We repair per name with the community fix —
``name.encode('latin-1').decode('utf-8')`` — applied only when it round-trips cleanly, so already-clean
names (accented or CJK) are left untouched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .base import ParsedRecord, RawField


def _repair_mojibake(name: str) -> str:
    """Undo Facebook's UTF-8-byte-as-\\u00XX corruption; a no-op on already-valid names.

    Mojibake ("JosÃ©") is latin-1-encodable and decodes as valid UTF-8 -> repaired. A clean name with
    a real accent ("José", U+00E9) encodes to a single byte that is *not* valid UTF-8 -> kept. A CJK
    name (code points > 255) isn't latin-1-encodable -> kept. ASCII is unchanged.
    """
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _friends_list(data) -> list:
    if isinstance(data, dict):
        for key in ("friends_v2", "friends"):
            if isinstance(data.get(key), list):
                return data[key]
        for key, value in data.items():           # any other "friends"-ish list
            if "friend" in key.lower() and isinstance(value, list):
                return value
    raise ValueError("no friends list found in Facebook JSON (expected a 'friends'/'friends_v2' key)")


def parse(raw, source: str) -> list[ParsedRecord]:
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
    friends = _friends_list(json.loads(text))

    records: list[ParsedRecord] = []
    for entry in friends:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue
        fields = [RawField("FN", _repair_mojibake(name))]
        ts = entry.get("timestamp")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            connected = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
            fields.append(RawField("X-CONNECTED-ON", connected))
        records.append(ParsedRecord(source=source, fields=fields,
                                     raw_text=json.dumps(entry, ensure_ascii=False)))
    return records
