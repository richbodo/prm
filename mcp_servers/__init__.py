"""PRM MCP servers — the AI surface over the contact store.

Two stdio servers, registered separately so their consent posture is independent:
``shared_data_ops`` (read-only over canonical contacts + duplicate candidates) and ``dedup_ops``
(**propose-only**: the AI stages a merge changeset for the human to review and apply — it cannot
apply, INV-11 / AC-PRM-F). Tool bodies live in ``tools`` (no SDK import, unit-tested); the server
modules are thin ``mcp`` SDK wrappers.

Named ``mcp_servers/`` (not ``mcp/``) because the official SDK's package *is* ``mcp`` — a ``mcp/``
directory would shadow ``import mcp``. The SDK installs into an isolated ``mcp_servers/.venv`` (see
``just mcp-install-deps``), keeping ``core``'s tiny-deps boundary clean.
"""
