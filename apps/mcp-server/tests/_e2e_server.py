"""
Ayudante de test_e2e_process.py (no es un test): arranca el servidor REAL (uvicorn + tarea de Wikipedia)
con Wikipedia simulada y consulta sus endpoints. Se ejecuta en un proceso aparte porque el MCP solo
puede arrancar una vez por proceso.   Uso:  python tests/_e2e_server.py ok|down
"""
import json
import os
os.environ.setdefault("MCP_TOOLS", "extended")   # los tests usan todas las herramientas; el valor por defecto son 3
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_ingest import page      # (este import fija WIKIPEDIA_INGEST=off; se corrige justo debajo)

mode = sys.argv[1]
data = tempfile.mkdtemp(prefix="padel-e2e-")
shutil.copytree(ROOT / "data", data, dirs_exist_ok=True)
os.environ.update(DATA_DIR=data, WIKIPEDIA_INGEST="on", INGEST_START_DELAY="0",
                  MCP_RATE_PER_MIN="100000", MCP_GLOBAL_RATE_PER_MIN="100000")
os.environ.pop("MCP_AUTH", None)
os.environ.pop("MCP_TOKEN", None)

import httpx
import uvicorn
from tools import wikipedia_source as ws

import server      # se importa ANTES de sustituir el cliente HTTP (groq hereda de httpx.AsyncClient al importarse)

real_client = httpx.AsyncClient
asked = []


def handler(request: httpx.Request) -> httpx.Response:
    asked.append(f"{request.url.host}{request.url.path}")
    if mode == "down":
        raise httpx.ConnectError("sin red")
    return httpx.Response(200, json=page(year=date.today().year))


ws.httpx.AsyncClient = lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
while not srv.started:
    time.sleep(0.05)


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8")) if "json" in r.headers.get("content-type", "") else None


deadline = time.time() + 15
while time.time() < deadline:
    if get("/api/data-status")[1]["ingesta"].get("ultimo_intento"):
        break
    time.sleep(0.1)
time.sleep(0.3)
server._cache.clear()

out = {"asked": asked}
out["status"], out["data_status"] = get("/api/data-status")
_, ranking = get("/api/ranking")
out["ranking"] = [(r["pos"], r["name"], r["pts"]) for r in ranking[:2]]
out["ranking_len"] = len(ranking)
_, cal = get("/api/calendar")
out["calendar"] = [t["name"] for t in cal][:3]
out["live"] = get("/api/live")[1]
out["codes"] = {p: get(p)[0] for p in ("/", "/api/version", "/api/ranking", "/api/calendar", "/api/live")}
print("RESULT:" + json.dumps(out, ensure_ascii=False))
srv.should_exit = True
time.sleep(0.5)
