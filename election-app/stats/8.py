#!/usr/bin/env python3
from pathlib import Path
import argparse

FILES_IN_ROOT = [
    # intermediates you listed
    "stats_with_statewide.csv",
    "stats2_final_headers.csv",
    "2024_county_results_with_final-old.csv",
    "2024_county_results_with_final.csv",
    "2024_county_results_with_final_1.csv",
    "2024_county_results_with_calcs-old.csv",
    "2024_county_results_with_calcs_new.csv",
    "output_statewide_stats.csv",
    "probs_timeseries_PA.csv",

    # common extra intermediate (safe to include)
    "2024_county_results_with_calcs.csv",
]

# Staged / temporary names that sometimes get created in OUTDIR (../win_prob)
FILES_IN_OUTDIR = [
    "data_output.csv",
    "data_output_with_margins_old.csv",
    "data_output_with_margins.csv",
    "data_output_with_margins-statewide.csv",
]

# Per-state intermediate outputs to clean (keep plots/jsonl)
GLOBS_IN_OUTDIR = [
    "snapshots_*.csv",
    "data_output_*.csv",
    "data_output_*_with_margins*.csv",
    "data_output*.checkpoint.json",          # e.g. data_output_PA.csv.checkpoint.json
    "data_output_*.csv.checkpoint.json",
    #"statewide_margin_pct_vs_percent_*.png",
    #"statewide_margin_pct_vs_percent_*.json",
    "stats.csv",
    "stats2.csv",
]


def delete_files(base: Path, names: list[str], dry_run: bool) -> int:
    removed = 0
    for name in names:
        p = base / name
        if p.exists() and p.is_file():
            if dry_run:
                print(f"[dry-run] would delete: {p}")
            else:
                p.unlink()
                print(f"[deleted] {p}")
            removed += 1
        else:
            print(f"[skip]    {p}")
    return removed

def delete_globs(base: Path, patterns: list[str], dry_run: bool) -> int:
    removed = 0
    for pat in patterns:
        for p in base.glob(pat):
            if p.exists() and p.is_file():
                if dry_run:
                    print(f"[dry-run] would delete: {p}")
                else:
                    p.unlink()
                    print(f"[deleted] {p}")
                removed += 1
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="pipeline folder (defaults to script folder)")
    ap.add_argument("--outdir", default=None, help="per-state output folder (defaults to ../win_prob)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else (Path(__file__).resolve().parent / "_work")
    outdir = Path(args.outdir).resolve() if args.outdir else (root / ".." / "win_prob").resolve()

    print(f"[info] root  = {root}")
    print(f"[info] outdir= {outdir}")

    total = 0
    total += delete_files(root, FILES_IN_ROOT, args.dry_run)

    if outdir.exists() and outdir.is_dir():
        total += delete_files(outdir, FILES_IN_OUTDIR, args.dry_run)
        total += delete_globs(outdir, GLOBS_IN_OUTDIR, args.dry_run)
    else:
        print(f"[skip] outdir missing or not a dir: {outdir}")

    print(f"[done] removed {total} file(s)")

if __name__ == "__main__":
    main()
