# Shift notes with a weak model

**Devanshi Garg** · `ibm-granite/granite-4.2-8b` · budgets 1× / 3× / 10×

Granite reads each line of the notes into a structured fact; a solver checks all 720 possible rotas and
answers unique / ambiguous / inconsistent. Macro exact match on the visible set, mean of 5 runs:
**1× 79.7 · 3× 83.3 · 10× 87.0** (Granite answering directly: 0.3).

**Contents:** [Write-up](#write-up) (sections 1–9) · [Appendix: detailed ablation](#appendix-detailed-ablation) ·
[Running the system and reproducing the ablation](#running-the-system-and-reproducing-the-ablation)

```bash
pip install -r requirements.txt && cp .env.example .env      # then add the endpoint and key
./run items.json --budget 1x --out answers.json
python3 ablate.py --runs 1                                    # quick ablation check, ~5 min
```

---

## Write-up

### 1. First principles: reading is the model's job, arranging is not

Every item is two problems stacked.

1.  **Understand** the notes: which lines are constraints, and what each one says.

2.  **Arrange:** find every assignment of people to blocks and holders to stations that satisfies them all, and decide unique, ambiguous or inconsistent.

An 8B model is poor at the second part: it has to hold five people, five blocks and three stations in its head and check every constraint against every candidate. Without a harness Granite scores **0.0%** (my own direct-answer baseline: 0.3% over 5 runs).

But the search space is tiny: 5! block orders × 3! station assignments = **720 possible rotas**. A solver tries all of them in milliseconds, counts the survivors (1 = unique, 2+ = ambiguous, 0 = inconsistent), and for inconsistent items finds a minimal conflicting set by dropping lines one at a time. Given correct facts, the solver scores **100%** on all 60 items. So every point lost is a *reading* error, and the whole design is about making a weak model read well.

The model's only job is to turn each line into one structured fact, or none:

- *"Speaking from memory, Ayesha sits between Meera and Nadia on the rota."* → between(middle=Ayesha, ends=\[Meera, Nadia\])

- *"Calibration is covered by someone other than Samuel."* → not_on(Samuel, calibration)

- *"Priya put in for the 15:00 block and was turned down."* → none (a failed request says nothing about the rota)

Seven relations cover the set (at, not_at, on, not_on, before, immediately_before, between), and a station name can stand for "whoever holds it". **Extraction is model-based; everything after it (checks, repair, solving, the conflict set) is symbolic.**

### 2. Characterising Granite before designing around it

I built a reference set with the correct fact for every line of the 60 items (drafted with Claude; the solver scores 100% on it), then compared Granite's output line by line instead of only looking at the final score.

- **It doesn't hallucinate on chatter.** 596 of 600 filler lines came back as none. It is conservative, not creative.

- **It misses real constraints.** With a plain prompt it returned none for about 40% of real constraint lines. Misses, not wrong facts, are the dominant error at every budget.

- **Its misreads are specific:** a name drifting to someone not in the line; before flipped (*"Tomas works earlier in the day than Nadia"* → Nadia first); the wrong person as the middle of a between; a station written into a time field.

- **Errors compound.** There's no partial credit: one wrong line removes the true rota and usually flips the item to inconsistent; one missed line adds solutions and turns unique into ambiguous.

- **Its mistakes repeat.** A second read with the same prompt disagreed with only 35% of the first read's errors; a differently formatted prompt disagreed with 59%. This one fact shaped the whole 3×/10× design.

### 3. 1×: one read, a code repair, the solver

Code parses the header (names, blocks, stations, holders) so the model never has to, and Granite gives exactly one entry per numbered line. Each prompt component exists because of a measured failure:

- **Hints on what isn't a fact** (requests, the past, hypotheticals, chatter), and that hedges like *"I'm fairly sure"* carry no meaning.

- **Worked examples** in made-up names, chosen for the patterns it got wrong (between, direction words, a station as the subject). The biggest single component: without them 1× falls from 78 to 48–50, and a single example gives only 53.

- **Restate the line first:** a say field in plain words (*"Carla somewhere between Alice and Dan"*) before the structured fields, with field names that carry meaning (earlier/later instead of a/b). Most direction flips were the model understanding the line and then filling the wrong slot; this makes it commit to the meaning first. Without it 1× drops to 63 and missed lines rise from ~40 to ~55 per run.

- **Wrong-name repair** (code, no call): if a fact names someone its own line never mentions, swap in the one unused name the line does mention, else drop the fact. It fires about 10 times per run: +8 at 1× on the same model output (70 → 78), +4 at 3×, +7 at 10×.

1× macro exact match: **79.7** over 5 runs (70–85: a single read at temperature 1.0 is noisy).

### 4. 3×: fix by agreement, not self-correction

I tried three ways to spend three calls:

- **Two reads + one call** that re-reads the lines where they disagree or a check fires. It recovers more missed lines than voting (32 vs 41 per run) but makes more wrong ones (14 vs 10). It scored 85.0 vs 81.7 in one run and 81.1 vs 83.9 in an earlier 3-run comparison, so on this data neither wins.

- **Three reads with the same prompt + per-line vote: 80.0**, because the reads share their mistakes.

- **Three reads with three differently formatted prompts + per-line vote: 83.3** (±2.6 over 5 runs, against ±6.2 at 1×); invented facts fall from 7 to 3 per run. I kept this one: it never lets a single fresh sample overwrite a reading, and 10× already re-asks flagged lines.

### 5. 10×: code decides which lines need another look

On top of the 3× vote, code flags lines and groups them by kind of problem, with one specialised call per kind: **name check** (a value not in the line), **missed line** (none, but the model's own restatement names people), **order** (reads agree on the relation but not on who is first), **line type** (reads disagree on the relation) and **contradiction** (two lines can't both hold). Each call shows only those lines, the earlier readings and what is wrong. A re-read replaces the vote only if code found a concrete problem and the new answer passes the checks; otherwise it is one more vote. My first version let any re-read overwrite, and 10× fell below its own vote (85 → 80 on a 20-item run). Final 10×: **87.0** (±1.4 over 5 runs) at 4.5 calls per item on average; 10 is a cap, not a target. The re-asks beat the vote in all 5 runs (+3.7 on average); once they exist, one read does as well as three (85.8 vs 85.8).

### 6. Built, then removed

- **Minimal-edit repair:** when two lines contradict on their own, apply the cheapest edit (flip an order, change a middle, flip a negation) that resolves it. +1.6 at 1× (measured before removal), inside run-to-run noise, and when it's wrong it is confidently wrong: on item B2-018 the real error was *"Tomas wanted 09:00 and did not get it"* read as not_at, and it "fixed" a correct line instead.

- **Station/time swap repair:** fired less than once per run and changed no score. Removed.

- **Strict JSON schema:** Granite can't hold a rigid output contract; it collapsed (empty or truncated output) on 6 of 60 items. Plain JSON mode kept.

### 7. Where it breaks

- **Missed constraints are what's left.** At 10×: 29 missed lines per run against 8 wrong and 3 invented. Plain sentences get missed too (*"Rohan sits between Daniel and Tomas on the rota."* → none), and voting slightly worsens misses, because two reads saying none outvote the one that found the fact.

- **Hedged and restated lines** (*"Speaking from memory, …"*, *"Noted twice in the handover: …"*) are missed 2–3× as often as plain ones (9% vs 3–4%). Granite reads a hedge as uncertainty despite the hint.

- **Failed requests become facts:** *"Ingrid put in for the 07:00 block and was turned down"* → not_at(Ingrid, 07:00), about 4 per run. Voting can't fix it because every prompt shares the bias.

- **Ambiguous is the weakest kind** (80% at 10×, against 92% unique and 89% inconsistent): it needs every constraint, and a single miss adds a solution.

- **Held-out will be lower.** I tuned on these 60 items: the examples use made-up names, but they and the hints were written from errors I saw here.

### 8. Model-based or symbolic, and notes written by real people

Extraction is model-based; checking and solving are symbolic. The checks use only each item's own vocabulary (the names, times and stations in its header), never English patterns, so they don't depend on phrasing. That's the part I'd trust on human notes: a person might write *"Dan's on after Bea, no gap"* in a form no template has, and a model will still read it where a regex wouldn't. What would break:

- **One fact per line.** People put two constraints in one sentence and refer back (*"she"*, *"the new starter"*). The wrong-name repair assumes every line names its people.

- **The guarantees I lean on** (at most 4 solutions, conflicts of 3+ lines, seven relations) belong to the generator, not to real rotas. Real notes say *"after lunch"* or *"not two nights running"*. The solver can take more relations easily; the schema and prompt would have to grow with them.

- **Real conflicts aren't planted.** Two people can genuinely disagree, and "inconsistent" stops being a clean answer.

### 9. With a month

- **Review every line of code and prompt myself.** Much of this was written with Claude in a few hours; each piece should be understood, not just pass the ablation.

- **A solver that scales, and a softer one.** Brute force works only because there are 720 rotas; 10 people and 10 blocks is ~3.6M orders. I'd move the same facts into a constraint solver (OR-Tools CP-SAT or Z3): count solutions up to 5, and take the conflict set from the unsatisfiable core, shrunk to minimal. Extraction would not change. With a confidence per fact (vote share or log-probabilities), a weighted version (MaxSAT) could also stop one wrong fact from making the notes inconsistent.

- **Understand 10× before adding to it.** It is a vote plus five kinds of re-ask, and the data cannot yet say which re-ask earns its call. I'd ablate each with enough runs, then move calls to where the errors are (29 missed lines per run vs 11 wrong or invented): re-read lines voted "none" that still name two people, and stop early on items whose reads agree.

- **Entity resolution as its own step:** tag each mention with its header name before extraction. Here it would only prevent the name drift the wrong-name repair patches (~10 facts per run); on people's notes ("she", "the new starter", nicknames) it becomes essential.

- **Less noise, real notes:** 5+ runs per component (most have 1–2) and a test on 20 notes written by people.

---

## Appendix: detailed ablation

All numbers are macro exact match (%) on the 60 visible items; one item moves a score by 1.7 points. Reproduce with `python3 ablate.py` (see the README): the files behind these tables are in `ablation/` in the repo (`*_orig` folders). The headline uses every run of the submitted systems (3 runs of the quick suite, 1 each of main and full); the component tables come from the full suite (1 run), so gaps under ~4 points there are within run-to-run noise.

### A. Headline: the submitted system at each budget (5 runs)

| **System**                           | **Runs** | **Macro (mean ± s.d.)** | **Range**   | **Unique** | **Ambiguous** | **Inconsistent** | **Calls/item** |
|--------------------------------------|----------|-------------------------|-------------|------------|---------------|------------------|----------------|
| Granite answers directly (no solver) | 5        | 0.3 ± 0.7               | 0.0 – 1.7   | 1          | 0             | 0                | 1              |
| 1x: one read → repair → solver       | 5        | 79.7 ± 6.2              | 70.0 – 85.0 | 83         | 73            | 83               | 1              |
| 3x: three reads, vote                | 5        | 83.3 ± 2.6              | 80.0 – 86.7 | 87         | 78            | 85               | 3              |
| 10x: vote + targeted re-asks         | 5        | 87.0 ± 1.4              | 85.0 – 88.3 | 92         | 80            | 89               | 4.5            |

The curve rises at every budget (79.7 → 83.3 → 87.0) and the spread shrinks (±6.2 → ±2.6 → ±1.4): extra calls buy stability as well as accuracy. Without the solver, the same single call scores 0.3.

### B. Every component with and without, at each budget (full suite, 1 run)

| **Component**                                          | **1x with** | **1x without** | **3x with** | **3x without** | **10x with** | **10x without** |
|--------------------------------------------------------|-------------|----------------|-------------|----------------|--------------|-----------------|
| Full system                                            | 78.3        |                | 81.7        |                | 85.0         |                 |
| Calls per item (mean / cap)                            | 1.0 / 1     |                | 3.0 / 3     |                | 4.4 / 10     |                 |
| **One read of the notes**                              |             |                |             |                |              |                 |
| Code solver (vs Granite answering directly)            | 78.3        | 0.0            | 81.7        | --             | 85.0         | --              |
| Worked examples in the prompt                          | 78.3        | 48.3           | 81.7        | 51.7           | 85.0         | 66.7            |
| Restate each line in plain words first                 | 78.3        | 63.3           | 81.7        | 68.3           | 85.0         | 70.0            |
| **Code repair (no model calls)**                       |             |                |             |                |              |                 |
| Wrong-name repair                                      | 78.3        | 70.0           | 81.7        | 76.7           | 85.0         | 80.0            |
| **Extra calls, 3x: read three times and vote**         |             |                |             |                |              |                 |
| 2nd and 3rd read + per-line vote (vs one read)         | --          | --             | 81.7        | 78.3           | 85.0         | 86.7            |
| Reads use different prompts (vs one prompt x3)         | --          | --             | 81.7        | 80.0           | 85.0         | 85.0            |
| **Extra calls, 10x: re-ask only the lines code flags** |             |                |             |                |              |                 |
| All targeted re-asks (vs the 3-read vote alone)        | --          | --             | --          | --             | 85.0         | 81.7            |
| *each kind of re-ask removed on its own:*              |             |                |             |                |              |                 |
| name check -- value not in its line                    | --          | --             | --          | --             | 85.0         | 86.7            |
| missed line -- "no fact" but names people              | --          | --             | --          | --             | 85.0         | 80.0            |
| order -- who comes first                               | --          | --             | --          | --             | 85.0         | 85.0            |
| line type -- kind of statement                         | --          | --             | --          | --             | 85.0         | 85.0            |
| contradiction -- lines cannot both hold                | --          | --             | --          | --             | 85.0         | 85.0            |

Each row removes one component from the system at that budget and keeps everything else; "with" is always the full system at that budget, "--" means the component does not exist there. "2nd and 3rd read" at 3x without = the 1x system. How the extra calls fix errors: at 3x by agreement (three differently prompted reads, per-line vote; the model is never asked to correct itself), at 10x by targeted correction (code flags lines and why, and a re-read replaces the vote only when code found a concrete problem and the new answer passes the checks).

### C. Prompt techniques at 1×

|                                                       | **Unique** | **Ambiguous** | **Inconsistent** | **Macro** | **Calls/item** | **Lines right** | **Missed** | **Wrong** |
|-------------------------------------------------------|------------|---------------|------------------|-----------|----------------|-----------------|------------|-----------|
| **★ Few-shot: 12 worked examples, restate each line** | **80.0**   | **75.0**      | **80.0**         | **78.3**  | **1.0**        | **94.5**        | **46**     | **17**    |
| One-shot: 1 worked example                            | 50.0       | 45.0          | 65.0             | 53.3      | 1.0            | 91.6            | 67         | 28        |
| Zero-shot: rules and hints only                       | 50.0       | 40.0          | 55.0             | 48.3      | 1.0            | 90.3            | 76         | 34        |
| No plain-words restatement                            | 65.0       | 60.0          | 65.0             | 63.3      | 1.0            | 94.1            | 55         | 12        |
| Plain field names (a/b instead of earlier/later)      | 80.0       | 70.0          | 80.0             | 76.7      | 1.0            | 94.7            | 37         | 23        |
| Granite answers the whole item directly (no solver)   | 0.0        | 0.0           | 0.0              | 0.0       | 1.0            | --              | --         | --        |

Each row is one 1x system that differs from the submitted one (★) only in its prompt, all scored with the wrong-name repair. Lines right: % of note lines whose facts match the hand-checked facts exactly; Missed: real constraints read as "no fact"; Wrong: a wrong fact, or a fact invented from filler (counts per run, over all items). Worked examples use made-up names but were written from errors seen on the visible notes, so they are the main thing to watch for overfitting.

*Takeaway: worked examples and the plain-words restatement are the two components that matter most; field names (earlier/later vs a/b) make no measurable difference in one run.*

### C. Code repairs at 1× (same model output)

|                          | **Unique** | **Ambiguous** | **Inconsistent** | **Macro** | **Calls/item** | **Lines right** | **Missed** | **Wrong** |
|--------------------------|------------|---------------|------------------|-----------|----------------|-----------------|------------|-----------|
| None                     | 70.0       | 75.0          | 65.0             | 70.0      | 1.0            | 93.7            | 45         | 27        |
| **★ Wrong-name repair**  | **80.0**   | **75.0**      | **80.0**         | **78.3**  | **1.0**        | **94.5**        | **46**     | **17**    |
| Station/time swap repair | 70.0       | 75.0          | 65.0             | 70.0      | 1.0            | 93.7            | 45         | 27        |
| Both                     | 80.0       | 75.0          | 80.0             | 78.3      | 1.0            | 94.5            | 46         | 17        |

All four rows score the same 1x model output, so differences come from the repair alone, not sampling. Wrong-name repair: a fact naming someone its own line never mentions is swapped to the one unused name the line does mention, else dropped. Swap repair: a station written where a time belongs (or the reverse) becomes the matching fact; it fires too rarely to matter and is not in the submitted system.

*Takeaway: the wrong-name repair is worth +8 on identical model output; the swap repair never changes a score, which is why it is not in the submitted system.*

### C. Ways to spend three calls

|                                                   | **Unique** | **Ambiguous** | **Inconsistent** | **Macro** | **Calls/item** | **Lines right** | **Missed** | **Wrong** |
|---------------------------------------------------|------------|---------------|------------------|-----------|----------------|-----------------|------------|-----------|
| 1 read (the 1x system)                            | 80.0       | 75.0          | 80.0             | 78.3      | 1.0            | 94.5            | 46         | 17        |
| 2 reads + 1 re-ask of the lines they disagree on  | 90.0       | 80.0          | 85.0             | 85.0      | 2.7            | 96.0            | 32         | 14        |
| 3 reads, same prompt, per-line vote               | 85.0       | 70.0          | 85.0             | 80.0      | 3.0            | 95.2            | 38         | 17        |
| **★ 3 reads, 3 different prompts, per-line vote** | **85.0**   | **75.0**      | **85.0**         | **81.7**  | **3.0**        | **95.5**        | **41**     | **10**    |

Same prompt and repair throughout; only the use of the extra calls changes. A re-ask lets one fresh sample overwrite a line, so it can turn a right reading wrong; a vote needs two reads to agree. Reads with the same prompt make the same mistakes, so they rarely outvote one another.

*Takeaway: in this run the re-ask design scored higher (85.0 vs 81.7); an earlier 3-run comparison went the other way (81.1 vs 83.9). Re-asking recovers misses (32 vs 41) but adds wrong lines (14 vs 10); with one run each, neither wins.*

### C. Where the 10× calls go

|                                                                    | **Items asked** | **Lines re-asked** | **Macro without it** | **Gain** |
|--------------------------------------------------------------------|-----------------|--------------------|----------------------|----------|
| 3-read vote (the 3x system)                                        | --              | --                 | 81.7                 | --       |
| \+ name check (a name, time or station the line does not mention)  | 40%             | 36                 | 86.7                 | -1.7     |
| \+ missed line (read as no fact, but the restatement names people) | 45%             | 39                 | 80.0                 | +5.0     |
| \+ order (reads disagree on who comes first)                       | 20%             | 13                 | 85.0                 | +0.0     |
| \+ line type (reads disagree on the kind of statement)             | 37%             | 38                 | 85.0                 | +0.0     |
| \+ contradiction (two lines cannot both hold)                      | 2%              | 3                  | 85.0                 | +0.0     |
| **★ Full 10x**                                                     | **--**          | **--**             | **85.0**             | **--**   |

After the 3-read vote, code flags lines and makes one call per kind of problem, only for items that have such lines. Items asked: share of items that got this call; Lines re-asked: lines sent, per run; Gain: full 10x minus the system without this kind of call.

*Takeaway: one run. Only the missed-line call shows a gain (+5.0); the others are within one item. Across the 5 headline runs, all re-asks together beat the vote every time (+3.7 on average).*

### C. Declared versus true kind

| **True kind →**       | **1x U** | **A** | **I** | **3x U** | **A** | **I** | **10x U** | **A** | **I** |
|-----------------------|----------|-------|-------|----------|-------|-------|-----------|-------|-------|
| Declared unique       | 16       | 0     | 1     | 17       | 0     | 1     | 18        | 0     | 1     |
| Declared ambiguous    | 4        | 18    | 1     | 3        | 19    | 1     | 1         | 19    | 0     |
| Declared inconsistent | 0        | 2     | 18    | 0        | 1     | 18    | 1         | 1     | 19    |

Mean item counts over runs for the submitted system at each budget (U = unique, A = ambiguous, I = inconsistent); the diagonal is correct. A missed constraint leaves too many solutions (a unique item declared ambiguous); a misread one usually makes the notes contradict (declared inconsistent).

*Takeaway: no kind is over-declared. Most errors at 1x are unique items declared ambiguous (a missed constraint leaves extra solutions); 10x fixes most of them.*

---

## Running the system and reproducing the ablation

### Where things are

| What | Where |
|---|---|
| Write-up (2 pages + appendix with the detailed ablation) | this README (above); also as `docs/devanshi_garg_write_up.docx` and `docs/devanshi_garg_write_up.md` |
| Ablation tables (PDF, full suite) | `docs/devanshi_garg_ablation_tables.pdf` |
| Reported ablation results (my runs) | `ablation/ablation_<suite>_suite_<N>_run(s)_orig/` (`.html`, `.md`, `.tex`, `.pdf`, `.json` inside). A re-run writes the same name without `_orig`, so it never overwrites these. |
| Any ablation run | `ablation/ablation_<quick\|main\|full>_suite_<N>_runs/`: `.html`, `.md`, `.tex` / `.pdf`, and `.json` (the scores behind the tables) |

### Setup

Python 3.9.6 (tested).

```bash
pip install -r requirements.txt
cp .env.example .env        # endpoint and key
```

### Run

```bash
./run <items.json> --budget <1x|3x|10x> --out <answers.json>
```

| Budget | System | Calls per item |
|---|---|---|
| `1x` | one read → wrong-name repair → solver | 1 |
| `3x` | three reads (different prompts) → per-line vote → repair → solver | 3 |
| `10x` | 3x + one targeted re-ask per kind of problem code flags | 3–8 |

No caching. Rate-limited calls (429) are retried up to 5 times and parallelism backs off; nothing else is retried.

### Ablation

The task files (`items.json`, `visible_key.json`, `score.py`) are included. 
You can reproduce whichever ablation fits your time budget.

| Command | What you get | Calls | Time (typical – worst) |
|---|---|---|---|
| `python3 ablate.py --runs 1` | **quick, 1 run**: fastest check of the headline numbers | ~350 | 4 – 12 min |
| `python3 ablate.py --runs 3` | **quick, 3 runs**: the same rows with run-to-run spread | ~1,000 | 12 – 33 min |
| `python3 ablate.py --suite main --runs 1` | **main**: the whole main table (prompt components at 1x only) | ~1,000 | 12 – 33 min |
| `python3 ablate.py --suite full --runs 1` | **full**: every component at every budget + supporting tables | ~1,500 | 18 – 50 min |

Typical: measured, ~1.4 calls/s with up to 16 calls in flight (main took 12 min). Worst: ~0.5 calls/s
if the endpoint rate-limits hard (quick × 3 took 33 min at 8 calls in flight). `--workers`
(default 16) caps calls in flight; each rate limit halves it and a streak of successes adds one
back, so the default is safe.

**If it is interrupted** (a laptop going to sleep, a dropped connection), run the same command
again: it resumes after the last finished system, so at most one system is redone (`--fresh`
starts over). On a laptop, stop it sleeping during long runs: `caffeinate -i <command>` on macOS,
`systemd-inhibit <command>` on Linux; servers need nothing.

**To reproduce:** run quick (1 run is enough) and compare `ablation/ablation_quick_suite_1_run/` with my
`ablation/ablation_quick_suite_3_runs_orig/`: each number should fall within about the
`±` spread shown there (one item moves a macro score by 1.7 points).

What each suite runs:

| Suite | Systems | Rows of the main table it fills |
|---|---|---|
| quick | `1x`, `1x-direct`, `3x`, `3x-norepair`, `10x` | full system at every budget; solver vs Granite answering directly (1x); wrong-name repair at 1x/3x; 3-read vote vs one read; all 10x re-asks vs the vote alone. Other rows show `--`. |
| main | 16: quick + `1x-zeroshot`, `1x-norestate`, `3x-sameprompt`, `10x-norepair`, `10x-sameprompt`, `10x-oneread`, and each 10x re-ask removed on its own | every row; worked examples and restating at 1x only |
| full | all 23: main + `1x-oneshot`, `1x-plainfields`, `3x-zeroshot`, `3x-norestate`, `3x-followup`, `10x-zeroshot`, `10x-norestate` | every row at every budget, plus supporting tables (prompt techniques, ways to spend 3 calls, where 10x calls go) |

Every system uses the solver except `1x-direct`, where Granite answers the whole item itself (the "without solver" baseline).

My results (`_orig` files) come from: quick × 3 runs, main × 1, full × 1.

Output: one folder per ablation, `ablation/ablation_<suite>_suite_<N>_runs/`, holding `.html`,
`.md`, `.tex` (and `.pdf` if `pdflatex` is installed) and `.json`. **Open the `.html` in a browser
for the best view if no `.pdf` was generated.** A re-run with the same settings overwrites its folder.
Within a run, systems share identical reads, so those that differ only after the reads are
compared on the same model output.

- Quick check: `python3 ablate.py --runs 1 --limit 10`
- Rebuild tables without calls: `python3 ablate.py --report ablation/<name>/<name>.json`

`reference_facts.json` (correct fact per visible line) is used only to count lines read right,
missed and wrong; never to answer.

### Files

| File | Role |
|---|---|
| `run`, `run.py` | entrypoint |
| `extract.py` | header parsing, prompts, model call |
| `multi.py` | 3x / 10x: extra reads, vote, re-asks |
| `repair.py` | checks and wrong-name repair |
| `solver.py` | rota search, case, conflict set |
| `ablate.py`, `report.py` | ablation and tables |
