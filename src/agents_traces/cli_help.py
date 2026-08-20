"""ANSI formatting and help emitter for agents-trace CLI."""

from __future__ import annotations

import argparse
import json
import sys


def emit_help_json(argv: list[str], parser: argparse.ArgumentParser, name: str = "agents-trace") -> None:
    """Emit machine-readable CLI specification as JSON."""
    actions = []
    for action in parser._actions:
        if action.dest == "help":
            continue
        actions.append({
            "dest": action.dest,
            "option_strings": action.option_strings,
            "help": action.help or "",
            "default": str(action.default) if action.default is not None else None,
            "required": action.required,
        })
    payload = {
        "command": name,
        "description": parser.description or "",
        "arguments": actions,
    }
    print(json.dumps(payload, indent=2))
