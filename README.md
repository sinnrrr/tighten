# tighten

tighten runs strict linters on the lines you just changed and reports what they find. It never edits code.

It is built for AI coding agents. Agents often get a check to pass by working around it, with a new `# noqa` or `eslint-disable`, a test marked `.only` or `skip`, or a strict-rule violation left for later. tighten runs at the end of each agent turn and at git pre-commit. It enables the linter flags that ignore inline suppressions and reports new suppression comments and skipped tests. Findings cover changed lines only. Old issues elsewhere in the repo are not reported, and the agent has to fix the code it wrote.

## Requirements

- [uv](https://docs.astral.sh/uv/). tighten is a single-file uv script and uv fetches its Python dependencies on first run.
- git.
- The linters you want, on PATH. tighten installs none of them. A check whose tool is missing fails the run when it has files to check, so install the ones that match your languages.

## Install

```sh
git clone https://github.com/sinnrrr/tighten
ln -s "$PWD/tighten/tighten" ~/.local/bin/tighten
```

Keep the clone and symlink the script, because it loads its bundled configs from `embedded/` beside its real path.

## Usage

```
tighten          check all uncommitted changes against HEAD, untracked files included
tighten staged   check staged changes only, for git pre-commit
tighten stop     Claude Code Stop hook, reads the hook JSON on stdin
```

Findings go to stderr, grouped by file, followed by a short instruction for the agent:

```
src/app.ts
  12 [eslint-strict] @typescript-eslint/no-floating-promises Promises must be awaited...
  14 [new-lint-or-type-suppression] This adds a suppression comment...
tighten: shellcheck-strict failed: shellcheck is not installed or not on PATH. Report this to the user and do not install it.
```

| Exit code | Meaning |
| --------- | ------- |
| 0 | Nothing found, or stop mode had nothing to check |
| 2 | At least one finding, or a tool was missing, failed, timed out or printed output tighten could not read |
| 1 | Internal error or bad usage |

## Claude Code

Add a SessionStart hook that writes a marker file and a Stop hook that runs tighten, in `~/.claude/settings.json` or a plugin's `hooks.json`:

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
    "Stop": [
      {
        "hooks": [{ "type": "command", "command": "tighten stop", "timeout": 180 }]
      }
    ]
  }
}
```

In stop mode tighten checks only files modified after the session's marker file, so files the agent never touched are skipped. Within those files every line changed against HEAD counts, including uncommitted edits made before the session. It exits 0 with no output when `stop_hook_active` is set, when there is no marker or when the session is not in a git repo. On findings it exits 2 and Claude Code shows the report to the agent, which keeps working.

## Git pre-commit

With a git version that supports hooks in config (see `git help hook`, tested on 2.56):

```gitconfig
[hook "tighten"]
	event = pre-commit
	command = tighten staged
