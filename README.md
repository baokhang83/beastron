<div align="center">
  <img src="docs/assets/beastron-logo2.png" alt="Beastron Mascot" width="260" />
  <h1>Beastron — Local Open-Weight Coding Agent</h1>
</div>

<div align="center">
  
<h3>How close can a free, local coding agent get to a hosted one on real work?</h3>

<p>
Beastron is an <b>experimental</b> coding agent that runs entirely on your own machine (no subscription, no per-token cost, no code sent anywhere) and a <b>measurement project</b> that answers that question with numbers. It works with any OpenAI-compatible model server, such as Ollama or LM Studio.
</p>

<a href="https://github.com/baokhang83/mnemo-warmup/actions/workflows/build.yml"><img alt="build" src="https://github.com/baokhang83/mnemo-warmup/actions/workflows/build.yml/badge.svg"></a>
<a href="LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache%202.0-blue.svg"></a>
<img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-yellow">

</div>

## The goal

**Complete at least 80% as many tasks as Claude Code** (with a pinned `claude-sonnet-5-5`), on a suite of tasks drawn from real work and weighted by how often that kind of task actually comes up, on a modern baseline developer machine (reference: Apple M5, 24 GB). Every claim in this README is a measured result or is marked as unmeasured.

## Where we stand

| | usage-weighted score | tasks passed | median task time |
|---|---:|---:|---:|
| Claude Code 2.1.328, `claude-sonnet-5-5` (3 repeats) | 100% | 171 / 171 | 14 s |
| **beastron + `qwen3.5:9b`** (latest full run) | **72.2%** | 42 / 57 | 57 s |
| beastron + `qwen3.5:9b` (first run) | 66.5% | 39 / 57 | 53 s |
| beastron + `qwen3:8b`, thinking on | 40% | 22 / 57 | 257 s |
| **Target** | **80%** | | |

Claude Code passed every run, so it sits at the ceiling and **beastron's score is also its share of the reference**: about 72%, about 8 points short.

**How far to trust that.** One repeat per task, so one-task changes are noise; a 95% range on the plain pass rate is about 61% to 83%, which still includes 80%. The latest full run was measured before the most recent harness changes (guard, reminder, empty-reply nudge), which have been tested on targeted tasks but not yet re-measured on the whole suite. The suite is 70% Java, and 18 of its 57 tasks replay commits from a handful of public repositories (with 8 more questions about them), so it measures those more than Java in general. The reference machine is the only one measured.

**Where the missing points are:** almost all in the **long Java tasks** (the "feature" category: 4 of 13 passed, 18 weighted points missing). Short tasks (build, config, refactor, remove, tests, git) are at or near 100%.

## How it is measured

- **A 57-task suite** ([evals/README.md](evals/README.md)): synthetic tasks for known failure types, 18 replays of real commits from public Apache-2.0 repositories (no copied code, only the URL and commit hash), and questions about those repositories. Each task has automatic checks (tests, file scope, answer content).
- **Usage weights:** each category counts as much as that kind of work occurs in real use ([evals/usecases.md](evals/usecases.md)): explain 29%, feature 24%, git 16%, fix 10%, docs 10%, build 6%, other 5%.
- **A sandboxed runner:** every action is approved automatically, so shell commands run in a macOS sandbox that only allows writes in the run's own folder.
- **A Claude Code reference runner** (`beastron eval reference`): the same tasks, workspaces and checks, with Claude Code in safe mode and a pinned model. Only summaries are stored, never transcripts.
- **Baselines and regression detection:** summaries are committed to `evals/results/`; `beastron eval report` compares a run with the stored baseline and flags real regressions, a changed model file, or a changed Claude Code version.
- **Traces:** every run records each step, tool call, token count and timing, so failures can be read, not guessed at.

## What we have learned

- **Worked and measured:** read-only tools for questions (no more edits when asked a question; 25 of 25 on the question tasks with the other fixes); an empty-reply nudge (git tasks 3/10 to 9/10); a reminder to finish an unfinished merge or cherry-pick (merge task 5/5 against 2/5); typed git commands understood by the git tool (tool errors 31 to 6); a guard against git commands that discard the task's changes (a `git stash` once destroyed a solved run).
- **Tried and not supported by the data:** a per-step output cap (no step in the long runs exceeded it), close-match edits (2 of 16 failures fixable), longer step limits, build-log summaries, "read the named test first" (44 of 45 runs already did). Four-bucket model routing scored 79.7% on the data used to choose it but 71.8% with each task held out, consistent with overfitting; escalation after two failed builds or 240 seconds would have switched away from three runs that ultimately passed. The three models scored 72.2% (`qwen3.5:9b`), 70.6% (`gpt-oss:20b`) and 73.9% (Qwen3-Coder 30B-A3B Q3, including a separate Git retry). An experimental advisor-model branch is not merged.
- **Thinking off** (`qwen3.5:9b`) is inconclusive: 4 of 9 long tasks against 3 of 9 with thinking on, fewer tokens but more steps.
- **Stronger local models help on the long tasks:** `gpt-oss:20b` and Qwen3-Coder 30B-A3B Q3 fit fully on the 24 GB M5 GPU at 32K context and substantially outperform the dense 9B model on multi-module Java. With Ollama's native `qwen3-coder` renderer and parser, Qwen3-Coder is the leading single-model candidate (73.9%, about 44 s median). Before closing model testing, try nudging malformed `<tool_call>` text leaks and raising `max_steps` from 25 to 40 for fast MoE profiles.

