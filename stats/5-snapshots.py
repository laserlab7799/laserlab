#!/usr/bin/env python3
import os, sys
from datetime import datetime, timedelta
import pandas as pd
import argparse

# =========================
# CONFIG (override via env)
# =========================
INPUT_CSV         = os.getenv("INPUT_CSV", "output_statewide_stats.csv")
OUTPUT_CSV        = os.getenv("OUTPUT_CSV", "snapshots.csv")
TARGET_STATE      = os.getenv("TARGET_STATE", "").strip()

START_TIMESTAMP   = os.getenv("START_TIMESTAMP", "2024-11-03T15-30-22")
END_TIMESTAMP     = os.getenv("END_TIMESTAMP",   "2020-11-05T16-30-22")
INTERVAL_MINUTES  = int(os.getenv("INTERVAL_MINUTES", "30"))
USE_WINDOW        = os.getenv("USE_WINDOW", "1") == "1"

# ---- parse CLI and override the above ----
ap = argparse.ArgumentParser()
ap.add_argument("--input", dest="input_csv")
ap.add_argument("--output", dest="output_csv")
ap.add_argument("--state", dest="target_state")
ap.add_argument("--use-window", dest="use_window", type=int, choices=[0,1])
ap.add_argument("--start", dest="start_ts")
ap.add_argument("--end", dest="end_ts")
ap.add_argument("--interval", dest="interval", type=int)
args, _ = ap.parse_known_args()

if args.input_csv:  INPUT_CSV = args.input_csv
if args.output_csv: OUTPUT_CSV = args.output_csv
if args.target_state is not None: TARGET_STATE = args.target_state.strip()
if args.use_window is not None:   USE_WINDOW = (args.use_window == 1)
if args.start_ts:   START_TIMESTAMP = args.start_ts
if args.end_ts:     END_TIMESTAMP   = args.end_ts
if args.interval:   INTERVAL_MINUTES = args.interval


# Toggle: when "1", filter by the timestamp window like the original script.
# When "0" (default), include ALL timestamps (and, if TARGET_STATE is ALL/blank, all states).


# Canonical output format used for matching/sorting
CANON_FMT = "%Y-%m-%dT%H-%M-%S"

# Accept many common input shapes (hyphens/colons, with/without subseconds/zone)
INPUT_FMTS = [
    "%Y-%m-%dT%H-%M-%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H-%M-%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H-%M-%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H-%M-%S.%f",
    "%Y-%m-%d %H:%M:%S.%f",
]

def parse_any_ts(ts_str: str) -> datetime:
    s = (ts_str or "").strip()
    if not s:
        raise ValueError("empty timestamp")
    if s.endswith(("Z", "z")):
        s = s[:-1]
    for fmt in INPUT_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        date_part, time_part = s.split("T", 1) if "T" in s else s.split(" ", 1)
        time_part_h = time_part.replace(":", "-")
        candidate = f"{date_part}T{time_part_h}"
        return datetime.strptime(candidate, "%Y-%m-%dT%H-%M-%S")
    except Exception:
        pass
    raise ValueError(f"Unrecognized timestamp format: {ts_str!r}")

def normalize_ts_to_canon(ts_str: str) -> str:
    try:
        dt = parse_any_ts(ts_str)
        return dt.strftime(CANON_FMT)
    except Exception:
        return ""

def generate_steps(start_str: str, end_str: str, step_minutes: int) -> list[str]:
    start_dt = parse_any_ts(start_str)
    end_dt   = parse_any_ts(end_str)
    if end_dt < start_dt:
        raise SystemExit("END_TIMESTAMP is earlier than START_TIMESTAMP.")
    out = []
    cur = start_dt
    while cur <= end_dt:
        out.append(cur.strftime(CANON_FMT))
        cur += timedelta(minutes=step_minutes)
    return out

def main():
    if not os.path.isfile(INPUT_CSV):
        raise SystemExit(f"Input file not found: {INPUT_CSV}")

    df = pd.read_csv(INPUT_CSV, dtype=str).fillna("")

    required = {"state", "timestamp"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Available columns: {', '.join(df.columns)}"
        )

    df["__ts_norm__"] = df["timestamp"].map(normalize_ts_to_canon)

    # --- NEW: decide if we're filtering by state or taking all ---
    take_all_states = (TARGET_STATE == "" or TARGET_STATE.upper() == "ALL")

    # already above:
    # df["__ts_norm__"] = df["timestamp"].map(normalize_ts_to_canon)
    # take_all_states = (TARGET_STATE == "" or TARGET_STATE.upper() == "ALL")

    # >>> REPLACE THIS WHOLE SECTION <<<
    take_all_states = (TARGET_STATE == "" or TARGET_STATE.upper() == "ALL")

    if USE_WINDOW:
        desired_ts  = generate_steps(START_TIMESTAMP, END_TIMESTAMP, INTERVAL_MINUTES)
        desired_set = set(desired_ts)
        time_mask   = df["__ts_norm__"].isin(desired_set)

        if take_all_states:
            mask = time_mask
        else:
            mask = (df["state"].astype(str).str.upper() == TARGET_STATE.upper()) & time_mask

        out  = df.loc[mask].copy()
        mode = "windowed-all-states" if take_all_states else "windowed-one-state"
    else:
        if take_all_states:
            out  = df.copy()
            mode = "all-states"
        else:
            out  = df.loc[df["state"].astype(str).str.upper() == TARGET_STATE.upper()].copy()
            mode = "one-state"



    out = out.sort_values(by=["__ts_norm__", "timestamp"])
    out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")

    # Diagnostics
    print(f"Mode: {mode}")
    print(f"Rows written: {len(out)}")
    if not out.empty:
        print("Distinct states in output:", ", ".join(sorted(out['state'].astype(str).unique())[:20]))
        counts = out["__ts_norm__"].value_counts(dropna=False).sort_index()
        print("Rows per normalized timestamp (empty = unparseable):")
        for ts, n in counts.items():
            print(f"  {ts or '(unparsed)'}: {n}")
    print(f"Wrote {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
