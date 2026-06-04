"""Source parsers: bytes -> source-native ``ParsedRecord``s.

A parser's only job is to turn a source file into a list of library-agnostic ``ParsedRecord``s —
one per record, each a flat list of ``RawField``s. Mapping those to the canonical jCard, capturing
provenance, and assigning a stable id all happen in ``cli.normalize`` (one layer up), so the choice
of parsing library never leaks past this package.

Implemented: vCard (``vcard``), Google Takeout (``takeout``, a zip of vCards), and vendor CSV
(``csv`` — LinkedIn + Google CSV, dialect-detected from the header). Planned next: Facebook JSON.
"""

from .base import ParsedRecord, RawField, SourceFormat, detect_format

__all__ = ["ParsedRecord", "RawField", "SourceFormat", "detect_format"]
