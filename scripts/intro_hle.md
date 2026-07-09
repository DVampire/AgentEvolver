# HLE Evaluation Guide

This document explains how to run end-to-end evaluation on **HLE (Humanity's Last Exam, 2500 questions)**. It covers: environment and key setup, the first run, how to watch progress and results, resuming after interruption, and re-running "empty / unfinished" questions.

> Entry script: examples/run_hle.py — Default config: configs/hle.py
>

> GitHub: https://github.com/DVampire/AgentEvolver
>

> 🌐 中文版请见 [intro_hle_zh.md](intro_hle_zh.md)

---

## **TL;DR**

Assuming you have already configured Vault, opencode, and the Python environment per [INSTALL.md](INSTALL.md):

```bash
# 0) Small smoke test (5 questions, a few minutes)
python examples/run_hle.py --start 0 --end 5 --max-concurrency 4

# 1) Full run over 2500 questions
python examples/run_hle.py --max-concurrency 16
#    During the run: the terminal prints Progress / ✅ / ❌;
#    live results are written to workdir/hle/results/hle/benchmark_hle_<timestamp>.json
#    logs at workdir/hle/hle.log

# 2) Interrupted midway — resume
python examples/run_hle.py --resume        # automatically uses the latest results file

# 3) After a full run, re-run questions with empty / unfinished predicted_answer
python examples/run_hle.py --resume <results.json> --filter null

# 4) Recompute stats + visualization page only (no re-run)
python examples/run_hle.py --stats
#    generates workdir/hle/results/hle/viewer.html, open in a browser to inspect
```

---

## **Full evaluation workflow (three steps)**

#### Each step continues on the results file produced by the previous one

#### **1. Normal full run**

```bash
python examples/run_hle.py --max-concurrency 16
```

Run all 2500 questions and obtain the baseline result `benchmark_hle_<ts1>.json`. If interrupted, use `--resume` to continue — this is not counted as a separate step.

### **2. `--filter null` re-run of empty questions**

```bash
python examples/run_hle.py --resume benchmark_hle_<ts1>.json --filter null --max-concurrency 16
```

Only picks questions with empty `predicted_answer` / unfinished planner and re-runs them — these may have failed to finish the full answering process due to unstable API keys, timeouts, or other transient issues, so this avoids them being mistakenly judged wrong.

### **3. `--eval-only` LLM re-judging**

```bash
python examples/run_hle.py --eval-only --resume benchmark_hle_<ts2>.json --eval-model-name openrouter/o3-mini
```

Does not re-run answers; only has the LLM judge re-score. Judging all answers together after they are complete gives the most accurate accuracy.

---

## **Evaluation scope & artifacts**

- **Question count**: the HLE test split has 2500 questions (both multiple-choice and exact-match types; some include images).
- **Per-question workflow**: `--use-bus` is on by default, i.e. the full AgentEvolver agent pipeline (planner + deep_researcher + deep_analyzer + opencode + sop).
- **Artifacts**: each run produces, under `workdir/hle/results/hle/`:
    - `benchmark_hle_<timestamp>.json` — per-question records + summary (written live, can be inspected anytime)
    - `benchmark_hle_<timestamp>_tagged.json` / `hle_tagged.json` — the tagged version generated after evaluation (read by the viewer)
    - `benchmark_hle_<timestamp>_viewer.html` / `viewer.html` — a visualization page you can open directly in a browser

---

## **Environment setup**

For detailed setup, see [INSTALL.md](INSTALL.md); please follow it strictly.

---

## **Running the full 2500 questions**

### **Launch command**

```bash
python examples/run_hle.py \
    --config configs/hle.py \
    --max-concurrency 16
```

Common optional arguments:

| Argument | Default | Description |
| --- | --- | --- |
| `--max-concurrency` | 16 | Number of concurrent tasks. Can be raised to 32 with ample memory / quota |
| `--max-rounds` | 20 | Max planner rounds per question in bus mode (usually no need to change) |
| `--start` / `--end` | None | Evaluate only the HLE `[start, end)` range, for sharding or smoke tests |
| `--tags TAG [TAG ...]` | None | Only run questions matching the given tags (tags defined in `datasets/hle/data/tags.json`) |
| `--eval-model-name` | `openrouter/o3-mini` | LLM evaluator used for re-judging |
| `--no-use-bus` | / | Fall back to direct LLM inference (no agent pipeline), for comparison only |

### **Watching progress during evaluation**

Right after launch it prints a line:

```
| Results will be saved to: workdir/hle/results/hle/benchmark_hle_2026-05-18_10-30-12.json
```

**This file is the single authoritative artifact of the evaluation** — note its path. The framework atomically rewrites the entire JSON (including the summary) after each completed question, so you can `cat` it or view it in an editor anytime:

```bash
# Watch the summary live (completed / correct / accuracy)
jq '.summary' workdir/hle/results/hle/benchmark_hle_<timestamp>.json
```

Key terminal log markers:

- `| Progress: 137/2500 (5.5%)` — overall progress
- `| ✅ Correct [<task_id>] | score=1.0 | time=42.3s` — a question judged correct
- `| ❌ Wrong [<task_id>] | score=0.0 | time=58.1s` — a question judged wrong
- `| ❌ [<id>] LLM eval: wrong — retrying` — the framework's built-in "LLM re-judge + auto-retry" logic triggered after a wrong answer

The full text log is at `log_path = hle.log` in the config (i.e. `workdir/hle/hle.log`); follow it with `tail -f`.

### **Viewing results after evaluation**

After all questions finish, `run_hle.py` automatically prints a summary, e.g.:

```
==================================================
Results file: workdir/hle/results/hle/benchmark_hle_2026-05-18_10-30-12.json
==================================================
Overall: 612/2500 = 24.48%
==================================================
Per-tag accuracy (sorted by task count):
  math                            123/420  = 29.29%
  biology                          89/310  = 28.71%
  ...
```

To recompute stats / viewer offline without re-running:

```bash
# Uses the most recent timestamped file under workdir/hle/results/hle/ by default
python examples/run_hle.py --stats

# Or specify a file
python examples/run_hle.py --stats --resume workdir/hle/results/hle/benchmark_hle_<timestamp>.json
```

- `--stats` recomputes and prints accuracy, and generates `viewer.html` + `hle_tagged.json` in the same directory as the results. **Open `viewer.html` directly in a browser to interactively browse each question's prompt, model answer, and ground truth by tag/category** — the most convenient way for reviewers to inspect.

JSON field quick reference (each record in the `results` array):

| Field | Meaning |
| --- | --- |
| `task_id` | HLE question ID |
| `question` | Question text (multipleChoice trailing hint stripped) |
| `answer` / `ground_truth` | Correct answer |
| `predicted_answer` | Model's final answer |
| `correct` | Whether judged correct (bool) |
| `category` / `raw_subject` / `tags` | Subject / tags |
| `processing_time` | Per-question time (seconds) |
| `llm_eval_reasoning` | (If present) the LLM re-judge's reasoning |

---

## **Resuming after interruption**

If the training machine crashes, the network drops, or you Ctrl-C the run, **do not start a new evaluation** — use `--resume` to continue on the original results file:

```bash
# Automatically uses the latest results under workdir/hle/results/hle/
python examples/run_hle.py --resume

# Or specify explicitly
python examples/run_hle.py --resume workdir/hle/results/hle/benchmark_hle_<timestamp>.json
```

Behavior:

- The framework reads the set of completed `task_id`s from the results file and **skips them**, running only the remaining unfinished ones;
- The resumed run writes to a **new** `benchmark_hle_<new timestamp>.json` (completed questions are migrated in as a preload, so this new file is the final "complete 2500-question" product; the old file can be kept or deleted);
- Other CLI arguments (`--max-concurrency`, etc.) still apply.

---

## **After a full run: re-running "empty / unfinished" questions**

Over 2500 questions, a few will inevitably leave `predicted_answer` empty due to bus timeout, planner stalls, or transient API failures. The framework provides a dedicated filter mode `--filter null` to re-run only these "empty" questions:

```bash
python examples/run_hle.py \
    --resume workdir/hle/results/hle/benchmark_hle_<timestamp>.json \
    --filter null \
    --max-concurrency 16
```

- `--filter null` rule (in examples/run_hle.py:677-690): a question is re-run if **any** of the following holds, otherwise it is skipped and its original result kept —
- `predicted_answer` is an empty string
- `predicted_answer` contains `"planner did not finish"`
- `predicted_answer` contains `"planner returned no decision"`
- `predicted_answer` contains `"unable to determine"`

Other `--filter` values (as needed):

| Value | Meaning |
| --- | --- |
| `null` | Re-run only empty / unfinished-planner questions (**recommended for back-filling**) |
| `wrong` | Re-run all `correct=False` questions (i.e. all wrong ones; use with care — re-runs a large fraction) |
| `none` | (Default) only run task_ids never seen in the results |

> ⚠️ `--filter` must be used together with `--resume`; passing it alone is ignored with a warning.
>

### **Re-judge with LLM only (without regenerating answers)**

If you only suspect the judging strictness is off (e.g. exact-match treats `1/2` and `0.5` as different), you can skip regenerating answers and just have the LLM re-judge all wrong questions:

```bash
python examples/run_hle.py \
    --eval-only \
    --resume workdir/hle/results/hle/benchmark_hle_<timestamp>.json \
    --eval-model-name openrouter/o3-mini
```

This updates the `correct` field of the resume file **in place** and back-fills `llm_eval_reasoning`, then re-prints stats.

---

## **FAQ**

| Symptom | Troubleshooting |
| --- | --- |
| Startup raises `vault.exceptions.InvalidRequest` or can't read a key | Check `.env`'s `VAULT_TOKEN` / `VAULT_ADDR`, and whether `vault status` is unsealed |
| Startup can't find a model / 401 | Usually `INT_OPENROUTER_API_KEY` is missing (not listed in INSTALL.md — easiest to overlook) |
| Log shows `the opencode CLI tool was not found` | `opencode` is not on PATH — reinstall per INSTALL.md section 2 |
| opencode subprocess reports newapi 401 / model not found | `NEWAPI_API_KEY` missing, or `model` in opencode.json was changed to an unavailable one |
| deep_researcher reports Firecrawl network / 401 | `FIRECRAWL_API_KEY` missing, or quota exhausted |
| `--resume: no previous results found` | `workdir/hle/results/hle/` doesn't exist or is empty — confirm a previous run happened |
| Many questions have empty `predicted_answer` | Usually bus timeout (5400s) or early planner exit; first `--filter null` to back-fill; if still empty, check the traceback for that task_id in `hle.log` |
| Frequent `Eval timeout` | Network issues with the judge model (`--eval-model-name`) — retry or switch models; does not affect the final raw answer |

---

## **Key file locations**

| Path | Description |
| --- | --- |
| examples/run_hle.py | Evaluation entry |
| configs/hle.py | HLE default config (models, agents, etc.) |
| src/benchmark/hle.py | HLE dataset loading & judging logic |
| `datasets/hle/` | Raw HLE data (obtained via git clone) |
| `workdir/hle/results/hle/benchmark_hle_*.json` | Results files (evaluation artifacts) |
| `workdir/hle/hle.log` | Full text log |
| `workdir/hle/results/hle/viewer.html` | Visualization page (auto-generated after results) |
