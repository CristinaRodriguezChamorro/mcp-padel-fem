from contextlib import asynccontextmanager, suppress
from datetime import date
import os
import time
import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from tools.news import get_latest_news, rss_status, NEWS_CACHE_SECONDS
from tools import ingest, padelapi_live
from tools.local_data import (get_ranking_live, get_calendar_live, get_tournament_now, get_recent_finals,
                              dataset_status, data_age_seconds)
from padel_mcp.tools import build_mcp, gate_from_env

# Versión que está corriendo: se ve en /api/version, en /api/data-status y al pie de la web, para saber
# siempre si el despliegue es el último.
APP_VERSION = "2026-10-23-v8-revision-final"

# ── CACHÉ ─────────────────────────────────────────────────────────────────────
_cache: dict = {}

def cache_get(key: str, ttl: int):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["data"]
    return None

def cache_set(key: str, data):
    _cache[key] = {"ts": time.time(), "data": data}

def cache_age(key: str):
    entry = _cache.get(key)
    return int(time.time() - entry["ts"]) if entry else None


# ── MCP (comparte cachés y fuentes con la web) ────────────────────────────────
# Mismas claves y duraciones de caché que los endpoints /api/*: web y MCP
# nunca duplican peticiones a las fuentes externas.

# Los datos locales son archivos JSON: leerlos es barato, y con una caché corta un cambio se ve pronto.
LOCAL_TTL = 60

async def _provide(key: str, ttl: int, fetch, dataset: str | None = None):
    data = cache_get(key, ttl)
    if data is None:
        data = await fetch()
        cache_set(key, data)
    out = {"data": data, "age_s": cache_age(key), "fallback": None}
    if dataset:
        status = dataset_status().get(dataset, {})
        out["tipo"] = status.get("tipo")
        out["fuente"] = status.get("fuente")
        out["licencia"] = status.get("licencia")
        out["desactualizado"] = status.get("desactualizado")
        out["age_s"] = data_age_seconds(dataset)          # antigüedad real de los datos, no de la caché
        if status.get("tipo") == "ejemplo":
            out["fallback"] = True
            out["aviso"] = status.get("nota") or "Datos de ejemplo sin verificar."
        elif not status.get("disponible"):
            out["aviso"] = status.get("nota")
    return out

async def _status_provider():
    return {"data": {"datasets": dataset_status(), "ingesta": ingest.ingest_status(), "noticias": rss_status()},
            "age_s": 0, "fallback": None}

mcp = build_mcp({
    "padelapi": padelapi_live.service(),
    "ranking":  lambda: _provide("ranking", LOCAL_TTL, get_ranking_live, "ranking"),
    "calendar": lambda: _provide("calendar", LOCAL_TTL, get_calendar_live, "calendario"),
    "live":     lambda: _provide("live:female", LOCAL_TTL, lambda: get_tournament_now(gender="female"), "calendario"),
    "finals":   lambda: _provide("finals", LOCAL_TTL, get_recent_finals, "calendario"),
    "news":     lambda: _provide("news", NEWS_CACHE_SECONDS, get_latest_news),
    "status":   _status_provider,
})
# stateless_http: cada petición es independiente (no hace falta afinidad de sesión en Railway).
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)

def make_lifespan(mcp_application):
    """Arranca el MCP y, si no está apagada, la tarea que refresca ranking y calendario desde Wikipedia."""
    @asynccontextmanager
    async def _lifespan(app):
        task = None
        async with mcp_application.lifespan(app):
            if ingest.enabled():
                print(f"  Ingesta: ranking y calendario desde Wikipedia cada {ingest.interval_hours():g} h")
                task = asyncio.create_task(ingest.run_forever())
            else:
                print("  Ingesta desactivada (WIKIPEDIA_INGEST=off): se usan los datos de data/ tal cual")
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
    return _lifespan


lifespan = make_lifespan(mcp_app)
app = FastAPI(title="Padel Fem MCP", lifespan=lifespan)


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/news")
async def api_news():
    cached = cache_get("news", NEWS_CACHE_SECONDS)
    if cached:
        return cached

    try:
        data = await get_latest_news()
        cache_set("news", data)
        return data
    except Exception as exc:
        print(f"  /api/news fatal error: {type(exc).__name__}: {exc}")
        return {
            "date": date.today().isoformat(),
            "resumen_diario": [],
            "error": True,
            "message": "No se pudieron actualizar las noticias",
        }


@app.get("/api/ranking")
async def api_ranking():
    cached = cache_get("ranking", LOCAL_TTL)
    if cached:
        return cached
    data = await get_ranking_live()
    cache_set("ranking", data)
    return data


@app.get("/api/calendar")
async def api_calendar():
    cached = cache_get("calendar", LOCAL_TTL)
    if cached:
        return cached
    data = await get_calendar_live()
    cache_set("calendar", data)
    return data


@app.get("/api/live")
async def api_live(gender: str = "female"):
    # Filtro de género EXCLUSIVO de la pestaña "En juego".
    gender = (gender or "female").strip().lower()
    if gender == "women":
        gender = "female"
    if gender not in {"female", "male"}:
        gender = "female"

    cache_key = f"live:{gender}"
    cached = cache_get(cache_key, LOCAL_TTL)
    if cached:
        return cached

    try:
        data = await get_tournament_now(gender=gender)
        cache_set(cache_key, data)
        return data
    except Exception as exc:
        # Do not leave the frontend with a generic HTTP 500.
        print(f"  /api/live fatal error: {type(exc).__name__}: {exc}")
        return {
            "active": False,
            "error": True,
            "message": "No se pudo actualizar En juego",
            "watch": [],
            "results": [],
            "next_match": None,
            "gender": gender,
        }


@app.get("/api/data-status")
async def api_data_status():
    """De dónde sale cada dato, qué tan al día está, el resultado de la última actualización y si las noticias están activas."""
    return {"version": APP_VERSION, "datasets": dataset_status(), "ingesta": ingest.ingest_status(), "noticias": rss_status(),
            "consultas_en_directo": padelapi_live.service().status()}


# ── MCP: endpoint /ai/mcp ─────────────────────────────────────────────────────
# El acceso se decide con MCP_AUTH (github | token | open); ver gate_from_env en padel_mcp/tools.py.
app.mount("/ai", gate_from_env(mcp_app))


# ── STATIC ────────────────────────────────────────────────────────────────────

@app.get("/api/version")
async def api_version():
    return {"version": APP_VERSION}

@app.get("/")
async def index():
    return FileResponse(
        "static/index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-App-Version": APP_VERSION,
        },
    )

@app.get("/como-funciona")
async def como_funciona():
    """Explicación de la arquitectura en lenguaje llano, para quien no sabe de informática."""
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "como-funciona.html"),
                        media_type="text/html")


if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
