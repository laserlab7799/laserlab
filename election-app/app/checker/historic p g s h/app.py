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

# Years we support for historic P/G/S/H
HIST_YEARS = {"2016", "2018", "2020", "2022", "2024"}

# Offices supported
VALID_OFFICES = {"P", "G", "S", "H"}  # President, Governor, Senate, House

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

def _two(x: str) -> str:
    try:
        return f"{int(x):02d}"
    except Exception:
        return (x or "").zfill(2)[:2]

def parse_state_file(xml_path, fallback_office="P", fallback_race_type="G"):
    """
    Generic state parser for P/G/S/H XML into normalized rows.

    Normalized row example:
      {
        "office": "P"|"G"|"S"|"H",
        "raceType": "G" (or as present),
        "state": "AK",
        "name": "At-Large Congressional District" or county name,
        "fips": "02013" (county) or "AK-00" (house district) or "STATE",
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

    # County / district units
    possible_unit_tags = [
        "ReportingUnit", "reportingUnit", "Reportingunit",
        "County", "county",
        "Jurisdiction", "jurisdiction",
        "GeographicUnit", "geographicUnit"
    ]
    unit_nodes = []
    for tag in possible_unit_tags:
        unit_nodes = root.findall(f".//{tag}")
        if unit_nodes:
            break
    if not unit_nodes:
        unit_nodes = [
            el for el in root.iter()
            if ("Name" in el.attrib or "name" in el.attrib) and (
                "FIPS" in el.attrib or "fips" in el.attrib or "District" in el.attrib or "district" in el.attrib
            )
        ]

    possible_cand_tags = ["Candidate", "candidate", "Choice", "choice", "Selection", "selection"]

    for unode in unit_nodes:
        name = _aget(
            unode.attrib,
            "ReportingUnitName", "Name", "CountyName", "JurisdictionName", "name",
            default="(Unknown)"
        )
        fips = _aget(unode.attrib, "FIPS", "Fips", "fips", "CountyFIPS", default="")
        district = _aget(unode.attrib, "District", "district", default="")

        # For House, synthesize an ID if no FIPS is available.
        # Example: AK.xml (H, at-large) -> "AK-00"
        if office == "H":
            if district:
                fips = f"{state}-{_two(district)}"
            elif not fips:
                # Last resort: treat as at-large/unknown district
                fips = f"{state}-00"

        candidates = []
        total = 0

        cand_children = []
        for tag in possible_cand_tags:
            cand_children.extend(unode.findall(f".//{tag}"))
        if not cand_children:
            for tag in possible_cand_tags:
                cand_children.extend(root.findall(f".//{tag}"))

        for cand in cand_children:
            # Try to restrict candidate to this unit if a reference exists
            ru_ref = _aget(cand.attrib, "ReportingUnit", "reportingUnit", "ru", "ruRef", default="")
            if ru_ref and fips and (ru_ref != fips) and (ru_ref != district):
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
    Given rows from parse_state_file, compute statewide totals.
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
      ../../HistoricData/2024/H/AK.xml
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
    GET /historic/list?year=2024&office=H  (also supports P/G/S)
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
    Returns rows + statewide for a single state-year-office (P/G/S/H, raceType defaults to G):
      GET /historic/state?year=2024&office=H&state=AK
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

@app.get("/historic/national")
def historic_national():
    """
    Aggregate all states for a given year + office into a NATIONAL roll-up.
    GET /historic/national?year=2024&office=P
    Returns:
      {
        "year":"2024","office":"P",
        "row":{
          "office":"P","raceType":"G","state":"US",
          "name":"National Total","fips":"NATIONAL",
          "candidates":[...],"total":12345678
        }
      }
    """
    year = request.args.get("year", "")
    office = request.args.get("office", "P").upper()

    if year not in HIST_YEARS or office not in VALID_OFFICES:
        return jsonify({"year": year, "office": office, "row": None})

    cand_totals = defaultdict(int)
    race_type_seen = "G"
    grand_total = 0
    any_rows = False

    for state in sorted(VALID_STATES):
        xml_path = historic_file_path(year, office, state)
        if not os.path.exists(xml_path):
            continue
        rows = parse_state_file(xml_path, fallback_office=office, fallback_race_type="G")
        if not rows:
            continue
        any_rows = True
        # aggregate to statewide first, then roll into national
        statewide = aggregate_statewide(rows, office_hint=office, race_type_hint="G")
        race_type_seen = statewide.get("raceType", race_type_seen) or race_type_seen
        for c in statewide["candidates"]:
            cand_totals[(c["name"], c["party"])] += c["votes"]
        grand_total += statewide["total"]

    if not any_rows:
        return jsonify({"year": year, "office": office, "row": None})

    candidates = [
        {"name": name, "party": party, "votes": votes}
        for (name, party), votes in cand_totals.items()
    ]
    candidates.sort(key=lambda x: x["votes"], reverse=True)

    row = {
        "office": office,
        "raceType": race_type_seen,
        "state": "US",
        "name": "National Total",
        "fips": "NATIONAL",
        "candidates": candidates,
        "total": grand_total
    }
    return jsonify({"year": year, "office": office, "row": row})

# ------------------------------------------------------------
# 2026 path (stub)
# ------------------------------------------------------------
@app.get("/cache/ru")
def cache_ru():
    """
    Live 2026 path (replace with your real implementation).
    Accepts office=P|G|S|H and raceTypeId (default G).
    """
    office = request.args.get("office", "P").upper()
    race_type = request.args.get("raceTypeId", "G").upper()

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
