# Beastron

**How close can a free, local coding agent get to a hosted one on real work?**

Beastron is an **experimental** coding agent for local open-weight models, built to answer that question. It runs entirely on your own machine: no subscription, no per-token cost, and no code sent anywhere. It works with any OpenAI-compatible model server (Ollama, LM Studio, llama.cpp, vLLM).

It is also a measurement project. Beastron is compared with Claude Code on the same tasks, drawn from real development work, and the gap is closed one measured change at a time. The target is to complete at least 80% as many of those tasks as Claude Code does. The full plan, the process and the roadmap are in [docs/plan.md](docs/plan.md).

## Status: early, and honest about it

Beastron handles small, clearly defined tasks. It is **not yet reliable for multi-step changes**. Observations so far, made by hand with `qwen3:8b` on the reference machine (the automated scoreboard doesn't exist yet):

- **Fixing an off-by-one bug** in a small Python project, with tests: correct in 3 of 3 runs with thinking off (about 35 s each), and 1 of 1 with thinking on (181 s).
- **Summarizing what a project is about:** wrong before beastron read the project's docs itself; correct since then, in one step.
- **Removing a recently added feature** while keeping an unrelated change: none of 3 runs fully correct, each wrong in a different way. beastron's own record of changed files was accurate every time.
- **Answering a question:** the model sometimes tries to edit files instead of answering. You can refuse, and beastron then stops instead of letting it retry.

Details in [docs/plan.md § Current status](docs/plan.md#7-current-status-and-known-limits).

**Reference machine:** Apple M5, 24 GB unified memory. `qwen3:8b` generates about 20–26 tokens/s there and uses about 9.4 GB with a 32K context. Nothing has been measured on other machines yet.

## What it does today

- **An agent loop with tools:**

  | Tool | Approval |
  |---|---|
  | `list_files`, `search_code`, `read_file` | no |
  | `git` (read-only: `status`, `diff`, `log`, `show`) | no |
  | `write_file`, `edit_file`, `delete_file` | yes, after showing a diff |
  | `run_shell` | yes, after showing the command |

- **Safety:** file tools can't reach outside the project folder or write inside `.git/`. The read-only `git` tool can't be talked into writing files or running programs. Shell commands run with your user's permissions, so read each one before approving it.
- **A change record:** after each task, beastron lists the files *its tools* changed (`A`/`M`/`D` with line counts). This comes from the tools, not from the model's summary, which can be wrong.
- **A project briefing:** when a session starts, beastron reads the project's documentation (`AGENTS.md`, else `README.md`, plus linked docs) and gives it to the model.
- **Guardrails for small models:**
  - When a model announces a step but doesn't take it, beastron nudges it to continue.
  - Saying "no" to an action ends the turn.
  - A failed edit shows the model the closest matching text from the file.
  - Invalid tool calls go back to the model as errors instead of crashing.
  - A task stops after 25 model calls.
- **Session traces:** every session is recorded in `~/.local/state/beastron/sessions/` as one JSONL file: what you asked, each tool call with its full output, the change record, tokens and timing, the exact model file (its digest) and the project's git state at the start. Traces stay on your machine; `--no-trace` turns them off.
- **Any model:** profiles in `models.toml`; switch with `-m` or `/model` during a session.

The eval harness and a 57-task suite drawn from real work exist ([evals/README.md](evals/README.md)); the first score against Claude Code doesn't yet. Not there yet, and planned: that score, undo, summarizing long conversations, automatic test runs, adapting the tools to the model's strength. See the [roadmap](docs/plan.md#6-roadmap).

## Setup

```bash
# 1. Model server (Ollama). On Apple M5 Macs the tensor setting is required for now.
GGML_METAL_TENSOR_DISABLE=1 OLLAMA_CONTEXT_LENGTH=32768 ollama serve
ollama pull qwen3:8b

# 2. Install beastron as a command you can run from any folder.
#    --editable: changes to the code here apply immediately, no reinstall needed.
uv tool install --editable ~/Documents/beastron

# 3. Make models.toml available from any folder (a link, so there's still one file to edit).
mkdir -p ~/.config/beastron
ln -s ~/Documents/beastron/models.toml ~/.config/beastron/models.toml
```

uv puts the `beastron` command in `~/.local/bin`. If your shell can't find it,
run `uv tool update-shell` and open a new terminal.

After changing beastron's dependencies in `pyproject.toml`, reinstall once with
`uv tool install --editable --force ~/Documents/beastron`. Code changes need nothing.

## Usage

Run beastron from the folder of the project you want it to work on. That folder
is the only place its file tools can read and write.

```bash
cd ~/Documents/my-project
beastron                                  # interactive session
beastron "fix the failing test"           # one task, then exit
beastron -m qwen3-8b-fast                 # pick a model profile
beastron -v                               # show reasoning and full tool output
beastron --no-trace                       # don't record the session
beastron eval --help                      # eval commands (being built: see the M0 milestone)
```

Use `beastron`, not `uv run beastron`: inside another project, `uv run` sets up that
project's environment and won't find beastron.

Beastron looks for `models.toml` in this order: the `--config` option, the
`BEASTRON_CONFIG` environment variable, the current folder, then
`~/.config/beastron/models.toml`.

Commands inside a session: `/help`, `/models`, `/model NAME`, `/clear`,
`/context`, `/verbose`, `/quit`. Ctrl-C stops the agent; Ctrl-D quits.

### Project briefing

When a session starts (and after `/clear`), beastron reads the project's
documentation and gives it to the model, so the model knows what the project
is before its first step:

1. It starts from `AGENTS.md`, else `README.md`, else the most likely looking
   other `.md` file.
2. It reads that file fully, then the Markdown files it links to, two levels
   deep. Informative names (overview, architecture, manifesto, ...) go first;
   files like CHANGELOG, LICENSE or CODE_OF_CONDUCT are skipped.
3. It stops at about 20% of the model's context. Linked files that didn't fit
   are listed by name for the model.

The header shows what was read:
`briefing: README.md, MANIFESTO.md, CONTRIBUTING.md · 6.5K tokens`. The first
answer takes longer to start because the model reads the briefing first.
`--no-briefing` turns it off.

## Models

Profiles live in `models.toml`. Each one is a server URL, a model name and a
context size, plus optional settings:

| Setting | Purpose |
|---|---|
| `effort` | How hard the model thinks; `"none"` turns thinking off (much faster on Qwen3, weaker on hard tasks). Sent to the server as `reasoning_effort`. |
| `api_key_env` | Environment variable that holds the API key, for hosted providers |
| `temperature` | Sampling temperature |
| `quirks` | Per-model fixes, e.g. `["send_reasoning_back"]` for gpt-oss |
| `extra_body` | Extra server-specific request fields |

Beastron can also talk to hosted OpenAI-compatible services (for example OpenRouter) through `api_key_env`. That sends your code to the provider, which goes against the point of the project. It's meant for benchmarking only.

## Layout

```
src/beastron/
  cli.py        entry point, prompt, slash commands
  ui.py         Rich display (listens to agent events)
  agent.py      the loop: model -> tools -> model, until done
  events.py     what the agent reports while it works, including the change record
  briefing.py   reads the project's docs at session start
  client.py     OpenAI-compatible model client
  quirks.py     fixes for model-specific behavior
  config.py     models.toml loading
  tools/        file tools, read-only git, shell, sandbox
tests/          uv run pytest
docs/plan.md    goal, process, architecture, roadmap
docs/refitting.md  refitting the eval suite to your work
```

## License

[Apache-2.0](LICENSE).
