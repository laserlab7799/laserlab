# app.py — minimal static server for the dropdown viewer
import os
from fastapi import FastAPI
from fastapi.responses import Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Election Dropdown Viewer")

# If you serve UI from a different host than the API, set CORS accordingly.
# By default we allow same-origin (no CORS needed). Uncomment and edit if needed:
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

@app.get("/ping", response_class=PlainTextResponse)
def ping():
    return "pong"

# Serve the index.html from the current directory
@app.get("/", response_class=Response)
def read_index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/html")
    except FileNotFoundError:
        return Response("<h3>Put index.html next to app.py</h3>", media_type="text/html")

# Optionally serve a /static folder if you add assets later
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5080")))
