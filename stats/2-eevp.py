#!/usr/bin/env python3
import pandas as pd

IN_FILE  = "2024_county_results_with_calcs-old.csv"
OUT_FILE = "2024_county_results_with_calcs.csv"

def main():
    df = pd.read_csv(IN_FILE)

    # No-op: write the exact same data back out (same columns + values, unchanged)
    df.to_csv(OUT_FILE, index=False)

    # "Names listed" = column names
    print(f"Wrote (no-op copy): {OUT_FILE}")
    print("Columns:")
    for c in df.columns.tolist():
        print(f" - {c}")

if __name__ == "__main__":
    main()