```

With older git, put `exec tighten staged` in `.git/hooks/pre-commit`.

Linters read files from the working tree, so a file with unstaged edits on top of staged ones would be checked against the wrong content. tighten reports such a partially staged file and skips it. Stage or stash the rest and commit again.

## Checks

| Check | Files | Runs in |
| ----- | ----- | ------- |
| suppression and skipped-test rules | per rule, see `embedded/rules.toml` | in process |
| [actionlint](https://github.com/rhysd/actionlint) | `.github/workflows/*.yml`, `*.yaml` | repo root |
| [zizmor](https://github.com/zizmorcore/zizmor) | workflows plus `**/action.yml`, `**/action.yaml` | repo root |
| [typos](https://github.com/crate-ci/typos) | every file | repo root |
| [vale](https://vale.sh) | `**/*.md`, `*.mdx`, `*.txt` | repo root |
| eslint | JavaScript and TypeScript | nearest dir with `node_modules/.bin/eslint` |
| eslint-strict | TypeScript | nearest dir with `node_modules` |
| tsc-strict | TypeScript, whole project | nearest dir with `tsconfig.json`, when `node_modules` exists there or in a parent |
| [ruff](https://docs.astral.sh/ruff/)-strict | `**/*.py`, `*.pyi` | repo root |
| [ty](https://docs.astral.sh/ty/)-strict | `**/*.py`, `*.pyi` | nearest dir with `pyproject.toml` |
| [shellcheck](https://www.shellcheck.net)-strict | `*.sh`, `*.bash` and files with an sh, dash or bash shebang | repo root |

- **Suppression rules** catch new lint and type suppressions across many tools (`eslint-disable`, `@ts-ignore`, `# noqa`, `# type: ignore`, `shellcheck disable`, `nosec` and more), focused or skipped JavaScript tests, unconditional pytest and unittest skips, and disabled JVM and .NET tests.
- **Strict checks** add rules on top of the repo's own config and ignore inline suppressions. ruff runs with `--ignore-noqa` and extra rule sets, and tsc runs with every strict flag. The full flag lists are in the script.
- **eslint** runs the repo's own eslint and config. tsc-strict and eslint-strict run pinned TypeScript and eslint through [deno](https://deno.com), so deno is needed for those two.
- **vale** uses the bundled [ai-tells](https://github.com/tbhb/vale-ai-tells) style, which flags common AI writing patterns.
- Checks with no files in scope are skipped, as are files outside any workspace for the workspace-based checks.

## Design notes

- **Hardcoded behavior.** Checks, flags and messages live in the script. Edit the script to change them.
- **Changed lines come from [pygit2](https://www.pygit2.org).** It gives added line numbers directly, with no diff text to parse. `tests/parity` compares the result with `git diff` on renamed, staged, untracked and force-added files.
- **Structured output.** SARIF or JSON where a tool has it, validated with [pydantic](https://docs.pydantic.dev). tsc has no structured output, so it gets a small strict regex parser. Output that does not validate counts as a tool failure.
- **Failures are loud.** A missing tool, a crash, an unknown exit code or unreadable output fails the run with the tool name and a reason. A broken linter never looks like a pass.
- **Timeouts clean up.** Each tool runs in its own process group with a 150 second timeout, under Claude Code's 180 second hook limit. On timeout tighten kills the group and every descendant it can find.

## Tested with

| Tool | Version | Clean | Findings | Failure |
| ---- | ------- | ----- | -------- | ------- |
| actionlint | 1.7.12 | 0 | 1 | 2 bad flag, 3 unreadable file or bad template |
| zizmor (SARIF) | 1.32.0 | 0 | 0, findings only in the SARIF | 1 audit error such as bad input or config, 2 bad flag, 3 no inputs collected. Codes 11 to 14 (highest finding informational to high) appear only without SARIF, as the docs say and a plain run returned 14 |
| typos | 1.52.2 | 0 | 2 | 64 missing path or bad flag |
| vale | 3.28.1 | 0 | 1 | 2 with JSON `Code` E100 or E201 |
| eslint | 10.15.0 | 0 (warnings allowed) | 1 | 2 config or internal error |
| tsc | 5.9.3 | 0 | 2 with `--noEmit` (1 is also accepted, since TypeScript defines it for diagnostics with skipped output) | 2 with a top-level `error TS` line, such as TS18003 |
| ruff | 0.17.6 | 0 | 1 | 2 |
| ty | 0.0.107 | 0 | 1 | 2 bad flag or config or missing path, 101 internal error |
| shellcheck | 0.11.0 | 0 | 1 | 2 unreadable file, 3 bad syntax, 4 bad option |

## Development

```
tests/parity      changed-line detection against git CLI diffs
tests/self-lint   ruff and ty strict on the scripts, plus a planted violation both must catch
```

## Credits

`embedded/vale/styles/ai-tells` is [tbhb/vale-ai-tells](https://github.com/tbhb/vale-ai-tells) v1.37.0 by Tony Burns, MIT licensed. Its license is in that directory.

## License

MIT, see `LICENSE`.
