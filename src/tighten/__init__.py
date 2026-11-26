import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field, replace
from importlib.resources import files
from pathlib import Path, PurePosixPath

import psutil
import pygit2
from identify.identify import parse_shebang, tags_from_interpreter
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel, to_pascal
from pygit2.enums import DeltaStatus, DiffOption, FileStatus, RepositoryOpenFlag

TIMEOUT = 150
BUDGET = 165
DEADLINE = time.monotonic() + BUDGET
RUNNING: set[subprocess.Popen[str]] = set()
EMBEDDED = Path(str(files("tighten") / "embedded"))
HOME = Path.home()
TSC_CACHE = f"{HOME}/.cache/agent-tsc"
TYPESCRIPT_VERSION = "5.9.3"
ESLINT_VERSION = "10.15.0"
DENO = ("deno", "run", "--quiet", "--no-config", "--no-lock", "--node-modules-dir=none", "--no-prompt", "--allow-read", "--allow-env")
WORKFLOWS = (".github/workflows/*.yml", ".github/workflows/*.yaml")
TYPESCRIPT = ("**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts")
JAVASCRIPT = ("**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs")
PYTHON = ("**/*.py", "**/*.pyi")
SHELL = ("**/*.sh", "**/*.bash")
SHEBANG_SHELLS = frozenset({"sh", "dash", "bash"})
TSC_FLAGS = (
    "--strict", "--noImplicitAny", "--strictNullChecks", "--strictFunctionTypes", "--strictBindCallApply",
    "--strictPropertyInitialization", "--useUnknownInCatchVariables", "--noImplicitThis",
    "--noUncheckedIndexedAccess", "--noImplicitOverride", "--noImplicitReturns", "--noFallthroughCasesInSwitch",
)
UNTRACKED = DiffOption.INCLUDE_UNTRACKED | DiffOption.RECURSE_UNTRACKED_DIRS | DiffOption.SHOW_UNTRACKED_CONTENT
INDEX_FLAGS = FileStatus.INDEX_NEW | FileStatus.INDEX_MODIFIED | FileStatus.INDEX_RENAMED | FileStatus.INDEX_TYPECHANGE
WORKTREE_FLAGS = FileStatus.WT_MODIFIED | FileStatus.WT_DELETED | FileStatus.WT_RENAMED | FileStatus.WT_TYPECHANGE
STOP_TRAILER = (
    "tighten found problems in lines you changed this session. Fix them without suppression comments. "
    "If a rule does not fit this repo, change it in the repo lint config and say why. "
    "If something is outside your task, list it in your reply and ask."
)
STAGED_TRAILER = (
    "tighten: fix the code above without adding suppression comments. "
    "If a rule does not fit this repo, change it in the repo lint config and say why. "
    "If two rules contradict each other, stop and report both rule names to the user."
)
USAGE = "usage: tighten [stop|staged]"
HOOK_EVENTS = frozenset({"Stop", "SubagentStop", "PreToolUse"})
DATA_FILES = ("**/*.csv", "**/*.tsv", "**/*.json", "**/*.jsonl", "**/*.lock", "**/*.svg", "**/*.ipynb")
UNTYPED_CELL = re.compile(r"\s*(async\s+)?def _\(")
CELL_ANNOTATIONS = ("ANN001", "ANN201", "ANN202")
SCRATCHPAD = re.compile(r"/(private/)?tmp/claude-\d+/[^/]+/[^/]+/scratchpad(/|$)")

type Added = dict[str, set[int]]
type Raw = tuple[str, int | None, str]
type DocRaw = tuple[str, int, str]


class ToolFailureError(Exception):
    pass


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, frozen=True)


class SarifMessage(Model):
    text: str | None = None
    markdown: str | None = None


class SarifRegion(Model):
    start_line: int | None = None


class SarifArtifact(Model):
    uri: str


class SarifPhysical(Model):
    artifact_location: SarifArtifact
    region: SarifRegion | None = None


