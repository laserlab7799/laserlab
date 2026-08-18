Laser Forensic Static Dataset
================================

Generated from:
forensic.jsonl

Files
-----

manifest.json
    Main entry point for a browser UI.

config.json
    Simulation configuration from the JSONL configuration record.

verification.json
    Final verification record from the JSONL.

angle_index.json
    Lightweight lookup table for every angle. It tells the UI:
    - whether the angle succeeded
    - basic summary counts
    - which batch file contains the full record
    - optional per-angle file path

batches/
    Full forensic angle records grouped into batches of up to 100.

angles/
    One full JSON file per angle.

Recommended static-UI flow
--------------------------

1. fetch("manifest.json")
2. fetch("angle_index.json")
3. When an angle is selected, read its "batch" value.
4. Fetch that batch JSON.
5. Use batch.angles["90.0"] (for example) to get the full forensic record.
6. Animate its ticks/slice_steps directly in the browser.

Counts
------

Angles parsed: 1799
Successful: 291
Unsuccessful: 1508
Batch files: 18
