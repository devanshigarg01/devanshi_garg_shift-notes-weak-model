#!/usr/bin/env python3
"""Budgets above 1x. Extra calls are spent on more reads, and code decides which lines need
another look.

Prompts: A is the 1x prompt; B and C differ only in output format. A repeat of the same prompt
repeats the same mistakes (a 2nd same-prompt read disagreed with 35% of read 1's errors, a
differently formatted one with 59%), so the extra reads use different prompts.

3x  (3 calls)
  calls 1-3  full reads with A, B and C; the repairs on each; per-line majority vote (ties go
             to A). Errors are fixed by agreement: the model is never asked to correct itself.

10x (at most 8 of 10 calls)
  calls 1-3  as 3x
  calls 4-8  one specialised call per kind of problem, made only if that kind has lines:
             names    - some read used a name/time/station not in the line, or an impossible fact
             missed   - the vote says "none" but some read found a fact, or A's own restatement
                        names people but it gave no fact
             order    - reads agree on the relation but not on who is first / in the middle
             kind     - reads disagree on what kind of statement the line is
             contradiction - after merging the above, two lines still cannot both hold
             Merging: where code found a concrete problem (a name not in the line, an impossible
             fact, restatement but no fact) and the re-read is a fact that passes the checks, it
             replaces the reading; otherwise it is one more vote and a tie keeps the majority.
             Contradiction answers replace.

Ablation switches (the defaults are the submitted system):
  fixes  repairs run inside the pipeline (F1 = station/time swap, F2 = wrong-name)
  mode   3x: "vote" (submitted) or "followup" (reads A and B, then one re-ask of the lines they
         disagree on or a check flags)
  skip   {"followup"} at 3x; any of names/missed/order/kind/contradiction at 10x
  cfgs   the same prompt listed three times = three reads of one prompt

A call that fails outright (API error, unparseable) is skipped; if read A fails, the next
successful read takes its place.
"""
import copy
import itertools
import json
from collections import Counter

import extract
import repair

ALL_FIXES = repair.SYSTEM_FIXES          # the repairs the submitted system runs (wrong-name repair)

SHAPE_TEXT = {
    "unknown relation": "its relation is not one of the allowed ones",
    "missing field": "it is missing a field",
    "value outside vocabulary": "it uses a value that is not a name, time or station from the header "
                                "(check that stations and times are in the right fields)",
    "relates a term to itself": "it puts someone before or next to themselves",
    "between with repeated terms": "its 'between' repeats the same name",
    "station for someone with no station": "it gives a station to someone who has no station",
}

# ------------------------------------------------------------------ code checks per line

def check_problems(fitem, entries=None, fixes=ALL_FIXES):
    """{line index: problem} for C2 / C3 failures (after F1) and say + none."""
    out = {}
    entries = entries or {}
    for li, l in enumerate(fitem["lines"]):
        say = str((entries.get(l["n"]) or {}).get("say") or "")
        if not l["facts"] and say and not say.strip().lower().startswith("nothing"):
            named = sorted(repair.mentioned(say, fitem))
            if named:
                out[li] = (f'the summary "{say}" describes {", ".join(named)}, but the answer was "none"; '
                           f"if the line states a fact, give it")
                continue
        probs = []
        for f in l["facts"]:
            g = (repair.f1_type_fix(f, fitem) if "F1" in fixes else None) or f
            stray = repair.c3_stray(g, l.get("text") or "", fitem)
            if stray:
                probs.append(f"it uses {', '.join(repr(v) for _, v in stray)}, which this line does not mention")
            shape = repair.c2_shape(g, fitem)
            if shape:
                probs.append(SHAPE_TEXT.get(shape, shape))
        if probs:
            out[li] = "; ".join(dict.fromkeys(probs))
    return out


suspects = check_problems  # name kept for older callers


def passes_checks(facts, line, fitem, fixes=ALL_FIXES):
    """True if none of these facts fails C2 or C3 (after F1) for this line."""
    for f in facts:
        g = (repair.f1_type_fix(f, fitem) if "F1" in fixes else None) or f
        if repair.c3_stray(g, line.get("text") or "", fitem) or repair.c2_shape(g, fitem):
            return False
    return True


