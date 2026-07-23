# app.py — Hub polls upstream on its own; UI only reads cache (true hub & spoke)
# - Supports ALL races (P/S/G/H) and broad AP raceTypeId set (G,W,H,D,R,U,V,J,K,A,B,APP,SAP,N,NP,L,T,RET,...)
# - County-level for P/S/G via /v2/elections; District-level for H via /v2/districts
# - Background hub cycles (office, raceTypeId, state) and snapshots cache to ./temp/p_cache.json
# Legal notice: Developed by Kevin Hogan at NTD News. All rights reserved.

import os, time, json, threading, itertools, queue, random
import sys, subprocess
from collections import deque
from datetime import datetime
from flask import Flask, send_from_directory, jsonify, request
from werkzeug.utils import secure_filename
import requests
import xml.etree.ElementTree as ET
import psutil
import shutil

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from email.utils import parsedate_to_datetime

import json
import hashlib
from pathlib import Path

APP_STATE_DIR = Path("app_state")
APP_STATE_DIR.mkdir(exist_ok=True)

FINGERPRINTS_PATH = APP_STATE_DIR / "race_fingerprints.json"

# Hub sleep default (seconds)
SLEEP_S = float(os.getenv("SLEEP_S", "60.0"))

def load_fingerprints():
    if not FINGERPRINTS_PATH.exists():
        return {}
    try:
        with open(FINGERPRINTS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_fingerprints(data):
    tmp = FINGERPRINTS_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(FINGERPRINTS_PATH)


# load once at startup
LAST_FINGERPRINTS = load_fingerprints()

def race_fingerprint(summary_row: dict) -> str:
    """
    summary_row must contain ONLY values that matter for recompute.
    """
    raw = json.dumps(
        summary_row,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


app = Flask(__name__, static_folder='.', static_url_path='')

from pathlib import Path
from flask import jsonify

# ---- File Manager API (for backend.html) ----
# Add near your other routes in app_hub.py

import os, shutil, mimetypes
from pathlib import Path
from flask import request, jsonify, send_file, abort
from werkzeug.utils import secure_filename

# Set this to "/" if you truly want everything, but safer is your project root.
FILE_MGR_BASE = os.getenv("FILE_MGR_BASE", "/")  # e.g. "/home/ubuntu" recommended
FILE_MGR_BASE_PATH = Path(FILE_MGR_BASE).resolve()

def _fm_auth_ok():
    # Reuse your existing env vars
    user = os.getenv("OVERRIDE_USER", "")  # already in your file
    pw   = os.getenv("OVERRIDE_PASS", "")
    if not user or not pw:
        # If you leave these blank, the API is open.
        # Strongly recommend setting them.
        return True

    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Basic "):
        return False
    import base64
    try:
        raw = base64.b64decode(hdr.split(" ", 1)[1]).decode("utf-8", "replace")
    except Exception:
        return False
    if ":" not in raw:
        return False
    u, p = raw.split(":", 1)
    return (u == user and p == pw)

def _fm_require_auth():
    return None

def _fm_resolve(user_path: str) -> Path:
    """
    Resolve a client-provided absolute-ish path to a real path under FILE_MGR_BASE.
    If FILE_MGR_BASE='/', this effectively allows all paths.
    """
    p = (user_path or "/").strip()
    if not p.startswith("/"):
        p = "/" + p
    # Normalize and resolve
    target = (FILE_MGR_BASE_PATH / p.lstrip("/")).resolve()
    # Enforce base containment
    if str(FILE_MGR_BASE_PATH) != "/" and not str(target).startswith(str(FILE_MGR_BASE_PATH) + os.sep):
        raise ValueError("Path escapes base")
    return target

HOLDOVERS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "HistoricData", "senate_holdovers.json")
)

def _ensure_dir(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)

