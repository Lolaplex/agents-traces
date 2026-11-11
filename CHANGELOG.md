# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `SessionStore.delete(session_id, persona)` removes a session record file (used by the relay `DELETE .../sessions/<s>` route).
- Trace events now carry `workspace` + `persona` in metadata (C201 contract): `record_message` accepts `workspace=`/`persona=` kwargs with `AGENTS_WORKSPACE`/`AGENTS_PERSONA` env fallback; `append_trace` stamps tool-call events from the child env the loop pins.

### Changed
- `.mailmap` maps legacy `Klix`/`Felix Kempf` author names to `Klix927` (no history rewrite; display only).

### Added
- `SessionRecord` keyed by workspace + user + persona (`workspace`, `persona`, `channel`, `workdir` fields). Session ids are `ses_<ULID>` (sortable ULID, D-session-id). New `SessionStore` persists records under `<ws>/personas/<persona>/sessions/<id>.json`; sessions are created only via API resolve paths. `resolve()` takes `persona`/`workdir` and lands unknown or legacy sessions on the workspace's first persona (`default`). Interceptor process session is keyed by (workspace, persona) so two personas in one workspace keep separate live sessions.

### Changed
- README + abi/LAYOUT.md describe the workspace store `<ws>/traces` and `<ws>/skills`; no `~/.agents` paths outside migration notes.

### Changed
- `sync.py`: `_agents_home()` -> `_workspace_dir()`; `mcp.json` probe resolves via `host.mcp_json`.
- Update-check cache resolves under `<host>/cache/updates/` instead of a workspace-root or XDG/home cache.
- Identity and trace paths resolve per call through `agents_workspace` instead of at import. `identity.DEFAULT_PATH` is now a compatibility attribute computed on each access; `IdentityStore` accepts an explicit `workspace=` and defaults to `Workspace.from_env().identity` (host `users.json`).
- The interceptor keeps one `TraceStore` per workspace id (`_stores` dict) instead of one global store per process, so live events from two workspaces in one process land in separate trace directories.

### Added
- Content-addressed artifacts under `content/<sha256[:2]>/<sha256>` keep the full UTF-8 text of message bodies, tool results, errors, and stacks. Daily JSONL stays bounded at 20,000 characters.
- Truncated records store `truncated`, `chars` (original length), `digest` (12-hex SHA-256 prefix), and `sha256`. Prompt `<ref>` and `<stdio>` markers include `truncated="1"` plus that digest and length.
- `read_content(session, digest, offset=0, limit=None)` verifies the digest, then returns a character window. MCP tool `read_content` and `agents-traces read` expose the same call. `DigestMismatchError` means the stored bytes do not match `digest`.
- `session_grep` accepts `until` as well as `since` (`YYYY-MM-DD` or an ISO timestamp) and skips daily files outside that window. A date bound covers the whole UTC day.

### Changed
- `record_message` no longer drops characters past 20,000. The stored digest is the hash of the full text. The JSONL field is a prefix when the body is longer; the tail is the artifact.
- Live tool results are no longer clipped at 300 characters. Up to 20,000 characters stay on the event. Longer results, errors, and stacks spill the same way.
- Ingest no longer pre-slices message bodies at 2,000 characters or error strings at 200. They use the same 20,000-character inline bound.
- Payloads already stored by an older build are unchanged. Text that was sliced off before this change cannot be reconstructed; `read_content` returns and checks only the bytes that were kept.

## [0.1.1] - 2026-10-21

### Added
- Standalone 6-lever host wiring and proactive MCP instruction injection in `agents_traces.sync`.
- Proactive self-diagnosis rules instructing coding agents to inspect recent errors via `agents-traces` when tool errors or loop stalls occur.

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
