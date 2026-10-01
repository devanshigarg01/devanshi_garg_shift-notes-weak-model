# Shift notes with a weak model

Granite reads each line into a structured fact; a solver checks all 720 possible rotas and
answers unique / ambiguous / inconsistent. Details: `docs/writeup.docx`.

## Setup

Python 3.10+.

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

Put `items.json`, `visible_key.json` and `score.py` from the task package in this folder, then:

```bash
python3 ablate.py --runs 3
```

Runs 23 systems (each budget's system, and each with one component removed or swapped) and
writes the tables to `ablation/<time>/ablation.md` (and `.tex`/`.pdf`). Within a run, systems
share identical reads, so those that differ only after the reads are compared on the same model
output; ~1,500 calls per run (~25 min with `--workers 16`). Reported results: `docs/ablation.md`.

- Quick check: `python3 ablate.py --runs 1 --limit 10`
- Rebuild tables without calls: `python3 ablate.py --report ablation/<time>`

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
