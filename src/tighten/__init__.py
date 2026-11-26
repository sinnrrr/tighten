import json
import os
import sys
from pathlib import Path

lazy from tighten.lint import manual, staged, stop

USAGE = "usage: tighten [hook|staged]"


def guards() -> Path:
    return Path(os.environ.get("TMPDIR") or "/tmp") / "claude-guards"


def record(name: str, text: str) -> None:
    directory = guards()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a") as marker:
        marker.write(text)


def hook() -> int:
    event = json.load(sys.stdin)
    session = event["session_id"]
    match event["hook_event_name"]:
        case "SessionStart":
            record(session, "")
        case "PostToolUse":
            tool_input = event.get("tool_input") or {}
            if path := tool_input.get("file_path") or tool_input.get("notebook_path"):
                agent = event.get("agent_id")
                record(f"{session}.{agent}" if agent else session, f"{path}\n")
        case "Stop" | "SubagentStop" | "PreToolUse":
            return stop(event)
    return 0


def main() -> int:
    match sys.argv[1:]:
        case []:
            return manual()
        case ["hook"]:
            return hook()
        case ["staged"]:
            return staged()
    print(USAGE, file=sys.stderr)
    return 1
