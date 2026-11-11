# Storage Layout & Rotation Specification

## Root Directory

By default, all traces are stored in:
```
<ws>/traces/
```
The location can be overridden at runtime by setting the `AGENTS_TRACES_DIR` environment variable.

## File Hierarchy

```
<ws>/traces/
├── content/
│   └── ab/
│       └── ab…64hex        # Full UTF-8 body, named by its SHA-256
├── 2026-08-18.jsonl.gz     # Compressed archive of older traces
├── 2026-08-19.jsonl        # Yesterday's trace log
└── 2026-08-20.jsonl        # Today's active append-only trace log
```

`content/` holds bodies that do not fit in a JSONL field (see SCHEMA). The file name is the SHA-256 of the bytes. `cleanup` deletes artifacts that no remaining `.jsonl` or `.jsonl.gz` file mentions.

## Rotation Rules
- Daily Partitioning: Each UTC day automatically starts a new file named `YYYY-MM-DD.jsonl`.
- Zero Concurrency Bottleneck: Appends use atomic-like write modes.
- Cleanup: Old traces can be gzipped to `.jsonl.gz` or deleted using `agents-traces cleanup --keep-days 30`.
