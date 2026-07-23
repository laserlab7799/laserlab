#!/usr/bin/env python3
import csv
import argparse

IN1_DEFAULT = "stats_with_statewide.csv"       # first csv
IN2_DEFAULT = "stats2_final_headers.csv"       # second csv
OUT_DEFAULT = "2024_county_results_with_final-old.csv"

ap = argparse.ArgumentParser()
ap.add_argument("--input1", default=IN1_DEFAULT)
ap.add_argument("--input2", default=IN2_DEFAULT)
ap.add_argument("--output", default=OUT_DEFAULT)
args = ap.parse_args()

IN1 = args.input1
IN2 = args.input2
OUT = args.output


FIELDS1 = [
    "timestamp","state","county","fips",
    "trump_votes","harris_votes","other_votes",
    "eevp","state_eevp",
    "harris_votes_statewide","trump_votes_statewide","other_votes_statewide",
]

FIELDS2 = [
    "final_state","final_county","final_fips",
    "final_trump_votes","final_harris_votes","final_other_votes",
]

OUT_FIELDS = FIELDS1 + FIELDS2

def key_from_row(r):
    # join on state+county+fips (adjust if you need something else)
    return (r.get("state",""), r.get("county",""), r.get("fips",""))

def norm_fips(x):
    x = (x or "").strip()
    if x.isdigit():
        return x.zfill(5)
    return x

def fips_row_mismatch(in1_path, in2_path):
    # mismatch if different row counts OR any row has different fips at same index
    f1 = []
    with open(in1_path, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            f1.append(norm_fips(row.get("fips", "")))

    f2 = []
    with open(in2_path, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            f2.append(norm_fips(row.get("final_fips", "")))

    if len(f1) != len(f2):
        return True

    for a, b in zip(f1, f2):
        if a != b:
            return True

    return False

MISMATCH = fips_row_mismatch(IN1, IN2)
if MISMATCH:
    print("[warn] stats/stats2 row+fips mismatch detected; will fill missing final_* with 10")


# Read second into dict keyed by (state,county,fips) using its "final_*" fields
with open(IN2, "r", newline="", encoding="utf-8") as f:
    r2 = csv.DictReader(f)
    map2 = {}
    for r in r2:
        k = (r.get("final_state",""), r.get("final_county",""), r.get("final_fips",""))
        map2[k] = {c: r.get(c, "") for c in FIELDS2}

# Read first, append matching final_* columns, write out
with open(IN1, "r", newline="", encoding="utf-8") as f1, open(OUT, "w", newline="", encoding="utf-8") as fo:
    r1 = csv.DictReader(f1)
    w  = csv.DictWriter(fo, fieldnames=OUT_FIELDS)
    w.writeheader()

    missing = 0
    rows = 0

    for r in r1:
        rows += 1
        out = {c: r.get(c, "") for c in FIELDS1}

        k = key_from_row(r)
        final_cols = map2.get(k)
        if final_cols is None:
            missing += 1
            fill = "10" if MISMATCH else ""
            final_cols = {c: fill for c in FIELDS2}


        out.update(final_cols)
        w.writerow(out)

print(f"Wrote {OUT} (rows={rows}, missing_final_matches={missing})")
