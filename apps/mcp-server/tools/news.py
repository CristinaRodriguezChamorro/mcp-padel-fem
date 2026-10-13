"""
news.py — Pipeline de noticias de pádel femenino profesional
"""

import feedparser
import re
import os
import asyncio
import aiohttp
import json
import datetime
import time
import unicodedata
from groq import Groq
from urllib.parse import urlparse
from email.utils import parsedate_to_datetime
from tools.local_data import get_womens_ranking_names
from tools.http_policy import USER_AGENT, check_and_wait
from tools.players import norm as _pnorm, players_from_names, mentioned_players

RSS_FEEDS = [
    "https://okdiario.com/deportes/padel/feed",
    "https://e00-marca.uecdn.es/rss/padel.xml",
    "https://www.mundodeportivo.com/rss/padel.xml",
    "https://www.padelspain.net/feed/",
    "https://www.padeladdict.com/feed/",
    "https://www.setpadelmagazine.es/feed/",
]


# Noticias: SOLO lo que cada medio publica en su feed RSS para que lo consuman programas
# (título, enlace, fecha y extracto). No se descargan artículos ni se scrapean páginas.
# Se puede desactivar con NEWS_RSS_ENABLED=false o sustituir la lista con NEWS_RSS_FEEDS
# (URLs separadas por comas).
def _rss_enabled() -> bool:
    return os.environ.get("NEWS_RSS_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")


def _rss_feeds() -> list[str]:
    custom = [u.strip() for u in os.environ.get("NEWS_RSS_FEEDS", "").split(",") if u.strip()]
    return custom or list(RSS_FEEDS)


# Qué se puede hacer con cada fuente, según lo que se ha podido verificar de sus condiciones:
#   "resumen"  -> título, resumen propio (puede hacerlo una IA) y enlace. Solo con permiso explícito
#                 de adaptar el contenido (OKDiario publica con Creative Commons no comercial).
#   "extracto" -> título, extracto corto TAL CUAL (sin reescribir ni pasar por una IA) y enlace.
#   "enlace"   -> solo título, fecha, medio y enlace. Es el valor por defecto: cualquier fuente que no
#                 esté en esta tabla (o cuyas condiciones no se hayan leído) se trata así.
# Para subir una fuente de nivel hay que leer antes sus condiciones de uso.
SOURCE_POLICY = {
    "okdiario.com": ("OKDiario", "resumen"),
    "padeladdict.com": ("Padel Addict", "extracto"),
    "e00-marca.uecdn.es": ("Marca", "enlace"),
    "mundodeportivo.com": ("Mundo Deportivo", "enlace"),
    "padelspain.net": ("Padel Spain", "enlace"),
    "setpadelmagazine.es": ("Set Padel Magazine", "enlace"),
}
DEFAULT_MODE = "enlace"
OUTLET_NAMES = {host: name for host, (name, _) in SOURCE_POLICY.items()}

NEWS_CACHE_SECONDS = 10 * 60      # cada cuánto se vuelve a consultar (como mucho)
MAX_RESUMENES = 6                 # noticias que se resumen con IA en cada actualización
MAX_TOTAL = 15                    # noticias que se publican en total
EXTRACT_CHARS = 220               # longitud máxima del extracto literal


