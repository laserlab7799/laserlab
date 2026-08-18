#!/usr/bin/env python3
"""
convert_plotdata_folder.py

Reads plotdata_*.jsonl files in a folder and produces
statewide_plot_data_<state>.json (or statewide_plot_data_<state>_<year>.json if needed).

IMPORTANT FIX (running total):
- This script now treats the output JSON as the persistent accumulator.
- Each run loads the existing output JSON (if present), merges in new JSONLs, and rewrites output.
- Deleting JSONLs will NOT lose history because history is stored in the output JSON (points_by_x).

Delete switch:
- Controlled by DELETE_JSONLS below and/or --delete-jsonls
"""

import os
import glob
import json
import argparse
from datetime import datetime, timezone
from collections import defaultdict


# ---------------------------
# EASY ON/OFF SWITCH
# ---------------------------
# If True, processed JSONLs are deleted after a successful merge.
# You can also force deletion per-run with: --delete-jsonls
DELETE_JSONLS = False
# File pattern for your "nametype"
PATTERN = "plotdata_*.jsonl"

# Output filename base
OUTPUT_PREFIX = "statewide_plot_data_"


# ---------------------------
# Helpers
# ---------------------------
def utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def parse_state_year_from_filename(path):
    """
    Expected: plotdata_<STATE>_<YEAR>_....jsonl
    Example:  plotdata_CA_2026_2026-01-05T09-46-02.jsonl
    """
    base = os.path.basename(path)
    stem = base[:-5] if base.endswith(".jsonl") else base
    parts = stem.split("_")
    if len(parts) >= 3 and parts[0] == "plotdata":
        state = parts[1].upper()
        year = None
        if parts[2].isdigit():
            year = int(parts[2])
        return state, year
    return None, None


def candidate_sort_key(cand: str):
    """
    Tie-breaker: prefer 'T' then 'H' if equal probability (matches your typical red/blue convention).
    Otherwise stable alphabetical.
    """
    cand = (cand or "").upper()
    if cand == "T":
        return (0, cand)
    if cand == "H":
        return (1, cand)
    return (2, cand)


def color_for_candidate(cand: str, fallback="gray"):
    cand = (cand or "").upper()
    if cand == "T":
        return "red"
    if cand == "H":
        return "blue"
    return fallback


def opposite_color(color: str):
    c = (color or "").lower()
    if c == "red":
        return "blue"
    if c == "blue":
        return "red"
    return "gray"


def color_to_candidate(color: str):
    c = (color or "").lower()
    if c == "red":
        return "T"
    if c == "blue":
        return "H"
    return None


def x_to_key(x: float) -> str:
    # Stable key for JSON dict; matches the by_x rounding below
    return f"{x:.12f}"


def key_to_x(k: str):
    try:
        return round(float(k), 12)
    except Exception:
        return None


def _empty_by_x():
    return defaultdict(lambda: {"adjusted": {}, "raw": {}})


