from datetime import date
import os
import time
import httpx
import asyncio
import re
import unicodedata
from urllib.parse import quote
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response, JSONResponse
from fastmcp import FastMCP
from bs4 import BeautifulSoup
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


def _photo_aliases(name: str, wiki_title: str = "") -> list[str]:
    """
    Ranking sources often use complete civil names:
      "Gemma Triay Pons" -> article "Gemma Triay"
      "Marta Ortega Gallego" -> article "Marta Ortega"

    Generate conservative aliases without hardcoding each player.
    """
    raw = re.sub(r"\s+", " ", (name or "").strip())
    parts = raw.split()

    aliases = []

    # Prioridad absoluta: nombre + primer apellido.
    # Es la forma pública/deportiva más habitual.
    if len(parts) >= 2:
        aliases.append(" ".join(parts[:2]))

    if raw:
        aliases.append(raw)

    if len(parts) >= 3:
        aliases.append(" ".join(parts[:3]))

    # Explicit title from Sponsors dataset if available.
    if wiki_title:
        aliases.append(wiki_title.replace("_", " "))

    # Accentless forms are useful for APIs whose index is inconsistent.
    aliases += [
        unicodedata.normalize("NFD", a).encode("ascii", "ignore").decode("ascii")
        for a in list(aliases)
    ]

    out = []
    seen = set()
    for a in aliases:
        a = re.sub(r"\s+", " ", a).strip()
        key = _photo_norm(a)
        if a and key not in seen:
            seen.add(key)
            out.append(a)
    return out


def _photo_title_matches(alias: str, title: str) -> bool:
    a = [x for x in _photo_norm(alias).split() if len(x) > 1]
    t = _photo_norm(title)
    if len(a) < 2 or not t:
        return False

    # Basta con que coincidan nombre + primer apellido.
    # Ej.: "Gemma Triay Pons" encaja con "Gemma Triay".
    first_name = a[0]
    first_surname = a[1]
    return first_name in t and first_surname in t


async def _fip_player_photo(name: str) -> str | None:
    """
    Fuente principal para fotos: perfil OFICIAL FIP.
    Los slugs oficiales usan normalmente el nombre civil completo:
      Gemma Triay Pons -> /player/gemma-triay-pons/
      Delfina Brea Senesi -> /player/delfina-brea-senesi/
    """
    slug = "-".join(_photo_norm(name).split())
    if not slug:
        return None

    urls = [
        f"https://www.padelfip.com/player/{slug}/",
        f"https://www.padelfip.com/es/player/{slug}/",
    ]

    headers = {"User-Agent": "Mozilla/5.0 PadelFemMCP/1.0"}
    timeout = httpx.Timeout(12.0)

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
        for profile_url in urls:
            try:
                resp = await client.get(profile_url)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")

                # Prefer image elements actually associated with the player's name.
                first, *rest = _photo_norm(name).split()
                surname = rest[0] if rest else ""

                candidates = []
                for img in soup.find_all("img"):
                    src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                    if not src:
                        continue
                    alt = _photo_norm(img.get("alt",""))
                    classes = " ".join(img.get("class",[])).lower()
                    parent_classes = " ".join(img.parent.get("class",[])).lower() if img.parent else ""

                    score = 0
                    if first and first in alt: score += 4
                    if surname and surname in alt: score += 5
                    if "player" in classes or "player" in parent_classes: score += 3
                    if "profile" in classes or "profile" in parent_classes: score += 3
                    if "avatar" in classes or "avatar" in parent_classes: score += 2

                    # Ignore obvious flags/logos/icons.
                    low_src = src.lower()
                    if any(x in low_src for x in ("flag","logo","icon","premier-padel","cupra")):
                        score -= 6

                    if score > 0:
                        if src.startswith("//"):
                            src = "https:" + src
                        elif src.startswith("/"):
                            src = "https://www.padelfip.com" + src
                        candidates.append((score, src, alt))

                if candidates:
                    candidates.sort(key=lambda x:x[0], reverse=True)
                    src=candidates[0][1]
                    print(f"  photo: {name} -> FIP profile ({profile_url})")
                    return src

                # Fallback: OpenGraph image only if it isn't an obvious generic/site image.
                og=soup.find("meta", attrs={"property":"og:image"})
                src=og.get("content") if og else None
                if src:
                    low=src.lower()
                    if not any(x in low for x in ("logo","default","generic","placeholder")):
                        print(f"  photo: {name} -> FIP og:image ({profile_url})")
                        return src

            except Exception:
                pass

    return None


