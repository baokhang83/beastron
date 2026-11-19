# beastron: plan and process

This document describes what beastron is trying to achieve, how success is measured, the process used to get there, the architecture that supports it, and the roadmap. It is meant for anyone reading the project without having followed its development.

## 1. Why beastron exists

Hosted coding agents such as Claude Code with a frontier model can already handle almost all everyday development work. They have three drawbacks:

- **Cost.** They need a paid subscription or per-token billing.
- **No control.** The model behind them changes when the provider ships a new version. Behavior can get worse without warning, and an older version can't be kept.
- **Your code leaves your machine.** Every file the agent reads is sent to the provider.

beastron explores the other end of that trade-off: a coding agent that runs **entirely on the developer's own machine**, with open-weight models served locally (Ollama, LM Studio, llama.cpp). A model changes only when you download a new one, nothing is sent anywhere, and nothing costs anything per use.

The price is capability. A model that fits in a laptop's memory is far weaker than a frontier model. beastron's bet is that a careful harness around a small model (the loop, the tools, the guardrails, the information it is given) can close much of that gap. The project measures how much.

## 2. Goal and how it is measured

> **The question:** how close can a free, local coding agent get to a hosted one on real work?
>
> **The target:** complete **at least 80% as many tasks as Claude Code** (with a pinned Sonnet model) on a suite drawn from the owner's real work, weighted by how often each kind of task occurs. Do it reliably, for free, on a **modern baseline developer machine**, with acceptable speed.

For example, if Claude Code completes 90% of the tasks, beastron must complete at least 72%.

### Reference machine

Every measurement is made on one reference machine: an **Apple M5 with 24 GB of unified memory**. Results are reported for that machine. Claims about other machines will only be made once they have been measured there.

### The 100% line: Claude Code with a pinned model

A target relative to Claude Code needs Claude Code's own results on the same tasks. So the eval tasks are also run through Claude Code in headless mode, with the model pinned to an exact version:

```bash
claude -p --model claude-sonnet-5-5 --output-format json "<task>"
```

The exact model ID is used instead of the `sonnet` alias, because the alias moves to newer models over time. Each reference result records the model ID and the Claude Code version, so that changes on the hosted side are visible too. Claude Code is used only for this benchmarking, never while beastron runs.

### The scoreboard

| Metric | Definition | Target |
|---|---|---|
| **Score** | usage-weighted pass rate of beastron ÷ usage-weighted pass rate of the reference, on the same tasks | ≥ 0.80 |
| **Speed** | median and 90th-percentile wall time per task | median ≤ 5 min and ≤ 3× the reference; p90 ≤ 15 min |
| **Safety** | tasks where something outside the task's scope was changed | 0 |
| **Honesty** | tasks where the agent's final summary claims changes that didn't happen | measured, and falling over time |

**Why "usage-weighted":** a benchmark full of tasks nobody actually does says little. beastron's tasks come from the owner's real work, and each category of task counts as much as it does in real use. If 40% of real requests are "fix a failing Java test", that category is 40% of the score. Results are also reported **per category**, so it's always clear where beastron falls short.

## 3. Principles learned so far

These come from building and testing the first version of beastron with `qwen3:8b`:

1. **Most failures come from the model, not the harness.** On multi-step tasks, the small model skipped steps, stopped early, misreported what it did, and ignored instructions.
2. **Fixes that don't depend on the model's judgment work; instructions alone don't.** Telling the model "read the README before answering" or "answer questions without editing files" didn't change its behavior. What did:
   - beastron reads the project's documentation itself and hands it over
   - beastron records every file change itself instead of trusting the model's summary
   - beastron ends the turn when the user says no, instead of letting the model retry
3. **Measure before changing.** Without a fixed set of tasks and a baseline, "it seems better" is impossible to verify, and regressions go unnoticed. The scoreboard is built first.
4. **Small and large models need different harnesses.** See section 5.4.

## 4. The process

beastron improves in a loop. Each turn of the loop changes one thing and measures its effect.

