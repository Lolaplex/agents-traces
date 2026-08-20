# Storage Layout & Rotation Specification

## Root Directory

By default, all traces are stored in:
```
~/.agents/traces/
```
The location can be overridden at runtime by setting the `AGENTS_TRACES_DIR` environment variable.

## File Hierarchy

```
~/.agents/traces/
├── 2026-08-18.jsonl.gz     # Compressed archive of older traces
├── 2026-08-19.jsonl        # Yesterday's trace log
└── 2026-08-20.jsonl        # Today's active append-only trace log
```

## Rotation Rules
- Daily Partitioning: Each UTC day automatically starts a new file named `YYYY-MM-DD.jsonl`.
- Zero Concurrency Bottleneck: Appends use atomic-like write modes.
- Cleanup: Old traces can be gzipped to `.jsonl.gz` or deleted using `agents-trace cleanup --keep-days 30`.
