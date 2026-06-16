"""core/suggest.py — suggest which contact a loose image file belongs to (R7c photo matcher).

The guided photo matcher (workspace) walks a folder of loose images one at a time and asks the user to
confirm a contact. This module produces the *suggestion*: it maps a **filename** to candidate contacts
using the **same identity normalizers the dedup engine uses** (``core.candidates``) — an email in the
filename matches a contact's normalized email; otherwise the cleaned name is folded + tokenized (incl.
the nickname table) and scored against every contact. Reusing that machinery is the point: matching a
filename to a contact is the same problem as matching a record to a contact.

Read-only leaf over ``core.projection`` + ``core.candidates``. Returns a **ranked** list; the user always
decides (the matcher never auto-assigns)."""

from __future__ import annotations

import re
from pathlib import Path

from core import candidates, projection

_COLLISION = re.compile(r"\(\d+\)$")               # a trailing "(3)" copy suffix
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")     # AdaLovelace -> Ada Lovelace
_MIN_FUZZY = 0.82                                  # difflib ratio floor for a fuzzy name suggestion


def _clean_stem(filename: str) -> str:
    return _COLLISION.sub("", Path(filename).stem).strip()


def _name_query(stem: str) -> str:
    """A filename stem → a name-ish query: split camelCase, turn `_`/`-`/`.` into spaces, collapse."""
    s = _CAMEL.sub(" ", stem)
    s = re.sub(r"[._\-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _score_name(q_tokens: set, q_fold: str, m: dict) -> tuple[float, str]:
    """Score a filename's name tokens against a contact's ``_matchable``. Exact token-set > subset
    (a surname-only or extra-token match) > a high difflib ratio; else 0 (not a candidate)."""
    m_tokens = set(m["tokens"])
    if q_tokens and m_tokens:
        if q_tokens == m_tokens:
            return 1.0, "name"
        if q_tokens <= m_tokens or m_tokens <= q_tokens:
            return 0.8, "partial"
    ratio = candidates._ratio(q_fold, m["fold"])
    return (round(ratio, 3), "fuzzy") if ratio >= _MIN_FUZZY else (0.0, "")


def suggest_for_filename(home, filename: str, *, limit: int = 5) -> list[dict]:
    """Ranked candidate contacts for a loose image ``filename`` (extension/`(N)` stripped). An email in
    the name → exact normalized-email match (the strong signal); otherwise a folded/tokenized name match.
    Returns ``[{id, name, email, score, basis}]`` (basis: ``email`` | ``name`` | ``partial`` | ``fuzzy``)."""
    stem = _clean_stem(filename)
    if not stem:
        return []
    matchables = [(c, candidates._matchable(c)) for c in projection.all_contacts(home)]

    if "@" in stem and (email := candidates.normalize_email(stem)):
        hits = [(c, 1.0, "email") for c, m in matchables if email in m["emails"]]
        if hits:
            return [_row(c, s, b) for c, s, b in hits[:limit]]

    qn = _name_query(stem)
    q_fold, q_tokens = candidates.fold(qn), set(candidates.name_tokens(qn))
    scored = []
    for c, m in matchables:
        score, basis = _score_name(q_tokens, q_fold, m)
        if score > 0:
            scored.append((c, score, basis))
    scored.sort(key=lambda t: (-t[1], (t[0].get("fn") or "").casefold()))
    return [_row(c, s, b) for c, s, b in scored[:limit]]


def _row(contact: dict, score: float, basis: str) -> dict:
    return {"id": contact["id"], "name": contact.get("fn") or "", "email": contact.get("email") or "",
            "score": round(score, 3), "basis": basis}
