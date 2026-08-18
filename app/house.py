import xml.etree.ElementTree as ET

# Load the file
tree = ET.parse("house.xml")
root = tree.getroot()

dem_winners = 0
gop_winners = 0

# Track (race_id, candidate_id) so we don't double-count
seen_winners = set()

for race in root.findall("Race"):
    race_id = race.get("ID")

    # Look at all Candidate elements under this race
    for cand in race.findall(".//Candidate"):
        winner_flag = cand.get("Winner")
        if not winner_flag:  # skip non-winners / not-called
            continue

        # Try a few IDs, fall back to None
        cand_id = cand.get("CandidateID") or cand.get("ID") or cand.get("PolID")
        key = (race_id, cand_id)

        # Avoid counting the same winner multiple times in the same race
        if key in seen_winners:
            continue
        seen_winners.add(key)

        # Normalize party
        party = (cand.get("Party") or "").upper()

        if party in ("DEM", "DEMOCRAT", "DEMOCRATIC"):
            dem_winners += 1
        elif party in ("GOP", "REP", "REPUBLICAN"):
            gop_winners += 1

print("Dem winners:", dem_winners)
print("GOP winners:", gop_winners)
