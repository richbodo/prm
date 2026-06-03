"""Per-field provenance (AC-17 / INV-7): where every value came from, and when.

Each canonical field value records the source it came from and an ``observed_at`` timestamp. This is
the sidecar to the jCard: the jCard is *what* the contact is; provenance is *where each piece came
from*. It maps directly to shared.db's ``field_provenance(source_record_id, field, value,
observed_at)``.

``observed_at`` is the source's own timestamp when it has one (vCard ``REV``, a LinkedIn
``Connected On`` date, a Facebook friend timestamp); otherwise it is left ``None`` and the loader
fills the ingestion time.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldProvenance:
    """One provenance record: this ``field`` had this ``value``, per ``source``, as of ``observed_at``."""

    field: str                       # canonical (jCard) property name, e.g. "email", "tel"
    value: str                       # string form of the value (structured values are ";"-joined)
    source: str                      # source identifier, e.g. "google_takeout", "apple_icloud"
    observed_at: str | None = None   # source-native timestamp (ISO-ish) or None -> filled at load


def value_to_text(value) -> str:
    """Flatten a jCard value (scalar or structured/multi array) to a provenance string."""
    if isinstance(value, list):
        return ";".join("" if v is None else str(v) for v in value)
    return "" if value is None else str(value)
