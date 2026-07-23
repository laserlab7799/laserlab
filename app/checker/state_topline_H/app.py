# app.py — Minimal checker API for House 2026 (reads ../temp/p_cache.json)
# Serves index.html and exposes /api/house2026 to flatten H:G (or H:GEN) rows.

import os, json
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request
from pathlib import Path

app = Flask(__name__, static_folder='.', static_url_path='')

# Snapshot path (same layout as your hub)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(ROOT_DIR, "../../.."))
TEMP_DIR = os.path.join(PARENT_DIR, "temp")
CACHE_SNAPSHOT_PATH = os.path.join(TEMP_DIR, "p_cache.json")


def _read_cache():
    """Read the hub snapshot (../temp/p_cache.json)."""
    if not os.path.exists(CACHE_SNAPSHOT_PATH):
        return {}
    with open(CACHE_SNAPSHOT_PATH, "r") as f:
        try:
            return json.load(f) or {}
        except Exception:
            return {}

def _pick_house_bucket(cache, race_type="G"):
    """
    Return the 'states' blob for House.
    Prefer 'H:G', else 'H:GEN' if present.
    """
    by_combo = (cache or {}).get("cache_by_combo") or {}
    bucket = (by_combo.get(f"H:{race_type}") or
              by_combo.get("H:GEN") or
              {})
    return bucket.get("states") or {}

@app.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp

@app.route("/")
def root():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/api/house2026")
def api_house2026():
    """
    Flatten House 2026 districts from ../temp/p_cache.json
    to a simple table the checker UI can read.
    """
    race_type = (request.args.get("raceTypeId") or "G").upper()
    cache = _read_cache()
    states = _pick_house_bucket(cache, race_type=race_type)

    rows = []
    for usps, state_blob in (states or {}).items():
        districts = (state_blob or {}).get("districts") or {}
        state_percent_in = (state_blob or {}).get("percent_in")
        updated = (state_blob or {}).get("updated")
        for did, d in districts.items():
            rc = (d.get("race_call") or {})
            win = (rc.get("winner") or {})
            rows.append({
                "state": usps,
                "district_id": d.get("district_id") or did,
                "district_num": d.get("district_num"),
                "name": d.get("name"),
                "percent_in": d.get("percent_in"),
                "state_percent_in": state_percent_in,
                "updated": updated,
                "status": rc.get("status") or "No Decision",
                "winner_name": win.get("name"),
                "winner_party": win.get("party")
            })

    # nice-to-have bits for the UI footer
    race_key = f"H:{race_type}"
    return jsonify({
        "race_key": race_key,
        "source_path": os.path.relpath(CACHE_SNAPSHOT_PATH, ROOT_DIR),
        "states_count": len(states),
        "districts_count": len(rows),
        "rows": rows
    })

@app.route("/health")
def health():
    exists = os.path.exists(CACHE_SNAPSHOT_PATH)
    size = os.path.getsize(CACHE_SNAPSHOT_PATH) if exists else 0
    mtime = datetime.utcfromtimestamp(os.path.getmtime(CACHE_SNAPSHOT_PATH)).isoformat()+"Z" if exists else None
    return jsonify({
        "snapshot_exists": exists,
        "snapshot_size": size,
        "snapshot_mtime_utc": mtime,
        "path": CACHE_SNAPSHOT_PATH
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","9053")))
