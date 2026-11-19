"""Use-case classification, the published summary, and usage weights in the eval score."""

import json

import pytest
from typer.testing import CliRunner

from firox.cli import app
from firox.evals.report import load_weights, score, summarize
from firox.usecases.classify import classify, classify_all, project_language, summary, write_markdown
from firox.usecases.requests import Request, save


def req(text, tools=None, shell=None, command=None, project=None, source="claude"):
    return Request(source=source, session="s", project=project, timestamp=None, text=text, position=1,
                   command=command, tools=tools or {}, shell=shell or {})


EDIT = {"Edit": 1}


@pytest.mark.parametrize("text, tools, shell, expected", [
    # follow-ups and agent commands get no weight
    ("yes", None, None, "followup"),
    ("#126 is merged", None, None, "followup"),
    ("merged, continue", None, None, "followup"),
    ("ok, all secrets are defined", None, None, "followup"),
    ("exit", None, None, "meta"),
    # questions answered without edits are explanations, whatever their topic
    ("why does the build fail on CI?", None, None, "explain"),
    ("what is this project about?", {"Read": 2}, None, "explain"),
    # ... except a tiny question about git state
    ("pushed?", None, None, "git"),
    # polite requests are actions, not questions
    ("can you delete the .tokensave file?", None, {"rm": 1}, "remove"),
    # keywords, most specific first
    ("ok fix the race condition now", EDIT, None, "fix"),
    ("update the results in the README", EDIT, None, "docs"),
    ("make GitHub Action cache the default over S3", EDIT, None, "build"),
    ("add a --verbose flag", EDIT, None, "feature"),
    # git wins only when it's what the request is about
    ("FIX THE LLMS.TXT in a new PR", EDIT, None, "fix"),
    ("create the PR, push it and merge it after CI", None, {"gh": 3}, "git"),
    ("push it", None, {"git": 1}, "git"),
    ("can you make short commit sha instead of the long ones?", EDIT, None, "feature"),
    # words inside links aren't keywords
    ("https://example.com/kubernetes-guide loads fine", None, None, "followup"),
    # no keyword: what the agent did decides
    ("the thing from yesterday, the same way as before please", EDIT, None, "feature"),
    ("the thing from yesterday, the same way as before please", None, {"git": 2, "gh": 1}, "git"),
])
def test_rules(text, tools, shell, expected):
    assert classify(req(text, tools, shell)).category == expected


def test_commands_and_unclear_requests():
    assert classify(req("", command="/clear")).category == "meta"
    assert classify(req("check the CI", command="/loop")).category == "build"  # a command with a task
    assert classify(req("the usual thing for the other one we talked about")).category is None


def test_a_local_model_is_asked_only_about_unclear_requests():
    asked = []

    def ask(text):
        asked.append(text)
        return "refactor"

    results = classify_all([req("fix the bug", EDIT), req("the usual thing for the other one we talked about")], ask)
    assert asked == ["the usual thing for the other one we talked about"]
    assert [r.category for r in results] == ["fix", "refactor"] and results[1].reason == "local model"


def test_summary_weights_only_task_categories_and_holds_no_text(tmp_path):
    java = tmp_path / "shop"
    java.mkdir()
    (java / "pom.xml").write_text("<project/>")
    requests = [
        req("fix the failing test", EDIT, {"mvn": 1}, project=str(java)),
        req("fix the NPE in Cart", EDIT, project=str(java / "src")),  # a subfolder of the project
        req("add a --verbose flag", {"Edit": 1, "WebSearch": 1}, {"gh": 1}, project=str(java)),
        req("what does Cart.total do?", {"Read": 1}, source="codex"),
        req("yes"), req("#5 merged"), req("exit"),
    ]
    data = summary(classify_all(requests))
    assert data["requests"] == 7 and data["task_requests"] == 4
    assert data["weights"] == {"fix": 0.5, "feature": 0.25, "explain": 0.25}
    assert data["counts"]["followup"] == 2 and data["counts"]["meta"] == 1
    assert data["sources"] == {"claude": 6, "codex": 1}
    assert data["languages"] == {"java": 3, "unknown": 1}
    assert data["features"]["file edits"] == 0.75 and data["features"]["web search or fetch"] == 0.25
    assert data["features"]["GitHub CLI (gh)"] == 0.25

    write_markdown(data, tmp_path / "evals" / "usecases.md")
    published = (tmp_path / "evals" / "usecases.md").read_text() + (tmp_path / "evals" / "usecases.json").read_text()
    for r in requests:
        if len(r.text) > 5:
            assert r.text not in published  # counts only
    assert "shop" not in published  # no project names
    assert "| fix | 2 | 50.0% |" in published


def test_project_language():
    assert project_language(None) == "unknown"
    assert project_language("/no/such/folder") == "unknown"


def test_the_eval_score_uses_usage_weights(tmp_path):
    categories = {"fix": {"rate": 1.0}, "explain": {"rate": 0.0}}
    assert score(categories, None) == (0.5, "equal")
    assert score(categories, {"fix": 0.25, "explain": 0.75}) == (0.25, "usage")
    assert score(categories, {"docs": 1.0}) == (0.5, "equal")  # no weight for the categories run

    weights_file = tmp_path / "usecases.json"
    weights_file.write_text(json.dumps({"weights": {"fix": 0.25, "explain": 0.75}}))
    weights = load_weights(weights_file)
    results = [
        {"task": "a", "category": "fix", "profile": "p", "model": "m", "passed": True, "reason": "done", "seconds": 1.0},
        {"task": "b", "category": "explain", "profile": "p", "model": "m", "passed": False, "reason": "done", "seconds": 1.0},
    ]
    [s] = summarize(results, weights=weights)
    assert s["totals"]["score"] == 0.25 and s["totals"]["score_weighting"] == "usage"
    assert load_weights(tmp_path / "missing.json") is None


def test_classify_and_sample_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("FIROX_STATE_DIR", str(tmp_path / "state"))
    save([req("fix the failing test", EDIT), req("yes"), req("what is this?", {"Read": 1})], "claude")
    out = tmp_path / "published" / "usecases.md"
    result = CliRunner().invoke(app, ["usecases", "classify", "--write", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "3 requests, 2 of them tasks" in result.output
    assert out.is_file() and out.with_suffix(".json").is_file()
    local = tmp_path / "state" / "usecases" / "classified.jsonl"
    assert oct(local.stat().st_mode & 0o777) == "0o600"

    sample = CliRunner().invoke(app, ["usecases", "sample", "-n", "2", "--seed", "1"])
    assert sample.exit_code == 0 and sample.output.count("\n  ") == 2


def test_classify_without_a_scan_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("FIROX_STATE_DIR", str(tmp_path / "empty"))
    result = CliRunner().invoke(app, ["usecases", "classify"])
    assert result.exit_code == 1 and "firox usecases scan" in result.output
