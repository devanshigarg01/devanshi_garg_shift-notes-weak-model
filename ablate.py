#!/usr/bin/env python3
"""Ablation: one command runs every system the ablation document needs, scores them, and
writes the document (the main with/without table plus supporting tables).

    python3 ablate.py --runs 3                      # quick suite, 3 runs -> ablation/ablation_quick_suite_3_runs/ (.html, .md, .tex, .pdf)
    python3 ablate.py --suite main --runs 1         # the whole main table
    python3 ablate.py --suite full                  # every system and every supporting table
    python3 ablate.py --runs 1 --limit 10           # quick check on 10 items
    python3 ablate.py --report ablation/ablation_quick_suite_3_runs/ablation_quick_suite_3_runs.json   # rebuild the tables, no model calls
    python3 ablate.py --only 1x 3x 10x              # a subset of systems (names in SYSTEMS)

Needs items.json, visible_key.json and score.py from the task package in this folder (or pass
--items / --key / --scorer), plus .env with the endpoint and key.

The submitted system at each budget (every other system is one of these with one thing changed):
  1x   one read (prompt A: worked examples + restate each line) -> wrong-name repair -> solver
  3x   three reads with prompts A, B, C -> per-line majority vote -> repair -> solver
  10x  the 3x system, then targeted re-asks of the lines code flags, one call per kind of problem

An interrupted ablation resumes where it stopped: re-run the same command (--fresh starts over).

Within one run, systems share model replies: a read with the same prompt (and the same n-th read
of it) for the same item is made once and reused, and so is a re-ask with exactly the same text.
So systems that differ only after the reads are compared on the same reads (paired), and a run
costs ~1,700 calls instead of ~4,000. Nothing is kept between runs or written to disk: every run
samples afresh, and every number is a mean over --runs runs with its spread.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import extract
import multi
import repair
import report
import solver
from extract import prompts

HERE = os.path.dirname(os.path.abspath(__file__))

# "Without the solver": the model answers the whole item itself.
DIRECT_PROMPT = """You are given shift notes. Some lines constrain which person works which time block, and which station the station-holders are on. Other lines are filler. Hedges like "I'm fairly sure" do not weaken a statement.

Decide which case applies:
- "unique": exactly one assignment satisfies all the constraints.
- "ambiguous": 2 to 4 assignments satisfy them. List ALL of them.
- "inconsistent": no assignment satisfies them. Quote the smallest set of lines that contradict each other, each line copied exactly.

Every assignment must include every person. Give "station" only for people the header says are on a station.

Reply with JSON only, in one of these forms:
{"case": "unique", "assignment": {"<name>": {"block": "<block>", "station": "<station>"}, "<name>": {"block": "<block>"}}}
{"case": "ambiguous", "assignments": [{...}, {...}]}
{"case": "inconsistent", "conflicts": ["<exact line>", "<exact line>"]}

