# First score report and gap analysis (M2)

Measured on 2026-11-19 on the reference machine (Apple M5, 24 GB), with beastron at commit `67fbc23`. Details of the models are in [model-comparison.md](model-comparison.md); the summaries are in `evals/results/`.

## The score

| | usage-weighted score | passed |
|---|---|---|
| Claude Code 2.1.328 with `claude-sonnet-5-5` (reference, 3 repeats) | 100% | 171 / 171 |
| beastron with `qwen3.5:9b` (1 repeat) | **67%** | 39 / 57 |
| beastron with `qwen3:8b`, thinking on (1 repeat) | 40% | 22 / 57 |

The target is **80%** of the reference, so the best measured setup is 13 points short. One repeat per task is rough: the plain pass rate of qwen3.5:9b has a 95% range of 56–79%.

**The reference is at its ceiling.** It passed every task in every repeat, so a "gap" here is simply how far a local run falls short of 100%, and the suite can't show anything where Claude Code is the weaker one.

## Where the gap is

The weighted gap is the category's weight times the share of its tasks that failed (qwen3.5:9b against the reference's 100%). The four rows add up to the 33 points missing from 100%.

| category | weight | failed | weighted gap |
|---|---|---|---|
| **feature** | 23.8% | 9 of 13 | **16.5 points** |
| git | 16.2% | 3 of 7 | 6.9 points |
| explain | 28.9% | 3 of 13 | 6.7 points |
| fix | 10.1% | 3 of 9 | 3.4 points |
| docs, build, config, remove, refactor, tests | 21.0% | 0 of 15 | 0 |

How the 18 failures ended: 9 hit the 25-step limit and 9 finished with a failing check. Across all 18 runs the failed checks were: 13 command checks (such as Maven tests), 5 on files changed outside the allowed paths, 3 on answers missing required content, and 3 on files changed in a task that must change nothing. A run can fail more than one check. Of the 21 commit-replay tasks (`blastradius-*` and `mnemo-*`), qwen3.5:9b failed 13: these are the long Java tasks, and they hold most of the feature and fix failures.

## Causes, from reading 6 traces

Claude Code passed all of these tasks, mostly in 3 to 7 steps. These are the cases I read; they show what happened, but six traces do not prove how common each cause is.

1. **Edits files when asked a question** (`explain-stack-trace`, explain). Asked to explain a stack trace, the model read two files, named the cause, then **edited `invoice.py`** to add the missing tax rate. The check `no_changes` failed. This is the problem issue #22 describes.
2. **Empty answer, with the real answer in the reasoning** (`question-must-not-edit` and `explain-blastradius-would-miss`, both explain). The last model message has no text at all: the explanation is written out in the reasoning field, so the harness sees an empty final answer and the answer checks fail. In `question-must-not-edit` the model also read the right file and reasoned correctly that it should only explain. **No M3 issue covers this.** It looks cheap to fix and affects the heaviest category, but it's two traces, not a measured rate.
3. **Misuses the git tool, then works around it** (`git-find-breaking-commit`, git). 12 of its 21 tool calls were errors: it passed whole command lines such as `diff a1a598f HEAD` in the `command` field, where only `status`, `diff`, `log` and `show` are allowed with separate `ref` and `path` arguments. It then switched to the shell, found the right commit (656d88f), but did it with `git checkout <commit> -- totals.py`, **leaving the file modified** on a question that must change nothing. This points at issue #31 (tools whose use is obvious) and #22.
4. **Stops before the git job is finished** (`git-resolve-merge-conflict`, git). It rewrote `pricing.py` correctly and the tests passed, then stopped. It never staged the file or committed, so the merge was left unfinished (the two git checks failed). It had the answer and skipped the last steps: issue #27.
5. **Runs out of steps on a long Java task** (`mnemo-warmup-2e93727`, feature). It used all 25 steps with 6 tool errors: two edits that failed with "old_text was not found" (a whitespace mismatch in a 600-line file) and Maven compile errors it then had to find in long output. Claude Code finished the same task in 6 to 7 steps. This points at #28 (close-match edits), #24 (build-log summaries) and #29 (compaction), but I only saw the errors, not what the model could see of the build log.

## Order for M3

By weighted gap, feature and fix on long Java tasks come first, then git and explain. The ordering below is a proposal based on these traces and should be rechecked after each change with `beastron eval run`.

1. **Feature and fix** (20 points between them): **#28** close-match edits and **#24** build-log summaries, both seen in the trace above; then **#29** compaction (9 of 18 failures hit the step limit, and the Java tasks fill a 32K context) and **#23** verification gate.
2. **Git** (6.9 points): **#31** specialized tools, because 12 of 21 calls were tool errors, then **#27** per-task recipes, so that git tasks finish.
3. **Explain** (6.7 points): **#22** intent routing, and **a new issue**: use the model's reasoning as the answer when the reply is empty. I haven't opened it; say if you want it.
4. **Later:** #25 claims check, #26 hybrid thinking, #30 checkpoints and `/undo`, #32 tiers. The traces read here don't show them as a cause. #26 matters less now: with thinking on, qwen3.5:9b has a median of 53 s and no timeouts.

## Limits of this report

- 1 repeat for the local models, 3 for the reference.
- 6 traces read of 18 failures.
- Only two small models were run (see the comparison document). A larger model might remove some of these gaps and bring others.
- The suite is 70% Java, like the owner's work, and 21 of its 57 tasks are commit replays of a few repositories, so it measures those repositories more than Java in general.
