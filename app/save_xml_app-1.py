#!/usr/bin/env python3
# app.py — fetch AP Elections XML to results.xml with a terminal progress bar

import os
import sys
import time
import math
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =========================
# CONFIG
# =========================
AP_URL = "https://api.ap.org/v3/elections/2024-11-05?level=ru&resultstype=b"

# Use same env-var pattern as app_hub.py (x-api-key header)
AP_API_KEY = os.getenv("AP_API_KEY", os.getenv("AP_KEY", "ziwbrof5ondu4qq67ej4cz73to"))

# Output path: project root (same folder as this file)
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(APP_ROOT, "results.xml")

# =========================
# HTTP Session with retries
# =========================
HTTP = requests.Session()
HTTP.trust_env = False
retries = Retry(
    total=5,
    connect=5,
    read=5,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET"]),
    raise_on_status=False,
)
HTTP.mount("http://", HTTPAdapter(max_retries=retries))
HTTP.mount("https://", HTTPAdapter(max_retries=retries))
HTTP.headers.update({
    "User-Agent": "ResultsXMLSaver/1.0",
    "x-api-key": AP_API_KEY,
    "Accept": "application/xml, text/xml;q=0.9, */*;q=0.8",
})

# =========================
# Progress helpers
# =========================
def format_bytes(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n/1024 if unit=='KB' else n/(1024**2) if unit=='MB' else n/(1024**3):.2f} {unit}"
        n /= 1024
    return f"{n:.2f} GB"

def draw_bar(downloaded: int, total: int | None, start_ts: float) -> None:
    elapsed = max(time.time() - start_ts, 1e-6)
    rate = downloaded / elapsed  # bytes/sec

    if total and total > 0:
        width = 40
        frac = min(downloaded / total, 1.0)
        filled = int(width * frac)
        bar = "█" * filled + "░" * (width - filled)
        pct = f"{frac*100:5.1f}%"
        info = f"{format_bytes(downloaded)} / {format_bytes(total)} @ {format_bytes(int(rate))}/s"
        line = f"\r[{bar}] {pct}  {info}"
    else:
        # Unknown content-length: spinner + bytes + rate
        spinner = "|/-\\"
        ch = spinner[int(elapsed * 10) % len(spinner)]
        info = f"{format_bytes(downloaded)} @ {format_bytes(int(rate))}/s"
        line = f"\r{ch}  {info}"

    sys.stdout.write(line)
    sys.stdout.flush()

# =========================
# Main
# =========================
def main() -> int:
    print(f"Fetching:\n  {AP_URL}")
    if not AP_API_KEY or AP_API_KEY == "123abc":
        print("WARNING: Using placeholder API key. Set AP_API_KEY or AP_KEY in your environment.\n")

    try:
        with HTTP.get(AP_URL, stream=True, timeout=60) as r:
            if r.status_code != 200:
                snippet = r.text[:300] if r.text else ""
                print(f"Error: HTTP {r.status_code}\n{snippet}")
                return 1

            total = None
            if "Content-Length" in r.headers:
                try:
                    total = int(r.headers.get("Content-Length", "0"))
                except ValueError:
                    total = None

            # write stream to file with progress
            chunk_size = 1024 * 64  # 64KB
            downloaded = 0
            start = time.time()

            # Ensure we got XML-ish content
            # If server doesn’t send content-type correctly, we still accept it as long as body looks like XML
            peek = r.raw.read(min(chunk_size, 8192), decode_content=True)
            if not peek:
                print("Error: empty response body.")
                return 1
            head = peek.lstrip()[:100].decode("utf-8", errors="ignore").lower()
            if not head.startswith("<"):
                print("Warning: response does not appear to start with XML. Proceeding anyway.")

            # Open file and write the already-peeked bytes first
            with open(OUT_PATH, "wb") as f:
                f.write(peek)
                downloaded += len(peek)
                draw_bar(downloaded, total, start)

                # Continue streaming the rest
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    draw_bar(downloaded, total, start)

            # finalize progress line
            draw_bar(downloaded, total, start)
            sys.stdout.write("\n")
            print(f"Saved to: {OUT_PATH}  ({format_bytes(downloaded)})")
            return 0

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nCanceled.")
        return 130

if __name__ == "__main__":
    sys.exit(main())
