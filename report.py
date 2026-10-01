#!/usr/bin/env python3
"""Builds the ablation document from ablate.py's results: <name>.md, <name>.tex, and
<name>.pdf when pdflatex is installed. Called by ablate.py at the end of a run; to rebuild
without calls:  python3 ablate.py --report ablation/<name>.json

A cell whose system has no results prints "--", so a partial run still gives a valid document.
"""
import os
import re
import shutil
import statistics
import subprocess

KINDS = ("unique", "ambiguous", "inconsistent")
REASKS = [("names", "name check", "a name, time or station the line does not mention"),
          ("missed", "missed line", "read as no fact, but the restatement names people"),
          ("order", "order", "reads disagree on who comes first"),
          ("kind", "line type", "reads disagree on the kind of statement"),
          ("contradiction", "contradiction", "two lines cannot both hold")]
SHORT_WHY = {"names": "value not in its line", "missed": "``no fact'' but names people",
             "order": "who comes first", "kind": "kind of statement", "contradiction": "lines cannot both hold"}


def mean(xs):
    return statistics.mean(xs) if xs else None


class R:
    def __init__(self, res):
        self.res = res
        self.meta = res.get("meta", {})

    def get(self, name, tag=None, fix=None):
        """Summary of one system (a stage of it, scored with a repair setting), or None."""
        e = self.res["systems"].get(name)
        if not e:
            return None
        runs = e["stages"].get(tag or e["budget"]) or []
        if not runs:
            return None
        recs = [r["fix"][fix or e["fixname"]] for r in runs]
        macro = [100 * x["macro"] for x in recs]
        lines = [x.get("lines") or {} for x in recs]
        out = {"n": len(runs), "macro": mean(macro), "sd": statistics.stdev(macro) if len(macro) > 1 else None,
               "kind": {k: 100 * mean([x["per_case"][k] for x in recs]) for k in KINDS},
               "calls": mean([r.get("calls_mean", 1.0) for r in runs]),
               "conf": {t: {d: mean([x["confusion"][t][d] for x in recs]) for d in KINDS} for t in KINDS},
               "lines": {k: mean([l.get(k, 0) for l in lines]) for k in ("correct", "missed", "wrong", "invented", "total")}
               if all(lines) else None,
               "reask_calls": {k: mean([r.get("reask_calls", {}).get(k, 0) for r in runs]) for k, _, _ in REASKS},
               "reask_lines": {k: mean([r.get("reask_lines", {}).get(k, 0) for r in runs]) for k, _, _ in REASKS}}
        return out

# ------------------------------------------------------------------ formatting

def f1(x):
    return "--" if x is None else f"{x:.1f}"


def pm(s):
    if s is None:
        return "--"
    return f1(s["macro"]) + (f" {{\\scriptsize$\\pm${s['sd']:.1f}}}" if s["sd"] is not None else "")


def m(s):
    return "--" if s is None else f1(s["macro"])


def tex2md(s):
    s = re.sub(r"\\textbf\{([^{}]*)\}", r"**\1**", s)
    s = re.sub(r"\\(textit|emph|scriptsize|footnotesize|small)\{([^{}]*)\}", r"\2", s)
    s = re.sub(r"\{\\scriptsize\$\\pm\$([^{}]*)\}", r"± \1", s)
    for a, b in (("\\footnotesize ", ""), ("$\\rightarrow$", "→"), ("$\\times$", "x"), ("$\\star$", "★"), ("$\\pm$", "±"), ("\\quad", "  "), ("\\%", "%"),
                 ("``", '"'), ("''", '"'), ("$-$", "-"), ("\\,", " ")):
        s = s.replace(a, b)
    return re.sub(r"[{}]", "", s).strip()


def kind_row(label, s, extra=()):
    if s is None:
        return [label] + ["--"] * (5 + len(extra))
    return [label] + [f1(s["kind"][k]) for k in KINDS] + [pm(s), f"{s['calls']:.1f}"] + list(extra)


