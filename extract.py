"""One read of the notes: Granite turns every line into one structured fact (or "none").

Code reads the item's header (names, blocks, stations, who holds a station) and numbers the
lines; the model only has to say what each numbered line states. If code can't read a header,
that item falls back to the model reading the header itself.

A prompt is described by four switches:
    roles     field names that carry meaning (earlier/later, first/then, middle/ends) instead of a/b/mid
    restate   each entry starts with "say": the line in plain words, before the structured fields
    examples  include worked examples
    shots     how many worked examples (None = all 12, 1 = one)
The 1x prompt is PROMPT_A. B and C differ in output format; 3x/10x read with A, B and C.
"""
import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = "ibm-granite/granite-4.2-8b"

PROMPT_A = {"roles": True, "restate": True, "examples": True}    # the 1x prompt
PROMPT_B = {"roles": False, "restate": True, "examples": True}   # plain field names a/b
PROMPT_C = {"roles": True, "restate": False, "examples": True}   # no restatement


def prompts(shots="few", restate=True, roles=True, reads="diff"):
    """The prompts a pipeline reads with. shots: few | one | zero. reads: diff (A, B, C),
    same (A three times) or one (A once). The switches are for ablations."""
    def t(c):
        c = dict(c)
        if shots == "zero":
            c["examples"] = False
        elif shots == "one":
            c["shots"] = 1
        if not restate:
            c["restate"] = False
        if not roles:
            c["roles"] = False
        return c
    A, B, C = t(PROMPT_A), t(PROMPT_B), t(PROMPT_C)
    return {"diff": [A, B, C], "same": [A, A, A], "one": [A]}[reads]

# ---------------------------------------------------------------- header (code)

FIELDS = {
    "at": ("who", "block"), "not_at": ("who", "block"),
    "on": ("person", "station"), "not_on": ("person", "station"),
    "before": ("a", "b"), "immediately_before": ("a", "b"),
    "between": ("mid", "a", "b"), "none": (),
}


def parse_header(text):
    head, _, body = text.partition("\n\n")
    grab = lambda pat: [x.strip() for x in re.search(pat, head).group(1).split(",")]
    vocab = {"people": grab(r"staff on the rota:\s*(.+?)\.\s"),
             "blocks": grab(r"Blocks run\s*(.+?),\s*one person per block"),
             "stations": grab(r"one person on each:\s*(.+?)\.\s"),
             "holders": grab(r"people on a station are\s*(.+?);")}
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    return vocab, lines


def code_header(item):
    """Code reads the header, then checks it is self-consistent. Returns (vocab, lines) or None."""
    try:
        vocab, lines = parse_header(item["text"])
    except Exception:
        return None
    p, b, s_, h = vocab["people"], vocab["blocks"], vocab["stations"], vocab["holders"]
    ok = (len(p) >= 2 and len(p) == len(b) == len(set(p)) and len(set(b)) == len(b)
          and 1 <= len(h) == len(s_) == len(set(h)) and len(set(s_)) == len(s_) and set(h) <= set(p)
          and all(x for x in p + b + s_) and len(lines) > 0)
    if item.get("n_staff") is not None and len(p) != item["n_staff"]:
        ok = False
    if item.get("n_stations") is not None and len(s_) != item["n_stations"]:
        ok = False
    return (vocab, lines) if ok else None


def item_cfg(item, cfg):
    """The prompt actually used for this item: if code can't read the header, the model reads it."""
    return {**cfg, "code_parse": code_header(item) is not None}

# ---------------------------------------------------------------- prompt text

RELATIONS = """A TERM is a person's name, or a station name meaning "whoever is on that station".
- at: {"who": TERM, "block": BLOCK}                  TERM is on that block
- not_at: {"who": TERM, "block": BLOCK}              TERM is not on that block
- on: {"person": PERSON, "station": STATION}         PERSON is on that station
- not_on: {"person": PERSON, "station": STATION}     PERSON is not on that station
- before: {"a": TERM, "b": TERM}                     a's block is earlier than b's (any gap)
- immediately_before: {"a": TERM, "b": TERM}         a's block comes right before b's
- between: {"mid": TERM, "a": TERM, "b": TERM}       mid's block is between a's and b's, in either order
BLOCK is a time like "09:00". STATION is a station name. Never put a station where a BLOCK goes, or a time where a STATION goes."""

