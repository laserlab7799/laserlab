#!/usr/bin/env python3
import os
import argparse
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import json
import json  # NEW

OUTPUT_JSONL = "probability_plot_points.jsonl"  # NEW


# ---------- defaults (override via CLI) ----------
INPUT_CSV              = "data_output_with_margins.csv"
TARGET_STATE           = "PA"
TARGET_TIMESTAMP_START = "2024-11-05T18-00-22"
TARGET_TIMESTAMP_END   = "2024-11-07T09-30-22"
INTERVAL_MINUTES       = 30

OUTPUT_CSV             = "probs_timeseries_{state}.csv"
OUTPUT_PNG             = "probs_timeseries_{state}.png"

MC_TRIALS = 200_000
MC_CHUNK  = 50_000
SEED      = 12345

# p-grid suffixes used by margin_{p}_new
PGRID = [0.01, 0.1, 1, 2, 3, 5,
         10, 15, 20, 25, 30, 35, 40, 45, 50,
         55, 60, 65, 70, 75, 80, 85, 90,
         95, 97, 98, 99, 99.9, 99.99]

# ---------------- helpers ----------------
def quantile_cell_weights(pvals: list[float]) -> np.ndarray:
    """
    Given sorted (not required) p-values in [0, 100], return weights proportional
    to each point's Voronoi cell width on the 0..100 axis. This approximates
    sampling a continuous quantile q ~ Uniform(0,100) and 'snapping' to nearest p.
    """
    if not pvals:
        return np.array([], dtype=float)
    ps = np.array(sorted(pvals), dtype=float)
    n = ps.size
    # distances to neighbors
    left_gap  = np.empty(n, dtype=float)
    right_gap = np.empty(n, dtype=float)
    left_gap[0]  = ps[0] - 0.0
    right_gap[-1] = 100.0 - ps[-1]
    if n > 1:
        left_gap[1:]  = (ps[1:] - ps[:-1]) / 2.0
        right_gap[:-1] = (ps[1:] - ps[:-1]) / 2.0
    else:
        # single column: it represents the whole range
        left_gap[0] = ps[0] - 0.0
        right_gap[0] = 100.0 - ps[0]
    widths = left_gap + right_gap
    # Map back to original (unsorted) order if needed
    # Build index map from sorted ps to original pvals order:
    order = np.argsort(pvals)
    inv = np.empty_like(order)
    inv[order] = np.arange(n)
    w = widths[inv].astype(float)
    w = w / w.sum()
    return w

