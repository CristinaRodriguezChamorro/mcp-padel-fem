from contextlib import asynccontextmanager, suppress
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
from tools.news import get_latest_news, rss_status, NEWS_CACHE_SECONDS
from tools.http_policy import USER_AGENT
from html import escape
from tools import ingest, photos
from tools.local_data import (get_ranking_live, get_calendar_live, get_tournament_now, get_recent_finals,
                              dataset_status, data_age_seconds)
from padel_mcp.tools import build_mcp, gate_from_env

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
    return {"data": {"datasets": dataset_status(), "ingesta": ingest.ingest_status(), "noticias": rss_status(),
                     "fotos": _photo_status()},
            "age_s": 0, "fallback": None}

mcp = build_mcp({
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
    return {"datasets": dataset_status(), "ingesta": ingest.ingest_status(), "noticias": rss_status(),
            "fotos": _photo_status()}


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


async def _wiki_player_photo(name: str, wiki_title: str = "") -> str | None:
    """
    Resolver en este orden, usando solo las APIs públicas de Wikimedia:
      1. Wikipedia ES/EN (artículo exacto o redirigido)
      2. Búsqueda en Wikipedia
      3. Wikimedia Commons

    No se lee ninguna otra web. Ojo: las fotos de Wikipedia/Commons tienen licencias que
    suelen exigir atribución: por eso las fotos están apagadas por defecto (PLAYER_PHOTOS=on las activa).
    """
    cache_key = f"player-photo-v59:{_photo_norm(name)}:{_photo_norm(wiki_title)}"
    cached = cache_get(cache_key, 7 * 24 * 60 * 60)
    if cached is not None:
        return cached or None
    if cache_get(cache_key + ":none", 6 * 60 * 60) is not None:      # sin foto: no se vuelve a preguntar hasta dentro de 6 h
        return None

    aliases = _photo_aliases(name, wiki_title)
    timeout = httpx.Timeout(12.0)
    headers = {"User-Agent": USER_AGENT}

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
                        "piprop": "thumbnail|original|name",
                        "pithumbsize": "1000",
                        "format": "json",
                    })
                    page = next(iter((resp.json().get("query", {}).get("pages", {}) or {}).values()), {})
                    src = (page.get("thumbnail") or {}).get("source") or (page.get("original") or {}).get("source")
                    title = page.get("title", "")
                    if src and page.get("pageimage") and any(_photo_title_matches(a, title) for a in aliases):
                        cache_set(f"photo-file:{src}", f"File:{page['pageimage']}")   # sin archivo no hay crédito
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
                        "piprop": "thumbnail|original|name",
                        "pithumbsize": "1000",
                        "format": "json",
                    })
                    pages = list((resp.json().get("query", {}).get("pages", {}) or {}).values())
                    for page in pages:
                        title = page.get("title", "")
                        src = (page.get("thumbnail") or {}).get("source") or (page.get("original") or {}).get("source")
                        if src and page.get("pageimage") and _photo_title_matches(alias, title):
                            cache_set(f"photo-file:{src}", f"File:{page['pageimage']}")
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
                        cache_set(f"photo-file:{src}", title)
                        cache_set(cache_key, src)
                        print(f"  photo: {name} -> Commons ({title}) alias={alias}")
                        return src
            except Exception:
                pass

    print(f"  photo: {name} -> no encontrada aliases={aliases[:4]}")
    cache_set(cache_key + ":none", True)
    return None


# ── FOTOS: solo Wikimedia, y solo con su crédito ──────────────────────────────
# Las fotos de Wikimedia son de licencia libre, pero casi todas exigen citar autor y licencia.
# Por eso una foto SOLO se sirve si se ha podido comprobar su crédito (tools/photos.py): ante la
# duda, sin foto y la web muestra las iniciales. Se apagan del todo con PLAYER_PHOTOS=off.
# Además solo se descargan dominios de Wikimedia/Wikipedia (antes /api/photo era un proxy abierto).
_WIKIMEDIA_SUFFIXES = ("wikimedia.org", "wikipedia.org")
_CREDIT_OK_TTL = 7 * 24 * 60 * 60
_CREDIT_NO_TTL = 6 * 60 * 60
_photo_stats = {"con_credito": 0, "sin_credito": 0, "motivos": {}}