RELATIONS_ROLES = """A TERM is a person's name, or a station name meaning "whoever is on that station".
- at: {"who": TERM, "block": BLOCK}                  TERM is on that block
- not_at: {"who": TERM, "block": BLOCK}              TERM is not on that block
- on: {"person": PERSON, "station": STATION}         PERSON is on that station
- not_on: {"person": PERSON, "station": STATION}     PERSON is not on that station
- before: {"earlier": TERM, "later": TERM}           the earlier one's block comes before the later one's (any gap)
- immediately_before: {"first": TERM, "then": TERM}  "then" is on the very next block after "first"
- between: {"middle": TERM, "ends": [TERM, TERM]}    middle's block is between the two ends, in either order
BLOCK is a time like "09:00". STATION is a station name. Never put a station where a BLOCK goes, or a time where a STATION goes."""

HEDGES = """Hedges such as "I'm fairly sure", "As far as I know" or "It bears repeating:" do not weaken a line. Translate it as if the hedge were not there."""

HINTS = """These lines state NO fact:
- anything about the past or an old arrangement ("last month", "back in the old arrangement", "on the previous cycle", "in the spring")
- anything uncertain or undecided ("nobody could remember whether", "was left open", "some disagreement about")
- requests or wishes ("lobbied for", "wanted ... and did not get it", "put in for ... and was turned down")
- hypotheticals ("would have been a better fit", "if X had taken", "had the rota gone the other way")
- chatter unrelated to blocks or stations (car-sharing, surveys, training, keys, deliveries)"""

RESTATE = """Start every entry with "say": a few plain words saying what the line means, e.g. "Frank first, then Eve later", "Carla somewhere between Alice and Dan", "Alice's station is not calibration", or "nothing: a wish". Then give "rel" and its fields so they match exactly what you said."""

# (sentence, fact in solver form, plain restatement, note)
EXAMPLE_DATA = [
    ("Put Carla between Alice and Dan, though not necessarily next to either.", {"rel": "between", "mid": "Carla", "a": "Alice", "b": "Dan"}, "Carla somewhere between Alice and Dan", ""),
    ("Whichever way round Alice and Dan are, Carla is between them.", {"rel": "between", "mid": "Carla", "a": "Alice", "b": "Dan"}, "Carla somewhere between Alice and Dan", ""),
    ("Carla is on after one of Alice and Dan and before the other.", {"rel": "between", "mid": "Carla", "a": "Alice", "b": "Dan"}, "Carla somewhere between Alice and Dan", ""),
    ("Frank is on later than whoever has packing.", {"rel": "before", "a": "packing", "b": "Frank"}, "packing's person first, Frank later", ""),
    ("The calibration station is covered earlier in the day than Eve's block.", {"rel": "before", "a": "calibration", "b": "Eve"}, "calibration's person first, Eve later", ""),
    ("Whoever has intake is done before Bob starts.", {"rel": "before", "a": "intake", "b": "Bob"}, "intake's person first, Bob later", ""),
    ("Bob relieves Alice directly, with no block in between.", {"rel": "immediately_before", "a": "Alice", "b": "Bob"}, "Alice, then Bob right after", "(Alice first, then Bob)"),
    ("Dan takes over from Carla later in the day.", {"rel": "before", "a": "Carla", "b": "Dan"}, "Carla first, Dan later", ""),
    ("There is no block between Eve's and Bob's, in that order.", {"rel": "immediately_before", "a": "Eve", "b": "Bob"}, "Eve, then Bob right after", ""),
    ("Alice is not assigned to calibration.", {"rel": "not_on", "person": "Alice", "station": "calibration"}, "Alice's station is not calibration", "(a station, so not_on, never not_at)"),
    ("You will not find Bob on the 15:00 block.", {"rel": "not_at", "who": "Bob", "block": "15:00"}, "Bob's time is not 15:00", ""),
    ("Calibration is covered by Carla.", {"rel": "on", "person": "Carla", "station": "calibration"}, "Carla's station is calibration", ""),
]


