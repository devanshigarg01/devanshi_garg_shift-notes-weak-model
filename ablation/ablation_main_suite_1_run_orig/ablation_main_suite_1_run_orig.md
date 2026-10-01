# Ablation

| component | 1x with | 1x without | 3x with | 3x without | 10x with | 10x without |
|---|---|---|---|---|---|---|
| **full system** | 85.0 (1.0 / 1 calls) |  | 85.0 (3.0 / 3 calls) |  | 86.7 (4.4 / 10 calls) |
| *One read of the notes* | | | | | | |
| Code solver (vs Granite answering directly) | 85.0 | 1.7 | 85.0 | -- | 86.7 | -- |
| Worked examples in the prompt | 85.0 | 51.7 | 85.0 | -- | 86.7 | -- |
| Restate each line in plain words first | 85.0 | 61.7 | 85.0 | -- | 86.7 | -- |
| *Code repair (no model calls)* | | | | | | |
| Wrong-name repair | 85.0 | 78.3 | 85.0 | 80.0 | 86.7 | 80.0 |
| *Extra calls, 3x: read three times and vote* | | | | | | |
| 2nd and 3rd read + per-line vote (vs one read) | -- | -- | 85.0 | 85.0 | 86.7 | 85.0 |
| Reads use different prompts (vs one prompt x3) | -- | -- | 85.0 | 80.0 | 86.7 | 83.3 |
| *Extra calls, 10x: re-ask only the lines code flags* | | | | | | |
| All targeted re-asks (vs the 3-read vote alone) | -- | -- | -- | -- | 86.7 | 85.0 |
| *each kind of re-ask removed on its own:* | | | | | | |
| name check -- value not in its line | -- | -- | -- | -- | 86.7 | 85.0 |
| missed line -- "no fact" but names people | -- | -- | -- | -- | 86.7 | 86.7 |
| order -- who comes first | -- | -- | -- | -- | 86.7 | 86.7 |
| line type -- kind of statement | -- | -- | -- | -- | 86.7 | 86.7 |
| contradiction -- lines cannot both hold | -- | -- | -- | -- | 86.7 | 86.7 |

## Code repairs at 1x (same model output)

|  | Unique | Ambiguous | Inconsistent | **Macro** | Calls/item | Lines right | Missed | Wrong |
|---|---|---|---|---|---|---|---|---|
| None | 80.0 | 70.0 | 85.0 | 78.3 | 1.0 | 95.0 | 31 | 26 |
| ★ Wrong-name repair | 85.0 | 75.0 | 95.0 | 85.0 | 1.0 | 95.5 | 35 | 16 |
| Station/time swap repair | 80.0 | 70.0 | 85.0 | 78.3 | 1.0 | 95.0 | 31 | 26 |
| Both | 85.0 | 75.0 | 95.0 | 85.0 | 1.0 | 95.5 | 35 | 16 |

## Where the 10x calls go

|  | Items asked | Lines re-asked | Macro without it | Gain |
|---|---|---|---|---|
| 3-read vote (the 3x system) | -- | -- | 85.0 | -- |
| + name check (a name, time or station the line does not mention) | 40% | 37 | 85.0 | +1.7 |
| + missed line (read as no fact, but the restatement names people) | 33% | 27 | 86.7 | +0.0 |
| + order (reads disagree on who comes first) | 17% | 13 | 86.7 | +0.0 |
| + line type (reads disagree on the kind of statement) | 50% | 47 | 86.7 | +0.0 |
| + contradiction (two lines cannot both hold) | 3% | 5 | 86.7 | +0.0 |
| ★ Full 10x | -- | -- | 86.7 | -- |

## Declared versus true kind

| True kind → | 1x U | A | I | 3x U | A | I | 10x U | A | I |
|---|---|---|---|---|---|---|---|---|---|
| Declared unique | 17 | 0 | 0 | 18 | 0 | 1 | 18 | 0 | 1 |
| Declared ambiguous | 1 | 18 | 1 | 1 | 18 | 1 | 1 | 17 | 1 |
| Declared inconsistent | 2 | 2 | 19 | 1 | 2 | 18 | 1 | 3 | 18 |

1 runs per system, 60 items. Command: python3 ablate.py --suite main --runs 1 --workers 16. Started 2026-10-01 22:55, took 12.6 min.
