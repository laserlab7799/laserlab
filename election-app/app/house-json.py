import json

def is_winner(cand):
    """
    Return True if this candidate is marked as a winner.

    Handles:
    - winner: true/false
    - winner: "X", "x", "W", "WINNER", etc.
    """
    w = cand.get("winner")
    if isinstance(w, bool):
        return w
    if isinstance(w, str):
        return w.strip().upper() in ("X", "W", "WINNER")
    return False

def normalize_party(party_raw):
    """
    Normalize party labels to 'DEM', 'GOP', or None.
    """
    p = (party_raw or "").strip().upper()
    if p in ("DEM", "DEMOCRAT", "DEMOCRATIC"):
        return "DEM"
    if p in ("GOP", "REP", "REPUBLICAN"):
        return "GOP"
    return None

def count_house_winners_from_json(path="house.json"):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    dem_winners = 0
    gop_winners = 0

    # Track (race_id, candidate_id) so we don't double-count
    seen = set()

    for race in data.get("races", []):
        race_id = race.get("raceID") or race.get("id") or race.get("ID")

        # Candidates are usually under reportingUnits[].candidates[]
        for ru in race.get("reportingUnits", []):
            for cand in ru.get("candidates", []):
                if not is_winner(cand):
                    continue

                cand_id = (
                    cand.get("candidateID")
                    or cand.get("polID")
                    or cand.get("id")
                )

                key = (race_id, cand_id)
                if key in seen:
                    continue
                seen.add(key)

                party = normalize_party(cand.get("party"))

                if party == "DEM":
                    dem_winners += 1
                elif party == "GOP":
                    gop_winners += 1

    return dem_winners, gop_winners

if __name__ == "__main__":
    dem, gop = count_house_winners_from_json("house.json")
    print("Dem winners:", dem)
    print("GOP winners:", gop)