def _host(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def _politica_de_feed(feed_url: str) -> tuple[str, str, str]:
    host = _host(feed_url)
    name, mode = SOURCE_POLICY.get(host, (host, DEFAULT_MODE))
    return host, name, mode


def rss_status() -> dict:
    policy = {}
    for u in _rss_feeds():
        _, name, mode = _politica_de_feed(u)
        policy[name] = mode
    return {
        "activo": _rss_enabled(),
        "fuentes": sorted(policy),
        "politica": [{"nombre": n, "modo": m} for n, m in sorted(policy.items())],
        "nota": "Solo se leen los feeds RSS que los propios medios publican; no se descargan artículos.",
    }


# ── limpieza de todo lo que viene de fuera ───────────────────────────────────

def _seguro(text: str, limit: int = 300) -> str:
    """Texto ajeno -> una línea sin etiquetas ni símbolos < >, con longitud acotada."""
    text = re.sub(r"<[^>]*>", " ", str(text or ""))
    text = text.replace("<", "").replace(">", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rstrip()


def _es_url_segura(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _extracto_corto(text: str, limit: int = EXTRACT_CHARS) -> str:
    """Primeras palabras del extracto original, tal cual y cortadas en una palabra completa."""
    text = _seguro(text, 2000)
    text = re.sub(r"\s*(?:\[…\]|\[\.\.\.\]|Continuar leyendo.*|Leer más.*|Seguir leyendo.*|Read more.*)$", "", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"


# Groq is optional. If quota/rate-limit fails, disable it temporarily and use
# deterministic fallbacks. This prevents repeated useless calls on every page load.
GROQ_MODEL = os.environ.get("GROQ_NEWS_MODEL", "llama-3.1-8b-instant")
GROQ_COOLDOWN_SECONDS = 30 * 60
_groq_disabled_until = 0.0

# In-memory summary cache: same source article is not summarized repeatedly.
# Key = source URLs + title fingerprints.
_summary_cache: dict[str, dict] = {}
SUMMARY_CACHE_TTL = 24 * 60 * 60


def _is_groq_available() -> bool:
    return time.time() >= _groq_disabled_until


def _disable_groq_temporarily(reason: str):
    global _groq_disabled_until
    _groq_disabled_until = time.time() + GROQ_COOLDOWN_SECONDS
    print(f"  Groq deshabilitado {GROQ_COOLDOWN_SECONDS//60} min: {reason}")


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit_exceeded" in text


def _summary_cache_key(articulos: list) -> str:
    parts = []
    for art in articulos:
        parts.append((art.get("url") or "") + "|" + (art.get("titulo") or ""))
    return "||".join(sorted(parts))


def _summary_cache_get(key: str):
    item = _summary_cache.get(key)
    if not item:
        return None
    if time.time() - item["ts"] > SUMMARY_CACHE_TTL:
        _summary_cache.pop(key, None)
        return None
    return item["value"]


def _summary_cache_set(key: str, value: dict):
    _summary_cache[key] = {"ts": time.time(), "value": value}



BASURA = [
    "horarios", "dónde ver", "donde ver", "cómo ver", "como ver",
    "dónde y cómo", "donde y como", "entradas para", "comprar entradas",
]


FILTRO_FEMENINO = (
    "female", "women", "woman",
    "femenino", "femenina", "femeninos", "femeninas", "mujeres",
)

# Las noticias se centran SOLO en el Top 20 del ranking femenino.
TOP_N = 20

# Cache local: el ranking no necesita consultarse en cada refresh de noticias.
_ranking_names_cache = {"ts": 0.0, "display": None}
RANKING_NAMES_TTL = 6 * 60 * 60


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.casefold()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def _ranking_full_names(names: list[str]) -> set[str]:
    """
    Normaliza los nombres completos devueltos por el ranking femenino.
    No genera apellidos sueltos ni heurísticas adicionales.
    """
    return {
        norm
        for name in names
        if (norm := _normalizar(name))
    }


async def get_top_player_names(limit: int = TOP_N) -> list[str]:
    """
    Nombres (tal como salen en el ranking) de las jugadoras del Top 20, en orden.
    Se cachean 6 h; si la fuente falla se reutiliza lo último conocido.
    """
    now = time.time()
    cached = _ranking_names_cache.get("display")
    if cached and (now - _ranking_names_cache["ts"]) < RANKING_NAMES_TTL:
        return cached

    try:
        names = [n for n in await get_womens_ranking_names(limit=limit) if n][:limit]
        if names:
            _ranking_names_cache["ts"] = now
            _ranking_names_cache["display"] = names
            print(f"Filtro noticias: Top {len(names)} del ranking femenino cargado")
            return names
    except Exception as exc:
        print(f"  ranking filter error: {exc}")
    return cached or []


def _female_term_present(padded: str) -> bool:
    return any(t and f" {t} " in padded for t in {_normalizar(x) for x in FILTRO_FEMENINO})


def es_noticia_top20(
    titulo: str,
    texto: str,
    ranking_names: set[str],
) -> bool:
    """
    Regla determinista: la noticia se conserva SOLO si habla de alguien del Top 20.

      - nombre completo de una jugadora del Top 20  -> se conserva;
      - solo apellido (o inicial + apellido)        -> se conserva si además hay una
        señal femenina (femenino, women...) o se mencionan al menos dos jugadoras
        distintas. Un apellido suelto podría ser un jugador masculino.

    Una noticia genérica sobre pádel femenino sin ninguna jugadora del Top 20 NO pasa.
    Groq NO interviene en esta decisión.
    """
    if any(b in titulo.lower() for b in BASURA):
        return False

    players = players_from_names(ranking_names)
    hits = mentioned_players(f"{titulo} {texto}", players)
    if not hits:
        return False
    if any(strength == 2 for _, strength in hits):
        return True
    padded = f" {_normalizar(f'{titulo} {texto}')} "
    return _female_term_present(padded) or len(hits) >= 2


def _extraer_fragmentos_femeninos(texto: str, ranking_names: set[str]) -> str:
    """
    Conserva únicamente las frases donde aparece alguna jugadora del Top 20.
    Esto evita que un artículo mixto lleve contenido masculino o ajeno al resumen.
    """
    if not texto:
        return ""

    players = players_from_names(ranking_names)
    frases = re.split(r"(?<=[.!?])\s+", texto)
    seleccion = [f.strip() for f in frases if mentioned_players(f, players)]
    return " ".join(seleccion)[:3000]


def _nombres_jugadoras(texto: str, players: list) -> list[str]:
    """Nombres (de pantalla) de las jugadoras del Top 20 mencionadas en el texto."""
    return [p["name"] for p, _ in mentioned_players(texto, players)]


def _centrar_en_top20(noticias: list, articulos: list, players: list) -> list:
    """
    Etiqueta cada noticia con las jugadoras del Top 20 de las que habla (las de sus
    fuentes y las que cita su propio texto) y descarta cualquiera que no hable de
    ninguna: el resumen nunca amplía el alcance del filtro.
    """
    url_tags = {a.get("url", ""): a.get("jugadoras", []) for a in articulos}
    centradas = []
    for n in noticias:
        tags = []
        for f in n.get("fuentes", []):
            for name in url_tags.get(f.get("url", ""), []):
                if name not in tags:
                    tags.append(name)
        for name in _nombres_jugadoras(f"{n.get('titular', '')} {n.get('resumen', '')}", players):
            if name not in tags:
                tags.append(name)
        if tags:
            n["jugadoras"] = tags
            centradas.append(n)
    return centradas


def _foco_jugadoras(articulos: list) -> str:
    nombres = []
    for art in articulos:
        for n in art.get("jugadoras", []):
            if n not in nombres:
                nombres.append(n)
    if not nombres:
        return ""
    return f"Céntrate en estas jugadoras del Top 20 mundial: {', '.join(nombres)}. Ignora el resto de informaciones."


def clean_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


async def generar_noticia(client: Groq, sem: asyncio.Semaphore, articulos: list) -> dict | None:
    """Groq/Llama mejora editorialmente noticias YA clasificadas como femeninas."""
    cache_key = _summary_cache_key(articulos)
    cached = _summary_cache_get(cache_key)
    if cached:
        return cached

    if not _is_groq_available():
        return None

    texto_articulos = ""
    for i, art in enumerate(articulos[:3], 1):
        texto_articulos += (
            f"\n--- Artículo {i} ---\n"
            f"Título: {art['titulo']}\n"
            f"Contenido: {art['texto'][:1800]}\n"
        )

    foco = _foco_jugadoras(articulos)
    prompt = f"""Eres periodista deportiva española especializada en pádel femenino.

Resume EXCLUSIVAMENTE información del circuito femenino.
No menciones jugadores masculinos ni inventes datos.
{foco}

FUENTES:
{texto_articulos}

Devuelve SOLO JSON:
{{
  "titular": "máximo 14 palabras; mini-resumen del hecho principal",
  "resumen": "2-3 frases completas, 45-85 palabras; síntesis propia, nunca extracto cortado"
}}

Reglas:
- español de España;
- sin puntos suspensivos;
- titular = sujeto + hecho principal;
- resumen = qué pasó + quién + resultado/consecuencia/contexto si consta.
"""

    async with sem:
        try:
            resp = await asyncio.to_thread(lambda: client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=300,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            ))
            raw = resp.choices[0].message.content.strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return None

            data = json.loads(match.group())
            titular = clean_text(data.get("titular", ""))
            resumen = clean_text(data.get("resumen", ""))
            if not titular or not resumen or resumen.endswith(("...", "…")):
                return None

            fuentes = []
            vistos = set()
            for art in articulos[:4]:
                url = art.get("url", "")
                if url and url not in vistos:
                    fuentes.append({
                        "domain": urlparse(url).netloc.replace("www.", ""),
                        "titulo": art["titulo"],
                        "url": url,
                    })
                    vistos.add(url)

            result = {
                "titular": titular,
                "resumen": resumen,
                "fecha": _latest_article_date(articulos) or datetime.date.today().isoformat(),
                "fuentes": fuentes,
                "generated_by": f"groq:{GROQ_MODEL}",
            }
            _summary_cache_set(cache_key, result)
            return result

        except Exception as exc:
            print(f"  generar error: {exc}")
            if _is_rate_limit_error(exc):
                _disable_groq_temporarily("rate limit / cuota")
            return None


async def generar_noticia_individual(client: Groq, sem: asyncio.Semaphore, art: dict) -> dict | None:
    """Segundo intento editorial. Se omite automáticamente si Groq está en cooldown."""
    cache_key = _summary_cache_key([art])
    cached = _summary_cache_get(cache_key)
    if cached:
        return cached

    if not _is_groq_available():
        return None

    foco = _foco_jugadoras([art])
    prompt = f"""Eres periodista deportiva española especializada en pádel femenino.

FUENTE:
Título original: {art.get('titulo', '')}
Contenido: {art.get('texto', '')[:2200]}

Devuelve SOLO JSON:
{{
  "titular": "máximo 14 palabras; mini-resumen",
  "resumen": "2-3 frases completas, 45-85 palabras; síntesis propia"
}}

Solo pádel femenino. No inventes datos ni menciones jugadores masculinos.
{foco}
"""

    async with sem:
        try:
            resp = await asyncio.to_thread(lambda: client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=280,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            ))
            raw = resp.choices[0].message.content.strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return None

            data = json.loads(match.group())
            titular = clean_text(data.get("titular", ""))
            resumen = clean_text(data.get("resumen", ""))
            if not titular or not resumen or resumen.endswith(("...", "…")):
                return None

            url = art.get("url", "")
            fuentes = [{
                "domain": urlparse(url).netloc.replace("www.", ""),
                "titulo": art.get("titulo", ""),
                "url": url,
            }] if url else []

            result = {
                "titular": titular,
                "resumen": resumen,
                "fecha": art.get("published_date") or datetime.date.today().isoformat(),
                "fuentes": fuentes,
                "generated_by": f"groq:{GROQ_MODEL}",
            }
            _summary_cache_set(cache_key, result)
            return result

        except Exception as exc:
            print(f"  resumen individual error: {exc}")
            if _is_rate_limit_error(exc):
                _disable_groq_temporarily("rate limit / cuota")
            return None



def _sentencias_completas(texto: str) -> list[str]:
    """
    Separa el contenido en frases completas para construir el fallback de Noticias.
    Nunca devuelve fragmentos truncados con "...".
    """
    texto = clean_text(texto or "")
    if not texto:
        return []

    partes = re.split(r"(?<=[.!?])\s+", texto)
    salida = []

    for parte in partes:
        parte = parte.strip()
        if len(parte) < 25:
            continue
        if parte.endswith(("...", "…")):
            continue
        if parte[-1:] not in ".!?":
            parte += "."
        salida.append(parte)

    return salida


def _female_title_strength(titulo: str, ranking_names: set[str]) -> int:
    """
    3 = nombre completo de una jugadora del Top 20, o apellido + señal femenina
    2 = al menos dos jugadoras del Top 20 por apellido
    0 = señal insuficiente (un texto genérico "femenino" sin jugadoras NO basta)
    """
    padded = f" {_normalizar(titulo)} "
    hits = mentioned_players(titulo, players_from_names(ranking_names))
    full = any(strength == 2 for _, strength in hits)
    weak = sum(1 for _, strength in hits if strength == 1)

    male_terms = {_normalizar(x) for x in ("masculino", "masculina", "masculinos", "masculinas", "men", "male")}
    explicit_male = any(term and f" {term} " in padded for term in male_terms)
    explicit_female = _female_term_present(padded)

    if explicit_male and not explicit_female and not full and weak < 2:
        return 0
    if full:
        return 3
    if explicit_female and weak >= 1:
        return 3
    if weak >= 2:
        return 2
    return 0


def _titulo_pasa_filtro(titulo: str, ranking_names: set[str]) -> bool:
    """
    El titular puede demostrar que la noticia es femenina por:
    - female / women / femenino / mujeres;
    - nombre completo de una jugadora;
    - al menos dos apellidos distintos del ranking femenino.
    """
    return _female_title_strength(titulo, ranking_names) >= 2


def _titulo_fallback_desde_frase(frase: str) -> str:
    """
    Construye un titular corto sin inventar información.
    Usa la primera proposición completa de una frase femenina.
    """
    frase = clean_text(frase)
    if not frase:
        return "Actualidad del pádel femenino"

    # Prefer first clause before semicolon/colon/em-dash.
    base = re.split(r"[;:–—]", frase, maxsplit=1)[0].strip()
    words = base.split()

    if len(words) > 14:
        # Cut at a natural comma if it appears early enough.
        comma = base.find(",")
        if 20 <= comma <= 110:
            base = base[:comma].strip()
        else:
            base = " ".join(words[:14]).rstrip(",.;:")

    if base and base[-1] not in ".!?":
        base += ""
    return base


def generar_fallback_sin_ia(art: dict, ranking_names: set[str]) -> dict | None:
    """
    Fallback cuando Groq está en 429.

    Mantiene varias noticias visibles sin introducir contenido masculino:
    - utiliza únicamente el texto femenino ya depurado;
    - exige al menos una señal femenina fuerte en título o cuerpo;
    - construye resumen con frases completas;
    - mantiene la fecha real.
    """
    titulo_original = clean_text(art.get("titulo", ""))
    texto = clean_text(art.get("texto", ""))

    frases = _sentencias_completas(texto)
    if not frases:
        return None

    # Body ya viene de _extraer_fragmentos_femeninos, pero exigimos una señal fuerte.
    body_strength = max(
        (_female_title_strength(frase, ranking_names) for frase in frases),
        default=0,
    )
    title_strength = _female_title_strength(titulo_original, ranking_names)

    if max(title_strength, body_strength) < 2:
        return None

    # 1 frase puede servir si contiene suficiente información;
    # preferimos 2-3 cuando existen.
    if len(frases) >= 3:
        resumen = " ".join(frases[:3])
    elif len(frases) == 2:
        resumen = " ".join(frases)
    else:
        resumen = frases[0]

    # Never truncate mid-sentence.
    if len(resumen) > 750 and len(frases) >= 2:
        resumen = " ".join(frases[:2])

    if title_strength >= 2:
        titular = titulo_original
    else:
        titular = _titulo_fallback_desde_frase(frases[0])

    if not titular or not resumen:
        return None

    url = art.get("url", "")
    fuentes = []
    if url:
        fuentes.append({
            "domain": urlparse(url).netloc.replace("www.", ""),
            "titulo": titulo_original,
            "url": url,
        })

    return {
        "titular": titular,
        "resumen": resumen,
        "fecha": art.get("published_date") or datetime.date.today().isoformat(),
        "fuentes": fuentes,
        "generated_by": "deterministic-fallback-v22",
    }


def agrupar(articulos: list) -> list[list]:
    grupos = []
    usados = set()
    stopwords = {"el","la","los","las","de","del","en","y","a","con","que","su","se","al","por","un","una","para"}

    for i, art in enumerate(articulos):
        if i in usados:
            continue
        grupo = [art]
        usados.add(i)
        palabras_i = set(art["titulo"].lower().split()) - stopwords

        for j, art2 in enumerate(articulos):
            if j in usados or j == i:
                continue
            palabras_j = set(art2["titulo"].lower().split()) - stopwords
            if len(palabras_i & palabras_j) >= 2:
                grupo.append(art2)
                usados.add(j)
            if len(grupo) >= 4:
                break

        grupos.append(grupo)

    return grupos


def _rss_publication_iso(entry) -> str:
    """Normalize the original article publication date to YYYY-MM-DD."""
    # feedparser structured date is the most robust path.
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime.date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday).isoformat()
        except Exception:
            pass

    raw = entry.get("published", "") or entry.get("updated", "")
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return dt.date().isoformat()
        except Exception:
            pass

    return ""


def _latest_article_date(articulos: list) -> str:
    dates = sorted(
        {a.get("published_date", "") for a in articulos if a.get("published_date")},
        reverse=True,
    )
    return dates[0] if dates else ""



_FEED_CACHE: dict[str, dict] = {}


async def _fetch_feed(feed_url: str):
    """
    Descarga un feed RSS respetando robots.txt, sin bloquear el event loop y con petición condicional
    (ETag / Last-Modified): si el medio responde "no ha cambiado" (304) se reutiliza lo anterior y casi
    no le cuesta nada. Así se puede consultar cada pocos minutos sin molestar.
    """
    if not await check_and_wait(feed_url):
        return None
    previous = _FEED_CACHE.get(feed_url, {})
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.5",
    }
    if previous.get("etag"):
        headers["If-None-Match"] = previous["etag"]
    if previous.get("modified"):
        headers["If-Modified-Since"] = previous["modified"]
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 304 and previous.get("feed") is not None:
                    return previous["feed"]
                if resp.status != 200:
                    print(f"  feed {feed_url}: HTTP {resp.status}")
                    return None
                data = await resp.read()
                etag = (resp.headers.get("ETag") or "")[:200]
                modified = (resp.headers.get("Last-Modified") or "")[:200]
    except Exception as exc:
        print(f"  feed error {feed_url}: {type(exc).__name__}: {exc}")
        return None
    feed = feedparser.parse(data)
    _FEED_CACHE[feed_url] = {"etag": etag, "modified": modified, "feed": feed}
    return feed


