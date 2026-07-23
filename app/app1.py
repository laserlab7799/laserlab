from flask import Flask, send_from_directory
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
WIN_PROB_DIR = os.path.abspath(
    os.path.join(BASE_DIR, "..", "win_prob")
)

app = Flask(__name__, static_folder=".", static_url_path="")

@app.route("/")
def home():
    return send_from_directory(".", "index_chart_only.html")

@app.route("/win_prob/<path:filename>")
def win_prob(filename):
    return send_from_directory(WIN_PROB_DIR, filename)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9053, debug=True)
