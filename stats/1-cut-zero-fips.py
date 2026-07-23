#!/usr/bin/env python3
"""
Hardcoded filter: read /mnt/data/input.csv, write /mnt/data/output.csv,
dropping rows where the FIPS column equals zero ("0", "000", "00000", etc.).
"""

import sys
import pandas as pd

INPUT_PATH = "2024_county_results_with_final.csv"
OUTPUT_PATH = "2024_county_results_with_final_1.csv"
FIPS_COL = None  # set to a specific column name to override; otherwise auto-detect 'fips' (case-insensitive)

def normalize_zero_like(s: str):
    if s is None:
        return s
    s2 = str(s).strip()
    if s2 == "":
        return s2
    if s2.isdigit():
        try:
            return str(int(s2))
        except ValueError:
            return s2
    return s2

def main():
    try:
        df = pd.read_csv(INPUT_PATH, dtype=str)
    except FileNotFoundError:
        print(f"ERROR: Input file not found: {INPUT_PATH}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Could not read CSV: {e}", file=sys.stderr)
        sys.exit(1)

    # Determine FIPS column
    fips_col = FIPS_COL
    if not fips_col:
        matches = [c for c in df.columns if c.lower() == "fips"]
        if not matches:
            print("ERROR: No 'fips' column found (case-insensitive). Set FIPS_COL to override.", file=sys.stderr)
            sys.exit(1)
        fips_col = matches[0]

    fips_norm = df[fips_col].fillna("").map(normalize_zero_like)
    mask_keep = fips_norm != "0"

    before = len(df)
    df_clean = df[mask_keep].copy()
    after = len(df_clean)
    dropped = before - after

    try:
        df_clean.to_csv(OUTPUT_PATH, index=False)
    except Exception as e:
        print(f"ERROR: Could not write output CSV: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Wrote: {OUTPUT_PATH}")
    print(f"Rows before: {before}")
    print(f"Rows after:  {after}")
    print(f"Dropped (fips == 0): {dropped}")

if __name__ == "__main__":
    main()
