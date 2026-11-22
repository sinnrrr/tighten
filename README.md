# tighten

tighten runs strict linters on the lines you changed and reports what they find. It never edits code. It is meant for AI coding agents, which tend to pass checks by adding suppression comments, skipping tests or leaving strict-rule violations behind. tighten reports those problems at the end of an agent turn and at git pre-commit. The agent then has to fix the code.

It replaces a chain of hk, Pkl, reviewdog, semgrep and a bash Stop hook with one Python script.

## Usage

```
tighten          check all uncommitted changes against HEAD, untracked files included
tighten staged   check staged changes only, for git pre-commit
tighten stop     Claude Code Stop hook, reads the hook JSON on stdin
```

tighten is a single-file [uv](https://docs.astral.sh/uv/) script. Put it on PATH with a symlink or a copy of the whole repo. It finds `embedded/` through its real path, and a symlink on PATH works. The linters come from PATH and tighten installs none of them.

### Claude Code plugin

`plugin/` is a Claude Code plugin. Its SessionStart hook writes the session marker file `${TMPDIR:-/tmp}/claude-guards/<session_id>`. Its Stop hook runs `tighten stop` with a 180 second timeout. A marketplace entry can point at `./plugin`.

In stop mode tighten exits 0 without output when `stop_hook_active` is true, when the marker file is missing or when the hook `cwd` is not inside a git work tree. It checks files that changed against HEAD and untracked files that are not ignored, but only those modified after the marker file. In those files every line changed against HEAD counts, including edits made before the session started.

### Pre-commit

Add this to a git config file. `git help hook` describes hooks defined in config. It was checked with git 2.56.1. The oldest git version with this feature was not checked.

```gitconfig
[hook "tighten"]
	event = pre-commit
	command = tighten staged
```

Staged mode checks the lines that the index adds against HEAD. Linters read files from the working tree, so a file whose working tree copy differs from its staged copy would be checked against the wrong content. tighten reports such a partially staged file as a failure with its path, skips linting it and exits 2. Stage the rest of the file or stash it, then commit again. Other files are still checked. Staged mode reads `GIT_INDEX_FILE`, so `git commit -a` and `git commit <path>` are checked against the index git is about to commit.

## Output

Findings go to stderr, grouped by file:

```
src/app.ts
  12 [eslint-strict] @typescript-eslint/no-floating-promises Promises must be awaited...
  14 [new-lint-or-type-suppression] This adds a suppression comment...
tighten: shellcheck-strict failed: shellcheck is not installed or not on PATH. Report this to the user and do not install it.
```

Findings are followed by a closing instruction for the agent. The text is the same as before with the product name changed from `agent-checks` to `tighten`, since the `agent-checks` hook goes away at cutover.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | No findings and no tool failures, or stop mode had nothing to check |
| 2 | At least one finding, or a tool was missing, failed, timed out or printed output that did not validate |
| 1 | Internal error in tighten (Python prints the traceback), usage error, or bare `tighten` outside a git work tree |

## Checks

| Check | Files | Runs in |
| ----- | ----- | ------- |
| actionlint | `.github/workflows/*.yml`, `*.yaml` | repo root |
| zizmor | workflows plus `**/action.yml`, `**/action.yaml` | repo root |
| typos | every file | repo root |
| vale | `**/*.md`, `*.mdx`, `*.txt` | repo root |
| rules | per rule, see `embedded/rules.toml` | in process |
| eslint | JavaScript and TypeScript | nearest dir with `node_modules/.bin/eslint` |
| tsc-strict | TypeScript, whole project | nearest dir with `tsconfig.json`, when `node_modules` exists there or in a parent inside the repo |
| eslint-strict | TypeScript | nearest dir with `node_modules` |
| ruff-strict | `**/*.py`, `*.pyi` | repo root |
| ty-strict | `**/*.py`, `*.pyi` | nearest dir with `pyproject.toml` |
| shellcheck-strict | `*.sh`, `*.bash`, and files whose shebang runs sh, dash or bash | repo root |

Flags, versions and globs are the ones from the old `hk.pkl`. Only the output format flags changed to structured ones. A check with no files in scope does not run. A workspace check with no workspace for a file skips that file without a message. A file that has no workspace for ty-strict (no `pyproject.toml`) is skipped too, same as in hk.

Tool output is kept only when it points at a line that was added in a file in scope. Paths are resolved to repo-relative paths first, whether the tool printed a `file://` URI, a path relative to its working directory or an absolute path.

## Choices

- **pygit2 for diffs.** It returns added line numbers from libgit2 with no diff text to parse. libgit2 has no public call that equals `git diff HEAD`, and a plain tree-to-workdir diff misses force-added ignored files and reports renamed files as all new. tighten diffs HEAD against the index, merges in the index-to-workdir diff and runs rename detection, then takes untracked files from the workdir diff because the merge drops their content. `tests/parity` checks the result against `git diff HEAD -U0`, `git ls-files --others --exclude-standard` and `git diff --cached -U0` on a fixture with modified, staged, staged-then-edited, renamed, deleted, untracked and force-added ignored files.
- **Unborn HEAD.** A repo with no commits is diffed against the empty tree, so its first commit is checked in full.
- **Binary files** have no added lines and are never checked.
- **pydantic models** validate SARIF, shellcheck json1, actionlint JSON, eslint JSON and vale JSON. Output that fails validation counts as a tool failure. Typed models avoid `Any`, which jmespath would return.
- **SARIF where it works.** ruff, ty, typos and zizmor share one SARIF model. typos also has a JSON lines format, but SARIF lets it reuse the model.
- **A small regex parser for tsc.** tsc has no structured output and no maintained Python parser was found. The file part is matched lazily up to the `(line,col): error TSnnnn:` tail, and paths with parentheses such as Next.js route groups parse. The parser is strict. A line that is not a diagnostic, a diagnostic with no file and a diagnostic on a non-source file such as `tsconfig.json` all count as tool failures.
- **Exit codes per check.** Each check lists the codes that mean success and the codes that mean findings. Any other code is a failure with an excerpt of stderr. A findings code with no parsed findings is also a failure. A finding without a line number becomes a failure with its message.
- **vale without `--no-exit`.** Its exit code now means something, and its config error JSON (a `Code` such as E201) is a failure.
- **The semgrep rules run in process.** All four are `generic` regex rules. They live in `embedded/rules.toml` as TOML literal strings. The patterns are byte for byte the YAML ones. Python `re` compiles them unchanged. They run on each added line. Include and exclude globs use semgrep style matching through `PurePosixPath.match`, where `*.js` matches at any depth. The exclude for the rules file itself is now `embedded/rules.toml`. The tool name in a finding is the rule id.
- **hk globs** are matched with `PurePosixPath.full_match` on repo-relative paths. `**/*.ts` and `**/action.yml` match files at the repo root too.
- **Workspaces follow hk.** For each file tighten walks up to the repo root and takes the nearest directory where the indicator path exists. Each workspace gets one run with its directory as the working directory and file paths relative to it.
- **tsc-strict node_modules condition, generalized.** hk evaluates `test -d node_modules` in its own working directory, which is the repo root. That would skip a package with its own `node_modules` when the root has none. tighten runs tsc-strict when `node_modules` exists in the workspace directory or any parent up to the repo root, which covers both the hoisted root layout and per-package installs.
- **Shebang detection** uses `identify.parse_shebang` and `tags_from_interpreter` from [identify](https://github.com/pre-commit/identify). `parse_shebang_from_file` and `tags_from_path` only read shebangs of executable files, while hk also reads them for files with no extension. tighten reads the shebang when the file has no extension or is executable, as hk does. identify tags `dash` without `sh`, so dash is accepted explicitly. identify does not understand `#!/bin/env bash`, which hk does. `#!/usr/bin/env bash` and `#!/usr/bin/env -S bash` work.
- **`-x` for shellcheck** is passed on the command line instead of `SHELLCHECK_OPTS`.
- **Threads and process groups.** One thread per check and workspace. Each tool starts in its own process group with stdin closed. On timeout tighten snapshots the tool's descendants with psutil, kills the process group and then each process in the snapshot, so children that left the group with `setsid` die too. A descendant whose parent already exited is reparented to launchd and tighten cannot find it. That is the limit of this approach. The timeout is 150 seconds per run, leaving headroom under the 180 second hook limit.
- **Missing tools** count only when their check has files in scope. The message tells the agent to report it and not to install anything.
- **`--no-warn-ignored` for repo eslint.** This is the one flag added to an hk command. Without it eslint 10 prints a warning with no line number for each passed file that the repo config ignores, and tighten would count that as a failure on every change to an ignored file. hk with reviewdog dropped those warnings. eslint-strict already had the flag.
- **Tool failures fail the run.** hk ignored them. A repo with `node_modules/.bin/eslint` but no eslint config now fails the eslint check with exit 2 from eslint.
- **Embedded configs** (`zizmor.yml`, `eslint.config.mjs`, `vale/.vale.ini`) are passed by absolute path. The eslint config and zizmor config are copied unchanged.
- **Argument handling is two lines of code.** There is one optional positional with two values. argparse would also exit 2 on usage errors, and tighten uses 2 for findings.
- **`_typos.toml`** excludes the vendored vale style, whose regex fragments are word pieces that typos reads as misspellings, and allows `xdescribe`, a test function name in the rules.
- **The PEP 723 block is the only comment** in the script, because uv needs it.

## Verified tool behavior

These come from real runs on this machine (`tests/verify-tools` reproduces most of them) with the versions below.

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

tsc reports config errors with the same exit code as type errors, so tighten tells them apart by how the lines look. A config error has no file location or points at a non-source file.

## To verify

- **ty, typos and zizmor accept SARIF.** Yes. `ty --output-format sarif`, `typos --format sarif` and `zizmor --format sarif` all print SARIF 2.1.0. typos puts the message in `message.markdown` and leaves `ruleId` null. typos prints one document for several files. ty can print a result with no location (a missing path) and tighten turns that into a failure. zizmor exits 0 with SARIF even with findings, as its docs say.
- **vale JSON for a normal alert.** An object keyed by the path as given on the command line, each value a list of alerts with `Check`, `Message`, `Line`, `Span`, `Severity`, `Match` and more. A config error is one object with `Code`, `Text`, `Path`, `Line` and `Span`.
- **actionlint JSON fields.** `message`, `filepath`, `line`, `column`, `end_column`, `kind` and `snippet`. `-ignore` still works with `-format`. A workflow whose only error is the ignored unknown label gives `[]` and exit 0. `-oneline` is dropped because `-format` replaces it.
- **Staged diff API.** `head_tree.diff_to_index(repo.index)` with rename detection matches `git diff --cached -U0` on the parity fixture. Opening the repo with `RepositoryOpenFlag.FROM_ENV` makes pygit2 read `GIT_INDEX_FILE` (checked with a temporary index made by `GIT_INDEX_FILE=x git add`).
- **Exit codes.** See the table above.
- **How vale finds the vendored style.** A relative `StylesPath` is resolved against the directory of the config file, not the working directory. A config at `/tmp/bad.ini` with `StylesPath = nope` reports `/tmp/nope` missing, and the embedded config finds `embedded/vale/styles/ai-tells` when vale runs from `/`. `--config` wins over `VALE_CONFIG_PATH` (a run with `VALE_CONFIG_PATH=/missing.ini` still works). No `vale sync` is needed because the config has no `Packages` line.
- **ruff and ty on the extensionless script.** Both lint `tighten` when it is passed by name. A copy with a deliberate violation appended gets ANN201 and ANN001 from ruff and `invalid-assignment` from ty. ty runs with `--python` pointing at the script environment from `uv sync --script tighten` and `uv python find --script tighten`, so pygit2, pydantic, psutil and identify resolve. Without that environment ty reports `import-not-found` for them.

## Tests

```
tests/parity          pygit2 against git CLI diffs, prints PASS or FAIL
tests/verify-design   glob semantics, shebang detection, rules compile and match the semgrep YAML, GIT_INDEX_FILE
tests/verify-tools    prints tool output and exit codes for the questions above
tests/self-lint       ruff and ty strict on the scripts, plus a violation copy that both must flag
```

## Credits

`embedded/vale/styles/ai-tells` is [tbhb/vale-ai-tells](https://github.com/tbhb/vale-ai-tells) v1.37.0, the release that `Packages = ai-tells` resolved to, by Tony Burns under the MIT license. Its license is in that directory.

## License

MIT, see `LICENSE`.
