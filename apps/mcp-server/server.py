from datetime import date
import os
import time
import httpx
import asyncio
import re
import unicodedata
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


def _photo_norm(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _photo_title_matches(name: str, title: str) -> bool:
    """
    Evita fotos de personas equivocadas.
    Exige nombre + apellido principal en el título del artículo/archivo.
    """
    n = [x for x in _photo_norm(name).split() if len(x) > 1]
    t = _photo_norm(title)
    if not n or not t:
        return False

    first = n[0]
    # Los rankings pueden traer apellidos compuestos; cualquiera de los tokens
    # posteriores puede actuar como apellido identificador.
    surnames = n[1:] or n
    return first in t and any(s in t for s in surnames)


async def _wiki_player_photo(name: str, wiki_title: str = "") -> str | None:
    """
    Resuelve una foto pública de la jugadora desde Wikipedia/Wikimedia.
    Se ejecuta en backend, no en el navegador, y queda cacheado.
    """
    cache_key = f"player-photo:{_photo_norm(name)}:{_photo_norm(wiki_title)}"
    cached = cache_get(cache_key, 7 * 24 * 60 * 60)
    if cached is not None:
        return cached or None

    timeout = httpx.Timeout(10.0)
    headers = {"User-Agent": "PadelFemMCP/1.0 (player-photo resolver)"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
        # 1. Artículo exacto en Wikipedia ES/EN.
        exact_titles = [x for x in {
            wiki_title,
            name,
            name.replace(" ", "_"),
        } if x]

        for base in ("https://es.wikipedia.org/w/api.php", "https://en.wikipedia.org/w/api.php"):
            for title in exact_titles:
                try:
                    resp = await client.get(base, params={
                        "action": "query",
                        "redirects": "1",
                        "titles": title,
                        "prop": "pageimages",
                        "pithumbsize": "1000",
                        "format": "json",
                    })
                    data = resp.json()
                    page = next(iter((data.get("query", {}).get("pages", {}) or {}).values()), {})
                    src = (page.get("thumbnail") or {}).get("source")
                    page_title = page.get("title", "")
                    if src and (_photo_title_matches(name, page_title) or _photo_norm(page_title) == _photo_norm(title)):
                        cache_set(cache_key, src)
                        print(f"  photo: {name} -> Wikipedia ({page_title})")
                        return src
                except Exception:
                    pass

        # 2. Buscar artículo por nombre exacto.
        for base in ("https://es.wikipedia.org/w/api.php", "https://en.wikipedia.org/w/api.php"):
            try:
                resp = await client.get(base, params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f'"{name}" padel',
                    "gsrlimit": "8",
                    "prop": "pageimages",
                    "pithumbsize": "1000",
                    "format": "json",
                })
                pages = list((resp.json().get("query", {}).get("pages", {}) or {}).values())
                for page in pages:
                    src = (page.get("thumbnail") or {}).get("source")
                    if src and _photo_title_matches(name, page.get("title", "")):
                        cache_set(cache_key, src)
                        print(f"  photo: {name} -> search ({page.get('title','')})")
                        return src
            except Exception:
                pass

        # 3. Wikimedia Commons: útil para jugadoras sin artículo con pageimage.
        try:
            resp = await client.get("https://commons.wikimedia.org/w/api.php", params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f'"{name}"',
                "gsrnamespace": "6",
                "gsrlimit": "12",
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": "1000",
                "format": "json",
            })
            pages = list((resp.json().get("query", {}).get("pages", {}) or {}).values())
            for page in pages:
                title = page.get("title", "")
                info = (page.get("imageinfo") or [{}])[0]
                src = info.get("thumburl") or info.get("url")
                if src and _photo_title_matches(name, title):
                    cache_set(cache_key, src)
                    print(f"  photo: {name} -> Commons ({title})")
                    return src
        except Exception:
            pass

    # Cache negative only 30 min instead of a week.
    _cache[cache_key] = {"ts": time.time() - (7 * 24 * 60 * 60) + (30 * 60), "data": ""}
    print(f"  photo: {name} -> no encontrada")
    return None


@app.get("/api/player-photo")
async def api_player_photo(name: str, wiki_title: str = ""):
    """
    Devuelve una URL local/proxy para la foto correcta de una jugadora.
    El frontend nunca necesita hablar directamente con Wikimedia.
    """
    if not name.strip():
        return JSONResponse({"photo": None}, status_code=400)

    src = await _wiki_player_photo(name.strip(), wiki_title.strip())
    if not src:
        return {"photo": None}

    return {"photo": src}


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
    return {"version": "v55-server-side-player-photos-2026-08-08"}

@app.get("/")
async def index():
    return FileResponse(
        "static/index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-App-Version": "v55-server-side-player-photos-2026-08-08",
        },
    )

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
