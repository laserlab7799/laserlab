#!/usr/bin/env python3
import pandas as pd

IN_FILE  = "2024_county_results_with_calcs-old.csv"
OUT_FILE = "2024_county_results_with_calcs.csv"

def main():
    df = pd.read_csv(IN_FILE)

    # Remember original position of 'eevp' so we can put the new one back in the same place
    cols = df.columns.tolist()
    insert_pos = cols.index("eevp") if "eevp" in cols else (
        cols.index("other_votes") + 1 if "other_votes" in cols else len(cols)
    )

    # Drop existing 'eevp'
    df = df.drop(columns=["eevp"], errors="ignore")

    # Safe numeric getter
    def num(col):
        return pd.to_numeric(df.get(col), errors="coerce").fillna(0)

    # Totals
    current_total = num("trump_votes") + num("harris_votes") + num("other_votes")
    final_total   = num("final_trump_votes") + num("final_harris_votes") + num("final_other_votes")

    # Compute new eevp as a percentage; 0 when final_total == 0; cap at 100
    new_eevp = pd.Series(0.0, index=df.index)
    mask = final_total != 0
    pct = (current_total.loc[mask] / final_total.loc[mask]) * 100.0
    new_eevp.loc[mask] = pct.clip(lower=0, upper=100).round(6)

    # Insert back at original position
    df.insert(insert_pos, "eevp", new_eevp)

    # Write output
    df.to_csv(OUT_FILE, index=False)
    print(f"Wrote: {OUT_FILE}")

if __name__ == "__main__":
    main()
