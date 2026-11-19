from beastron.briefing import budget_for, build_briefing, find_entry
from beastron.tools import Workspace


def write(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return Workspace(root)


def read(briefing):
    return [f.path for f in briefing.files]


def test_agents_md_comes_first(tmp_path):
    ws = write(tmp_path, {"AGENTS.md": "Agent rules.", "README.md": "The readme."})
    assert read(build_briefing(ws, 10_000)) == ["AGENTS.md"]  # README not linked: not read


def test_agents_md_can_lead_to_the_readme(tmp_path):
    ws = write(tmp_path, {"AGENTS.md": "Start with the [README](README.md).", "README.md": "The readme."})
    assert read(build_briefing(ws, 10_000)) == ["AGENTS.md", "README.md"]


def test_readme_is_read_fully_and_links_are_followed(tmp_path):
    readme = "# Tool\nThe point of the project.\n" + "Detail line.\n" * 200 + (
        "See the [manifesto](MANIFESTO.md#why), <a href=\"CONTRIBUTING.md#status\">contributing</a>,\n"
        "[arch]: docs/ARCHITECTURE.md\n"
        "and `docs/guide.md` for more. Not followed: [site](https://example.com/x.md), "
        "[outside](../secret.md), [changes](CHANGELOG.md), [license](LICENSE.md), [missing](nope.md).\n"
    )
    ws = write(tmp_path, {
        "README.md": readme,
        "MANIFESTO.md": "Why we exist.",
        "CONTRIBUTING.md": "How to help.",
        "docs/ARCHITECTURE.md": "How it works.",
        "docs/guide.md": "A guide.",
        "CHANGELOG.md": "v1",
        "LICENSE.md": "MIT",
    })
    (tmp_path.parent / "secret.md").write_text("outside the project")
    b = build_briefing(ws, 50_000)
    # informative names first, then link order; skipped and external links not followed
    assert read(b) == ["README.md", "MANIFESTO.md", "docs/ARCHITECTURE.md", "docs/guide.md", "CONTRIBUTING.md"]
    assert "Detail line.\n" * 200 in b.text  # the whole README, not just the start
    assert "outside the project" not in b.text and "v1" not in b.text
    assert "Do not guess what the project does from file names" in b.text


def test_links_are_followed_two_levels_deep(tmp_path):
    ws = write(tmp_path, {
        "README.md": "[a](a.md)", "a.md": "[b](b.md)", "b.md": "[c](c.md)", "c.md": "too deep",
    })
    assert read(build_briefing(ws, 10_000)) == ["README.md", "a.md", "b.md"]


def test_budget_cuts_and_lists_what_did_not_fit(tmp_path):
    ws = write(tmp_path, {
        "README.md": "intro\n" + "x" * 5_000 + "\n[one](one.md) [two](two.md)",
        "one.md": "1" * 3_000,
        "two.md": "2" * 3_000,
    })
    b = build_briefing(ws, 8_500)
    assert read(b) == ["README.md", "one.md"]
    assert b.not_read == ["two.md"]
    assert "two.md" in b.text.splitlines()[-1]  # the model is told it exists


def test_large_file_is_cut_with_a_note(tmp_path):
    ws = write(tmp_path, {"README.md": "line\n" * 10_000})
    b = build_briefing(ws, 60_000)
    assert b.files[0].truncated
    assert "call read_file on README.md with offset=" in b.text


def test_fallback_to_any_markdown_file(tmp_path):
    ws = write(tmp_path, {
        "docs/deep/notes.md": "deep",
        "docs/overview.md": "the overview",
        "docs/zzz.md": "other",
        "CHANGELOG.md": "history",
        "src/app.py": "print()",
    })
    assert find_entry(ws).name == "overview.md"  # shallowest, then informative name; CHANGELOG skipped


def test_no_documentation(tmp_path):
    ws = write(tmp_path, {"app.py": "print()"})
    b = build_briefing(ws, 10_000)
    assert b.files == []
    assert "app.py" in b.text  # the top-level listing is still useful


def test_budget_scales_with_context():
    assert budget_for(32_768) == 26_214
    assert budget_for(1_000) == 8_000
    assert budget_for(1_000_000) == 60_000
