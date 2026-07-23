from flask import Flask, send_from_directory
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=None)

@app.route("/")
def index():
    return send_from_directory(ROOT, "index_box_only.html")

@app.route("/scripts/<path:filename>")
def scripts(filename):
    return send_from_directory(os.path.join(ROOT, "..", "scripts"), filename)

@app.route("/win_prob/<path:filename>")
def win_prob(filename):
    return send_from_directory(os.path.join(ROOT, "..", "win_prob"), filename)

if __name__ == "__main__":
    print("ROOT:", ROOT)
    print("Scripts folder:", os.path.join(ROOT, "scripts"))
    print("▶ Box app running at http://localhost:9060")
    app.run(port=9060, debug=True)
