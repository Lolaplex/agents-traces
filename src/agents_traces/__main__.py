"""
CLI entry point for agents-trace.
Commands: init, stats, inspect, sessions, tail, record, assemble, cleanup, ingest, serve, skills, sync-mcp
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure stdout / stderr handle UTF-8 properly on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import warnings

# Suppress upstream pydantic_settings forward-ref warnings
warnings.filterwarnings("ignore", message=".*IncompleteFieldDefinitionWarning.*")
warnings.filterwarnings("ignore", message=".*Field 'lifespan' has an incomplete definition.*")

from . import __version__
from .audit import (
    GENESIS,
    audit_replay,
    events_to_records,
    format_audit_text,
    format_verify_text,
    links_to_jsonl,
    load_scope,
    load_sealed_jsonl,
    parse_transcript_text,
    seal_records,
    verify_chain,
)
from .cli_help import emit_help_json
from .models import TraceEvent
from .stats import compute_model_breakdown, compute_stats, format_model_breakdown_text, format_stats_text
from .store import TraceStore
from .sync import merge_agent_mcp, sync_skills
from .timeline import render_timeline


def cmd_stats(args: argparse.Namespace, store: TraceStore) -> None:
    """Show aggregated usage statistics."""
    if getattr(args, "date", None):
        target_path = store.get_date_file_path(datetime.strptime(args.date, "%Y-%m-%d").date())
        files = [target_path] if target_path.exists() else []
        label = args.date
    elif getattr(args, "days", None):
        files = store.list_trace_files(days=args.days)
        label = f"Last {args.days} days"
    else:
        files = store.list_trace_files(days=1)
        label = f"Heute ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})"

    events = store.iter_events(files=files)

    if getattr(args, "by_model", False) or getattr(args, "command", "") == "analyze-models":
        model_data = compute_model_breakdown(events)
        if getattr(args, "json", False):
            print(json.dumps(model_data, indent=2))
        else:
            print(format_model_breakdown_text(model_data, label=label))
        return

    stats = compute_stats(events)

    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2))
    else:
        print(format_stats_text(stats, label=label))


def cmd_inspect(args: argparse.Namespace, store: TraceStore) -> None:
    """Inspect timeline for a session."""
    session_id = getattr(args, "session_id", None)
    if not session_id:
        session_id = store.get_last_session_id()
        if not session_id:
            print("\033[33mNo sessions found in traces.\033[0m")
            return

    events = store.get_events_for_session(session_id)
    if not events:
        print(f"\033[33mNo events found for session '{session_id}'.\033[0m")
        return

    output = render_timeline(events, session_id=session_id)
    print(output)


def cmd_sessions(args: argparse.Namespace, store: TraceStore) -> None:
    """List recent sessions."""
    days = getattr(args, "days", 1)
    limit = getattr(args, "limit", 20)
    sessions = store.list_sessions(days=days, limit=limit)
    if not sessions:
        print("\033[33mNo active sessions found.\033[0m")
        return

    print(f"\033[1;36m=== Recent Sessions (Last {days}d) ===\033[0m")
    header = f"{'Session ID':<16} {'Events':<8} {'Tools':<8} {'Errors':<8} {'Tokens':<10} {'Cost':<10} {'Model'}"
    print(f"\033[1m{header}\033[0m")
    print("-" * 75)

    for s in sessions:
        err_str = f"\033[31m{s['errors']}\033[0m" if s['errors'] > 0 else f"\033[32m0\033[0m"
        tok_str = f"{(s['tokens_in'] + s['tokens_out']) / 1000:.1f}k" if (s['tokens_in'] + s['tokens_out']) >= 1000 else str(s['tokens_in'] + s['tokens_out'])
        cost_str = f"${s['cost_usd']:.4f}"
        model_str = s['model'] or "n/a"
        sid = s['session'][:15]
        print(f"{sid:<16} {s['event_count']:<8} {s['tool_calls']:<8} {err_str:<17} {tok_str:<10} {cost_str:<10} {model_str}")


def cmd_tail(args: argparse.Namespace, store: TraceStore) -> None:
    """Tail trace events in real time."""
    today_file = store.get_date_file_path()
    if not today_file.exists():
        today_file.touch()

    # Print initial N lines
    with open(today_file, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
        for line in lines[-args.lines:]:
            print(line.strip())

    if not args.follow:
        return

    print("\033[90m--- Following live trace stream (Ctrl+C to exit) ---\033[0m")
    with open(today_file, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        try:
            while True:
                line = f.readline()
                if line:
                    event = TraceEvent.from_json(line)
                    if event and not args.raw:
                        dur = f" ({event.duration_ms:.0f}ms)" if event.duration_ms is not None else ""
                        status_str = f"[{event.status or 'ok'}]"
                        if event.status == "error" or event.error:
                            print(f"\033[31m[{event.session}] ⚙ {event.tool or event.type}: {event.error or 'error'}{dur}\033[0m")
                        else:
                            print(f"\033[32m[{event.session}]\033[0m \033[36m{event.type}\033[0m: {event.tool or event.file or ''} {status_str}{dur}")
                    else:
                        print(line.strip())
                else:
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass


def cmd_record(args: argparse.Namespace, store: TraceStore) -> None:
    """Record an event from the CLI."""
    meta = None
    if args.data:
        try:
            meta = json.loads(args.data)
        except Exception:
            meta = {"raw": args.data}

    event = TraceEvent(
        session=args.session or "cli-session",
        type=args.type or "custom",
        tool=args.tool,
        status=args.status,
        error=args.error,
        duration_ms=args.duration,
        tokens_in=args.tokens_in,
        tokens_out=args.tokens_out,
        cost_usd=args.cost,
        file=args.file,
        metadata=meta,
    )
    store.append(event)
    print(f"\033[32m✓ Recorded event to {store.get_date_file_path().name}\033[0m")


def cmd_cleanup(args: argparse.Namespace, store: TraceStore) -> None:
    """Clean up old trace files."""
    count = store.cleanup_old_traces(keep_days=args.keep_days, compress=args.compress)
    action = "Compressed" if args.compress else "Deleted"
    print(f"\033[32m✓ {action} {count} trace file(s) older than {args.keep_days} days.\033[0m")


def cmd_seal(args: argparse.Namespace, store: TraceStore) -> int:
    """Cryptographically seal tool calls into a SHA-256 hash chain."""
    target = getattr(args, "target", None) or getattr(args, "session", None)
    records = []
    session_label = target or "latest"

    if target and Path(target).is_file():
        text = Path(target).read_text(encoding="utf-8", errors="replace")
        records = parse_transcript_text(text)
        session_label = Path(target).name
    else:
        sid = target or store.get_last_session_id()
        if not sid:
            print("\033[33mNo sessions found to seal.\033[0m")
            return 1
        session_label = sid
        events = store.get_events_for_session(sid)
        records = events_to_records(events)

    if not records:
        print(f"\033[33mNo tool calls found for '{session_label}'.\033[0m")
        return 1

    links = seal_records(records)
    jsonl_output = links_to_jsonl(links)

    out_path = getattr(args, "output", None)
    if out_path:
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(jsonl_output, encoding="utf-8")
        print(f"\033[32m✓ Sealed {len(links)} tool calls into {out_path}\033[0m")
        print(f"Head digest: {links[-1].digest if links else GENESIS}")
    elif getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "status": "sealed",
                    "session": session_label,
                    "total_links": len(links),
                    "head_digest": links[-1].digest if links else GENESIS,
                },
                indent=2,
            )
        )
    else:
        print(jsonl_output, end="")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify cryptographic integrity of a sealed hash chain."""
    path = getattr(args, "path", None)
    if not path or not Path(path).is_file():
        print(f"\033[31mError: file not found: {path}\033[0m")
        return 1

    links = load_sealed_jsonl(path)
    res = verify_chain(links)

    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(format_verify_text(res))
    return 0 if res.ok else 1


