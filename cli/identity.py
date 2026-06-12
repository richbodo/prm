"""Stable contact identity (INV-5): a key that survives re-import and cross-source merge.

Most real sources carry no ``UID`` (a validated fact — 0 of 1000 in a real Google Takeout export),
so identity is assigned by a **fallback chain**, walking down until a key exists:

    UID  ->  normalized email  ->  source permalink (e.g. LinkedIn profile URL)  ->  content hash

The content hash (over name + org + a phone + the source) is the last resort for the thinnest
records — Facebook name-only friends, name-less phone-only cards. It is deliberately *fragile*: if
the name changes (or a source fixes a mojibake bug) the hash changes and re-import creates a new
contact instead of re-attaching; the dedup/review layer compensates (see the v0.1 plan §5).

**Identity assignment is NOT cross-source merge.** Two records that hash differently but are the
same person are reconciled by the propose -> review -> apply dedup flow, never auto-merged here
(AC-PRM-B). This module only assigns each *source record* a deterministic, re-import-stable key.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .jcard import JCard


@dataclass(frozen=True)
class StableKey:
    """A stable identity key. ``kind`` records which rung of the fallback chain produced it."""

    kind: str          # "uid" | "email" | "url" | "hash"
    value: str

    def as_str(self) -> str:
        return f"{self.kind}:{self.value}"

    @classmethod
    def from_str(cls, text: str) -> "StableKey":
        """Reconstruct a key from its ``as_str()`` form (``kind:value``). Used by the raw-backup
        restore to preserve the *stored* key verbatim rather than re-deriving it — so a backup
        re-imports to the exact same ``source_record_id``. Splits on the first ``:`` (a ``uid`` value
        may itself contain one)."""
        kind, _, value = text.partition(":")
        return cls(kind=kind, value=value)


def normalize_email(addr: str) -> str:
    return addr.strip().lower()


def normalize_url(url: str) -> str:
    u = url.strip()
    if u.endswith("/"):
        u = u[:-1]
    return u.lower()


def _first_value(jcard: JCard, name: str) -> str | None:
    prop = jcard.get(name)
    if not prop or prop.value in (None, ""):
        return None
    return str(prop.value)


def stable_key(jcard: JCard, source: str) -> StableKey:
    """Compute the stable key for a canonical contact via the fallback chain."""
    uid = _first_value(jcard, "uid")
    if uid and uid.strip():
        return StableKey("uid", uid.strip())

    email = _first_value(jcard, "email")
    if email and email.strip():
        return StableKey("email", normalize_email(email))

    url = _first_value(jcard, "url")
    if url and url.strip():
        return StableKey("url", normalize_url(url))

    return StableKey("hash", _content_hash(jcard, source))


def _content_hash(jcard: JCard, source: str) -> str:
    """SHA-1 over the strongest weak signals we have, namespaced by source.

    Basis = display name + organization + connected-on date + first phone + source. The connected-on
    date matters for the thinnest real records (a LinkedIn connection with no name and no profile URL
    — a deactivated profile — is identified only by company + the date you connected). Joined with a
    unit separator so two distinct field layouts can't collide by concatenation.
    """
    fn = _first_value(jcard, "fn") or ""
    org = jcard.get("org")
    org_text = ";".join(str(v) for v in org.value) if org and isinstance(org.value, list) else (
        str(org.value) if org else ""
    )
    connected = _first_value(jcard, "x-connected-on") or ""
    tel = _first_value(jcard, "tel") or ""
    basis = "\x1f".join([fn.strip().lower(), org_text.strip().lower(), connected.strip(), tel.strip(), source])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()
