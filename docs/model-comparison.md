# Model comparison on the reference machine

Measured on 2026-11-19 on an Apple M5 with 24 GB, a modern baseline developer machine. Models were served by Ollama 0.40.16 (started with `GGML_METAL_TENSOR_DISABLE=1 OLLAMA_CONTEXT_LENGTH=32768`) at a 32K context. The suite is the 57 tasks in `evals/tasks/` and the score is usage-weighted (`evals/usecases.json`), as in `evals/README.md`. The summaries are in `evals/results/`.

## What was compared, and what was not

| profile | model | thinking | runs |
|---|---|---|---|
| `qwen35-9b` | `qwen3.5:9b` (Ollama, 7.6 GB download) | default (on; the traces carry reasoning on every turn) | 57 (1 repeat) |
| `qwen3-8b-local` | `qwen3:8b` (Ollama, 5.2 GB download) | default (on) | 57 (1 repeat) |
| `claude-code-sonnet-5-5` | Claude Code 2.1.328, `claude-sonnet-5-5` | reference, see [#19](https://github.com/baokhang83/beastron/pull/57) | 171 (3 repeats) |

The owner limited the comparison to these two small models. **Measured only in part:** Devstral Small 2, see [below](#devstral-small-2-partial-stopped-early). **Not measured:** gpt-oss:20b, Qwen3-Coder 30B and Qwen3.6 27B (Q3 builds), and `qwen3-8b-fast` (qwen3:8b with thinking off). Its run was stopped after 10 runs, 1 passed, and those runs are dropped as unreliable: they are not in any number below. So **there is no measured "fast" profile**, and the larger models might do better or not fit; this document says nothing about them.

## Results

One repeat per task means each task is a single pass or fail, so the numbers are rough. The 95% range (Wilson score) of the plain pass rate is 56–79% for qwen3.5:9b and 27–52% for qwen3:8b.

| | claude-code-sonnet-5-5 | qwen35-9b | qwen3-8b-local |
|---|---|---|---|
| runs passed | 171 / 171 | 39 / 57 | 22 / 57 |
| **usage-weighted score** | 100% | **67%** | **40%** |
| median time per task | 14 s | 53 s | 257 s |
| 90th percentile | 35 s | 561 s | 1203 s |
| timeouts | 0 | 0 | 16 |
| errors | 0 | 0 | 1 |
| tool errors (all runs) | 17 | 76 | 189 |
| memory, whole model on the GPU | – | 15–17 GB | 9.4 GB |

The memory is what `ollama ps` showed while each model ran at the 32K context, both 100% on the GPU. qwen3.5:9b grows during a run. At 17 GB of the 24 GB it fits, with little room left for other applications.

### Per category (passed / runs)

| category | weight | claude-code | qwen35-9b | qwen3-8b-local |
|---|---|---|---|---|
| explain | 28.9% | 100% | 10 / 13 | 10 / 13 |
| feature | 23.8% | 100% | 4 / 13 | 0 / 13 |
| git | 16.2% | 100% | 4 / 7 | 3 / 7 |
| fix | 10.1% | 100% | 6 / 9 | 3 / 9 |
| docs | 9.5% | 100% | 4 / 4 | 0 / 4 |
| build | 6.2% | 100% | 3 / 3 | 2 / 3 |
| remove | 3.1% | 100% | 2 / 2 | 1 / 2 |
| config | 1.9% | 100% | 2 / 2 | 2 / 2 |
| refactor | 0.2% | 100% | 2 / 2 | 1 / 2 |
| tests | 0.1% | 100% | 2 / 2 | 0 / 2 |

qwen3.5:9b passed 19 tasks that qwen3:8b failed, and failed 2 that qwen3:8b passed (`explain-blastradius-would-miss`, `git-history-question`). With a single run each, those two could be luck.

## Devstral Small 2 (partial, stopped early)

Devstral Small 2 (`devstral-small-2:24b`, a 24B dense model from Mistral, 15 GB download) was run later, after the comparison above. **The run was stopped by the owner after 42 of 57 tasks**, because the early results showed it was not better than qwen3.5:9b and was much slower. It is not part of the tables above and no summary file was saved for it; the raw results stay in the local state folder.

Results on the 42 tasks it finished (one repeat; the same 42 tasks for every column):

| | Devstral Small 2 | qwen3.5:9b | qwen3:8b |
|---|---|---|---|
| passed | 29 / 42 | 31 / 42 | 20 / 42 |
| median time per task | 150 s* | 38 s | 131 s |
| total time on these tasks | 4.3 h* | 1.3 h | 3.6 h |
| timeouts | 6* | 0 | 9 |

\* **Why Devstral is slower, and why these numbers are not a clean comparison.** The first 35 tasks ran with a 32K context and a normal cache. At that setting Devstral takes 20 GB, and `ollama ps` showed **20% of it on the CPU and 80% on the GPU**, because the GPU can use only part of the 24 GB. That spill made every task slow and caused all 6 timeouts (five `blastradius-*` replays at 20 minutes, `explain-warden-boost` at 10). I then restarted the server with a 16K context and a compressed (q8) context cache, which put the whole model on the GPU (about 8.6 generated tokens/s), and ran the next 7 tasks that way. On those 7 tasks the median times were similar (92 s for Devstral, 112 s for qwen3.5:9b), but 7 tasks are too few to say more. One further run was lost to a server restart and is excluded. So on this 24 GB machine Devstral cannot run at the 32K context the other models use, and at 16K its results come from a smaller context.

What the partial run shows:
- **Not better than qwen3.5:9b:** 29 against 31 passed on the same 42 tasks, a gap within the noise of one repeat. It passed `git-find-breaking-commit`, `git-history-question` and `explain-blastradius-would-miss`, which qwen3.5:9b failed, and failed five that qwen3.5:9b passed.
- **The hard replays are mostly untested:** the 15 tasks not run include most of the `mnemo-*` replays. Devstral failed the 3 it finished (a 4th was lost to a server restart), and qwen3.5:9b also failed most of them, so this part is open.
- **Memory:** 20 GB at a 32K context (20% on the CPU), against 15–17 GB for qwen3.5:9b fully on the GPU.

Devstral is therefore **not chosen**. This does not rule out a better result with a smaller or more compressed build that fits at a 32K context; that was not tried.

## The choice

- **Default: `qwen35-9b`.** It scores 67% against 40%, finishes the median task in about a fifth of the time, and has no timeouts. The cost is memory: 15–17 GB against 9.4 GB.
- **Fast profile: none chosen.** The only fast candidate was dropped, so `models.toml` keeps `qwen3-8b-fast`, marked as unmeasured. A fast profile needs a measured candidate: for example `qwen35-9b` with thinking off, which was not run.
- `qwen3-8b-local` stays as a smaller-memory option.

## What this does not show

- The target is 80% of the reference score. The best measured model is at 67%, **not there yet**.
- One repeat per task; the Claude Code reference has three. A change of a few tasks is within the noise.
- The reference passed every run, so the suite can't show where Claude Code is weaker than the local models, only where they fall short of it.
