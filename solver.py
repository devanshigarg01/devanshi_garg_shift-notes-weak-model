#!/usr/bin/env python3
"""Solver: structured facts in, answers out. No model, no parsing of English.

    python3 solver.py facts.json --out answers.json [--report report.json]

Input (facts.json), keyed by item id:
{
  "B2-002": {
    "people":   ["Daniel", "Rohan", "Nadia", "Priya", "Meera"],
    "blocks":   ["07:00", "09:00", "11:00", "13:00", "15:00"],   # earliest first
    "stations": ["intake", "calibration", "packing"],
    "holders":  ["Daniel", "Rohan", "Meera"],                    # people on a station
    "lines": [
      {"n": 1, "text": "<exact line from the notes>", "facts": []},
      {"n": 2, "text": "Nadia then Priya, back to back.",
       "facts": [{"rel": "immediately_before", "a": "Nadia", "b": "Priya"}]}
    ]
  }
}

Relations. A "term" is a person, or a station name meaning "whoever holds it".
  at                  {who: term, block}      term is on that block
  not_at              {who: term, block}      term is not on that block
  on                  {person, station}       person holds that station
  not_on              {person, station}       person does not hold that station
  before              {a: term, b: term}      a strictly earlier than b (any gap)
  immediately_before  {a: term, b: term}      a's block is the one right before b's
  between             {mid, a, b: terms}      mid strictly between a and b, either order

How it works:
  1. Drop (and report) any fact with a name/block/station not in the vocabulary.
  2. Enumerate every world: people -> blocks (5! = 120) x holders -> stations (3! = 6).
  3. For every world, record which lines it breaks (a line breaks if any fact on it is false).
  4. Worlds that break nothing are the solutions: 1 = unique, 2+ = ambiguous, 0 = inconsistent.
  5. Inconsistent: deletion-based minimal conflict set over lines -- drop each line in turn,
     keep it dropped if the rest is still impossible. What remains is minimal.
"""
import argparse
import json
from itertools import permutations

ARGS = {
    "at": ("who", "block"),
    "not_at": ("who", "block"),
    "on": ("person", "station"),
    "not_on": ("person", "station"),
    "before": ("a", "b"),
    "immediately_before": ("a", "b"),
    "between": ("mid", "a", "b"),
}


def check_fact(f, item):
    """Return (clean_fact, None) or (None, reason)."""
    rel = f.get("rel")
    if rel not in ARGS:
        return None, f"unknown relation {rel!r}"
    terms = set(item["people"]) | set(item["stations"])
    allowed = {"who": terms, "a": terms, "b": terms, "mid": terms,
               "person": set(item["people"]), "station": set(item["stations"]),
               "block": set(item["blocks"])}
    for k in ARGS[rel]:
        if f.get(k) not in allowed[k]:
            return None, f"{rel}.{k}={f.get(k)!r} not in vocabulary"
    return {"rel": rel, **{k: f[k] for k in ARGS[rel]}}, None


def holds(f, block, station, holder_of):
    """Is fact f true in the world (block: person->index, station: holder->station)?"""
    def t(term):  # block index of a term; a station name means its holder
        return block[holder_of.get(term, term)]

    r = f["rel"]
    if r == "at":
        return t(f["who"]) == f["_bi"]
    if r == "not_at":
        return t(f["who"]) != f["_bi"]
    if r == "on":
        return station.get(f["person"]) == f["station"]
    if r == "not_on":
        return station.get(f["person"]) != f["station"]
    if r == "before":
        return t(f["a"]) < t(f["b"])
    if r == "immediately_before":
        return t(f["a"]) + 1 == t(f["b"])
    if r == "between":
        m, a, b = t(f["mid"]), t(f["a"]), t(f["b"])
        return min(a, b) < m < max(a, b)