```
  mine real usage ──▶ build tasks ──▶ measure beastron and the reference
        ▲                                         │
        │                                         ▼
  update the baseline ◀── measure again ◀── change one thing ◀── pick the biggest gap
```

1. **Mine real usage:** find out which kinds of tasks the owner really does, and how often (section 5.3).
2. **Build tasks:** turn those into eval tasks with automatic checks, partly from real commits in the owner's repositories (section 5.2).
3. **Measure:** run beastron and the reference on the same tasks; compute the score per category.
4. **Pick the biggest gap:** the category with the most weight × the largest difference.
5. **Change one thing:** one setting, one tool, one guardrail, behind a flag, in one pull request.
6. **Measure again:** the change is kept only if the score goes up without regressions elsewhere.
7. **Update the baseline**, so the next change is compared against the new level.

A second loop runs during daily use:

```
 daily use ──/bad <note>──▶ the session trace marks that turn ──beastron triage──▶ flagged turns
     ▲                                                                              │
     │                                                                beastron eval capture
     │                                                                              ▼
 baseline updated ◀── the suite passes ◀── beastron is fixed ◀── a new task from that real failure
```

Every real failure can become a permanent regression test, so the same mistake can't quietly come back.

### Rules of the process

- Every change is measured on the quick suite before it is merged, and the result goes in the pull request.
- Defaults are chosen from measurements, not from guesses.
- A regression blocks a merge.
- Private data stays local: mined prompts, session traces, and tasks built from private repositories are stored under `~/.local/state/beastron` and never committed. Only category counts and tasks the owner approves go into the repository.
- Projects the owner excludes are never used as eval material, for mining or for commit replay. The exclusion list is local configuration.
- Documentation states only what has been measured, failures included.

## 5. Architecture

### 5.1 Session trace

Every session is written to a JSONL file under `~/.local/state/beastron/sessions/`:
- the agent's events and tool calls with their full output
- the change record (which files beastron changed)
- tokens and timing
- the **model digest** (the exact model file in use)
- beastron's own git version
- the project's state at the start: git commit, uncommitted changes, untracked files

The trace is the foundation for everything else. Evals use it to score runs, the feedback loop uses it to rebuild the situation of a failure, and the model digest shows exactly when a model changed. `--no-trace` turns it off.

### 5.2 Eval suite

Each task lives in `evals/tasks/<id>/task.toml`: the prompt, a category, a language, a time and step limit, how to set up its workspace, and the checks that decide pass or fail.

**Where tasks come from:**
- **Synthetic:** a small project folder committed with the task, built to reproduce a specific failure type.
- **Commit replay:** a real commit from one of the owner's public repositories. The workspace is the repository at the commit's parent. The task is the commit's intent, written as an instruction and approved by the owner. The checks are the tests that commit added or changed, run offline, plus "only these files may change". The owner's main repositories (blastradius, mnemo-jvm-warden, mnemo-warmup, blastradius-cache-warmer: over a thousand commits with tests between them) are mostly Java with Maven, so Java is heavily represented.
- **Captured:** a real failure from daily use, flagged with `/bad`.

**Checks:**
- a command must succeed (for example the tests)
- a file must exist, or must not exist
- a file must contain, or must not contain, given text
- unrelated work must be left **unchanged**
- a question must cause **no changes**
- an answer must mention given facts

**Running:**
- Each task runs in a fresh temporary copy, with approvals given automatically.
- Shell commands run inside a macOS sandbox that only allows writing to the task's folder.
- Each task is repeated three times, because small models give different results on each run.

**Two suite sizes:**
- a **quick** suite (about 10 tasks, ~30 minutes) before each merge
- a **full** suite (about 40 tasks × 3 repeats) overnight

**Regressions:** results are compared with a stored baseline per model profile.
- A task that now fails in at least 2 of 3 repeats where it used to pass, or a category that drops beyond normal noise, is flagged.
- A changed model digest is reported, so a model update can't hide inside a score change.
- Result summaries are committed, so the history of the score is part of the repository.

CI has no GPU, so it tests only the eval machinery itself, with a scripted fake model. Real evals run on the reference machine.

