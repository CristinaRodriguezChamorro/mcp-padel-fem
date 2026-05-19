import os
import time
import httpx
import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastmcp import FastMCP
from tools.news import get_latest_news
from tools.live_data import get_ranking_live, get_calendar_live

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
    data = await get_latest_news()
    cache_set("news", data)
    return data


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

@app.get("/")
async def index():
    return FileResponse("static/index.html", media_type="text/html")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