def _item_directo(art: dict) -> dict:
    """Noticia de una fuente que solo permite título (y extracto literal) y enlace: nunca pasa por la IA."""
    mode = art["modo"]
    return {
        "titular": art["titulo"],
        "resumen": _extracto_corto(art.get("texto_original", "")) if mode == "extracto" else "",
        "fecha": art.get("published_date") or datetime.date.today().isoformat(),
        "fuentes": [{"domain": art["host"], "medio": art["medio"], "titulo": art["titulo"],
                     "url": art["url"], "autor": art.get("autor", "")}],
        "modo": mode,
    }


def _enriquecer_fuentes(noticias: list, articulos: list) -> list:
    """Añade el medio y el autor a cada fuente (la atribución que piden algunas licencias) y el modo a cada noticia."""
    by_url = {a.get("url", ""): a for a in articulos}
    for n in noticias:
        modes = set()
        for f in n.get("fuentes", []):
            art = by_url.get(f.get("url", ""))
            f.setdefault("medio", art["medio"] if art else f.get("domain", ""))
            f.setdefault("autor", art.get("autor", "") if art else "")
            if art:
                modes.add(art["modo"])
        n.setdefault("modo", "resumen" if "resumen" in modes else (next(iter(modes)) if modes else "enlace"))
    return noticias


