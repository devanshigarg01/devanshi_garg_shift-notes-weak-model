# Shift notes with a weak model

Granite reads each line into a structured fact; a solver checks all 720 possible rotas and
answers unique / ambiguous / inconsistent. Details: `docs/devanshi_garg_write_up.docx`.

## Where things are

| What | Where |
|---|---|
| Write-up (2 pages) | `docs/devanshi_garg_write_up.docx` |
| Reported ablation results | `ablation/ablation_full_suite_3_runs.md` (and `.tex`, `.pdf`) |
| Any ablation run | `ablation/ablation_<main\|full>_suite_<N>_runs.md` / `.tex` / `.pdf`, plus `.json` (the scores behind the tables) |

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

No caching. Rate-limited calls (429) are retried up to 5 times; nothing else is retried.

## Ablation

The task files (`items.json`, `visible_key.json`, `score.py`) are included. Pick one:

| Command | What you get | Calls per run | Time for 3 runs (`--workers 16`) |
|---|---|---|---|
| `python3 ablate.py --runs 3 --workers 16` | main table: every component with/without at each budget (prompt components at 1x only) | ~1,000 | ~40 min |
| `python3 ablate.py --suite full --runs 3 --workers 16` | everything: main table at every budget + supporting tables (prompt techniques, repairs, ways to spend 3 calls, where 10x calls go, declared vs true kind) | ~1,600 | ~75 min |

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
