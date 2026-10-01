#!/usr/bin/env python3
"""Code-level checks and fixes on a facts item, applied after extraction and before the
solver. Pure code, no model calls, nothing specific to English wording: every check uses
only the item's own vocabulary (names, times, stations from its header) and logic.

Checks (detect):
  C2  shape sanity   - a fact that can't be right in any rota: someone before themselves, a
                       "between" whose middle is also an end, a time in a station field, a
                       station for someone who has none, a value outside the vocabulary
  C3  name in line   - every name/time/station a fact uses must appear in its own line's text
  C6  guarantees     - (check only, no fix) from the brief: an item has 1-4 solutions, or none with every minimal
                       conflict set having >= 3 lines. So >4 solutions, a single line that is
                       impossible on its own, or two lines that contradict on their own, mean
                       something was misread.

Fixes (act):
  F1  type fix       - a station in a time field (or the reverse) -> the matching station/time fact
  F2  name fix       - a C3 failure: swap in the one name of the same kind that the line
                       mentions and the fact didn't use; otherwise drop the fact

    import repair
    fixed_item, log = repair.apply(item, {"F1", "F2"})
"""
import copy
import itertools
import re
from collections import Counter
from itertools import permutations

import solver

# The submitted system runs only the wrong-name repair (F2). F1 (station/time swap) is kept for
# the ablation, where it is measured.
SYSTEM_FIXES = frozenset({"F2"})

TERM_FIELDS = ("who", "a", "b", "mid")


def _kind(v, item):
    if v in item["people"]:
        return "person"
    if v in item["stations"]:
        return "station"
    if v in item["blocks"]:
        return "block"
    return None


def _values(f):
    return [(k, f[k]) for k in ("who", "person", "station", "block", "a", "b", "mid") if k in f]

# ------------------------------------------------------------------ C2 shape sanity

def c2_shape(f, item):
    """Reason string if the fact can't be right, else None."""
    r = f.get("rel")
    if r not in solver.ARGS:
        return "unknown relation"
    if any(f.get(k) is None for k in solver.ARGS[r]):
        return "missing field"
    if solver.check_fact(f, item)[0] is None:
        return "value outside vocabulary"
    if r in ("before", "immediately_before") and f["a"] == f["b"]:
        return "relates a term to itself"
    if r == "between" and (f["mid"] in (f["a"], f["b"]) or f["a"] == f["b"]):
        return "between with repeated terms"
    if r == "on" and f["person"] not in item["holders"]:
        return "station for someone with no station"
    return None

# ------------------------------------------------------------------ F1 type fix

def f1_type_fix(f, item):
    r = f.get("rel")
    if r in ("at", "not_at") and f.get("block") in item["stations"] and f.get("who") in item["people"]:
        return {"rel": "on" if r == "at" else "not_on", "person": f["who"], "station": f["block"]}
    if r in ("on", "not_on") and f.get("station") in item["blocks"] and f.get("person") in item["people"]:
        return {"rel": "at" if r == "on" else "not_at", "who": f["person"], "block": f["station"]}
    return None

# ------------------------------------------------------------------ C3 name in line / F2

def mentioned(text, item):
    """Vocabulary items that appear in the line (whole-word, case-insensitive)."""
    out = set()
    for v in item["people"] + item["stations"] + item["blocks"]:
        if re.search(r"(?<![\w:])" + re.escape(v) + r"(?![\w:])", text, flags=re.IGNORECASE):
            out.add(v)
    return out


def c3_stray(f, text, item):
    """Values the fact uses that its line never mentions."""
    present = mentioned(text, item)
    return [(k, v) for k, v in _values(f) if isinstance(v, str) and _kind(v, item) and v not in present]


