# Firox

A coding agent for local and open-weight models. Firox talks to any
OpenAI-compatible server (Ollama, LM Studio, llama.cpp, vLLM, OpenRouter), and
you can switch models with one setting.

## Setup

```bash
# 1. Model server (Ollama). On Apple M5 Macs the tensor setting is required for now.
GGML_METAL_TENSOR_DISABLE=1 OLLAMA_CONTEXT_LENGTH=32768 ollama serve
ollama pull qwen3:8b

# 2. Firox
uv sync
```

## Usage

```bash
uv run firox                                  # interactive session
uv run firox "fix the failing test"           # one task, then exit
uv run firox -m qwen3-8b-fast                 # pick a model profile
uv run firox -v                               # show reasoning and full tool output
```

To use firox in another project, run it from that project's folder:
`uv run --project ~/Documents/firox firox`. It uses `models.toml` from the
current folder, or `~/.config/firox/models.toml`.

Commands inside a session: `/help`, `/models`, `/model NAME`, `/clear`,
`/context`, `/verbose`, `/quit`. Ctrl-C stops the agent; Ctrl-D quits.

## Models

Profiles live in `models.toml`. Each one is a server URL, a model name and a
context size, plus optional settings:

| Setting | Purpose |
|---|---|
| `reasoning_effort` | `"none"` turns thinking off (much faster on Qwen3, weaker on hard tasks) |
| `api_key_env` | Environment variable that holds the API key, for hosted providers |
| `temperature` | Sampling temperature |
| `quirks` | Per-model fixes, e.g. `["send_reasoning_back"]` for gpt-oss |
| `extra_body` | Extra server-specific request fields |

## Tools

| Tool | Approval |
|---|---|
| `list_files`, `search_code`, `read_file` | no |
| `write_file`, `edit_file` | yes, after showing a diff |
| `run_shell` | yes, after showing the command |

File tools can't reach paths outside the folder firox runs in, and can't write
inside `.git/`. Shell commands run with your user's permissions, so read each
one before approving it.

## Layout

```
src/firox/
  cli.py      entry point, prompt, slash commands
  ui.py       Rich display (listens to agent events)
  agent.py    the loop: model -> tools -> model, until done
  events.py   what the agent reports while it works
  client.py   OpenAI-compatible model client
  quirks.py   fixes for model-specific behavior
  config.py   models.toml loading
  tools/      file tools, shell tool, sandbox
tests/        uv run pytest
```