class SarifLocation(Model):
    physical_location: SarifPhysical | None = None


class SarifResult(Model):
    rule_id: str | None = None
    message: SarifMessage
    locations: list[SarifLocation] = []


class SarifRun(Model):
    results: list[SarifResult]


class Sarif(Model):
    runs: list[SarifRun]


class ShellcheckComment(Model):
    file: str
    line: int
    code: int
    message: str


class Shellcheck(Model):
    comments: list[ShellcheckComment]


class ActionlintError(Model):
    message: str
    filepath: str
    line: int
    kind: str


class EslintMessage(Model):
    rule_id: str | None = None
    message: str
    line: int | None = None


class EslintFile(Model):
    file_path: str
    messages: list[EslintMessage]


class ValeModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_pascal, frozen=True)


class ValeAlert(ValeModel):
    check: str
    message: str
    line: int


class ValeError(ValeModel):
    code: str
    text: str
    path: str = ""
    line: int = 0


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    message: str
    pattern: str
    include: list[str] = []
    exclude: list[str] = []


class Rules(BaseModel):
    rules: list[Rule]


class StopInput(BaseModel):
    session_id: str
    cwd: str
    hook_event_name: str = "Stop"
    stop_hook_active: bool = False
    agent_id: str | None = None


SARIF = TypeAdapter(Sarif)
SHELLCHECK = TypeAdapter(Shellcheck)
ACTIONLINT = TypeAdapter(list[ActionlintError])
ESLINT = TypeAdapter(list[EslintFile])
VALE = TypeAdapter(ValeError | dict[str, list[ValeAlert]])
VALE_ERROR = TypeAdapter(ValeError)
TSC_LINE = re.compile(r"(?P<file>.+?)\((?P<line>\d+),\d+\): (?:error|warning) (?P<code>TS\d+): (?P<msg>.*)")
TSC_GLOBAL = re.compile(r"(?:error|warning) TS\d+: .*")


def parse_sarif(out: str) -> list[Raw]:
    raws: list[Raw] = []
    for result in (r for run in SARIF.validate_json(out).runs for r in run.results):
        text = result.message.text or result.message.markdown or ""
        message = f"{result.rule_id} {text}" if result.rule_id else text
        physical = [loc.physical_location for loc in result.locations if loc.physical_location]
        if not physical:
            raws.append(("", None, message))
        for loc in physical:
            raws.append((loc.artifact_location.uri, loc.region.start_line if loc.region else None, message))
    return raws


def parse_shellcheck(out: str) -> list[Raw]:
    return [(c.file, c.line, f"SC{c.code} {c.message}") for c in SHELLCHECK.validate_json(out).comments]


def parse_actionlint(out: str) -> list[Raw]:
    return [(e.filepath, e.line, f"{e.message} [{e.kind}]") for e in ACTIONLINT.validate_json(out)]


def parse_eslint(out: str) -> list[Raw]:
    return [
        (f.file_path, m.line, f"{m.rule_id} {m.message}" if m.rule_id else m.message)
        for f in ESLINT.validate_json(out)
        for m in f.messages
    ]


def parse_vale(out: str) -> list[Raw]:
    parsed = VALE.validate_json(out)
    if isinstance(parsed, ValeError):
        raise ToolFailureError(f"{parsed.code} {parsed.text}")
    return [(path, a.line, f"{a.check} {a.message}") for path, alerts in parsed.items() for a in alerts]


def parse_vale_errors(err: str) -> list[DocRaw]:
    decoder = json.JSONDecoder()
    raws: list[DocRaw] = []
    text = err.strip()
    index = 0
    while index < len(text):
        try:
            value, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            raise ToolFailureError(f"unreadable output: {excerpt(err)}") from None
        error = VALE_ERROR.validate_python(value)
        if not error.path:
            raise ToolFailureError(f"{error.code} {error.text}")
        raws.append((error.path, error.line, f"{error.code} {error.text}"))
        index = len(text) - len(text[index:].lstrip())
    return raws