def _photos_enabled() -> bool:
    """Activas por defecto (con crédito obligatorio). PLAYER_PHOTOS=off/0/false/no las apaga."""
    return os.environ.get("PLAYER_PHOTOS", "").strip().lower() not in ("off", "0", "false", "no")


def _note_photo(ok: bool, reason: str = "") -> None:
    _photo_stats["con_credito" if ok else "sin_credito"] += 1
    if reason:
        reasons = _photo_stats["motivos"]
        reasons[reason] = reasons.get(reason, 0) + 1
        if len(reasons) > 30:
            reasons.pop(next(iter(reasons)))


def _photo_status() -> dict:
    return {"activas": _photos_enabled(), **{k: (dict(v) if isinstance(v, dict) else v) for k, v in _photo_stats.items()}}


def _is_wikimedia_host(host) -> bool:
    host = (host or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _WIKIMEDIA_SUFFIXES)


async def _resolve_photo(name: str, wiki_title: str = ""):
    """(url de la foto, crédito) si la foto existe Y su crédito está comprobado; si no, (None, None)."""
    src = await _wiki_player_photo(name, wiki_title)
    if not src:
        _note_photo(False, "sin foto en Wikimedia")
        return None, None
    file_title = cache_get(f"photo-file:{src}", _CREDIT_OK_TTL)
    if not file_title:
        _note_photo(False, "no se sabe de qué archivo es la foto")
        return None, None
    credit = cache_get(f"photo-credit-ok:{file_title}", _CREDIT_OK_TTL)
    if credit is None:
        cached_no = cache_get(f"photo-credit-no:{file_title}", _CREDIT_NO_TTL)
        if cached_no is not None:
            _note_photo(False, cached_no)
            return None, None
        credit, reason = await photos.fetch_credit(file_title)
        if credit is None:
            cache_set(f"photo-credit-no:{file_title}", reason)
            _note_photo(False, reason)
            return None, None
        cache_set(f"photo-credit-ok:{file_title}", credit)
    cache_set(f"photo-src-ok:{src}", {"jugadora": name, **credit})     # lo que se ha servido, con su crédito
    _note_photo(True)
    return src, credit


async def _download_wikimedia_image(src: str):
    """Descarga una imagen solo si es https y de Wikimedia/Wikipedia. Devuelve (bytes, tipo) o None."""
    try:
        parsed = httpx.URL(src)
        if parsed.scheme != "https" or not _is_wikimedia_host(parsed.host):
            print(f"  photo bloqueada (host no permitido): {parsed.host}")
            return None
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(src, headers={
                "User-Agent": USER_AGENT,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            })
        if not _is_wikimedia_host(resp.url.host):          # una redirección no puede sacarnos de Wikimedia
            return None
        ct = (resp.headers.get("content-type") or "").lower()
        if resp.status_code != 200 or not ct.startswith("image/") or not resp.content:
            print(f"  photo fallida: status={resp.status_code} type={ct or 'none'}")
            return None
        return resp.content, ct.split(";")[0]
    except Exception as exc:
        print(f"  photo excepción: {type(exc).__name__}: {exc}")
        return None


