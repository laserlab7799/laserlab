from flask import Flask, jsonify, send_from_directory, request
import os
import xml.etree.ElementTree as ET
from collections import defaultdict
import logging
from xml.etree.ElementTree import ParseError

# CREATE THE APP FIRST
app = Flask(__name__, static_url_path="", static_folder=".")

# NOW it’s safe to touch app.logger
app.logger.setLevel(logging.INFO)


# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------
# HistoricData is two folders up from this file.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "..", "HistoricData"))

# Years we support for historic P/G
HIST_YEARS = {"2016", "2018", "2020", "2022", "2024"}
VALID_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DC","DE","FL","GA","HI","IA","ID","IL","IN","KS",
    "KY","LA","MA","MD","ME","MI","MN","MO","MS","MT","NC","ND","NE","NH","NJ","NM","NV",
    "NY","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VA","VT","WA","WI","WV","WY"
}

# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------

def safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def parse_presidential_state_file(xml_path):
    """
    Parse a single state's presidential XML into normalized county rows.

    Returns a list of dicts like:
      {
        "office": "P",
        "raceType": "G",
        "state": "AK",
        "name": "Aleutians East County",
        "fips": "02013",
        "candidates": [{"name":"Donald Trump","party":"REP","votes":12345}, ...],
        "total": 12345
      }
    """
    rows = []

    # 1) Existence check
    if not os.path.exists(xml_path):
        app.logger.warning(f"[historic] XML not found: {xml_path}")
        return rows

    # 2) Parse with hardening
    try:
        tree = ET.parse(xml_path)
    except ParseError as e:
        app.logger.error(f"[historic] XML parse error for {xml_path}: {e}")
        return rows
    except Exception as e:
        app.logger.exception(f"[historic] Unexpected error reading {xml_path}: {e}")
        return rows

    root = tree.getroot()

    # Common root attributes in many feeds
    state = root.attrib.get("StatePostal", "").upper() or root.attrib.get("statePostal", "").upper()
    office = root.attrib.get("Office", "P") or root.attrib.get("office", "P")
    race_type = root.attrib.get("RaceTypeID", "G") or root.attrib.get("raceTypeId", "G")

    # Helper: extract text attribute case-insensitively
    def a(d, *keys, default=""):
        for k in keys:
            if k in d:
                return d[k]
            # try case variants
            for variant in {k, k.lower(), k.upper(), k.capitalize()}:
                if variant in d:
                    return d[variant]
        return default

    # 3) Find county-like nodes (different feeds use different tag names)
    # Try a few common possibilities in order:
    possible_county_tags = [
        "County", "county",
        "ReportingUnit", "reportingUnit", "Reportingunit",
        "Jurisdiction", "jurisdiction",
        "GeographicUnit", "geographicUnit"
    ]

    county_nodes = []
    for tag in possible_county_tags:
        county_nodes = root.findall(f".//{tag}")
        if county_nodes:
            break

    if not county_nodes:
        # Fallback: consider any element that seems county-ish (has Name+FIPS)
        county_nodes = [
            el for el in root.iter()
            if ("Name" in el.attrib or "name" in el.attrib) and ("FIPS" in el.attrib or "fips" in el.attrib)
        ]

    # 4) For each county node, gather candidates
    # Candidate tag names also vary across feeds:
    possible_cand_tags = ["Candidate", "candidate", "Choice", "choice", "Selection", "selection"]

    for cnode in county_nodes:
        name = a(cnode.attrib, "Name", "CountyName", "JurisdictionName", "ReportingUnitName", "name", default="(Unknown County)")
        fips = a(cnode.attrib, "FIPS", "Fips", "fips", "CountyFIPS", default="")

        # Collect candidate votes
        candidates = []
        total = 0

        # Find candidate child elements using several possible tag names
        cand_children = []
        for tag in possible_cand_tags:
            cand_children.extend(cnode.findall(f".//{tag}"))

        # If we didn’t find any below the county, try on the root (some files keep cands at race-level with ruRef)
        if not cand_children:
            for tag in possible_cand_tags:
                cand_children.extend(root.findall(f".//{tag}"))

        for cand in cand_children:
            # some feeds scope candidates to RU via attributes; if present, filter to this county
            ru_ref = a(cand.attrib, "ReportingUnit", "reportingUnit", "ru", "ruRef", default="")
            if ru_ref and fips and (ru_ref != fips):
                continue  # candidate row belongs to a different RU/FIPS

            first = a(cand.attrib, "First", "first", "FirstName", "firstName", default="")
            last  = a(cand.attrib, "Last", "last", "LastName", "lastName", default="")
            party = a(cand.attrib, "Party", "party", "Affiliation", "affiliation", default="")
            votes = safe_int(a(cand.attrib, "VoteCount", "voteCount", "Votes", "votes", default="0"))

            total += votes
            candidates.append({
                "name": (f"{first} {last}".strip() or a(cand.attrib, "Name", "name", default="(Unnamed)")),
                "party": party,
                "votes": votes
            })

        rows.append({
            "office": office or "P",
            "raceType": race_type or "G",
            "state": state,
            "name": name,
            "fips": fips,
            "candidates": candidates,
            "total": total
        })

    return rows