def parse_tsc(out: str) -> list[Raw]:
    raws: list[Raw] = []
    for line in out.splitlines():
        if not line.strip() or line[0].isspace():
            continue
        if match := TSC_LINE.fullmatch(line):
            if not matches(match["file"], TYPESCRIPT + JAVASCRIPT):
                raise ToolFailureError(line)
            raws.append((match["file"], int(match["line"]), f"{match['code']} {match['msg']}"))
        elif TSC_GLOBAL.fullmatch(line):
            raise ToolFailureError(line)
        else:
            raise ToolFailureError(f"unreadable output: {line}")
    return raws


def matches(path: str, globs: Iterable[str]) -> bool:
    return any(PurePosixPath(path).full_match(g) for g in globs)


def has_shell_shebang(path: Path) -> bool:
    if path.suffix and not os.access(path, os.X_OK):
        return False
    try:
        with path.open("rb") as handle:
            command = parse_shebang(handle)
    except OSError:
        return False
    return bool(command) and bool(tags_from_interpreter(command[0]) & SHEBANG_SHELLS)


@dataclass(frozen=True)
class Job:
    cwd: Path
    files: list[str]


@dataclass(frozen=True)
class Check:
    name: str
    argv: Callable[[Job], list[str]]
    parse: Callable[[str], list[Raw]]
    clean: frozenset[int]
    found: frozenset[int]
    globs: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    drop: Callable[[str, str], bool] | None = None
    parse_stderr: Callable[[str], list[DocRaw]] | None = None
    shebang: bool = False
    indicator: str | None = None
    needs_node_modules: bool = False
    project: bool = False

    def selects(self, path: str, root: Path) -> bool:
        if matches(path, self.exclude):
            return False
        return matches(path, self.globs) or (self.shebang and has_shell_shebang(root / path))


def untyped_marimo_cell(text: str, message: str) -> bool:
    return message.startswith(CELL_ANNOTATIONS) and UNTYPED_CELL.match(text) is not None


def eslint_argv(job: Job) -> list[str]:
    return [str(job.cwd / "node_modules/.bin/eslint"), "-f", "json", "--no-warn-ignored", *job.files]


def tsc_argv(job: Job) -> list[str]:
    cache = f"{TSC_CACHE}{job.cwd}/strict.tsbuildinfo"
    return [
        *DENO, f"--allow-write={TSC_CACHE}", f"npm:typescript@{TYPESCRIPT_VERSION}/tsc", "-p", ".", "--noEmit",
        "--pretty", "false", "--composite", "false", "--incremental", "--tsBuildInfoFile", cache, *TSC_FLAGS,
    ]