def cmd_audit(args: argparse.Namespace, store: TraceStore) -> int:
    """Audit tool calls for non-determinism, redundant calls, and scope violations."""
    target = getattr(args, "target", None) or getattr(args, "session", None)
    records = []
    session_label = target or "latest"

    if target and Path(target).is_file():
        text = Path(target).read_text(encoding="utf-8", errors="replace")
        records = parse_transcript_text(text)
        session_label = Path(target).name
    else:
        sid = target or store.get_last_session_id()
        if not sid:
            print("\033[33mNo sessions found to audit.\033[0m")
            return 1
        session_label = sid
        events = store.get_events_for_session(sid)
        records = events_to_records(events)

    if not records:
        print(f"\033[33mNo tool calls found for '{session_label}'.\033[0m")
        return 0

    scope = None
    scope_path = getattr(args, "scope", None)
    if scope_path:
        try:
            scope = load_scope(scope_path)
        except Exception as exc:
            print(f"\033[31mError loading scope: {exc}\033[0m")
            return 1

    res = audit_replay(records, allowed_tools=scope, session=session_label)

    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(format_audit_text(res))

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agents-traces",
        description="Append-only JSONL session traces and per-request assemble.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"agents-traces {__version__}",
    )
    parser.add_argument(
        "--help-json",
        action="store_true",
        help="Emit machine-readable CLI spec as JSON and exit.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # init
    subparsers.add_parser("init", help="Plug & Play setup: auto-configure MCP in all IDEs and sync skills")

    # stats
    p_stats = subparsers.add_parser("stats", help="Display token, cost and tool statistics")
    p_stats.add_argument("--date", help="Specific date YYYY-MM-DD")
    p_stats.add_argument("--days", type=int, help="Number of past days to aggregate")
    p_stats.add_argument("--by-model", action="store_true", help="Break down statistics and failure rates by model")
    p_stats.add_argument("--json", action="store_true", help="Output raw JSON")

    # analyze-models
    p_models = subparsers.add_parser("analyze-models", help="Analyze tool failure rates and empirical traps by model")
    p_models.add_argument("--days", type=int, default=7, help="Days to aggregate (default 7)")
    p_models.add_argument("--json", action="store_true", help="Output raw JSON")

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect timeline for a session")
    p_inspect.add_argument("session_id", nargs="?", help="Session ID (defaults to latest)")

    # sessions
    p_sessions = subparsers.add_parser("sessions", help="List recent active sessions")
    p_sessions.add_argument("--days", type=int, default=1, help="Days to look back (default 1)")
    p_sessions.add_argument("--limit", type=int, default=20, help="Max sessions to list")

    # tail
    p_tail = subparsers.add_parser("tail", help="Tail trace events in real time")
    p_tail.add_argument("-n", "--lines", type=int, default=20, help="Initial lines to print")
    p_tail.add_argument("-f", "--follow", action="store_true", help="Follow live output")
    p_tail.add_argument("--raw", action="store_true", help="Print raw JSON lines")

    # record
    p_record = subparsers.add_parser("record", help="Record a trace event")
    p_record.add_argument("--session", default="cli-session", help="Session ID")
    p_record.add_argument("--type", default="custom", help="Event type")
    p_record.add_argument("--tool", help="Tool name")
    p_record.add_argument("--status", help="Status (ok/error)")
    p_record.add_argument("--error", help="Error message")
    p_record.add_argument("--duration", type=float, help="Duration in ms")
    p_record.add_argument("--tokens-in", type=int, help="Input tokens")
    p_record.add_argument("--tokens-out", type=int, help="Output tokens")
    p_record.add_argument("--cost", type=float, help="Cost in USD")
    p_record.add_argument("--file", help="File edited")
    p_record.add_argument("--data", help="JSON metadata")

    # cleanup
    p_cleanup = subparsers.add_parser("cleanup", help="Delete or compress old traces")
    p_cleanup.add_argument("--keep-days", type=int, default=30, help="Days to retain (default 30)")
    p_cleanup.add_argument("--compress", action="store_true", help="Gzip instead of deleting")

    # assemble (agent = trace, per-request reconstruct)
    p_assemble = subparsers.add_parser(
        "assemble",
        help="Rebuild chat-completions messages from a session trace",
    )
    p_assemble.add_argument("--session", required=True, help="Session id (ses_… or legacy channel-user)")
    p_assemble.add_argument("--limit", type=int, default=24, help="Max turns")
    p_assemble.add_argument("--days", type=int, default=30, help="Trace files to scan")
    p_assemble.add_argument("--include-tools", action="store_true")

    # ingest
    subparsers.add_parser("ingest", help="Ingest IDE transcripts (Antigravity / Cursor) into traces")

    # serve / mcp
    subparsers.add_parser("serve", help="Start FastMCP stdio server")
    subparsers.add_parser("mcp", help="Alias for serve")

    # skills
    subparsers.add_parser("skills", help="Sync trace skills into IDE customization roots")

    # sync-mcp
    subparsers.add_parser("sync-mcp", help="Auto-inject MCP config into Cursor, Antigravity, Claude Desktop, Zed")

    # seal
    p_seal = subparsers.add_parser("seal", help="Cryptographically seal tool calls into a SHA-256 hash chain")
    p_seal.add_argument("target", nargs="?", help="Session ID or transcript file path (defaults to latest)")
    p_seal.add_argument("--session", help="Session ID filter")
    p_seal.add_argument("--output", "-o", help="Output file path (default: stdout)")
    p_seal.add_argument("--json", action="store_true", help="Output summary JSON")

    # verify
    p_verify = subparsers.add_parser("verify", help="Verify cryptographic integrity of a sealed hash chain")
    p_verify.add_argument("path", help="Path to sealed JSONL file")
    p_verify.add_argument("--json", action="store_true", help="Output verification result as JSON")

    # audit / replay
    p_audit = subparsers.add_parser("audit", help="Audit tool calls for non-determinism, redundant calls, and scope violations")
    p_audit.add_argument("target", nargs="?", help="Session ID or transcript file path (defaults to latest)")
    p_audit.add_argument("--session", help="Session ID filter")
    p_audit.add_argument("--scope", help="Path to scope JSON file")
    p_audit.add_argument("--json", action="store_true", help="Output audit result as JSON")

    p_replay = subparsers.add_parser("replay", help="Alias for audit")
    p_replay.add_argument("target", nargs="?", help="Session ID or transcript file path (defaults to latest)")
    p_replay.add_argument("--session", help="Session ID filter")
    p_replay.add_argument("--scope", help="Path to scope JSON file")
    p_replay.add_argument("--json", action="store_true", help="Output audit result as JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])

    if argv and argv[0] in ("version",):
        from . import __version__
        print(f"agents-traces {__version__}")
        return 0
    if argv and argv[0] in ("help",):
        argv = ["--help"]

    parser = build_parser()

    if "--help-json" in argv:
        emit_help_json(argv, parser, name="agents-traces")
        return 0

    args = parser.parse_args(argv)
    if args.command not in ("serve", "mcp"):
        try:
            from .updates import check_for_updates
            check_for_updates("agents-traces", __version__)
        except Exception:
            pass

    store = TraceStore()

    if args.command == "init":
        print("=== Initializing agents-traces (Plug & Play) ===\n")
        synced = sync_skills()
        print(f"1. Synced {len(synced)} agent skills:")
        for s in synced:
            print(f"   * {s}")

        mcp_res = merge_agent_mcp()
        print(f"\n2. Auto-configured MCP servers in {len(mcp_res)} IDE config files:")
        for r in mcp_res:
            print(f"   * {r}")

        print("\n[OK] Plug & Play setup complete. Reload your IDE/Agent to start using agents-traces.")
        return 0

    elif args.command == "stats":
        cmd_stats(args, store)
        return 0
    elif args.command == "analyze-models":
        cmd_stats(args, store)
        return 0
    elif args.command == "inspect":
        cmd_inspect(args, store)
        return 0
    elif args.command == "sessions":
        cmd_sessions(args, store)
        return 0
    elif args.command == "tail":
        cmd_tail(args, store)
        return 0
    elif args.command == "record":
        cmd_record(args, store)
        return 0
    elif args.command == "cleanup":
        cmd_cleanup(args, store)
        return 0
    elif args.command == "assemble":
        from .assemble import assemble_messages

        msgs = assemble_messages(
            args.session,
            store=store,
            limit=args.limit,
            days=args.days,
            include_tools=args.include_tools,
        )
        print(json.dumps({"session": args.session, "messages": msgs}, ensure_ascii=False, indent=2))
        return 0
    elif args.command == "ingest":
        from .ingest import ingest_all_ide_transcripts
        res = ingest_all_ide_transcripts(store=store)
        print(f"\033[1;36m=== Ingested {res['total_events']:,} trace events across {res['total_sessions']} IDE sessions ===\033[0m")
        print(f"• Antigravity: {res['antigravity_events']:,} events")
        print(f"• Cursor:      {res.get('cursor_events', 0):,} events")
        print(f"• Claude:      {res['claude_events']:,} events")
        print(f"• Cline/Tasks: {res['cline_events']:,} events")
        skipped = res.get("skipped_sessions", 0)
        if skipped:
            print(f"• Skipped:     {skipped} sessions already in traces")
        twins = res.get("skipped_live_twins", 0)
        purged_s = res.get("purged_twin_sessions", 0)
        purged_e = res.get("purged_twin_events", 0)
        if twins or purged_s:
            print(
                f"• Live twins:  skipped {twins} ingest sessions; "
                f"dropped {purged_e} events in {purged_s} already-written sessions"
            )
        return 0
    elif args.command in ["serve", "mcp"]:
        from .mcp_server import mcp
        mcp.run()
        return 0
    elif args.command == "skills":
        synced = sync_skills()
        print(f"Synced {len(synced)} skill files into agent customization roots.")
        return 0
    elif args.command == "sync-mcp":
        res = merge_agent_mcp()
        print(f"Configured MCP servers across {len(res)} host configs:")
        for r in res:
            print(f" * {r}")
        return 0
    elif args.command == "seal":
        return cmd_seal(args, store)
    elif args.command == "verify":
        return cmd_verify(args)
    elif args.command in ("audit", "replay"):
        return cmd_audit(args, store)
    else:
        # Default behavior: run stats for today
        cmd_stats(argparse.Namespace(date=None, days=1, json=False), store)
        return 0


if __name__ == "__main__":
    sys.exit(main())
