#!/usr/bin/env python3
import csv
import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--input",  default="stats2.csv")
ap.add_argument("--output", default="stats2_final_headers.csv")
args = ap.parse_args()

INFILE  = args.input
OUTFILE = args.output


# Input columns (expected)
# timestamp,state,county,fips,trump_votes,harris_votes,other_votes,eevp,state_eevp

# Output columns (exact)
OUT_FIELDS = [
    "final_state",
    "final_county",
    "final_fips",
    "final_trump_votes",
    "final_harris_votes",
    "final_other_votes",
]

with open(INFILE, "r", newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

with open(OUTFILE, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
    w.writeheader()

    for r in rows:
        out = {
            "final_state":        r.get("state", ""),
            "final_county":       r.get("county", ""),
            "final_fips":         r.get("fips", ""),
            "final_trump_votes":  r.get("trump_votes", ""),
            "final_harris_votes": r.get("harris_votes", ""),
            "final_other_votes":  r.get("other_votes", ""),
        }
        w.writerow(out)

print(f"Wrote {OUTFILE} (rows={len(rows)})")
