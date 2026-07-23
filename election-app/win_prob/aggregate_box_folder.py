#!/usr/bin/env python3
"""
aggregate_box_folder.py

Aggregates successive statewide_margin_pct_vs_percent_*_box.json files
into a persistent rolling accumulator.

Input (single-run):
  statewide_margin_pct_vs_percent_CA_2026_box.json

Output (rolling):
  statewide_margin_pct_vs_percent_CA_2026_box_all.json
"""

import os
import json
import argparse
from datetime import datetime

def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--year", required=True)
    args = ap.parse_args()

    state = args.state.upper()
    year  = str(args.year)

    single_name = f"statewide_margin_pct_vs_percent_{state}_{year}_box.json"
    all_name    = f"statewide_margin_pct_vs_percent_{state}_{year}_box_all.json"

    single_path = os.path.join(args.input_dir, single_name)
    all_path    = os.path.join(args.input_dir, all_name)

    single = load_json(single_path)
    if not single:
        return  # nothing to do

    # normalize: single file is a LIST of entries
    if not isinstance(single, list):
        return

    acc = load_json(all_path)
    if not isinstance(acc, list):
        acc = []

    # index by (timestamp, statewide_percent_in)
    seen = {
        (row.get("timestamp"), row.get("statewide_percent_in")): row
        for row in acc
        if isinstance(row, dict)
    }

    for row in single:
        if not isinstance(row, dict):
            continue
        key = (row.get("timestamp"), row.get("statewide_percent_in"))
        seen[key] = row  # overwrite = intentional

    merged = list(seen.values())

    # stable sort
    merged.sort(key=lambda r: (
        r.get("statewide_percent_in", 0),
        r.get("timestamp", "")
    ))

    atomic_write(all_path, merged)

if __name__ == "__main__":
    main()