async def get_latest_news() -> dict:
    if not _rss_enabled():
        return {"date": datetime.date.today().isoformat(), "resumen_diario": [],
                "message": "Noticias desactivadas (NEWS_RSS_ENABLED=false)."}
    api_key = os.environ.get("GROQ_API_KEY")
    client = Groq(api_key=api_key) if api_key else None
    print("\n=== PADELfem: inicio pipeline ===")

    raw = []
    vistos_titulos = set()
    vistos_links = set()

    for feed_url in _rss_feeds():
        try:
            feed = await _fetch_feed(feed_url)
            if feed is None:
                continue
            host, medio, modo = _politica_de_feed(feed_url)
            for entry in feed.entries[:30]:
                titulo = _seguro(entry.get("title", ""), 220)
                link = entry.get("link", "").strip()
                if not titulo or not _es_url_segura(link):
                    continue
                if titulo.lower() in vistos_titulos or link in vistos_links:
                    continue
                vistos_titulos.add(titulo.lower())
                vistos_links.add(link)
                raw.append({
                    "titulo": titulo,
                    "texto": clean_text(entry.get("summary", "") or entry.get("description", "")),
                    "url": link,
                    "host": host,
                    "medio": medio,
                    "modo": modo,
                    "autor": _seguro(entry.get("author", ""), 80),
                    "published": entry.get("published", ""),
                    "published_date": _rss_publication_iso(entry),
                })
        except Exception as e:
            print(f"  feed error {feed_url}: {e}")

    print(f"RSS: {len(raw)} artículos")

    top_names = await get_top_player_names()
    ranking_names = {_pnorm(n) for n in top_names}
    players = players_from_names(top_names)
    if not players:
        print("  sin Top 20 disponible: no se puede filtrar, se devuelven 0 noticias")
    femeninos = []
    for art in raw:
        titulo = art["titulo"]
        texto = art.get("texto", "")
        if not es_noticia_top20(titulo, texto, ranking_names):
            continue

        title_norm = _normalizar(titulo)
        padded_title = f" {title_norm} "
        male_title = any(
            f" {_normalizar(term)} " in padded_title
            for term in ("masculino","masculina","masculinos","masculinas","men","male")
        )

        # Si el titular es explícitamente masculino, solo se conserva cuando el propio
        # texto menciona a una jugadora del Top 20.
        if male_title and not mentioned_players(texto, players):
            continue

        femeninos.append(art)
    print(f"Filtro Top 20: {len(femeninos)} noticias")
    for art in femeninos:
        print(f"  ✓ {art['titulo'][:70]}")

    if not femeninos:
        return {"resumen_diario": [], "date": datetime.date.today().isoformat()}

    depurados = []
    for art in femeninos:
        art["texto_original"] = art.get("texto", "")
        fragmento = _extraer_fragmentos_femeninos(art.get("texto", ""), ranking_names)

        if not fragmento:
            fragmento = _extraer_fragmentos_femeninos(
                f"{art['titulo']}. {art.get('texto', '')}",
                ranking_names,
            )

        if fragmento:
            art["texto"] = fragmento
            art["jugadoras"] = _nombres_jugadoras(f"{art['titulo']} {fragmento}", players)
            depurados.append(art)

    femeninos = depurados
    print(f"Tras depurar artículos mixtos: {len(femeninos)} noticias")

    if not femeninos:
        return {"resumen_diario": [], "date": datetime.date.today().isoformat()}

    # Reparto por política de la fuente: solo las que lo permiten expresamente pasan por la IA.
    todos = femeninos
    femeninos = [a for a in todos if a["modo"] == "resumen"]
    print(f"Por política: {len(femeninos)} para resumir, {len(todos) - len(femeninos)} solo con enlace/extracto")

    grupos = agrupar(femeninos)[:MAX_RESUMENES]
    print(f"Grupos: {len(grupos)}")

    sem = asyncio.Semaphore(2)
    noticias = []

    if client and _is_groq_available():
        # Sequential batches deliberately limit token spikes and make the
        # circuit breaker useful after the first 429.
        for grupo in grupos:
            if not _is_groq_available():
                break
            item = await generar_noticia(client, sem, grupo)
            if isinstance(item, dict):
                noticias.append(item)

    print(f"Noticias generadas con IA/cache: {len(noticias)}")

    # Si faltan resúmenes, hacemos un segundo intento artículo a artículo.
    # Nunca mostramos un fragmento RSS truncado como si fuera un resumen.
    if len(noticias) < len(femeninos):
        usados_urls = {
            f.get("url", "")
            for noticia in noticias
            for f in noticia.get("fuentes", [])
        }
        candidatos = [
            art for art in femeninos
            if art.get("url", "") not in usados_urls
        ][:4]

        if candidatos and client and _is_groq_available():
            print(f"  noticias: reintento individual máximo 1")
            # At most one extra LLM call. If it 429s, breaker opens immediately.
            item = await generar_noticia_individual(client, sem, candidatos[0])
            if item:
                noticias.append(item)

    # Si Groq está sin cuota (429) o no genera suficiente contenido,
    # rellenamos con resúmenes deterministas de artículos claramente femeninos.
    # De esta forma Noticias NO desaparece por una dependencia externa.
    if len(noticias) < len(femeninos):
        usados_urls = {
            f.get("url", "")
            for noticia in noticias
            for f in noticia.get("fuentes", [])
        }

        for art in femeninos:
            if False:  # sin límite fijo de noticias
                break
            if art.get("url", "") in usados_urls:
                continue

            try:
                fallback = generar_fallback_sin_ia(art, ranking_names)
            except Exception as exc:
                print(f"  fallback noticias error: {type(exc).__name__}: {exc}")
                fallback = None

            if fallback:
                noticias.append(fallback)
                usados_urls.add(art.get("url", ""))

        print(f"Noticias tras fallback determinista: {len(noticias)}")

    noticias.extend(_item_directo(a) for a in todos if a["modo"] != "resumen")
    noticias = _centrar_en_top20(noticias, todos, players)
    noticias = _enriquecer_fuentes(noticias, todos)

    noticias.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    noticias = noticias[:MAX_TOTAL]

    return {
        "date": datetime.date.today().isoformat(),
        "resumen_diario": noticias,
    }