@app.get("/api/holdovers/senate/load")
def load_senate_holdovers():
    try:
        if os.path.exists(HOLDOVERS_PATH):
            with open(HOLDOVERS_PATH, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        return jsonify({"by_year": {}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/holdovers/senate/save")
def save_senate_holdovers():
    try:
        payload = request.get_json(force=True, silent=False)
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected JSON object"}), 400
        if "by_year" not in payload or not isinstance(payload["by_year"], dict):
            payload = {"by_year": {}}

        # sanitize values
        out = {"by_year": {}}
        for year, obj in payload["by_year"].items():
            y = str(year)
            if not isinstance(obj, dict):
                continue
            def to_int(v):
                try: return int(v)
                except: return 0
            out["by_year"][y] = {
                "DEM": to_int(obj.get("DEM", 0)),
                "REP": to_int(obj.get("REP", 0)),
                "IND": to_int(obj.get("IND", 0)),
            }

        _ensure_dir(HOLDOVERS_PATH)
        with open(HOLDOVERS_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

        return jsonify({"ok": True, "path": HOLDOVERS_PATH, "years": sorted(out["by_year"].keys())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config/years", methods=["POST"])
def save_years_json():
    """
    Save years.json uploaded from year.html
    """
    try:
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "Invalid JSON"}), 400

        out_path = os.path.join(PARENT_DIR, "HistoricData","years.json")
        tmp = out_path + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        os.replace(tmp, out_path)

        return jsonify({
            "ok": True,
            "saved_to": out_path,
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/full-url")
def full_url_page():
    return send_file("full-url.html")

@app.route("/backend")
def backend_page():
    # Put backend.html in the same folder as your other static pages
    # (your app is created with static_folder='.' in your snippet)
    return send_file("backend.html")
    
@app.route("/results-type.html")
def results_type_page():
    return send_file("results-type.html")

@app.get("/api/results_type/load")
def api_results_type_load():
    rs = _results_type_load(force=True)
    try:
        mtime = os.path.getmtime(RESULTS_TYPE_PATH) if os.path.exists(RESULTS_TYPE_PATH) else 0.0
    except Exception:
        mtime = 0.0
    return jsonify({"ok": True, "resultstype": rs, "path": RESULTS_TYPE_PATH, "mtime": mtime})

@app.post("/api/results_type/save")
def api_results_type_save():
    data = request.get_json(silent=True) or {}
    rs = _results_type_norm(data.get("resultstype"))
    _results_type_save(rs)
    return jsonify({"ok": True, "resultstype": rs, "path": RESULTS_TYPE_PATH})


@app.route("/api/fs/list")
def api_fs_list():
    auth = _fm_require_auth()
    if auth: return auth

    p = request.args.get("path", "/")
    try:
        d = _fm_resolve(p)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid path"}), 400

    if not d.exists() or not d.is_dir():
        return jsonify({"ok": False, "error": "Not a directory"}), 400

    items = []
    try:
        for child in d.iterdir():
            try:
                st = child.stat()
                is_dir = child.is_dir()
                mime = None
                if not is_dir:
                    mime = mimetypes.guess_type(str(child.name))[0] or "application/octet-stream"
                items.append({
                    "name": child.name,
                    "is_dir": is_dir,
                    "size": None if is_dir else st.st_size,
                    "mtime": __import__("datetime").datetime.fromtimestamp(st.st_mtime).isoformat(),
                    "mime": mime,
                })
            except Exception:
                # skip unreadable entries
                continue
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "path": str(d), "items": items})

import tempfile, zipfile
from flask import after_this_request

@app.route("/api/fs/download")
def api_fs_download():
    auth = _fm_require_auth()
    if auth: return auth

    p = request.args.get("path", "")
    if not p:
        return jsonify({"ok": False, "error": "Missing path"}), 400

    zip_flag = (request.args.get("zip", "0") == "1")

    try:
        target = _fm_resolve(p)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid path"}), 400

    if not target.exists():
        return jsonify({"ok": False, "error": "Not found"}), 404

    # If it's a directory, allow zip=1 to download it
    if target.is_dir():
        if not zip_flag:
            return jsonify({"ok": False, "error": "Path is a directory (use zip=1)"}), 400

        base = target.name or "folder"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_path = tmp.name
        tmp.close()

        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z:
                root_dir = str(target)
                for root, dirs, files in os.walk(root_dir):
                    for fn in files:
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, start=root_dir)
                        arc = os.path.join(base, rel)  # keep a top-level folder name
                        z.write(full, arc)
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return jsonify({"ok": False, "error": str(e)}), 500

        @after_this_request
        def _cleanup(resp):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return resp

        return send_file(tmp_path, as_attachment=True, download_name=f"{base}.zip")

    # Normal file download
    return send_file(str(target), as_attachment=True, download_name=target.name)


@app.route("/api/fs/mkdir", methods=["POST"])
def api_fs_mkdir():
    auth = _fm_require_auth()
    if auth: return auth

    data = request.get_json(force=True, silent=True) or {}
    base = data.get("path", "/")
    name = (data.get("name") or "").strip()

    if not name or "/" in name or "\\" in name:
        return jsonify({"ok": False, "error": "Invalid folder name"}), 400

    try:
        d = _fm_resolve(base)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid base path"}), 400

    if not d.exists() or not d.is_dir():
        return jsonify({"ok": False, "error": "Base is not a directory"}), 400

    # Use secure_filename but keep dots/dashes sensible
    safe = secure_filename(name) or name.replace("..","").replace("/","").replace("\\","")
    out = (d / safe)

    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return jsonify({"ok": False, "error": "Folder already exists"}), 409
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "created": str(out)})

@app.route("/api/fs/upload", methods=["POST"])
def api_fs_upload():
    auth = _fm_require_auth()
    if auth: return auth

    base = request.form.get("path", "/")
    mode = (request.form.get("mode") or "files").lower().strip()

    try:
        dest_dir = _fm_resolve(base)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid base path"}), 400

    if not dest_dir.exists() or not dest_dir.is_dir():
        return jsonify({"ok": False, "error": "Base is not a directory"}), 400

    incoming = request.files.getlist("files") or []
    if not incoming:
        return jsonify({"ok": False, "error": "No files uploaded"}), 400

    saved = []
    skipped = []

    for f in incoming:
        if not f or not getattr(f, "filename", ""):
            continue

        # For folder uploads, the browser sends "sub/dir/file.ext" as filename.
        raw = f.filename.replace("\\", "/").lstrip("/")
        parts = [p for p in raw.split("/") if p and p != "."]
        # block traversal
        if any(p == ".." for p in parts):
            skipped.append(raw)
            continue

        # secure each part (folders + file)
        safe_parts = []
        for i, part in enumerate(parts):
            if i == len(parts)-1:
                safe_parts.append(secure_filename(part) or part)
            else:
                safe_parts.append(secure_filename(part) or part)
        rel = "/".join(safe_parts)
        out_path = (dest_dir / rel).resolve()

        # enforce base containment
        if str(FILE_MGR_BASE_PATH) != "/" and not str(out_path).startswith(str(FILE_MGR_BASE_PATH) + os.sep):
          skipped.append(raw)
          continue

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            f.save(str(out_path))
            saved.append(rel)
        except Exception:
            skipped.append(raw)

    return jsonify({"ok": True, "saved": saved, "skipped": skipped})

@app.route("/api/fs/delete", methods=["DELETE"])
def api_fs_delete():
    auth = _fm_require_auth()
    if auth: return auth

    data = request.get_json(force=True, silent=True) or {}
    p = data.get("path", "")
    if not p:
        return jsonify({"ok": False, "error": "Missing path"}), 400

    try:
        target = _fm_resolve(p)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid path"}), 400

    if not target.exists():
        return jsonify({"ok": False, "error": "Not found"}), 404

    try:
        if target.is_dir():
            shutil.rmtree(str(target))
            return jsonify({"ok": True, "deleted_dir": str(target)})
        else:
            target.unlink()
            return jsonify({"ok": True, "deleted_file": str(target)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/images/_list")
def list_images():
    BASE_DIR = Path(__file__).resolve().parent
    images_dir = BASE_DIR.parent / "images"

    out = []

    for p in images_dir.iterdir():
        if p.is_file() and p.suffix.lower() == ".png":
            out.append(p.name)

    return jsonify(out)

from flask import Response

@app.route("/images/custom-images.json", methods=["POST"])
def save_custom_images_json():
    BASE_DIR = Path(__file__).resolve().parent
    images_dir = BASE_DIR.parent / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    out_path = images_dir / "custom-images.json"

    try:
        data = request.get_json(force=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return Response("ok\n", status=200, mimetype="text/plain")

    except Exception as e:
        return Response(str(e), status=500, mimetype="text/plain")


# Robust HTTP session with retries & backoff for transient errors
HTTP = requests.Session()
HTTP.trust_env = False
_retries = Retry(
    total=3,                 # overall tries
    connect=3, read=3,
    backoff_factor=0.5,      # 0.5, 1.0, 2.0s ...
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET"]),
    raise_on_status=False
)
HTTP.mount("http://", HTTPAdapter(max_retries=_retries))
HTTP.mount("https://", HTTPAdapter(max_retries=_retries))

AP_API_KEY   = os.getenv("AP_API_KEY", os.getenv("AP_KEY", "ziwbrof5ondu4qq67ej4cz73to"))

# Identify the client and carry AP key like app-with-key.py
HTTP.headers.update({
    "User-Agent": "ElectionHub/1.0 (+ops@example.com)",
    "x-api-key": AP_API_KEY
})

_recent_pipelines = deque(maxlen=20)


_hub_started = False
def _start_hub_once():
    # start the polling thread exactly once per process
    global _hub_started
    if _hub_started:
        return
    _hub_started = True
    threading.Thread(target=_hub_loop, daemon=True).start()
    
    # start the periodic stats export thread (optional)
    try:
        _start_stats_export_once()
    except Exception:
        pass
    
    threading.Thread(target=_pipeline_sampler, daemon=True).start()

# NEW (works on Flask 3+, falls back on older Flask)
def _kick_hub_for_gunicorn():
    _start_hub_once()

# Try to start once when the worker begins serving (Flask 3+)
try:
    app.before_serving(_kick_hub_for_gunicorn)     # Flask 3.x
except Exception:
    # Fallbacks for older Flask
    try:
        app.before_request(_kick_hub_for_gunicorn) # runs on first request; guarded by _start_hub_once()
    except Exception:
        pass




def _load_overrides(force=False):
    """Hot-load overrides.json if it changed on disk, or on demand."""
    global _overrides_mtime, _overrides
    try:
        if not os.path.exists(OVERRIDES_PATH):
            with _overrides_lock:
                if force or not _overrides:
                    _overrides = {}
                    _overrides_mtime = 0.0
            return
        mt = os.path.getmtime(OVERRIDES_PATH)
        if force or mt != _overrides_mtime:
            with open(OVERRIDES_PATH, "r") as f:
                data = json.load(f) or {}
            with _overrides_lock:
                _overrides = data
                _overrides_mtime = mt
            log(f"Overrides loaded ({len(_overrides)} combos)")
    except Exception as e:
        log(f"Overrides load error: {e}", "WARN")


def _save_overrides():
    """Persist the overrides dict to disk (pretty for sanity)."""
    tmp = OVERRIDES_PATH + ".tmp"
    with _overrides_lock:
        data = json.dumps(_overrides, indent=2, sort_keys=True)
    with open(tmp, "w") as f:
        f.write(data)
    os.replace(tmp, OVERRIDES_PATH)
    try:
        os.chmod(OVERRIDES_PATH, 0o640)
    except Exception:
        pass
    log(f"Overrides saved to {OVERRIDES_PATH}")


def _bop_load(force=False):
    global _bop, _bop_mtime
    try:
        if not os.path.exists(BOP_OVERRIDES_PATH):
            with _bop_lock:
                if force or not _bop:
                    _bop = {}
                    _bop_mtime = 0.0
            return
        mt = os.path.getmtime(BOP_OVERRIDES_PATH)
        if force or mt != _bop_mtime:
            with open(BOP_OVERRIDES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            with _bop_lock:
                _bop = data
                _bop_mtime = mt
            log(f"BOP overrides loaded ({len(_bop)})")
    except Exception as e:
        log(f"BOP overrides load error: {e}", "WARN")

def _bop_save():
    tmp = BOP_OVERRIDES_PATH + ".tmp"
    with _bop_lock:
        data = json.dumps(_bop, indent=2, sort_keys=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, BOP_OVERRIDES_PATH)
    try:
        os.chmod(BOP_OVERRIDES_PATH, 0o640)
    except Exception:
        pass
    log(f"BOP overrides saved to {BOP_OVERRIDES_PATH}")

def _bop_key(year:int, chamber:str)->str:
    return f"{int(year)}|{(chamber or '').upper()[:1]}"



def _is_expired(payload):
    """Return True if payload has expires_at in the past."""
    exp = (payload or {}).get("expires_at")
    if not exp:
        # If no explicit expiry, synthesize one when default TTL > 0
        if OVERRIDE_DEFAULT_TTL_HOURS <= 0:
            return False
        try:
            created = (payload or {}).get("_created_at")
            if not created:
                return False
            t0 = datetime.fromisoformat(created.replace("Z",""))
            return (datetime.utcnow() - t0).total_seconds() > OVERRIDE_DEFAULT_TTL_HOURS*3600
        except Exception:
            return False
    try:
        return datetime.utcnow() > datetime.fromisoformat(exp.replace("Z",""))
    except Exception:
        return False


def _get_override(combo, unit):
    """Return an override payload for (combo, unit) if present and not expired."""
    _load_overrides()  # hot-reload if file changed
    with _overrides_lock:
        by_combo = (_overrides or {}).get(combo) or {}
        payload = by_combo.get(unit)
    if payload and _is_expired(payload):
        return None
    return payload


def _apply_psg_override(usps, parsed_state_blob, office, race_type):
    """P/S/G: overwrite state-level race_call if we have an override."""
    combo = _combo_key(office, race_type)
    ov = _get_override(combo, usps)
    if not ov:
        return

    # Current (API) view
    rc = parsed_state_blob.setdefault("race_call", {})
    cur_status = (rc.get("status") or "No Decision").strip()
    cur_party  = _norm_party((rc.get("winner") or {}).get("party"))
    sticky     = bool(ov.get("sticky"))

    # If API already says Called with a party, ignore non-sticky overrides
    if cur_status == "Called" and cur_party and not sticky:
        return

    # Apply override (normalize party when present)
    if ov.get("status"):
        rc["status"] = ov["status"]
    if ov.get("winner"):
        wname = ov["winner"].get("name")
        wpart = _norm_party(ov["winner"].get("party")) or None
        rc["winner"] = {"name": wname, "party": wpart}
    rc["source"] = "override"


def _apply_house_overrides(usps, parsed_districts, office, race_type):
    """
    Apply House overrides for a given state.
    Accepts unit keys as either 'CA-12' (USPS + 2-digit district) OR raw DistrictId.
    Normalizes inputs so admin keys like 'DE-00' and 'MN-08' match parsed districts.
    """
    combo = _combo_key(office, race_type)
    _load_overrides()  # ensure freshest view

    # Build an index from various key forms -> canonical DistrictId present in parsed_districts
    idx = {}
    for did, row in (parsed_districts or {}).items():
        dnum_raw = (row.get("district_num") or "").strip()
        idx[did] = did  # allow direct DistrictId lookups
        if dnum_raw:
            d2 = dnum_raw.zfill(2)           # pad "0"→"00", "8"→"08"
            idx[f"{usps}-{d2}"] = did
            if d2 == "00":                   # common at-large aliases
                idx[f"{usps}-0"]  = did
                idx[f"{usps}-AL"] = did

    with _overrides_lock:
        by_combo = (_overrides or {}).get(combo) or {}
        for unit_key, payload in by_combo.items():
            if _is_expired(payload):
                continue

            # Normalize incoming key
            uk = (unit_key or "").strip().upper()

            # Only act if it's for this state (USPS-XX) or a direct DistrictId we have
            if not (uk.startswith((usps or "").upper() + "-") or uk in parsed_districts or uk in idx):
                continue

            # usps-AL → usps-00
            if uk.endswith("-AL"):
                uk = uk[:-3] + "-00"

            # If pattern USPS-# or USPS-##, pad to two digits
            parts = uk.split("-")
            if len(parts) == 2 and parts[1].isdigit():
                uk = parts[0] + "-" + parts[1].zfill(2)

            # Resolve to canonical DistrictId
            did = idx.get(uk, uk)
            if did not in parsed_districts:
                continue

            # Apply override
            rc = parsed_districts[did].setdefault("race_call", {})
            if payload.get("status"):
                rc["status"] = payload["status"]
            if payload.get("winner"):
                rc["winner"] = {
                    "name":  payload["winner"].get("name"),
                    "party": _norm_party(payload["winner"].get("party")) or None
                }
            rc["source"] = "override"


def interpret_race_type(race_type: str) -> dict:
    """
    Minimal classifier just to decide which date to use.
    Treat D/R as primaries; everything else as general.
    """
    raw = (race_type or "G").upper()
    mode = "primary" if raw in ("D", "R") else "general"
    return {"raw": raw, "mode": mode}




# ---------------- Tunables (env) ----------------
# Statewide offices (P/S/G) endpoint:
BASE_URL_E    = os.getenv("BASE_URL_E", os.getenv("BASE_URL", "https://api.ap.org/v3/elections"))
#BASE_URL_E    = os.getenv("BASE_URL_E", os.getenv("BASE_URL", "http://localhost:5037/v2/elections"))
# House districts endpoint:
BASE_URL_D    = os.getenv("BASE_URL_D", "https://api.ap.org/v3/elections")
#BASE_URL_D    = os.getenv("BASE_URL_D", "http://localhost:5037/v2/districts")


#BASE_URL_E   = os.getenv("BASE_URL_E", "https://api.ap.org/v2/elections")
#BASE_URL_E   = os.getenv("BASE_URL_E", "https://api.ap.org/v3/elections")
#BASE_URL_D   = os.getenv("BASE_URL_D", BASE_URL_E)  # House also via /v2/elections

# ---------------- Tunables (env) ----------------
# Statewide offices (P/S/G) endpoint:
# BASE_URL_E = os.getenv("BASE_URL_E", "http://127.0.0.1:5037/v2/elections")
# House districts endpoint:
# BASE_URL_D = os.getenv("BASE_URL_D", "http://127.0.0.1:5037/v2/districts")

LEVEL_PARAM = os.getenv("LEVEL_PARAM", "ru")


SPECIAL_DATE  = os.getenv("SPECIAL_DATE", "2024-11-05")  # change per special
PRIMARY_DATE  = os.getenv("PRIMARY_DATE", "2024-11-05")
GENERAL_DATE  = os.getenv("GENERAL_DATE", "2024-11-05")
HUB_MODE      = os.getenv("HUB_MODE", "1") in ("1","true","True","YES","yes")

# ---- Single-call full dataset mode ----
# Default includes level=ru so county/district RUs are present; override to match your exact endpoint if desired.

# ---- Single-call full dataset mode ----



"""
    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2022-11-08?testID=20221108&statepostal=AZ&raceTypeID=G&level=ru&historyDateTime=2022-11-08T03:35:22.000Z")),
    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2022-11-08?testID=20221108&statepostal=AZ&raceTypeID=G&level=ru&historyDateTime=2022-11-09T03:35:22.000Z")),
    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2022-11-08?testID=20221108&statepostal=AZ&raceTypeID=G&level=ru&historyDateTime=2022-11-09T08:35:22.000Z")),
    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2022-11-08?testID=20221108&statepostal=AZ&raceTypeID=G&level=ru&historyDateTime=2022-11-09T18:35:22.000Z")),
    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2022-11-08?testID=20221108&statepostal=AZ&raceTypeID=G&level=ru&historyDateTime=2022-11-10T18:35:22.000Z")),
    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2022-11-08?testID=20221108&statepostal=AZ&raceTypeID=G&level=ru&historyDateTime=2022-11-11T18:35:22.000Z")),
    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2022-11-08?testID=20221108&statepostal=AZ&raceTypeID=G&level=ru&historyDateTime=2022-11-11T23:35:22.000Z")),
    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2022-11-08?testID=20221108&statepostal=AZ&raceTypeID=G&level=ru&historyDateTime=2022-11-12T04:35:22.000Z")),
"""


# ---- Single-call full dataset mode (ROTATING) ----
# 4 placeholders; swap these to real URLs later.
FULL_DATA_URLS = []

#FULL_DATA_URLS = [
#    os.getenv("FULL_DATA_URL_1", os.getenv("FULL_DATA_URL", "https://api.ap.org/v3/elections/2026-11-05?resultstype=b&level=ru")),
#]




_full_rot_idx = 0  # runtime rotation pointer
CURRENT_FULL_DATA_URL = "" # what _fetch_full_once() uses



#FULL_DATA_URL = os.getenv(
#    "FULL_DATA_URL",
#    f"https://api.ap.org/v3/elections/2024-11-05?testID=20241105&statepostal=VA&level=ru&raceTypeID=G&historyDateTime=2024-11-06T04:35:22.000Z"

#    f"https://api.ap.org/v3/elections/2020-11-03?testID=20201103&statepostal=WI&level=ru&raceTypeID=G&historyDateTime=2020-11-04T10:35:22.000Z"
#)





# NEW: runtime pointer (mutable by admin endpoint)
#CURRENT_FULL_DATA_URL = FULL_DATA_URL

# New England states: for these, skip writing subunits/counties into cache
NEW_ENGLAND = {"ME","NH","VT","MA","CT","RI"}


#FULL_DATA_URL = os.getenv(
#    "FULL_DATA_URL",
#    f"https://api.ap.org/v3/elections/2024-11-05?level={LEVEL_PARAM}&resultstype=b"
#)

#FULL_DATA_URL = os.getenv(
#    "FULL_DATA_URL",
#    f"https://api.ap.org/v3/elections/2016-11-08?level={LEVEL_PARAM}&resultstype=b"
#)


# Add after PRIMARY_URLS
PRIMARY_APP_DATES = {}

# -------- Primary endpoints rotation (DEM/REP per date) --------
PRIMARY_DATES = []

# Build the exact list you provided: D then R for each date, rotating in order
# 1) APP (House, state-scoped) FIRST


# APP first (unchanged)
# APP first (unchanged), then only REP (R) for each primary date
# APP first (unchanged), then interleave D and R for each primary date
PRIMARY_URLS = [
    f"https://api.ap.org/v3/elections/{d}?raceTypeID=APP&officeId=H&statepostal={usps}&level=ru&resultstype=b"
    for d, states in PRIMARY_APP_DATES.items()
    for usps in states
] + [
    url
    for d in PRIMARY_DATES
    for url in (
        f"https://api.ap.org/v3/elections/{d}?raceTypeID=D&level=ru&resultstype=b",
        f"https://api.ap.org/v3/elections/{d}?raceTypeID=R&level=ru&resultstype=b",
    )
]


# Start rotation after APP items so the first primary tick hits D.
APP_URL_COUNT = sum(len(states) for states in PRIMARY_APP_DATES.values())
_primary_rot_idx = APP_URL_COUNT






POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "3"))


# ========= Hub mode config (runtime) =========
# mode: "general" or "primary"
_hub_cfg = {
    "mode": "general",                    # default: general-election mode
    "full_date": "2024-11-05",            # used in general mode
    "main_date": None,                    # selected primary date by UI
    "secondary_dates": [],                # all others become secondary
    "dr_flip_main": "D",                  # alternator: D then R on main hits
    "dr_flip_secondary": "D",             # alternator: D then R on secondary hits
    "app_dates": PRIMARY_APP_DATES,       # {"2024-03-05":["CA"],"2024-08-06":["WA"]}
    "all_primary_dates": PRIMARY_DATES[:] # copy for UI convenience
}

_qp_idx = 0  # quick primary round-robin index


# last-hit ledger so UI can display "what URL just fired" + status
_last_hits = []  # list of dicts: {"ts": "...", "url": "...", "status": 200, "which": "main|secondary|full"}
def _record_last_hit(url, status, which):
    _last_hits.append({"ts": _now_iso(), "url": url, "status": status, "which": which})
    if len(_last_hits) > 200:  # trim
        del _last_hits[:len(_last_hits)-200]



# Which combos to poll (comma-separated). Add others like "Lib,Grn,NP,APP,RET" if desired.
OFFICES    = [x.strip().upper() for x in os.getenv("OFFICES", "G,A,M,H").split(",") if x.strip()]
RACE_TYPES = [x.strip().upper() for x in os.getenv("RACE_TYPES", "G,S").split(",") if x.strip()]

# Pace the hub to suit your infra:
MAX_CONCURRENCY        = int(os.getenv("MAX_CONCURRENCY", "1"))
STATES_PER_CYCLE       = int(os.getenv("STATES_PER_CYCLE", "50"))   # any
DELAY_BETWEEN_REQUESTS = float(os.getenv("DELAY_BETWEEN_REQUESTS","80"))
DELAY_BETWEEN_CYCLES   = float(os.getenv("DELAY_BETWEEN_CYCLES",".1"))
TIMEOUT_SECONDS        = float(os.getenv("TIMEOUT_SECONDS","15.0"))

# Snapshot in ./temp (project root)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(ROOT_DIR, ".."))  # go one folder up
TEMP_DIR = os.path.join(PARENT_DIR, "temp")                 # ../temp
os.makedirs(TEMP_DIR, exist_ok=True)
# Persistent UI config (mode/general/primary selections)
DATE_CONFIG_PATH = os.path.join(TEMP_DIR, "date.json")

# === Results-type config (b/t) ===
RESULTS_TYPE_PATH = os.path.join(TEMP_DIR, "results-type.json")
_results_type_lock = threading.RLock()
_results_type = "b"
_results_type_mtime = 0.0

def _results_type_norm(v) -> str:
    v = (v or "b")
    if not isinstance(v, str):
        return "b"
    v = v.strip().lower()
    return v if v in ("b", "t") else "b"

def _results_type_save(rs: str):
    rs = _results_type_norm(rs)
    os.makedirs(TEMP_DIR, exist_ok=True)
    tmp = RESULTS_TYPE_PATH + ".tmp"
    payload = {"resultstype": rs}
    with _results_type_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, RESULTS_TYPE_PATH)
        global _results_type, _results_type_mtime
        _results_type = rs
        try:
            _results_type_mtime = os.path.getmtime(RESULTS_TYPE_PATH)
        except Exception:
            _results_type_mtime = 0.0

def _results_type_load(force=False) -> str:
    """Load ../temp/results-type.json into memory. Returns current resultstype."""
    global _results_type, _results_type_mtime
    try:
        if not os.path.exists(RESULTS_TYPE_PATH):
            # create default if missing
            _results_type_save("b")
            return _results_type

        mtime = 0.0
        try:
            mtime = os.path.getmtime(RESULTS_TYPE_PATH)
        except Exception:
            mtime = 0.0

        if (not force) and mtime and mtime == _results_type_mtime:
            return _results_type

        with open(RESULTS_TYPE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}

        rs = _results_type_norm(data.get("resultstype"))
        with _results_type_lock:
            _results_type = rs
            _results_type_mtime = mtime
        return _results_type
    except Exception as e:
        # fail safe: keep current in-memory value
        return _results_type

def _resultstype_qp() -> str:
    # If you want hot-reload always, call _results_type_load() here.
    rs = _results_type_load(force=False)
    return f"resultstype={rs}"


# --- Full-data URL rotation list (general mode) ---
# Stored in ../temp/full-data-url.json like:
#   {"urls": ["https://...", "https://..."]}
FULL_DATA_URLS_PATH = os.path.join(TEMP_DIR, "full-data-url.json")

# --- Primary dates (new) ---
PRIMARY_DATES_NEW_PATH = os.path.join(TEMP_DIR, "primary-dates-new.json")
_primary_dates_new_lock = threading.RLock()
_primary_dates_new_mtime = 0.0

def _sanitize_primary_dates_list(lst):
    out = []
    seen = set()
    for d in (lst or []):
        s = (d or "").strip()
        if not s:
            continue
        # basic YYYY-MM-DD shape check
        if len(s) != 10 or s[4] != "-" or s[7] != "-":
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

def _sanitize_primary_app_dates(obj):
    """
    Expect dict like {"2024-03-05":["CA","TX"], ...}
    Normalize state codes to 2-letter uppercase, de-dupe.
    """
    out = {}
    if not isinstance(obj, dict):
        return out
    for date, states in obj.items():
        d = (str(date) or "").strip()
        if not d:
            continue
        if len(d) != 10 or d[4] != "-" or d[7] != "-":
            continue
        if not isinstance(states, list):
            continue
        cleaned = []
        seen = set()
        for st in states:
            s = (str(st) or "").strip().upper()
            if len(s) != 2:
                continue
            if s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
        if cleaned:
            out[d] = cleaned
    return out

def _primary_dates_new_apply(primary_dates, primary_app_dates, *, persist=False):
    """
    Applies to runtime (PRIMARY_DATES, PRIMARY_APP_DATES) and optionally persists.
    Also refreshes _hub_cfg copies for UI convenience.
    """
    global PRIMARY_DATES, PRIMARY_APP_DATES

    pd  = _sanitize_primary_dates_list(primary_dates)
    pad = _sanitize_primary_app_dates(primary_app_dates)

    with _primary_dates_new_lock:
        PRIMARY_DATES = pd
        PRIMARY_APP_DATES = pad

        # keep hub cfg copies in sync (your UI expects these)
        try:
            with _hub_cfg_lock:
                _hub_cfg["app_dates"] = PRIMARY_APP_DATES
                _hub_cfg["all_primary_dates"] = PRIMARY_DATES[:]
        except Exception:
            pass

        if persist:
            os.makedirs(TEMP_DIR, exist_ok=True)
            tmp = PRIMARY_DATES_NEW_PATH + ".tmp"
            payload = {
                "primary_app_dates": PRIMARY_APP_DATES,
                "primary_dates": PRIMARY_DATES
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            os.replace(tmp, PRIMARY_DATES_NEW_PATH)

def _primary_dates_new_load(*, force=False) -> bool:
    """
    Load ../temp/primary-dates-new.json if present; return True if applied.
    File format:
      {"primary_app_dates": {...}, "primary_dates": [...]}
    """
    global _primary_dates_new_mtime
    try:
        if not os.path.exists(PRIMARY_DATES_NEW_PATH):
            return False
        st = os.stat(PRIMARY_DATES_NEW_PATH)
        mtime = float(getattr(st, "st_mtime", 0.0) or 0.0)
        if (not force) and mtime and mtime <= _primary_dates_new_mtime:
            return False

        with open(PRIMARY_DATES_NEW_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}

        if not isinstance(data, dict):
            _primary_dates_new_mtime = mtime
            return False

        primary_app_dates = data.get("primary_app_dates")
        primary_dates = data.get("primary_dates")

        _primary_dates_new_apply(primary_dates, primary_app_dates, persist=False)
        _primary_dates_new_mtime = mtime
        return True
    except Exception as e:
        log(f"primary-dates-new.json load error: {e}", "WARN")
        return False


_full_urls_lock = threading.RLock()
_full_urls_mtime = 0.0
def _full_data_urls_apply(urls, *, persist: bool = False):
    """Apply a new FULL_DATA_URLS list and reset rotation safely."""
    global FULL_DATA_URLS, _full_rot_idx
    if urls is None:
        urls = []
    # sanitize
    cleaned = []
    for u in (urls or []):
        s = (u or "").strip()
        if not s:
            continue
        cleaned.append(s)
    with _full_urls_lock:
        FULL_DATA_URLS = cleaned
        _full_rot_idx = 0
        if FULL_DATA_URLS:
            globals()["CURRENT_FULL_DATA_URL"] = FULL_DATA_URLS[0]
        if persist:
            try:
                os.makedirs(TEMP_DIR, exist_ok=True)
                tmp = FULL_DATA_URLS_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"urls": FULL_DATA_URLS}, f, indent=2)
                os.replace(tmp, FULL_DATA_URLS_PATH)
            except Exception as e:
                log(f"full-data-url.json save error: {e}", "WARN")


def _full_data_urls_load(*, force: bool = False) -> bool:
    """Load ../temp/full-data-url.json if present; return True if applied."""
    global _full_urls_mtime
    try:
        if not os.path.exists(FULL_DATA_URLS_PATH):
            return False
        st = os.stat(FULL_DATA_URLS_PATH)
        mtime = float(getattr(st, "st_mtime", 0.0) or 0.0)
        if (not force) and mtime and mtime <= _full_urls_mtime:
            return False
        with open(FULL_DATA_URLS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        urls = None
        if isinstance(data, dict):
            urls = data.get("urls")

        if not isinstance(urls, list):

            # don't clobber an existing in-memory list on malformed file

            log("full-data-url.json: missing/invalid 'urls' list", "WARN")

            _full_urls_mtime = mtime

            return False



        _full_data_urls_apply(urls, persist=False)

        _full_urls_mtime = mtime

        return True

    except Exception as e:

        log(f"full-data-url.json load error: {e}", "WARN")

        return False



@app.get("/api/primary_dates_new/load")
def api_primary_dates_new_load():
    try:
        changed = _primary_dates_new_load(force=True)
        with _primary_dates_new_lock:
            return jsonify({
                "ok": True,
                "path": PRIMARY_DATES_NEW_PATH,
                "changed": bool(changed),
                "primary_app_dates": PRIMARY_APP_DATES,
                "primary_dates": list(PRIMARY_DATES),
            })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/primary_dates_new/save")
def api_primary_dates_new_save():
    try:
        data = request.get_json(force=True, silent=True) or {}

        primary_app_dates = data.get("primary_app_dates", {})
        primary_dates = data.get("primary_dates", [])

        # Apply + persist
        _primary_dates_new_apply(primary_dates, primary_app_dates, persist=True)

        # Refresh mtime tracker after persist
        try:
            st = os.stat(PRIMARY_DATES_NEW_PATH)
            globals()["_primary_dates_new_mtime"] = float(getattr(st, "st_mtime", 0.0) or 0.0)
        except Exception:
            pass

        with _primary_dates_new_lock:
            return jsonify({
                "ok": True,
                "path": PRIMARY_DATES_NEW_PATH,
                "primary_app_dates": PRIMARY_APP_DATES,
                "primary_dates": list(PRIMARY_DATES),
            })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/full_data_urls/load")

def api_full_data_urls_load():

    try:

        changed = _full_data_urls_load(force=True)

        with _full_urls_lock:

            return jsonify({

                "ok": True,

                "path": FULL_DATA_URLS_PATH,

                "changed": bool(changed),

                "urls": list(FULL_DATA_URLS),

                "current": globals().get("CURRENT_FULL_DATA_URL", "")

            })

    except Exception as e:

        return jsonify({"ok": False, "error": str(e)}), 500





@app.post("/api/full_data_urls/save")

def api_full_data_urls_save():

    try:

        data = request.get_json(force=True, silent=True) or {}

        urls = data.get("urls")

        if not isinstance(urls, list):

            return jsonify({"ok": False, "error": "Expected JSON body: {urls: [...]}"}), 400

        _full_data_urls_apply(urls, persist=True)

        # Refresh mtime tracker after persist

        try:

            st = os.stat(FULL_DATA_URLS_PATH)

            globals()["_full_urls_mtime"] = float(getattr(st, "st_mtime", 0.0) or 0.0)

        except Exception:

            pass

        return jsonify({

            "ok": True,

            "path": FULL_DATA_URLS_PATH,

            "urls": list(FULL_DATA_URLS),

            "current": globals().get("CURRENT_FULL_DATA_URL", "")

        })

    except Exception as e:

        return jsonify({"ok": False, "error": str(e)}), 500

# === BOP overrides store ===
BOP_OVERRIDES_PATH = os.path.join(TEMP_DIR, "bop_overrides.json")
_bop_lock = threading.Lock()
_bop = {}
_bop_mtime = 0.0

# === Pipeline targets store (which exports / pipelines to run) ===
PIPELINE_TARGETS_PATH = os.path.join(TEMP_DIR, "pipeline_targets.json")
_pipeline_targets_lock = threading.Lock()
_pipeline_targets = None
_pipeline_targets_mtime = 0.0

def _normalize_target_row(row: dict):
    try:
        office = (row.get("office") or "").strip().upper()
        state  = (row.get("state")  or "").strip().upper()
        enabled = (row.get("enabled", True) is not False)

        if office not in ("P","S","G","H"):
            return None
        if state not in ALL_STATES:
            return None

        district = row.get("district")
        if office == "H":
            if district is None:
                district = ""
            district = str(district).strip()
            if not district:
                return None
            # accept "1" or "01" → store "01"
            try:
                n = int(district)
            except Exception:
                return None
            if n < 1 or n > 99:
                return None
            district = str(n).zfill(2)
        else:
            district = None

        return {"office": office, "state": state, "district": district, "enabled": enabled}
    except Exception:
        return None

def _default_pipeline_targets():
    # keep your current default behavior:
    # ALL_STATES minus NEW_ENGLAND; PSG statewide + H 01–59
    target_states = [s for s in ALL_STATES if s not in NEW_ENGLAND]
    out = []
    for st in target_states:
        for office in ("P","S","G"):
            out.append({"office": office, "state": st, "district": None, "enabled": True})
        for d in range(1, 60):
            out.append({"office": "H", "state": st, "district": str(d).zfill(2), "enabled": True})
    return out

def _pipeline_targets_load():
    global _pipeline_targets, _pipeline_targets_mtime
    try:
        if not os.path.exists(PIPELINE_TARGETS_PATH):
            with _pipeline_targets_lock:
                _pipeline_targets = _default_pipeline_targets()
                _pipeline_targets_mtime = 0.0
            return

        m = os.path.getmtime(PIPELINE_TARGETS_PATH)
        with _pipeline_targets_lock:
            if _pipeline_targets is not None and m == _pipeline_targets_mtime:
                return

        with open(PIPELINE_TARGETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw_list = data.get("targets") if isinstance(data, dict) else None
        if not isinstance(raw_list, list):
            raw_list = []

        cleaned = []
        seen = set()
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            nr = _normalize_target_row(r)
            if not nr:
                continue
            k = (nr["office"], nr["state"], nr["district"] or "")
            if k in seen:
                # keep first; if any dup is enabled, turn on
                if cleaned:
                    for item in cleaned:
                        if (item["office"], item["state"], item["district"] or "") == k:
                            item["enabled"] = item["enabled"] or nr["enabled"]
                            break
                continue
            seen.add(k)
            cleaned.append(nr)

        if not cleaned:
            cleaned = _default_pipeline_targets()

        with _pipeline_targets_lock:
            _pipeline_targets = cleaned
            _pipeline_targets_mtime = m
    except Exception as e:
        log(f"pipeline_targets.json load error: {e}", "WARN")
        with _pipeline_targets_lock:
            _pipeline_targets = _default_pipeline_targets()

def _pipeline_targets_save(target_list: list):
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        payload = {"targets": target_list}
        tmp = PIPELINE_TARGETS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, PIPELINE_TARGETS_PATH)
        # update cache
        with _pipeline_targets_lock:
            global _pipeline_targets, _pipeline_targets_mtime
            _pipeline_targets = target_list
            try:
                _pipeline_targets_mtime = os.path.getmtime(PIPELINE_TARGETS_PATH)
            except Exception:
                _pipeline_targets_mtime = 0.0
    except Exception as e:
        raise

def _pipeline_targets_active():
    _pipeline_targets_load()
    with _pipeline_targets_lock:
        base = list(_pipeline_targets or [])
    # only enabled rows actually run
    active = [t for t in base if t.get("enabled", True)]
    return active, base


# === Overrides config ===
OVERRIDES_PATH = os.getenv("OVERRIDES_PATH", os.path.join(TEMP_DIR, "overrides.json"))
OVERRIDE_DEFAULT_TTL_HOURS = float(os.getenv("OVERRIDE_DEFAULT_TTL_HOURS", "0"))  # 0 = no TTL by default

# Basic auth for admin endpoints
OVERRIDE_USER = os.getenv("OVERRIDE_USER", "")
OVERRIDE_PASS = os.getenv("OVERRIDE_PASS", "")

_overrides_lock = threading.Lock()
_overrides = {}         # in-memory dict { "P:G": { "CA": {...} }, "H:G": { "CA-12": {...} } }
_overrides_mtime = 0.0  # to hot-reload when the file changes

_hub_cfg_lock = threading.RLock()

def _date_cfg_save():
    """Persist _hub_cfg to ../temp/date.json atomically."""
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        tmp = DATE_CONFIG_PATH + ".tmp"
        with _hub_cfg_lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_hub_cfg, f, indent=2, sort_keys=True)
        os.replace(tmp, DATE_CONFIG_PATH)
    except Exception as e:
        log(f"date.json save error: {e}", "WARN")

def _date_cfg_load():
    """Load ../temp/date.json into _hub_cfg (if present) and update runtime pointers."""
    try:
        if not os.path.exists(DATE_CONFIG_PATH):
            # No persisted config yet: write out the in-memory defaults
            _date_cfg_save()
            return

        with open(DATE_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        with _hub_cfg_lock:
            # Only accept known keys to avoid junk
            for k in ("mode","full_date","main_date","secondary_dates",
                      "dr_flip_main","dr_flip_secondary","app_dates","all_primary_dates"):
                if k in data:
                    _hub_cfg[k] = data[k]
            # Update the full pointer immediately (for general mode)
            fd = _hub_cfg.get("full_date") or "2024-11-05"
            globals()["CURRENT_FULL_DATA_URL"] = f"{BASE_URL_E}/{fd}?level={LEVEL_PARAM}&resultstype=b"
    except Exception as e:
        log(f"date.json load error: {e}", "WARN")


FAVICONS_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "favicons"))