def line_cells(s):
    L = s and s.get("lines")
    if not L:
        return ["--"] * 3
    return [f"{100 * L['correct'] / L['total']:.1f}", f"{L['missed']:.0f}", f"{L['wrong'] + L['invented']:.0f}"]

# ------------------------------------------------------------------ tables

def main_table(r):
    S1, S3, S10 = r.get("1x"), r.get("3x"), r.get("10x")
    rows = []

    def comp(label, cells):
        out = [label]
        for c in cells:
            out += ["--", "--"] if c is None else [m(c[0]), m(c[1])]
        rows.append(out)

    rows.append(("group", "One read of the notes"))
    comp(r"Code solver \textit{(vs Granite answering directly)}",
         [(S1, r.get("1x-direct")), (S3, None), (S10, None)])
    comp("Worked examples in the prompt",
         [(S1, r.get("1x-zeroshot")), (S3, r.get("3x-zeroshot")), (S10, r.get("10x-zeroshot"))])
    comp("Restate each line in plain words first",
         [(S1, r.get("1x-norestate")), (S3, r.get("3x-norestate")), (S10, r.get("10x-norestate"))])
    rows.append(("group", "Code repair (no model calls)"))
    comp("Wrong-name repair",
         [(S1, r.get("1x", fix="no fixes")), (S3, r.get("3x-norepair")), (S10, r.get("10x-norepair"))])
    rows.append(("group", r"Extra calls, 3$\times$: read three times and vote"))
    comp(r"2nd and 3rd read + per-line vote \textit{(vs one read)}",
         [None, (S3, S1), (S10, r.get("10x-oneread"))])
    comp(r"Reads use different prompts \textit{(vs one prompt $\times$3)}",
         [None, (S3, r.get("3x-sameprompt")), (S10, r.get("10x-sameprompt"))])
    rows.append(("group", r"Extra calls, 10$\times$: re-ask only the lines code flags"))
    comp(r"All targeted re-asks \textit{(vs the 3-read vote alone)}", [None, None, (S10, r.get("10x", tag="vote"))])
    rows.append(("sub", "each kind of re-ask removed on its own:"))
    for k, name, why in REASKS:
        comp(rf"\quad {name} \textit{{\footnotesize -- {SHORT_WHY[k]}}}", [None, None, (S10, r.get(f"10x-no{k}"))])
    full = [pm(S1), pm(S3), pm(S10)]
    calls = [f"{s['calls']:.1f} / {cap}" if s else f"-- / {cap}" for s, cap in ((S1, 1), (S3, 3), (S10, 10))]
    return rows, full, calls


def main_tex(r):
    rows, full, calls = main_table(r)
    body = []
    for row in rows:
        if row[0] == "group":
            body.append(r"\multicolumn{7}{@{}l}{\rule{0pt}{11pt}\textbf{" + row[1] + r"}}\\[1pt]")
        elif row[0] == "sub":
            body.append(r"\multicolumn{7}{@{}l}{\quad\textit{\footnotesize " + row[1] + r"}}\\")
        else:
            body.append(" & ".join(row) + r" \\")
    mc = lambda x: r"\multicolumn{2}{c}{" + x + "}"
    pc = lambda x: r"\multicolumn{2}{>{\centering\arraybackslash}p{2.1cm}}{\footnotesize " + x + "}"
    return r"""\begin{table}[H]
\centering\small\setlength{\tabcolsep}{4pt}\renewcommand{\arraystretch}{1.1}
\begin{tabular}{@{}p{8.4cm} cc cc cc@{}}
\toprule
 & \multicolumn{2}{c}{\textbf{1$\times$}} & \multicolumn{2}{c}{\textbf{3$\times$}} & \multicolumn{2}{c}{\textbf{10$\times$}} \\
\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}
Component & with & without & with & without & with & without \\
\midrule
\rowcolor{band}\textbf{Full system}, macro exact match (\%) & """ + " & ".join(mc(r"\textbf{" + x + "}") for x in full) + r""" \\
\rowcolor{band}\quad model calls per item (mean / cap) & """ + " & ".join(mc(x) for x in calls) + r""" \\
\rowcolor{band}\quad how a misread line gets fixed & """ + " & ".join(pc(x) for x in (
        "code repairs only", "outvoted by two other reads", "code flags it, model re-reads it")) + r""" \\
""" + "\n".join(body) + r"""
\bottomrule
\end{tabular}
\caption{The ablation. Macro exact match (\%), mean of """ + str(r.meta.get("runs", "?")) + r""" runs on """ + str(r.meta.get("items", "?")) + r""" items. Each row removes one component from the system at that budget and keeps everything else; ``with'' is always the full system at that budget. --: the component does not exist at that budget""" + (r" or was not run in this suite (prompt components at 3$\times$/10$\times$ need \texttt{-{}-suite full})" if r.meta.get("suite") == "main" else "") + r""". ``2nd and 3rd read'' at 3$\times$ without = the 1$\times$ system.}
\end{table}
"""


