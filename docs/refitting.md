# Refitting the eval suite

The eval suite and the score are fitted to the owner's real work: which kinds of tasks they ask coding agents for, how often, and in which languages. When that changes, the suite has to follow, or the score stops meaning "how close is firox on *my* work". This is the procedure. It is written to be followed step by step by a person or by Claude Code (`/refit` in this repository loads it).

## When to refit

| Trigger | What changes | Steps |
|---|---|---|
| Your way of working changed (new projects, a few months passed) | the weights | 1, 2, 5, 6 |
| You want firox judged on a different mix than you use (e.g. fixes matter more) | the weights, by hand | 3, 5, 6 |
| A new technology or repository (e.g. a Go service) | new tasks, maybe a new build tool | 4, 5, 6 |
| The coverage report shows gaps | new tasks | 4, 5, 6 |

Refitting changes what is measured, so scores before and after a refit aren't directly comparable. Step 6 starts a new baseline.

## Rules (they always apply)

- **Private data stays local.** Session logs, scanned requests and per-request categories live under `~/.local/state/firox/` and never go into the repository. Only counts, weights and approved tasks do.
- **Excluded projects are never used.** They are listed in `~/.config/firox/exclude.txt` (local, one path per line), and every step refuses them. Add a project there before a refit if it must stay out.
- **Repository tasks only reference public commits:** a public GitHub URL and a full commit hash, no copied code. The repository needs a license; it is recorded in the task.
- **No Claude Code transcripts in git**, ever. Summaries of reference runs (pass/fail, tokens, time) are fine.
- **Every new task is approved by the owner** before it is written, and verified: it must fail as given and pass with its reference solution.

## 1. Rescan the history

```bash
firox usecases scan                 # all of it
firox usecases scan --since 120d    # or only recent work, so old habits fade out
```

This reads the Claude Code and Codex logs into `~/.local/state/firox/usecases/` and prints counts only.

## 2. Reclassify and publish the weights

```bash
firox usecases classify --write
firox usecases sample -n 20 --seed <any number>
```

- `classify --write` rewrites `evals/usecases.md` and `evals/usecases.json` (counts, weights, languages, agent features; no request text).
- **Check the classifier on a fresh sample.** Judge 20 requests by hand and count the agreements. It was 17/20 when introduced (#16). Below about 15/20, improve the rules in `src/firox/usecases/classify.py` first. Measure on a sample you didn't tune on.
- `--model <profile>` lets a local model classify the requests the rules can't place.

## 3. Adjust the weights by hand (optional)

Edit `evals/weights.toml`. firox never overwrites it:

```toml
[multiply]   # scale a measured weight
fix = 1.5

[set]        # or fix a category's share of the score; the others share the rest
explain = 0.20
```

The score uses the measured weights, then these adjustments, renormalized to 100%. `firox eval coverage` shows the resulting weights.

## 4. Fill the gaps in the suite

```bash
firox eval coverage              # tasks per category and language vs the weights, and what to add
firox eval coverage --size 60    # the same for a bigger suite
```

Each category needs at least 2 tasks. The target per category is its weight times the suite size. Languages are reported against their share of your work. Close the gaps with:

**Commit replays** (feature, fix, build, tests): real commits from a public repository, replayed from their parent with their tests in place.

```bash
firox eval propose --repo ~/Documents/<repo>            # candidates, writes nothing
firox eval add --repo ~/Documents/<repo> --commit <sha> # draft, verify, show, write after approval
```

- Prefer commits whose message explains the intent: the prompt is drafted from it, and you can override it with `--prompt`.
- A candidate that "passes without work" (its tests already pass before the commit) is rejected automatically.

**A new technology:** check that `test_command()` in `src/firox/usecases/commits.py` knows its build tool. Today it knows Maven (`pom.xml`), Gradle (`gradlew`) and pytest. For another tool (Go, npm, Cargo), add a branch that runs only the commit's test files, offline where possible, then a test in `tests/test_commit_miner.py`. Add the language to `LANGUAGE_FILES` in `src/firox/usecases/classify.py` so its share is measured.

**Questions about a repository** (explain): a task with `source = "commit-replay"` and `at = "commit"`, a pinned full commit hash, `answer_mentions` patterns for the facts the answer must contain, `no_changes`, and the reference answer in `solution/answer.md`. Base the facts on the repository's own documentation at that commit.

**Synthetic tasks** (explain, git, docs, refactor, remove, config, and whatever replays can't give): a folder in `evals/tasks/<id>/` (format in [evals/README.md](../evals/README.md)):
- `repo/`: the project as the agent finds it
- `setup.sh`: optional, e.g. to build a git history
- `[[checks]]`: what decides pass or fail
- `solution/`: files a correct solution changes, plus `answer.md` for answers and `solution.sh` for command solutions such as git operations

Keep fixtures small. For Maven, pin the plugin and JUnit versions that are in `~/.m2` so builds run offline. For tests-category tasks, add a check that injects bugs and requires the new tests to fail.

**Prepare before asking.** Draft and verify all candidates in a staging folder under `~/.local/state/firox/` (not in the repository). Then show the owner a table of verified candidates per category and language, with proposed synthetic tasks, and get approval for the exact list. Double-check any totals you present: they are what the owner approves.

## 5. Verify everything

```bash
firox eval verify          # every task: fails as given, passes with its reference solution
uv run pytest              # the suite's structure, synthetic tasks, licensing and "no copied code" checks
firox eval coverage        # every category has at least 2 tasks
```

All three must be clean before the PR. `firox eval verify` needs the local clones and the Maven cache, so it runs on the reference machine; CI repeats only the parts that work without them.

## 6. Re-measure and start a new baseline

```bash
firox eval run -s quick -n 1                          # a quick sanity run
firox eval run -m <default profile>,<fast profile>    # the full suite, 3 repeats each (overnight)
firox eval baseline                                   # the new reference for regressions
```

Commit the new `evals/usecases.*`, `evals/weights.toml`, new tasks, the summaries in `evals/results/` and the baselines, in one PR. Its description says what triggered the refit, the coverage before and after, and the verify result. Mention in the PR that scores from before the refit aren't directly comparable.

## Checklist

- [ ] excluded projects listed in `~/.config/firox/exclude.txt`
- [ ] `firox usecases scan` (with `--since` if wanted), then `classify --write`
- [ ] hand check of 20 fresh requests: ≥ 15 agree
- [ ] `evals/weights.toml` reflects any wanted adjustment
- [ ] `firox eval coverage`: candidates drafted and verified in staging, approved by the owner
- [ ] new build tool or language added to `test_command()` / `LANGUAGE_FILES`, with tests
- [ ] `firox eval verify`, `uv run pytest`, `firox eval coverage` all clean
- [ ] new baseline recorded; the PR explains the refit
