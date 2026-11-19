# Eval suite

Tasks that measure firox (see [docs/plan.md § 5.2](../docs/plan.md#52-eval-suite)). Each task is a folder in `evals/tasks/<id>/` with a `task.toml`:

```toml
prompt = "The tests fail. Fix the bug, then run: python -m pytest -q"
category = "fix"            # explain, fix, feature, refactor, remove, tests, build, git, docs, config
language = "python"
source = "synthetic"        # synthetic | commit-replay | captured
timeout = 600               # seconds for the whole run (default 600)
max_steps = 25              # model calls (default 25)
tags = ["seed", "quick"]    # suites the task belongs to

[workspace]                 # synthetic / captured:
fixture = "repo"            #   folder next to task.toml, copied fresh for every run (default "repo")
setup = "setup.sh"          #   optional script run inside the copy, e.g. to build a git history
# commit-replay:
# repo = "~/Documents/blastradius"
# commit = "1a2b3c4"        #   the workspace is the repository at this commit's parent

[[checks]]                  # what decides pass or fail
type = "command"
run = "python -m pytest -q"
```

Write the top-level keys before the first `[[checks]]`: in TOML, lines after it belong to that check.

Every run gets a fresh temporary workspace, removed afterwards. Setup scripts run with fixed git identities and dates, so they build the same history, with the same commit ids, every time. Commit replay clones the repository locally and checks out the parent commit, so the source repository is never touched.

**Excluded projects:** repositories listed in `~/.config/firox/exclude.txt` (or the file `FIROX_EXCLUDE` points to; one path per line, `#` for comments) are never used for commit replay. The list is local configuration and is never committed.

## Checks

Each `[[checks]]` entry decides part of the result; a run passes only when all of them pass. Paths are relative to the workspace and can't leave it.

| `type` | fields | passes when |
|---|---|---|
| `command` | `run`, `timeout` (default 300 s), `exit_code` (default 0) | the command exits with `exit_code` |
| `file_exists` / `file_absent` | `path` | the file exists / doesn't |
| `contains` / `not_contains` | `path`, `text`, `regex` (default false) | the file contains the text or matches the regex / doesn't |
| `unchanged` | `paths` | these files or folders are exactly as before the run (for unrelated work that must survive) |
| `no_changes` | `ignore` (optional globs) | no file changed at all (for questions) |
| `only_changed` | `paths` (globs allowed) | nothing changed outside these paths (for commit replay) |
| `answer_mentions` | `patterns` | the final answer matches every regex, ignoring case |

"Before the run" means a snapshot of content hashes taken when the workspace is ready. `.git` and generated folders (caches, `build`, `target`, `node_modules`, …) are left out, so running tests doesn't count as a change. A failed check gives a reason that names the file or the missing fact, e.g. `pyproject.toml still contains 'x-handles'`.

## Running

```bash
firox eval run                          # every task, the default model profile, 3 repeats
firox eval run -s quick -n 1            # one suite (tag), one repeat
firox eval run -t fix-chunk -m qwen3-8b-fast,qwen3-8b-local
firox eval run --keep -v                # keep the workspaces and watch the agent work
```

Each run gets a fresh workspace. Every action is **approved automatically**, so every shell command the model writes runs inside a macOS sandbox (`sandbox-exec`) that only allows writes inside the run's own folder, the system temp folders, and the build caches `~/.m2`, `~/.gradle` and `~/.cache`. Reading and network access stay allowed. On other systems firox refuses to run evals unless you pass `--unsafe-no-sandbox`.

The task's `timeout` (wall clock) and `max_steps` are enforced. A timeout counts as a failure. Ctrl-C stops the suite; finished runs are kept.

Results go to `~/.local/state/firox/evals/runs/<run id>/`:
- `results.jsonl`: one line per run, with pass/fail, each check's reason, how the turn ended, steps, tokens, time, nudges, tool errors, the change record, the model digest and firox's commit
- `traces/`: the session trace of every run

## Reports, baselines and regressions

After a run, `firox eval run` prints a report per model profile and writes a **summary** to `evals/results/<run>_<profile>_<firox commit>.json`. Summaries hold pass counts per task and category, timing, the model digest and firox's commit, and no transcripts. Commit them: together they are the score's history.

```bash
firox eval report                 # the latest run, compared with the baseline
firox eval report <run folder>    # a specific run
firox eval report --history       # committed summaries over time, per profile
firox eval baseline               # store the latest run as the baseline (evals/baselines/<profile>.json)
```

Compared with the baseline, the report flags:
- **a task regression:** the task passed in most baseline runs, and now fails in at least 2 of every 3 runs
- **a category regression:** the pass rate is clearly lower, meaning the 95% confidence ranges (Wilson score) of the two rates don't overlap. With few runs, smaller drops are noise.
- **a changed model file** (digest), so a model update can't hide inside a score change
- improvements, and tasks added or removed

`firox eval run` and `firox eval report` exit with status 1 when there is a regression. Until use-case weights exist (milestone M1), the score is the plain average of the category pass rates.
