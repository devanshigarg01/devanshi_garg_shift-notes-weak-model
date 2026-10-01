"""Entrypoint:  ./run <items.json> --budget <1x|3x|10x> --out <answers.json>

  1x   one read of the notes -> wrong-name repair -> solver                      (exactly 1 call)
  3x   three reads with three different prompts -> per-line vote -> repair -> solver   (3 calls)
  10x  the 3x system + targeted re-asks of the lines code flags                  (3 to 8 calls)

Granite turns each line into a structured fact; everything after that is code. See README.md.
"""
import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import extract
import multi
import repair
import solver

CAPS = {"1x": 1, "3x": 3, "10x": 10}


def answer(item, budget):
    """(answer, number of model calls) for one item."""
    if budget == "1x":
        facts, _ = extract.read(item, extract.PROMPT_A)
        calls = 1
    else:
        out = multi.run_item(item, extract.prompts(), budget, repair.SYSTEM_FIXES)
        facts, calls = out["stages"][budget], out["calls"]
    if isinstance(facts, dict) and "lines" in facts:
        facts = repair.apply(facts, repair.SYSTEM_FIXES)[0]
    return solver.solve_checked(facts)[0], calls     # never raises; a bad item gets a valid, wrong answer


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("items")
    ap.add_argument("--budget", required=True, choices=list(CAPS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8, help="max model calls in flight (halves on rate limits, then recovers)")
    a = ap.parse_args()

    problem = extract.check_setup()
    if problem:
        sys.exit(f"cannot call the model: {problem}")

    extract.LIMITER = extract.Limiter(a.workers)
    items = json.load(open(a.items))
    answers, calls = {}, {}
    with ThreadPoolExecutor(a.workers) as pool:
        futs = {pool.submit(answer, it, a.budget): it["id"] for it in items}
        for i, f in enumerate(as_completed(futs), 1):
            answers[futs[f]], calls[futs[f]] = f.result()
            print(f"\r{i}/{len(items)} items", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)

    with open(a.out, "w") as fh:
        json.dump({it["id"]: answers[it["id"]] for it in items}, fh, indent=1)

    used = list(calls.values())
    failed = extract.STATS["errors"]
    assert 1 <= min(used) and max(used) <= CAPS[a.budget], "budget violated"
    cases = Counter(x["case"] for x in answers.values())
    print(f"wrote {a.out}: {len(items)} items, {dict(cases)} | model calls per item: "
          f"min {min(used)}, mean {sum(used) / len(used):.2f}, max {max(used)} | "
          f"rate-limit retries {extract.STATS['rate_limited']}, failed calls {failed}",
          file=sys.stderr)
    if failed:
        iid, err = extract.ERRORS[0]
        print(f"WARNING: {failed} model call(s) failed; first ({iid}): {err[:300]}", file=sys.stderr)
        if failed > sum(used) / 2:          # mostly failures: the answers are not the model's
            sys.exit("most model calls failed, so these answers are not meaningful (see the error above)")


if __name__ == "__main__":
    main()
