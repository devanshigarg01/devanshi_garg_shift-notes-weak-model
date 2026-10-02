# ablation

One folder per ablation run, named `ablation_<suite>_suite_<N>_run(s)`. Folders ending in `_orig` are
my reported results; a re-run writes the same name without `_orig`, so it never overwrites them.

| Folder | Suite | Runs | Took |
|---|---|---|---|
| `ablation_quick_suite_3_runs_orig/` | quick: full system at each budget + solver, repair, vote, re-ask rows | 3 | 33 min |
| `ablation_main_suite_1_run_orig/` | main: the whole main table (prompt components at 1x only) | 1 | 13 min |
| `ablation_full_suite_1_run_orig/` | full: every component at every budget + supporting tables | 1 | 21 min |

Each folder has the same tables in several formats:

- `.html`: open in a browser (best view)
- `.pdf`: same as the HTML, if `pdflatex` was installed
- `.md` / `.tex`: Markdown and LaTeX sources
- `.json`: every score behind the tables; `python3 ablate.py --report <folder>/<name>.json` rebuilds the tables from it without model calls

All scores are macro exact match (%) on the 60 visible items; one item moves a score by 1.7 points.
To reproduce, see the Ablation section of the main [README](../README.md).