def simple_tex(header, rows, spec, caption):
    body = []
    for row in rows:
        if row and row[0] == "group":
            body.append(r"\multicolumn{" + str(len(header)) + r"}{@{}l}{\textit{" + row[1] + r"}}\\")
        else:
            body.append(" & ".join(row) + r" \\")
    return (r"\begin{table}[H]" "\n" r"\centering\footnotesize\setlength{\tabcolsep}{3.5pt}\renewcommand{\arraystretch}{1.08}" "\n"
            r"\begin{tabular}{@{}" + spec + r"@{}}" "\n" r"\toprule" "\n" + " & ".join(header) + r" \\" "\n"
            r"\midrule" "\n" + "\n".join(body) + "\n" r"\bottomrule" "\n" r"\end{tabular}" "\n"
            r"\caption{" + caption + "}\n" r"\end{table}" "\n")


def md_table(header, rows):
    out = ["| " + " | ".join(tex2md(h) for h in header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        if row and row[0] in ("group", "sub"):
            out.append(f"| *{tex2md(row[1])}* |" + " |" * (len(header) - 1))
        else:
            out.append("| " + " | ".join(tex2md(c) for c in row) + " |")
    return "\n".join(out)


KH = ["", "Unique", "Ambiguous", "Inconsistent", r"\textbf{Macro}", "Calls/item"]
LH = ["Lines right", "Missed", "Wrong"]


def side_tables(r):
    """[(title, header, rows, colspec, caption)]"""
    T = []
    prompts = [(r"$\star$ Few-shot: 12 worked examples, restate each line", r.get("1x")),
               ("One-shot: 1 worked example", r.get("1x-oneshot")),
               ("Zero-shot: rules and hints only", r.get("1x-zeroshot")),
               ("No plain-words restatement", r.get("1x-norestate")),
               (r"Plain field names (\textit{a/b} instead of \textit{earlier/later})", r.get("1x-plainfields")),
               ("Granite answers the whole item directly (no solver)", r.get("1x-direct"))]
    T.append(("Prompt techniques at 1$\\times$", KH + LH,
              [kind_row(l, s, line_cells(s)) for l, s in prompts], "p{5.4cm} ccc c c ccc",
              r"Each row is one 1$\times$ system that differs from the submitted one ($\star$) only in its prompt, all scored with the wrong-name repair. "
              r"\emph{Lines right}: \% of note lines whose facts match the hand-checked facts exactly; \emph{Missed}: real constraints read as ``no fact''; "
              r"\emph{Wrong}: a wrong fact, or a fact invented from filler (counts per run, over all items). Worked examples are the main thing to watch "
              r"for overfitting: they are written in fresh wording, not copied from the visible notes."))
    reps = [("None", r.get("1x", fix="no fixes")), (r"$\star$ Wrong-name repair", r.get("1x", fix="F2")),
            ("Station/time swap repair", r.get("1x", fix="F1")), ("Both", r.get("1x", fix="all"))]
    T.append(("Code repairs at 1$\\times$ (same model output)", KH + LH,
              [kind_row(l, s, line_cells(s)) for l, s in reps], "p{5.4cm} ccc c c ccc",
              r"All four rows score the \emph{same} 1$\times$ model output, so differences come from the repair alone, not sampling. "
              r"Wrong-name repair: a fact naming someone its own line never mentions is swapped to the one unused name the line does mention, else dropped. "
              r"Swap repair: a station written where a time belongs (or the reverse) becomes the matching fact; it fires too rarely to matter and is not in the submitted system."))
    three = [(r"1 read (the 1$\times$ system)", r.get("1x")),
             ("2 reads + 1 re-ask of the lines they disagree on", r.get("3x-followup")),
             ("3 reads, same prompt, per-line vote", r.get("3x-sameprompt")),
             (r"$\star$ 3 reads, 3 different prompts, per-line vote", r.get("3x"))]
    T.append(("Ways to spend three calls", KH + LH,
              [kind_row(l, s, line_cells(s)) for l, s in three], "p{5.4cm} ccc c c ccc",
              r"Same prompt and repair throughout; only the use of the extra calls changes. A re-ask lets one fresh sample overwrite a line, "
              r"so it can turn a right reading wrong; a vote needs two reads to agree. Reads with the same prompt make the same mistakes, "
              r"so they rarely outvote one another."))
    S10, V = r.get("10x"), r.get("10x", tag="vote")
    ten = [("3-read vote (the 3$\\times$ system)", "--", "--", m(V), "--")]
    for k, name, why in REASKS:
        w = r.get(f"10x-no{k}")
        calls = S10 and S10["reask_calls"].get(k)
        lines = S10 and S10["reask_lines"].get(k)
        nitems = r.meta.get("items") or 1
        ten.append((rf"\quad + {name} \textit{{\footnotesize ({why})}}",
                    "--" if calls is None else f"{100 * calls / nitems:.0f}\\%",
                    "--" if lines is None else f"{lines:.0f}",
                    m(w), "--" if (S10 is None or w is None) else f"{S10['macro'] - w['macro']:+.1f}"))
    ten.append((r"$\star$ Full 10$\times$", "--", "--", m(S10), "--"))
    T.append(("Where the 10$\\times$ calls go", ["", "Items asked", "Lines re-asked", "Macro without it", "Gain"],
              [list(x) for x in ten], "l cccc",
              r"After the 3-read vote, code flags lines and makes one call per kind of problem, only for items that have such lines. "
              r"\emph{Items asked}: share of items that got this call; \emph{Lines re-asked}: lines sent, per run; "
              r"\emph{Gain}: full 10$\times$ minus the system without this kind of call."))
    conf_rows = []
    sys_ = [r.get("1x"), r.get("3x"), r.get("10x")]
    for d in KINDS:
        cells = [f"Declared {d}"]
        for s in sys_:
            cells += ["--"] * 3 if s is None else [f"{s['conf'][t][d]:.1f}".rstrip("0").rstrip(".") for t in KINDS]
        conf_rows.append(cells)
    T.append(("Declared versus true kind",
              [r"True kind $\rightarrow$", r"1$\times$ U", "A", "I", r"3$\times$ U", "A", "I", r"10$\times$ U", "A", "I"],
              conf_rows, "l ccc ccc ccc",
              r"Mean item counts over runs for the submitted system at each budget (U = unique, A = ambiguous, I = inconsistent); the diagonal is correct. "
              r"A missed constraint leaves too many solutions (a unique item declared ambiguous); a misread one usually makes the notes contradict (declared inconsistent)."))
    return T

# ------------------------------------------------------------------ document

HOW_TO_READ = r"""
\textbf{How to read it.} Read across a row to see what one component is worth at each budget: the gap between \emph{with} and \emph{without}. Read down the shaded rows for the cost--accuracy curve. The budgets are nested: 3$\times$ is the 1$\times$ system plus two more reads, and 10$\times$ is the 3$\times$ system plus targeted re-asks, so every 1$\times$ component is also inside 3$\times$ and 10$\times$, and every 3$\times$ component inside 10$\times$. With 60 items, one item moves the macro score by 1.7 points; differences smaller than the run-to-run spread ($\pm$) are noise.

\textbf{How the extra calls fix errors, and why 3$\times$ and 10$\times$ do it differently.}
At \textbf{1$\times$} a misread line can only be caught by code: the wrong-name repair swaps or drops a fact that names someone its line never mentions. Nothing can recover a line the model missed.
At \textbf{3$\times$} the system never asks the model to correct itself. It reads the whole note three times, each with a differently formatted prompt, and keeps, line by line, the fact at least two reads agree on. This fixes \emph{random} misreads; it cannot fix a mistake all three reads share, which is why the reads use different prompts.
At \textbf{10$\times$} code decides \emph{which} lines need another look and \emph{why}. After the vote it flags lines that break a check or on which the reads disagreed, groups them by kind of problem, and makes one call per kind, showing the model only those lines, the earlier readings, and what is wrong. A re-read replaces the vote only when code had found a concrete problem and the new answer passes the checks; otherwise it counts as one more vote. So 3$\times$ fixes errors by \emph{agreement}, and 10$\times$ by \emph{targeted correction}.
"""


def build(res, outdir, name="ablation"):
    """Writes <outdir>/<name>.md and .tex, and .pdf if pdflatex is installed (LaTeX's aux and
    log files are removed)."""
    r = R(res)
    meta = r.meta
    side = side_tables(r)
    if meta.get("suite") == "main":      # supporting tables whose systems the main suite runs
        side = [t for t in side if not t[0].startswith(("Prompt techniques", "Ways to spend"))]
    footer = (f"{meta.get('runs', '?')} runs per system, {meta.get('items', '?')} items. "
              f"Command: \\texttt{{{meta.get('command', 'python3 ablate.py').replace('_', chr(92) + '_').replace('--', '-{}-')}}}. "
              f"Started {meta.get('started', '?')}" + (f", took {meta['minutes']} min" if "minutes" in meta else "") + ".")
    tex = (r"""\documentclass[10pt]{article}
\usepackage[a4paper,margin=1.8cm]{geometry}
\usepackage{booktabs,array,xcolor,caption,float,colortbl}
\usepackage[T1]{fontenc}
\captionsetup{font=small,labelfont=bf,skip=6pt}
\setlength{\parindent}{0pt}\setlength{\parskip}{5pt}
\definecolor{band}{gray}{0.94}
\begin{document}
\section*{Ablation}
""" + main_tex(r) + HOW_TO_READ + "\n{\\footnotesize " + footer + "}\n\n\\clearpage\n\\section*{Supporting tables}\n"
           + "\n".join(r"\subsection*{" + t + "}\n" + simple_tex(h, rows, spec, cap) for t, h, rows, spec, cap in side)
           + "\n\\end{document}\n")
    tex_path = os.path.join(outdir, name + ".tex")
    open(tex_path, "w").write(tex)

    rows, full, calls = main_table(r)
    md = ["# Ablation", "",
          "| component | 1x with | 1x without | 3x with | 3x without | 10x with | 10x without |", "|---" * 7 + "|",
          "| **full system** | " + " | ".join(f"{tex2md(x)} ({c} calls) | " for x, c in zip(full, calls)).rstrip(" |") + " |"]
    for row in rows:
        md.append(f"| *{tex2md(row[1])}* |" + " |" * 6 if row[0] in ("group", "sub")
                  else "| " + " | ".join(tex2md(c) for c in row) + " |")
    for t, h, rows_, spec, cap in side:
        md += ["", f"## {tex2md(t)}", "", md_table(h, rows_)]
    md += ["", tex2md(footer.replace("\\texttt", ""))]
    open(os.path.join(outdir, name + ".md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))

    msg = f"\nwrote {os.path.join(outdir, name)}.md and .tex"
    if shutil.which("pdflatex"):
        for _ in range(2):
            p = subprocess.run(["pdflatex", "-interaction=nonstopmode", name + ".tex"], cwd=outdir,
                               capture_output=True, text=True)
        ok = p.returncode == 0
        for ext in (".aux", ".out") + ((".log",) if ok else ()):
            if os.path.exists(os.path.join(outdir, name + ext)):
                os.remove(os.path.join(outdir, name + ext))
        msg += " and .pdf" if ok else f" (pdflatex failed; see {name}.log)"
    print(msg)
