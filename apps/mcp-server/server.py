from datetime import date
import os
import time
import httpx
import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response, JSONResponse
from fastmcp import FastMCP
from tools.news import get_latest_news
from tools.live_data import get_ranking_live, get_calendar_live, get_tournament_now

app = FastAPI(title="Padel Fem MCP")
mcp = FastMCP("PadelFemMCP")

# ── CACHÉ ─────────────────────────────────────────────────────────────────────
_cache: dict = {}

def cache_get(key: str, ttl: int):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["data"]
    return None

def cache_set(key: str, data):
    _cache[key] = {"ts": time.time(), "data": data}


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/news")
async def api_news():
    cached = cache_get("news", 30 * 60)
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
    cached = cache_get("ranking", 6 * 60 * 60)
    if cached:
        return cached
    data = await get_ranking_live()
    cache_set("ranking", data)
    return data


@app.get("/api/calendar")
async def api_calendar():
    cached = cache_get("calendar", 6 * 60 * 60)
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
    cached = cache_get(cache_key, 3 * 60)
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


@app.get("/api/live-debug")
async def api_live_debug(gender: str = "female"):
    """Diagnóstico mínimo para comprobar qué ve la lógica de En juego."""
    gender = (gender or "female").strip().lower()
    if gender == "women":
        gender = "female"
    if gender not in {"female", "male"}:
        gender = "female"
    data = await get_tournament_now(gender=gender)
    return {
        "active": data.get("active", False),
        "error": data.get("error", False),
        "message": data.get("message", ""),
        "name": data.get("name", ""),
        "place": data.get("place", ""),
        "dates": data.get("dates", ""),
        "gender": data.get("gender", gender),
        "results_count": len(data.get("results", [])),
        "has_next_match": bool(data.get("next_match")),
        "source_url": data.get("source_url", ""),
        "extraction": data.get("_debug", {}),
    }


@app.get("/api/photo")
async def api_photo(url: str):
    """Proxy para imágenes de Wikimedia — evita hotlink block."""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "PadelFemMCP/1.0 httpx/0.27",
                    "Referer": "https://commons.wikimedia.org/",
                },
                timeout=10,
            )
            ct = resp.headers.get("content-type", "image/jpeg")
            return StreamingResponse(
                iter([resp.content]),
                media_type=ct,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        return Response(status_code=404)


# ── MCP TOOL ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def resumen_diario_padel_femenino():
    """Devuelve el resumen diario de noticias del circuito profesional femenino de pádel."""
    cached = cache_get("news", 30 * 60)
    if cached:
        return cached
    data = await get_latest_news()
    cache_set("news", data)
    return data


# ── STATIC ────────────────────────────────────────────────────────────────────

@app.get("/api/version")
async def api_version():
    return {"version": "v35-live-winner-anchor-filter-later-2026-08-08"}

@app.get("/")
async def index():
    return FileResponse(
        "static/index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-App-Version": "v35-live-winner-anchor-filter-later-2026-08-08",
        },
    )

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