CHECKS = (
    Check(
        "actionlint",
        lambda j: ["actionlint", "-ignore", 'label ".+" is unknown', "-format", "{{json .}}", *j.files],
        parse_actionlint, frozenset({0}), frozenset({1}), WORKFLOWS,
    ),
    Check(
        "zizmor",
        lambda j: [
            "zizmor", "--quiet", "--offline", "--no-ignores", "--no-progress", "--color", "never",
            "--strict-collection", "--config", str(EMBEDDED / "zizmor.yml"), "--format", "sarif", *j.files,
        ],
        parse_sarif, frozenset({0}), frozenset(), (*WORKFLOWS, "**/action.yml", "**/action.yaml"),
    ),
    Check(
        "typos",
        lambda j: ["typos", "--force-exclude", "--format", "sarif", *j.files],
        parse_sarif, frozenset({0}), frozenset({2}), ("**/*",), DATA_FILES,
    ),
    Check(
        "vale",
        lambda j: ["vale", "--config", str(EMBEDDED / "vale/.vale.ini"), "--output=JSON", *j.files],
        parse_vale, frozenset({0}), frozenset({1, 2}), ("**/*.md", "**/*.mdx", "**/*.txt"),
        parse_stderr=parse_vale_errors,
    ),
    Check(
        "eslint", eslint_argv, parse_eslint, frozenset({0}), frozenset({1}), JAVASCRIPT + TYPESCRIPT,
        indicator="node_modules/.bin/eslint",
    ),
    Check(
        "tsc-strict", tsc_argv, parse_tsc, frozenset({0}), frozenset({1, 2}), TYPESCRIPT,
        indicator="tsconfig.json", needs_node_modules=True, project=True,
    ),
    Check(
        "eslint-strict",
        lambda j: [
            *DENO, f"npm:eslint@{ESLINT_VERSION}", "-c", str(EMBEDDED / "eslint.config.mjs"), "-f", "json",
            "--no-warn-ignored", *j.files,
        ],
        parse_eslint, frozenset({0}), frozenset({1}), TYPESCRIPT, indicator="node_modules",
    ),
    Check(
        "ruff-strict",
        lambda j: [
            "ruff", "check", "--no-fix", "--ignore-noqa", "--force-exclude", "--output-format", "sarif",
            "--extend-select", "ANN,B,BLE,ASYNC,PGH,RUF", "--extend-ignore", "B008,RUF005,RUF010,RUF021,RUF022,RUF023",
            *j.files,
        ],
        parse_sarif, frozenset({0}), frozenset({1}), PYTHON, drop=untyped_marimo_cell,
    ),
    Check(
        "ty-strict",
        lambda j: [
            "ty", "check", "--output-format", "sarif", "--color", "never", "--force-exclude",
            "-c", "analysis.respect-type-ignore-comments=false", "--ignore", "import-not-found",
            "--error", "missing-type-argument", "--error", "possibly-unresolved-reference",
            "--error", "possibly-missing-attribute", "--error", "possibly-missing-import",
            "--error", "unused-awaitable", "--error", "deprecated", "--error", "redundant-cast", *j.files,
        ],
        parse_sarif, frozenset({0}), frozenset({1}), PYTHON, indicator="pyproject.toml",
    ),
    Check(
        "shellcheck-strict",
        lambda j: [
            "shellcheck", "-x", "-f", "json1", "-S", "info",
            "-o", "check-set-e-suppressed,check-extra-masked-returns,check-unassigned-uppercase", *j.files,
        ],
        parse_shellcheck, frozenset({0}), frozenset({1}), SHELL, shebang=True,
    ),
)


@dataclass(order=True, frozen=True)
class Finding:
    file: str
    line: int
    tool: str
    message: str


@dataclass
class Report:
    findings: set[Finding] = field(default_factory=set[Finding])
    failures: list[str] = field(default_factory=list[str])

    def fail(self, tool: str, reason: str) -> None:
        self.failures.append(f"tighten: {tool} failed: {reason}")


def added_lines(patches: Iterable[pygit2.Patch | None]) -> Added:
    added: Added = {}
    for patch in patches:
        if patch is None or patch.delta.status == DeltaStatus.DELETED or patch.delta.is_binary:
            continue
        lines = {line.new_lineno for hunk in patch.hunks for line in hunk.lines if line.origin == "+"}
        if lines:
            added.setdefault(patch.delta.new_file.path, set()).update(lines)
    return added


def head_tree(repo: pygit2.Repository) -> pygit2.Tree:
    if repo.head_is_unborn:
        return repo[repo.TreeBuilder().write()].peel(pygit2.Tree)
    return repo.head.peel(pygit2.Tree)


def changes(repo: pygit2.Repository, *, staged: bool) -> Added:
    diff = head_tree(repo).diff_to_index(repo.index, context_lines=0)
    untracked: list[pygit2.Patch | None] = []
    if not staged:
        workdir = repo.index.diff_to_workdir(flags=UNTRACKED, context_lines=0)
        diff.merge(workdir)
        untracked = [p for p in workdir if p is not None and p.delta.status == DeltaStatus.UNTRACKED]
    diff.find_similar()
    added = added_lines(diff)
    for path, lines in added_lines(untracked).items():
        added.setdefault(path, set()).update(lines)
    return added