# Where we persist the merged primary cache (separate from p_cache.json)
PRIMARY_SNAPSHOT_PATH = os.getenv(
    "PRIMARY_SNAPSHOT_PATH",
    os.path.join(TEMP_DIR, "primary_cache.json")
)

from flask import send_file, jsonify
import os


@app.route("/images/_upload", methods=["POST"])
def upload_images():
    BASE_DIR = Path(__file__).resolve().parent
    images_dir = BASE_DIR.parent / "images"   # ../images
    images_dir.mkdir(parents=True, exist_ok=True)

    # expects multipart/form-data with one or more files under key "files"
    incoming = request.files.getlist("files")

    saved = []
    skipped = []

    for f in (incoming or []):
        if not f or not getattr(f, "filename", ""):
            continue

        name = secure_filename(f.filename or "")
        if not name.lower().endswith(".png"):
            skipped.append(f.filename)
            continue

        try:
            (images_dir / name).parent.mkdir(parents=True, exist_ok=True)
            f.save(str(images_dir / name))
            saved.append(name)
        except Exception:
            skipped.append(f.filename)

    return jsonify({"ok": True, "saved": saved, "skipped": skipped})


@app.route("/images/_delete/<path:filename>", methods=["DELETE"])
def delete_image(filename):
    BASE_DIR = Path(__file__).resolve().parent
    images_dir = BASE_DIR.parent / "images"   # ../images
    images_dir.mkdir(parents=True, exist_ok=True)

    # harden against traversal
    raw = (filename or "").split("/")[-1].split("\\")[-1]
    name = secure_filename(raw)

    if not name or not name.lower().endswith(".png"):
        return jsonify({"ok": False, "error": "invalid filename (png only)"}), 400

    p = images_dir / name
    if not p.exists():
        return jsonify({"ok": False, "error": "not found"}), 404

    try:
        p.unlink()
        return jsonify({"ok": True, "deleted": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/primary-dates-new.html")
def primary_dates_new_page():
    return send_file("primary-dates-new.html")

@app.route("/win_prob/<office>/<state>/<path:rest>")
def serve_win_prob_json(office, state, rest):
    """
    Serve win_prob JSON files.
    If a file does not exist, return empty JSON with 200
    to avoid browser console 404 noise.
    """

    base_dir = os.path.join(PARENT_DIR, "win_prob", office.upper(), state.upper())
    file_path = os.path.join(base_dir, rest)

    # House has an extra district layer; path already includes it
    if os.path.isfile(file_path):
        return send_file(file_path, mimetype="application/json")

    # ✅ CRITICAL: return EMPTY JSON, NOT 404
    return jsonify({
        "meta": {"missing": True},
        "series": {}
    })

@app.route("/api/pipeline/request", methods=["POST"])
def pipeline_request():
    """
    Manual "Go" button:
    - Normalizes the current race selection.
    - Forces an export for that single target (even if not in TARGETS).
    - Marks the resulting pipeline job as "manual-next" so it starts next in the queue.
    """
    data = request.get_json(force=True, silent=True) or {}

    office = (data.get("office") or "").strip().upper() or None
    state  = (data.get("state")  or "").strip().upper() or None
    district = data.get("district")

    # Normalize district
    if office != "H":
        district = None
    else:
        if district is None:
            district = None
        else:
            d = str(district).strip()
            # allow inputs like "4215" / "0601" (statefp+dist) → keep last 2
            if len(d) >= 2:
                d2 = d[-2:]
            else:
                d2 = d
            try:
                district = str(int(d2)).zfill(2)
            except Exception:
                district = d2.zfill(2)

    log(f"[PIPELINE][REQUEST] received office={office} state={state} district={district}", "INFO")

    if not office or not state:
        return jsonify({
            "ok": False,
            "error": "missing office/state (UI didn't send current selection)",
            "received": {"office": office, "state": state, "district": district},
        }), 400

    # Mark this key as "next" so when export finishes and enqueues pipeline, it jumps to the front.
    key = _pipeline_key(office, state, district)
    _mark_manual_next_pipeline(key)

    def _do_export():
        try:
            _export_stats_once(office=office, usps=state, district=district)
        except Exception as e:
            log(f"[PIPELINE][REQUEST] export failed key={key}: {e}", "WARN")

    threading.Thread(target=_do_export, daemon=True).start()

    return jsonify({
        "ok": True,
        "queued_next": True,
        "key": key,
        "received": {"office": office, "state": state, "district": district},
    })
@app.route("/favicons/<path:filename>")
def serve_favicons(filename):
    return send_from_directory(FAVICONS_DIR, filename)
    
CACHE_SNAPSHOT_PATH = os.getenv(
    "CACHE_SNAPSHOT_PATH",
    os.path.join(TEMP_DIR, "p_cache.json")
)

SCRIPTS_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "scripts"))

@app.route("/scripts/<path:filename>")
def serve_scripts(filename):
    return send_from_directory(SCRIPTS_DIR, filename)

# Serve ../HistoricData as /HistoricData
HISTORIC_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "HistoricData"))

# ---- FullData (on-demand full dataset saves; NOT part of hub loop) ----
FULLDATA_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "FullData"))
os.makedirs(FULLDATA_DIR, exist_ok=True)

def _fulldata_safe_name(name: str) -> str:
    # allow only simple "<year>.json" filenames
    n = (name or "").strip()
    if not n.endswith(".json"):
        return ""
    base = n[:-5]
    if not (len(base) == 4 and base.isdigit()):
        return ""
    return f"{base}.json"

def _mmddyyyy_to_ymd(s: str) -> str | None:
    # input: mm.dd.yyyy -> output: yyyy-mm-dd
    import re
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", (s or "").strip())
    if not m:
        return None
    mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
    # quick sanity
    try:
        mi, di = int(mm), int(dd)
        if mi < 1 or mi > 12 or di < 1 or di > 31:
            return None
    except Exception:
        return None
    return f"{yyyy}-{mm}-{dd}"


def _target_dir_for(office: str, usps: str, district: str | None):
    office = (office or "").upper()
    usps   = (usps or "").upper()

    d = os.path.join(PARENT_DIR, "win_prob", office, usps)
    if office == "H":
        d = os.path.join(d, str(district or "").zfill(2))
    os.makedirs(d, exist_ok=True)
    return d

# ---------------- Stats export (server-side) ----------------
# Mirrors the "Stats" and "Stats 2" button logic in index.html, but writes to disk.
STATS_DIR = os.getenv("STATS_DIR", os.path.join(PARENT_DIR, "stats"))
try:
    os.makedirs(STATS_DIR, exist_ok=True)
except Exception:
    pass


def _env_bool(name: str, default: str = "1") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")

# ⬇️ ADD THIS HERE (module level, not inside any function)
SKIP_PIPELINE_IF_NO_CHANGE = _env_bool("SKIP_PIPELINE_IF_NO_CHANGE", "0")


# shared/constants.py (or top of app_hub.py if you prefer)



STATS_EXPORT_ENABLED  = _env_bool("STATS_EXPORT_ENABLED", "1")
STATS_EXPORT_INTERVAL = float(os.getenv("STATS_EXPORT_INTERVAL", "20"))
STATS_EXPORT_YEAR     = int(os.getenv("STATS_EXPORT_YEAR", "2026"))
#STATS_EXPORT_PRIOR_OFFSET = int(os.getenv("STATS_EXPORT_PRIOR_OFFSET", "2"))  # mirrors YEAR-2 used in UI
OFFICE_PRIOR_OFFSETS = {"G": 6, "P": 6, "S": 8, "H": 4}

STATS_EXPORT_USPS     = os.getenv("STATS_EXPORT_USPS", "CA").upper()
STATS_EXPORT_OFFICE   = os.getenv("STATS_EXPORT_OFFICE", "H").upper()
STATS_EXPORT_RACE     = os.getenv("STATS_EXPORT_RACE_TYPE", "G").upper()
STATS_EXPORT_TIMESTAMP = os.getenv("STATS_EXPORT_TIMESTAMP", "2024-11-05T15-30-22")  # matches UI export
# House-only targeting (seat number like "01"; DistrictId becomes "0101" for AL-01)
STATS_EXPORT_DISTRICT_NUM = os.getenv("STATS_EXPORT_DISTRICT_NUM", "13").strip().zfill(2)


_stats_export_started = False

def _atomic_write_text(path: str, text: str):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)

def _party_votes_from_candidates(cands):
    dem = rep = tot = 0
    for c in (cands or []):
        try:
            v = int(c.get("votes") or 0)
        except Exception:
            try:
                v = int(float(c.get("votes") or 0))
            except Exception:
                v = 0
        if v < 0:
            continue
        tot += v
        p = _norm_party(c.get("party") or "")
        if p == "DEM":
            dem += v
        elif p == "REP":
            rep += v
    other = max(0, tot - dem - rep)
    return dem, rep, other

def _build_stats_lines_from_state_blob(usps: str, state_blob: dict, *, pad_fips: bool, comma_safe_county: bool):
    header = ["timestamp","state","county","fips","trump_votes","harris_votes","other_votes","eevp","state_eevp"]
    lines = [",".join(header)]
    if not state_blob or not isinstance(state_blob, dict):
        return lines

    counties = state_blob.get("counties") or {}
    state_eevp = state_blob.get("percent_in") or state_blob.get("state_percent_in") or ""

    # Stable order (UI doesn't guarantee order; this makes diffs sane)
    def _sort_key(k):
        s = str(k)
        try:
            return (0, int(s))
        except Exception:
            return (1, s)
    for fips_key in sorted((counties or {}).keys(), key=_sort_key):
        cty = (counties or {}).get(fips_key) or {}
        county_name = cty.get("name") or cty.get("county_name") or cty.get("county") or ""
        if comma_safe_county:
            county_name = str(county_name).replace(",", " ")

        fips = str(cty.get("fips") or fips_key or "")
        if pad_fips:
            fips = fips.zfill(5)

        dem, rep, other = _party_votes_from_candidates(cty.get("candidates") or [])
        eevp = cty.get("percent_in") if cty.get("percent_in") is not None else (cty.get("eevp") or "")

        row = [
            str(STATS_EXPORT_TIMESTAMP),
            str(usps),
            str(county_name),
            str(fips),
            str(rep),    # trump_votes (REP)
            str(dem),    # harris_votes (DEM)
            str(other),
            str(eevp),
            str(state_eevp),
        ]
        lines.append(",".join(row))

        # ---- NEW: fallback when no counties exist (statewide historic data) ----
        if len(lines) == 1:
            dem = rep = other = 0

            # try candidates if present
            dem, rep, other = _party_votes_from_candidates(
                (state_blob or {}).get("candidates") or []
            )

            lines.append(",".join([
                str(STATS_EXPORT_TIMESTAMP),
                str(usps),
                usps,                # county placeholder
                usps + "000",        # fake fips to satisfy pipeline
                str(rep),
                str(dem),
                str(other),
                str(state_blob.get("percent_in") or ""),
                str(state_blob.get("percent_in") or ""),
            ]))

    return lines

def _build_stats_lines_from_house_district(usps: str, district_id: str, district_blob: dict, *, pad_fips: bool, comma_safe_county: bool):
    # Keep the SAME header as statewide so the rest of your pipeline doesn’t instantly break.
    header = ["timestamp","state","county","fips","trump_votes","harris_votes","other_votes","eevp","state_eevp"]
    lines = [",".join(header)]
    if not district_blob or not isinstance(district_blob, dict):
        return lines

    counties = district_blob.get("counties") or {}
    district_eevp = district_blob.get("percent_in") or ""

    def _sort_key(k):
        s = str(k)
        try:
            return (0, int(s))
        except Exception:
            return (1, s)

    for fips_key in sorted((counties or {}).keys(), key=_sort_key):
        cty = (counties or {}).get(fips_key) or {}
        county_name = cty.get("name") or cty.get("county_name") or cty.get("county") or ""
        if comma_safe_county:
            county_name = str(county_name).replace(",", " ")

        fips = str(cty.get("fips") or fips_key or "")
        if pad_fips:
            fips = fips.zfill(5)

        dem, rep, other = _party_votes_from_candidates(cty.get("candidates") or [])
        eevp = cty.get("percent_in") if cty.get("percent_in") is not None else (cty.get("eevp") or "")

        row = [
            str(STATS_EXPORT_TIMESTAMP),
            str(usps),
            str(county_name),
            str(fips),
            str(rep),     # trump_votes (REP)
            str(dem),     # harris_votes (DEM)
            str(other),
            str(eevp),
            str(district_eevp),  # written into "state_eevp" slot for compatibility
        ]
        lines.append(",".join(row))

    # If district has no counties, still write one row so you can see it exported
    if len(lines) == 1:
        dem, rep, other = _party_votes_from_candidates(district_blob.get("candidates") or [])
        lines.append(",".join([
            str(STATS_EXPORT_TIMESTAMP),
            str(usps),
            f"{usps}-{district_blob.get('district_num') or STATS_EXPORT_DISTRICT_NUM}",
            str(district_id),
            str(rep), str(dem), str(other),
            str(district_blob.get("percent_in") or ""),
            str(district_blob.get("percent_in") or ""),
        ]))

    return lines


def _load_historic_combo_states(year: int, combo: str):
    """Load HistoricData/<year>.json and return its states map for combo (or None)."""
    p = os.path.join(HISTORIC_DIR, f"{year}.json")
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        jd = json.load(f)

    # Mirror UI loader behavior:
    root = None
    if isinstance(jd, dict):
        root = (jd.get("cache_by_combo", {}) or {}).get(combo) or jd.get(combo) or jd
    if not isinstance(root, dict):
        return None
    states = root.get("states") or root.get("States")
    if not isinstance(states, dict):
        return None
    return states


"""
def _export_stats_once():
    usps   = STATS_EXPORT_USPS
    office = STATS_EXPORT_OFFICE
    race   = STATS_EXPORT_RACE
    combo  = _combo_key(office, race)

    # Compute House DistrictId like "0101" for AL-01
    district_id = None
    if office == "H":
        stfp = USPS_TO_STATEFP.get(usps, "")
        district_id = (stfp + STATS_EXPORT_DISTRICT_NUM) if stfp else STATS_EXPORT_DISTRICT_NUM

    # ---- stats.csv (current live cache) ----
    if office == "H":
        with _cache_lock:
            st = ((_cache.get("cache_by_combo") or {}).get(combo) or {}).get("states", {}).get(usps)
            district_blob = ((st or {}).get("districts") or {}).get(district_id)

        lines = _build_stats_lines_from_house_district(usps, district_id, district_blob, pad_fips=False, comma_safe_county=True)
    else:
        with _cache_lock:
            state_blob = ((_cache.get("cache_by_combo") or {}).get(combo) or {}).get("states", {}).get(usps)
        lines = _build_stats_lines_from_state_blob(usps, state_blob, pad_fips=False, comma_safe_county=True)

    _atomic_write_text(os.path.join(STATS_DIR, "stats.csv"), "\n".join(lines) + "\n")

    # ---- stats2.csv (historic cache) ----
    prior_year = STATS_EXPORT_YEAR - STATS_EXPORT_PRIOR_OFFSET
    try:
        states = _load_historic_combo_states(prior_year, combo)
        prior_state = (states or {}).get(usps) if isinstance(states, dict) else None

        if office == "H":
            prior_dist = ((prior_state or {}).get("districts") or {}).get(district_id)
            lines2 = _build_stats_lines_from_house_district(usps, district_id, prior_dist, pad_fips=True, comma_safe_county=False)
        else:
            lines2 = _build_stats_lines_from_state_blob(usps, prior_state, pad_fips=True, comma_safe_county=False)

    except Exception as e:
        log(f"Stats2 export error: {e}", "WARN")
        lines2 = ["timestamp,state,county,fips,trump_votes,harris_votes,other_votes,eevp,state_eevp"]

    _atomic_write_text(os.path.join(STATS_DIR, "stats2.csv"), "\n".join(lines2) + "\n")

    log(f"Stats export -> {usps} {office}:{race} rows={max(0,len(lines)-1)}; stats2(year={prior_year}) rows={max(0,len(lines2)-1)}")

    _kick_pipeline_after_export()
"""

def _export_stats_once(*, office, usps, district):
    key = f"{office}:{usps}:{district or ''}"
    info = _pipeline_procs.get(key)
    if info is not None and info["proc"].poll() is None:
        log(f"Export skipped (pipeline running): {key}", "INFO")
        return


    combo = _combo_key(office, STATS_EXPORT_RACE)
    race   = STATS_EXPORT_RACE
    combo  = _combo_key(office, race)

    # For House, build DistrictId like "0101" (AL-01), "0401" (AZ-01), etc.
    district_id = None
    if office == "H":
        stfp = USPS_TO_STATEFP.get(usps, "")
        d = (district or "").zfill(2)
        district_id = f"{stfp}{d}" if stfp else d


    # ---- stats.csv (current live cache) ----
    with _cache_lock:
        state_blob = ((_cache.get("cache_by_combo") or {}).get(combo) or {}).get("states", {}).get(usps)

    if office == "H":
        dist_blob = ((state_blob or {}).get("districts") or {}).get(district_id)
        lines = _build_stats_lines_from_house_district(
            usps, district_id, dist_blob,
            pad_fips=False,
            comma_safe_county=True
        )
    else:
        lines = _build_stats_lines_from_state_blob(
            usps, state_blob,
            pad_fips=False,
            comma_safe_county=True
        )

    # ---- GATE: if header-only, do NOT overwrite last-good stats.csv ----
    # ---- stats.csv (current live cache) ----
    data_rows = len(lines) - 1
    if data_rows <= 0:
        log(
            f"Pipeline skipped (empty stats.csv): office={office} state={usps} district={district} "
            "(leaving last-good stats.csv intact)",
            "INFO"
        )
        return
    
    out_dir = _target_dir_for(office, usps, district)
    stats_path  = os.path.join(out_dir, "stats.csv")
    stats3_path = os.path.join(out_dir, "stats3.csv")

    _atomic_write_text(stats_path, "\n".join(lines) + "\n")
    
    # ---- PIPELINE GATE: stats.csv vs stats3.csv ----
    # If stats is identical to last-run stats3, skip pipeline and delete stats.csv.
    try:
        if os.path.exists(stats3_path) and _files_equal(stats_path, stats3_path):
            log(
                f"[PIPELINE] SKIP (stats unchanged): {office}/{usps}/{district}",
                "INFO"
            )
            return
    except Exception as e:
        log(f"[PIPELINE] gate error (continuing to run): {e}", "WARN")

    # Data changed → refresh stats3 to match current stats, then proceed to pipeline
    try:
        if os.path.exists(stats3_path):
            os.remove(stats3_path)
        shutil.copyfile(stats_path, stats3_path)
    except Exception as e:
        log(f"[PIPELINE] unable to refresh stats3 (continuing): {e}", "WARN")



    prior_offset = OFFICE_PRIOR_OFFSETS[office]
    prior_year = STATS_EXPORT_YEAR - prior_offset

    # ---- stats2.csv (historic cache) ----
    #prior_year = STATS_EXPORT_YEAR - STATS_EXPORT_PRIOR_OFFSET

    try:
        states = _load_historic_combo_states(prior_year, combo)
        if not states:
            log(f"Stats2 missing historic: year={prior_year} combo={combo} (file missing or no states)", "WARN")

        prior_state = (states or {}).get(usps) if isinstance(states, dict) else None
        if not prior_state:
            log(f"Stats2 missing state in historic: year={prior_year} combo={combo} state={usps}", "WARN")

        if office == "H":
            prior_dist = ((prior_state or {}).get("districts") or {}).get(district_id)
            lines2 = _build_stats_lines_from_house_district(
                usps, district_id, prior_dist,
                pad_fips=True,
                comma_safe_county=False
            )
        else:
            lines2 = _build_stats_lines_from_state_blob(
                usps, prior_state,
                pad_fips=True,
                comma_safe_county=False
            )

    except Exception as e:
        log(f"Stats2 export error: {e}", "WARN")
        lines2 = ["timestamp,state,county,fips,trump_votes,harris_votes,other_votes,eevp,state_eevp"]

    _atomic_write_text(os.path.join(out_dir, "stats2.csv"), "\n".join(lines2) + "\n")

    #_atomic_write_text(os.path.join(STATS_DIR, "stats2.csv"), "\n".join(lines2) + "\n")

    if office == "H":
        log(f"Stats export -> {usps} {office}:{race} district_id={district_id} rows={max(0,len(lines)-1)}; stats2(year={prior_year}) rows={max(0,len(lines2)-1)}")
    else:
        log(f"Stats export -> {usps} {office}:{race} rows={max(0,len(lines)-1)}; stats2(year={prior_year}) rows={max(0,len(lines2)-1)}")


    fp_key = f"{office}:{usps}:{district or ''}"

    summary_row = {
        "office": office,                 # "H", "P", "S", "G"
        "state": usps,                    # "CA", "PA"
        "district": district or "",       # "13" or ""
        "rows": data_rows,                # number of rows written to stats.csv
        "prior_year": prior_year,         # used stats2 year
    }

    new_fp = race_fingerprint(summary_row)
    old_fp = LAST_FINGERPRINTS.get(fp_key)

    if SKIP_PIPELINE_IF_NO_CHANGE and new_fp == old_fp:
        log(f"[SKIP] no data change for {fp_key}")
        return   # 🚫 gated skip



    # ---- data changed → allow pipeline ----
    LAST_FINGERPRINTS[fp_key] = new_fp
    save_fingerprints(LAST_FINGERPRINTS)

    log(f"[RUN] data changed for {fp_key}, launching pipeline")

    _kick_pipeline_after_export(
        office=office,
        usps=usps,
        district=district
    )





