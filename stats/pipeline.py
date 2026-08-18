#!/usr/bin/env python3
# pipeline.py — shared once, then per-state with explicit args into 5,6,7

import os
import sys
import time
import subprocess
from pathlib import Path
import shutil
import csv
import argparse

def log(msg, level="INFO"):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {level}: {msg}", flush=True)


ROOT = Path(__file__).parent.resolve()

def winprob_dir_for_target(office, state, district):
    base = (ROOT / "../win_prob").resolve()

    if not office or not state:
        return base  # safe fallback

    office = office.upper()
    state  = state.upper()

    if office == "H" and district:
        return base / office / state / str(int(district)).zfill(2)

    return base / office / state


RUN_STAMP = time.strftime("%Y-%m-%dT%H-%M-%S")

def _parse_cli():
    ap = argparse.ArgumentParser()

    ap.add_argument("--office", required=False)
    ap.add_argument("--state", required=False)
    ap.add_argument("--district", required=False)

    ap.add_argument("--append-input1", default=None)
    ap.add_argument("--append-input2", default=None)
    ap.add_argument("--append-export-dir", default=None)
    ap.add_argument("--statewide-input", default=None)
    ap.add_argument("--stats2-input", default=None)

    args = ap.parse_args()

    log(f"[PIPELINE] args: office={args.office} state={args.state} district={args.district}")

    return args




# ----------------- CONFIG -----------------
DELAY_SECONDS = 1

# Shared stages (run once)
SHARED_SCRIPTS = [
    "1-statewide.py",
    "1-stats-2.py",
    "1-append-new.py",      # → 2016_county_results_with_final-old.csv
    "1-change_header.py",
    "1-cut-zero-fips.py",
    "2-calc.py",
    "2-eevp.py",        # → 2016_county_results_with_calcs.csv (reinsert eevp)
    "3-calc-new.py",    # → 2016_county_results_with_calcs_new.csv
    "4-stats.py",       # → output_statewide_stats.csv
]

STATEWIDE_STATS   = str(ROOT / "output_statewide_stats.csv")
SNAPSHOTS_SCRIPT  = str(ROOT / "5-snapshots.py")
PVAL_SCRIPT       = str(ROOT / "6-pval.py")
PLOT_SCRIPT       = str(ROOT / "7.py")


# If you want a year label in plots, set it here (or None to omit)
PLOT_YEAR = 2026

# Optional: global flags per script (append here as needed)
GLOBAL_ARGS = {
    SNAPSHOTS_SCRIPT: [
        "--use-window", "1",
        "--interval", "30",
    ],
    PVAL_SCRIPT: [
        "-c", "50",
    ],
    PLOT_SCRIPT: [
        "--interval", "30",
    ],
    "1-statewide.py": [
        "--input",  str(ROOT / "stats.csv"),
        "--output", str(ROOT / "stats_with_statewide.csv"),
    ],
    "1-stats-2.py": [
        "--input",  str(ROOT / "stats2.csv"),
        "--output", str(ROOT / "stats2_final_headers.csv"),
    ],
}


# USPS codes (50 states)
STATES = [
    "MI","WI","PA","GA","NV","SC","AZ",
    "AL","AR","CA","CO","DE","FL","HI","IA","ID","IL","IN","KS","KY","LA","MD","MN","MO","MS","MT",
    "NC","ND","NE","NM","NJ","NY","OH","OK","OR","SD","TN","TX","UT","VA","WA","WV","WY"
]

#OUTDIR = WINPROB_DIR
EEVP_STATE_SCRIPT = str(ROOT / "7-eevp-statewide.py")  # or your actual filename

