#!/usr/bin/env python3
import csv
import os
from math import fabs

INPUT_CSV = "2024_county_results_with_final_1.csv"
OUTPUT_CSV = "2024_county_results_with_calcs-old.csv"


def to_int(value):
    """Safe int conversion: blank/None -> 0."""
    try:
        if value is None:
            return 0
        value = str(value).strip()
        if value == "":
            return 0
        return int(value)
    except ValueError:
        return 0


def compute_percents(trump_votes, harris_votes, other_votes):
    """
    Given raw vote counts, return (harris_pct, trump_pct, margin_pct)
    where values are in percentage points (0–100).
    If total is 0, all are 0.0.
    """
    total = trump_votes + harris_votes + other_votes
    if total <= 0:
        return 0.0, 0.0, 0.0

    harris_pct = (harris_votes / total) * 100.0
    trump_pct = (trump_votes / total) * 100.0
    margin_pct = harris_pct - trump_pct
    return harris_pct, trump_pct, margin_pct


def main():
    here = os.getcwd()
    in_path = os.path.join(here, INPUT_CSV)
    out_path = os.path.join(here, OUTPUT_CSV)

    if not os.path.exists(in_path):
        raise SystemExit(f"Input CSV not found: {in_path}")

    with open(in_path, newline="", encoding="utf-8") as f_in, \
         open(out_path, "w", newline="", encoding="utf-8") as f_out:

        reader = csv.DictReader(f_in)
        original_fields = reader.fieldnames or []

        # New columns we’re appending
        calc_fields = [
            "harris_percent_snapshot",
            "trump_percent_snapshot",
            "margin_snapshot",
            "harris_percent_final",
            "trump_percent_final",
            "margin_final",
            "margin_difference",
            "margin_difference_abs",
        ]

        fieldnames = original_fields + calc_fields
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()

        row_count = 0
        for row in reader:
            row_count += 1

            # Snapshot values (original columns)
            snap_trump = to_int(row.get("trump_votes"))
            snap_harris = to_int(row.get("harris_votes"))
            snap_other = to_int(row.get("other_votes"))

            (
                harris_pct_snapshot,
                trump_pct_snapshot,
                margin_snapshot,
            ) = compute_percents(snap_trump, snap_harris, snap_other)

            # Final values (from final_* columns)
            final_trump = to_int(row.get("final_trump_votes"))
            final_harris = to_int(row.get("final_harris_votes"))
            final_other = to_int(row.get("final_other_votes"))

            (
                harris_pct_final,
                trump_pct_final,
                margin_final,
            ) = compute_percents(final_trump, final_harris, final_other)

            # Differences (signed)
            margin_difference = margin_snapshot - margin_final
            margin_difference_abs = fabs(margin_difference)

            # Update row with new columns
            row.update({
                "harris_percent_snapshot": harris_pct_snapshot,
                "trump_percent_snapshot": trump_pct_snapshot,
                "margin_snapshot": margin_snapshot,
                "harris_percent_final": harris_pct_final,
                "trump_percent_final": trump_pct_final,
                "margin_final": margin_final,
                "margin_difference": margin_difference,
                "margin_difference_abs": margin_difference_abs,
            })

            writer.writerow(row)

    print(f"Wrote {row_count} rows with calculations to {out_path}")


if __name__ == "__main__":
    main()