# Run pipeline after writing stats.csv + stats2.csv
STATS_EXPORT_RUN_PIPELINE = _env_bool("STATS_EXPORT_RUN_PIPELINE", "1")  # set 0 to disable
PIPELINE_CMD = os.getenv("PIPELINE_CMD", "pipeline.py")  # resolved relative to STATS_DIR
PIPELINE_PATH = os.getenv("PIPELINE_PATH", os.path.join(STATS_DIR, PIPELINE_CMD))



_pipeline_procs = {}      # key -> Popen/None
_pipeline_locks = {}      # key -> Lock

# ---- Pipeline concurrency control ----
PIPELINE_MAX_CONCURRENCY = int(os.getenv("PIPELINE_MAX_CONCURRENCY", "4"))
_pipeline_semaphore = threading.Semaphore(PIPELINE_MAX_CONCURRENCY)

_pipeline_peak_rss_mb = 0.0
_pipeline_last_rss_mb = 0.0

def _pipeline_sampler():
    """Continuously sample pipeline RSS/CPU so peaks are not missed."""
    last_logged_peak = 0.0  # local state for this thread

    while True:
        try:
            _system_usage()  # updates global _pipeline_peak_rss_mb

            if _pipeline_peak_rss_mb > last_logged_peak:
                log(f"Pipeline RSS ramp → {_pipeline_peak_rss_mb:.0f}MB")
                last_logged_peak = _pipeline_peak_rss_mb

        except Exception as e:
            log(f"Sampler error: {e}", "WARN")

        time.sleep(0.5)



def _system_usage():
    """
    Return dict with:
      - system CPU / RAM
      - app RSS
      - per-pipeline CPU + RSS
      - peak pipeline RSS
    """
    global _pipeline_peak_rss_mb, _pipeline_last_rss_mb

    mem = psutil.virtual_memory()
    proc = psutil.Process(os.getpid())

    # App (hub) memory
    app_rss_mb = proc.memory_info().rss / (1024 * 1024)

    pipelines = []

    for key, info in list(_pipeline_procs.items()):
        p = info["proc"]

        if p.poll() is not None:
            continue  # process finished

        try:
            pp = psutil.Process(p.pid)
            rss_mb = pp.memory_info().rss / (1024 * 1024)
            cpu = pp.cpu_percent(interval=0.0)

            info["last_rss_mb"] = rss_mb
            info["last_cpu"] = cpu

            # ---- ignore startup noise & update peaks ----
            if rss_mb > 5:  # MB threshold
                info["peak_rss_mb"] = max(info["peak_rss_mb"], rss_mb)
                _pipeline_last_rss_mb = rss_mb
                _pipeline_peak_rss_mb = max(
                    _pipeline_peak_rss_mb,
                    info["peak_rss_mb"],
                )


            pipelines.append({
                "key": key,
                "pid": p.pid,
                "cpu_percent": cpu,
                "rss_mb": rss_mb,
                "peak_rss_mb": info["peak_rss_mb"],
                "runtime_sec": int(time.time() - info["start_ts"]),
            })

        except psutil.NoSuchProcess:
            continue


    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "mem_percent": mem.percent,
        "mem_used_mb": (mem.total - mem.available) / (1024 * 1024),
        "mem_total_mb": mem.total / (1024 * 1024),

        "app_rss_mb": app_rss_mb,

        "pipelines": pipelines,
        "pipeline_count": len(pipelines),
        "pipeline_last_rss_mb": _pipeline_last_rss_mb,
        "pipeline_peak_rss_mb": _pipeline_peak_rss_mb,
        "recent_pipelines": list(_recent_pipelines),
    }





# ---- Pipeline start-order queue (so manual "Go" runs next) ----
_pipeline_job_deque = deque()
_pipeline_job_cond  = threading.Condition()
_pipeline_job_set   = set()
_pipeline_dispatcher_started = False

# Manual requests can mark a key as "next" for a short TTL.
_manual_next_pipeline = {}  # key -> expires_at (unix ts)
_manual_next_lock = threading.Lock()
_MANUAL_NEXT_TTL_SEC = float(os.getenv("MANUAL_NEXT_TTL_SEC", "90"))

def _pipeline_key(office: str, usps: str, district: str) -> str:
    return f"{(office or '').upper()}:{(usps or '').upper()}:{(district or '')}"

def _mark_manual_next_pipeline(key: str) -> None:
    with _manual_next_lock:
        _manual_next_pipeline[key] = time.time() + _MANUAL_NEXT_TTL_SEC

def _consume_manual_next_pipeline(key: str) -> bool:
    now = time.time()
    with _manual_next_lock:
        # cleanup expired
        for k, exp in list(_manual_next_pipeline.items()):
            if exp < now:
                _manual_next_pipeline.pop(k, None)
        if key in _manual_next_pipeline:
            _manual_next_pipeline.pop(key, None)
            return True
    return False

def _enqueue_pipeline_job(*, office: str, usps: str, district: str, front: bool = False, reason: str = "auto") -> str:
    key = _pipeline_key(office, usps, district)
    job = {
        "office": office,
        "usps": usps,
        "district": district,
        "key": key,
        "reason": reason,
        "enqueued_at": time.time(),
    }

    with _pipeline_job_cond:
        if key in _pipeline_job_set:
            # de-dup: optionally move to front
            if front:
                try:
                    for old in list(_pipeline_job_deque):
                        if old.get("key") == key:
                            _pipeline_job_deque.remove(old)
                            break
                except Exception:
                    pass
                _pipeline_job_deque.appendleft(job)
        else:
            if front:
                _pipeline_job_deque.appendleft(job)
            else:
                _pipeline_job_deque.append(job)
            _pipeline_job_set.add(key)

        _pipeline_job_cond.notify()

    return key

def _start_pipeline_dispatcher_once() -> None:
    global _pipeline_dispatcher_started
    if _pipeline_dispatcher_started:
        return
    _pipeline_dispatcher_started = True
    threading.Thread(target=_pipeline_dispatcher_loop, daemon=True).start()

def _pipeline_dispatcher_loop():
    while True:
        # FIFO start order; blocks until a job exists.
        with _pipeline_job_cond:
            while not _pipeline_job_deque:
                _pipeline_job_cond.wait()
            job = _pipeline_job_deque.popleft()
            _pipeline_job_set.discard(job.get("key"))

        # Respect global concurrency, but preserve queue order.
        _pipeline_semaphore.acquire()
        threading.Thread(target=_run_pipeline_job, args=(job,), daemon=True).start()

def _run_pipeline_job(job: dict) -> None:
    try:
        _run_pipeline_job_body(job)
    finally:
        # free slot so next queued pipeline can start
        _pipeline_semaphore.release()

def _run_pipeline_job_body(job: dict) -> None:
    office   = job.get("office")
    usps     = job.get("usps")
    district = job.get("district")
    target_dir = _target_dir_for(office, usps, district)

    cmd = [
        sys.executable, PIPELINE_PATH,
        "--statewide-input", "stats.csv",
        "--stats2-input", "stats2.csv",
        "--office", office,
        "--state", usps,
    ]
    if office == "H":
        cmd += ["--district", district]

    key = job.get("key") or _pipeline_key(office, usps, district)
    lock = _pipeline_locks.setdefault(key, threading.Lock())

    try:
        with lock:
            info = _pipeline_procs.get(key)

            # already running for this target
            if info is not None and info.get("proc") is not None and info["proc"].poll() is None:
                log(f"[PIPELINE][SKIP] already running key={key}", "INFO")
                return

            # log input sizes (helps catch empty/missing stats files)
            try:
                for fn in ("stats.csv", "stats2.csv"):
                    fp = os.path.join(target_dir, fn)
                    log(
                        f"[PIPELINE][INPUT] key={key} {fn} exists={os.path.exists(fp)} bytes="
                        f"{(os.path.getsize(fp) if os.path.exists(fp) else -1)}",
                        "INFO"
                    )
            except Exception as e:
                log(f"[PIPELINE][INPUT] stat error key={key}: {e}", "WARN")

            log(f"[PIPELINE][START] key={key} reason={job.get('reason')} cmd={' '.join(cmd)} (cwd={target_dir})", "INFO")

            # capture stdout/stderr to per-target files
            out_log = os.path.join(target_dir, "pipeline_stdout.log")
            err_log = os.path.join(target_dir, "pipeline_stderr.log")

            env = dict(os.environ)
            env["TMPDIR"] = target_dir  # reduces cross-target tempfile collisions

            out_f = open(out_log, "ab")
            err_f = open(err_log, "ab")

            p = subprocess.Popen(cmd, cwd=target_dir, stdout=out_f, stderr=err_f, env=env)

            _pipeline_procs[key] = {
                "proc": p,
                "start_ts": time.time(),
                "peak_rss_mb": 0.0,
                "last_cpu": 0.0,
                "last_rss_mb": 0.0,
                # keep file handles so we can close them later
                "_out_f": out_f,
                "_err_f": err_f,
                "_err_log": err_log,
            }

        # ---- wait for pipeline ----
        rc = p.wait()

        # close log files
        try:
            with lock:
                info2 = _pipeline_procs.get(key) or {}
                for h in (info2.get("_out_f"), info2.get("_err_f")):
                    try:
                        if h:
                            h.flush()
                            h.close()
                    except Exception:
                        pass
        except Exception:
            pass

        # if failed, dump tail of stderr into main log
        if rc != 0:
            try:
                err_path = os.path.join(target_dir, "pipeline_stderr.log")
                with open(err_path, "rb") as f:
                    tail = f.read()[-8000:].decode("utf-8", "replace")
                log(f"[PIPELINE][FAIL] key={key} rc={rc} tail(stderr)=\n{tail}", "WARN")
            except Exception as e:
                log(f"[PIPELINE][FAIL] key={key} rc={rc} (could not read stderr: {e})", "WARN")

        log(f"[PIPELINE][DONE] key={key} rc={rc}", "INFO")


        info = _pipeline_procs.pop(key, None)
        if info:
            _recent_pipelines.append({
                "key": key,
                "pid": p.pid,
                "peak_rss_mb": round(info.get("peak_rss_mb", 0.0), 1),
                "runtime_sec": int(time.time() - info["start_ts"]),
                "finished_at": time.strftime("%H:%M:%S"),
            })

        # ---- converters (still count toward the 4) ----
        subprocess.run(
            [
                sys.executable,
                "convert_plotdata_folder.py",
                "--input-dir", target_dir,
                "--year", str(STATS_EXPORT_YEAR),
            ],
            cwd=os.path.join(PARENT_DIR, "win_prob"),
            check=False
        )

        subprocess.run(
            [
                sys.executable,
                "aggregate_box_folder.py",
                "--input-dir", target_dir,
                "--state", usps,
                "--year", str(STATS_EXPORT_YEAR),
            ],
            cwd=os.path.join(PARENT_DIR, "win_prob"),
            check=False
        )
        time.sleep(30)
        subprocess.run(
            [
                sys.executable,
                "checker.py",
                "--input-dir", target_dir,
                "--state", usps,
                "--year", str(STATS_EXPORT_YEAR),
            ],
            cwd=os.path.join(PARENT_DIR, "win_prob"),
            check=False
        )

        
        # ---- CLEANUP: safe to remove ephemeral inputs ----
        try:
            stats_path = os.path.join(target_dir, "stats.csv")
            if os.path.exists(stats_path):
                os.remove(stats_path)
                log(f"[PIPELINE][CLEANUP] removed stats.csv key={key}", "INFO")
        except Exception as e:
            log(f"[PIPELINE][CLEANUP] failed to remove stats.csv key={key}: {e}", "WARN")


        log(f"[PIPELINE][DONE] key={key} rc={rc}", "INFO")

    except Exception as e:
        log(f"[PIPELINE][ERROR] failed key={key}: {e}", "WARN")


def _kick_pipeline_after_export(*, office, usps, district):
    #return
    """Enqueue pipeline so start-order is deterministic (FIFO, with manual requests optionally 'next')."""
    if not STATS_EXPORT_RUN_PIPELINE:
        return

    # Ensure dispatcher exists.
    _start_pipeline_dispatcher_once()

    key = _pipeline_key(office, usps, district)
    front = _consume_manual_next_pipeline(key)

    enq_key = _enqueue_pipeline_job(
        office=office,
        usps=usps,
        district=district,
        front=front,
        reason=("manual-next" if front else "auto"),
    )

    if front:
        log(f"[PIPELINE][QUEUE] manual-next enqueued key={enq_key}", "INFO")
    else:
        log(f"[PIPELINE][QUEUE] enqueued key={enq_key}", "INFO")


def parse_targets(spec: str):
    """
    Parse STATS_EXPORT_TARGETS like:
      H:CA:13,P:PA

    Returns:
      [
        {"office":"H","state":"CA","district":"13"},
        {"office":"P","state":"PA","district":None},
      ]
    """
    out = []
    if not spec:
        return out

    for raw in spec.split(","):
        parts = [p.strip().upper() for p in raw.split(":")]
        if len(parts) < 2:
            continue

        office = parts[0]
        state  = parts[1]
        district = parts[2] if len(parts) >= 3 else None

        out.append({
            "office": office,
            "state": state,
            "district": district,
        })

    return out


def _stats_export_loop():
    if not STATS_EXPORT_ENABLED:
        return

    # initial load (so the first log line has the right count)
    active, _base = _pipeline_targets_active()

    interval = max(1.0, float(STATS_EXPORT_INTERVAL or 30.0))
    log(f"Stats export loop enabled: {len(active)} target(s) every {interval}s")

    while True:
        active, _base = _pipeline_targets_active()

        # fallback safety (should be rare)
        if not active:
            active = [{
                "office": STATS_EXPORT_OFFICE,
                "state": STATS_EXPORT_USPS,
                "district": STATS_EXPORT_DISTRICT_NUM if STATS_EXPORT_OFFICE == "H" else None,
                "enabled": True
            }]

        for t in active:
            try:
                _export_stats_once(
                    office=t["office"],
                    usps=t["state"],
                    district=t.get("district"),
                )
            except Exception as e:
                log(f"Stats export error for target {t}: {e}", "WARN")

        time.sleep(interval)



def _start_stats_export_once():
    global _stats_export_started
    if _stats_export_started:
        return
    _stats_export_started = True
    if STATS_EXPORT_ENABLED:
        t = threading.Thread(target=_stats_export_loop, daemon=True)
        t.start()
        
@app.get("/api/fulldata/list")
def api_fulldata_list():
    try:
        os.makedirs(FULLDATA_DIR, exist_ok=True)
        out = []
        for fn in sorted(os.listdir(FULLDATA_DIR)):
            safe = _fulldata_safe_name(fn)
            if not safe:
                continue
            p = os.path.join(FULLDATA_DIR, safe)
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
            out.append({
                "name": safe,
                "size": st.st_size,
                "mtime": __import__("datetime").datetime.fromtimestamp(st.st_mtime).isoformat()
            })
        return jsonify({"ok": True, "dir": FULLDATA_DIR, "files": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/fulldata/download/<path:filename>")
def api_fulldata_download(filename):
    safe = _fulldata_safe_name(filename)
    if not safe:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400
    p = os.path.join(FULLDATA_DIR, safe)
    if not os.path.exists(p):
        return jsonify({"ok": False, "error": "Not found"}), 404
    return send_file(p, as_attachment=True, download_name=safe, mimetype="application/json")

@app.route("/fulldata")
def fulldata_page():
    return send_file("fulldata.html")

@app.delete("/api/fulldata/delete")
def api_fulldata_delete():
    data = request.get_json(force=True, silent=True) or {}
    safe = _fulldata_safe_name(data.get("file") or "")
    if not safe:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400
    p = os.path.join(FULLDATA_DIR, safe)
    if not os.path.exists(p):
        return jsonify({"ok": False, "error": "Not found"}), 404
    try:
        os.unlink(p)
        return jsonify({"ok": True, "deleted": safe})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/fulldata/fetch-save")
def api_fulldata_fetch_save():
    """
    On-demand fetch of /v3/elections/<date>?resultstype=b&level=ru
    Parses with _parse_full_dataset() (same shape your hub uses) and saves to ../FullData/<YEAR>.json
    """
    data = request.get_json(force=True, silent=True) or {}
    mmddyyyy = (data.get("date") or "").strip()
    ymd = _mmddyyyy_to_ymd(mmddyyyy)
    if not ymd:
        return jsonify({"ok": False, "error": "Bad date format (expected mm.dd.yyyy)"}), 400

    year = ymd.split("-", 1)[0]
    out_name = f"{year}.json"
    out_path = os.path.join(FULLDATA_DIR, out_name)

    # Build URL exactly like your example (swap date only)
    url = f"{BASE_URL_E}/{ymd}?resultstype=b&level=ru"

    try:
        r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
        _record_last_hit(url, r.status_code if hasattr(r, "status_code") else 0, "fulldata")
    except requests.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": f"Transport error: {e}"}), 502

    # gentle quota retry (mirrors your full-dataset logic) :contentReference[oaicite:2]{index=2}
    if r.status_code == 403 and _is_quota_403(r):
        time.sleep(7)
        try:
            r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as e:
            return jsonify({"ok": False, "error": f"Transport error after 403-retry: {e}"}), 502

    if r.status_code != 200:
        return jsonify({"ok": False, "error": f"HTTP {r.status_code} from AP"}), 502

    parsed = _parse_full_dataset(r.text)
    if not parsed:
        return jsonify({"ok": False, "error": "Parse failed or empty XML"}), 500

    # atomic write
    try:
        os.makedirs(FULLDATA_DIR, exist_ok=True)
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2)
        os.replace(tmp, out_path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Save failed: {e}"}), 500

    return jsonify({
        "ok": True,
        "date_in": mmddyyyy,
        "date_ymd": ymd,
        "url": url,
        "saved_as": out_name,
        "saved_path": out_path,
        "bytes": len(r.content or b""),
        "combo_count": len(parsed.keys())
    })


@app.route("/pipeline-targets")
def pipeline_targets_page():
    return send_from_directory(app.static_folder, "pipeline-targets.html")

@app.route("/HistoricData/<path:filename>")
def serve_historic(filename):
    return send_from_directory(HISTORIC_DIR, filename)


TOPOJSON_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "topojson"))

@app.route("/topojson/<path:filename>")
def serve_topojson(filename):
    return send_from_directory(TOPOJSON_DIR, filename)
    
# Serve ../fonts as /fonts
FONTS_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "fonts"))

@app.route("/fonts/<path:filename>")
def serve_fonts(filename):
    return send_from_directory(FONTS_DIR, filename)


IMAGES_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "images"))

@app.route("/images/<path:filename>")
def serve_images(filename):
    return send_from_directory(IMAGES_DIR, filename)


MIN_STATE_REFRESH_SEC  = float(os.getenv("MIN_STATE_REFRESH_SEC","15"))  # per-(combo,state) cooldown

ALL_STATES = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","IA","ID","IL","IN","KS","KY",
    "LA","MA","MD","ME","MI","MN","MO","MS","MT","NC","ND","NE","NH","NJ","NM","NV","NY",
    "OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VA","VT","WA","WI","WV","WY","DC","PR"
]

# Optional per-office state filters (defaults target your request)
OFFICE_STATE_FILTERS = {
    "A": [s.strip().upper() for s in os.getenv("A_STATES", "VA").split(",") if s.strip()],
    "M": [s.strip().upper() for s in os.getenv("M_STATES", "NY").split(",") if s.strip()],
    "G": [s.strip().upper() for s in os.getenv("G_STATES", "VA,NJ").split(",") if s.strip()],
    "H": [s.strip().upper() for s in os.getenv("H_STATES", ",".join(ALL_STATES)).split(",") if s.strip()],
}

# NYC borough FIPS (Bronx, Kings, New York, Queens, Richmond)
NYC_BOROUGH_FIPS = {"36005","36047","36061","36081","36085"}


def _states_for_office(office: str):
    of = (office or "").upper()
    filt = OFFICE_STATE_FILTERS.get(of)
    if filt:
        # keep only valid USPS codes
        return [s for s in filt if s in ALL_STATES]
    return ALL_STATES


# Add this small USPS → statefp map near ALL_STATES (once)
USPS_TO_STATEFP = {
    "AL":"01","AK":"02","AZ":"04","AR":"05","CA":"06","CO":"08","CT":"09","DE":"10","FL":"12","GA":"13",
    "HI":"15","ID":"16","IL":"17","IN":"18","IA":"19","KS":"20","KY":"21","LA":"22","ME":"23","MD":"24",
    "MA":"25","MI":"26","MN":"27","MS":"28","MO":"29","MT":"30","NE":"31","NV":"32","NH":"33","NJ":"34",
    "NM":"35","NY":"36","NC":"37","ND":"38","OH":"39","OK":"40","OR":"41","PA":"42","RI":"44","SC":"45",
    "SD":"46","TN":"47","TX":"48","UT":"49","VT":"50","VA":"51","WA":"53","WV":"54","WI":"55","WY":"56",
    "DC":"11","PR":"72"
}


