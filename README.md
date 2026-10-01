# Shift notes with a weak model

Granite (`ibm-granite/granite-4.2-8b`) reads the notes; code does the rest. The model turns each
line into one structured fact (or `none`); a solver enumerates all 720 possible rotas, keeps the
ones every fact allows, and answers unique / ambiguous / inconsistent (with a minimal conflicting
set of statements). Design, measurements and failure modes: `docs/writeup.docx`.

## Setup

Python 3.10 or newer.

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in the endpoint and key
```

## Run

```bash
./run <items.json> --budget <1x|3x|10x> --out <answers.json>
```

| Budget | What it does | Model calls per item |
|---|---|---|
| `1x` | one read of the notes → wrong-name repair → solver | exactly 1 |
| `3x` | three reads with three differently formatted prompts → per-line majority vote → repair → solver | 3 |
| `10x` | the 3x system, then one targeted re-ask per kind of problem code finds (name not in the line, missed line, order, line type, contradiction) | 3 to 8 |

Every call carries the `X-Item-Id` header and goes to the endpoint in `.env` with
`temperature=1.0`, `top_p=0.95`, reasoning disabled. Nothing is cached: every answer comes from
live model output. `--workers` (default 8) sets how many items run in parallel.

A rate-limited request (HTTP 429) is retried up to 5 times with backoff (`RETRIES_429` in
`extract.py`); no other error is retried, and the OpenAI client's own retries are off. If a
counting proxy counts rejected requests as calls, lower `--workers` rather than relying on retries.

## Ablation

```bash
python3 ablate.py              # every system, 3 runs each, all items
python3 ablate.py --runs 1 --limit 10    # quick check
```

Needs `items.json`, `visible_key.json` and `score.py` from the task package in this folder
(or `--items`, `--key`, `--scorer`). It runs 23 systems (the submitted system at each budget,
and each one with a single component removed or swapped), scores them with `score.py`, and writes
`ablation/<time>/ablation.md` and `ablation.tex` (plus `ablation.pdf` if `pdflatex` is installed):
the with/without table for every component at every budget, and supporting tables (prompt
techniques, code repairs, ways to spend three calls, where the 10x calls go, declared vs true
kind). `python3 ablate.py --report ablation/<time>` rebuilds the document without calls.

`reference_facts.json` is the correct fact for every line of the visible set; the ablation uses
it only to count lines read right, missed and wrong. It is never used to answer an item.

## Files

| File | Role |
|---|---|
| `run`, `run.py` | entrypoint |
| `extract.py` | header parsing (code), the prompts, the model call, reply → facts |
| `multi.py` | 3x and 10x: extra reads, voting, targeted re-asks |
| `repair.py` | code checks on facts; the wrong-name repair |
| `solver.py` | enumerates rotas, decides the case, finds a minimal conflict set, validates the answer |
| `ablate.py`, `report.py` | the ablation and its document |
