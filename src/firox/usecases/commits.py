"""Mine commit-replay tasks from the owner's public repositories.

A good candidate is a small commit (at most 3 files and 150 changed lines by
default) that changes source code *and* tests: replayed from its parent, with
its tests put in place, the task is "make these tests pass", and the commit
itself is the reference solution.

Tasks only **reference** public commits: the repository's public URL and the
commit hash. No code from the repository is copied into firox. A repository
is only used if it is public on GitHub and has a license (recorded in the
task), and never if it is on the exclusion list.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..evals.exclude import is_excluded
from .classify import classify
from .requests import Request

TEST_PATH = re.compile(r"(^|/)src/test/|(^|/)tests?/|(^|/)test_[^/]+\.py$|_test\.py$|Tests?\.java$|_test\.go$")
_CONVENTIONAL = re.compile(r"^\s*\w+(\([^)]*\))?!?:\s*")
_GITHUB = re.compile(r"github\.com[:/]([^/]+)/([^/.]+?)(\.git)?/?$")


class MinerError(Exception):
    pass


@dataclass(frozen=True)
class RepoInfo:
    path: Path
    url: str  # https://github.com/<owner>/<name>
    visibility: str
    license: str | None


@dataclass
class Candidate:
    sha: str
    subject: str
    body: str
    files: list[tuple[str, int, int]] = field(default_factory=list)  # (path, added, removed)
    deleted: set[str] = field(default_factory=set)  # files the commit deleted

    @property
    def tests(self) -> list[str]:
        """Test files that exist after the commit: the ones to put in place and make pass."""
        return [p for p, _, _ in self.files if TEST_PATH.search(p) and p not in self.deleted]

    @property
    def sources(self) -> list[str]:
        return [p for p, _, _ in self.files if not TEST_PATH.search(p)]

    @property
    def lines(self) -> int:
        return sum(a + r for _, a, r in self.files)

    @property
    def category(self) -> str:
        guess = classify(Request("git", "", None, None, self.subject, 1, tools={"Edit": 1})).category
        return guess if guess and guess not in ("followup", "meta", "explain", "git") else "fix"


def repo_info(repo: Path) -> RepoInfo:
    """Check that the repository may be used: on GitHub, public, licensed, not excluded."""
    repo = repo.expanduser().resolve()
    if is_excluded(repo):
        raise MinerError(f"{repo} is on the exclusion list")
    if not (repo / ".git").exists():
        raise MinerError(f"{repo} is not a git repository")
    remote = _git(repo, "remote", "get-url", "origin").strip()
    match = _GITHUB.search(remote)
    if not match:
        raise MinerError(f"{repo}: origin ({remote or 'none'}) is not a GitHub repository, so it can't be checked as public")
    owner, name = match.group(1), match.group(2)
    data = github_repo(owner, name)
    if data.get("visibility") != "public" or data.get("private"):
        raise MinerError(f"{owner}/{name} is not public: only public repositories can be referenced")
    license_id = (data.get("license") or {}).get("spdx_id")
    if not license_id or license_id == "NOASSERTION":
        raise MinerError(f"{owner}/{name} has no license GitHub recognizes, so its use can't be checked")
    return RepoInfo(repo, f"https://github.com/{owner}/{name}", "public", license_id)


def github_repo(owner: str, name: str) -> dict:
    """Repository metadata from the GitHub API, through the gh command line."""
    if not shutil.which("gh"):
        raise MinerError("the GitHub CLI (gh) is needed to check that a repository is public")
    try:
        proc = subprocess.run(["gh", "api", f"repos/{owner}/{name}"], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise MinerError(f"GitHub didn't answer about {owner}/{name} within 30 s; try again") from None
    if proc.returncode != 0:
        raise MinerError(f"can't read {owner}/{name} from GitHub: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def candidates(repo: Path, *, scan: int = 400, max_files: int = 3, max_lines: int = 150,
               category: str | None = None) -> list[Candidate]:
    """Small commits that change source code and tests, newest first."""
    # --no-renames: a renamed file is a deletion plus an addition with real paths,
    # instead of git's "dir/{old => new}/File" notation, which isn't a path.
    out = _git(repo, "log", "--no-merges", "--no-renames", f"-n{scan}",
               "--format=@@%H%x1f%P%x1f%s%x1f%b%x1e", "--numstat")
    found = []
    for block in out.split("@@")[1:]:
        header, _, numstat = block.partition("\x1e")
        sha, parents, subject, body = (header.split("\x1f") + ["", "", ""])[:4]
        if not parents.strip():
            continue  # a first commit has no parent to replay it from
        files = []
        for row in numstat.strip().splitlines():
            added, removed, path = (row.split("\t") + ["", "", ""])[:3]
            if added.isdigit() and removed.isdigit():  # binary files show "-"
                files.append((path, int(added), int(removed)))
        c = Candidate(sha.strip(), subject.strip(), body.strip(), files)
        if not files or len(files) > max_files or c.lines > max_lines or not c.tests or not c.sources:
            continue
        c.deleted = _deleted(repo, c.sha)
        if not c.tests:  # every test file the commit touched was deleted: nothing to make pass
            continue
        if category and c.category != category:
            continue
        found.append(c)
    return found


def _deleted(repo: Path, sha: str) -> set[str]:
    out = _git(repo, "show", "--no-renames", "--name-status", "--format=", sha)
    return {line.split("\t", 1)[1] for line in out.splitlines() if line.startswith("D\t")}


def test_command(repo: Path, tests: list[str]) -> str:
    """The command that runs the given test files, offline where the build tool supports it."""
    if (repo / "pom.xml").is_file():
        modules = sorted({t.split("src/test/")[0].rstrip("/") for t in tests if "src/test/" in t} - {""})
        classes = ",".join(sorted({Path(t).stem for t in tests}))
        module_flags = f"-pl {','.join(modules)} -am " if modules else ""
        return f"mvn -o -q {module_flags}test -Dtest={classes} -Dsurefire.failIfNoSpecifiedTests=false"
    if (repo / "gradlew").is_file():
        return "./gradlew --offline test " + " ".join(f"--tests {Path(t).stem}" for t in tests)
    if any(t.endswith(".py") for t in tests):
        return "python3 -m pytest -q " + " ".join(sorted(tests))
    raise MinerError("don't know how to run these tests (no pom.xml, gradlew or Python tests)")


def task_id(info: RepoInfo, c: Candidate) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _CONVENTIONAL.sub("", c.subject).lower()).strip("-")[:40].rstrip("-")
    return f"{info.url.rsplit('/', 1)[-1]}-{c.sha[:7]}-{slug}"


def draft_toml(info: RepoInfo, c: Candidate, *, category: str | None = None, prompt: str | None = None) -> str:
    command = test_command(info.path, c.tests)
    if prompt is None:
        title = _CONVENTIONAL.sub("", c.subject).strip()
        title = title[:1].upper() + title[1:]
        details = _without_trailers(c.body).split("\n\n")[0].strip()
        prompt = title.rstrip(".") + "." + (f"\n\n{details}" if details else "")
        prompt += (f"\n\nThe tests in {', '.join(c.tests)} describe what's expected. Make them pass without "
                   f"changing them. Check with: {command}")
    lines = [
        f"# Replays {info.url}/commit/{c.sha}",
        "# Only the public commit is referenced; no code from the repository is stored here.",
        f"prompt = {json.dumps(prompt)}",
        f'category = "{category or c.category}"',
        f'language = "{_language(info.path)}"',
        'source = "commit-replay"',
        "timeout = 1200",
        'tags = ["replay"]',
        "",
        "[workspace]",
        f'url = "{info.url}"',
        f'commit = "{c.sha}"',
        f'repo = "{_home_relative(info.path)}"',
        f"tests = {json.dumps(c.tests)}",
        f'license = "{info.license}"',
        "",
        "[[checks]]",
        'type = "command"',
        f"run = {json.dumps(command)}",
        "timeout = 900",
        "",
        "[[checks]]",
        'type = "unchanged"',
        f"paths = {json.dumps(c.tests)}",
        "",
        "[[checks]]",
        'type = "only_changed"',
        f"paths = {json.dumps(c.sources)}",
    ]
    return "\n".join(lines) + "\n"


_TRAILER = re.compile(r"^[A-Za-z][A-Za-z-]*: .+$")


def _without_trailers(body: str) -> str:
    """Drop git trailers ("Co-authored-by: ...", "Signed-off-by: ...") from a commit message body."""
    return "\n".join(line for line in body.splitlines() if not _TRAILER.match(line.strip())).strip()


def _language(repo: Path) -> str:
    if (repo / "pom.xml").is_file() or (repo / "gradlew").is_file():
        return "java"
    if (repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file():
        return "python"
    return "other"


def _home_relative(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise MinerError(f"git {args[0]} failed in {repo}: {proc.stderr.strip()[:200]}")
    return proc.stdout