def aggregate_statewide(rows):
    """
    Given county rows from parse_presidential_state_file, compute statewide totals.
    """
    cand_totals = defaultdict(int)
    party_map = {}
    grand_total = 0
    for r in rows:
        for c in r["candidates"]:
            key = (c["name"], c["party"])
            cand_totals[key] += c["votes"]
            party_map[c["name"]] = c["party"]
        grand_total += r["total"]

    candidates = [
        {"name": name, "party": party, "votes": votes}
        for (name, party), votes in cand_totals.items()
    ]
    candidates.sort(key=lambda x: x["votes"], reverse=True)

    return {
        "office": "P",
        "raceType": "G",
        "state": rows[0]["state"] if rows else "",
        "name": "Statewide Total",
        "fips": "STATE",
        "candidates": candidates,
        "total": grand_total
    }


def historic_file_path(year: str, office: str, state: str):
    """
    Example we read:
    ../../HistoricData/2024/P/AK.xml
    """
    year = str(year)
    office = str(office).upper()
    state = str(state).upper()
    return os.path.join(HIST_DIR, year, office, f"{state}.xml")


# ------------------------------------------------------------
# Historic endpoints (XML → JSON)
# ------------------------------------------------------------
    
@app.get("/historic/list")
def historic_list():
    """
    Returns available states (by USPS code) for a given year+office.
    GET /historic/list?year=2024&office=P
    """
    year = request.args.get("year", "")
    office = request.args.get("office", "P").upper()

    if year not in HIST_YEARS or office != "P":
        return jsonify({"year": year, "office": office, "states": []})

    folder = os.path.join(HIST_DIR, year, office)
    states = []
    if os.path.isdir(folder):
        for fname in os.listdir(folder):
            if fname.lower().endswith(".xml"):
                code = os.path.splitext(fname)[0].upper()
                if code in VALID_STATES:
                    states.append(code)

    states.sort()
    return jsonify({"year": year, "office": office, "states": states})


@app.get("/historic/state")
def historic_state():
    """
    Returns county rows + statewide for a single state-year (P/G):
    GET /historic/state?year=2024&office=P&state=AK
    """
    year = request.args.get("year", "")
    office = request.args.get("office", "P").upper()
    state = request.args.get("state", "").upper()

    if year not in HIST_YEARS or office != "P" or state not in VALID_STATES:
        return jsonify({"year": year, "office": office, "state": state, "rows": []})

    xml_path = historic_file_path(year, office, state)
    rows = parse_presidential_state_file(xml_path)
    if rows:
        rows_sorted = sorted(rows, key=lambda r: r["name"])
        rows_sorted.insert(0, aggregate_statewide(rows_sorted))  # "Statewide Total" first
        return jsonify({"year": year, "office": office, "state": state, "rows": rows_sorted})

    return jsonify({"year": year, "office": office, "state": state, "rows": []})


# ------------------------------------------------------------
# 2026 path (keep whatever upstream cache/API you already use)
# Minimal stub below still returns shape the front-end expects:
#   GET /cache/ru?office=P&raceTypeId=G
# Replace with your real cache if you already have it.
# ------------------------------------------------------------
@app.get("/cache/ru")
def cache_ru():
    """
    Live 2026 path (replace with your real implementation).
    """
    office = request.args.get("office", "P")
    race_type = request.args.get("raceTypeId", "G")

    # TODO: Replace with real cache/API call
    # For now, return an empty set of rows so the UI still works.
    return jsonify({
        "year": "2026",
        "office": office,
        "raceTypeId": race_type,
        "rows": []
    })


# ------------------------------------------------------------
# Static (serve index.html, assets, etc.)
# ------------------------------------------------------------

@app.get("/")
def root():
    return send_from_directory(".", "index.html")


if __name__ == "__main__":
    # Flask dev server
    app.run(host="0.0.0.0", port=5000, debug=True)