def f2_name_fix(f, text, item):
    """Swap the one stray value for the one unused mentioned value of the same kind; else None."""
    stray = c3_stray(f, text, item)
    if len(stray) != 1:
        return None
    k, v = stray[0]
    used = {x for _, x in _values(f)}
    spare = [m for m in mentioned(text, item) if m not in used and _kind(m, item) == _kind(v, item)]
    if len(spare) != 1:
        return None
    g = dict(f); g[k] = spare[0]
    return g

# ------------------------------------------------------------------ C6 guarantees (bitsets)

class Worlds:
    """All 720 worlds; a fact becomes an int bitmask of the worlds where it holds."""

    def __init__(self, item):
        self.item = item
        self.w = []
        for bperm in permutations(range(len(item["blocks"])), len(item["people"])):
            block = dict(zip(item["people"], bperm))
            for sperm in permutations(item["stations"], len(item["holders"])):
                station = dict(zip(item["holders"], sperm))
                self.w.append((block, station, {s: p for p, s in station.items()}))
        self.ALL = (1 << len(self.w)) - 1
        self._cache = {}

    def mask(self, f):
        clean, _ = solver.check_fact(f, self.item)
        if clean is None:          # the solver drops invalid facts, so: no constraint
            return self.ALL
        key = tuple(sorted((k, v) for k, v in clean.items()))
        if key not in self._cache:
            if "block" in clean:
                clean["_bi"] = self.item["blocks"].index(clean["block"])
            m = 0
            for i, (b, s, h) in enumerate(self.w):
                if solver.holds(clean, b, s, h):
                    m |= 1 << i
            self._cache[key] = m
        return self._cache[key]


def guarantee_problem(line_masks, ALL):
    """None if the brief's guarantees hold, else a short reason."""
    full = ALL
    for m in line_masks:
        full &= m
    n = bin(full).count("1")
    if n > 4:
        return f"{n} solutions"
    if n >= 1:
        return None
    if any(m == 0 for m in line_masks):
        return "a line is impossible on its own"
    for m1, m2 in itertools.combinations(line_masks, 2):
        if m1 & m2 == 0:
            return "two lines contradict on their own"
    return None

# ------------------------------------------------------------------ pipeline

def apply(item, opts):
    """opts: subset of {"F1", "F2"}. Checks C2/C3/C6 always run; fixes act only when on.
    Returns (item, log) where log counts what fired and what was changed."""
    log = Counter()
    notes = []
    try:
        it = copy.deepcopy(item)
        it["lines"]
        it["people"], it["blocks"], it["stations"], it["holders"]
    except Exception:
        return item, log
    suspects = set()
    for li, l in enumerate(it["lines"]):
        text = l.get("text") or ""
        new_facts = []
        for fi, f in enumerate(l.get("facts", [])):
            if not isinstance(f, dict):
                continue
            if "F1" in opts:
                g = f1_type_fix(f, it)
                if g:
                    log["F1 type fixed"] += 1; notes.append(f"line {l['n']}: type fix {f['rel']}->{g['rel']}"); f = g
            stray = c3_stray(f, text, it)
            if stray:
                log["C3 name not in line"] += 1
                if "F2" in opts:
                    g = f2_name_fix(f, text, it)
                    if g:
                        log["F2 name swapped"] += 1; notes.append(f"line {l['n']}: swapped {stray[0][1]}"); f = g
                    else:
                        log["F2 dropped"] += 1; notes.append(f"line {l['n']}: dropped (names {stray})"); continue
            if c2_shape(f, it):
                log["C2 bad shape"] += 1
            if c3_stray(f, text, it) or c2_shape(f, it):
                suspects.add((li, len(new_facts)))
            new_facts.append(f)
        l["facts"] = new_facts
    try:
        W = Worlds(it)
        masks = [_and(W, l["facts"]) for l in it["lines"] if l["facts"]]
        if guarantee_problem(masks, W.ALL):
            log["C6 guarantee failed"] += 1
    except Exception:
        pass
    it["_repair_notes"] = notes
    return it, log


def _and(W, facts):
    m = W.ALL
    for f in facts:
        m &= W.mask(f)
    return m