Notes:
"""

# ------------------------------------------------------------------ systems

SYSTEM_FIXES = repair.SYSTEM_FIXES      # the submitted system runs the wrong-name repair only
FIXES = {"no fixes": set(), "F1": {"F1"}, "F2": {"F2"}, "all": {"F1", "F2"}}   # F1 = swap, F2 = wrong-name
FIXNAME = {frozenset(v): k for k, v in FIXES.items()}
REASKS = ("names", "missed", "order", "kind", "contradiction")


def _systems():
    s = {
        # 1x: one read. Repair settings are compared on the same output (no extra calls).
        "1x":             dict(budget="1x"),
        "1x-oneshot":     dict(budget="1x", shots="one"),
        "1x-zeroshot":    dict(budget="1x", shots="zero"),
        "1x-norestate":   dict(budget="1x", restate=False),
        "1x-plainfields": dict(budget="1x", roles=False),
        "1x-direct":      dict(budget="1x", direct=True),
        # 3x: three reads + vote
        "3x":             dict(budget="3x", mode="vote"),
        "3x-zeroshot":    dict(budget="3x", mode="vote", shots="zero"),
        "3x-norestate":   dict(budget="3x", mode="vote", restate=False),
        "3x-norepair":    dict(budget="3x", mode="vote", fixes=()),
        "3x-sameprompt":  dict(budget="3x", mode="vote", reads="same"),
        "3x-followup":    dict(budget="3x", mode="followup"),    # alternative: 2 reads + 1 re-ask
        # 10x: the 3x system + targeted re-asks
        "10x":            dict(budget="10x"),
        "10x-zeroshot":   dict(budget="10x", shots="zero"),
        "10x-norestate":  dict(budget="10x", restate=False),
        "10x-norepair":   dict(budget="10x", fixes=()),
        "10x-sameprompt": dict(budget="10x", reads="same"),
        "10x-oneread":    dict(budget="10x", reads="one"),
    }
    for k in REASKS:
        s[f"10x-no{k}"] = dict(budget="10x", skip=(k,))
    return s


SYSTEMS = _systems()

# Two suites. "main": only what the brief asks for -- the main with/without table, with the
# prompt components (examples, restating) measured at 1x only. "full": every system, every table.
FULL_ONLY = {"1x-oneshot", "1x-plainfields", "3x-zeroshot", "3x-norestate", "3x-followup",
             "10x-zeroshot", "10x-norestate"}
QUICK = ["1x", "1x-direct", "3x", "3x-norepair", "10x"]   # full system per budget + what comes free from those runs
SUITES = {"quick": QUICK, "main": [n for n in SYSTEMS if n not in FULL_ONLY], "full": list(SYSTEMS)}

# ------------------------------------------------------------------ scoring

def score(answers_path, a):
    out = subprocess.run([sys.executable, a.scorer, a.key, answers_path, a.items],
                         capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)
    return {"macro": s["macro_exact_match"], "per_case": s["per_case_rate"], "confusion": s["confusion"]}


def canon(f):
    if f.get("rel") == "between":
        return ("between", f.get("mid"), *sorted([str(f.get("a")), str(f.get("b"))]))
    return (f.get("rel"), *[f.get(k) for k in solver.ARGS.get(f.get("rel"), ())])


def line_accuracy(facts, gold):
    """Each gold line against the system's line of the same text: correct / missed / wrong / invented."""
    c = Counter()
    for iid, g in gold.items():
        by_text = {l.get("text"): l for l in ((facts.get(iid) or {}).get("lines") or []) if isinstance(l, dict)}
        for l in g["lines"]:
            c["total"] += 1
            m = by_text.get(l["text"])
            gf = sorted(map(canon, l["facts"]))
            xf = sorted(set(map(canon, (m or {}).get("facts") or [])))
            c["correct" if gf == xf else "invented" if not gf else "missed" if not xf else "wrong"] += 1
    return dict(c)


def evaluate(facts, stem, a, gold):
    """Score one set of extracted facts under every repair setting (paired: same model output)."""
    out = {}
    for fname, opts in FIXES.items():
        fixed = {iid: (repair.apply(it, opts)[0] if isinstance(it, dict) and "lines" in it else it)
                 for iid, it in facts.items()}
        answers = {iid: solver.solve_checked(it)[0] for iid, it in fixed.items()}
        path = stem + "_answers_" + re.sub(r"[^\w]", "_", fname) + ".json"
        json.dump(answers, open(path, "w"), indent=1)
        out[fname] = {**score(path, a), "lines": line_accuracy(fixed, gold)}
    return out


def parse_direct(raw):
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    i, j = raw.find("{"), raw.rfind("}")
    try:
        return json.loads(raw[i:j + 1])
    except Exception:
        return {"case": "inconsistent", "conflicts": []}

# ------------------------------------------------------------------ running

class Progress:
    """tqdm bar if installed, else a line every 10 items."""

    def __init__(self, total, desc):
        try:
            from tqdm import tqdm
            self.bar = tqdm(total=total, desc=desc, unit="item", dynamic_ncols=True, leave=False)
        except ImportError:
            self.bar = None
        self.total, self.desc, self.n = total, desc, 0

    def step(self):
        self.n += 1
        S = extract.STATS
        stats = f"calls={S['calls']} shared={S['shared']} rate-limit retries={S['rate_limited']} failed={S['errors']}"
        if self.bar is not None:
            self.bar.set_postfix_str(stats, refresh=False)
            self.bar.update(1)
        elif self.n % 10 == 0 or self.n == self.total:
            print(f"  {self.desc}: {self.n}/{self.total}  {stats}", flush=True)

    def close(self):
        if self.bar is not None:
            self.bar.close()