def load_existing_accumulator(outpath: str):
    """
    Load previous output JSON if present and return a seed structure compatible with by_x.

    Returns:
      seed_by_x: defaultdict(float_x -> {"adjusted":{cand:wp}, "raw":{cand:wp}})
      existing_sources: set[str]

    Supports:
      - Preferred: doc["points_by_x"]
      - Backward-compat: infer from doc["series"] if points_by_x not present
    """
    seed = _empty_by_x()
    existing_sources = set()

    if not os.path.exists(outpath):
        return seed, existing_sources

    try:
        with open(outpath, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return seed, existing_sources

    # sources
    try:
        for s in (doc.get("meta", {}).get("sources", []) or []):
            existing_sources.add(str(s))
    except Exception:
        pass

    # Preferred: points_by_x (explcit accumulator)
    pbx = doc.get("points_by_x")
    if isinstance(pbx, dict) and pbx:
        for k, v in pbx.items():
            x = key_to_x(k)
            if x is None or not isinstance(v, dict):
                continue

            adj = v.get("adjusted") or {}
            raw = v.get("raw") or {}
            if isinstance(adj, dict):
                for cand, p in adj.items():
                    wp = safe_float(p)
                    if wp is not None:
                        seed[x]["adjusted"][str(cand)] = wp
            if isinstance(raw, dict):
                for cand, p in raw.items():
                    wp = safe_float(p)
                    if wp is not None:
                        seed[x]["raw"][str(cand)] = wp

        return seed, existing_sources

    # Backward-compat: infer from "series" (older output without points_by_x)
    series = doc.get("series") or {}
    leader = series.get("leader") or {}
    trailer = series.get("trailer") or {}
    leader_raw = series.get("leader_raw") or {}

    def ingest_series(s_obj, series_type: str):
        xs = s_obj.get("x") or []
        ys = s_obj.get("y") or []
        cols = s_obj.get("color") or []
        n = min(len(xs), len(ys), len(cols))
        for i in range(n):
            x = safe_float(xs[i])
            y = safe_float(ys[i])
            cand = color_to_candidate(cols[i])
            if x is None or y is None or not cand:
                continue
            xk = round(x, 12)
            seed[xk][series_type][cand] = y

    # leader/trailer are adjusted
    ingest_series(leader, "adjusted")
    ingest_series(trailer, "adjusted")

    # leader_raw is raw
    ingest_series(leader_raw, "raw")

    return seed, existing_sources


def serialize_points_by_x(by_x):
    out = {}
    for x in sorted(by_x.keys()):
        out[x_to_key(x)] = {
            "adjusted": by_x[x].get("adjusted", {}) or {},
            "raw": by_x[x].get("raw", {}) or {},
        }
    return out


# ---------------------------
# Core merge
# ---------------------------
def merge_group(jsonl_paths, state, year, seed_by_x=None, existing_sources=None):
    """
    Reads JSONL lines that look like:
      {
        "state": "CA",
        "timestamp": "...",
        "x_statewide_percent_in": 37.66,
        "candidate": "T" or "H",
        "series_type": "adjusted" or "raw",
        "win_probability": 57.648
      }

    Produces:
      {
        "meta": {...},
        "series": {
          "leader": {...},
          "trailer": {...},
          "leader_raw": {...},
          "statewide_margin_pp": {"x": [], "y": []}
        },
        "points_by_x": { "37.660000000000": {"adjusted": {...}, "raw": {...}}, ... }
      }

    Running total:
      - seed_by_x / existing_sources are loaded from the existing output JSON (if present).
      - new JSONLs update/overwrite by_x entries.
    """
    # keyed by x -> { "adjusted": {cand: prob}, "raw": {cand: prob} }
    by_x = seed_by_x if seed_by_x is not None else _empty_by_x()

    processed_sources = set(existing_sources or set())

    for path in jsonl_paths:
        processed_sources.add(os.path.basename(path))
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                # Your JSONL uses x_statewide_percent_in
                x = safe_float(obj.get("x_statewide_percent_in"))
                if x is None:
                    # fallback to a couple common alternates
                    for k in ("x", "percent_in", "eevp", "x_statewide"):
                        x = safe_float(obj.get(k))
                        if x is not None:
                            break
                if x is None:
                    continue

                cand = obj.get("candidate") or obj.get("cand") or obj.get("name") or obj.get("label")
                cand = (cand or "").strip()
                if not cand:
                    continue

                series_type = (obj.get("series_type") or "").strip().lower()
                if series_type not in ("adjusted", "raw"):
                    # if missing, assume adjusted
                    series_type = "adjusted"

                wp = safe_float(obj.get("win_probability"))
                if wp is None:
                    wp = safe_float(obj.get("win_prob") or obj.get("prob") or obj.get("p_win"))
                if wp is None:
                    continue

                # In your JSONLs, win_probability is usually 0-100
                # (but handle 0-1 just in case)
                if 0.0 <= wp <= 1.0000001:
                    wp *= 100.0

                x_key = round(x, 12)
                by_x[x_key][series_type][cand] = wp  # overwrite is intentional

    # Build series arrays from the FULL accumulator
    xs = sorted(by_x.keys())

    leader_x, leader_y, leader_color, leader_marker = [], [], [], []
    trailer_x, trailer_y, trailer_color, trailer_marker = [], [], [], []

    leader_raw_x, leader_raw_y, leader_raw_color, leader_raw_marker = [], [], [], []
    trailer_raw_x, trailer_raw_y, trailer_raw_color, trailer_raw_marker = [], [], [], []


    for x in xs:
        adjusted = by_x[x]["adjusted"]
        raw = by_x[x]["raw"]

        if not adjusted:
            continue

        # Pick leader by max adjusted prob; tie-break by candidate_sort_key
        best_cand = None
        best_prob = None
        for cand, prob in adjusted.items():
            if best_prob is None or prob > best_prob:
                best_cand, best_prob = cand, prob
            elif prob == best_prob:
                if candidate_sort_key(cand) < candidate_sort_key(best_cand):
                    best_cand, best_prob = cand, prob

        if best_prob is None:
            continue

        # Trailer: choose best of remaining if present, otherwise 100 - leader
        trailer_prob = 100.0 - best_prob
        trailer_cand = None
        if len(adjusted) >= 2:
            rest = [(c, p) for c, p in adjusted.items() if c != best_cand]
            if rest:
                rest.sort(key=lambda cp: (-cp[1], candidate_sort_key(cp[0])))
                trailer_cand = rest[0][0]

        # Colors
        lcol = color_for_candidate(best_cand)
        tcol = color_for_candidate(trailer_cand) if trailer_cand else opposite_color(lcol)

        # leader_raw: raw prob for same leader candidate if available, else leader adjusted
        # leader_raw: TRUE raw leader (independent of adjusted leader)
        # leader_raw / trailer_raw: raw values aligned to adjusted roles
        lraw = raw.get(best_cand)
        traw = raw.get(trailer_cand) if trailer_cand else None

        # fallbacks (should rarely trigger, but keeps history safe)
        if lraw is None:
            lraw = best_prob
        if traw is None:
            traw = 100.0 - lraw

        lraw_color = color_for_candidate(best_cand)
        traw_color = color_for_candidate(trailer_cand) if trailer_cand else opposite_color(lraw_color)



        leader_x.append(x)
        leader_y.append(best_prob)
        leader_color.append(lcol)
        leader_marker.append("o")

        trailer_x.append(x)
        trailer_y.append(trailer_prob)
        trailer_color.append(tcol)
        trailer_marker.append("s")

        leader_raw_x.append(x)
        leader_raw_y.append(lraw)
        leader_raw_color.append(lraw_color)
        leader_raw_marker.append("^")
        trailer_raw_x.append(x)
        trailer_raw_y.append(traw)
        trailer_raw_color.append(traw_color)
        trailer_raw_marker.append("^")


    points_by_x = serialize_points_by_x(by_x)

    out = {
        "meta": {
            "state": state,
            "year": year,
            "sources": sorted(processed_sources),
            "generated_at": utc_now_iso_z(),
            "notes": (
                "Persistent accumulator: merges new plotdata JSONLs into existing output JSON. "
                "leader/trailer computed per x by max adjusted win_probability; "
                "leader_raw from raw series for same leader candidate when present. "
                "History is stored in points_by_x so deleting JSONLs does not lose past points."
            ),
        },
        "series": {
            "leader": {
                "x": leader_x, "y": leader_y,
                "color": leader_color, "marker": leader_marker
            },
            "trailer": {
                "x": trailer_x, "y": trailer_y,
                "color": trailer_color, "marker": trailer_marker
            },
            "leader_raw": {
                "x": leader_raw_x, "y": leader_raw_y,
                "color": leader_raw_color, "marker": leader_raw_marker
            },
            "trailer_raw": {
                "x": trailer_raw_x, "y": trailer_raw_y,
                "color": trailer_raw_color, "marker": trailer_raw_marker
            },
            "statewide_margin_pp": {"x": [], "y": []},
        },

        "points_by_x": points_by_x,
    }
    return out


def choose_output_name(groups):
    """
    If only one (state,year) group: statewide_plot_data_<state>.json
    If multiple groups: statewide_plot_data_<state>_<year>.json
    """
    multi = len(groups) > 1
    names = {}
    for (state, year), _paths in groups.items():
        if multi or year is None:
            suffix = f"{state.lower()}_{year}" if year is not None else state.lower()
        else:
            suffix = state.lower()
        names[(state, year)] = f"{OUTPUT_PREFIX}{suffix}.json"
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete-jsonls", action="store_true", help="Delete processed JSONLs after successful merge")

    # make it work from app_hub
    parser.add_argument("--input-dir", default=None, help="Folder to scan for plotdata_*.jsonl (default: script folder)")
    parser.add_argument("--state", default=None, help="Optional filter: only process this state (e.g., CA)")
    parser.add_argument("--year", type=int, default=None, help="Optional filter: only process this year (e.g., 2026)")

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.abspath(args.input_dir) if args.input_dir else script_dir

    jsonl_paths = sorted(glob.glob(os.path.join(base_dir, PATTERN)))
    if not jsonl_paths:
        print(f"No files matched {PATTERN} in {base_dir}")
        return

    # Group by (state, year)
    groups = defaultdict(list)
    for p in jsonl_paths:
        state, year = parse_state_year_from_filename(p)
        if state is None:
            state, year = "UNKNOWN", None
        groups[(state, year)].append(p)

    # Optional filtering
    if args.state:
        st = args.state.strip().upper()
        groups = defaultdict(list, {k: v for k, v in groups.items() if k[0] == st})
    if args.year is not None:
        groups = defaultdict(list, {k: v for k, v in groups.items() if k[1] == args.year})

    if not groups:
        print("No groups matched the requested --state/--year filters.")
        return

    outnames = choose_output_name(groups)

    processed_files = []

    for (state, year), paths in groups.items():
        outpath = os.path.join(base_dir, outnames[(state, year)])  # write into input dir

        # Load accumulator (running total) from existing output if present
        seed_by_x, existing_sources = load_existing_accumulator(outpath)

        merged = merge_group(
            paths,
            state=state,
            year=year,
            seed_by_x=seed_by_x,
            existing_sources=existing_sources,
        )

        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)

        processed_files.extend(paths)
        print(f"Wrote: {outpath}  (merged existing + {len(paths)} new JSONL files)")

    do_delete = bool(args.delete_jsonls) or bool(DELETE_JSONLS)
    if do_delete:
        for p in processed_files:
            try:
                os.remove(p)
                print(f"Deleted: {p}")
            except Exception as e:
                print(f"WARNING: Could not delete {p}: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
