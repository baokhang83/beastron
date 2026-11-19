---
name: refit
description: Refit beastron's eval suite and score weights to the owner's real work. Use when the owner says "refit", wants to change the category percentages, add a new technology or repository to the evals, or when `beastron eval coverage` shows gaps.
---

# Refit the eval suite

Follow `docs/refitting.md` in this repository, step by step. Read it fully before starting; it is the procedure, and these notes only add how to run it with the owner.

1. **Ask what triggered the refit** (new way of working, different weights wanted, new technology or repository, coverage gaps), and pick the steps from the table in "When to refit". Ask only what the owner's trigger leaves open.
2. **Run the read-only steps yourself:** `beastron usecases scan`, `beastron usecases classify --write`, `beastron eval coverage`, `beastron eval propose`. Report counts and gaps, never request text from the logs.
3. **Hand check of the classifier:** sample 20 requests with a fresh seed, judge each, report the agreement count honestly, and say which seed you used.
4. **Weights by hand:** only edit `evals/weights.toml` when the owner asks for a different mix; show the resulting weights from `beastron eval coverage`.
5. **New tasks:**
   - Draft and verify every candidate in a staging folder under `~/.local/state/beastron/`, never in the repository.
   - Present a table of verified candidates, plus proposed synthetic tasks per category and language, with correct totals.
   - **Stop and get the owner's approval of the exact list before writing any task.**
6. **Verify:** `beastron eval verify`, `uv run pytest` (check its real exit code, not a piped one), `beastron eval coverage`.
7. **Commit on a new branch.** Push and open a PR only when the owner says so (or when they asked for automatic PRs in this session).

Never use projects listed in `~/.config/beastron/exclude.txt`. Only reference public repositories with a license, never copy their code. Never commit session logs, prompts or Claude Code transcripts.