def render_fact(f, roles):
    """Solver-form fact -> the field names the prompt asks for."""
    if not roles:
        return dict(f)
    r = f["rel"]
    if r == "before":
        return {"rel": r, "earlier": f["a"], "later": f["b"]}
    if r == "immediately_before":
        return {"rel": r, "first": f["a"], "then": f["b"]}
    if r == "between":
        return {"rel": r, "middle": f["mid"], "ends": [f["a"], f["b"]]}
    return dict(f)


def examples_text(cfg):
    shots = cfg.get("shots")
    data = EXAMPLE_DATA[:shots] if shots else EXAMPLE_DATA
    out = [("Example" if shots == 1 else "More examples") + " (made-up names):"]
    for sent, f, say, note in data:
        fields = ", ".join(f"{k} {' and '.join(v) if isinstance(v, list) else v}"
                           for k, v in render_fact(f, cfg.get("roles")).items() if k != "rel")
        prefix = f'say "{say}"; ' if cfg.get("restate") else ""
        out.append(f'"{sent}" -> {prefix}{f["rel"]}, {fields}' + (f"   {note}" if note else ""))
    return "\n".join(out)


def output_shape(cfg):
    demo = [("Alice is on the 09:00 block.", {"rel": "at", "who": "Alice", "block": "09:00"}, "Alice's time is 09:00"),
            ("The person on packing works earlier in the day than Dan.", {"rel": "before", "a": "packing", "b": "Dan"},
             "packing's person first, Dan later"),
            ("The coffee machine is broken again.", None, "nothing: chatter")]
    entries = []
    for n, (text, f, say) in enumerate(demo, 1):
        e = {"n": n}
        if cfg.get("restate"):
            e["say"] = say
        e.update(render_fact(f, cfg.get("roles")) if f else {"rel": "none"})
        if not cfg["code_parse"]:
            e["text"] = text
        entries.append(json.dumps(e))
    head = '"people": [...], "blocks": [...], "stations": [...], "holders": [...], ' if not cfg["code_parse"] else ""
    return "{" + head + '"lines": [\n  ' + ",\n  ".join(entries) + "\n]}"


def build_prompt(item, cfg, vocab=None, lines=None):
    """cfg must come from item_cfg (it says whether code read the header)."""
    parts = ["Convert these shift notes into structured JSON.", ""]
    if cfg["code_parse"]:
        parts += [f"People: {', '.join(vocab['people'])}",
                  f"Blocks (earliest first): {', '.join(vocab['blocks'])}",
                  f"Stations: {', '.join(vocab['stations'])}",
                  f"People on a station: {', '.join(vocab['holders'])}", ""]
    else:
        parts += ["From the first paragraph (the header), copy:",
                  '- "people": the staff names', '- "blocks": the block times, earliest first',
                  '- "stations": the station names', '- "holders": the people who are on a station', "",
                  "Then number every line of the notes after the header, starting at 1.", ""]
    parts += ['For EVERY numbered line give exactly one entry with "n" (its number) and "rel" (the relation it states), plus the fields for that relation.',
              'Choose "rel" from the list below, or "none" if the line states nothing about blocks or stations.']
    if not cfg["code_parse"]:
        parts += ['Also give "text": the line copied exactly, word for word.']
    parts += ["", RELATIONS_ROLES if cfg.get("roles") else RELATIONS, "", HEDGES]
    if cfg.get("restate"):
        parts += ["", RESTATE]
    parts += ["", HINTS]
    if cfg.get("examples"):
        parts += ["", examples_text(cfg)]
    parts += ["", "Output shape:", output_shape(cfg), "", "Reply with the JSON only."]
    if cfg["code_parse"]:
        parts += ["", "Lines:"] + [f"{i}. {l}" for i, l in enumerate(lines, 1)]
    else:
        parts += ["", "Shift notes:", item["text"]]
    return "\n".join(parts)