# ---------------- Cache & Stats -----------------
_cache_lock = threading.Lock()
_log_seq = 0
_cache = {
    # cache_by_combo: { "P:G": { "states": { "CA": {...}}, "updated": ts }, ... }
    "cache_by_combo": {},
    "last_cycle_end": 0.0,
    "log": deque(maxlen=4000),
}
_stats = {
    "upstream_calls": 0,
    "upstream_bytes": 0,
    "errors": 0,
    # per_combo_state: { "P:G|CA": {"last_fetch": ts, "ok": n, "err": n}, ... }
    "per_combo_state": {},
}
_inflight = set()

def _now_iso(): return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def log(msg, lvl="INFO"):
    global _log_seq
    with _cache_lock:
        _log_seq += 1
        _cache["log"].append({"seq": _log_seq, "ts": _now_iso(), "lvl": lvl, "msg": str(msg)})

# -------------- Snapshot (optional) -------------
def _snapshot_save():
    try:
        with _cache_lock:
            data = {
                "cache_by_combo": _cache["cache_by_combo"],
                "last_cycle_end": _cache["last_cycle_end"],
            }
        with open(CACHE_SNAPSHOT_PATH,"w") as f: json.dump(data,f)
        log(f"Saved snapshot to {CACHE_SNAPSHOT_PATH}")
    except Exception as e:
        log(f"Snapshot save error: {e}","WARN")

def _snapshot_load():
    try:
        if os.path.exists(CACHE_SNAPSHOT_PATH):
            with open(CACHE_SNAPSHOT_PATH,"r") as f: data=json.load(f)
            with _cache_lock:
                _cache["cache_by_combo"] = data.get("cache_by_combo",{})
                _cache["last_cycle_end"] = data.get("last_cycle_end",0.0)
                # merge stats shallowly
                for k,v in data.get("_stats",{}).items():
                    _stats[k] = v
            log(f"Loaded snapshot from {CACHE_SNAPSHOT_PATH}")
    except Exception as e:
        log(f"Snapshot load error: {e}","WARN")
        
# ================= PRIMARY CACHE (separate file) =================
_primary_cache_lock = threading.Lock()
_primary_cache = {
    "cache_by_combo": {},   # e.g., {"P:D": {...}, "H:R": {...}}
    "last_cycle_end": 0.0,
}

def _primary_snapshot_save():
    try:
        with _primary_cache_lock:
            data = {
                "cache_by_combo": _primary_cache["cache_by_combo"],
                "last_cycle_end": _primary_cache["last_cycle_end"],
            }
        with open(PRIMARY_SNAPSHOT_PATH, "w") as f:
            json.dump(data, f)
        log(f"Saved primary snapshot to {PRIMARY_SNAPSHOT_PATH}")
    except Exception as e:
        log(f"Primary snapshot save error: {e}", "WARN")

def _primary_snapshot_load():
    try:
        if os.path.exists(PRIMARY_SNAPSHOT_PATH):
            with open(PRIMARY_SNAPSHOT_PATH, "r") as f:
                data = json.load(f)
            with _primary_cache_lock:
                _primary_cache["cache_by_combo"] = data.get("cache_by_combo", {})
                _primary_cache["last_cycle_end"] = data.get("last_cycle_end", 0.0)
            log(f"Loaded primary snapshot from {PRIMARY_SNAPSHOT_PATH}")
    except Exception as e:
        log(f"Primary snapshot load error: {e}", "WARN")


# -------------- AP-style URL builder ----------------
def _build_url(state: str, office: str, race_type: str) -> str:
    office_u = (office or "").upper()
    base = BASE_URL_E if office_u in ("G","A","M","S") else BASE_URL_D
    rt = interpret_race_type(race_type)
    raw = rt["raw"]  # 'G','D','R','S',...
    if raw == "S":
        date = SPECIAL_DATE
    else:
        date = GENERAL_DATE if rt["mode"] == "general" else PRIMARY_DATE
    # now includes &level=ru (or whatever LEVEL_PARAM is)
    return f"{base}/{date}?statepostal={state}&officeId={office_u}&raceTypeId={rt['raw']}&level={LEVEL_PARAM}&resultstype=t"

def _looks_like_xml(s: str) -> bool:
    if not isinstance(s, str):
        return False
    head = s.lstrip()[:200].lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        return False
    return head.startswith("<")
    
# --- Party normalization (canonical short tags) ---
def _norm_party(s: str) -> str:
    if not s:
        return "IND"
    u = str(s).strip().upper()
    aliases = {
        # Democrats
        "DEM": "DEM", "DEMOCRAT": "DEM", "DEMOCRATIC": "DEM", "D": "DEM",
        # Republicans
        "GOP": "REP", "REPUBLICAN": "REP", "R": "REP", "REP": "REP",
        # Independents / Other
        "INDEPENDENT": "IND", "IND": "IND", "OTHER": "IND", "OTH": "IND",
        "NP": "NP", "NPA": "NP", "NONPARTISAN": "NP", "NO PARTY": "NP",
        # Common minors
        "CON": "CON", "CONSERVATIVE": "CON",
        "LIB": "LIB", "LIBERTARIAN": "LIB",
        "GRN": "GRN", "GREEN": "GRN",
        # Add more aliases as needed...
    }
    return aliases.get(u, u)

    
def _is_quota_403(resp) -> bool:
    """
    Heuristic for AP/gateway 'per-minute quota exceeded' responses.
    """
    try:
        if resp.status_code != 403:
            return False
        body = (resp.text or "").lower()
        if "per-minute quota exceeded" in body or "quota exceeded" in body:
            return True
        # Some gateways hint via ratelimit headers
        for k in ("x-ratelimit-remaining", "ratelimit-remaining"):
            if k in resp.headers and str(resp.headers.get(k)).strip() == "0":
                return True
    except Exception:
        pass
    return False




# -------------- Parsers ----------------
def _parse_state_ru(xml_text: str, usps: str, office: str, race_type: str) -> dict:
    out = {"counties": {}, "percent_in": None}
    if not _looks_like_xml(xml_text):
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    # --- Choose the correct <Race> block ---
    races = root.findall(".//Race")
    race = None
    if (office or "").upper() == "M" and (usps or "").upper() == "NY":
        # Prefer SeatName="New York City"
        for r in races:
            if (r.attrib.get("SeatName","") or "").strip().lower().startswith("new york city"):
                race = r
                break
    if race is None:
        race = races[0] if races else None

    # --- Race-level status & percent in (prefer EEVP) ---
    state_status = (race.attrib.get("RaceCallStatus") if race is not None else None) or "No Decision"
    percent = race.attrib.get("EEVP") if race is not None else None

    # Pull state RU (relative to chosen Race); fall back to document if missing
    state_ru = race.find("./ReportingUnit[@Level='state']") if race is not None else None
    if state_ru is None:
        state_ru = root.find(".//ReportingUnit[@Level='state']") or root.find(".//ReportingUnit[@ReportingUnitLevel='1']")
    if not percent and state_ru is not None:
        percent = state_ru.attrib.get("EEVP") or None
        if not percent:
            prec = state_ru.find("./Precincts")
            if prec is not None:
                percent = prec.attrib.get("ReportingPct")
    out["percent_in"] = percent

    # --- Winner (from the chosen Race's state RU only) ---
    # --- Winner (from the chosen Race's state RU only) ---
    winner_payload = None
    if state_status == "Called" and state_ru is not None:
        w_node = next((c for c in state_ru.findall("./Candidate") if (c.attrib.get("Winner") or "").upper() == "X"), None)
        if w_node is not None:
            first = (w_node.attrib.get("First") or "").strip()
            last  = (w_node.attrib.get("Last") or "").strip()
            party = _norm_party(w_node.attrib.get("Party"))
            name  = (first + " " + last).strip()
            winner_payload = {"name": name or None, "party": party or None}
    out["race_call"] = {"status": state_status, "winner": winner_payload, "source": "api"}

    # NEW: read the state topline straight from the Level="state" RU
    state_topline, state_total = [], 0
    if state_ru is not None:
        for c in state_ru.findall("./Candidate"):
            first = (c.attrib.get("First") or "").strip()
            last  = (c.attrib.get("Last") or "").strip()
            party = _norm_party(c.attrib.get("Party"))
            votes = int(c.attrib.get("VoteCount") or "0")
            state_total += votes
            state_topline.append({"name": (first + " " + last).strip(), "party": party, "votes": votes})
    out["state_topline"] = state_topline
    out["state_total"]   = state_total
    # === NEW ENGLAND OVERRIDE: statewide = sum of all subunits; DO NOT STORE SUBUNITS OR COUNTIES ===
    # Works for both general and primaries. For ME/NH/VT/MA/CT/RI, we keep only the statewide topline
    # we just computed above and skip building counties/subunits. (Avoids double-counting/missing units.)
    if (usps or "").upper() in NEW_ENGLAND:
        # Ensure counties key exists but stays empty for these states
        out.setdefault("counties", {})
        # Optionally, set percent_in for the state if available from the state RU
        if state_ru is not None:
            state_percent = state_ru.attrib.get("EEVP")
            if not state_percent:
                prec = state_ru.find("./Precincts")
                if prec is not None:
                    state_percent = prec.attrib.get("ReportingPct")
            # Put a single percent field on the state blob (UI can read this if desired)
            out["percent_in"] = state_percent
        # Early return: skip the county/subunit aggregation below
        return out


    # --- County RUs (scope to chosen Race; fallback if needed) ---
    # --- County RUs (scope to chosen Race; fallback if needed) ---
    # --- County RUs (scope to chosen Race; fallback if needed) ---
    # --- County RUs (scope to chosen Race; fallback if needed) ---
    rus = race.findall("./ReportingUnit[@Level='subunit']") if race is not None else []
    if not rus:
        rus = race.findall("./ReportingUnit[@ReportingUnitLevel='2']") if race is not None else []
    if not rus:
        rus = [ru for ru in root.findall(".//ReportingUnit") if ru.attrib.get("FIPSCode")]

    # If still nothing, just return the state topline we already built
    if not rus:
        return out

    # --- First pass: FIPS values that have a county RU (ReportingUnitLevel == "2") ---
    fips_with_level2 = set()
    for ru in rus:
        lvl = (ru.attrib.get("ReportingUnitLevel") or "").strip()
        if lvl == "2":
            f = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip()
            if f and f.isdigit() and len(f) < 5:
                f = f.zfill(5)
            if f and f != "00000":
                fips_with_level2.add(f)

    # per-county accumulators
    county_aggs = {}        # fips -> {"name": <first seen county name>, "cands": {(name,party): votes}, "percent": best_percent}
    county_subs = {}        # fips -> { ru_id: {name, percent_in, candidates[]} }

    for ru in rus:
        fips = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip()
        if fips and fips.isdigit() and len(fips) < 5:
            fips = fips.zfill(5)
        if not fips or fips == "00000":
            continue

        # NYC filter unchanged
        if (office or "").upper() == "M" and (usps or "").upper() == "NY":
            if fips not in NYC_BOROUGH_FIPS:
                continue

        ru_level = (ru.attrib.get("ReportingUnitLevel") or "").strip()

        ru_id   = (ru.attrib.get("ID") or ru.attrib.get("ReportingUnitID") or "").strip()
        ru_name = (ru.attrib.get("Name") or f"FIPS {fips}").strip()

        # percent at RU level
        ru_percent = ru.attrib.get("EEVP")
        if not ru_percent:
            prec = ru.find("./Precincts")
            if prec is not None:
                ru_percent = prec.attrib.get("ReportingPct")

        # collect candidate votes for this RU
        ru_cands, ru_total = [], 0
        for c in ru.findall("./Candidate"):
            first = (c.attrib.get("First") or "").strip()
            last  = (c.attrib.get("Last") or "").strip()
            party = _norm_party(c.attrib.get("Party"))
            raw_v = (c.attrib.get("VoteCount") or "0")
            try:
                votes = int(raw_v)
            except (ValueError, TypeError):
                prev = _get_prev_vote_county(usps, fips, party, office, race_type)
                votes = prev if prev is not None else 0
            ru_total += votes
            ru_cands.append({"name": (first + " " + last).strip(), "party": party, "votes": votes})

        # init county holders
        ca = county_aggs.setdefault(fips, {"name": None, "cands": {}, "percent": None})
        cs = county_subs.setdefault(fips, {})

        # remember a readable name for the county (first seen RU name that looks county-like, else keep first RU)
        if ca["name"] is None or ("County" in ru_name and "County" not in (ca["name"] or "")):
            ca["name"] = ru_name

        # Decide whether this RU should contribute to the county aggregate:
        # - if there IS a level-2 (county) RU for this FIPS, only count those
        # - if there is NO level-2, count everything (like before)
        should_aggregate = (ru_level == "2") or (fips not in fips_with_level2)

        if should_aggregate:
            if ru_percent and (ru_level == "2" or ca["percent"] is None):
                ca["percent"] = ru_percent

            for rc in ru_cands:
                key = (rc["name"], rc["party"])
                ca["cands"][key] = ca["cands"].get(key, 0) + rc["votes"]

        # keep the finer-grain RU intact so nothing is lost
        if ru_id:
            cs[ru_id] = {
                "name": ru_name,
                "percent_in": ru_percent,
                "candidates": ru_cands,
                "total": ru_total
            }

    # write county aggregates + subunit breakdowns to out["counties"]
    for fips, agg in county_aggs.items():
        # compose candidates list
        cands = [{"name": n, "party": p, "votes": v} for (n, p), v in agg["cands"].items()]
        # optional: stable sort by votes desc
        cands.sort(key=lambda x: x["votes"], reverse=True)

        out["counties"][fips] = {
            "state": usps,
            "fips": fips,
            "name": agg["name"] or f"FIPS {fips}",
            "candidates": cands,
            "total": sum(c["votes"] for c in cands),
            "percent_in": agg["percent"],
            "subunits": county_subs.get(fips)
        }

    return out


    # First pass: FIPS values that have a county RU (ReportingUnitLevel == "2")
    fips_with_level2 = set()
    for ru in rus:
        lvl = (ru.attrib.get("ReportingUnitLevel") or "").strip()
        if lvl == "2":
            f = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip().zfill(5)
            if f and f != "00000":
                fips_with_level2.add(f)

    # per-county accumulators
    county_aggs = {}        # fips -> {"name": <first seen county name>, "cands": {(name,party): votes}, "percent": best_percent}
    county_subs = {}        # fips -> { ru_id: {name, percent_in, candidates[]} }

    for ru in rus:
        fips = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip().zfill(5)
        if not fips or fips == "00000":
            continue

        # NYC filter unchanged
        if (office or "").upper() == "M" and (usps or "").upper() == "NY":
            if fips not in NYC_BOROUGH_FIPS:
                continue

        ru_level = (ru.attrib.get("ReportingUnitLevel") or "").strip()

        ru_id   = (ru.attrib.get("ID") or ru.attrib.get("ReportingUnitID") or "").strip()
        ru_name = (ru.attrib.get("Name") or f"FIPS {fips}").strip()

        # percent at RU level
        ru_percent = ru.attrib.get("EEVP")
        if not ru_percent:
            prec = ru.find("./Precincts")
            if prec is not None:
                ru_percent = prec.attrib.get("ReportingPct")

        # collect candidate votes for this RU
        ru_cands, ru_total = [], 0
        for c in ru.findall("./Candidate"):
            first = (c.attrib.get("First") or "").strip()
            last  = (c.attrib.get("Last") or "").strip()
            party = _norm_party(c.attrib.get("Party"))
            raw_v = (c.attrib.get("VoteCount") or "0")
            try:
                votes = int(raw_v)
            except (ValueError, TypeError):
                prev = _get_prev_vote_county(usps, fips, party, office, race_type)
                votes = prev if prev is not None else 0
            ru_total += votes
            ru_cands.append({"name": (first + " " + last).strip(), "party": party, "votes": votes})

        # init county holders
        ca = county_aggs.setdefault(fips, {"name": None, "cands": {}, "percent": None})
        cs = county_subs.setdefault(fips, {})

        # remember a readable name for the county (first seen RU name that looks county-like, else keep first RU)
        if ca["name"] is None or ("County" in ru_name and "County" not in (ca["name"] or "")):
            ca["name"] = ru_name

        # Decide whether this RU should contribute to the county aggregate
        should_aggregate = (ru_level == "2") or (fips not in fips_with_level2)

        # aggregate by (candidate name, party) only if we should aggregate this RU
        if should_aggregate:
            if ru_percent and (ru_level == "2" or ca["percent"] is None):
                ca["percent"] = ru_percent

            for rc in ru_cands:
                key = (rc["name"], rc["party"])
                ca["cands"][key] = ca["cands"].get(key, 0) + rc["votes"]

        # keep the finer-grain RU intact so nothing is lost
        if ru_id:
            cs[ru_id] = {
                "name": ru_name,
                "percent_in": ru_percent,
                "candidates": ru_cands,
                "total": ru_total
            }

    # write county aggregates + subunit breakdowns to out["counties"]
    for fips, agg in county_aggs.items():
        # compose candidates list
        cands = [{"name": n, "party": p, "votes": v} for (n, p), v in agg["cands"].items()]
        # optional: stable sort by votes desc
        cands.sort(key=lambda x: x["votes"], reverse=True)

        out["counties"][fips] = {
            "state": usps,
            "fips": fips,
            "name": agg["name"] or f"FIPS {fips}",
            "candidates": cands,
            "total": sum(c["votes"] for c in cands),
            "percent_in": agg["percent"],      # leave as None or derive if you prefer a weighted pct
            "subunits": county_subs.get(fips)  # every RU preserved here
        }

    return out

def _parse_house_ru(xml_text: str, usps: str, office: str, race_type: str) -> dict:
    out = {"districts": {}, "percent_in": None}
    if not _looks_like_xml(xml_text):
        log(f"Non-XML upstream payload for {usps} {office}:{race_type}; keeping last good.", "WARN")
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    # --- v3 elections shape: many <Race> nodes (one per district) + county subunits inside each race
    races = root.findall(".//Race")
    if races:
        for race in races:
            off = (race.attrib.get("OfficeID") or "").upper()
            if off and off != "H":
                continue

            state_status = (race.attrib.get("RaceCallStatus") or "No Decision").strip()
            percent = race.attrib.get("EEVP")

            state_ru = race.find("./ReportingUnit[@Level='state']") or race.find("./ReportingUnit[@ReportingUnitLevel='1']")
            if state_ru is None:
                continue

            if not percent:
                percent = state_ru.attrib.get("EEVP")
            if not percent:
                prec = state_ru.find("./Precincts")
                if prec is not None:
                    percent = prec.attrib.get("ReportingPct")

            seat_num = str(race.attrib.get("SeatNum") or race.attrib.get("SeatNumber") or "").strip().zfill(2)
            if not seat_num:
                seat_num = (state_ru.attrib.get("District") or "").strip().zfill(2)
            if not seat_num:
                continue

            statefp = USPS_TO_STATEFP.get(usps, "")
            did = (statefp + seat_num) if statefp else seat_num

            # HARD LIMIT: for House Special (H:S*), retain only TX-18
            if office.upper() == "H" and interpret_race_type(race_type)["raw"].upper().startswith("S"):
                if not (usps == "TX" and seat_num == "18"):
                    continue

            # district candidates (from state RU)
            cands, total = [], 0
            for c in state_ru.findall("./Candidate"):
                first = (c.attrib.get("First") or "").strip()
                last  = (c.attrib.get("Last")  or "").strip()
                party = _norm_party(c.attrib.get("Party"))
                raw = c.attrib.get("VoteCount") or "0"
                try:
                    votes = int(raw)
                except Exception:
                    votes = 0
                total += votes
                cands.append({
                    "name": (first + " " + last).strip(),
                    "party": party,
                    "votes": votes,
                    "winner": (c.attrib.get("Winner","").upper() == "X")
                })

            winner_payload = None
            if state_status == "Called":
                w_node = next((c for c in state_ru.findall("./Candidate")
                               if (c.attrib.get("Winner") or "").upper() == "X"), None)
                if w_node is not None:
                    nm = ((w_node.attrib.get("First") or "").strip() + " " + (w_node.attrib.get("Last") or "").strip()).strip()
                    winner_payload = {"name": nm or None, "party": _norm_party(w_node.attrib.get("Party")) or None}

            district_obj = {
                "state": usps,
                "district_id": did,
                "district_num": seat_num,
                "name": race.attrib.get("SeatName") or f"District {seat_num}",
                "candidates": cands,
                "total": total,
                "percent_in": percent,
                "race_call": {"status": state_status, "winner": winner_payload}
            }

            # --- NEW: county-level results inside this district race
            county_aggs = {}
            subunits = {}
            fips_with_level2 = set()

            for ru in race.findall("./ReportingUnit"):
                ru_level = ru.attrib.get("ReportingUnitLevel") or ru.attrib.get("Level")
                is_level2 = (ru_level == "2") or (str(ru_level).lower() == "subunit")
                fips = (ru.attrib.get("FIPSCode") or "").strip()
                if not fips:
                    continue

                ru_id = (ru.attrib.get("ID") or "").strip()
                ru_name = (ru.attrib.get("Name") or "").strip()

                ru_percent = ru.attrib.get("EEVP")
                if not ru_percent:
                    prec = ru.find("./Precincts")
                    if prec is not None:
                        ru_percent = prec.attrib.get("ReportingPct")

                if is_level2:
                    fips_with_level2.add(fips)

                ru_cands = []
                for c in ru.findall("./Candidate"):
                    party = _norm_party(c.attrib.get("Party"))
                    first = (c.attrib.get("First") or "").strip()
                    last  = (c.attrib.get("Last")  or "").strip()
                    nm = (first + " " + last).strip()
                    raw = c.attrib.get("VoteCount") or "0"
                    try:
                        votes = int(raw)
                    except Exception:
                        prev = _get_prev_vote_house_county(usps, did, fips, party, office, race_type)
                        votes = int(prev) if prev is not None else 0
                    ru_cands.append({"name": nm, "party": party, "votes": votes})

                subunits[ru_id or f"{fips}:{ru_name}"] = {
                    "id": ru_id or None,
                    "fips": fips,
                    "name": ru_name,
                    "level": str(ru_level) if ru_level is not None else None,
                    "percent_in": ru_percent,
                    "candidates": ru_cands
                }

                should_aggregate = is_level2 or (fips not in fips_with_level2)
                if should_aggregate:
                    ca = county_aggs.setdefault(fips, {"name": ru_name, "cands": {}, "percent": None})
                    if ru_percent and (is_level2 or ca["percent"] is None):
                        ca["percent"] = ru_percent
                    if not ca.get("name"):
                        ca["name"] = ru_name
                    for rc in ru_cands:
                        key = (rc["name"], rc["party"])
                        ca["cands"][key] = ca["cands"].get(key, 0) + rc["votes"]

            counties = {}
            for fips, agg in county_aggs.items():
                cand_list = []
                total_votes = 0
                for (nm, party), votes in agg["cands"].items():
                    cand_list.append({"name": nm, "party": party, "votes": votes})
                    total_votes += votes
                counties[fips] = {
                    "state": usps,
                    "fips": fips,
                    "name": agg.get("name") or None,
                    "candidates": cand_list,
                    "total": total_votes,
                    "percent_in": agg.get("percent"),
                    "subunits": subunits
                }

            if counties:
                district_obj["counties"] = counties

            out["districts"][did] = district_obj

        return out

    # --- legacy /v2/districts shape fallback (kept as-is)
    rus = root.findall(".//ReportingUnit")
    if not rus:
        return None

    out["percent_in"] = root.attrib.get("PercentIn")

    for ru in rus:
        if (ru.attrib.get("StatePostal") or "").upper() != usps:
            continue
        dnum = (ru.attrib.get("DistrictNumber") or ru.attrib.get("District") or "").strip()
        did = (ru.attrib.get("DistrictId") or "").strip()
        if not did:
            statefp = USPS_TO_STATEFP.get(usps, "")
            d2 = (dnum or "").zfill(2)
            did = (statefp + d2) if statefp else d2

        ru_percent = ru.attrib.get("PercentIn")
        ru_status  = (ru.attrib.get("RaceCallStatus") or "No Decision").strip()
        name = (ru.attrib.get("Name") or "").strip()

        cands, total = [], 0
        winner_name, winner_party = (None, None)

        for c in ru.findall("./Candidate"):
            first = (c.attrib.get("First") or "").strip()
            last  = (c.attrib.get("Last")  or "").strip()
            party = _norm_party(c.attrib.get("Party"))
            raw = c.attrib.get("VoteCount") or "0"
            try:
                votes = int(raw)
            except Exception:
                prev = _get_prev_vote_district(usps, did, party, office, race_type)
                votes = int(prev) if prev is not None else 0
            total += votes

            is_winner = (c.attrib.get("Winner","").upper() == "X")
            if is_winner:
                nm = (first + " " + last).strip()
                winner_name, winner_party = (nm or None), (party or None)

            cands.append({
                "name": (first + " " + last).strip(),
                "party": party,
                "votes": votes,
                "winner": bool(is_winner)
            })

        statefp = USPS_TO_STATEFP.get(usps, "")
        d2 = (dnum or "").zfill(2)
        canonical_did = (did or (statefp + d2) or dnum)

        if office.upper() == "H" and interpret_race_type(race_type)["raw"].upper().startswith("S"):
            if not (usps == "TX" and d2 == "18"):
                continue

        out["districts"][canonical_did] = {
            "state": usps,
            "district_id": canonical_did,
            "district_num": dnum,
            "name": name,
            "candidates": cands,
            "total": total,
            "percent_in": ru_percent,
            "race_call": {
                "status": ru_status,
                "winner": None if not winner_name and not winner_party else {
                    "name":  winner_name,
                    "party": winner_party
                }
            }
        }
    return out