### 5.3 Use-case inference

To weight the score by real work, beastron reads the owner's own history of AI-assisted work, with the owner's permission and locally only:
- Claude Code session logs (`~/.claude/projects/`)
- Codex session logs (`~/.codex/sessions/`)

For each request it records:
- the request itself
- what the agent did in response (only reading points to a question; editing plus running tests to a code change; `git` or `gh` commands to repository work)
- the project and its language
- whether the user immediately corrected the agent

Requests are sorted into categories, mainly by rules, with the local model only for unclear cases:
- explain / answer a question
- fix a bug or failing test
- add a feature
- refactor
- remove / undo
- write tests
- build / CI
- git, release, pull request
- documentation
- configuration / infrastructure

The result has two parts:
- a committed summary, `evals/usecases.md`, with counts and weights only
- the list of Claude Code features the owner actually relies on (web access, MCP servers, planning, sub-agents, the GitHub CLI): the capabilities beastron has to cover for the target to be reachable

A **commit miner** proposes commit-replay tasks from the owner's repositories, one category at a time:
- small commits
- that touch tests
- that build offline

The owner approves each one.

### 5.4 Adapting to the model's strength

A small and a large model need different setups. Each model profile gets a **tier**: small, medium, large, or automatic. The automatic tier comes from a short calibration run that measures how reliably the model makes valid tool calls, gets edits right, follows instructions, and how fast it is.

**Tools.** A small model gets **many specialized tools**; a large model can do with **a few general ones**. In testing, `qwen3:8b` never thought of running `git status` through its general shell tool. Given a dedicated `git` tool, it used it on the very first step. A strong model works out the right shell command for git, tests or deleting files on its own. Many tool definitions at once fill a small model's context and cause wrong choices, though. So for small models the set is **filtered per request**: a question only sees reading tools; a fix sees reading, editing, git and tests.

| | small model | large model | why |
|---|---|---|---|
| **Tools** | all specialized tools (`git`, `delete_file`, `run_tests`, `find_files`, …), filtered by request type | a few general tools: read, edit, shell | small models use a tool only when its name matches the job |
| **Edits** | an edit is applied when one block of the file is a very close match (the diff is still shown for approval) | exact match only | small models often get whitespace or a character wrong when copying text |
| **Instructions** | a short recipe per task type, e.g. "fix a bug: reproduce → find → fix → test" | short general instructions | small models skip steps unless told |
| **Loop** | beastron enforces phases: explore → change → verify | the model decides its own steps | small models stop early or don't verify |
| **Thinking** | on for the first step and after a failure, off for routine steps | the model's default | speed: on one bug fix, 181 s with thinking vs 35 s without |
| **Context** | smaller limits, earlier summarizing | larger | a 32K context fills quickly |

**Guardrails for every model.** These don't depend on the model's judgment:
- **Intent routing:** a question gets only reading tools, so it can't lead to edits.
- **Verification gate:** beastron knows the project's test command (pytest, Maven, Gradle, npm). If files changed and the model didn't test, beastron runs the tests itself and sends failures back once.
- **Claims check:** the final summary is compared with the change record. A mismatch is shown to the user.
- **Build-log summaries:** long Maven, Gradle and pytest output is reduced to errors, failing tests and the summary lines.
- **Compaction:** old tool output is summarized when the context fills up.
- **Undo:** file contents are saved before each change, and `/undo` restores them.

Every one of these is a switch, so its effect can be measured, and it is kept only if the measurements support it.

### 5.5 Model selection

Candidate models are measured on the full suite on the reference machine:
- **Score** per category
- **Speed**
- **Memory:** the whole model must run on the GPU

The current candidates that fit in 24 GB are the Qwen3 8B model with and without thinking, gpt-oss-20b, Qwen3-Coder 30B in a compressed form, Devstral Small, and current 24–27B Qwen releases.

The best two become the default and fast profiles. The comparison is repeated whenever a promising new model appears. Because a model only changes when the owner downloads a new one, and the trace records which one ran, **version drift becomes a deliberate choice instead of a surprise**.

