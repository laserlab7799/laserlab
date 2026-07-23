#!/usr/bin/env python3
# no-op copy: reads INPUT_CSV and writes OUTPUT_CSV with identical content

import shutil

INPUT_CSV  = "data_output_with_margins_old.csv"
OUTPUT_CSV = "data_output_with_margins.csv"

shutil.copyfile(INPUT_CSV, OUTPUT_CSV)
print(f"Wrote {OUTPUT_CSV} (copied from {INPUT_CSV})")
