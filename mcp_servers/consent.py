"""Cloud-LLM consent propagation notice (EX-H7) — surfaced to MCP clients via ``instructions``.

Honest signaling, **not** enforcement: an MCP server cannot tell which LLM consumes its output
(see ``docs/design-notes/mcp-cannot-identify-the-consuming-llm.md``), so this is a best-effort hint a
*cooperating* client can relay to the human — never a gate. PRM's read surface (and the propose-only
dedup surface) return contact PII, so this is the v0.1 down-payment on PNT's ``EX-CLOUD-LLM`` handler.
The full handler — a workspace consent gate, a persistent "not a PNA" banner, and a return-to-PNA-mode
control (EX-H2–H5) — is v0.2 (see ``docs/roadmap.md``).

Pure module: **no ``mcp`` SDK import**, so the default test suite can assert on the notice without the
isolated ``mcp_servers/.venv`` (mirrors ``tools.py``'s no-SDK-import rule).
"""

from __future__ import annotations

CLOUD_LLM_NOTICE = (
    "PRM is a local-only Personal Network Application: by default the user's contact data never "
    "leaves their device. These tools return that PRIVATE contact data (names, emails, phones, and "
    "the user's merge decisions). Sending it to a cloud-hosted model takes PRM out of local (PNA) "
    "mode under the EX-CLOUD-LLM exception. If you are a cloud AI acting on a person's behalf, you "
    "MUST ensure that person knows their contacts are crossing to a cloud provider and has "
    "personally consented — do NOT treat your own invocation as their consent. Prefer a local "
    "model. You can only PROPOSE merges; the human reviews and applies every change in the PRM "
    "workspace."
)
