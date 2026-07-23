#!/usr/bin/env python3
import csv
from pathlib import Path

# Hardcoded paths
IN_CSV  = Path("2024_county_results_with_final-old.csv")    # change if needed
OUT_CSV = Path("2024_county_results_with_final.csv")   # change if needed

# Old → New names
rename = {
    "harris_votes": "harris_votes",
    "reporting_pct": "eevp",
    "state_reporting_pct": "state_eevp",
    "harris_votes_statewide": "harris_votes_statewide",
    "final_harris_votes": "final_harris_votes",
}

# Final header order
target_fields = [
    "timestamp","state","county","fips",
    "trump_votes","harris_votes","other_votes",
    "eevp","state_eevp",
    "harris_votes_statewide","trump_votes_statewide","other_votes_statewide",
    "final_state","final_county","final_fips",
    "final_trump_votes","final_harris_votes","final_other_votes",
]

with IN_CSV.open(newline="", encoding="utf-8") as f_in:
    reader = csv.DictReader(f_in)
    rows = []
    for row in reader:
        new_row = {}
        for old_key, value in row.items():
            key = rename.get(old_key, old_key)  # apply rename if present
            new_row[key] = value
        for k in target_fields:                 # ensure all fields exist
            new_row.setdefault(k, "")
        rows.append(new_row)

with OUT_CSV.open("w", newline="", encoding="utf-8") as f_out:
    writer = csv.DictWriter(f_out, fieldnames=target_fields)
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote: {OUT_CSV}")
