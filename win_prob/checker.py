#!/usr/bin/env python3
"""
checker.py

Post-step verifier/fixer that runs AFTER:
  1) convert_plotdata_folder.py  -> statewide_plot_data_<state>.json (or _<state>_<year>.json)
  2) aggregate_box_folder.py     -> statewide_margin_pct_vs_percent_<STATE>_<YEAR>_box_all.json

It:
  - reads plot JSON + box_all JSON
  - checks, point by point (statewide_percent_in), whether leader/trailer/leader_raw colors match
    the MC median sign convention:
        median > 0 => leader blue, trailer red
        median < 0 => leader red,  trailer blue
        median == 0/None => leaves existing colors (safe fallback)
  - rewrites the plot JSON in-place (atomic) to fix mismatches

Usage (mirrors other scripts):
  python checker.py --input-dir /path/to/win_prob/H/VA/02 --state VA --year 2026
"""

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone

# -----------------------------
# Helpers
# -----------------------------

def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _safe_float(x):
    try:
        return float(x)
    except Exception:
        return None

def _atomic_write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    tmp.replace(path)

def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _find_plot_file(input_dir: Path, state: str, year: str) -> Path:
    """
    Prefer:
      statewide_plot_data_<state>.json
      statewide_plot_data_<state>_<year>.json
    else:
      any statewide_plot_data_*.json
    """
    st = state.lower()
    candidates = [
        input_dir / f"statewide_plot_data_{st}.json",
        input_dir / f"statewide_plot_data_{st}_{year}.json",
    ]
    for p in candidates:
        if p.exists():
            return p

    # fallback wildcard
    any_matches = sorted(input_dir.glob("statewide_plot_data_*.json"))
    if any_matches:
        return any_matches[0]

    raise FileNotFoundError(f"No statewide_plot_data_*.json found in {input_dir}")

def _find_box_all_file(input_dir: Path, state: str, year: str) -> Path:
    """
    Prefer:
      statewide_margin_pct_vs_percent_<STATE>_<YEAR>_box_all.json
    else:
      any *box_all.json
    """
    st = state.upper()
    y = str(year)
    preferred = input_dir / f"statewide_margin_pct_vs_percent_{st}_{y}_box_all.json"
    if preferred.exists():
        return preferred

    # fallback wildcard
    any_matches = sorted(input_dir.glob("*_box_all.json"))
    if any_matches:
        return any_matches[0]

    raise FileNotFoundError(f"No *_box_all.json found in {input_dir}")

def _expected_colors_from_median(median, fallback_leader=None, fallback_trailer=None):
    """
    If median is numeric:
      >0 => (blue, red)
      <0 => (red, blue)
      ==0 => keep fallbacks (safe)
    If median is None/non-numeric => keep fallbacks (safe)
    """
    m = _safe_float(median)
    if m is None:
        return fallback_leader, fallback_trailer, "missing"
    if m > 0:
        return "blue", "red", "positive"
    if m < 0:
        return "red", "blue", "negative"
    return fallback_leader, fallback_trailer, "zero"

def _get_list_at(obj, path_list, default=None):
    cur = obj
    for k in path_list:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

