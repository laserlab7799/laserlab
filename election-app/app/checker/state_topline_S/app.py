#!/usr/bin/env python3
import os, json, traceback
from pathlib import Path
from flask import Flask, jsonify, send_from_directory

PORT = int(os.getenv("PORT", "8003"))

app = Flask(__name__, static_folder=".", static_url_path="")

def resolve_cache_path() -> Path:
    env_val = os.getenv("P_CACHE_PATH")
    if env_val:
        return Path(env_val).expanduser().resolve()
    base = Path(__file__).resolve().parent
    candidates = [
        base / ".." / ".." / "temp" / "p_cache.json",
        base / ".." / "temp" / "p_cache.json",
        base / "temp" / "p_cache.json",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return candidates[0].resolve()

def nocache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

def error_json(status: int, message: str):
    resp = jsonify({"error": message})
    resp.status_code = status
    return nocache(resp)

# 50 states only
FIFTY_STATES = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
    "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
    "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"
]

@app.route("/")
def index():
    return nocache(send_from_directory(app.static_folder, "index.html"))

@app.route("/api/senate2026")
def senate2026():
    try:
        cache_path = resolve_cache_path()
        if not cache_path.exists():
            return error_json(404, f"Cache not found at {str(cache_path)}")

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception as e:
            traceback.print_exc()
            return error_json(500, f"Failed to parse p_cache.json: {e}")
        # 1
        
        # replace these lines:
        # race = cache.get("S:G") or cache.get("S:GEN") or {}
        # states_obj = (race or {}).get("states", {})

        # with this:
        container = (cache.get("cache_by_combo") or {})
        race = (
            container.get("S:G")
            or container.get("S:GEN")
            or cache.get("S:G")
            or cache.get("S:GEN")
            or {}
        )
        states_obj = (race.get("states") or {})


        out = []
        for usps in sorted(FIFTY_STATES):
            entry = states_obj.get(usps, {}) if isinstance(states_obj, dict) else {}
            rc = entry.get("race_call", {}) if isinstance(entry, dict) else {}
            status = rc.get("status")
            winner = rc.get("winner") if isinstance(rc, dict) else None
            name = winner.get("name") if isinstance(winner, dict) else None
            party = winner.get("party") if isinstance(winner, dict) else None
            out.append({
                "state": usps,
                "status": status or "No Data",
                "winner_name": name,
                "winner_party": party
            })

        resp = jsonify({
            "source_path": str(cache_path),
            "race_key": "S:G",
            "states": out
        })
        return nocache(resp)
    except Exception:
        traceback.print_exc()
        return error_json(500, "Unhandled server error")

@app.route("/favicon.ico")
def favicon():
    # Silence favicon warning; not essential
    return ("", 204)

if __name__ == "__main__":
    print(f"[info] Resolving cache to: {resolve_cache_path()}")
    app.run(host="0.0.0.0", port=PORT, debug=True)