def reading(facts):
    """Comparable form of a line's facts."""
    return tuple(sorted(json.dumps(f, sort_keys=True) for f in facts))


def show(facts):
    if not facts:
        return '"none"'
    return " + ".join(json.dumps(extract.render_fact(f, True)) for f in facts)


def contradiction_lines(fitem, fixes=ALL_FIXES):
    """Line indices in a single-line or two-line contradiction (C6), after the repairs in `fixes`."""
    it = repair.apply(fitem, set(fixes))[0]
    W = repair.Worlds(it)
    masks = {li: repair._and(W, l["facts"]) for li, l in enumerate(it["lines"]) if l["facts"]}
    full = W.ALL
    for m in masks.values():
        full &= m
    if full:
        return set()
    bad = {li for li, m in masks.items() if m == 0}
    for (a, ma), (b, mb) in itertools.combinations(masks.items(), 2):
        if ma & mb == 0:
            bad |= {a, b}
    return bad

# ------------------------------------------------------------------ model calls

class Item:
    """Call bookkeeping for one item: never more than `cap` calls."""

    def __init__(self, item, cap):
        self.item, self.cap = item, cap
        self.calls, self.raws, self.asked = 0, [], []

    def left(self):
        return self.cap - self.calls

    def read(self, cfg):
        """Full read with prompt cfg -> (facts item, {n: entry}) or (None, {})."""
        raw = extract.call(self.item, cfg)
        self.calls += 1; self.raws.append(raw)
        obj = extract.to_json(raw)
        if obj is None:
            return None, {}
        facts = extract.to_facts_item(self.item, obj, cfg)
        return facts, {e.get("n"): e for e in (obj.get("lines") or []) if isinstance(e, dict)}

    def ask(self, cfg, facts, intro, msgs, tag):
        """One targeted call: re-read only the lines in msgs {li: text}. Returns {li: facts}."""
        vocab, lines = extract.code_header(self.item)
        out = ["", intro, "Re-read ONLY these lines and give one entry for each, in the same format "
               '(use "none" if a line states no fact):']
        for li, m in msgs.items():
            out.append(f"- Line {facts['lines'][li]['n']}. {m}")
        nums = ", ".join(str(facts["lines"][li]["n"]) for li in msgs)
        out.append(f'Reply with JSON only: {{"lines": [...]}} with one entry for each of lines {nums}, and nothing else.')
        prompt = extract.build_prompt(self.item, extract.item_cfg(self.item, cfg), vocab, lines) + "\n" + "\n".join(out)
        raw = extract.call(self.item, cfg, prompt=prompt)
        self.calls += 1; self.raws.append(raw)
        self.asked.append({"type": tag, "lines": [facts["lines"][li]["n"] for li in msgs]})
        obj = extract.to_json(raw) or {}
        by_n = {e.get("n"): e for e in (obj.get("lines") or []) if isinstance(e, dict)}
        c = extract.item_cfg(self.item, cfg)
        return {li: extract.entry_facts(by_n[facts["lines"][li]["n"]], c)
                for li in msgs if facts["lines"][li]["n"] in by_n}


def _reads(st, cfgs, fixes=ALL_FIXES):
    """Full reads with each cfg, skipping failures. Returns [(cfg, repaired facts, raw facts, entries)].
    The same cfg twice is a repeat read of the same prompt (a fresh sample at temperature 1.0)."""
    out = []
    for cfg in cfgs:
        if not st.left():
            break
        facts, ent = st.read(cfg)
        if facts is not None and "lines" in facts:
            out.append((cfg, repair.apply(facts, set(fixes))[0], facts, ent))
    return out


def _vote(reads):
    """Per-line majority over the (repaired) reads; ties go to the earliest read.
    Returns (voted facts item, options per line, vote counts per line)."""
    voted = copy.deepcopy(reads[0][1])
    options, counts = [], []
    for li in range(len(voted["lines"])):
        opts = [r[1]["lines"][li]["facts"] for r in reads]
        cnt = Counter(reading(o) for o in opts)
        counts.append(cnt)
        best = max(cnt.values())
        voted["lines"][li]["facts"] = copy.deepcopy(next(o for o in opts if cnt[reading(o)] == best))
        uniq = []
        for o in opts:
            if reading(o) not in {reading(u) for u in uniq}:
                uniq.append(o)
        options.append(uniq)
    return voted, options, counts


