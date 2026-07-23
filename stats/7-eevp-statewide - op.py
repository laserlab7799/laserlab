#!/usr/bin/env python3
import pandas as pd
import numpy as np

# --- Hardcoded paths ---
INPUT_CSV  = "data_output_with_margins_old.csv"
OUTPUT_CSV = "data_output_with_margins.csv"

# --- Required column names ---
REQ = [
    "timestamp", "state",
    "trump_votes", "harris_votes", "other_votes",
    "final_trump_votes", "final_harris_votes", "final_other_votes",
    "state_eevp",  # will be replaced
]

def main():
    # Read CSV
    df = pd.read_csv(INPUT_CSV)

    # Sanity check for columns
    missing = [c for c in REQ if c not in df.columns]
    if missing:
        raise SystemExit(f"Missing required columns: {missing}")

    # Coerce numeric columns used in the calc
    num_cols = [
        "trump_votes", "harris_votes", "other_votes",
        "final_trump_votes", "final_harris_votes", "final_other_votes"
    ]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Group by (timestamp, state) and compute sums
    grp = df.groupby(["timestamp", "state"], as_index=True).agg(
        cur_trump=("trump_votes", "sum"),
        cur_harris=("harris_votes", "sum"),
        cur_other=("other_votes", "sum"),
        fin_trump=("final_trump_votes", "sum"),
        fin_harris=("final_harris_votes", "sum"),
        fin_other=("final_other_votes", "sum"),
    )

    # Compute state_eevp per (timestamp, state)
    cur_sum = grp["cur_trump"].fillna(0) + grp["cur_harris"].fillna(0) + grp["cur_other"].fillna(0)
    fin_sum = grp["fin_trump"].fillna(0) + grp["fin_harris"].fillna(0) + grp["fin_other"].fillna(0)

    with np.errstate(divide="ignore", invalid="ignore"):
        state_eevp_calc = 100.0 * (cur_sum / fin_sum)
    # If final total is zero or NaN, result should be NaN (can’t compute % in)
    state_eevp_calc = state_eevp_calc.where(np.isfinite(state_eevp_calc))

    grp["state_eevp_calc"] = state_eevp_calc

    # Join back and overwrite df['state_eevp']
    df = df.join(grp["state_eevp_calc"], on=["timestamp", "state"])
    df["state_eevp"] = df["state_eevp_calc"]

    # Optional: keep more precision; comment/adjust as preferred
    # df["state_eevp"] = df["state_eevp"].round(6)

    # Drop the helper column
    df = df.drop(columns=["state_eevp_calc"])

    # Write output
    df.to_csv(OUTPUT_CSV, index=False)

    # Quick summary
    total_groups = grp.shape[0]
    nan_groups = grp["state_eevp_calc"].isna().sum()
    print(f"Wrote {OUTPUT_CSV}. Recomputed state_eevp for {total_groups} (timestamp, state) groups.")
    if nan_groups:
        print(f"Note: {nan_groups} groups had zero/NaN final totals → state_eevp set to NaN.")

if __name__ == "__main__":
    main()
