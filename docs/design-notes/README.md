# Design notes

The *why* behind PRM's decisions — the reasoning, constraints, and rejected alternatives that the
[feature spec](../prm-feature-spec.md) (*what the product is*) and the [roadmap](../roadmap.md)
(*what's coming, and when*) deliberately don't carry. A note records a decision, the constraint that
forced it, the alternatives considered, and **what would change the decision**. Notes are
append-mostly: when a decision is revisited, supersede the note (a dated update or a successor link)
rather than silently rewriting history.

This is the doc category that *owns design rationale* — see [`../../CLAUDE.md`](../../CLAUDE.md),
"Documentation map".

## LLMs & MCP

How PRM reasons about large language models and the Model Context Protocol — the trust boundary
around cloud AI, what the protocol can and can't enforce, and how PRM stays an honest PNA across it.

- [**MCP cannot identify the consuming LLM → `EX-CLOUD-LLM`**](mcp-cannot-identify-the-consuming-llm.md)
  — an MCP server can't tell whether a cloud or local model is reading its output, so PRM handles
  cloud AI with a *consented, reversible exception*, not detection or blocking.
