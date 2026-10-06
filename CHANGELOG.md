# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-06

### Added
- Automatic daily and idle session rollover in `IdentityStore.resolve` (configurable `max_idle_hours` defaulting to 4h across calendar day boundaries) preventing infinite zombie session context accumulation.
- Cryptographic SHA-256 hash chaining for session tool calls (`seal_records`, `verify_chain`).
- Session replay audit engine detecting non-determinism, divergence index, and redundant calls without intervening mutators.
- Tool scope validation against declared permission boundaries (`load_scope`, `OVERREACH`).
- CLI subcommands `seal`, `verify`, and `audit` (with `replay` alias).
- MCP tools `trace_seal`, `trace_verify`, and `trace_audit`.
- CLI (and MCP, when present) check PyPI at most once per day for a newer release and print one stderr / tool-response line (`uv tool upgrade …`). Disabled with `AGENTS_NO_UPDATE_CHECK=1` or when `CI` is set; offline/timeout stays silent.

## [0.0.3] - 2026-09-26

### Changed
- README and ABI list the live CLI (`assemble`, `ingest`, `analyze-models`, `record`) and all nine MCP tools. Bare `agents-traces` prints today's stats.
- Skills and layout docs call the installed command `agents-traces`.
- Event schema documents optional `origin` (`live` or `ingested`).

## [0.0.2] - 2026-09-26

### Added
- Support for uniform `trace:` locator prefix in `session_view` and MCP self-diagnosis tools (e.g. `trace:ses_...`).

### Changed
- CI runs only on pull requests to `main` with strict bundled verification.
- Documented complete suite of 9 MCP tools in README.md.

### Removed
- Automatic package publishing and GitHub Release creation from Actions (no PyPI upload, no tag-triggered release).

### Fixed
- Per-turn clock converts into the caller's IANA timezone and includes weekday plus local offset, so models no longer see a UTC stamp labeled as another zone.

## [0.42.0] - 2026-08-20

### Added
- Append-only daily JSONL traces under `~/.agents/traces/` (sub-0.05ms append; no daemon, no database, no Docker).
- CLI inspection: stats, inspect, tail, and session timelines.
- MCP self-heal tools: `get_last_session_trace`, `get_recent_errors`.
- Multi-IDE MCP autowire (Cursor, Antigravity, Claude, Zed).

[Unreleased]: https://github.com/Lolaplex/agents-traces/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Lolaplex/agents-traces/compare/v0.0.3...v0.1.0
[0.0.3]: https://github.com/Lolaplex/agents-traces/compare/v0.0.2...v0.0.3
[0.0.2]: https://github.com/Lolaplex/agents-traces/compare/v0.42.0...v0.0.2
[0.42.0]: https://github.com/Lolaplex/agents-traces/releases/tag/v0.42.0