def _image_response(result):
    if not result:
        return Response(status_code=404)
    content, media_type = result
    return Response(content=content, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=86400"})


async def _credited_image(name: str, wiki_title: str):
    if not _photos_enabled() or not name.strip():
        return Response(status_code=404)
    src, credit = await _resolve_photo(name.strip(), wiki_title.strip())
    if not src:
        return Response(status_code=404)
    response = _image_response(await _download_wikimedia_image(src))
    if response.status_code == 200:
        response.headers["X-Photo-Credit"] = f"{credit['autor']} | {credit['licencia']}".encode("ascii", "ignore").decode()
    return response


@app.get("/api/sponsor-photo-image")
async def api_sponsor_photo_image(name: str, wiki_title: str = ""):
    """Foto de una jugadora para la pestaña Sponsors (solo Wikimedia y solo con crédito comprobado)."""
    return await _credited_image(name, wiki_title)


@app.get("/api/player-photo-image")
async def api_player_photo_image(name: str, wiki_title: str = ""):
    """Foto de una jugadora (solo Wikimedia y solo con crédito comprobado)."""
    return await _credited_image(name, wiki_title)


@app.get("/api/player-photo")
async def api_player_photo(name: str, wiki_title: str = ""):
    """URL de la foto en Wikimedia y su crédito, o nulos si no hay foto o no se puede acreditar."""
    if not name.strip():
        return JSONResponse({"photo": None}, status_code=400)
    if not _photos_enabled():
        return {"photo": None, "credito": None}
    src, credit = await _resolve_photo(name.strip(), wiki_title.strip())
    return {"photo": src, "credito": credit}


@app.get("/api/player-photo-credit")
async def api_player_photo_credit(name: str, wiki_title: str = ""):
    """Autor y licencia de la foto de una jugadora (404 si no hay foto acreditable)."""
    if not _photos_enabled() or not name.strip():
        return Response(status_code=404)
    _, credit = await _resolve_photo(name.strip(), wiki_title.strip())
    return credit if credit else Response(status_code=404)


@app.get("/api/photo")
async def api_photo(url: str):
    """Proxy de imágenes: solo de Wikimedia y solo de fotos cuyo crédito ya se ha comprobado."""
    if not _photos_enabled() or cache_get(f"photo-src-ok:{url}", _CREDIT_OK_TTL) is None:
        return Response(status_code=404)
    return _image_response(await _download_wikimedia_image(url))


@app.get("/creditos-fotos")
async def photo_credits_page():
    """Página de créditos: autor, licencia y archivo de cada foto que se muestra."""
    rows = []
    if _photos_enabled():
        names = [r["name"] for r in (await get_ranking_live())]
        sem = asyncio.Semaphore(3)

        async def one(name):
            async with sem:
                return name, (await _resolve_photo(name))[1]

        for name, credit in await asyncio.gather(*(one(n) for n in names)):
            if credit:
                rows.append((name, credit))
    cells = []
    for name, c in rows:
        lic = escape(c["licencia"])
        if c.get("licencia_url", "").startswith("https://creativecommons.org/"):
            lic = f'<a href="{escape(c["licencia_url"], quote=True)}" rel="noopener noreferrer">{lic}</a>'
        page = c.get("pagina", "")
        file_cell = (f'<a href="{escape(page, quote=True)}" rel="noopener noreferrer">{escape(c["archivo"])}</a>'
                     if page.startswith("https://") and _is_wikimedia_host(httpx.URL(page).host) else escape(c["archivo"]))
        cells.append(f"<tr><td>{escape(name)}</td><td>{escape(c['autor'])}</td><td>{lic}</td><td>{file_cell}</td></tr>")
    body = ("".join(cells) if cells else
            '<tr><td colspan="4">No hay fotos acreditadas en este momento.</td></tr>')
    html_page = (
        '<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Créditos de las fotos</title>"
        "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#222}"
        "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:.5rem;text-align:left;vertical-align:top}"
        "</style></head><body><h1>Créditos de las fotos</h1>"
        "<p>Las fotos proceden de Wikimedia Commons y Wikipedia, con licencia libre. Cada una se muestra solo si se ha "
        "podido comprobar su autor y su licencia.</p>"
        "<table><thead><tr><th>Jugadora</th><th>Autor</th><th>Licencia</th><th>Archivo</th></tr></thead><tbody>"
        + body + '</tbody></table><p><a href="/">Volver</a></p></body></html>'
    )
    return Response(content=html_page, media_type="text/html; charset=utf-8")


# ── MCP: endpoint /ai/mcp ─────────────────────────────────────────────────────
# El acceso se decide con MCP_AUTH (github | token | open); ver gate_from_env en padel_mcp/tools.py.
app.mount("/ai", gate_from_env(mcp_app))


# ── STATIC ────────────────────────────────────────────────────────────────────

@app.get("/api/version")
async def api_version():
    return {"version": "v96-order-final-first-2026-08-09"}

@app.get("/")
async def index():
    return FileResponse(
        "static/index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-App-Version": "v96-order-final-first-2026-08-09",
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
