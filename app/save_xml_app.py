# save_xml_app.py
import os
from flask import Flask, jsonify
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(APP_ROOT, "results.xml")

# Endpoint to fetch
AP_URL = "https://api.ap.org/v3/elections/2024-11-05?level=ru&resultstype=b"

# API key (same env-var pattern as app_hub.py)
AP_API_KEY = os.getenv("AP_API_KEY", os.getenv("AP_KEY", "ziwbrof5ondu4qq67ej4cz73to"))

# Robust HTTP session with light retries
HTTP = requests.Session()
HTTP.trust_env = False
retries = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET"]),
    raise_on_status=False,
)
HTTP.mount("http://", HTTPAdapter(max_retries=retries))
HTTP.mount("https://", HTTPAdapter(max_retries=retries))
HTTP.headers.update({
    "User-Agent": "SimpleXMLSaver/1.0",
    "x-api-key": AP_API_KEY,
})

app = Flask(__name__)

@app.route("/")
def home():
    return "OK. Use /save to fetch and write results.xml."

@app.route("/save")
def save():
    try:
        r = HTTP.get(AP_URL, timeout=30)
        if r.status_code != 200:
            return jsonify({"ok": False, "status": r.status_code, "error": r.text[:300]}), 502

        # Basic guard: ensure we actually got XML
        text = r.text or ""
        head = text.lstrip()[:100].lower()
        if not head.startswith("<"):
            return jsonify({"ok": False, "status": r.status_code, "error": "Response is not XML"}), 502

        with open(OUT_PATH, "w", encoding="utf-8") as f:
            f.write(text)

        return jsonify({"ok": True, "path": OUT_PATH, "bytes": len(text.encode("utf-8"))})
    except requests.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    # Run: python save_xml_app.py
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "9051")))
