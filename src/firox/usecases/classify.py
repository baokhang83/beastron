"""Sort requests into use-case categories and turn the counts into the score's weights.

Categories are the eval task categories (firox.evals.task.CATEGORIES), plus two
kinds that are counted but get no weight:
- `followup`: short continuations and approvals ("yes", "#56 merged", "next")
- `meta`: controlling the agent itself (a slash command without a task, e.g. /clear)

Rules decide first: the request's words, then what the agent did in response
(edits, read-only work, git/gh commands). A local model is only asked about
the requests the rules can't place, and only when the caller passes a client.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..evals.task import CATEGORIES
from .requests import Request

UNWEIGHTED = ("followup", "meta")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
READ_TOOLS = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "web__run"}

# Short replies that continue earlier work instead of asking for something new.
_FOLLOWUP = re.compile(
    r"^\s*(yes|yep|yeah|yup|ok(ay)?|sure|great|good|nice|perfect|thanks?( you)?|thx|cool|continue|go( on| ahead)?|"
    r"next\??|done|i do|do it|makes sense|agreed|right|correct|exactly|lgtm|fine|merged|all good|confirm(ed)?|"
    r"(ok[, ]*)?(run|do|start) the next( one)?|#?\d+ (is )?merged( btw)?|#?\d+ it is( then)?|#\d+ (ok|done|yes))"
    r"[\s!.,]*(\w+[\s!.,]*){0,2}$",
    re.IGNORECASE,
)
# "can you delete X?" asks for an action, unless it asks to explain something.
_POLITE_REQUEST = re.compile(
    r"^\s*(please\s+)?(can|could|would|will) you( please)?\s+(?!explain|tell|summari[sz]e|describe|clarify|confirm)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+")
_QUESTION_START = re.compile(
    r"^\s*(what|how|why|when|where|which|who|is|are|was|were|do|does|did|can|could|should|shouldn'?t|would|will|"
    r"isn'?t|aren'?t|don'?t|doesn'?t|explain|summari[sz]e|tell me|describe|any idea)\b",
    re.IGNORECASE,
)

# Category keywords, in priority order: when several match, the first one wins.
# "git" is checked separately: requests often end with "... in a new PR", so git
# only wins when it is the only thing a request asks for.
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("remove", re.compile(r"\b(remove|delete|revert|undo|get rid of|drop|uninstall|strip out)\b", re.IGNORECASE)),
    ("tests", re.compile(r"\b((write|add|create|more|missing|unit|integration) tests?|test coverage|tests? for)\b", re.IGNORECASE)),
    ("fix", re.compile(r"\b(fix\w*|bugs?|errors?|fail\w*|broken|crash\w*|exceptions?|traceback|stuck|stucks|"
                       r"doesn'?t work|not working|wrong|regression|issue with)\b", re.IGNORECASE)),
    ("docs", re.compile(r"\b(readme|docs?|documentation|changelog|llms\.txt|blog|article|linkedin|post(ed)?|reply|"
                        r"tweet|wording|rephrase|sentence|description|write-?up|plan\.md|slack)\b", re.IGNORECASE)),
    ("build", re.compile(r"\b(ci|pipelines?|github actions?|workflows?|maven|mvn|gradle|compile\w*|builds?|"
                         r"dependenc\w+|artifacts?|artefacts?|publish\w*|jar|benchmarks?|cache)\b", re.IGNORECASE)),
    ("config", re.compile(r"\b(config\w*|settings?|install\w*|set ?up|docker|kubernetes|k8s|helm|deploy\w*|"
                          r"env(ironment)? var\w*|process(es)?|terminate|kill|license)\b", re.IGNORECASE)),
    ("refactor", re.compile(r"\b(refactor\w*|rename\w*|clean ?up|restructur\w*|simplif\w*|extract|reorgani[sz]e)\b",
                            re.IGNORECASE)),
    ("feature", re.compile(r"\b(add|implement|create|build me|support|new|make|enhance|improve|extend|want|"
                           r"integrate|generate)\b", re.IGNORECASE)),
]
GIT = re.compile(r"\b(pull requests?|prs?|push(ed)?|merge[ds]?|commits?|rebase|cherry-pick|branch(es)?|releases?|"
                 r"tags?|git|gh|milestones?|issues?)\b", re.IGNORECASE)
_ACK_START = re.compile(r"^\s*(ok(ay)?|yes|yeah|great|good|done|alright|so|fine|cool|all right|right)\b[\s,!.]*", re.IGNORECASE)
_META = re.compile(r"^\s*/?(exit|quit|clear|stop|bye|hello|hi|hey)[\s!.?]*$", re.IGNORECASE)


@dataclass
class Classified:
    request: Request
    category: str | None  # None: the rules couldn't place it
    reason: str  # which rule or signal decided


def classify(request: Request) -> Classified:
    text = request.text.strip()
    if request.command and not text:
        return Classified(request, "meta", f"command {request.command} without a task")
    if not text or _META.match(text):
        return Classified(request, "meta", "talking to the agent itself")

    edits = sum(n for tool, n in request.tools.items() if tool in EDIT_TOOLS)
    words = len(text.split())
    if words <= 8 and _FOLLOWUP.match(text):
        return Classified(request, "followup", "short continuation")

    plain = _URL.sub(" ", text)  # words inside links aren't keywords
    matches = [category for category, pattern in RULES if pattern.search(plain)]
    git_hits = len(GIT.findall(plain))
    mentions_git = git_hits > 0
    is_question = (plain.rstrip().endswith("?") or bool(_QUESTION_START.match(plain))) and not _POLITE_REQUEST.match(plain)
    if is_question and edits == 0:
        # Asked and answered without changing anything: an explanation, whatever the topic,
        # except a very short question about git state ("pushed?").
        if mentions_git and words <= 4 and not matches:
            return Classified(request, "git", "short question about git")
        return Classified(request, "explain", "question, no edits in response")

    if git_hits >= 2 and git_hits > len(matches):
        return Classified(request, "git", "mostly git (several git actions)")
    if matches:
        return Classified(request, matches[0], f"keyword ({matches[0]})")
    if mentions_git:
        return Classified(request, "git", "only git keywords")
    if words <= 10 and _ACK_START.match(text):
        return Classified(request, "followup", "short reply starting with an acknowledgement")
    if words <= 6 and edits == 0:
        return Classified(request, "followup", "short remark without a task keyword")

    shell = request.shell
    if edits:
        return Classified(request, "feature", "no keyword; files were edited")
    if shell and set(shell) <= {"git", "gh", "cd"}:
        return Classified(request, "git", "no keyword; only git/gh commands")
    if request.tools and set(request.tools) <= READ_TOOLS:
        return Classified(request, "explain", "no keyword; only reading")
    return Classified(request, None, "unclear")


def classify_all(requests: list[Request], ask: Callable[[str], str | None] | None = None) -> list[Classified]:
    """Classify every request; `ask` (e.g. a local model) is only used for the unclear ones."""
    results = [classify(r) for r in requests]
    if ask:
        for item in results:
            if item.category is None:
                answer = (ask(item.request.text) or "").strip().lower()
                if answer in CATEGORIES:
                    item.category, item.reason = answer, "local model"
    return results


MODEL_PROMPT = (
    "Classify this request a developer typed to a coding agent. Answer with exactly one word from: "
    + ", ".join(CATEGORIES) + ".\n\nRequest:\n{text}"
)


def model_asker(client: Any) -> Callable[[str], str | None]:
    """Ask a firox model client for one category word."""
    def ask(text: str) -> str | None:
        prompt = MODEL_PROMPT.format(text=text[:2000])
        reply = client.complete([{"role": "user", "content": prompt}], [], on_text=lambda s: None, on_reasoning=lambda s: None)
        words = re.findall(r"[a-z]+", reply.content.lower())
        return next((w for w in words if w in CATEGORIES), None)
    return ask


# ----- weights and the published summary ------------------------------------------

LANGUAGE_FILES = [
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")),
    ("python", ("pyproject.toml", "setup.py", "requirements.txt")),
    ("javascript", ("package.json",)),
    ("go", ("go.mod",)),
    ("rust", ("Cargo.toml",)),
]
FEATURES = {
    "web search or fetch": lambda r: any(t in r.tools for t in ("WebSearch", "WebFetch", "web__run", "web_search")),
    "MCP servers": lambda r: any(t.startswith("mcp__") for t in r.tools),
    "sub-agents": lambda r: any(t in r.tools for t in ("Agent", "Task")),
    "planning (plan mode, todo lists)": lambda r: any(t in r.tools for t in ("EnterPlanMode", "ExitPlanMode", "TodoWrite", "update_plan")),
    "asking the user questions": lambda r: any(t in r.tools for t in ("AskUserQuestion", "request_user_input", "request_user_input_async")),
    "background work and waiting": lambda r: any(t in r.tools for t in ("Monitor", "ScheduleWakeup", "CronCreate", "wait")),
    "skills": lambda r: "Skill" in r.tools,
    "GitHub CLI (gh)": lambda r: "gh" in r.shell,
    "file edits": lambda r: any(t in r.tools for t in EDIT_TOOLS),
    "shell commands": lambda r: bool(r.shell) or any(t in r.tools for t in ("Bash", "exec_command")),
}


def project_language(project: str | None) -> str:
    if not project:
        return "unknown"
    folder = Path(project)
    for _ in range(3):  # the session may have run in a subfolder
        if folder.is_dir():
            for language, markers in LANGUAGE_FILES:
                if any((folder / m).exists() for m in markers):
                    return language
        folder = folder.parent
    return "other" if Path(project).is_dir() else "unknown"


def summary(results: list[Classified]) -> dict[str, Any]:
    """Counts, weights, languages and feature usage. No prompt text, no project names."""
    counts = Counter(item.category or "unclear" for item in results)
    weighted = {c: counts[c] for c in CATEGORIES if counts[c]}
    total = sum(weighted.values())
    tasks = [item.request for item in results if item.category in CATEGORIES]
    languages = Counter(project_language(r.project) for r in tasks)
    return {
        "schema": 1,
        "requests": len(results),
        "task_requests": total,
        "sources": dict(Counter(item.request.source for item in results)),
        "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "weights": {c: round(n / total, 4) for c, n in sorted(weighted.items(), key=lambda kv: -kv[1])} if total else {},
        "languages": dict(languages.most_common()),
        "features": {name: round(sum(map(test, tasks)) / len(tasks), 4) for name, test in FEATURES.items()} if tasks else {},
    }


def write_markdown(data: dict[str, Any], path: Path) -> None:
    lines = [
        "# Use cases",
        "",
        "What the owner really asks coding agents to do, from their local Claude Code and Codex history.",
        "Generated by `firox usecases classify --write`. It contains counts only: no request text, no project names.",
        "",
        f"{data['requests']} requests ({', '.join(f'{n} {s}' for s, n in data['sources'].items())}), "
        f"of which {data['task_requests']} are tasks. Follow-ups (\"yes\", \"#56 merged\"), agent commands and "
        "unclear requests are counted but get no weight.",
        "",
        "## Weights",
        "",
        "Each category's share of the task requests. The eval score weights category pass rates by these "
        "(`evals/usecases.json`).",
        "",
        "| category | requests | weight |",
        "|---|---:|---:|",
    ]
    for category, weight in data["weights"].items():
        lines.append(f"| {category} | {data['counts'][category]} | {weight:.1%} |")
    lines += ["", "Not weighted:", ""]
    for kind in ("followup", "meta", "unclear"):
        if data["counts"].get(kind):
            lines.append(f"- {kind}: {data['counts'][kind]}")
    lines += ["", "## Languages of the projects", "", "| language | task requests |", "|---|---:|"]
    lines += [f"| {lang} | {n} |" for lang, n in data["languages"].items()]
    lines += ["", "## Agent features used", "", "Share of task requests where the agent used each feature: the "
              "capabilities firox has to cover.", "", "| feature | share |", "|---|---:|"]
    lines += [f"| {name} | {share:.0%} |" for name, share in sorted(data["features"].items(), key=lambda kv: -kv[1])]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
