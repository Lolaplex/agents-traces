# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `--help-json` emits `cordis_expose: true` so agents-harness auto-mounts this package's commands as Cordis tools when no checked-in manifest exists (#32/#34).

### Changed
- `--help-json` now emits a curated `commands` map with usage, `side_effects`, and JSON parameters (was a raw argparse dump). agents-harness Cordis discovery can classify and argv-map suite calls live; `audit`/`seal`/`verify`/`stats`/`sessions` and friends are exposed per command.

### Added
- `render_system_prompt` / `system_messages` accept a `template` — the system-prompt layout is now per-deployment configuration instead of hardcoded. Slots `{instructions}` / `{skill_catalog}` / `{runtime_context}` splice the same rendered sections the default layout produces; unknown slots pass through untouched, and the default (empty template) rendering is byte-identical to the previous layout. Deployments can drop or reorder whole sections — a frozen-world replay can omit the runtime/session metadata without code changes.

### Fixed
- `resolve()` no longer mints a new `SessionRecord` on every call when traces hold no resumable thread. One active session per (workspace, user, persona): the newest stored record is reused before falling through to legacy adoption or a fresh mint, so repeat resolves of the same tuple keep the same `ses_` id. `new_session=True` and the idle/stale rollover still mint deliberately.
- Session and persona ids are validated (`[A-Za-z0-9][A-Za-z0-9_-]*`) before any `SessionStore` path is built: `resolve()` rejects them up front and `put` raises `ValueError`, so a crafted `session=../../x` or `persona=../x` can no longer write session files outside `<ws>/personas/<p>/sessions/`. Reads clamp instead — `get`/`delete` treat un-storable ids as absent, traced ids that cannot be stored resume without persisting, and non-conforming persona dirs are skipped.

### Changed
- Sync tests point at the workspace root (`<host>/workspaces/default`) now that `AGENTS_HOME` is removed.

### Added
- `export_session` / `session_chat` rebuild one session's chat from traces as Markdown or JSON (messages, tool calls, compaction frames, retained bodies). CLI: `agents-traces export`. MCP: `export_session`.
- Write-time secret redaction in `TraceStore.append`, before the JSONL line and any content blob. Built-in coverage: common token shapes, `Authorization` / `Proxy-Authorization` values, secret `key=value` and JSON fields (`password`, `api_key`, `token`, …), and env assignments whose names mark the value secret (`GITHUB_TOKEN`, `OPENAI_API_KEY`; not `MAX_TOKENS` or `TOKEN_COUNT`). The placeholder is `[redacted]`. Digests are recomputed from the redacted text. Unquoted dictionary words (`password: use a manager`) stay. Fixed-length vendor tokens (`ghp_` plus 36 letters) are removed with or without a digit. Extra patterns that nest quantifiers, quantify an alternation (`(a|aa)+`), repeat an unbounded dot, match empty, or exceed 200 characters are skipped and not logged. They run on the whole text in 8192-character windows overlapping by 4096. A match that hits a window edge is completed on the full text, so there is no maximum replaced length. The `regex` module's timeout is used when that package is installed; a timed-out completion still redacts from the match start through the end of that window.
- Extra redaction patterns via `TraceStore(..., extra_patterns=[...])`, `configure_redaction(extra_patterns=[...])`, or `AGENTS_TRACE_REDACT_EXTRA` (JSON array of regexes). `AGENTS_TRACE_REDACT=0` or `TraceStore(..., redact=False)` turns redaction off.
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
