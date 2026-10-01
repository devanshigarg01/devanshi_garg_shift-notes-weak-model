# Ablation

| component | 1x with | 1x without | 3x with | 3x without | 10x with | 10x without |
|---|---|---|---|---|---|---|
| **full system** | 78.3 (1.0 / 1 calls) |  | 81.7 (3.0 / 3 calls) |  | 85.0 (4.4 / 10 calls) |
| *One read of the notes* | | | | | | |
| Code solver (vs Granite answering directly) | 78.3 | 0.0 | 81.7 | -- | 85.0 | -- |
| Worked examples in the prompt | 78.3 | 48.3 | 81.7 | 51.7 | 85.0 | 66.7 |
| Restate each line in plain words first | 78.3 | 63.3 | 81.7 | 68.3 | 85.0 | 70.0 |
| *Code repair (no model calls)* | | | | | | |
| Wrong-name repair | 78.3 | 70.0 | 81.7 | 76.7 | 85.0 | 80.0 |
| *Extra calls, 3x: read three times and vote* | | | | | | |
| 2nd and 3rd read + per-line vote (vs one read) | -- | -- | 81.7 | 78.3 | 85.0 | 86.7 |
| Reads use different prompts (vs one prompt x3) | -- | -- | 81.7 | 80.0 | 85.0 | 85.0 |
| *Extra calls, 10x: re-ask only the lines code flags* | | | | | | |
| All targeted re-asks (vs the 3-read vote alone) | -- | -- | -- | -- | 85.0 | 81.7 |
| *each kind of re-ask removed on its own:* | | | | | | |
| name check -- value not in its line | -- | -- | -- | -- | 85.0 | 86.7 |
| missed line -- "no fact" but names people | -- | -- | -- | -- | 85.0 | 80.0 |
| order -- who comes first | -- | -- | -- | -- | 85.0 | 85.0 |
| line type -- kind of statement | -- | -- | -- | -- | 85.0 | 85.0 |
| contradiction -- lines cannot both hold | -- | -- | -- | -- | 85.0 | 85.0 |

## Prompt techniques at 1x

|  | Unique | Ambiguous | Inconsistent | **Macro** | Calls/item | Lines right | Missed | Wrong |
|---|---|---|---|---|---|---|---|---|
| ★ Few-shot: 12 worked examples, restate each line | 80.0 | 75.0 | 80.0 | 78.3 | 1.0 | 94.5 | 46 | 17 |
| One-shot: 1 worked example | 50.0 | 45.0 | 65.0 | 53.3 | 1.0 | 91.6 | 67 | 28 |
| Zero-shot: rules and hints only | 50.0 | 40.0 | 55.0 | 48.3 | 1.0 | 90.3 | 76 | 34 |
| No plain-words restatement | 65.0 | 60.0 | 65.0 | 63.3 | 1.0 | 94.1 | 55 | 12 |
| Plain field names (a/b instead of earlier/later) | 80.0 | 70.0 | 80.0 | 76.7 | 1.0 | 94.7 | 37 | 23 |
| Granite answers the whole item directly (no solver) | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 | -- | -- | -- |

## Code repairs at 1x (same model output)

|  | Unique | Ambiguous | Inconsistent | **Macro** | Calls/item | Lines right | Missed | Wrong |
|---|---|---|---|---|---|---|---|---|
| None | 70.0 | 75.0 | 65.0 | 70.0 | 1.0 | 93.7 | 45 | 27 |
| ★ Wrong-name repair | 80.0 | 75.0 | 80.0 | 78.3 | 1.0 | 94.5 | 46 | 17 |
| Station/time swap repair | 70.0 | 75.0 | 65.0 | 70.0 | 1.0 | 93.7 | 45 | 27 |
| Both | 80.0 | 75.0 | 80.0 | 78.3 | 1.0 | 94.5 | 46 | 17 |

## Ways to spend three calls

|  | Unique | Ambiguous | Inconsistent | **Macro** | Calls/item | Lines right | Missed | Wrong |
|---|---|---|---|---|---|---|---|---|
| 1 read (the 1x system) | 80.0 | 75.0 | 80.0 | 78.3 | 1.0 | 94.5 | 46 | 17 |
| 2 reads + 1 re-ask of the lines they disagree on | 90.0 | 80.0 | 85.0 | 85.0 | 2.7 | 96.0 | 32 | 14 |
| 3 reads, same prompt, per-line vote | 85.0 | 70.0 | 85.0 | 80.0 | 3.0 | 95.2 | 38 | 17 |
| ★ 3 reads, 3 different prompts, per-line vote | 85.0 | 75.0 | 85.0 | 81.7 | 3.0 | 95.5 | 41 | 10 |

## Where the 10x calls go

|  | Items asked | Lines re-asked | Macro without it | Gain |
|---|---|---|---|---|
| 3-read vote (the 3x system) | -- | -- | 81.7 | -- |
| + name check (a name, time or station the line does not mention) | 40% | 36 | 86.7 | -1.7 |
| + missed line (read as no fact, but the restatement names people) | 45% | 39 | 80.0 | +5.0 |
| + order (reads disagree on who comes first) | 20% | 13 | 85.0 | +0.0 |
| + line type (reads disagree on the kind of statement) | 37% | 38 | 85.0 | +0.0 |
| + contradiction (two lines cannot both hold) | 2% | 3 | 85.0 | +0.0 |
| ★ Full 10x | -- | -- | 85.0 | -- |

## Declared versus true kind

| True kind → | 1x U | A | I | 3x U | A | I | 10x U | A | I |
|---|---|---|---|---|---|---|---|---|---|
| Declared unique | 16 | 0 | 1 | 17 | 0 | 1 | 18 | 0 | 1 |
| Declared ambiguous | 4 | 18 | 1 | 3 | 19 | 1 | 1 | 19 | 0 |
| Declared inconsistent | 0 | 2 | 18 | 0 | 1 | 18 | 1 | 1 | 19 |

1 runs per system, 60 items. Command: python3 ablate.py --suite full --runs 1 --workers 16. Started 2026-10-01 23:19, took 21.3 min.