def _result(st, first, stages, stage_calls=None):
    sc = {"1x": 1, **(stage_calls or {})}
    for k in stages:
        sc.setdefault(k, st.calls)
    return {"stages": {"1x": first, **stages}, "stage_calls": sc, "calls": st.calls, "raws": st.raws,
            "asked": st.asked}


FAILED = {"_error": "unparseable model output"}


def _first(st, reads, A):
    """What the 1x pipeline would have produced: call 1 (prompt A) alone."""
    if reads and reads[0][0] is A and extract.to_json(st.raws[0]) is not None:
        return reads[0][2]
    return FAILED

# ------------------------------------------------------------------ 3x

def run_3x(item, cfgs, fixes=ALL_FIXES, mode="vote", skip=()):
    """mode "vote" (submitted): three full reads (A, B, C) and a per-line majority, no re-ask.
    mode "followup" (ablation): reads A and B, then one call on lines where they disagree or a check fires.
    skip: {"followup"} drops the follow-up call (the two reads then only count where they agree)."""
    st = Item(item, 3)
    A = cfgs[0]
    if mode == "vote":
        reads = _reads(st, cfgs[:3], fixes)
        first = _first(st, reads, A)
        if not reads:
            return _result(st, first, {"3x": FAILED})
        if (not extract.item_cfg(item, reads[0][0])["code_parse"]
                or any(len(r[1]["lines"]) != len(reads[0][1]["lines"]) for r in reads)):
            return _result(st, first, {"3x": reads[0][2]})
        return _result(st, first, {"3x": _vote(reads)[0]})
    reads = _reads(st, cfgs[:2], fixes)
    first = _first(st, reads, A)
    if not reads:                                   # both failed: last call is a fresh full read
        reads = _reads(st, [A], fixes)
    if not reads:
        return _result(st, first, {"3x": FAILED})
    cfg0, fixed0, raw0, ent0 = reads[0]
    if not extract.item_cfg(item, cfg0)["code_parse"]:   # header fallback: no line-level re-asks
        return _result(st, first, {"3x": raw0})
    final = copy.deepcopy(raw0)
    msgs = {li: f"Problem: {p}." for li, p in check_problems(raw0, ent0, fixes).items()}
    if len(reads) > 1 and len(reads[1][1]["lines"]) == len(fixed0["lines"]):
        fixed1 = reads[1][1]
        for li, (l0, l1) in enumerate(zip(fixed0["lines"], fixed1["lines"])):
            if reading(l0["facts"]) != reading(l1["facts"]):
                extra = f" {msgs[li]}" if li in msgs else ""
                msgs[li] = (f"Two readings disagree: (1) {show(raw0['lines'][li]['facts'])} "
                            f"(2) {show(fixed1['lines'][li]['facts'])}.{extra}")
    if msgs and st.left() and "followup" not in skip:
        new = st.ask(A, final, "Some earlier readings of these lines have a problem or disagree with each other.",
                     msgs, "3x")
        for li, fs in new.items():
            final["lines"][li]["facts"] = fs
    return _result(st, first, {"3x": final})

# ------------------------------------------------------------------ 10x

INTRO = {
    "names": "Earlier readings of these lines used a name, time or station that the line itself does not "
             "mention, or an impossible combination. Use only what is written in each line.",
    "missed": "For each line below, decide whether it states a CURRENT fact about who works which time or which "
              "station. If it does, give that fact. Use \"none\" only if the line is about the past, something "
              "unknown, a request or wish, a hypothetical, or something unrelated.",
    "order": "Each line below states an order. Earlier readings disagreed about who comes first, or who is in the "
             "middle. Read each line slowly; in \"say\" write who comes first, then fill the fields to match.",
    "kind": "Earlier readings disagreed about what kind of statement each line below is (a time, a station, an "
            "order, ...). Read each line again and choose the relation that matches it.",
    "contradiction": "As currently read, the lines below cannot all be true at once, so at least one was read "
                     "wrongly. Read each again carefully and correct any misread one; if a reading was right, "
                     "give it again.",
}
ORDER_RELS = {"before", "immediately_before", "between"}


