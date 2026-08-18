import os
from flask import Flask, render_template_string
from flask_cors import CORS

# Serve index.html with API base injected. Keep origin on :9053 to satisfy CORS in your API.
PORT = int(os.getenv("PORT", "9053"))
API_BASE = os.getenv("API_BASE", "http://localhost:5037")  # where your uploaded api.py is running

INDEX_HTML = open("index.html", "r", encoding="utf-8").read()

app = Flask(__name__)

@app.route("/")
def index():
    # Simple Jinja-style replace for {{ API_BASE }} token in index.html
    html = INDEX_HTML.replace("{{ API_BASE }}", API_BASE)
    return render_template_string(html)

if __name__ == "__main__":
    # Run on localhost:9053 so API CORS allowlist matches
    app.run(host="0.0.0.0", port=PORT, debug=True)
