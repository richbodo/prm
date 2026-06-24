"""core/disclosure.py — the cloud-disclosure mode + the read-side data-floor policy (EX-CLOUD-LLM / AC-MCP-C).

The **mode** governs whether the ``private-shareable-on-consent`` overlay projection may cross the MCP
surface. Three modes:

- ``pna`` (default) — local-only; **nothing** private crosses the MCP surface.
- ``local-ai`` — a local model has been granted access; data stays on the host, so **no exception** is
  raised (INV-1 holds). The shareable projection is unlocked.
- ``cloud-exception`` — a cloud model has been granted access; ``EX-CLOUD-LLM`` is active. The shareable
  projection is unlocked.

``private-sealed`` fields NEVER cross, in any mode — that floor is structural (the cloud projection selects
only shareable rows at the query layer; see ``relationships_db.shareable_field_values`` — AC-MCP-C / PR-7).

State lives in the relationships.db ``settings`` table: a single source the daemon writes (under the
AC-PRM-C file-lock, via ``core/apply.py``) and the MCP servers — separate processes — read on each request,
so a return-to-PNA in the workspace takes effect on the very next tool call. A leaf over ``relationships_db``
+ ``schema``; it never imports ``cli``. Rationale: ``plans/ex-cloud-llm-workspace-handler.md``.
"""

from __future__ import annotations

import json

from core import relationships_db, schema

PNA = "pna"
LOCAL_AI = "local-ai"
CLOUD_EXCEPTION = "cloud-exception"
MODES = (PNA, LOCAL_AI, CLOUD_EXCEPTION)
_DISCLOSING = frozenset({LOCAL_AI, CLOUD_EXCEPTION})   # modes that unlock the shareable projection
_SHAREABLE_TIER = "private-shareable-on-consent"

# settings keys (relationships.db `settings` table)
MODE_KEY = "disclosure_mode"
AT_KEY = "disclosure_consented_at"
SCOPE_KEY = "disclosure_consented_scope"
HISTORY_KEY = "disclosure_history"
HISTORY_MAX = 50


def discloses(mode: str) -> bool:
    """Whether ``mode`` unlocks the shareable overlay projection for the MCP surface."""
    return mode in _DISCLOSING


def _loads_list(raw) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return v if isinstance(v, list) else []


def current(home) -> dict:
    """The disclosure state — ``{mode, consented_at, consented_scope, history}``. Read-only and safe before
    the store exists (defaults to ``pna``)."""
    db = home.relationships_db
    if not db.exists():
        return {"mode": PNA, "consented_at": None, "consented_scope": [], "history": []}
    mode = relationships_db.get_setting(db, MODE_KEY, PNA) or PNA
    if mode not in MODES:
        mode = PNA
    return {
        "mode": mode,
        "consented_at": relationships_db.get_setting(db, AT_KEY) or None,
        "consented_scope": _loads_list(relationships_db.get_setting(db, SCOPE_KEY)),
        "history": _loads_list(relationships_db.get_setting(db, HISTORY_KEY)),
    }


def mode(home) -> str:
    """Just the current mode (``pna`` if no store)."""
    return current(home)["mode"]


def shareable_field_ids(home) -> list:
    """Sorted field_ids whose ``disclosure_tier`` is shareable-on-consent — the shape of the curated
    projection a disclosing client may read."""
    if not home.relationships_db.exists():
        return []
    return sorted(f["field_id"] for f in schema.list_fields(home.relationships_db)
                  if f.get("disclosure_tier") == _SHAREABLE_TIER)


def shareable_scope(home) -> dict:
    """The disclosure blast radius (EX-H9): which fields are shareable and how many contacts / values would
    cross. Drives the consent-time preview and the scope-grew re-consent check."""
    fids = shareable_field_ids(home)
    contacts = values = 0
    if fids:
        for vals in relationships_db.shareable_field_values(home.relationships_db).values():
            n = sum(1 for v in vals if v.get("value") not in (None, ""))
            if n:
                contacts += 1
                values += n
    return {"field_ids": fids, "contacts": contacts, "values": values}


def scope_grew(home) -> bool:
    """True if the shareable set now contains a field the user did NOT consent to (one marked shareable
    *after* consent) — the re-consent trigger. Always False in ``pna`` (nothing crosses anyway)."""
    st = current(home)
    if not discloses(st["mode"]):
        return False
    consented = set(st["consented_scope"])
    return any(fid not in consented for fid in shareable_field_ids(home))
