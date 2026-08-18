#!/usr/bin/env python3
import csv
import os
from math import fabs

INPUT_CSV = "2024_county_results_with_calcs.csv"
OUTPUT_CSV = "2024_county_results_with_calcs_new.csv"


def to_float(value):
    """Safe float conversion: blank/None -> 0.0."""
    try:
        if value is None:
            return 0.0
        s = str(value).strip()
        if s == "":
            return 0.0
        return float(s)
    except ValueError:
        return 0.0


def classify_side(margin):
    """
    Given a signed margin (Harris% - Trump%), return
    'Dem', 'Rep', or 'Tie'.
    """
    if margin > 0:
        return "Dem"
    if margin < 0:
        return "Rep"
    return "Tie"


def compute_shift_positive_in_early_leaders_direction(margin_snapshot, margin_final, leader_change):
    """
    Implement the Excel-style intent:

    if leader_change == 1:
        shift = -1 * abs(abs_final + abs_snap)
    else:
        if abs_final > abs_snap:
            shift = abs(abs_final - abs_snap)
        else:
            shift = -1 * abs(abs_final - abs_snap)
    """
    abs_snap = fabs(margin_snapshot)
    abs_final = fabs(margin_final)

    if leader_change == 1:
        # Leader flipped: always counted as movement against the early leader
        return -1.0 * fabs(abs_final + abs_snap)

    # Leader stayed the same
    if abs_final > abs_snap:
        # Margin grew in early leader's favor
        return fabs(abs_final - abs_snap)
    else:
        # Margin shrank (toward 0 or toward the other side)
        return -1.0 * fabs(abs_final - abs_snap)


def main():
    here = os.getenv("WORKDIR", os.getcwd())
    in_path = os.path.join(here, INPUT_CSV)
    out_path = os.path.join(here, OUTPUT_CSV)

    if not os.path.exists(in_path):
        raise SystemExit(f"Input CSV not found: {in_path}")

    with open(in_path, newline="", encoding="utf-8") as f_in, \
         open(out_path, "w", newline="", encoding="utf-8") as f_out:

        reader = csv.DictReader(f_in)
        original_fields = reader.fieldnames or []

        # New columns to append
        new_fields = [
            "leader",
            "winner",
            "leader_change",
            "margin_snapshot_abs",
            "shift_positive_in_early_leaders_direction",
        ]

        fieldnames = original_fields + new_fields
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()

        count = 0
        for row in reader:
            count += 1

            # Get margins from existing columns (they're in pct points)
            margin_snapshot = to_float(row.get("margin_snapshot"))
            margin_final = to_float(row.get("margin_final"))

            # Leader and winner
            leader = classify_side(margin_snapshot)
            winner = classify_side(margin_final)

            # Leader change flag
            leader_change = -1 if (leader == winner) else 1

            # Absolute snapshot margin
            margin_snapshot_abs = fabs(margin_snapshot)

            # Shift in early leader's direction
            shift_dir = compute_shift_positive_in_early_leaders_direction(
                margin_snapshot, margin_final, leader_change
            )

            # Update row with new columns
            row.update({
                "leader": leader,
                "winner": winner,
                "leader_change": leader_change,
                "margin_snapshot_abs": margin_snapshot_abs,
                "shift_positive_in_early_leaders_direction": shift_dir,
            })

            writer.writerow(row)

    print(f"Wrote {count} rows with new columns to {out_path}")


if __name__ == "__main__":
    main()
