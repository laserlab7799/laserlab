#!/usr/bin/env python3
import csv
import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--input",  default="stats.csv")
ap.add_argument("--output", default="stats_with_statewide.csv")
args = ap.parse_args()

INFILE  = args.input
OUTFILE = args.output


def to_int(x):
    x = (x or "").strip()
    if x == "":
        return 0
    # handle values like "123.0" just in case
    return int(float(x))

with open(INFILE, "r", newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

# Sum votes per (timestamp, state) — if you truly have only one timestamp, this still works.
totals = {}  # (timestamp, state) -> (harris_sum, trump_sum, other_sum)
for r in rows:
    key = (r["timestamp"], r["state"])
    h = to_int(r.get("harris_votes"))
    t = to_int(r.get("trump_votes"))
    o = to_int(r.get("other_votes"))
    if key not in totals:
        totals[key] = [0, 0, 0]
    totals[key][0] += h
    totals[key][1] += t
    totals[key][2] += o

# Write out with the new statewide columns appended
base_fields = [
    "timestamp","state","county","fips",
    "trump_votes","harris_votes","other_votes",
    "eevp","state_eevp"
]
new_fields = base_fields + [
    "harris_votes_statewide","trump_votes_statewide","other_votes_statewide"
]

with open(OUTFILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=new_fields)
    writer.writeheader()

    for r in rows:
        key = (r["timestamp"], r["state"])
        h_sum, t_sum, o_sum = totals[key]

        out = {k: r.get(k, "") for k in base_fields}
        out["harris_votes_statewide"] = str(h_sum)
        out["trump_votes_statewide"]  = str(t_sum)
        out["other_votes_statewide"]  = str(o_sum)

        writer.writerow(out)

print(f"Wrote {OUTFILE} (rows={len(rows)})")
