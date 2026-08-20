# agents-traces ABI Specification

This directory defines the stable contracts, event schemas, MCP tool signatures, and layout conventions for `agents-traces`.

## Documents

- [VERSION](VERSION): Semantic version of the agents-traces ABI contract.
- [WHY.md](WHY.md): Rationale for append-only JSONL vs bloated Docker/SaaS observability stacks.
- [SCHEMA.md](SCHEMA.md): Formal JSON Line event schemas for tool calls, LLM invocations, errors, and edits.
- [LAYOUT.md](LAYOUT.md): Filesystem structure and daily rotation rules under `~/.agents/traces/`.
- [MCP.md](MCP.md): FastMCP server tool interfaces for agent self-reflection.
- [CLI.md](CLI.md): CLI command contracts and flags.
