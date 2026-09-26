---
name: trace-stats
description: Monitor token consumption, cost metrics, and tool reliability rates via agents-traces MCP.
---

# Trace Stats Skill

Use this skill when checking session resource usage, cost breakdowns, or error percentages.

## How to check Agent Metrics

1. To get today's token, cost, and tool reliability metrics:
   - Tool: `get_session_stats(days=1)`
   - Or CLI: `agents-traces stats`
2. For weekly aggregated usage:
   - Tool: `get_session_stats(days=7)`
   - Or CLI: `agents-traces stats --days 7`
3. Per-model failure rates:
   - Tool: `get_model_stats(days=7)`
   - Or CLI: `agents-traces analyze-models`
4. Identify problematic tools:
   - Check `top_tools` and `tool_success_rate` in the returned JSON.
