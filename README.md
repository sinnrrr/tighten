# tighten

`tighten` gets your Claude a bit of that IDE feedback: type checkers, linters and spell checkers.

![tighten in a Claude Code session](demo/demo.gif)

## Setup

Install [uv](https://docs.astral.sh/uv/), then add the Claude Code plugin:

```sh
claude plugin marketplace add sinnrrr/tighten
claude plugin install tighten@tighten
```

## What it runs

| Language | Tools |
|---|---|
| Python | [ruff](https://docs.astral.sh/ruff/), [ty](https://docs.astral.sh/ty/) |
| TypeScript | [tsc](https://www.typescriptlang.org/), [ESLint](https://eslint.org/) |
| JavaScript | [ESLint](https://eslint.org/) |
| Shell | [ShellCheck](https://www.shellcheck.net/) |
| GitHub Actions | [actionlint](https://github.com/rhysd/actionlint), [zizmor](https://docs.zizmor.sh/) |
| Markdown and text | [Vale](https://vale.sh/) with [vale-ai-tells](https://github.com/tbhb/vale-ai-tells) |
| All files | [typos](https://github.com/crate-ci/typos), built-in rules against suppression comments and skipped tests |

## License

MIT. The bundled vale style is [tbhb/vale-ai-tells](https://github.com/tbhb/vale-ai-tells) by Tony Burns, MIT licensed.
