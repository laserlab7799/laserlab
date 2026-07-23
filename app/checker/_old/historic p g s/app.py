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
# HistoricData is three folders up from this file (unchanged).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HIST_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "..", "HistoricData"))

# Years we support for historic P/G/S
HIST_YEARS = {"2016", "2018", "2020", "2022", "2024"}

# Offices supported
VALID_OFFICES = {"P", "G", "S"}  # President, Governor, Senate

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

def _aget(d: dict, *keys, default=""):
    """
    Helper: attribute get (case-insensitive across common variants).
    """
    for k in keys:
        if k in d:
            return d[k]
        for variant in {k, k.lower(), k.upper(), k.capitalize()}:
            if variant in d:
                return d[variant]
    return default

def parse_state_file(xml_path, fallback_office="P", fallback_race_type="G"):
    """
    Generic state parser for P/G/S XML into normalized county rows.

    Returns a list of dicts like:
      {
        "office": "P"|"G"|"S",
        "raceType": "G" (or as present),
        "state": "AK",
        "name": "Aleutians East County",
        "fips": "02013",
        "candidates": [{"name":"Jane Doe","party":"DEM","votes":12345}, ...],
        "total": 12345
      }
    """
    rows = []

    if not os.path.exists(xml_path):
        app.logger.warning(f"[historic] XML not found: {xml_path}")
        return rows

    try:
        tree = ET.parse(xml_path)
    except ParseError as e:
        app.logger.error(f"[historic] XML parse error for {xml_path}: {e}")
        return rows
    except Exception as e:
        app.logger.exception(f"[historic] Unexpected error reading {xml_path}: {e}")
        return rows

    root = tree.getroot()

    # Office / state / raceType sniffing from common attributes
    state = _aget(root.attrib, "StatePostal", "statePostal", default="").upper()
    office = _aget(root.attrib, "Office", "office", default=fallback_office).upper()
    race_type = _aget(root.attrib, "RaceTypeID", "raceTypeId", default=fallback_race_type).upper()

    # Candidate + county-like nodes appear under various tag names depending on feed
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
        # Fallback: anything with Name + FIPS attributes
        county_nodes = [
            el for el in root.iter()
            if ("Name" in el.attrib or "name" in el.attrib) and ("FIPS" in el.attrib or "fips" in el.attrib)
        ]

    possible_cand_tags = ["Candidate", "candidate", "Choice", "choice", "Selection", "selection"]

    for cnode in county_nodes:
        name = _aget(cnode.attrib, "Name", "CountyName", "JurisdictionName", "ReportingUnitName", "name", default="(Unknown County)")
        fips = _aget(cnode.attrib, "FIPS", "Fips", "fips", "CountyFIPS", default="")

        candidates = []
        total = 0

        cand_children = []
        for tag in possible_cand_tags:
            cand_children.extend(cnode.findall(f".//{tag}"))
        if not cand_children:
            # Some feeds attach candidates at race level with ruRef/ReportingUnit pointers
            for tag in possible_cand_tags:
                cand_children.extend(root.findall(f".//{tag}"))

        for cand in cand_children:
            ru_ref = _aget(cand.attrib, "ReportingUnit", "reportingUnit", "ru", "ruRef", default="")
            if ru_ref and fips and (ru_ref != fips):
                continue

            first = _aget(cand.attrib, "First", "first", "FirstName", "firstName", default="")
            last  = _aget(cand.attrib, "Last", "last", "LastName", "lastName", default="")
            party = _aget(cand.attrib, "Party", "party", "Affiliation", "affiliation", default="")
            votes = safe_int(_aget(cand.attrib, "VoteCount", "voteCount", "Votes", "votes", default="0"))

            candidates.append({
                "name": (f"{first} {last}".strip() or _aget(cand.attrib, "Name", "name", default="(Unnamed)")),
                "party": party,
                "votes": votes
            })
            total += votes

        rows.append({
            "office": office or fallback_office,
            "raceType": race_type or fallback_race_type,
            "state": state,
            "name": name,
            "fips": fips,
            "candidates": candidates,
            "total": total
        })

    return rows

def aggregate_statewide(rows, office_hint="P", race_type_hint="G"):
    """
    Given county rows from parse_state_file, compute statewide totals.
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
        "office": rows[0]["office"] if rows else office_hint,
        "raceType": rows[0]["raceType"] if rows else race_type_hint,
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
      ../../HistoricData/2024/G/AK.xml
      ../../HistoricData/2024/S/AK.xml
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
    GET /historic/list?year=2024&office=G  (also supports P and S)
    """
    year = request.args.get("year", "")
    office = request.args.get("office", "P").upper()

    if year not in HIST_YEARS or office not in VALID_OFFICES:
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
    Returns county rows + statewide for a single state-year-office (P/G/S, raceType defaults to G):
      GET /historic/state?year=2024&office=S&state=AK
    """
    year = request.args.get("year", "")
    office = request.args.get("office", "P").upper()
    state = request.args.get("state", "").upper()

    if year not in HIST_YEARS or office not in VALID_OFFICES or state not in VALID_STATES:
        return jsonify({"year": year, "office": office, "state": state, "rows": []})

    xml_path = historic_file_path(year, office, state)
    rows = parse_state_file(xml_path, fallback_office=office, fallback_race_type="G")
    if rows:
        rows_sorted = sorted(rows, key=lambda r: r["name"])
        rows_sorted.insert(0, aggregate_statewide(rows_sorted, office_hint=office, race_type_hint="G"))
        return jsonify({"year": year, "office": office, "state": state, "rows": rows_sorted})

    return jsonify({"year": year, "office": office, "state": state, "rows": []})

# ------------------------------------------------------------
# 2026 path (keep whatever upstream cache/API you already use)
# Minimal stub below still returns shape the front-end expects.
# ------------------------------------------------------------
@app.get("/cache/ru")
def cache_ru():
    """
    Live 2026 path (replace with your real implementation).
    Accepts office=P|G|S and raceTypeId (default G).
    """
    office = request.args.get("office", "P").upper()
    race_type = request.args.get("raceTypeId", "G").upper()

    # TODO: Replace with your real cache/API call
    return jsonify({
        "year": "2026",
        "office": office if office in VALID_OFFICES else "P",
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
