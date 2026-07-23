#!/usr/bin/env python3
import pandas as pd
import numpy as np
from pathlib import Path

# Hardcoded paths
IN_PATH  = Path("data_output.csv")
OUT_PATH = Path("data_output_with_margins_old.csv")

# Your p-value grid
PVALS = ["0.01","0.1","1","2","3","5","10","15","20","25","30","35","40","45",
         "50","55","60","65","70","75","80","85","90","95","97","98","99","99.9","99.99"]

def pick_first_col(df, names, default=None):
    for n in names:
        if n in df.columns:
            return n
    return default

def main():
    if not IN_PATH.exists():
        raise FileNotFoundError(f"Missing {IN_PATH}")

    df = pd.read_csv(IN_PATH)

    # Snapshot vote columns (H, T, O) — robust to a few common aliases.
    h_col = pick_first_col(df, ["harris_votes", "biden_votes", "dem_votes"])
    t_col = pick_first_col(df, ["trump_votes", "gop_votes", "rep_votes"])
    o_col = pick_first_col(df, ["other_votes", "others_votes", "thirdparty_votes"])

    # %-in (EEVP) column — accept common variants.
    eevp_col = pick_first_col(df, ["eevp", "percent_in", "pct_in", "county_eevp"])

    # Prepare vectors; if any are missing, fall back to zeros (except EEVP).
    H_snap = pd.to_numeric(df[h_col], errors="coerce").fillna(0.0) if h_col else pd.Series(0.0, index=df.index)
    T_snap = pd.to_numeric(df[t_col], errors="coerce").fillna(0.0) if t_col else pd.Series(0.0, index=df.index)
    O_snap = pd.to_numeric(df[o_col], errors="coerce").fillna(0.0) if o_col else pd.Series(0.0, index=df.index)

    snap_tot = H_snap + T_snap + O_snap
    base_margin_votes = H_snap - T_snap

    if eevp_col:
        EEVP = pd.to_numeric(df[eevp_col], errors="coerce")
        denom = EEVP / 100.0
        # NEW: for county EEVP==0, force margin_{p}_new = H_fallback - T_fallback
        eevp0_mask = (EEVP.fillna(-1) == 0)

        h_fb_col = "harris_normalized_fallback_votes"
        t_fb_col = "trump_normalized_fallback_votes"
        if (h_fb_col in df.columns) and (t_fb_col in df.columns):
            H_fb = pd.to_numeric(df[h_fb_col], errors="coerce").fillna(0.0)
            T_fb = pd.to_numeric(df[t_fb_col], errors="coerce").fillna(0.0)
            fallback_margin_votes = H_fb - T_fb
        else:
            fallback_margin_votes = None

        with np.errstate(divide="ignore", invalid="ignore"):
            total_expected = np.where(denom > 0, snap_tot / denom, np.nan)
        remaining = pd.Series(total_expected - snap_tot, index=df.index)
        remaining = remaining.where(remaining >= 0, 0.0)  # guard tiny negatives
        lower = base_margin_votes - remaining
        upper = base_margin_votes + remaining
    else:
        # Without EEVP we cannot expand to expected total — bound collapses to current margin
        lower = base_margin_votes.copy()
        upper = base_margin_votes.copy()

    # Compute per-p margins (H−T from the corresponding *_votes_new) and clamp to [lower, upper]
    for p in PVALS:
        h_new_col = f"trump_cond_{p}_harris_votes_new"
        t_new_col = f"trump_cond_{p}_trump_votes_new"
        out_col   = f"margin_{p}_new"

        if h_new_col in df.columns and t_new_col in df.columns:
            H_new = pd.to_numeric(df[h_new_col], errors="coerce")
            T_new = pd.to_numeric(df[t_new_col], errors="coerce")
            margin_new_votes = H_new - T_new
            df[out_col] = margin_new_votes.clip(lower=lower, upper=upper)
        else:
            df[out_col] = np.nan  # ensure the column exists

        # NEW: if county EEVP==0, use fallback margin (no clamping)
        if eevp_col and (fallback_margin_votes is not None):
            df.loc[eevp0_mask, out_col] = fallback_margin_votes.loc[eevp0_mask]


    df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {OUT_PATH}")

if __name__ == "__main__":
    main()