def excerpt(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return " | ".join(lines[-8:])[-800:] or "no output"


def kill_tree(proc: subprocess.Popen[str]) -> None:
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    with suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    for child in children:
        with suppress(psutil.NoSuchProcess):
            child.kill()
    psutil.wait_procs(children, timeout=5)
    with suppress(subprocess.TimeoutExpired):
        proc.communicate(timeout=5)


def execute(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    timeout = min(TIMEOUT, DEADLINE - time.monotonic())
    if timeout <= 0:
        raise ToolFailureError(f"skipped, the {BUDGET}s budget for this run is used up")
    with subprocess.Popen(
        argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace", process_group=0,
    ) as proc:
        RUNNING.add(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise ToolFailureError(f"timed out after {timeout:.0f}s, killed with its child processes") from None
        finally:
            RUNNING.discard(proc)
        return proc.returncode, out, err


def terminate(signum: int, _frame: object) -> None:
    for proc in list(RUNNING):
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    os._exit(128 + signum)


def missing(tool: str) -> str:
    return f"{tool} is not installed or not on PATH. Report this to the user and do not install it."


def run_job(check: Check, job: Job, root: Path, added: Added, report: Report) -> None:
    argv = check.argv(job)
    if shutil.which(argv[0]) is None:
        report.fail(check.name, missing(argv[0]))
        return
    try:
        code, out, err = execute(argv, job.cwd)
        if code not in check.clean | check.found:
            raise ToolFailureError(f"exit {code}: {excerpt(err or out)}")
        try:
            docs = check.parse_stderr(err) if check.parse_stderr and err.strip() else []
            raws = check.parse(out)
        except ValidationError:
            raise ToolFailureError(f"unreadable output: {excerpt(out or err)}") from None
        if code in check.found and not raws and not docs:
            raise ToolFailureError(f"exit {code} with no findings: {excerpt(err or out)}")
    except FileNotFoundError:
        report.fail(check.name, missing(argv[0]))
        return
    except ToolFailureError as error:
        report.fail(check.name, str(error))
        return
    texts: dict[str, list[str]] = {}
    for path, line, message in raws:
        if line is None:
            report.fail(check.name, message)
            continue
        file = repo_path(path, job.cwd, root)
        if file is None or line not in added.get(file, set()):
            continue
        if check.drop is not None:
            if file not in texts:
                texts[file] = read_lines(root / file)
            text = texts[file][line - 1] if line <= len(texts[file]) else ""
            if check.drop(text, message):
                continue
        report.findings.add(Finding(file, line, check.name, message))
    for path, line, message in docs:
        file = repo_path(path, job.cwd, root)
        if file is None:
            report.fail(check.name, f"{path}: {message}")
        elif file in added:
            report.findings.add(Finding(file, line, check.name, message))


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def repo_path(path: str, cwd: Path, root: Path) -> str | None:
    resolved = (Path.from_uri(path) if path.startswith("file://") else cwd / path).resolve()
    if not resolved.is_relative_to(root):
        return None
    return resolved.relative_to(root).as_posix()


def workspace(path: str, root: Path, indicator: str) -> Path | None:
    for directory in (root / path).parents:
        if (directory / indicator).exists():
            return directory
        if directory == root:
            break
    return None


def has_node_modules(directory: Path, root: Path) -> bool:
    return any((d / "node_modules").is_dir() for d in (directory, *directory.parents) if d.is_relative_to(root))


def plan(check: Check, files: list[str], root: Path) -> list[Job]:
    selected = [f for f in files if check.selects(f, root)]
    if check.indicator is None:
        return [Job(root, selected)] if selected else []
    groups: dict[Path, list[str]] = {}
    for file in selected:
        directory = workspace(file, root, check.indicator)
        if directory is not None and (not check.needs_node_modules or has_node_modules(directory, root)):
            groups.setdefault(directory, []).append((root / file).relative_to(directory).as_posix())
    ordered = sorted(groups.items(), key=lambda item: len(item[0].parts), reverse=True)
    return [Job(d, [] if check.project else fs) for d, fs in ordered]


def run_rules(root: Path, added: Added, report: Report) -> None:
    rules = Rules.model_validate(tomllib.loads((EMBEDDED / "rules.toml").read_text())).rules
    for path, lines in added.items():
        active = [r for r in rules if (not r.include or matches(path, r.include)) and not matches(path, r.exclude)]
        if not active:
            continue
        text = read_lines(root / path)
        for number in lines:
            for rule in active:
                if number <= len(text) and re.search(rule.pattern, text[number - 1]):
                    report.findings.add(Finding(path, number, rule.id, rule.message))


def lint(root: Path, added: Added, report: Report) -> None:
    files = sorted(added)
    jobs = [(check, job) for check in CHECKS for job in plan(check, files, root)]
    run_rules(root, added, report)
    with ThreadPoolExecutor(max_workers=max(len(jobs), 1)) as pool:
        for future in [pool.submit(run_job, c, j, root, added, report) for c, j in jobs]:
            future.result()


def render(report: Report, trailer: str) -> str:
    lines: list[str] = []
    current = None
    for finding in sorted(report.findings):
        if finding.file != current:
            current = finding.file
            lines.append(current)
        lines.append(f"  {finding.line} [{finding.tool}] {finding.message}")
    lines += sorted(set(report.failures))
    if report.findings:
        lines.append(f"\n{trailer}")
    return "\n".join(lines)


def print_report(report: Report, trailer: str) -> int:
    if report.findings or report.failures:
        print(render(report, trailer), file=sys.stderr)
    return 2 if report.findings or report.failures else 0


def warn(failures: list[str]) -> int:
    if failures:
        print(json.dumps({"systemMessage": "\n".join(sorted(set(failures)))}))
    return 0


def open_repo(cwd: str, flags: RepositoryOpenFlag) -> pygit2.Repository | None:
    found = pygit2.discover_repository(cwd)
    if found is None:
        return None
    repo = pygit2.Repository(found, flags)
    return None if repo.workdir is None else repo


def skipped(repo: pygit2.Repository, path: str) -> bool:
    return any(repo.get_attr(path, attr) in (True, "true") for attr in ("linguist-vendored", "linguist-generated"))


def existing(repo: pygit2.Repository, added: Added) -> Added:
    root = Path(repo.workdir)
    return {path: lines for path, lines in added.items() if (root / path).is_file() and not skipped(repo, path)}


def shown(path: Path, cwd: Path) -> str:
    return path.relative_to(cwd).as_posix() if path.is_relative_to(cwd) else str(path)


def guards() -> Path:
    return Path(os.environ.get("TMPDIR") or "/tmp") / "claude-guards"


def marked(marker: Path) -> set[Path]:
    if not marker.is_file():
        return set()
    return {Path(line).resolve() for line in marker.read_text().splitlines() if line.strip()}


def changed_since(directories: Iterable[str], since: float, report: Report) -> set[Path]:
    seen: set[str] = set()
    found: set[Path] = set()
    for directory in directories:
        repo = open_repo(directory, RepositoryOpenFlag.DEFAULT)
        if repo is None or repo.workdir in seen:
            continue
        seen.add(repo.workdir)
        root = Path(repo.workdir).resolve()
        try:
            paths = [root / p for p in changes(repo, staged=False)]
        except OSError as error:
            report.fail("tighten", f"{root}: {error}")
            continue
        found |= {p for p in paths if p.is_file() and p.stat().st_mtime > since}
    return found


def session_paths(data: StopInput, report: Report) -> set[Path] | None:
    directory = guards()
    if data.agent_id:
        return marked(directory / f"{data.session_id}.{data.agent_id}")
    marker = directory / data.session_id
    if not marker.is_file():
        return None
    info = marker.stat()
    since = getattr(info, "st_birthtime", info.st_mtime)
    own = marked(marker)
    delegated: set[Path] = set()
    for other in directory.glob(f"{data.session_id}.*"):
        if not other.name.endswith(".last"):
            delegated |= marked(other)
    directories = {data.cwd, *(str(p.parent) for p in own if p.parent.is_dir())}
    return own | (changed_since(directories, since, report) - delegated)


def repeated(data: StopInput, text: str) -> bool:
    if not data.agent_id:
        return False
    last = guards() / f"{data.session_id}.{data.agent_id}.last"
    if last.is_file() and last.read_text() == text:
        return True
    last.write_text(text)
    return False


def stop() -> int:
    data = StopInput.model_validate_json(sys.stdin.read())
    if data.hook_event_name not in HOOK_EVENTS or (data.stop_hook_active and not data.agent_id):
        return 0
    scan = Report()
    candidates = session_paths(data, scan)
    if not candidates:
        return warn(scan.failures)
    candidates = {p for p in candidates if not SCRATCHPAD.match(p.as_posix())}
    report = lint_paths(candidates, Path(data.cwd).resolve())
    report.failures += scan.failures
    if not report.findings:
        return warn(report.failures)
    text = render(report, STOP_TRAILER)
    if data.stop_hook_active or repeated(data, text):
        return warn([f"tighten: a subagent finished with findings it was already shown:\n{text}"])
    print(text, file=sys.stderr)
    return 2


def lint_paths(candidates: set[Path], cwd: Path) -> Report:
    groups: dict[str, set[Path]] = {}
    for path in candidates:
        found = pygit2.discover_repository(str(path.parent)) if path.is_file() else None
        if found is not None:
            groups.setdefault(found, set()).add(path)
    report = Report()
    for found, paths in groups.items():
        repo = pygit2.Repository(found)
        if repo.workdir is None:
            continue
        root = Path(repo.workdir).resolve()
        wanted = {p.relative_to(root).as_posix() for p in paths if p.is_relative_to(root)}
        part = Report()
        try:
            lint(root, existing(repo, {p: ls for p, ls in changes(repo, staged=False).items() if p in wanted}), part)
        except OSError as error:
            part.fail("tighten", f"{root}: {error}")
        report.failures += part.failures
        report.findings |= {replace(f, file=shown(root / f.file, cwd)) for f in part.findings}
    return report


def staged() -> int:
    repo = open_repo(os.getcwd(), RepositoryOpenFlag.FROM_ENV)
    if repo is None:
        return 0
    root = Path(repo.workdir).resolve()
    report = Report()
    added = changes(repo, staged=True)
    status = repo.status()
    for path in sorted(added):
        flags = status.get(path, FileStatus.CURRENT)
        if flags & INDEX_FLAGS and flags & WORKTREE_FLAGS:
            report.fail("staged", f"{path} is partially staged. Stage the rest or stash it so linters see what is committed")
            del added[path]
    lint(root, existing(repo, added), report)
    return print_report(report, STAGED_TRAILER)


def manual() -> int:
    repo = open_repo(os.getcwd(), RepositoryOpenFlag.DEFAULT)
    if repo is None:
        print("tighten: not inside a git work tree", file=sys.stderr)
        return 1
    root = Path(repo.workdir).resolve()
    report = Report()
    lint(root, existing(repo, changes(repo, staged=False)), report)
    return print_report(report, STAGED_TRAILER)


def main() -> int:
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, terminate)
    args = sys.argv[1:]
    modes: dict[str, Callable[[], int]] = {"stop": stop, "staged": staged}
    if not args:
        return manual()
    if len(args) == 1 and args[0] in modes:
        return modes[args[0]]()
    print(USAGE, file=sys.stderr)
    return 1