def solve_item(item):
    report = {"dropped": [], "warnings": []}
    people, blocks, holders = item["people"], item["blocks"], item["holders"]
    # header sanity: a bad header would silently produce nonsense worlds
    if not (len(people) == len(blocks) == len(set(people))
            and len(holders) == len(item["stations"]) == len(set(holders))
            and set(holders) <= set(people)):
        raise ValueError(f"inconsistent header: people={people} blocks={blocks} "
                         f"stations={item['stations']} holders={holders}")

    # 1. validate facts, keep them grouped by line
    by_line, text = {}, {}
    for line in item["lines"]:
        text[line["n"]] = line["text"]
        for f in line.get("facts", []):
            clean, why = check_fact(f, item)
            if clean is None:
                report["dropped"].append({"line": line["n"], "fact": f, "why": why})
                continue
            if "block" in clean:
                clean["_bi"] = blocks.index(clean["block"])
            by_line.setdefault(line["n"], []).append(clean)
    lines = sorted(by_line)

    # 2 + 3. every world, and the set of lines it breaks
    worlds = []
    for bperm in permutations(range(len(blocks)), len(people)):
        block = dict(zip(people, bperm))
        for sperm in permutations(item["stations"], len(holders)):
            station = dict(zip(holders, sperm))
            holder_of = {s: p for p, s in station.items()}
            broken = frozenset(n for n in lines
                               if not all(holds(f, block, station, holder_of) for f in by_line[n]))
            worlds.append((block, station, broken))

    # 4. solutions and case
    sols = [(b, s) for b, s, broken in worlds if not broken]
    report["n_solutions"] = len(sols)
    report["constraint_lines"] = lines

    def answer_for(b, s):
        return {p: ({"block": blocks[b[p]], "station": s[p]} if p in s
                    else {"block": blocks[b[p]]}) for p in people}

    if len(sols) == 1:
        return {"case": "unique", "assignment": answer_for(*sols[0])}, report
    if len(sols) > 1:
        if len(sols) > 4:
            report["warnings"].append(f"{len(sols)} solutions (the brief says at most 4)")
        return {"case": "ambiguous", "assignments": [answer_for(b, s) for b, s in sols]}, report

    # 5. minimal conflict set
    broken_sets = [broken for _, _, broken in worlds]
    impossible = lambda subset: all(br & subset for br in broken_sets)
    core = set(lines)
    for n in lines:
        if impossible(core - {n}):
            core.discard(n)
    core = sorted(core)
    report["conflict_lines"] = core
    if len(core) < 3:
        report["warnings"].append(f"conflict set has {len(core)} lines (the brief says at least 3)")
    return {"case": "inconsistent", "conflicts": [text[n] for n in core]}, report


FALLBACK = {"case": "inconsistent", "conflicts": []}  # valid shape; scores 0


def validate_answer(ans, item):
    """F6: is this answer well-formed for this item? Returns a problem string or None.
    Checks what the brief's format and auto-fail rules need: known case, every person named
    exactly once with a real and distinct block, a station only (and always) for station
    holders with distinct stations, citations that are whole lines of the notes."""
    people, blocks = item["people"], set(item["blocks"])
    holders, stations = set(item["holders"]), set(item["stations"])

    def check_assignment(a):
        if not isinstance(a, dict) or set(a) != set(people):
            return "assignment does not name exactly the rota"
        bl = [v.get("block") if isinstance(v, dict) else None for v in a.values()]
        if any(b not in blocks for b in bl) or len(set(bl)) != len(bl):
            return "bad or repeated block"
        st = [a[p].get("station") for p in people if p in holders]
        if any(s not in stations for s in st) or len(set(st)) != len(st):
            return "bad or repeated station"
        if any("station" in a[p] for p in people if p not in holders):
            return "station given to someone with no station"
        return None

    case = ans.get("case") if isinstance(ans, dict) else None
    if case == "unique":
        return check_assignment(ans.get("assignment"))
    if case == "ambiguous":
        A = ans.get("assignments")
        if not isinstance(A, list) or len(A) < 2:
            return "ambiguous needs 2+ assignments"
        for a in A:
            p = check_assignment(a)
            if p:
                return p
        if len({json.dumps(a, sort_keys=True) for a in A}) != len(A):
            return "duplicate assignments"
        return None
    if case == "inconsistent":
        C = ans.get("conflicts")
        texts = {l.get("text") for l in item["lines"]}
        if not isinstance(C, list) or not C:
            return "no conflicts cited"
        if any(c not in texts for c in C) or len(set(C)) != len(C):
            return "citation is not a line of the notes"
        return None
    return f"unknown case {case!r}"


def solve_checked(item):
    """solve_item + F6: never raises, always returns a well-formed answer."""
    try:
        ans, rep = solve_item(item)
    except Exception as e:  # malformed item (e.g. bad model output)
        return dict(FALLBACK), {"error": repr(e), "warnings": [], "dropped": []}
    problem = validate_answer(ans, item)
    if problem:
        rep["error"] = f"invalid answer: {problem}"
        return dict(FALLBACK), rep
    return ans, rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("facts")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", help="optional per-item diagnostics (json)")
    a = ap.parse_args()

    facts = json.load(open(a.facts))
    answers, reports = {}, {}
    for iid, item in facts.items():
        answers[iid], reports[iid] = solve_checked(item)

    json.dump(answers, open(a.out, "w"), indent=1)
    if a.report:
        json.dump(reports, open(a.report, "w"), indent=1)

    cases = [x["case"] for x in answers.values()]
    warn = sum(bool(r["warnings"]) for r in reports.values())
    errs = sum("error" in r for r in reports.values())
    drop = sum(len(r["dropped"]) for r in reports.values())
    print(f"{len(answers)} items: " + ", ".join(f"{c}={cases.count(c)}" for c in
          ("unique", "ambiguous", "inconsistent")) + f" | items with warnings={warn}, dropped facts={drop}, malformed items={errs}")


if __name__ == "__main__":
    main()