# ---------------------------------------------------------------- model call

RETRIES_429 = 5          # rate-limited requests are retried with backoff (see README)
STATS = Counter()        # calls, rate limits, errors, shared replies (for progress output)
_client = None

# Ablation only (./run never sets it): replies shared by all systems within ONE ablation run, so
# systems that differ only after the reads see the same reads (a paired comparison). Keyed by
# (item, exact prompt, n-th read of that prompt); reset for every run; never written to disk.
SHARED = None
_shared_lock = threading.Lock()


ERRORS = []              # failed calls: (item id, error), for the run summary


def _load_env():
    """Read .env next to this file if python-dotenv is installed; plain environment variables
    work either way."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(HERE, ".env"))
    except ImportError:
        pass


def check_setup():
    """None if a model call can be made, else a message saying what is missing."""
    _load_env()
    try:
        import openai  # noqa: F401
    except ImportError:
        return "the openai package is not installed: pip3 install -r requirements.txt"
    missing = [k for k in ("OPENROUTER_BASE_URL", "OPENROUTER_API_KEY") if not os.environ.get(k)]
    if missing:
        return (f"{' and '.join(missing)} not set: copy .env.example to .env and fill it in "
                f"(or export the variables)")
    return None


def client():
    global _client
    if _client is None:
        from openai import OpenAI
        _load_env()
        _client = OpenAI(base_url=os.environ["OPENROUTER_BASE_URL"], api_key=os.environ["OPENROUTER_API_KEY"],
                         max_retries=0)   # the SDK's own retries would be hidden extra calls
    return _client


class Limiter:
    """Adaptive concurrency: at most `limit` calls in flight. A rate limit (429) halves the limit;
    a streak of successes adds one back, up to `hi`. Changes only how many calls run at once."""

    def __init__(self, hi=8):
        self.limit, self.hi, self.active, self.streak = hi, hi, 0, 0
        self.cv = threading.Condition()

    def __enter__(self):
        with self.cv:
            while self.active >= self.limit:
                self.cv.wait()
            self.active += 1

    def __exit__(self, *exc):
        with self.cv:
            self.active -= 1
            self.cv.notify_all()

    def ok(self):
        with self.cv:
            self.streak += 1
            if self.streak >= 2 * self.limit and self.limit < self.hi:
                self.limit += 1
                self.streak = 0
                self.cv.notify_all()

    def throttled(self):
        with self.cv:
            self.limit = max(1, self.limit // 2)
            self.streak = 0


LIMITER = Limiter(8)     # run.py / ablate.py set hi from --workers


def _request(item, messages):
    """The HTTP call. Only a rate limit (HTTP 429) is retried, with exponential backoff."""
    for attempt in range(RETRIES_429 + 1):
        try:
            with LIMITER:
                resp = client().chat.completions.create(
                    model=os.environ.get("MODEL", DEFAULT_MODEL), messages=messages,
                    temperature=1.0, top_p=0.95,
                    extra_body={"reasoning": {"enabled": False}},
                    extra_headers={"X-Item-Id": item["id"]},
                    response_format={"type": "json_object"})
            LIMITER.ok()
            STATS["calls"] += 1
            return resp.choices[0].message.content or ""
        except Exception as e:
            err = f"ERROR: {e!r}"
            limited = "429" in err or "rate limit" in err.lower()
            if not limited or attempt == RETRIES_429:
                STATS["errors"] += 1
                ERRORS.append((item["id"], err))
                return err
            STATS["rate_limited"] += 1
            LIMITER.throttled()
            time.sleep(min(60, 4 * 2 ** attempt))


def call(item, cfg, prompt=None, sample=0):
    """One model call for this item; returns the reply text, or "ERROR: ..." on failure.
    `prompt` replaces the extraction prompt (used by the targeted re-asks at 3x/10x).
    `sample` = which read of this same prompt it is (0, 1, 2); only matters for SHARED."""
    cfg = item_cfg(item, cfg)
    vocab, lines = code_header(item) if cfg["code_parse"] else (None, None)
    messages = [{"role": "user", "content": prompt or build_prompt(item, cfg, vocab, lines)}]
    if SHARED is None:
        return _request(item, messages)
    key = f'{item["id"]}|{hashlib.sha1(messages[0]["content"].encode()).hexdigest()}|{sample}'
    with _shared_lock:
        if key in SHARED:
            STATS["shared"] += 1
            return SHARED[key]
    text = _request(item, messages)
    if not text.startswith("ERROR:"):
        with _shared_lock:
            SHARED[key] = text
    return text

# ---------------------------------------------------------------- reply -> facts

def to_json(raw):
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    i, j = raw.find("{"), raw.rfind("}")
    try:
        obj = json.loads(raw[i:j + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def clean_fact(f):
    """Keep only the fields the relation uses; "none" -> no fact. Role-named fields
    (earlier/later, first/then, middle/ends) are mapped back to the solver's a/b/mid."""
    if not isinstance(f, dict) or f.get("rel") in (None, "none"):
        return None
    f = dict(f)
    r = f["rel"]
    if r == "before":
        f.setdefault("a", f.get("earlier")); f.setdefault("b", f.get("later"))
    elif r == "immediately_before":
        f.setdefault("a", f.get("first")); f.setdefault("b", f.get("then"))
    elif r == "between":
        f.setdefault("mid", f.get("middle"))
        ends = f.get("ends")
        if isinstance(ends, list) and len(ends) == 2:
            f.setdefault("a", ends[0]); f.setdefault("b", ends[1])
    return {"rel": r, **{k: f.get(k) for k in FIELDS.get(r, ())}}


