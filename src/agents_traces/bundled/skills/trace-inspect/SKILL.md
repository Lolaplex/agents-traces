---
name: trace-inspect
description: Inspect execution timelines, diagnose tool errors, and self-heal failed agent loops via agents-traces MCP.
---

# Trace Inspect Skill

Use this skill when a task fails, a tool call throws an exception, or you need to inspect what happened in previous agent steps.

## How to use `agents-traces` for Self-Diagnosis

1. To inspect the current or previous session's timeline:
   - Tool: `get_last_session_trace()`
   - Or CLI: `agents-traces inspect`
2. To look up recent tool errors and exception stack traces across sessions:
   - Tool: `get_recent_errors(limit=5)`
3. Session message text (not the tool timeline) lives on `session_snap`, `session_grep`, and `session_tail`. That store is traces, not agents-memory.
4. To analyze root causes:
   - Read the exact arguments passed to the failed tool.
   - Check error messages and stack traces to fix parameters or environment states without asking the user.