## What it does today

- **An agent loop with tools:**

  | Tool | Approval |
  |---|---|
  | `list_files`, `search_code`, `read_file` | no |
  | `git` (read-only: `status`, `diff`, `log`, `show`) | no |
  | `write_file`, `edit_file`, `delete_file` | yes, after showing a diff |
  | `run_shell` | yes, after showing the command |

- **Questions get read-only tools.** A request that only asks something gets the reading tools, so editing is impossible. `/do REQUEST` runs a request with every tool.
- **Safety:** file tools can't reach outside the project or write inside `.git/`. Shell commands run with your permissions, so read each one before approving. A guard blocks common Git discard commands (`stash`, `reset --hard`, `restore`, `clean`, `checkout --`) when they would discard changes made during the task. It is **not a security boundary**, does not cover `git branch -D`, and does not protect uncommitted work already present at the start of the task.
- **Guardrails for small models:** a nudge when the model announces a step without taking it, when its reply is empty (including when the answer was only in its reasoning), and when a merge, cherry-pick or rebase is still unfinished at the end of the turn; invalid tool calls go back as errors; a failed edit shows the closest matching text; saying "no" ends the turn; a task stops after 25 model calls.
- **A change record** from the tools themselves, not from the model's summary, which can be wrong.
- **A project briefing:** at session start beastron reads the project's documentation (`AGENTS.md`, else `README.md`, plus linked docs).
- **Session traces** in `~/.local/state/beastron/sessions/`: what you asked, each tool call with its output, tokens, timing and the exact model file.
- **Any model:** profiles in `models.toml`; switch with `-m` or `/model` during a session.

## Setup

```bash
# 1. Model server (Ollama). On Apple M5 Macs the tensor setting is required for now.
GGML_METAL_TENSOR_DISABLE=1 OLLAMA_CONTEXT_LENGTH=32768 ollama serve
ollama pull qwen3.5:9b          # the default profile; uses about 15-18 GB at a 32K context

# 2. Install beastron as a command you can run from any folder.
uv tool install --editable ~/Documents/beastron

# 3. Make models.toml available from any folder (a link, so there's still one file to edit).
mkdir -p ~/.config/beastron
ln -s ~/Documents/beastron/models.toml ~/.config/beastron/models.toml
```

uv puts the `beastron` command in `~/.local/bin`. If your shell can't find it, run `uv tool update-shell` and open a new terminal.

## Usage

Run beastron from the folder of the project you want it to work on. That folder is the only place its file tools can read and write.

```bash
cd ~/Documents/my-project
beastron                                  # interactive session
beastron "fix the failing test"           # one task, then exit
beastron -m qwen3-8b-local                # pick a model profile
beastron -v                               # show reasoning and full tool output
beastron --no-trace                       # don't record the session
```

Commands inside a session: `/help`, `/models`, `/model NAME`, `/do REQUEST`, `/clear`, `/context`, `/verbose`, `/quit`. Ctrl-C stops the agent; Ctrl-D quits.

**Measuring** (details in [evals/README.md](evals/README.md)):

```bash
beastron eval run -s quick -n 1           # the quick suite, one repeat
beastron eval run -t TASK -n 5            # one task, five repeats
beastron eval report                      # latest run, compared with the baseline
beastron eval baseline                    # store the latest run as the baseline
beastron eval verify                      # every task fails as given and passes with its solution (no model)
beastron eval reference                   # Claude Code on the same tasks (a real session on your account)
```

## Models

Profiles live in `models.toml`: a server URL, a model name and a context size, plus optional settings (`effort` to turn thinking off, `temperature`, `quirks` such as `send_reasoning_back` for gpt-oss, `api_key_env` for hosted providers). The default is `qwen35-9b`. Other models were compared in [docs/model-comparison.md](docs/model-comparison.md): `qwen3:8b` scored 40%, and Devstral Small 2 did not beat `qwen3.5:9b` and only fits the GPU at a 16K context. Hosted services can be used through `api_key_env`, but that sends your code to the provider, which goes against the point of the project; it is meant for benchmarking.

## Roadmap

Milestones M0 (scoreboard), M1 (real usage and tasks) and M2 (reference and model choice) are done. **M3, reliability and tiers, is in progress:** read-only questions and the git fixes are in; next is measuring a stronger model on the long tasks and, if it helps, switching to it automatically (issue #32). M4 (feedback from daily use) and M5 (regression discipline) are planned. See the [issues](https://github.com/baokhang83/beastron/issues) and [docs/plan.md](docs/plan.md).

## Layout

```
src/beastron/
  cli.py            entry point, prompt, slash commands
  agent.py          the loop: model -> tools -> model, until done
  policy.py         which tools a request gets (questions are read-only)
  discard_guard.py  blocks git commands that would discard the task's changes
  observability.py  passive counters recorded in traces
  tools/            file tools, read-only git, shell, sandbox
  evals/            task suite runner, checks, reports, Claude Code reference runner
  usecases/         reads real sessions to weight the suite
  ui.py events.py briefing.py client.py quirks.py config.py trace.py
evals/              the 57 tasks, usage weights, committed result summaries and baselines
docs/               plan.md, model-comparison.md, m2-gap-report.md, refitting.md
tests/              uv run pytest
```

## License

[Apache-2.0](LICENSE).