def entry_facts(e, cfg=None):
    """One line entry from the model -> list of solver-form facts (0 or 1)."""
    f = clean_fact(e) if isinstance(e, dict) else None
    return [f] if f else []


def to_facts_item(item, obj, cfg):
    """Model JSON -> the solver's facts format."""
    cfg = item_cfg(item, cfg)
    entries = obj.get("lines") if isinstance(obj.get("lines"), list) else []
    if cfg["code_parse"]:
        vocab, lines = code_header(item)
        by_n = {e.get("n"): e for e in entries if isinstance(e, dict)}
        return {**vocab, "lines": [{"n": i, "text": t, "facts": entry_facts(by_n.get(i, {}))}
                                   for i, t in enumerate(lines, 1)]}
    out = {k: obj.get(k) for k in ("people", "blocks", "stations", "holders")}
    out["_header_fallback"] = True
    out["lines"] = [{"n": e.get("n"), "text": e.get("text"), "facts": entry_facts(e)}
                    for e in entries if isinstance(e, dict)]
    return out


def read(item, cfg):
    """One full read: (facts item or {"_error": ...}, raw reply)."""
    raw = call(item, cfg)
    obj = to_json(raw)
    return (to_facts_item(item, obj, cfg) if obj is not None else {"_error": "unparseable model output"}), raw


def run_extract(items, cfg, workers=8, progress=None):
    """One read per item, in parallel. Returns (facts_by_id, raw_by_id, n_unparseable, n_api_errors)."""
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(read, it, cfg): it["id"] for it in items}
        done = {}
        for f in as_completed(futs):
            done[futs[f]] = f.result()
            if progress:
                progress()
    facts = {it["id"]: done[it["id"]][0] for it in items}
    raws = {it["id"]: done[it["id"]][1] for it in items}
    return (facts, raws, sum("_error" in v for v in facts.values()),
            sum(r.startswith("ERROR:") for r in raws.values()))