def _rels(facts):
    return frozenset(f.get("rel") for f in facts)


def run_10x(item, cfgs, cap=10, fixes=ALL_FIXES, skip=()):
    """skip: any of "names", "missed", "order", "kind", "contradiction" -- that call is not made."""
    A = cfgs[0]
    st = Item(item, cap)
    reads = _reads(st, cfgs[:3], fixes)
    first = _first(st, reads, A)
    if not reads:
        return _result(st, first, {"vote": FAILED, "10x": FAILED})
    cfg0, _, raw0, ent0 = reads[0]
    if (not extract.item_cfg(item, cfg0)["code_parse"]
            or any(len(r[1]["lines"]) != len(reads[0][1]["lines"]) for r in reads)):
        return _result(st, first, {"vote": raw0, "10x": raw0})

    # per-line majority vote over the repaired readings; ties go to the earliest read (A first)
    voted, options, counts = _vote(reads)
    stages = {"vote": copy.deepcopy(voted)}
    vote_calls = st.calls

    # code-check problems on each raw read (say + none only for A, the only one with "say")
    probs = {}
    for cfg, _, raw, ent in reads:
        for li, p in check_problems(raw, ent if cfg is A else None, fixes).items():
            probs.setdefault(li, p)

    # each line goes to at most one error type (first match wins)
    groups = {k: {} for k in ("names", "missed", "order", "kind")}
    for li, opts in enumerate(options):
        listed = " ".join(f"({i + 1}) {show(o)}" for i, o in enumerate(opts))
        seen = f"Earlier readings: {listed}." if len(opts) > 1 else f"Earlier reading: {show(opts[0])}."
        cur = voted["lines"][li]["facts"]
        p = probs.get(li)
        if p and "summary" not in p:
            groups["names"][li] = f"{seen} Problem: {p}."
        elif not cur and (len(opts) > 1 or p):
            groups["missed"][li] = seen + (f" Problem: {p}." if p else "")
        elif len(opts) > 1 and all(opts) and len({_rels(o) for o in opts}) == 1 and _rels(opts[0]) <= ORDER_RELS:
            groups["order"][li] = seen
        elif len(opts) > 1:
            groups["kind"][li] = seen

    # Merging a targeted answer T: where code found a concrete problem on the line (C2/C3,
    # say + none) and T is a fact that passes the checks, T replaces the reading. Otherwise T
    # counts as one more vote and a tie keeps the earlier majority, so a re-ask that repeats a
    # minority mistake cannot overturn a correct majority.
    final = voted
    for tag in ("names", "missed", "order", "kind"):
        if groups[tag] and tag not in skip and st.left():
            for li, fs in st.ask(A, final, INTRO[tag], groups[tag], tag).items():
                if li in probs and fs and passes_checks(fs, final["lines"][li], final, fixes):
                    final["lines"][li]["facts"] = fs
                    continue
                cur = reading(final["lines"][li]["facts"])
                counts[li][reading(fs)] += 1
                if counts[li][reading(fs)] > counts[li][cur]:
                    final["lines"][li]["facts"] = fs
    bad = contradiction_lines(final, fixes) if "contradiction" not in skip else set()
    if bad and st.left():
        msgs = {li: f"Current reading: {show(final['lines'][li]['facts'])}." for li in sorted(bad)}
        for li, fs in st.ask(A, final, INTRO["contradiction"], msgs, "contradiction").items():
            final["lines"][li]["facts"] = fs
    stages["10x"] = final
    return _result(st, first, stages, {"vote": vote_calls})


def run_item(item, cfgs, budget, fixes=ALL_FIXES, mode="vote", skip=()):
    """One item at 3x or 10x. fixes: repairs run inside the pipeline. mode/skip: ablation switches."""
    if budget == "3x":
        return run_3x(item, cfgs, fixes, mode, skip)
    if budget == "10x":
        return run_10x(item, cfgs, 10, fixes, skip)
    raise ValueError(budget)
