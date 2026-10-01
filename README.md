# Shift notes with a weak model

Granite reads each line into a structured fact; a solver checks all 720 possible rotas and
answers unique / ambiguous / inconsistent. Details: `docs/devanshi_garg_write_up.docx`.

## Where things are

| What | Where |
|---|---|
| Write-up (2 pages) | `docs/devanshi_garg_write_up.docx` |
| Reported ablation results (my runs) | `ablation/ablation_<suite>_suite_<N>_run(s)_orig.md` (each with `.tex`, `.pdf`, `.json`). A re-run writes the same names without `_orig`, so it never overwrites these. |
| Any ablation run | `ablation/ablation_<quick\|main\|full>_suite_<N>_runs.md` / `.tex` / `.pdf`, plus `.json` (the scores behind the tables) |

## Setup

Python 3.9.6 (tested).

```bash
pip install -r requirements.txt
cp .env.example .env        # endpoint and key
```

## Run

```bash
./run <items.json> --budget <1x|3x|10x> --out <answers.json>
```

| Budget | System | Calls per item |
|---|---|---|
| `1x` | one read → wrong-name repair → solver | 1 |
| `3x` | three reads (different prompts) → per-line vote → repair → solver | 3 |
| `10x` | 3x + one targeted re-ask per kind of problem code flags | 3–8 |

No caching. Rate-limited calls (429) are retried up to 5 times and parallelism backs off; nothing else is retried.

## Ablation

The task files (`items.json`, `visible_key.json`, `score.py`) are included. Pick one:

| Command | What you get | Approx. time |
|---|---|---|
| `python3 ablate.py --runs 3` | **quick**: the full system at each budget, plus the solver (vs Granite answering directly), the wrong-name repair, the 3-read vote and the 10x re-asks with/without (~360 calls/run) | ~30 min |
| `python3 ablate.py --suite main --runs 1` | **main**: the whole main table, prompt components at 1x only (~1,000 calls) | ~35 min |
| `python3 ablate.py --suite full --runs 1` | **full**: every component at every budget + supporting tables (~1,500 calls) | ~50 min |

Times assume the endpoint's rate limit (~0.5 calls/s). `--workers` (default 8) is a ceiling on
parallel calls: each rate limit halves it, a streak of successes adds one back.

What each suite runs:

| Suite | Systems | Rows of the main table it fills |
|---|---|---|
| quick | `1x`, `1x-direct`, `3x`, `3x-norepair`, `10x` | full system at every budget; solver vs Granite answering directly (1x); wrong-name repair at 1x/3x; 3-read vote vs one read; all 10x re-asks vs the vote alone. Other rows show `--`. |
| main | 16: quick + `1x-zeroshot`, `1x-norestate`, `3x-sameprompt`, `10x-norepair`, `10x-sameprompt`, `10x-oneread`, and each 10x re-ask removed on its own | every row; worked examples and restating at 1x only |
| full | all 23: main + `1x-oneshot`, `1x-plainfields`, `3x-zeroshot`, `3x-norestate`, `3x-followup`, `10x-zeroshot`, `10x-norestate` | every row at every budget, plus supporting tables (prompt techniques, ways to spend 3 calls, where 10x calls go) |

My results (`_orig` files) come from: quick × 3 runs, main × 1, full × 1.

Output: `ablation/ablation_<suite>_suite_<N>_runs.md` and `.tex` (and `.pdf` if `pdflatex` is
installed), named by suite and number of runs; a re-run with the same settings overwrites them.
Within a run, systems share identical reads, so those that differ only after the reads are
compared on the same model output.

- Quick check: `python3 ablate.py --runs 1 --limit 10`
- Rebuild tables without calls: `python3 ablate.py --report ablation/<name>.json`

`reference_facts.json` (correct fact per visible line) is used only to count lines read right,
missed and wrong; never to answer.

## Files

| File | Role |
|---|---|
| `run`, `run.py` | entrypoint |
| `extract.py` | header parsing, prompts, model call |
| `multi.py` | 3x / 10x: extra reads, vote, re-asks |
| `repair.py` | checks and wrong-name repair |
| `solver.py` | rota search, case, conflict set |
| `ablate.py`, `report.py` | ablation and tables |
