# tighten

`tighten` gives coding agents the feedback an IDE gives people: type checkers, linters and LSPs. At the end of each Claude Code action it runs those tools on the lines the agent changed and sends the findings back. The agent fixes them before handing over.

## What it runs

- Strict type checkers and linters for Python, TypeScript, shell, GitHub Actions and like markdown stuff.
- Opinionated rules that improve how the agents write code.

## Setup

Paste this into Claude Code and it sets things up for you:

```
Install tighten from https://github.com/sinnrrr/tighten. Read its README first.
1. Run: uv tool install git+https://github.com/sinnrrr/tighten
2. Merge the three hooks from the README's "Claude Code hooks" section into ~/.claude/settings.json. Keep my existing hooks.
3. Check which of the linters it uses are missing for the languages in my repos and list them. Ask before installing anything.
```

Needs [uv](https://docs.astral.sh/uv/) and git.

## Claude Code hooks

These need to be added to `~/.claude/settings.json`. Each subagent gets the findings for its own edits before it reports back, and the main session gets the rest.

Files in Claude Code's scratchpad folder are skipped. Quoted third-party text such as pasted emails can be skipped with `linguist-vendored` in `.gitattributes`.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.session_id' | { read -r id; d=\"${TMPDIR:-/tmp}/claude-guards\"; mkdir -p \"$d\"; [ -e \"$d/$id\" ] || touch \"$d/$id\"; }"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "SubagentHandback",
        "hooks": [
          {
            "type": "command",
            "command": "tighten stop",
            "timeout": 180
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.session_id, (.agent_id // \"\"), (.tool_input.file_path // .tool_input.notebook_path // \"\")' | { read -r id; read -r agent; read -r p; [ -z \"$p\" ] || { d=\"${TMPDIR:-/tmp}/claude-guards\"; mkdir -p \"$d\"; printf '%s\\n' \"$p\" >> \"$d/$id${agent:+.$agent}\"; }; }"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "tighten stop",
            "timeout": 180
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "tighten stop",
            "timeout": 180
          }
        ]
      }
    ]
  }
}
```

## License

MIT. The bundled vale style is [tbhb/vale-ai-tells](https://github.com/tbhb/vale-ai-tells) by Tony Burns, MIT licensed.