async def _wiki_player_photo(name: str, wiki_title: str = "") -> str | None:
    """
    Resolver en este orden:
      1. Perfil oficial FIP (mejor fuente para jugadoras de pádel)
      2. Wikipedia ES/EN
      3. Wikimedia Commons

    Google Images no se scrapea: es frágil y requiere API/credenciales para uso
    estable en producción.
    """
    cache_key = f"player-photo-v58:{_photo_norm(name)}:{_photo_norm(wiki_title)}"
    cached = cache_get(cache_key, 7 * 24 * 60 * 60)
    if cached is not None:
        return cached or None

    # 1) Official FIP profile.
    fip_src = await _fip_player_photo(name)
    if fip_src:
        cache_set(cache_key, fip_src)
        return fip_src

    aliases = _photo_aliases(name, wiki_title)
    timeout = httpx.Timeout(12.0)
    headers = {"User-Agent": "PadelFemMCP/1.0 (player-photo resolver)"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
        # 2) Exact / redirected Wikipedia articles.
        for base in ("https://es.wikipedia.org/w/api.php", "https://en.wikipedia.org/w/api.php"):
            for alias in aliases:
                try:
                    resp = await client.get(base, params={
                        "action": "query",
                        "redirects": "1",
                        "titles": alias,
                        "prop": "pageimages",
                        "piprop": "thumbnail|original",
                        "pithumbsize": "1000",
                        "format": "json",
                    })
                    page = next(iter((resp.json().get("query", {}).get("pages", {}) or {}).values()), {})
                    src = (page.get("thumbnail") or {}).get("source") or (page.get("original") or {}).get("source")
                    title = page.get("title", "")
                    if src and any(_photo_title_matches(a, title) for a in aliases):
                        cache_set(cache_key, src)
                        print(f"  photo: {name} -> Wikipedia ({title}) alias={alias}")
                        return src
                except Exception:
                    pass

        # 3) Wikipedia searches.
        for base in ("https://es.wikipedia.org/w/api.php", "https://en.wikipedia.org/w/api.php"):
            for alias in aliases[:4]:
                try:
                    resp = await client.get(base, params={
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": f"{alias} padel",
                        "gsrlimit": "10",
                        "prop": "pageimages",
                        "piprop": "thumbnail|original",
                        "pithumbsize": "1000",
                        "format": "json",
                    })
                    pages = list((resp.json().get("query", {}).get("pages", {}) or {}).values())
                    for page in pages:
                        title = page.get("title", "")
                        src = (page.get("thumbnail") or {}).get("source") or (page.get("original") or {}).get("source")
                        if src and _photo_title_matches(alias, title):
                            cache_set(cache_key, src)
                            print(f"  photo: {name} -> search ({title}) alias={alias}")
                            return src
                except Exception:
                    pass

        # 4) Wikimedia Commons.
        for alias in aliases[:4]:
            try:
                resp = await client.get("https://commons.wikimedia.org/w/api.php", params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": alias,
                    "gsrnamespace": "6",
                    "gsrlimit": "20",
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
                    if src and _photo_title_matches(alias, title):
                        cache_set(cache_key, src)
                        print(f"  photo: {name} -> Commons ({title}) alias={alias}")
                        return src
            except Exception:
                pass

    print(f"  photo: {name} -> no encontrada aliases={aliases[:4]}")
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
    return {"version": "v59-ranking-photos-padelspeak-first-2026-08-08"}

@app.get("/")
async def index():
    return FileResponse(
        "static/index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-App-Version": "v59-ranking-photos-padelspeak-first-2026-08-08",
        },
    )

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