## 6. Roadmap

| Milestone | Content | Done when |
|---|---|---|
| **M0 Scoreboard** | session trace; eval task format, workspaces, checks, sandboxed runner, report and baseline; `beastron eval …` commands; about 8 synthetic seed tasks covering the failure types seen so far | the seed suite runs for two model profiles and produces a report, and deliberately breaking a known fix shows up as a regression |
| **M1 Real usage and tasks** | log readers and the classifier; `evals/usecases.md` with weights; the commit miner and approval flow; the suite grows to about 40 tasks | weights committed; at least 30 approved tasks, at least 2 per category |
| **M2 Reference and model choice** | the Claude Code reference run; the model comparison; the first real score | the score and the gap per category are known; default profiles chosen from data |
| **M3 Reliability and tiers** | the guardrails and settings from section 5.4, one pull request each, in the order of the biggest gaps; tiers and calibration | every pull request shows its measured effect; score ≥ 0.80 |
| **M4 Feedback from daily use** | `/bad` and `/good`, `beastron triage`, `beastron eval capture`, resuming sessions | at least one real failure captured, fixed and guarded by a task |
| **M5 Regression discipline** | `beastron eval gate` before each merge; baseline update procedure; optional nightly runs; tracking of model and Claude Code versions | the gate blocks a deliberately introduced regression |

The critical path is M0 → M1 → M2 → M3: nothing can be prioritized before it can be measured, and nothing can be weighted before real usage is known. Each milestone is planned in detail when it starts, using the data from the one before. Progress is tracked as GitHub milestones and issues.

## 7. Current status and known limits

Measured on the reference machine with `qwen3:8b` served by Ollama 0.40.16, by running tasks by hand. The scoreboard (M0) doesn't exist yet, so these are individual observations, not scores:

| Task | Result |
|---|---|
| Fix an off-by-one bug in a small Python project and run its tests | thinking on: correct in 1 of 1 run, 181 s. Thinking off: first announced the fix and stopped without doing it; after beastron started nudging the model to continue, correct in 3 of 3 runs, about 35 s each |
| Summarize what a documentation-heavy project is about | before the project briefing: wrong, a guess from file names. With the briefing: correct with both thinking on and off, in one step; the first answer took about 55 s because the model reads about 6.5K tokens of documentation first |
| Remove a recently added feature (three new files and one configuration line) without touching an unrelated change | 3 runs, none fully correct. One deleted the three files but left the configuration line. The other two removed the line, but deleted only one file or none, and also undid the unrelated change. All three used git to find the changes, and beastron's change record matched what really happened in all three |
| Answer a question about possible changes | the model tried to edit files instead of answering. After each "no" it tried again: 6 edit attempts in one run. Now a "no" ends the turn, but the attempt to edit in response to a question still happens |

Other facts:
- **Speed:** `qwen3:8b` generates about 20–26 tokens per second on the reference machine. With a 32K context it uses about 9.4 GB of memory.
- **Known issue:** Ollama 0.40.16 crashes when loading models on M5 chips unless it is started with `GGML_METAL_TENSOR_DISABLE=1`.

**In short:** beastron is usable for small, clearly defined tasks, and unreliable for multi-step changes. Reaching the goal depends on M0–M3: measuring first, then closing the biggest gaps one at a time, and trying stronger models that fit the machine.

## 8. Decisions

| Decision | Reason |
|---|---|
| Local models only while beastron runs | the point of the project: no cost per use, no data leaving the machine, no unannounced model changes |
| Claude Code with a pinned Sonnet model as the reference | a target relative to Claude Code only means something when measured against Claude Code itself; pinning the model keeps the reference stable |
| Score weighted by the owner's real usage | the goal is real work, not a generic benchmark |
| Apache-2.0 license | permissive, with an explicit patent grant; the same license as the owner's other projects; compatible with beastron's dependencies (Apache-2.0, MIT, BSD). Model weights aren't bundled; users download them under their own licenses |
| Python, OpenAI-compatible API | works with every common local model server; the agent never depends on one provider |
