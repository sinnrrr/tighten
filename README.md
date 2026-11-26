# tighten

`tighten` gives coding agents the feedback an IDE gives people: type checkers, linters and LSPs. At the end of each Claude Code action it runs those tools on the lines the agent changed and sends the findings back. The agent fixes them before handing over.

## What it runs

- Strict type checkers and linters for Python, TypeScript, shell, GitHub Actions and like markdown stuff.
- Opinionated rules that improve how the agents write code.

## Setup

Install the CLI with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/sinnrrr/tighten
```

Then add the Claude Code plugin, which runs `tighten` from your PATH:

```
/plugin marketplace add sinnrrr/tighten
/plugin install tighten@tighten
```

Each subagent gets the findings for its own edits before it reports back, and the main session gets the rest.

Files in Claude Code's scratchpad folder are skipped. Quoted third-party text such as pasted emails can be skipped with `linguist-vendored` in `.gitattributes`.

## Before each commit

`tighten staged` checks the staged lines. Call it from your git pre-commit hook. Plain `tighten` checks every uncommitted change.

## License

MIT. The bundled vale style is [tbhb/vale-ai-tells](https://github.com/tbhb/vale-ai-tells) by Tony Burns, MIT licensed.
