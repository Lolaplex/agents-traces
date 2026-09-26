# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Lolaplex/agents-traces/compare/v0.0.2...HEAD
[0.0.2]: https://github.com/Lolaplex/agents-traces/compare/v0.42.0...v0.0.2
[0.42.0]: https://github.com/Lolaplex/agents-traces/releases/tag/v0.42.0