def run_system(name, spec, r, items, a, gold, outdir):
    """Run one system once over all items. Returns {stage: record}; 10x also returns its "vote" stage."""
    cfgs = prompts(spec.get("shots", "few"), spec.get("restate", True), spec.get("roles", True),
                   spec.get("reads", "diff"))
    fixes = frozenset(spec.get("fixes", SYSTEM_FIXES))
    stem = os.path.join(outdir, f"{name}_run{r}")
    bar = Progress(len(items), f"{name} run {r}")
    budget = spec["budget"]

    if spec.get("direct"):
        def one(it):
            raw = extract.call(it, cfgs[0], prompt=DIRECT_PROMPT + it["text"])
            bar.step()
            return raw
        with ThreadPoolExecutor(a.workers) as pool:
            raws = list(pool.map(one, items))
        bar.close()
        answers = {it["id"]: parse_direct(x) for it, x in zip(items, raws)}
        json.dump(answers, open(stem + "_answers.json", "w"), indent=1)
        s = score(stem + "_answers.json", a)
        return {"1x": {"fix": {f: s for f in FIXES}, "calls_mean": 1.0}}

    if budget == "1x":
        facts, raws, bad, api = extract.run_extract(items, cfgs[0], a.workers, progress=bar.step)
        bar.close()
        json.dump(facts, open(stem + "_facts.json", "w"), indent=1)
        return {"1x": {"fix": evaluate(facts, stem, a, gold), "calls_mean": 1.0,
                       "unparseable": bad, "api_errors": api}}

    with ThreadPoolExecutor(a.workers) as pool:
        futs = {pool.submit(multi.run_item, it, cfgs, budget, fixes,
                            spec.get("mode", "vote"), set(spec.get("skip", ()))): it["id"] for it in items}
        done = {}
        for f in as_completed(futs):
            done[futs[f]] = f.result()
            bar.step()
    bar.close()
    outs = [done[it["id"]] for it in items]
    json.dump({it["id"]: o["asked"] for it, o in zip(items, outs)}, open(stem + "_asked.json", "w"), indent=1)
    res = {}
    for tag in outs[0]["stages"]:
        if tag == "1x":
            continue
        facts = {it["id"]: o["stages"][tag] for it, o in zip(items, outs)}
        json.dump(facts, open(f"{stem}_{tag}_facts.json", "w"), indent=1)
        used = [o["stage_calls"][tag] for o in outs]
        asked = [q for o in outs for q in o["asked"]] if tag == budget else []
        lines = Counter()
        for q in asked:
            lines[q["type"]] += len(q["lines"])
        res[tag] = {"fix": evaluate(facts, f"{stem}_{tag}", a, gold),
                    "calls_mean": statistics.mean(used), "calls_max": max(used),
                    "reask_calls": dict(Counter(q["type"] for q in asked)), "reask_lines": dict(lines)}
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--suite", choices=list(SUITES), default="quick",
                    help="quick: each budget's system + solver/repair/vote/re-ask rows (~360 calls/run); "
                         "main: the whole main table (~1,000); full: every system and table (~1,500)")
    ap.add_argument("--only", nargs="*", help="run only these systems (see SYSTEMS); overrides --suite")
    ap.add_argument("--limit", type=int, help="first N items only (plumbing checks)")
    ap.add_argument("--fresh", action="store_true", help="start over instead of resuming an interrupted run")
    ap.add_argument("--report", help="a results .json written by an earlier run: rebuild its tables, no calls")
    ap.add_argument("--workers", type=int, default=16, help="max model calls in flight (halves on rate limits, then recovers)")
    ap.add_argument("--items", default=os.path.join(HERE, "items.json"))
    ap.add_argument("--key", default=os.path.join(HERE, "visible_key.json"))
    ap.add_argument("--scorer", default=os.path.join(HERE, "score.py"))
    ap.add_argument("--gold", default=os.path.join(HERE, "reference_facts.json"),
                    help="correct fact for every line of the visible set (for the line-level counts)")
    a = ap.parse_args()

    if a.report:
        base = os.path.splitext(a.report)[0]
        return report.build(json.load(open(a.report)), os.path.dirname(base) or ".", os.path.basename(base))

    problem = extract.check_setup()
    if problem:
        sys.exit(f"cannot call the model: {problem}")
    extract.LIMITER = extract.Limiter(a.workers)
    names = a.only or SUITES[a.suite]
    unknown = [n for n in names if n not in SYSTEMS]
    if unknown:
        sys.exit(f"unknown systems {unknown}; choose from {list(SYSTEMS)}")
    items = json.load(open(a.items))[: a.limit]
    ids = {i["id"] for i in items}
    gold = {k: v for k, v in json.load(open(a.gold)).items() if k in ids}
    # Final tables go to ablation/<name>/<name>.html/.md/.tex/.pdf (+ .json, the scores behind
    # them, for --report). Intermediate files (facts, answers per system) live in a temp folder.
    suite = "custom" if a.only else a.suite
    out_name = (f"ablation_{suite}_suite_{a.runs}_run{'s' if a.runs > 1 else ''}"
            + (f"_{len(items)}_items" if a.limit else ""))
    final_dir = os.path.join(HERE, "ablation", out_name)       # one folder per ablation
    os.makedirs(final_dir, exist_ok=True)
    tmp = tempfile.TemporaryDirectory()
    outdir = tmp.name
    if a.limit:   # score only the items run
        key = {k: v for k, v in json.load(open(a.key)).items() if k in ids}
        a.key = os.path.join(outdir, "key_subset.json")
        json.dump(key, open(a.key, "w"))

    print(f"{len(names)} systems x {a.runs} runs x {len(items)} items -> ablation/{out_name}/", flush=True)

    res = {"meta": {"items": len(items), "runs": a.runs, "suite": suite, "started": time.strftime("%Y-%m-%d %H:%M"),
                    "command": " ".join(["python3", "ablate.py"] + sys.argv[1:]), "systems": names, "runs_done": 0},
           "systems": {}}
    # Checkpoints: after every system, the scores so far and this run's shared replies are saved.
    # Re-running the same command (same systems, runs and items) resumes after the last finished
    # system, with the same shared replies, so systems keep sharing reads across the interruption.
    path = os.path.join(final_dir, out_name + ".json")
    shared_path = os.path.join(final_dir, ".shared_replies.json")   # deleted when the ablation finishes
    done_in_run = []
    if not a.fresh and os.path.exists(path):
        old = json.load(open(path))
        m = old.get("meta", {})
        if ("minutes" not in m and m.get("systems") == names and m.get("runs") == a.runs
                and m.get("items") == len(items)):
            res = old
            r_now = m.get("runs_done", 0) + 1
            done_in_run = [n for n in m.get("done_in_run", []) if n in names]
            for n, e in res["systems"].items():          # drop anything not checkpointed
                keep = r_now if n in done_in_run else r_now - 1
                e["stages"] = {t: recs[:keep] for t, recs in e["stages"].items()}
            if done_in_run and os.path.exists(shared_path):
                extract.SHARED = json.load(open(shared_path))
            elif done_in_run:                              # replies lost: redo this run from the start
                done_in_run = []
                for e in res["systems"].values():
                    e["stages"] = {t: recs[:r_now - 1] for t, recs in e["stages"].items()}
            print(f"resuming {out_name}: {r_now - 1} run(s) done"
                  + (f", run {r_now}: {len(done_in_run)}/{len(names)} systems done" if done_in_run else "")
                  + " (--fresh to start over)", flush=True)
    res["meta"]["done_in_run"] = done_in_run

    def checkpoint():
        res["meta"]["done_in_run"] = done_in_run
        json.dump(res, open(path, "w"), indent=1)
        json.dump(extract.SHARED, open(shared_path, "w"))

    t0 = time.time()
    for r in range(res["meta"]["runs_done"] + 1, a.runs + 1):
        if not done_in_run:
            extract.SHARED = {}               # replies shared by the systems of this run only
        for name in names:
            if name in done_in_run:
                continue
            spec = SYSTEMS[name]
            stages = run_system(name, spec, r, items, a, gold, outdir)
            e = res["systems"].setdefault(name, {
                "budget": spec["budget"], "fixname": FIXNAME[frozenset(spec.get("fixes", SYSTEM_FIXES))],
                "stages": {}})
            for tag, rec in stages.items():
                e["stages"].setdefault(tag, []).append(rec)
            final = stages[spec["budget"]]
            print(f"  run {r}  {name:16s} macro={100 * final['fix'][e['fixname']]['macro']:5.1f}  "
                  f"calls/item={final['calls_mean']:.2f}", flush=True)
            done_in_run.append(name)
            checkpoint()
        res["meta"]["runs_done"] = r            # run r is complete
        done_in_run = []
        extract.SHARED = {}
        checkpoint()
    res["meta"].pop("done_in_run", None)
    if os.path.exists(shared_path):
        os.remove(shared_path)
    res["meta"]["minutes"] = round((time.time() - t0) / 60, 1)
    res["meta"]["calls"] = dict(extract.STATS)
    json.dump(res, open(path, "w"), indent=1)
    report.build(res, final_dir, out_name)
    tmp.cleanup()


if __name__ == "__main__":
    main()