# --------------- HELPERS -------------------
def states_from_statewide_csv(path: str) -> list[str]:
    """Return unique state USPS codes in the order they appear in STATEWIDE_STATS."""
    out, seen = [], set()
    with open(path, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            st = (row.get("state") or "").strip()
            if st and st not in seen:
                seen.add(st)
                out.append(st)
    return out

def timestamps_from_statewide_csv(path: str) -> list[str]:
    """Return unique timestamps in the order they appear in STATEWIDE_STATS."""
    out, seen = [], set()
    with open(path, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            ts = (row.get("timestamp") or "").strip()
            if ts and ts not in seen:
                seen.add(ts)
                out.append(ts)
    return out

def run(cmd: list[str], cwd: Path | None = None):
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=cwd)

def pause():
    time.sleep(DELAY_SECONDS)

def ensure_file(p: str | Path):
    if not Path(p).is_file():
        sys.exit(f"[error] missing file: {p}")

def ensure_dir(p: str | Path):
    Path(p).mkdir(parents=True, exist_ok=True)

# --------------- MAIN ----------------------
def main():
    args = _parse_cli()
    
    WINPROB_DIR = winprob_dir_for_target(
        args.office,
        args.state,
        args.district
    )
    WINPROB_DIR.mkdir(parents=True, exist_ok=True)

    OUTDIR = WINPROB_DIR
    
    WORKDIR = OUTDIR / "_work"
    WORKDIR.mkdir(parents=True, exist_ok=True)

    # Put intermediates in WORKDIR (NOT ROOT)
    statewide_stats_path = WORKDIR / "output_statewide_stats.csv"


    log(f"[PIPELINE] WINPROB_DIR resolved to: {WINPROB_DIR}")
    log(f"[PIPELINE] OUTDIR set to: {OUTDIR}")



    if args.statewide_input:
        GLOBAL_ARGS["1-statewide.py"] = [
            "--input",  str(Path(args.statewide_input).resolve()),
            "--output", str(WORKDIR / "stats_with_statewide.csv"),
        ]

    if args.stats2_input:
        GLOBAL_ARGS["1-stats-2.py"] = [
            "--input",  str(Path(args.stats2_input).resolve()),
            "--output", str(WORKDIR / "stats2_final_headers.csv"),
        ]

    # Run shared once
    for i, script in enumerate(SHARED_SCRIPTS):
        script_path = ROOT / script
        ensure_file(script_path)

        # run shared stages in the per-target WORKDIR
        run([sys.executable, str(script_path), *GLOBAL_ARGS.get(script, [])], cwd=WORKDIR)

        if script == "1-append-new.py" and args.append_export_dir:
            export_dir = Path(args.append_export_dir).resolve()
            export_dir.mkdir(parents=True, exist_ok=True)

            # ✅ copy from WORKDIR (where 1-append-new.py wrote it)
            src = WORKDIR / "2024_county_results_with_final-old.csv"
            dst = export_dir / src.name
            shutil.copyfile(src, dst)
            print(f"[info] copied append output -> {dst}")

        if i < len(SHARED_SCRIPTS) - 1:
            pause()


    ensure_file(statewide_stats_path)
    ensure_dir(OUTDIR)

    # ---- TARGET STATE LOCK (critical) ----
    if args.state:
        states = [args.state.upper()]
    else:
        states = states_from_statewide_csv(str(statewide_stats_path)) or STATES

    ts_list = timestamps_from_statewide_csv(str(statewide_stats_path))
    if not ts_list:
        sys.exit("[error] no timestamps found in output_statewide_stats.csv")

    if len(ts_list) == 1:
        start_ts = end_ts = ts_list[0]
    else:
        # fallback: use full range if more than one timestamp exists
        start_ts, end_ts = min(ts_list), max(ts_list)
        print(f"[warn] multiple timestamps in input ({len(ts_list)}); using {start_ts} → {end_ts}")

    log(f"[PIPELINE] ENTER win-prob generation | states={states} ts_list={ts_list}")

    for idx, st in enumerate(states, start=1):
        print("\n" + "="*76)
        print(f"[{idx:02d}/{len(states)}] STATE = {st}")
        print("="*76)

        # 5) Per-state snapshots
        snapshots_out = OUTDIR / f"snapshots_{st}.csv"
        run([
            sys.executable, SNAPSHOTS_SCRIPT,
            "--state", st,
            "--input", str(statewide_stats_path),
            "--output", str(snapshots_out),
            "--start", start_ts,
            "--end", end_ts,
            *GLOBAL_ARGS.get(SNAPSHOTS_SCRIPT, [])
        ])
        
        ensure_file(snapshots_out)
        pause()

        # 6) P-value engine → per-state data_output
        data_output = OUTDIR / f"data_output_{st}.csv"
        PARAM_TABLE = str((ROOT / "parameters.csv").resolve())

        run([
            sys.executable, PVAL_SCRIPT,
            str(snapshots_out),
            "-t", PARAM_TABLE,          # 👈 FIX
            "-o", str(data_output),
            *GLOBAL_ARGS.get(PVAL_SCRIPT, [])
        ])


        ensure_file(data_output)
        pause()
        
        # 6.5) Add per-p margins (runs inside OUTDIR because 7-margin-new.py uses hardcoded filenames)
        tmp_in  = OUTDIR / "data_output.csv"
        tmp_out = OUTDIR / "data_output_with_margins_old.csv"   # <-- produced by 7-margin-new.py
        final_with_margins = OUTDIR / f"data_output_{st}_with_margins.csv"

        # Stage the per-state file to the hardcoded input name
        shutil.copyfile(data_output, tmp_in)
        ensure_file(ROOT / "7-margin-new.py")
        run([sys.executable, str(ROOT / "7-margin-new.py")], cwd=OUTDIR)
        ensure_file(tmp_out)
        shutil.move(tmp_out, final_with_margins)
        # Clean the staged input to keep the folder tidy (optional)
        try: tmp_in.unlink()
        except Exception: pass

        
        # 6.7) Recompute statewide %in (state_eevp) BEFORE plotting
        # Input (cwd):  data_output_with_margins.csv
        # Output (cwd): data_output_with_margins-statewide.csv
        eevp_in   = final_with_margins
        staged_in = OUTDIR / "data_output_with_margins_old.csv"   # what 7-eevp-statewide expects as INPUT_CSV
        shutil.copyfile(eevp_in, staged_in)
        run([sys.executable, EEVP_STATE_SCRIPT], cwd=OUTDIR)
        eevp_tmp_out = OUTDIR / "data_output_with_margins.csv"     # produced by 7-eevp-statewide.py
        ensure_file(eevp_tmp_out)
        final_with_statewide = OUTDIR / f"data_output_{st}_with_margins_statewide_eevp.csv"
        shutil.move(eevp_tmp_out, final_with_statewide)
        try: staged_in.unlink()
        except Exception: pass

        # 7) Plot → per-state PNG (+ optional JSONL)
        png_out = OUTDIR / f"statewide_margin_pct_vs_percent_{st}_{PLOT_YEAR}.png"
        jsonl_out = OUTDIR / f"plotdata_{st}_{PLOT_YEAR or 'NA'}_{RUN_STAMP}.jsonl"
        stats_path  = OUTDIR / "stats.csv"
        stats2_path = OUTDIR / "stats2.csv"
        plot_cmd = [
            sys.executable, PLOT_SCRIPT,
            "--input",  str(final_with_statewide),
            "--output", str(png_out),
            "--state",  st,
            "--start", start_ts,
            "--end", end_ts,

            # ✅ Option A: pass these explicitly so 7.py doesn't use its default ROOT/stats.csv
            "--stats",  str(stats_path),
            "--stats2", str(stats2_path),

            *GLOBAL_ARGS.get(PLOT_SCRIPT, [])
        ]

        if PLOT_YEAR is not None:
            plot_cmd += ["--year", str(PLOT_YEAR)]
        # Write a JSONL of plotted points too (7.py supports --jsonl)
        plot_cmd += ["--jsonl", str(jsonl_out)]

        run(plot_cmd)

        # Don’t enforce presence of PNG/JSONL if 7.py conditionally skips, but check if you want:
        if not png_out.exists():
            print(f"[warn] plot not created for {st}")
        pause()

    print("\nAll 50 states complete. See:", str(OUTDIR))
    run([
        sys.executable,
        str(ROOT / "8.py"),
        "--root", str(OUTDIR / "_work"),
        "--outdir", str(OUTDIR),
    ])



if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"[failed] {e}")
    except KeyboardInterrupt:
        sys.exit("[aborted] Interrupted.")