# -----------------------------
# Main
# -----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True, help="Target folder (race/state[/district]) that holds the JSON outputs.")
    ap.add_argument("--state", required=True, help="USPS state, e.g. VA")
    ap.add_argument("--year", required=True, help="Election year label, e.g. 2026")

    # Optional overrides if you ever want to force specific filenames
    ap.add_argument("--plot-file", default=None, help="Optional: explicit plot JSON filename/path to rewrite")
    ap.add_argument("--box-all-file", default=None, help="Optional: explicit box_all JSON filename/path to use")

    args = ap.parse_args()

    input_dir = Path(args.input_dir).resolve()
    state = args.state.strip().upper()
    year = str(args.year).strip()

    if not input_dir.exists():
        raise FileNotFoundError(f"--input-dir does not exist: {input_dir}")

    plot_path = Path(args.plot_file).resolve() if args.plot_file else _find_plot_file(input_dir, state, year)
    box_path  = Path(args.box_all_file).resolve() if args.box_all_file else _find_box_all_file(input_dir, state, year)

    plot = _read_json(plot_path)
    box_all = _read_json(box_path)

    if not isinstance(box_all, list):
        raise ValueError(f"box_all must be a list of rows, got: {type(box_all)}")

    # Index box rows by statewide_percent_in
    box_by_x = {}
    for row in box_all:
        if not isinstance(row, dict):
            continue
        x = row.get("statewide_percent_in")
        if x is None:
            continue
        box_by_x[x] = row

    leader_x = _get_list_at(plot, ["series", "leader", "x"], default=[])
    leader_c_old = _get_list_at(plot, ["series", "leader", "color"], default=[])
    trailer_c_old = _get_list_at(plot, ["series", "trailer", "color"], default=[])
    leader_raw_c_old = _get_list_at(plot, ["series", "leader_raw", "color"], default=[])

    if not isinstance(leader_x, list):
        raise ValueError("plot series.leader.x is missing or not a list")

    # Build fixed color arrays aligned to leader_x length
    new_leader_c = []
    new_trailer_c = []
    new_leader_raw_c = []

    report_rows = []
    mismatch_count = 0

    for i, x in enumerate(leader_x):
        row = box_by_x.get(x)

        old_leader = leader_c_old[i] if i < len(leader_c_old) else None
        old_trailer = trailer_c_old[i] if i < len(trailer_c_old) else None
        old_leader_raw = leader_raw_c_old[i] if i < len(leader_raw_c_old) else None

        median = row.get("median") if isinstance(row, dict) else None

        exp_leader, exp_trailer, sign = _expected_colors_from_median(
            median,
            fallback_leader=old_leader,
            fallback_trailer=old_trailer,
        )

        # leader_raw should match leader color when we are fixing by median sign
        exp_leader_raw = exp_leader if exp_leader is not None else old_leader_raw

        leader_match = (old_leader == exp_leader)
        trailer_match = (old_trailer == exp_trailer)
        leader_raw_match = (old_leader_raw == exp_leader_raw)

        matched_all = bool(leader_match and trailer_match and leader_raw_match)
        if not matched_all:
            mismatch_count += 1

        new_leader_c.append(exp_leader)
        new_trailer_c.append(exp_trailer)
        new_leader_raw_c.append(exp_leader_raw)

        report_rows.append({
            "index": i,
            "statewide_percent_in": x,
            "median": median,
            "median_sign": sign,

            "old_leader_color": old_leader,
            "expected_leader_color": exp_leader,
            "leader_match": leader_match,

            "old_trailer_color": old_trailer,
            "expected_trailer_color": exp_trailer,
            "trailer_match": trailer_match,

            "old_leader_raw_color": old_leader_raw,
            "expected_leader_raw_color": exp_leader_raw,
            "leader_raw_match": leader_raw_match,

            "matched_all": matched_all,
        })

    # Apply fixes (rewrite plot JSON)
    plot.setdefault("series", {})
    plot["series"].setdefault("leader", {})
    plot["series"].setdefault("trailer", {})
    plot["series"].setdefault("leader_raw", {})

    plot["series"]["leader"]["color"] = new_leader_c
    plot["series"]["trailer"]["color"] = new_trailer_c
    plot["series"]["leader_raw"]["color"] = new_leader_raw_c

    # Provenance
    plot.setdefault("meta", {})
    plot["meta"]["leader_color_source"] = "mc_median_sign"
    plot["meta"]["leader_color_fix_applied"] = True
    plot["meta"]["leader_color_fix_generated_at"] = _utc_now_iso_z()

    _atomic_write_json(plot_path, plot)

    # Write reports into the same folder

    print(f"[checker] rewrote plot: {plot_path}")
    print(f"[checker] mismatches  : {mismatch_count}/{len(leader_x)}")


if __name__ == "__main__":
    main()
