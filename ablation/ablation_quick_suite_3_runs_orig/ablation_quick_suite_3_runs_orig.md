# Ablation

| component | 1x with | 1x without | 3x with | 3x without | 10x with | 10x without |
|---|---|---|---|---|---|---|
| **full system** | 78.3 ± 7.6 (1.0 / 1 calls) |  | 83.3 ± 3.3 (3.0 / 3 calls) |  | 87.8 ± 1.0 (4.5 / 10 calls) |
| *One read of the notes* | | | | | | |
| Code solver (vs Granite answering directly) | 78.3 | 0.0 | 83.3 | -- | 87.8 | -- |
| Worked examples in the prompt | 78.3 | -- | 83.3 | -- | 87.8 | -- |
| Restate each line in plain words first | 78.3 | -- | 83.3 | -- | 87.8 | -- |
| *Code repair (no model calls)* | | | | | | |
| Wrong-name repair | 78.3 | 72.2 | 83.3 | 79.4 | 87.8 | -- |
| *Extra calls, 3x: read three times and vote* | | | | | | |
| 2nd and 3rd read + per-line vote (vs one read) | -- | -- | 83.3 | 78.3 | 87.8 | -- |
| Reads use different prompts (vs one prompt x3) | -- | -- | 83.3 | -- | 87.8 | -- |
| *Extra calls, 10x: re-ask only the lines code flags* | | | | | | |
| All targeted re-asks (vs the 3-read vote alone) | -- | -- | -- | -- | 87.8 | 83.3 |
| *each kind of re-ask removed on its own:* | | | | | | |
| name check -- value not in its line | -- | -- | -- | -- | 87.8 | -- |
| missed line -- "no fact" but names people | -- | -- | -- | -- | 87.8 | -- |
| order -- who comes first | -- | -- | -- | -- | 87.8 | -- |
| line type -- kind of statement | -- | -- | -- | -- | 87.8 | -- |
| contradiction -- lines cannot both hold | -- | -- | -- | -- | 87.8 | -- |

## Code repairs at 1x (same model output)

|  | Unique | Ambiguous | Inconsistent | **Macro** | Calls/item | Lines right | Missed | Wrong |
|---|---|---|---|---|---|---|---|---|
| None | 78.3 | 70.0 | 68.3 | 72.2 ± 5.1 | 1.0 | 95.1 | 26 | 29 |
| ★ Wrong-name repair | 83.3 | 71.7 | 80.0 | 78.3 ± 7.6 | 1.0 | 95.7 | 29 | 19 |
| Station/time swap repair | 78.3 | 70.0 | 68.3 | 72.2 ± 5.1 | 1.0 | 95.1 | 26 | 29 |
| Both | 83.3 | 71.7 | 80.0 | 78.3 ± 7.6 | 1.0 | 95.7 | 29 | 19 |

## Declared versus true kind

| True kind → | 1x U | A | I | 3x U | A | I | 10x U | A | I |
|---|---|---|---|---|---|---|---|---|---|
| Declared unique | 16.7 | 0.7 | 1 | 17.3 | 0 | 0.3 | 18.7 | 0 | 0 |
| Declared ambiguous | 2.3 | 17.7 | 1 | 2.3 | 19 | 1.3 | 1.3 | 18.7 | 1.3 |
| Declared inconsistent | 1 | 1.7 | 18 | 0.3 | 1 | 18.3 | 0 | 1.3 | 18.7 |

3 runs per system, 60 items. Command: python3 ablate.py --runs 3. Started 2026-10-01 21:52, took 33.2 min.