def _parse_full_dataset(xml_text: str):
    """
    Parse a nation-wide /v3/elections/<date> XML into the cache_by_combo shape:
      { "<OFFICE>:<TypeID>": { "states": { "CA": {...}, ... }, "updated": ts } }
    Supports OfficeID in P,S,G,A,M (state+county) and H (district).
    """
    out = {}  # combo -> {"states": {USPS: blob}, "updated": ts}

    if not _looks_like_xml(xml_text):
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    # iterate over all races in the file
    for race in root.findall(".//Race"):
        office = (race.attrib.get("OfficeID") or "").upper()   # P, S, G, A, M, H, ...
        race_type = (race.attrib.get("TypeID") or race.attrib.get("TypeId") or "G").upper()
        combo = _combo_key(office, race_type)
        bucket = out.setdefault(combo, {"states": {}, "updated": time.time()})

        # Prefer the state-level ReportingUnit for USPS, percent, and winner
        state_ru = race.find("./ReportingUnit[@Level='state']") \
                   or race.find("./ReportingUnit[@ReportingUnitLevel='1']")
        if state_ru is None:
            # If no clear state RU, skip this race
            continue

        usps = (state_ru.attrib.get("StatePostal") or "").upper()
        if not usps:
            continue
        
                # --- choose the best race per (office, race_type, state): keep only the one with highest state RU votes
        # --- choose the best race per (office, race_type, state) ---
        if office != "H":
            this_total_for_choice = _sum_state_ru_votes(state_ru)

            st_existing = bucket["states"].get(usps)
            if st_existing is not None:
                prev_best = int(st_existing.get("_best_state_total") or -1)
                # If this race has fewer or equal state votes than what we already kept, skip it
                if this_total_for_choice <= prev_best:
                    continue

            # Prefer this race: re-init statewide blob and clear prior county payloads
            st = bucket["states"].setdefault(usps, {
                "updated": time.time(),
                "office": office,
                "percent_in": None,
            })
            # For statewide offices, clearing previous subunits is correct
            st.pop("counties", None)
            st.pop("districts", None)
            st["_best_state_total"] = this_total_for_choice  # internal marker
        else:
            # House: keep all districts — never clear previously parsed ones
            st = bucket["states"].setdefault(usps, {
                "updated": time.time(),
                "office": office,
                "percent_in": None,
            })
            # Note: do NOT touch st["districts"] here; we'll add to it below


        
        
        
        # --- shared race-level bits
        state_status = (race.attrib.get("RaceCallStatus") or "No Decision").strip()
        percent = race.attrib.get("EEVP") or state_ru.attrib.get("EEVP")
        if not percent:
            prec = state_ru.find("./Precincts")
            if prec is not None:
                percent = prec.attrib.get("ReportingPct")

        # Winner from the state RU
        winner_payload = None
        if state_status == "Called":
            w_node = next((c for c in state_ru.findall("./Candidate")
                           if (c.attrib.get("Winner") or "").upper() == "X"), None)
            if w_node is not None:
                first = (w_node.attrib.get("First") or "").strip()
                last  = (w_node.attrib.get("Last")  or "").strip()
                party = _norm_party(w_node.attrib.get("Party"))
                nm    = (first + " " + last).strip()
                winner_payload = {"name": nm or None, "party": party or None}

        # Ensure a state blob for this office/type
        st = bucket["states"].setdefault(usps, {
            "updated": time.time(),
            "office": office,
            "percent_in": None,
        })
        st["percent_in"] = percent or st.get("percent_in")
        # --- Duplicate any W (runoff/special-coded) as S bucket as well ---


        if office == "H":
            # ---- House: each <Race> is a single district for a state
            # Canonical district id from USPS + SeatNum
            seat_num = str(race.attrib.get("SeatNum") or race.attrib.get("SeatNumber") or "").strip().zfill(2)
            if not seat_num:
                # fallback: District on RU (rare)
                seat_num = (state_ru.attrib.get("District") or "").strip().zfill(2)
            if not seat_num:
                # cannot place district
                continue

            statefp = USPS_TO_STATEFP.get(usps, "")
            did = (statefp + seat_num) if statefp else seat_num

            # Collect candidates from the state RU
            cands, total = [], 0
            for c in state_ru.findall("./Candidate"):
                first = (c.attrib.get("First") or "").strip()
                last  = (c.attrib.get("Last")  or "").strip()
                party = _norm_party(c.attrib.get("Party"))
                try:
                    votes = int(c.attrib.get("VoteCount") or "0")
                except Exception:
                    votes = 0
                total += votes
                cands.append({
                    "name": (first + " " + last).strip(),
                    "party": party,
                    "votes": votes,
                    "winner": (c.attrib.get("Winner","").upper() == "X")
                })

            st.setdefault("districts", {})
            st["districts"][did] = {
                "state": usps,
                "district_id": did,
                "district_num": seat_num,
                "name": race.attrib.get("SeatName") or f"District {seat_num}",
                "candidates": cands,
                "total": total,
                "percent_in": percent,
                "race_call": {
                    "status": state_status,
                    "winner": winner_payload
                }
            }
            # --- NEW: store county/subunit data INSIDE this House district (if present) ---
            district = st["districts"][did]
            district.setdefault("counties", {})

            # Pull RUs scoped to this district Race (prefer Level='subunit' / ReportingUnitLevel='2')
            rus = race.findall("./ReportingUnit[@Level='subunit']") \
                or race.findall("./ReportingUnit[@ReportingUnitLevel='2']")
            if not rus:
                # Fallback: any RU with a FIPSCode under this race
                rus = [ru for ru in race.findall("./ReportingUnit")
                       if ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS")]

            if rus:
                # First pass: FIPS values that have a county RU (ReportingUnitLevel == "2")
                fips_with_level2 = set()
                for ru in rus:
                    lvl = (ru.attrib.get("ReportingUnitLevel") or "").strip()
                    if lvl == "2":
                        f = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip()
                        if f and f.isdigit() and len(f) < 5:
                            f = f.zfill(5)
                        if f and f != "00000":
                            fips_with_level2.add(f)

                county_aggs = {}  # fips -> {"name": best_name, "cands": {(name,party): votes}, "percent": maybe}
                county_subs = {}  # fips -> { ru_id: {name, percent_in, candidates[], total} }

                for ru in rus:
                    fips = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip()
                    if fips and fips.isdigit() and len(fips) < 5:
                        fips = fips.zfill(5)
                    if not fips or fips == "00000":
                        continue

                    ru_level = (ru.attrib.get("ReportingUnitLevel") or "").strip()
                    ru_id   = (ru.attrib.get("ID") or ru.attrib.get("ReportingUnitID") or "").strip()
                    ru_name = (ru.attrib.get("Name") or f"FIPS {fips}").strip()

                    # percent at RU level
                    ru_percent = ru.attrib.get("EEVP")
                    if not ru_percent:
                        prec = ru.find("./Precincts")
                        if prec is not None:
                            ru_percent = prec.attrib.get("ReportingPct")

                    # candidates for this RU
                    ru_cands, ru_total = [], 0
                    for c in ru.findall("./Candidate"):
                        first = (c.attrib.get("First") or "").strip()
                        last  = (c.attrib.get("Last")  or "").strip()
                        party = _norm_party(c.attrib.get("Party"))
                        try:
                            votes = int(c.attrib.get("VoteCount") or "0")
                        except Exception:
                            votes = 0
                        ru_total += votes
                        ru_cands.append({"name": (first + " " + last).strip(), "party": party, "votes": votes})

                    ca = county_aggs.setdefault(fips, {"name": None, "cands": {}, "percent": None})
                    cs = county_subs.setdefault(fips, {})

                    # prefer a county-looking label if we ever see one; otherwise keep first RU name
                    if ca["name"] is None or ("County" in ru_name and "County" not in (ca["name"] or "")):
                        ca["name"] = ru_name

                    # track best %in we see for this FIPS
                    if ru_percent is not None:
                        try:
                            p = float(ru_percent)
                            ca["percent"] = p if ca["percent"] is None else max(ca["percent"], p)
                        except Exception:
                            pass

                    # aggregate if:
                    #   - this RU is a county (level 2), OR
                    #   - there is no county RU at all for this FIPS
                    should_aggregate = (ru_level == "2") or (fips not in fips_with_level2)
                    if should_aggregate:
                        for rc in ru_cands:
                            key = (rc["name"], rc["party"])
                            ca["cands"][key] = ca["cands"].get(key, 0) + rc["votes"]

                    # keep fine-grain RU under subunits
                    if ru_id:
                        cs[ru_id] = {
                            "name": ru_name,
                            "percent_in": ru_percent,
                            "candidates": ru_cands,
                            "total": ru_total
                        }

                # write county aggregates + subunit breakdowns into the DISTRICT (not the state)
                for fips, agg in county_aggs.items():
                    cands = [{"name": n, "party": p, "votes": v}
                             for (n, p), v in agg["cands"].items()]
                    cands.sort(key=lambda x: x["votes"], reverse=True)

                    district["counties"][fips] = {
                        "state": usps,
                        "fips": fips,
                        "name": agg["name"] or f"FIPS {fips}",
                        "candidates": cands,
                        "total": sum(c["votes"] for c in cands),
                        "percent_in": agg.get("percent"),
                        "subunits": county_subs.get(fips, {})
                    }

            # --- Duplicate any W (runoff/special-coded) as S bucket as well ---
            if (race_type or "").upper() == "W":
                dup_key = _combo_key(office, "S")
                s_bucket = out.setdefault(dup_key, {"states": {}, "updated": time.time()})
                # Deep-copy the state blob so we don't share references
                s_bucket["states"][usps] = json.loads(json.dumps(st))
                s_bucket["updated"] = time.time()


            # House overrides (by state) after we have at least one district
            _apply_house_overrides(usps, st.get("districts") or {}, office, race_type)
            
            # If Louisiana House is TypeID 'L' (LA jungle), also store into H:G
            if office == "H" and usps == "LA" and race_type in ("L", "NP", "X"):
                dup_key = _combo_key("H", "G")
                h_g_bucket = out.setdefault(dup_key, {"states": {}, "updated": time.time()})
                # Deep copy the LA state blob so we don't share references
                h_g_bucket["states"]["LA"] = json.loads(json.dumps(st))
                h_g_bucket["updated"] = time.time()


        else:
            # ---- Statewide offices (P, S, G, A, M ...): New England = statewide sum of subunits; NO counties ----
            st["race_call"] = {
                "status": state_status,
                "winner": winner_payload,
                "source": "api"
            }

            # Default topline from state RU
            state_topline, state_total = [], 0
            for c in state_ru.findall("./Candidate"):
                first = (c.attrib.get("First") or "").strip()
                last  = (c.attrib.get("Last")  or "").strip()
                party = _norm_party(c.attrib.get("Party"))
                try:
                    votes = int(c.attrib.get("VoteCount") or "0")
                except Exception:
                    votes = 0
                state_total += votes
                state_topline.append({"name": (first + " " + last).strip(), "party": party, "votes": votes})

            # === NEW ENGLAND override: recompute statewide topline from subunits and SKIP counties ===
# === NEW ENGLAND: use API state RU topline exactly; do NOT aggregate subunits/counties ===
            if usps in NEW_ENGLAND:
                # (state_topline/state_total were just built above from the state RU)
                st["state_topline"] = state_topline
                st["state_total"]   = state_total

                # Carry state percent_in from race/state RU if present
                if not st.get("percent_in"):
                    if state_ru is not None:
                        pct = state_ru.attrib.get("EEVP")
                        if not pct:
                            prec = state_ru.find("./Precincts")
                            if prec is not None:
                                pct = prec.attrib.get("ReportingPct")
                        if pct:
                            st["percent_in"] = pct

                # Suppress county/subunit payload for NE
                st["counties"] = {}

                # Apply any state-level override after we set the RU topline
                _apply_psg_override(usps, st, office, race_type)

                # Duplicate W→S if needed
                if (race_type or "").upper() == "W":
                    dup_key = _combo_key(office, "S")
                    s_bucket = out.setdefault(dup_key, {"states": {}, "updated": time.time()})
                    s_bucket["states"][usps] = json.loads(json.dumps(st))
                    s_bucket["updated"] = time.time()

                # Timestamps & continue (skip normal counties path)
                st["updated"] = time.time()
                bucket["updated"] = time.time()
                continue


            # --- Non-NE states: keep your existing counties/subunits logic ---
            st["state_topline"] = state_topline
            st["state_total"]   = state_total

            st.setdefault("counties", {})

            # Pull RUs scoped to this Race (prefer Level='subunit' / ReportingUnitLevel='2')
            rus = race.findall("./ReportingUnit[@Level='subunit']") \
                or race.findall("./ReportingUnit[@ReportingUnitLevel='2']")
            if not rus:
                # Fallback: any RU with a FIPSCode under this race
                rus = [ru for ru in race.findall("./ReportingUnit") if ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS")]

            if not rus:
                # no county-level info for this state/race
                continue

            # First pass: FIPS values that have a county RU (ReportingUnitLevel == "2")
            fips_with_level2 = set()
            for ru in rus:
                lvl = (ru.attrib.get("ReportingUnitLevel") or "").strip()
                if lvl == "2":
                    f = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip()
                    if f and f.isdigit() and len(f) < 5:
                        f = f.zfill(5)
                    if f and f != "00000":
                        fips_with_level2.add(f)

            county_aggs = {}  # fips -> {"name": best_name, "cands": {(name,party): votes}}
            county_subs = {}  # fips -> { ru_id: {name, percent_in, candidates[], total} }

            for ru in rus:
                fips = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip()
                if fips and fips.isdigit() and len(fips) < 5:
                    fips = fips.zfill(5)
                if not fips or fips == "00000":
                    continue

                # For NYC Mayor, keep only boroughs
                if office == "M" and usps == "NY" and fips not in NYC_BOROUGH_FIPS:
                    continue

                ru_level = (ru.attrib.get("ReportingUnitLevel") or "").strip()

                ru_id   = (ru.attrib.get("ID") or ru.attrib.get("ReportingUnitID") or "").strip()
                ru_name = (ru.attrib.get("Name") or f"FIPS {fips}").strip()

                # percent at RU level
                ru_percent = ru.attrib.get("EEVP")
                if not ru_percent:
                    prec = ru.find("./Precincts")
                    if prec is not None:
                        ru_percent = prec.attrib.get("ReportingPct")

                # candidates for this RU
                ru_cands, ru_total = [], 0
                for c in ru.findall("./Candidate"):
                    first = (c.attrib.get("First") or "").strip()
                    last  = (c.attrib.get("Last")  or "").strip()
                    party = _norm_party(c.attrib.get("Party"))
                    try:
                        votes = int(c.attrib.get("VoteCount") or "0")
                    except Exception:
                        votes = 0
                    ru_total += votes
                    ru_cands.append({"name": (first + " " + last).strip(), "party": party, "votes": votes})

                # init per-county holders
                ca = county_aggs.setdefault(fips, {"name": None, "cands": {}})
                # NEW: track the best county-level %in seen for this FIPS (use max of available RUs)
                if ru_percent is not None:
                    try:
                        p = float(ru_percent)
                        prev = ca.get("percent")
                        ca["percent"] = p if prev is None else max(prev, p)
                    except Exception:
                        pass

                cs = county_subs.setdefault(fips, {})

                # prefer a county-looking label if we ever see one; otherwise keep first RU name
                if ca["name"] is None or ("County" in ru_name and "County" not in (ca["name"] or "")):
                    ca["name"] = ru_name

                # Only aggregate if:
                #   - this RU is a county (level 2), OR
                #   - there is no county RU at all for this FIPS
                should_aggregate = (ru_level == "2") or (fips not in fips_with_level2)

                if should_aggregate:
                    if ru_percent and (ru_level == "2" or ca["percent"] is None):
                        ca["percent"] = ru_percent

                    for rc in ru_cands:
                        key = (rc["name"], rc["party"])
                        ca["cands"][key] = ca["cands"].get(key, 0) + rc["votes"]

                # retain the fine-grain RU under `subunits`
                if ru_id:
                    cs[ru_id] = {
                        "name": ru_name,
                        "percent_in": ru_percent,
                        "candidates": ru_cands,
                        "total": ru_total
                    }

            # write county aggregates + subunit breakdowns to the state blob
            for fips, agg in county_aggs.items():
                cands = [{"name": n, "party": p, "votes": v}
                         for (n, p), v in agg["cands"].items()]
                cands.sort(key=lambda x: x["votes"], reverse=True)
                st["counties"][fips] = {
                    "state": usps,
                    "fips": fips,
                    "name": agg["name"] or f"FIPS {fips}",
                    "candidates": cands,
                    "total": sum(c["votes"] for c in cands),
                    "percent_in": agg.get("percent"),
                    "subunits": county_subs.get(fips, {})
                }



            # Apply any state-level override calls
            _apply_psg_override(usps, st, office, race_type)
            if (race_type or "").upper() == "W":
                dup_key = _combo_key(office, "S")
                s_bucket = out.setdefault(dup_key, {"states": {}, "updated": time.time()})
                s_bucket["states"][usps] = json.loads(json.dumps(st))  # deep copy of fully-populated blob
                s_bucket["updated"] = time.time()

        # refresh per-state timestamp each time we touch it
        st["updated"] = time.time()

        # bump combo bucket updated
        bucket["updated"] = time.time()

    return out
    
    
def _fetch_full_once():
    """Fetch FULL_DATA_URL and replace cache_by_combo in one shot."""
    url = CURRENT_FULL_DATA_URL
    try:
        r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
        _record_last_hit(url, r.status_code if hasattr(r, "status_code") else 0, "full")
        with _cache_lock:
            _stats["upstream_calls"] += 1
            _stats["upstream_bytes"] += len(r.content or b"")
    except requests.exceptions.RequestException as e:
        log(f"Transport error (full-dataset): {e}", "WARN")
        _stats["errors"] += 1
        return False

    # gentle quota retry, same semantics you already use
    if r.status_code == 403 and _is_quota_403(r):
        wait_s = 7
        log(f"403 quota (full-dataset); sleeping {wait_s}s then retrying once.", "WARN")
        time.sleep(wait_s)
        try:
            r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
            with _cache_lock:
                _stats["upstream_calls"] += 1
                _stats["upstream_bytes"] += len(r.content or b"")
        except requests.exceptions.RequestException as e:
            log(f"Transport error after 403-retry (full-dataset): {e}", "WARN")
            _stats["errors"] += 1
            return False

    if r.status_code != 200:
        log(f"HTTP {r.status_code} from FULL_DATA_URL", "WARN")
        _stats["errors"] += 1
        return False

    parsed = _parse_full_dataset(r.text)
    if not parsed:
        log("Parse error or empty full-dataset XML; keeping last good cache.", "WARN")
        _stats["errors"] += 1
        return False

    with _cache_lock:
        _cache["cache_by_combo"] = parsed
        _cache["last_cycle_end"] = time.time()

    _snapshot_save()
    return True



# -------------- Hub helpers ----------------

def _files_equal(a, b):
    if not os.path.exists(a) or not os.path.exists(b):
        return False
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()



def gate_pipeline_by_stats(stats_path: Path, stats3_path: Path) -> bool:
    """
    Returns True if pipeline SHOULD run.
    Returns False if pipeline should be skipped.
    """
    if stats3_path.exists() and files_are_equal(stats_path, stats3_path):
        try:
            stats_path.unlink()
        except Exception:
            pass
        return False

    # data changed → refresh stats3
    try:
        if stats3_path.exists():
            stats3_path.unlink()
        stats3_path.write_bytes(stats_path.read_bytes())
    except Exception:
        pass

    return True



def _csv_has_data_rows(path: str) -> bool:
    """
    Return True if CSV has at least one data row (beyond header).
    Safe for empty / missing files.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            # read at most 2 lines: header + first data row
            return len(f.readlines(2)) > 1
    except Exception:
        return False


def parse_targets(s: str):
    targets = []
    if not s:
        return targets

    for chunk in s.split(","):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) < 2:
            continue

        office = parts[0].upper()
        state  = parts[1].upper()
        district = None

        if office == "H" and len(parts) >= 3:
            district = parts[2].zfill(2)

        targets.append({
            "office": office,
            "state": state,
            "district": district,
        })

    return targets


def _sum_state_ru_votes(state_ru) -> int:
    """Sum Candidate@VoteCount inside a state-level ReportingUnit."""
    if state_ru is None:
        return 0
    total = 0
    for c in state_ru.findall("./Candidate"):
        raw = c.attrib.get("VoteCount") or "0"
        try:
            total += int(raw)
        except Exception:
            pass
    # Fallback: if AP provides <Parameters><Vote total="...">, prefer that when larger
    v = state_ru.find("./Parameters/Vote") or state_ru.find("./Vote")
    if v is not None and v.attrib.get("total"):
        try:
            vote_attr = int(v.attrib.get("total"))
            if vote_attr > total:
                total = vote_attr
        except Exception:
            pass
    return total


def _deep_merge_states(dst_states: dict, src_states: dict):
    """
    Merge state blobs without deleting existing ones.
    - For House: merge/overwrite districts by district_id.
    - For PSG (statewide): merge/overwrite counties and state topline.
    """
    for usps, src_blob in (src_states or {}).items():
        dst_blob = dst_states.get(usps, {})
        merged = dict(dst_blob)

        # Simple scalars or small dicts
        for k in ("updated","office","percent_in","race_call","state_topline","state_total"):
            if k in src_blob:
                merged[k] = src_blob[k]

        # House districts
        if "districts" in src_blob:
            merged["districts"] = dict(merged.get("districts", {}))
            for did, drow in (src_blob.get("districts") or {}).items():
                merged["districts"][did] = drow

        # Statewide counties
        if "counties" in src_blob:
            merged["counties"] = dict(merged.get("counties", {}))
            for fips, crow in (src_blob.get("counties") or {}).items():
                merged["counties"][fips] = crow

        dst_states[usps] = merged

#resultstype=this
def _build_primary_url_for(date_str: str) -> str:
    # Hit once per date; let AP include both D and R in one payload.
    return f"{BASE_URL_E}/{date_str}?level={LEVEL_PARAM}&{_resultstype_qp()}"

def _build_app_urls_for(date_str: str):
    # CA/WA APP House endpoints (state-scoped); include alongside D/R in rotation
    usps_list = (_hub_cfg["app_dates"].get(date_str) or [])
    return [
        f"{BASE_URL_E}/{date_str}?raceTypeID=APP&officeId=H&statepostal={usps}&level={LEVEL_PARAM}&{_resultstype_qp()}"
        for usps in usps_list
    ]


def _fetch_primary_url(url: str, which_label: str):
    try:
        r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
        with _cache_lock:
            _stats["upstream_calls"] += 1
            _stats["upstream_bytes"] += len(r.content or b"")
    except requests.exceptions.RequestException as e:
        log(f"Transport error (primary-url): {e}", "WARN")
        _stats["errors"] += 1
        _record_last_hit(url, 0, which_label)
        return False

    if r.status_code == 403 and _is_quota_403(r):
        wait_s = 7
        log(f"403 quota (primary-url); sleeping {wait_s}s then retrying once.", "WARN")
        time.sleep(wait_s)
        try:
            r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
            with _cache_lock:
                _stats["upstream_calls"] += 1
                _stats["upstream_bytes"] += len(r.content or b"")
        except requests.exceptions.RequestException as e:
            log(f"Transport error after 403-retry (primary-url): {e}", "WARN")
            _stats["errors"] += 1
            _record_last_hit(url, 0, which_label)
            return False

    _record_last_hit(url, r.status_code, which_label)
    if r.status_code != 200:
        log(f"HTTP {r.status_code} for primary-url", "WARN")
        _stats["errors"] += 1
        return False

    parsed = _parse_full_dataset(r.text)
    if not parsed:
        return False

    # Normalize H:APP → H:D/H:R for CA/WA to keep H:D/H:R addressable by UI
    norm = {}
    for combo, bucket in (parsed or {}).items():
        try:
            office, rtype = combo.split(":")
        except ValueError:
            norm[combo] = bucket
            continue
        if office == "H" and rtype == "APP":
            states = (bucket or {}).get("states") or {}
            for usps, st_blob in states.items():
                if usps in ("CA", "WA"):
                    for rt in ("D", "R"):
                        k = f"H:{rt}"
                        dst = norm.setdefault(k, {"states": {}, "updated": time.time()})
                        dst["states"][usps] = json.loads(json.dumps(st_blob))
                        dst["updated"] = time.time()
            norm[combo] = bucket
        else:
            norm[combo] = bucket

    with _primary_cache_lock:
        for combo, bucket in (norm or {}).items():
            dst = _primary_cache["cache_by_combo"].setdefault(combo, {"states": {}, "updated": 0.0})
            _deep_merge_states(dst["states"], bucket.get("states") or {})
            dst["updated"] = time.time()
        _primary_cache["last_cycle_end"] = time.time()
    _primary_snapshot_save()
    log(f"Primary merged from {url}")
    return True


def _fetch_primary_once():
    """
    Fetch the next item from PRIMARY_URLS and MERGE its parsed combos into the
    separate _primary_cache, preserving previously stored combos (no erase).
    """
    global _primary_rot_idx
    if not PRIMARY_URLS:
        log("No PRIMARY_URLS configured; skipping primary fetch.", "WARN")
        return False

    url = PRIMARY_URLS[_primary_rot_idx % len(PRIMARY_URLS)]
    _primary_rot_idx += 1

    try:
        r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
        with _cache_lock:
            _stats["upstream_calls"] += 1
            _stats["upstream_bytes"] += len(r.content or b"")
    except requests.exceptions.RequestException as e:
        log(f"Transport error (primary): {e}", "WARN")
        _stats["errors"] += 1
        return False

    # gentle quota retry, mirroring full
    if r.status_code == 403 and _is_quota_403(r):
        wait_s = 7
        log(f"403 quota (primary); sleeping {wait_s}s then retrying once.", "WARN")
        time.sleep(wait_s)
        try:
            r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
            with _cache_lock:
                _stats["upstream_calls"] += 1
                _stats["upstream_bytes"] += len(r.content or b"")
        except requests.exceptions.RequestException as e:
            log(f"Transport error after 403-retry (primary): {e}", "WARN")
            _stats["errors"] += 1
            return False

    if r.status_code != 200:
        log(f"HTTP {r.status_code} from PRIMARY_URLS", "WARN")
        _stats["errors"] += 1
        return False

    parsed = _parse_full_dataset(r.text)
    if not parsed:
        # keep your existing handling
        return False

    # Normalize House APP → (D and R) for CA/WA (so UI can query H:D/H:R)
    norm = {}
    for combo, bucket in (parsed or {}).items():
        try:
            office, rtype = combo.split(":")
        except ValueError:
            norm[combo] = bucket
            continue

        if office == "H" and rtype == "APP":
            states = (bucket or {}).get("states") or {}
            for usps, st_blob in states.items():
                if usps in ("CA", "WA"):
                    for rt in ("D", "R"):
                        k = f"H:{rt}"
                        dst = norm.setdefault(k, {"states": {}, "updated": time.time()})
                        dst["states"][usps] = json.loads(json.dumps(st_blob))  # deep copy
                        dst["updated"] = time.time()
            # keep original H:APP too (useful for debugging)
            norm[combo] = bucket
        else:
            norm[combo] = bucket

    parsed = norm

   


    # --- Merge combos into _primary_cache without erasing older combos ---
    with _primary_cache_lock:
        for combo, bucket in (parsed or {}).items():
            dst = _primary_cache["cache_by_combo"].setdefault(combo, {"states": {}, "updated": 0.0})
            _deep_merge_states(dst["states"], bucket.get("states") or {})
            dst["updated"] = time.time()
        _primary_cache["last_cycle_end"] = time.time()

    _primary_snapshot_save()
    log(f"Primary refresh merged from {url}")
    return True


def _record_error(office, race_type, usps, status_code=None, retry_after=None, why=None):
    pk = _combo_state_key(office, race_type, usps)
    now = time.time()
    with _cache_lock:
        m = _stats["per_combo_state"].setdefault(pk, {})
        m["err"] = m.get("err", 0) + 1
        m["consecutive_err"] = m.get("consecutive_err", 0) + 1
        m["last_error"] = {"ts": now, "status": status_code, "why": why}

        # exponential backoff with caps; honor Retry-After when present
        base = 2 ** min(6, m["consecutive_err"] - 1)  # 1,2,4,8,16,32,64
        delay = base
        if retry_after:
            try:
                delay = max(delay, int(retry_after))
            except Exception:
                try:
                    ra_dt = parsedate_to_datetime(str(retry_after))
                    if ra_dt:
                        delay = max(delay, (ra_dt.timestamp() - now))
                except Exception:
                    pass

        # specialize by status
        if status_code in (401, 403):        # auth/forbidden → longer pause
            delay = max(delay, 15 * 60)
        elif status_code == 429:              # rate limited
            delay = max(delay, 60)
        elif status_code in (500, 502, 503, 504):
            delay = max(delay, 10)

        m["next_ok_at"] = now + min(delay, 30 * 60)  # cap at 30m


def _record_success(office, race_type, usps):
    pk = _combo_state_key(office, race_type, usps)
    now = time.time()
    with _cache_lock:
        m = _stats["per_combo_state"].setdefault(pk, {})
        m["ok"] = m.get("ok", 0) + 1
        m["last_fetch"] = now
        m["consecutive_err"] = 0
        m["next_ok_at"] = now



def _combo_key(office: str, race_type: str) -> str:
    return f"{office.upper()}:{race_type}"

def _combo_state_key(office: str, race_type: str, usps: str) -> str:
    return f"{office.upper()}:{race_type}|{usps}"

def _ensure_combo_bucket(office: str, race_type: str):
    key = _combo_key(office, race_type)
    with _cache_lock:
        _cache["cache_by_combo"].setdefault(key, {"states": {}, "updated": 0.0})

def _should_refetch(office: str, race_type: str, usps: str) -> bool:
    pk = _combo_state_key(office, race_type, usps)
    now = time.time()
    with _cache_lock:
        st = _stats["per_combo_state"].get(pk, {})
        last = st.get("last_fetch", 0.0)
        next_ok_at = st.get("next_ok_at", 0.0)
    if now < next_ok_at:
        return False
    return (now - last) >= MIN_STATE_REFRESH_SEC

    
    
def _get_prev_vote_county(usps: str, fips: str, party: str, office: str, race_type: str):
    combo = _combo_key(office, race_type)
    with _cache_lock:
        bucket = _cache["cache_by_combo"].get(combo, {})
        state_blob = (bucket.get("states") or {}).get(usps, {})
        counties = state_blob.get("counties") or {}
        c = counties.get(fips)
        if not c: return None
        want = _norm_party(party)
        for cand in c.get("candidates", []):
            if _norm_party(cand.get("party")) == want:
                try:
                    return int(cand.get("votes") or 0)
                except Exception:
                    return None
    return None

def _get_prev_vote_district(usps: str, did: str, party: str, office: str, race_type: str):
    combo = _combo_key(office, race_type)
    with _cache_lock:
        bucket = _cache["cache_by_combo"].get(combo, {})
        state_blob = (bucket.get("states") or {}).get(usps, {})
        districts = state_blob.get("districts") or {}
        d = districts.get(did)
        if not d: return None
        want = _norm_party(party)
        for cand in d.get("candidates", []):
            if _norm_party(cand.get("party")) == want:
                try:
                    return int(cand.get("votes") or 0)
                except Exception:
                    return None
    return None

def _get_prev_vote_house_county(usps: str, did: str, fips: str, party: str, office: str, race_type: str):
    """Last-good VoteCount fallback for House county RUs stored under districts[did]['counties'][fips]."""
    combo = _combo_key(office, race_type)
    want = (party or "").upper()
    with _cache_lock:
        bucket = (_cache.get("cache_by_combo") or {}).get(combo, {})
        st = (bucket.get("states") or {}).get(usps, {})
        dist = (st.get("districts") or {}).get(did, {})
        county = (dist.get("counties") or {}).get(fips, {})
        total = 0
        found = False
        for c in county.get("candidates") or []:
            if (c.get("party") or "").upper() == want:
                try:
                    total += int(c.get("votes") or 0)
                    found = True
                except Exception:
                    pass
        return total if found else None


def _fetch_state(usps: str, office: str, race_type: str):
    combo = _combo_key(office, race_type)
    inflight_key = f"{combo}|{usps}"
    with _cache_lock:
        if inflight_key in _inflight: return False
        _inflight.add(inflight_key)
    try:
        if not _should_refetch(office, race_type, usps):
            log(f"Skip {combo} {usps}: within refresh window ({int(MIN_STATE_REFRESH_SEC)}s)")
            return True

        url = _build_url(usps, office, race_type)
        t0 = time.time()
        try:
            r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
            with _cache_lock:
                _stats["upstream_calls"] += 1
                _stats["upstream_bytes"] += len(r.content or b"")
        except requests.exceptions.RequestException as e:
            _record_error(office, race_type, usps, why=type(e).__name__)
            log(f"Transport error {combo} {usps}: {e}", "WARN")
            return False
        
        #
        # Graceful inline retry for '403: Per-minute quota exceeded'
        if r.status_code == 403 and _is_quota_403(r):
            wait_s = random.randint(5, 10)
            log(f"403 quota for {combo} {usps}; sleeping {wait_s}s then retrying once.", "WARN")
            time.sleep(wait_s)
            try:
                r = HTTP.get(url, timeout=TIMEOUT_SECONDS)
                with _cache_lock:
                    _stats["upstream_calls"] += 1
                    _stats["upstream_bytes"] += len(r.content or b"")
            except requests.exceptions.RequestException as e:
                _record_error(office, race_type, usps, why=type(e).__name__)
                log(f"Transport error after 403-retry {combo} {usps}: {e}", "WARN")
                return False


        if r.status_code != 200:

            ra = r.headers.get("Retry-After")
            _record_error(office, race_type, usps, status_code=r.status_code, retry_after=ra)
            log(f"HTTP {r.status_code} for {combo} {usps}", "WARN")
            return False

        # ---- Parse (guard against XML failures) ----
        if office.upper() == "H":
            parsed = _parse_house_ru(r.text, usps, office, race_type)
            if parsed is None:
                _record_error(office, race_type, usps, why="parse")
                return False
            _apply_house_overrides(usps, parsed.get("districts"), office, race_type)
        else:
            parsed = _parse_state_ru(r.text, usps, office, race_type)
            if parsed is None:
                _record_error(office, race_type, usps, why="parse")
                return False
            _apply_psg_override(usps, parsed, office, race_type)
            
        #
        # ---- Never overwrite a good cache with an empty parsed payload ----
        _ensure_combo_bucket(office, race_type)
        with _cache_lock:
            bucket = _cache["cache_by_combo"][combo]
            prev_blob = (bucket.get("states") or {}).get(usps)
        new_units_len = len(parsed.get("districts") or parsed.get("counties") or {})
        if prev_blob and new_units_len == 0:
            _record_error(office, race_type, usps, why="empty")
            log(f"Empty/invalid payload for {combo} {usps}; preserving last good cache.", "WARN")
            return False



        # Store
        with _cache_lock:
            bucket = _cache["cache_by_combo"][combo]
            # If this is a W race type, also mirror into :S


            if office.upper() == "H":
                bucket["states"][usps] = {
                    "updated": time.time(),
                    "office": "H",
                    "percent_in": parsed.get("percent_in"),
                    "districts": parsed["districts"]          # carries race_call after overrides
                }
                if interpret_race_type(race_type)["raw"].upper() == "W":
                    dup_combo = _combo_key("H", "S")
                    dbucket = _cache["cache_by_combo"].setdefault(dup_combo, {"states": {}, "updated": 0.0})
                    dbucket["states"][usps] = json.loads(json.dumps(bucket["states"][usps]))  # deep copy
                    dbucket["updated"] = time.time()
            else:
                bucket["states"][usps] = {
                    "updated": time.time(),
                    "office": office.upper(),
                    "percent_in": parsed.get("percent_in"),
                    "counties": parsed["counties"],
                    "race_call": parsed.get("race_call"),       # may be overridden
                    "state_topline": parsed.get("state_topline"),
                    "state_total":   parsed.get("state_total"),
                }
                if interpret_race_type(race_type)["raw"].upper() == "W":
                    dup_combo = _combo_key(office, "S")
                    dbucket = _cache["cache_by_combo"].setdefault(dup_combo, {"states": {}, "updated": 0.0})
                    dbucket["states"][usps] = json.loads(json.dumps(bucket["states"][usps]))  # deep copy
                    dbucket["updated"] = time.time()

            bucket["updated"] = time.time()
            pk = _combo_state_key(office, race_type, usps)
            ps = _stats["per_combo_state"].setdefault(pk,{})
            ps["last_fetch"] = time.time()
            ps["ok"] = ps.get("ok",0) + 1

        if office.upper() == "H" and usps == "LA" and interpret_race_type(race_type)["raw"] in ("L", "NP", "X"):
            dup_combo = _combo_key("H", "G")
            with _cache_lock:
                src = _cache["cache_by_combo"].get(combo, {}).get("states", {}).get("LA")
                if src:
                    dbucket = _cache["cache_by_combo"].setdefault(dup_combo, {"states": {}, "updated": 0.0})
                    dbucket["states"]["LA"] = json.loads(json.dumps(src))  # deep copy
                    dbucket["updated"] = time.time()

        _record_success(office, race_type, usps)
        dt = time.time()-t0
        nodes = len(parsed.get("districts") or parsed.get("counties") or {})
        log(f"Fetched {combo} {usps}: {nodes} units in {dt:.1f}s")
        return True
    finally:
        with _cache_lock:
            _inflight.discard(inflight_key)

def _hub_loop():
    log("Hub (mode-aware) poller starting..." if HUB_MODE else "Hub disabled (serve-only).")

    if not HUB_MODE:

        return

    # Load persisted UI config once on startup

    _date_cfg_load()
    # Load primary-dates-new.json once on startup (if present)
    try:
        _primary_dates_new_load(force=True)
    except Exception:
        pass


    # Load rotating full-data URLs (if present)

    _full_data_urls_load(force=True)

    _snapshot_load()

    _primary_snapshot_load()



    step = 0
    while True:
        try:
            # Hot-load resultstype (b/t) once per hub cycle (mtime-aware)
            _results_type_load(force=False)

            #
            if _hub_cfg["mode"] == "general":
                # General mode uses the rotating list from ../temp/full-data-url.json.
                # We hot-reload it on mtime change.
                _full_data_urls_load(force=False)


                # If a list is configured, rotate through it; otherwise fall back to date.json full_date.

                if FULL_DATA_URLS:

                    global _full_rot_idx

                    globals()["CURRENT_FULL_DATA_URL"] = FULL_DATA_URLS[_full_rot_idx % len(FULL_DATA_URLS)]

                    _full_rot_idx += 1

                else:

                    if not (globals().get("CURRENT_FULL_DATA_URL") or "").strip():

                        fd = _hub_cfg.get("full_date") or "2024-11-05"

                        globals()["CURRENT_FULL_DATA_URL"] = f"{BASE_URL_E}/{fd}?level={LEVEL_PARAM}&resultstype=b"

                ok = _fetch_full_once()

                log("Full-dataset refresh complete." if ok else "Full-dataset refresh failed; will retry.",

                    "WARN" if not ok else "INFO")

                sleep_s = SLEEP_S  # 3 per minute


            elif _hub_cfg["mode"] == "quick_primary":
                # QUICK PRIMARY: walk all dates linearly, one hit per tick (~20s)
                global _qp_idx
                all_dates = list(_hub_cfg.get("all_primary_dates") or [])
                if not all_dates:
                    log("Quick primary mode: no primary dates configured; skipping.", "WARN")
                    sleep_s = SLEEP_S
                else:
                    sel = all_dates[_qp_idx % len(all_dates)]
                    _qp_idx += 1

                    # One URL per date (no raceTypeID) …
                    urls = [_build_primary_url_for(sel)]
                    # … plus APP hits if this date has CA/WA APP races
                    urls += _build_app_urls_for(sel)

                    hit_ok = True
                    for u in urls:
                        hit_ok &= _fetch_primary_url(u, "quick")
                    log("Quick primary refresh complete." if hit_ok else "Quick primary refresh had errors.",
                        "WARN" if not hit_ok else "INFO")
                    sleep_s = SLEEP_S  # one date every ~20s

            else:
                # PRIMARY MODE: step cycle = MAIN, MAIN, SECONDARY (then repeat)
                cycle_index = step % 3
                all_dates = list(_hub_cfg.get("all_primary_dates") or [])
                main_date = _hub_cfg.get("main_date")
                secondary = [d for d in all_dates if d != main_date] if main_date else all_dates[:]
                _hub_cfg["secondary_dates"] = secondary

                which = "secondary" if cycle_index == 2 else "main"
                if which == "main":
                    if not main_date:
                        log("Primary mode: no main_date selected; skipping.", "WARN")
                        sleep_s = SLEEP_S
                        step += 1
                        time.sleep(sleep_s)
                        continue
                    # One URL (no raceTypeID) + APP if applicable
                    urls = [_build_primary_url_for(main_date)] + _build_app_urls_for(main_date)
                    hit_ok = True
                    for u in urls:
                        hit_ok &= _fetch_primary_url(u, "main")
                    log("Primary (main) refresh complete." if hit_ok else "Primary (main) refresh had errors.",
                        "WARN" if not ok else "INFO")
                else:
                    if not secondary:
                        log("Primary mode: no secondary dates; skipping.", "INFO")
                    else:
                        sec_index = (step // 3) % len(secondary)
                        sec_date = secondary[sec_index]
                        # One URL (no raceTypeID) + APP if applicable
                        urls = [_build_primary_url_for(sec_date)] + _build_app_urls_for(sec_date)
                        hit_ok = True
                        for u in urls:
                            hit_ok &= _fetch_primary_url(u, "secondary")
                        log("Primary (secondary) refresh complete." if hit_ok else "Primary (secondary) refresh had errors.",
                            "WARN" if not hit_ok else "INFO")

                sleep_s = SLEEP_S  # main, main, secondary → 3 hits per min



        except Exception as e:
            log(f"Unhandled in hub loop: {e}", "WARN")
            sleep_s = SLEEP_S

        step += 1
        # --- system usage readout ---
        # --- system usage readout ---
        try:
            u = _system_usage()

            # Line 1: system
            log(
                f"System CPU={u['cpu_percent']:.1f}% | "
                f"RAM={u['mem_percent']:.1f}%"
            )

            # Line 2: app memory
            log(f"App RSS={u['app_rss_mb']:.0f}MB")

            # Line 3: pipelines (running vs history)
            recent = u.get("recent_pipelines", [])
            last = recent[-1] if recent else None

            if u["pipeline_count"] > 0:
                log(
                    f"Pipelines running={u['pipeline_count']} | "
                    f"Current peak={u['pipeline_peak_rss_mb']:.0f}MB"
                )
            elif last:
                log(
                    f"Pipelines running=0 | "
                    f"Last peak={last['peak_rss_mb']:.0f}MB | "
                    f"Last runtime={last['runtime_sec']}s"
                )
            else:
                log("Pipelines running=0")

        except Exception as e:
            log(f"System load check failed: {e}", "WARN")
        # -----------------------------

        # -----------------------------

        time.sleep(sleep_s)



# ---------------- HTTP (spokes read-only) ----------------
@app.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    # add POST for admin endpoints
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp





# (optional) handle preflight quickly
@app.route("/<path:_any>", methods=["OPTIONS"])
def _any_options(_any):
    return ("", 204)

WIN_PROB_DIR = os.path.abspath(os.path.join(ROOT_DIR, "..", "win_prob"))

@app.route("/win_prob/<path:filename>")
def serve_win_prob(filename):
    return send_from_directory(WIN_PROB_DIR, filename)

@app.route("/")
def root(): return send_from_directory(app.static_folder,"index.html")

@app.route("/health")
def health():
    with _cache_lock:
        combos = list(_cache["cache_by_combo"].keys())
        states_cached = {k: len(v.get("states", {})) for k, v in _cache["cache_by_combo"].items()}
        last_cycle_end = _cache["last_cycle_end"]

    with _primary_cache_lock:
        p_combos = list(_primary_cache["cache_by_combo"].keys())
        p_states_cached = {k: len(v.get("states", {})) for k, v in _primary_cache["cache_by_combo"].items()}
        p_last_cycle_end = _primary_cache.get("last_cycle_end", 0.0)

    return jsonify({
        "hub_mode": HUB_MODE,
        "combos": combos,
        "states_cached_by_combo": states_cached,
        "last_cycle_end_utc": datetime.utcfromtimestamp(last_cycle_end).strftime("%Y-%m-%d %H:%M:%S") if last_cycle_end else None,
        "primary_combos": p_combos,
        "primary_states_cached_by_combo": p_states_cached,
        "primary_last_cycle_end_utc": datetime.utcfromtimestamp(p_last_cycle_end).strftime("%Y-%m-%d %H:%M:%S") if p_last_cycle_end else None,
    })


@app.route("/admin/hub-config", methods=["GET","POST"])
def admin_hub_config():
    if request.method == "GET":
        # include current pointer URL + last few hits for convenience
        return jsonify({
            "cfg": _hub_cfg,
            "current_full_url": globals().get("CURRENT_FULL_DATA_URL", (FULL_DATA_URLS[0] if FULL_DATA_URLS else None)),
            "primary_snapshot_path": PRIMARY_SNAPSHOT_PATH,
            "general_snapshot_path": CACHE_SNAPSHOT_PATH,
            "date_config_path": DATE_CONFIG_PATH
        })
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or _hub_cfg["mode"]).strip().lower()
    if mode not in ("general","primary","quick_primary"):
        mode = "general"
    _hub_cfg["mode"] = mode


    # Full-date override (general)
    fd = data.get("full_date")
    if isinstance(fd, str) and fd:
        _hub_cfg["full_date"] = fd

    # Main primary date + recompute secondaries
    md = data.get("main_date")
    if isinstance(md, str) and md:
        _hub_cfg["main_date"] = md

    # Optional: reset alternators
    if data.get("reset_flips"):
        _hub_cfg["dr_flip_main"] = "D"
        _hub_cfg["dr_flip_secondary"] = "D"

    # Immediately update full pointer too (no wait)
    globals()["CURRENT_FULL_DATA_URL"] = f"{BASE_URL_E}/{_hub_cfg['full_date']}?level={LEVEL_PARAM}&resultstype=b"
    _date_cfg_save()
    return jsonify({"ok": True, "cfg": _hub_cfg, "date_config_path": DATE_CONFIG_PATH})

@app.route("/admin/last-hit", methods=["GET"])
def admin_last_hit():
    # newest first
    return jsonify(list(reversed(_last_hits[-25:])))

@app.get("/admin/bop")
def admin_bop_get():
    _bop_load()
    debug = request.args.get("debug") in ("1","true","yes")
    with _bop_lock:
        out = dict(_bop)  # shallow copy
    if debug:
        try:
            mtime = os.path.getmtime(BOP_OVERRIDES_PATH) if os.path.exists(BOP_OVERRIDES_PATH) else 0.0
        except Exception:
            mtime = 0.0
        out["__meta"] = {
            "path": BOP_OVERRIDES_PATH,
            "exists": os.path.exists(BOP_OVERRIDES_PATH),
            "mtime": mtime,
            "keys": list((_bop or {}).keys()),
        }
        app.logger.info(f"[BOP] GET debug path={BOP_OVERRIDES_PATH} exists={out['__meta']['exists']} keys={len(out['__meta']['keys'])}")
    else:
        app.logger.info(f"[BOP] GET ok keys={len((_bop or {}).keys())}")
    return jsonify(out)


@app.post("/admin/bop")
def admin_bop_post():
    try:
        body = request.get_json(silent=True) or {}
        op   = (body.get("op") or "").strip().lower()
        data = body.get("data") or {}
        year = int(data.get("year"))
        chamber = (data.get("chamber") or "H").upper()[:1]
        key = f"{year}|{chamber}"

        _bop_load()
        changed = False
        with _bop_lock:
            if op == "delete":
                if key in _bop:
                    _bop.pop(key, None)
                    changed = True
                    app.logger.info(f"[BOP] DELETE {key}")
            elif op in ("upsert","save","set"):
                row = {
                    "DEM": int(data.get("DEM") or 0),
                    "REP": int(data.get("REP") or 0),
                    "IND": int(data.get("IND") or 0),
                    "updated": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                }
                _bop[key] = row
                changed = True
                app.logger.info(f"[BOP] UPSERT {key} -> {row}")
            else:
                app.logger.warning(f"[BOP] Unknown op: {op}")

        if changed:
            _bop_save()
        with _bop_lock:
            return jsonify(_bop)
    except Exception as e:
        app.logger.exception("[BOP] POST error")
        return jsonify({"error": str(e)}), 400

@app.route("/admin/system-usage")
def admin_system_usage():
    return jsonify(_system_usage())



@app.route("/cache/p")  # Back-compat: presidential general counties
def cache_p():
    # Exactly what your UI expects today (P general)
    return cache_ru()  # will default to office=P, raceTypeId=G below

@app.route("/cache/ru")
def cache_ru():
    office = (request.args.get("office") or "P").upper()
    race_type = request.args.get("raceTypeId") or "G"
    combo = _combo_key(office, race_type)

    with _cache_lock:
        bucket = _cache["cache_by_combo"].get(combo, {})
        if not bucket:
        # try normalized "raw" (e.g. GEN → G)
            raw = interpret_race_type(race_type)["raw"]
            combo_raw = _combo_key(office, raw)
            bucket = _cache["cache_by_combo"].get(combo_raw, {})
        states = bucket.get("states", {})
        
    if office == "H" and (race_type or "").upper() == "G":
        sources = ["L", "NP", "X"]
        with _cache_lock:
            needs_la = "LA" not in (states or {})
            if needs_la:
                for src_rt in sources:
                    src_bucket = _cache["cache_by_combo"].get(_combo_key("H", src_rt), {})
                    la_state = (src_bucket.get("states") or {}).get("LA")
                    if la_state:
                        # Don't mutate shared dict—copy and graft
                        states = dict(states)
                        states["LA"] = json.loads(json.dumps(la_state))
                        break

    rows = []
    if office == "H":
        for usps, blob in states.items():
            for did, d in (blob.get("districts") or {}).items():
                rc  = (d.get("race_call") or {})                  # NEW
                win = (rc.get("winner") or {})
                rows.append({
                    "state": usps,
                    "district_id": did,
                    "district_num": d.get("district_num"),
                    "name": d["name"],
                    "candidates": d["candidates"],
                    "total": d["total"],
                    "updated": blob["updated"],
                    "percent_in": d.get("percent_in"),
                    "state_percent_in": blob.get("percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    # NEW:
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": win.get("name"),
                    "race_called_winner_party": win.get("party"),
                    "raceTypeId": race_type,
                    "office": office,
                })
    else:
        for usps, blob in states.items():
            rc  = (blob.get("race_call") or {})                   # NEW
            win = (rc.get("winner") or {})
            # --- DC FIX ---
            # DC comes in as wards with "090xx" pseudo-fips (looks like CT),
            # but your map geometry expects DC county-equivalent FIPS "11001".
            if usps == "DC":
                rows.append({
                    "state": usps,
                    "fips": "11001",
                    "name": "District of Columbia",
                    "candidates": blob.get("state_topline") or [],
                    "total": blob.get("state_total") or 0,
                    "updated": blob.get("updated"),
                    "percent_in": blob.get("percent_in") or blob.get("state_percent_in"),
                    "state_percent_in": blob.get("percent_in") or blob.get("state_percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": win.get("name"),
                    "race_called_winner_party": win.get("party"),
                })

                # Optional but helpful for any logic that keys off "xx000" statewide rows:
                rows.append({
                    "state": usps,
                    "fips": "11000",
                    "name": "DC Statewide",
                    "candidates": blob.get("state_topline") or [],
                    "total": blob.get("state_total") or 0,
                    "updated": blob.get("updated"),
                    "percent_in": blob.get("percent_in") or blob.get("state_percent_in"),
                    "state_percent_in": blob.get("percent_in") or blob.get("state_percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": win.get("name"),
                    "race_called_winner_party": win.get("party"),
                })

                continue
            # --- /DC FIX ---

            
            for fips, c in (blob.get("counties") or {}).items():
                rows.append({
                    "state": usps,
                    "fips": fips,
                    "name": c["name"],
                    "candidates": c["candidates"],
                    "total": c["total"],
                    "updated": blob["updated"],
                    "percent_in": c.get("percent_in"),
                    "state_percent_in": blob.get("percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    # NEW (state-level applies to all counties in state):
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": win.get("name"),
                    "race_called_winner_party": win.get("party")
                })
            if not any(r.get("state") == usps for r in rows):
                stfp = USPS_TO_STATEFP.get(usps, "")
                rows.append({
                    "state": usps,
                    "fips": f"{stfp}000" if stfp else None,
                    "name": f"{usps} Statewide",
                    "candidates": blob.get("state_topline") or [],
                    "total": blob.get("state_total") or 0,
                    "updated": blob.get("updated"),
                    "percent_in": blob.get("percent_in"),
                    "state_percent_in": blob.get("percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": (win or {}).get("name"),
                    "race_called_winner_party": (win or {}).get("party"),
                })
        # keep your sort, but guard int() safely
        rows.sort(key=lambda r: (r["state"], int(r.get("fips","0")) if str(r.get("fips","0")).isdigit() else 0))

    return jsonify({"office": office, "raceTypeId": race_type, "rows": rows})


@app.route("/primary_cache/ru")
def primary_cache_ru():
    office = (request.args.get("office") or "P").upper()
    race_type = (request.args.get("raceTypeId") or "D").upper()
    combo = _combo_key(office, race_type)

    with _primary_cache_lock:
        bucket = _primary_cache["cache_by_combo"].get(combo, {})
        states = bucket.get("states", {})

    rows = []
    if office == "H":
        for usps, blob in states.items():
            for did, d in (blob.get("districts") or {}).items():
                rc  = (d.get("race_call") or {})
                win = (rc.get("winner") or {})
                rows.append({
                    "state": usps,
                    "district_id": d.get("district_id"),
                    "district_num": d.get("district_num"),
                    "name": d.get("name"),
                    "candidates": d.get("candidates"),
                    "total": d.get("total"),
                    "updated": blob.get("updated"),
                    "percent_in": d.get("percent_in"),
                    "state_percent_in": blob.get("percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": win.get("name"),
                    "race_called_winner_party": win.get("party"),
                    "raceTypeId": race_type,
                    "office": office,
                })
    else:
        for usps, blob in states.items():
            rc  = (blob.get("race_call") or {})
            win = (rc.get("winner") or {})
            # --- DC FIX ---
            # DC comes in as wards with "090xx" pseudo-fips (looks like CT),
            # but your map geometry expects DC county-equivalent FIPS "11001".
            if usps == "DC":
                rows.append({
                    "state": usps,
                    "fips": "11001",
                    "name": "District of Columbia",
                    "candidates": blob.get("state_topline") or [],
                    "total": blob.get("state_total") or 0,
                    "updated": blob.get("updated"),
                    "percent_in": blob.get("percent_in") or blob.get("state_percent_in"),
                    "state_percent_in": blob.get("percent_in") or blob.get("state_percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": win.get("name"),
                    "race_called_winner_party": win.get("party"),
                })

                # Optional but helpful for any logic that keys off "xx000" statewide rows:
                rows.append({
                    "state": usps,
                    "fips": "11000",
                    "name": "DC Statewide",
                    "candidates": blob.get("state_topline") or [],
                    "total": blob.get("state_total") or 0,
                    "updated": blob.get("updated"),
                    "percent_in": blob.get("percent_in") or blob.get("state_percent_in"),
                    "state_percent_in": blob.get("percent_in") or blob.get("state_percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": win.get("name"),
                    "race_called_winner_party": win.get("party"),
                })

                continue
            # --- /DC FIX ---

            for fips, c in (blob.get("counties") or {}).items():
                rows.append({
                    "state": usps,
                    "fips": fips,
                    "name": c.get("name"),
                    "candidates": c.get("candidates"),
                    "total": c.get("total"),
                    "updated": blob.get("updated"),
                    "percent_in": c.get("percent_in"),
                    "state_percent_in": blob.get("percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": win.get("name"),
                    "race_called_winner_party": win.get("party"),
                })
            if not any(r.get("state") == usps for r in rows):
                stfp = USPS_TO_STATEFP.get(usps, "")
                rows.append({
                    "state": usps,
                    "fips": f"{stfp}000" if stfp else None,
                    "name": f"{usps} Statewide",
                    "candidates": blob.get("state_topline") or [],
                    "total": blob.get("state_total") or 0,
                    "updated": blob.get("updated"),
                    "percent_in": blob.get("percent_in"),
                    "state_percent_in": blob.get("percent_in"),
                    "state_topline": blob.get("state_topline") or [],
                    "state_total":   blob.get("state_total")   or 0,
                    "race_call_status": rc.get("status") or "No Decision",
                    "race_called_winner_name": (win or {}).get("name"),
                    "race_called_winner_party": (win or {}).get("party"),
                })

        rows.sort(key=lambda r: (r["state"], int(r.get("fips","0")) if str(r.get("fips","0")).isdigit() else 0))

    return jsonify({"office": office, "raceTypeId": race_type, "rows": rows})


@app.route("/log")
def get_log():
    try: since = int(request.args.get("since","0"))
    except ValueError: since = 0
    with _cache_lock:
        items = [x for x in list(_cache["log"]) if x["seq"] > since]
        max_seq = _log_seq
    return jsonify({"max_seq":max_seq,"items":items})
    
    
# === Basic Auth decorator for override admin ===
from functools import wraps
import base64

def _check_auth(auth_header):
    if not OVERRIDE_USER or not OVERRIDE_PASS:
        # No auth configured → allow all
        return True
    try:
        scheme, b64 = (auth_header or "").split(" ", 1)
        if scheme.lower() != "basic":
            return False
        userpass = base64.b64decode(b64).decode("utf-8", "ignore")
        u, p = userpass.split(":", 1)
        return (u == OVERRIDE_USER and p == OVERRIDE_PASS)
    except Exception:
        return False

def require_override_auth(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        if request.method == "GET":  # allow read-only even when creds unset
            return fn(*args, **kwargs)
        if _check_auth(request.headers.get("Authorization")):
            return fn(*args, **kwargs)
        return jsonify({"error":"unauthorized"}), 401
    return inner

@app.route("/api/pipeline/targets", methods=["GET","POST"])
def api_pipeline_targets():
    if request.method == "GET":
        active, base = _pipeline_targets_active()
        # return the full stored list (including disabled) for editing
        return jsonify({"ok": True, "path": PIPELINE_TARGETS_PATH, "targets": base, "enabled_count": len(active)})

    data = request.get_json(force=True, silent=True) or {}
    raw_list = data.get("targets")
    if not isinstance(raw_list, list):
        return jsonify({"error": "body must be {\"targets\": [...]}"}), 400

    cleaned = []
    seen = set()
    for r in raw_list:
        if not isinstance(r, dict):
            continue
        nr = _normalize_target_row(r)
        if not nr:
            continue
        k = (nr["office"], nr["state"], nr["district"] or "")
        if k in seen:
            # merge enabled flag
            for item in cleaned:
                if (item["office"], item["state"], item["district"] or "") == k:
                    item["enabled"] = item["enabled"] or nr["enabled"]
                    break
            continue
        seen.add(k)
        cleaned.append(nr)

    if not cleaned:
        return jsonify({"error": "empty/invalid targets list"}), 400

    try:
        _pipeline_targets_save(cleaned)
    except Exception as e:
        return jsonify({"error": f"save failed: {e}"}), 500

    active = [t for t in cleaned if t.get("enabled", True)]
    return jsonify({"ok": True, "path": PIPELINE_TARGETS_PATH, "targets": cleaned, "enabled_count": len(active)})


@app.route("/overrides", methods=["GET"])
def overrides_get():
    _load_overrides()
    with _overrides_lock:
        # also return a small derived view of 'active' (not expired) items
        active = {}
        for combo, m in (_overrides or {}).items():
            for unit, payload in (m or {}).items():
                if not _is_expired(payload):
                    active.setdefault(combo, {})[unit] = payload
    return jsonify({"path": OVERRIDES_PATH, "overrides": active})


@app.route("/overrides/upsert", methods=["POST"])
def overrides_upsert():
    """
    Body:
      {
        "combo": "P:G" | "S:G" | "G:G" | "H:G" (etc),
        "unit":  "CA" (for P/S/G) or "CA-12" / "0601" (for H),
        "payload": {
            "status": "Called",
            "winner": {"name":"Jane Doe","party":"DEM"},
            "note": "Desk call 21:13 ET",
            "sticky": true,
            "expires_at": "2026-11-06T09:00:00Z"   # optional
        }
      }
    """
    try:
        j = request.get_json(force=True) or {}
        combo = str(j.get("combo","")).strip().upper()
        unit  = str(j.get("unit","")).strip()
        payload = j.get("payload") or {}
        if not combo or not unit:
            return jsonify({"error":"combo and unit are required"}), 400

        # stamp creation/update time (helps TTL if used)
        payload = dict(payload)
        payload["_created_at"] = datetime.utcnow().isoformat() + "Z"

        _load_overrides()
        with _overrides_lock:
            _overrides.setdefault(combo, {})[unit] = payload
        _save_overrides()
        log(f"Override upsert {combo}/{unit}: {payload}")
        return jsonify({"ok": True})
    except Exception as e:
        log(f"/overrides/upsert error: {e}", "WARN")
        return jsonify({"error": str(e)}), 500


@app.route("/overrides/delete", methods=["POST"])
def overrides_delete():
    try:
        j = request.get_json(force=True) or {}
        combo = str(j.get("combo","")).strip().upper()
        unit  = str(j.get("unit","")).strip()
        if not combo or not unit:
            return jsonify({"error":"combo and unit are required"}), 400

        _load_overrides()
        removed = False
        with _overrides_lock:
            byc = _overrides.get(combo)
            if byc and unit in byc:
                byc.pop(unit, None)
                if not byc:
                    _overrides.pop(combo, None)
                removed = True
        if removed:
            _save_overrides()
            log(f"Override deleted {combo}/{unit}")
        return jsonify({"ok": True, "removed": removed})
    except Exception as e:
        log(f"/overrides/delete error: {e}", "WARN")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    _start_hub_once()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","9052")))