def _sum_votes(snap: pd.DataFrame, cols: list[str]) -> float:
    """
    Sum per-county vote columns across all rows (counties) for this snapshot.
    """
    if not cols:
        return 0.0
    block = snap[cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return float(block.to_numpy(dtype=float).sum())

def statewide_counted_and_final_total_estimate(snap: pd.DataFrame, statewide_percent: float) -> float:
    """
    final_total_est ≈ counted_statewide_total / (statewide_percent / 100)
    If *_votes_statewide exist, read a single non-null value per column (statewide totals).
    Else, fall back to summing per-county votes across rows.
    """
    counted_cols_statewide = ["trump_votes_statewide", "harris_votes_statewide", "other_votes_statewide"]

    if all(c in snap.columns for c in counted_cols_statewide):
        counted_total = 0.0
        for c in counted_cols_statewide:
            s = pd.to_numeric(snap[c], errors="coerce").dropna()
            if not s.empty:
                counted_total += float(s.iloc[0])  # use the statewide total once
        # if all three were missing/NaN, counted_total stays 0.0
    else:
        # fall back to summing per-county (robust)
        per_county_cols = ["trump_votes", "harris_votes", "other_votes"]
        present = [c for c in per_county_cols if c in snap.columns]
        counted_total = _sum_votes(snap, present) if present else 0.0

    if statewide_percent <= 0:
        return float("nan")
    return counted_total / (statewide_percent / 100.0)

    
def _p_label(p):
    if isinstance(p, float) and p.is_integer():
        p = int(p)
    return str(p)

def read_csv(path: str) -> pd.DataFrame:
    # robust load (auto-delimiter, python engine; no low_memory)
    return pd.read_csv(path, dtype=str, sep=None, engine="python")
    
def stats_fips_mismatch(stats_path: str, stats2_path: str, state: str) -> bool:
    """
    Return True if stats vs stats2 are NOT aligned:
      - different number of rows after filtering to state
      - different multiset of FIPS values (counts matter)
      - missing fips column in either
    """
    def _load_fips_counter(p: str) -> Counter:
        df = read_csv(p)

        # Filter to state if possible
        if "state" in df.columns:
            df = df.loc[df["state"].astype(str).str.upper() == state.upper()].copy()

        if "fips" not in df.columns:
            # try common alternatives if you ever rename
            for alt in ["FIPS", "county_fips"]:
                if alt in df.columns:
                    df["fips"] = df[alt]
                    break

        if "fips" not in df.columns:
            return Counter({"__MISSING_FIPS_COL__": 1})

        f = df["fips"].astype(str).str.strip()

        # normalize common junk
        f = f.replace({"nan": "", "None": ""})
        # standardize digits (keeps non-numeric as-is, but those will mismatch too)
        f = f.apply(lambda x: x.zfill(5) if x.isdigit() else x)

        # drop blank/zero fips (optional; remove these 3 lines if you want them counted)
        f = f[(f != "") & (f != "0") & (f != "00000")]

        return Counter(f.tolist())

    p1 = str(Path(stats_path))
    p2 = str(Path(stats2_path))

    # If either file is missing, treat as mismatch (forces penalty=100)
    if not os.path.isfile(p1) or not os.path.isfile(p2):
        return True

    c1 = _load_fips_counter(p1)
    c2 = _load_fips_counter(p2)
    return c1 != c2


def _to_num(s, default=np.nan):
    s = pd.to_numeric(s, errors="coerce")
    if not (isinstance(default, float) and np.isnan(default)):
        s = s.fillna(default)
    return s

def statewide_percent_in(snap: pd.DataFrame) -> float:
    """
    Return the statewide % in exactly as stored (no implicit scaling).
    Only accepts explicit statewide fields; never falls back to county 'eevp'.
    """
    candidates = [
        "state_eevp", "statewide_eevp", "eevp_statewide",
        "percent_in_statewide", "state_percent_in", "statewide_percent_in"
    ]
    for c in candidates:
        if c in snap.columns:
            s = pd.to_numeric(snap[c], errors="coerce").dropna()
            if not s.empty:
                v = float(s.iloc[0])
                # Do NOT multiply by 100. Use the exact stored value.
                return float(np.clip(v, 0.0, 100.0))

    # If you prefer to fail hard rather than guess, raise:
    raise RuntimeError("No explicit statewide %in column found for this snapshot.")


def build_margin_matrix(snap: pd.DataFrame) -> tuple[np.ndarray, list[float]]:
    # M[i, j] = margin_{p}_new (H−T) for county i, p-index j; NaN→0
    cols = []
    pvals = []
    for p in PGRID:
        lab = _p_label(p)
        col = f"margin_{lab}_new"
        if col in snap.columns:
            cols.append(col)
            # keep the numeric p corresponding to this column, as float
            pvals.append(float(p))
    if not cols:
        raise RuntimeError("No margin_{p}_new columns present in this snapshot.")
    M = snap[cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    return M, pvals


def trump_win_pct_from_matrix(
    M: np.ndarray,
    trials: int,
    chunk: int,
    seed: int | None,
    prob: np.ndarray | None = None
) -> float:
    n_counties, n_p = M.shape
    if n_counties == 0 or n_p == 0:
        return np.nan
    rng = np.random.default_rng(seed)
    wins = 0
    done = 0
    county_idx = np.arange(n_counties)[:, None]
    while done < trials:
        t = min(chunk, trials - done)
        if prob is not None:
            # non-uniform over p-indices (same probs for every county/trial)
            idx = rng.choice(n_p, size=(n_counties, t), p=prob)
        else:
            # fallback: uniform over columns
            idx = rng.integers(low=0, high=n_p, size=(n_counties, t), endpoint=False)
        chosen = M[county_idx, idx]
        totals = chosen.sum(axis=0)
        wins += (totals < 0).sum()
        done += t
    return 100.0 * wins / done


def margin_pct_box_stats_from_matrix(
    M: np.ndarray,
    trials: int,
    chunk: int,
    seed: int | None,
    final_total_est: float,
    prob: np.ndarray | None = None
) -> dict[str, float]:
    n_counties, n_p = M.shape
    if n_counties == 0 or n_p == 0 or not np.isfinite(final_total_est) or final_total_est <= 0:
        return {"med": np.nan, "q1": np.nan, "q3": np.nan, "whislo": np.nan, "whishi": np.nan, "mean": np.nan, "fliers": []}

    rng = np.random.default_rng(seed)
    county_idx = np.arange(n_counties)[:, None]
    margins_pct = np.empty(trials, dtype=float)
    filled = 0

    while filled < trials:
        t = min(chunk, trials - filled)
        if prob is not None:
            idx = rng.choice(n_p, size=(n_counties, t), p=prob)
        else:
            idx = rng.integers(low=0, high=n_p, size=(n_counties, t), endpoint=False)
        chosen = M[county_idx, idx]
        totals = chosen.sum(axis=0)
        margins_pct[filled:filled+t] = 100.0 * (totals / final_total_est)
        filled += t

    q1, med, q3 = np.percentile(margins_pct, [25, 50, 75])
    iqr = q3 - q1
    whislo = np.min(margins_pct[margins_pct >= q1 - 1.5 * iqr], initial=q1)
    whishi = np.max(margins_pct[margins_pct <= q3 + 1.5 * iqr], initial=q3)
    mean = float(np.mean(margins_pct))
    return {"med": float(med), "q1": float(q1), "q3": float(q3),
            "whislo": float(whislo), "whishi": float(whishi),
            "mean": mean, "fliers": []}



def percent_no_data(snap: pd.DataFrame) -> float:
    """
    Percent (0..100) of counties with 'no data' at this snapshot.
    'No data' = county %in missing or <= 0. Tries common county-level %in names.
    """
    candidates = ["eevp", "percent_in", "pct_in", "county_eevp", "county_percent_in"]
    eevp_col = next((c for c in snap.columns if c.lower() in candidates), None)
    if not eevp_col:
        return 0.0
    s = pd.to_numeric(snap[eevp_col], errors="coerce")
    if s.size == 0:
        return 0.0
    no_data_mask = ~s.notna() | (s <= 0)
    denom = int((s.notna()).sum())
    if denom <= 0:
        return 0.0
    return float(no_data_mask.sum() * 100.0 / denom)

def normalize_ts(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H-%M-%S")

# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=INPUT_CSV)
    ap.add_argument("--state", default=TARGET_STATE)
    ap.add_argument("--start", default=TARGET_TIMESTAMP_START)
    ap.add_argument("--end",   default=TARGET_TIMESTAMP_END)
    ap.add_argument("--interval", type=int, default=INTERVAL_MINUTES)
    ap.add_argument("--trials",   type=int, default=MC_TRIALS)
    ap.add_argument("--chunk",    type=int, default=MC_CHUNK)
    ap.add_argument("--seed", type=int, default=None,
                    help="Optional fixed seed. Omit for true randomness.")
    ap.add_argument("--out_csv",  default=OUTPUT_CSV)
    ap.add_argument("--out_png",  default=OUTPUT_PNG)
    # --- Aliases for pipeline compatibility ---
    ap.add_argument("--output", dest="out_png",
                    help="Alias of --out_png for pipeline compatibility")
    ap.add_argument("--year", type=int, default=None,
                    help="Optional year label (used in chart titles only)")
    ap.add_argument("--jsonl", dest="jsonl_path", default=None,
                    help="If set, write probability_plot_points.jsonl to this path")
    # opt-in plot JSONL (OFF by default)
    # opt-in plot JSONL (OFF by default). --jsonl implies this automatically.
    ap.add_argument("--plot-jsonl", action="store_true",
                    help=f"Write {OUTPUT_JSONL} (default OFF; implied by --jsonl)")
    # --- NEW: opt-in debug (OFF by default) ---
    ap.add_argument("--trace-jsonl", action="store_true",
                    help="Write per-timestamp mc_trace_*.jsonl (default OFF)")
    ap.add_argument("--trace-print", action="store_true",
                    help="Print p-grid/weights and per-trial county p/margins (default OFF)")
    default_stats  = str(Path(__file__).resolve().parent / "stats.csv")
    default_stats2 = str(Path(__file__).resolve().parent / "stats2.csv")
    ap.add_argument("--stats",  default=default_stats,  help="Path to stats.csv (for alignment check)")
    ap.add_argument("--stats2", default=default_stats2, help="Path to stats2.csv (for alignment check)")


    args = ap.parse_args()
    # Format output paths now that state is known
    args.out_png = (args.out_png or OUTPUT_PNG).format(state=args.state.upper())
    args.out_csv = (args.out_csv or OUTPUT_CSV).format(state=args.state.upper())
    # Resolve JSONL behavior/path
    plot_jsonl = bool(args.jsonl_path) or args.plot_jsonl
    jsonl_path = args.jsonl_path or OUTPUT_JSONL
    if not os.path.isfile(args.input):
        print(f"[error] missing {args.input}")
        return

    try:
        df = read_csv(args.input)
    except Exception as e:
        print(f"[error] failed to read {args.input}: {e}")
        return

    if "state" not in df.columns or "timestamp" not in df.columns:
        print("[error] CSV missing required 'state' or 'timestamp'")
        return

    # Filter to state
    df = df.loc[df["state"].astype(str).str.upper() == args.state.upper()].copy()
    if df.empty:
        print(f"[error] no rows for state={args.state}")
        return
    # If stats/stats2 don't match on FIPS, force max penalty
    # If stats/stats2 don't match on FIPS, flag it (penalty applied only if %nodata > 0)
    stats_mismatch = stats_fips_mismatch(args.stats, args.stats2, args.state)
    if stats_mismatch:
        print(f"[warn] stats/stats2 FIPS mismatch for state={args.state}; will apply penalty_pts=100 only if %nodata>0")


    # Build normalized 30-min timestamp grid
    start_ts = pd.to_datetime(args.start, format="%Y-%m-%dT%H-%M-%S", errors="raise")
    end_ts   = pd.to_datetime(args.end,   format="%Y-%m-%dT%H-%M-%S", errors="raise")
    grid = pd.date_range(start=start_ts, end=end_ts, freq=f"{args.interval}min")
    targets = [normalize_ts(t) for t in grid]

    rows = []
    print(f"[info] evaluating {len(targets)} timestamps from {args.start} to {args.end} every {args.interval} minutes")
  
    box_positions: list[float] = []
    box_stats: list[dict] = []
    box_timestamps: list[str] = []   # <— add this


    for ts in targets:
        snap = df.loc[df["timestamp"] == ts].copy()
        if snap.empty:
            continue

        # Build margin matrix for this snapshot
        # Build margin matrix and p-grid for this snapshot
        try:
            M, pvals = build_margin_matrix(snap)
        except RuntimeError:
            continue

        # Compute non-uniform probabilities over the included p’s
        prob = quantile_cell_weights(pvals)
        if args.trace_print:
            print("[p-grid]", pvals)
            print("[weights]", np.round(prob, 6), "sum=", prob.sum())
        # ----- MC TRACE: per-county p and chosen margin, 5 trials -----
        TRACE_TRIALS = 5
        # Only create a trace RNG if we’re going to use it
        if args.trace_jsonl or args.trace_print:
            rng_trace = np.random.default_rng(args.seed)   # None ⇒ fresh entropy

        # county labels (best-effort)
        county_labels = None
        if "county" in snap.columns:
            county_labels = snap["county"].astype(str).tolist()
        elif "final_county" in snap.columns:
            county_labels = snap["final_county"].astype(str).tolist()

        n_counties, n_p = M.shape
        if (args.trace_jsonl or args.trace_print) and n_counties > 0 and n_p > 0:
            # draw per-county column indices for 5 trials using your non-uniform weights
            idx = rng_trace.choice(n_p, size=(n_counties, TRACE_TRIALS), p=prob)
            county_idx = np.arange(n_counties)[:, None]

            # map to actual p-values (same shape as idx)
            p_used = np.array(pvals, dtype=float)[idx]  # (n_counties, 5)

            # pick margins from M at those columns
            chosen = M[county_idx, idx]                 # (n_counties, 5)

            # write JSONL (one record per trial) and echo trial 0 to console
            # Optionally write the JSONL trace
            if args.trace_jsonl:
                trace_path = f"mc_trace_{args.state.upper()}_{ts}.jsonl"
                with open(trace_path, "w", encoding="utf-8") as f:
                    for j in range(TRACE_TRIALS):
                        rec = {
                            "trial": j,
                            "mode": "discrete",
                            "county_p_used": p_used[:, j].tolist(),
                            "county_margins": chosen[:, j].tolist(),
                        }
                        if county_labels:
                            rec["counties"] = county_labels
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if args.trace_print:
                    print(f"[trace] saved {trace_path}")

            # Optionally print full per-trial details
            if args.trace_print:
                for j in range(TRACE_TRIALS):
                    if county_labels:
                        print(f"[trace trial {j}] ALL counties (name, p, margin):")
                        for name, pval, margin in zip(county_labels, p_used[:, j].tolist(), chosen[:, j].tolist()):
                            print(f"    {name}: p={pval}, margin={margin:.3f}")
                    else:
                        print(f"[trace trial {j}] ALL counties (p, margin):")
                        for pval, margin in zip(p_used[:, j].tolist(), chosen[:, j].tolist()):
                            print(f"    p={pval}, margin={margin:.3f}")

        # ---------------------------------------------------------------


        # Raw MC
        trump_raw = trump_win_pct_from_matrix(M, trials=args.trials, chunk=args.chunk, seed=args.seed, prob=prob)
        harris_raw = 100.0 - trump_raw

        # New penalty: subtract (% no-data)/3 from the **leader**, floor at 50%; trailer = 100 - leader
        percent_nd = percent_no_data(snap)  # 0..100

        # 100pt penalty ONLY if mismatch AND some counties have no-data
        if stats_mismatch and percent_nd > 0:
            penalty_pts = 100.0
        else:
            penalty_pts = (percent_nd / 0.5)

        if trump_raw >= harris_raw:
            leader_raw = trump_raw
            leader_name = "Trump"
        else:
            leader_raw = harris_raw
            leader_name = "Harris"

        # Base adjusted leader after existing penalties (no-data / mismatch), floor at 50.
        # We'll apply the box-based gates AFTER we compute bx.
        leader_adj_base = max(50.0, leader_raw - penalty_pts)


        # X coordinate = statewide % in
        try:
            x = statewide_percent_in(snap)
        except RuntimeError:
            continue

        # estimate final statewide total votes for this snapshot
        final_total_est = statewide_counted_and_final_total_estimate(snap, x)

        # build box/whisker stats for final statewide margin% across trials
        bx = margin_pct_box_stats_from_matrix(
                M, trials=args.trials, chunk=args.chunk, seed=args.seed,
                final_total_est=final_total_est, prob=prob
            )
        # ---- NEW: box-based gating of adjusted win prob (leader-only) ----
        q1 = bx.get("q1", np.nan)
        q3 = bx.get("q3", np.nan)

        # default: no extra adjustment
        extra_reduce = 0.0
        cap_65 = False

        if np.isfinite(q1) and np.isfinite(q3):
            # Stronger rule first: if the IQR endpoints hug 0, cap leader at 65
            if (-0.5 <= q1 <= 0.5) and (-0.5 <= q3 <= 0.5):
                cap_65 = True
            # Otherwise, if still fairly tight around 0, reduce leader by 5
            elif (-1.0 <= q1 <= 1.0) and (-1.0 <= q3 <= 1.0):
                extra_reduce = 5.0

        leader_adj = max(50.0, leader_adj_base - extra_reduce)
        if cap_65:
            leader_adj = min(leader_adj, 65.0)

        trailer_adj = 100.0 - leader_adj
        trump_adj  = leader_adj if leader_name == "Trump" else trailer_adj
        harris_adj = leader_adj if leader_name == "Harris" else trailer_adj

        box_positions.append(x)
        box_stats.append(bx)
        box_timestamps.append(ts)   # <— add this

        rows.append({
            "timestamp": ts,
            "state": args.state.upper(),
            "statewide_percent_in": x,
            "trump_win_pct_raw": trump_raw,
            "harris_win_pct_raw": harris_raw,
            "percent_counties_no_data": percent_nd,
            "penalty_points_applied": penalty_pts,
            "leader_before": leader_name,
            "trump_win_pct": trump_adj,
            "harris_win_pct": harris_adj,
        })

        if args.trace_print:
            print(f"[{ts}] %in={x:6.2f}  Trump_raw={trump_raw:6.2f}%  Biden_raw={harris_raw:6.2f}%  "
                  f"no-data={percent_nd:5.2f}%  penalty={penalty_pts:5.2f}  leader={leader_name:<6}  "
                  f"→  Trump={trump_adj:6.2f}%  Biden={harris_adj:6.2f}%")

    if not rows:
        print("[warn] no points produced in requested range.")
        return

    out = pd.DataFrame(rows).sort_values("statewide_percent_in")
    out.to_csv(args.out_csv, index=False)
    print(f"[saved] {args.out_csv}  ({len(out)} points)")
    # NEW — only plot points where statewide %in > 0
    plot_mask = out["statewide_percent_in"] > 0
    out_plot = out.loc[plot_mask].copy()
    # --- JSONL for the probability plot (non-box), INCLUDING penalty + raw + 100dp fields
    # --- JSONL for the probability plot (non-box), INCLUDING penalty + raw + 100dp fields
    import math  # optional to keep here; or move to top
    from decimal import Decimal, getcontext, ROUND_DOWN
    # (remove the inner 'import json')


    # high precision for 100dp formatting
    getcontext().prec = 120
    _Q100 = Decimal("1." + "0"*100)

    def _fmt100(x):
        """Return a string with exactly 100 decimal places, or None if not finite."""
        if x is None:
            return None
        # handle pandas/NumPy NaNs & infs
        try:
            if pd.isna(x) or (isinstance(x, (float, np.floating)) and not np.isfinite(x)):
                return None
        except Exception:
            pass
        try:
            d = Decimal(str(x))
            return format(d.quantize(_Q100, rounding=ROUND_DOWN), "f")
        except Exception:
            # last resort
            try:
                return f"{float(x):.100f}"
            except Exception:
                return None

    def _pick(row, names):
        for n in names:
            if n in row and pd.notna(row[n]):
                return row[n]
        return np.nan

    def _emit_point(rec_list, *, ts, st, x, cand, win_p, series_type,
                    penalty_points, no_data_pct, raw_t, raw_h):
        if np.isfinite(x) and win_p is not None and (not isinstance(win_p, (float, np.floating)) or np.isfinite(win_p)):
            rec = {
                "state": st,
                "timestamp": ts,
                "x_statewide_percent_in": float(x),
                "candidate": cand,                     # "T" or "H"
                "series_type": series_type,            # "adjusted" | "raw"

                # numeric (as before)
                "win_probability": (float(win_p) if win_p is not None and not pd.isna(win_p) else None),

                # NEW: fixed-width strings with 100 decimal places
                "win_probability_100dp": _fmt100(win_p),

                # context (numeric, unchanged)
                "penalty_points": (float(penalty_points)
                                   if pd.notna(penalty_points) and not isinstance(penalty_points, str)
                                   else (float(str(penalty_points).replace("%",""))
                                         if isinstance(penalty_points, str) and penalty_points.strip()
                                         else None)),
                "no_data_pct": (float(no_data_pct)
                                if pd.notna(no_data_pct) and not isinstance(no_data_pct, str)
                                else (float(str(no_data_pct).replace("%",""))
                                      if isinstance(no_data_pct, str) and no_data_pct.strip()
                                      else None)),

                # raw snapshot win% for both candidates (numeric as before) ...
                "trump_win_pct_raw_snapshot": (float(raw_t) if raw_t is not None and not pd.isna(raw_t) else None),
                "harris_win_pct_raw_snapshot": (float(raw_h) if raw_h is not None and not pd.isna(raw_h) else None),

                # ... and NEW: 100dp strings
                "trump_win_pct_raw_snapshot_100dp": _fmt100(raw_t),
                "harris_win_pct_raw_snapshot_100dp": _fmt100(raw_h),
            }
            rec_list.append(rec)

    jsonl = []
    for _, r in out_plot.iterrows():
        ts = r["timestamp"]
        st = r["state"]
        x  = r["statewide_percent_in"]

        # Pull penalty and no-data (accept common aliases)
        penalty_points = _pick(r, ["penalty_points", "penalty", "penalty_pp"])
        no_data_pct    = _pick(r, ["no_data_pct", "no_data", "no_data_percent"])

        # Pull the raw win % columns (accept common aliases)
        raw_t = _pick(r, ["trump_win_pct_raw", "trump_raw", "trump_raw_win_pct"])
        raw_h = _pick(r, ["harris_win_pct_raw", "harris_raw", "harris_raw_win_pct"])

        # Adjusted (the lines you draw)
        _emit_point(jsonl, ts=ts, st=st, x=x, cand="T",
                    win_p=r.get("trump_win_pct"),
                    series_type="adjusted",
                    penalty_points=penalty_points, no_data_pct=no_data_pct,
                    raw_t=raw_t, raw_h=raw_h)

        _emit_point(jsonl, ts=ts, st=st, x=x, cand="H",
                    win_p=r.get("harris_win_pct"),
                    series_type="adjusted",
                    penalty_points=penalty_points, no_data_pct=no_data_pct,
                    raw_t=raw_t, raw_h=raw_h)

        # Raw (the x/+ markers you plot)
        _emit_point(jsonl, ts=ts, st=st, x=x, cand="T",
                    win_p=raw_t,
                    series_type="raw",
                    penalty_points=penalty_points, no_data_pct=no_data_pct,
                    raw_t=raw_t, raw_h=raw_h)

        _emit_point(jsonl, ts=ts, st=st, x=x, cand="H",
                    win_p=raw_h,
                    series_type="raw",
                    penalty_points=penalty_points, no_data_pct=no_data_pct,
                    raw_t=raw_t, raw_h=raw_h)

    if plot_jsonl:
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for rec in jsonl:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[saved] {jsonl_path}  ({len(jsonl)} points)")


    """
    # Plot: lines vs statewide % in (show adjusted series)
    fig, ax = plt.subplots(figsize=(7.5, 4.5))

    # NEW — raw series (points only; show where lines like "Trump_raw=100.00%  Harris_raw=  0.00%" land)
    ax.plot(out_plot["statewide_percent_in"], out_plot["trump_win_pct_raw"],
            marker="x", linestyle="none", label="Trump win % (raw)")
    ax.plot(out_plot["statewide_percent_in"], out_plot["harris_win_pct_raw"],
            marker="+", linestyle="none", label="Biden win % (raw)")

    # Existing adjusted series — now using out_plot so %in=0 isn’t plotted
    ax.plot(out_plot["statewide_percent_in"], out_plot["trump_win_pct"],
            marker="o", linewidth=1.5, label="Trump win % (leader-penalized)")
    ax.plot(out_plot["statewide_percent_in"], out_plot["harris_win_pct"],
            marker="o", linewidth=1.5, label="Biden win % (leader-penalized)")

    ax.axhline(50, linestyle="--", linewidth=1, alpha=0.7)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Statewide % in")
    ax.set_ylabel("Win probability (%)")
    ax.set_title(f"{args.state.upper()} Win % vs statewide % in\n{args.start} → {args.end}, "
                 f"{args.interval}-min steps, trials={args.trials:,} (no-data penalty)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(args.out_png, dpi=200)
    
    """
    
    # Plot: lines vs statewide % in (show adjusted series)
    fig, ax = plt.subplots(figsize=(7.5, 4.5))

    # Force colors: Trump = red, Harris = blue; raw = light red/blue
    TRUMP_COLOR       = "#d62728"  # red
    HARRIS_COLOR      = "#1f77b4"  # blue
    TRUMP_RAW_COLOR   = "#f4a6a6"  # light red
    HARRIS_RAW_COLOR  = "#a6c8ff"  # light blue

    # Raw series (points only; no legend labels)
    ax.plot(out_plot["statewide_percent_in"], out_plot["trump_win_pct_raw"],
            marker="x", linestyle="none", color=TRUMP_RAW_COLOR)
    ax.plot(out_plot["statewide_percent_in"], out_plot["harris_win_pct_raw"],
            marker="+", linestyle="none", color=HARRIS_RAW_COLOR)

    # Adjusted series (lines; no legend labels)
    ax.plot(out_plot["statewide_percent_in"], out_plot["trump_win_pct"],
            marker="o", linewidth=1.5, color=TRUMP_COLOR)
    ax.plot(out_plot["statewide_percent_in"], out_plot["harris_win_pct"],
            marker="o", linewidth=1.5, color=HARRIS_COLOR)

    ax.axhline(50, linestyle="--", linewidth=1, alpha=0.7)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Statewide % in")
    ax.set_ylabel("Win probability (%)")
    title_year = f" {args.year}" if args.year else ""
    ax.set_title(f"{args.state.upper()}{title_year} MC win % vs statewide % in\n"
                 f"{args.start} → {args.end}, {args.interval}-min steps, trials={args.trials:,} (leader-only no-data penalty)")
    ax.grid(True, alpha=0.3)

    # Remove legend
    # ax.legend(loc="best")

    fig.tight_layout()
    fig.savefig(args.out_png, dpi=200)

    # ---- Box & whisker PNG of final statewide margin% vs statewide %in ----
    box_png = args.out_png.replace(".png", "_box.png")
    fig2, ax2 = plt.subplots(figsize=(8.0, 4.8))

    # Sort by x so the boxes follow %in left→right
    order = np.argsort(np.array(box_positions))
    pos_sorted = np.array(box_positions, dtype=float)[order]
    stats_sorted = [box_stats[i] for i in order]
    # ---- Save JSON with the box data used for plotting ----
    # Sort timestamps the same way as positions/stats
    ts_sorted = [box_timestamps[i] for i in order]

    box_json = []
    for ts, xval, s in zip(ts_sorted, pos_sorted, stats_sorted):
        # s is the dict returned by margin_pct_box_stats_from_matrix
        # Ensure floats (JSON-serializable) and include timestamp and trials meta
        box_json.append({
            "timestamp": ts,
            "statewide_percent_in": float(xval),
            "mean": float(s.get("mean")) if s.get("mean") is not None else None,
            "median": float(s.get("med")) if s.get("med") is not None else None,
            "q1": float(s.get("q1")) if s.get("q1") is not None else None,
            "q3": float(s.get("q3")) if s.get("q3") is not None else None,
            "whisker_low": float(s.get("whislo")) if s.get("whislo") is not None else None,
            "whisker_high": float(s.get("whishi")) if s.get("whishi") is not None else None,
            "trials": int(args.trials)
        })

    box_json_path = args.out_png.replace(".png", "_box.json")
    with open(box_json_path, "w", encoding="utf-8") as f:
        json.dump(box_json, f, ensure_ascii=False, indent=2)
    print(f"[saved] {box_json_path}")


    # Draw boxplot at explicit x positions; show mean markers
    bp = ax2.bxp(stats_sorted, positions=pos_sorted, showmeans=True, meanline=False)

    ax2.axhline(0, linestyle="--", linewidth=1, alpha=0.6)
    ax2.set_xlim(0, 100)
    ax2.set_xlabel("Statewide % in")
    ax2.set_ylabel("Final statewide margin (Harris − Trump)  %")
    ax2.set_title(f"{args.state.upper()}{title_year} MC distribution of FINAL margin% vs statewide % in\n"
                  f"{args.start} → {args.end}, {args.interval}-min steps, trials={args.trials:,}")
    ax2.grid(True, alpha=0.3)

    fig2.tight_layout()
    fig2.savefig(box_png, dpi=200)
    plt.close(fig2)
    print(f"[saved] {box_png}")

    plt.close(fig)
    print(f"[saved] {args.out_png}")

if __name__ == "__main__":
    main()
