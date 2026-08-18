#!/usr/bin/env python3
import xml.etree.ElementTree as ET

XML_PATH = "results.xml"


def find_trump_harris_ids(root):
    """
    Try to find the candidate IDs for Trump and Harris using the
    national ReportingUnit (StatePostal='US', Level='national').
    Fallback: scan all candidates by last name.
    """
    trump_id = None
    harris_id = None

    # First, try the national total
    for ru in root.findall(".//ReportingUnit"):
        if ru.get("StatePostal") == "US" and ru.get("Level") == "national":
            for cand in ru.findall("Candidate"):
                last = cand.get("Last", "")
                if last == "Trump":
                    trump_id = cand.get("ID")
                elif last == "Harris":
                    harris_id = cand.get("ID")

    # Fallback: scan all candidates if one or both IDs not found
    if trump_id is None or harris_id is None:
        for cand in root.findall(".//Candidate"):
            last = cand.get("Last", "")
            if trump_id is None and last == "Trump":
                trump_id = cand.get("ID")
            elif harris_id is None and last == "Harris":
                harris_id = cand.get("ID")

    return trump_id, harris_id


def main():
    tree = ET.parse(XML_PATH)
    root = tree.getroot()

    trump_id, harris_id = find_trump_harris_ids(root)

    if trump_id is None or harris_id is None:
        raise RuntimeError(
            f"Could not find Trump/Harris candidate IDs in {XML_PATH} "
            f"(trump_id={trump_id}, harris_id={harris_id})"
        )

    # Per-state accumulators
    state_totals = {}  # { "AK": {"trump": int, "harris": int, "other": int}, ... }

    # Loop over statewide presidential general races
    for race in root.findall("Race"):
        if race.get("OfficeID") != "P":
            continue
        if race.get("TypeID") != "G":
            continue
        # Only use statewide races (avoid CD splits like ME-01, ME-02)
        if race.get("State") != "1":
            continue

        for ru in race.findall("ReportingUnit"):
            # Only state-level units, not national, counties, etc.
            if ru.get("Level") != "state":
                continue
            state_postal = ru.get("StatePostal")
            # Skip the national line and any weird missing state code
            if not state_postal or state_postal == "US":
                continue

            # Ensure this state exists in our dict
            st_bucket = state_totals.setdefault(
                state_postal,
                {"trump": 0, "harris": 0, "other": 0},
            )

            for cand in ru.findall("Candidate"):
                votes = int(cand.get("VoteCount", "0") or "0")
                cid = cand.get("ID")

                if cid == trump_id:
                    st_bucket["trump"] += votes
                elif cid == harris_id:
                    st_bucket["harris"] += votes
                else:
                    st_bucket["other"] += votes

    # Now compute national sums from the per-state dict
    trump_total = 0
    harris_total = 0
    other_total = 0

    print("Per-state totals (sum of state-level presidential general results):")
    for state_postal in sorted(state_totals.keys()):
        st = state_totals[state_postal]
        st_trump = st["trump"]
        st_harris = st["harris"]
        st_other = st["other"]
        st_total = st_trump + st_harris + st_other

        trump_total += st_trump
        harris_total += st_harris
        other_total += st_other

        print(
            f"{state_postal}: "
            f"Trump={st_trump:,}  "
            f"Harris={st_harris:,}  "
            f"Other={st_other:,}  "
            f"Total={st_total:,}"
        )

    grand_total = trump_total + harris_total + other_total

    print()
    print("National sums (from summed state totals):")
    print(f"Trump (sum of state totals): {trump_total:,}")
    print(f"Harris (sum of state totals): {harris_total:,}")
    print(f"Other  (sum of state totals): {other_total:,}")
    print(f"Grand total (Trump + Harris + Other): {grand_total:,}")


if __name__ == "__main__":
    main()
