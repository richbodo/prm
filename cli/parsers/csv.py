"""Vendor CSV parser: LinkedIn `Connections.csv` and Google Contacts "Google CSV".

These are two different shapes, so the parser **detects the dialect from the header row** (the
provenance `source` label is passed through but not trusted for mapping):

- **LinkedIn** — a 3-line `Notes:` preamble, then `First Name,Last Name,URL,Email Address,Company,
  Position,Connected On`. Email is present for ~2% of rows; ~2% have no name *and* no URL (deactivated
  profiles) — the thinnest real records, identified only by company + the connected-on date.
- **Google CSV** — a wide, sparse header (`Name`, split `Given/Family Name`, numbered typed
  `E-mail N`/`Phone N`, `Organization 1 - …`, ` ::: `-joined `Group Membership`). No id column.

The whole file is parsed with the stdlib reader (so quoted multi-line fields and the LinkedIn
preamble are handled correctly), then the header row is found by content. Output is library-agnostic
`ParsedRecord`s — mapping to canonical jCard happens in `cli.normalize`, same as the vCard path.
"""

from __future__ import annotations

import csv
import io

from .base import ParsedRecord, RawField

GROUP_SEPARATOR = ":::"          # Google joins group membership with " ::: "


def parse(raw, source: str) -> list[ParsedRecord]:
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
    rows = list(csv.reader(io.StringIO(text)))
    for i, row in enumerate(rows):
        if not row:
            continue
        head = row[0].strip()
        if head == "First Name" and "URL" in row:
            return _parse_linkedin(row, rows[i + 1:], source)
        if head == "Name" and "Given Name" in row:
            return _parse_google(row, rows[i + 1:], source)
    raise ValueError("unrecognized CSV: no LinkedIn or Google Contacts header found")


def _nonblank(row) -> bool:
    return any(c.strip() for c in row)


# --------------------------------------------------------------------------- LinkedIn
def _parse_linkedin(header, data, source: str) -> list[ParsedRecord]:
    col = {name: idx for idx, name in enumerate(header)}

    def cell(row, name):
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    records: list[ParsedRecord] = []
    for row in data:
        if not _nonblank(row):
            continue
        first, last = cell(row, "First Name"), cell(row, "Last Name")
        url, email = cell(row, "URL"), cell(row, "Email Address")
        company, position = cell(row, "Company"), cell(row, "Position")
        connected = cell(row, "Connected On")

        fields: list[RawField] = []
        if first or last:
            fields.append(RawField("FN", f"{first} {last}".strip()))
            fields.append(RawField("N", [last, first, "", "", ""]))
        if url:
            fields.append(RawField("URL", url))
        if email:
            fields.append(RawField("EMAIL", email, params={"TYPE": ["INTERNET"]}))
        if company:
            fields.append(RawField("ORG", [company]))
        if position:
            fields.append(RawField("TITLE", position))
        if connected:
            fields.append(RawField("X-CONNECTED-ON", connected))

        records.append(ParsedRecord(source=source, fields=fields, raw_text=",".join(row)))
    return records


# --------------------------------------------------------------------------- Google CSV
def _parse_google(header, data, source: str) -> list[ParsedRecord]:
    col = {name: idx for idx, name in enumerate(header)}

    def cell(row, name):
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    def numbered(row, prefix):
        """Yield (type, value) for `prefix N - Value` columns until one is absent."""
        n = 1
        while f"{prefix} {n} - Value" in col:
            value = cell(row, f"{prefix} {n} - Value")
            if value:
                yield cell(row, f"{prefix} {n} - Type"), value
            n += 1

    records: list[ParsedRecord] = []
    for row in data:
        if not _nonblank(row):
            continue
        given, family = cell(row, "Given Name"), cell(row, "Family Name")
        additional, prefix, suffix = cell(row, "Additional Name"), cell(row, "Name Prefix"), cell(row, "Name Suffix")
        fn = cell(row, "Name") or f"{given} {family}".strip()

        fields: list[RawField] = []
        if fn:
            fields.append(RawField("FN", fn))
        if any((given, family, additional, prefix, suffix)):
            fields.append(RawField("N", [family, given, additional, prefix, suffix]))
        if cell(row, "Nickname"):
            fields.append(RawField("NICKNAME", cell(row, "Nickname")))
        for label, value in numbered(row, "E-mail"):
            params = {"TYPE": [label]} if label else {}
            fields.append(RawField("EMAIL", value, params=params))
        for label, value in numbered(row, "Phone"):
            params = {"TYPE": [label]} if label else {}
            fields.append(RawField("TEL", value, params=params))
        if cell(row, "Organization 1 - Name"):
            fields.append(RawField("ORG", [cell(row, "Organization 1 - Name")]))
        if cell(row, "Organization 1 - Title"):
            fields.append(RawField("TITLE", cell(row, "Organization 1 - Title")))
        if cell(row, "Website 1 - Value"):
            fields.append(RawField("URL", cell(row, "Website 1 - Value")))
        if cell(row, "Birthday"):
            fields.append(RawField("BDAY", cell(row, "Birthday")))
        if cell(row, "Notes"):
            fields.append(RawField("NOTE", cell(row, "Notes")))
        groups = [g.strip() for g in cell(row, "Group Membership").split(GROUP_SEPARATOR) if g.strip()]
        if groups:
            fields.append(RawField("CATEGORIES", groups))

        records.append(ParsedRecord(source=source, fields=fields, raw_text=",".join(row)))
    return records
