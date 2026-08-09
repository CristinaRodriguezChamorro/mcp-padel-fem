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
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from email.utils import parsedate_to_datetime
from tools.live_data import get_womens_ranking_names

RSS_FEEDS = [
    "https://e00-marca.uecdn.es/rss/padel.xml",
    "https://www.mundodeportivo.com/rss/padel.xml",
    "https://www.padelspain.net/feed/",
    "https://www.padeladdict.com/feed/",
    "https://www.setpadelmagazine.es/feed/",
]


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

# Cache local: el ranking no necesita consultarse en cada refresh de noticias.
_ranking_names_cache = {"ts": 0.0, "names": None}
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


async def get_dynamic_ranking_names() -> set[str]:
    """
    Obtiene primero el ranking femenino y guarda sus nombres completos en caché.
    Las noticias se filtran únicamente contra este conjunto.
    """
    now = time.time()
    cached = _ranking_names_cache.get("names")
    if cached and (now - _ranking_names_cache["ts"]) < RANKING_NAMES_TTL:
        return cached

    try:
        names = await get_womens_ranking_names(limit=200)
        normalized_names = _ranking_full_names(names)
        _ranking_names_cache["ts"] = now
        _ranking_names_cache["names"] = normalized_names
        print(
            f"Filtro noticias: {len(normalized_names)} jugadoras cargadas "
            "desde el ranking femenino"
        )
        return normalized_names
    except Exception as exc:
        print(f"  ranking filter error: {exc}")
        return cached or set()


def es_noticia_femenina(
    titulo: str,
    texto: str,
    ranking_names: set[str],
) -> bool:
    """
    Regla determinista:

    La noticia se conserva si cumple AL MENOS una condición:
    1) aparece el nombre completo de una jugadora del ranking femenino;
    2) aparece una señal explícita de contenido femenino
       (women, female, woman, femenino, femenina, mujeres...).

    Groq NO interviene en esta decisión.
    """
    if any(b in titulo.lower() for b in BASURA):
        return False

    contenido = _normalizar(f"{titulo} {texto}")
    padded = f" {contenido} "

    # Señal explícita de contenido femenino.
    female_terms = {_normalizar(term) for term in FILTRO_FEMENINO}
    if any(term and f" {term} " in padded for term in female_terms):
        return True

    # Nombre completo de una jugadora del ranking.
    return any(
        player_name and f" {player_name} " in padded
        for player_name in ranking_names
    )


def _extraer_fragmentos_femeninos(texto: str, ranking_names: set[str]) -> str:
    """
    Conserva únicamente las frases donde aparece alguna jugadora del ranking.
    Esto evita que un artículo mixto lleve contenido masculino al resumen.
    """
    if not texto:
        return ""

    frases = re.split(r"(?<=[.!?])\s+", texto)
    seleccion = []

    female_terms = {_normalizar(term) for term in FILTRO_FEMENINO}

    for frase in frases:
        norm = _normalizar(frase)
        padded = f" {norm} "

        has_ranked_player = any(
            player_name and f" {player_name} " in padded
            for player_name in ranking_names
        )
        has_female_term = any(
            term and f" {term} " in padded
            for term in female_terms
        )

        if has_ranked_player or has_female_term:
            seleccion.append(frase.strip())

    return " ".join(seleccion)[:3000]

def clean_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


async def scrape(session: aiohttp.ClientSession, url: str) -> dict:
    """Obtiene texto + fecha original si la web la publica."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=12),
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        ) as resp:
            if resp.status != 200:
                return {"text": "", "published_date": ""}
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        page_date = ""

        for node in (
            soup.find("meta", attrs={"property": "article:published_time"}),
            soup.find("meta", attrs={"name": "date"}),
            soup.find("meta", attrs={"name": "publish-date"}),
            soup.find("time", attrs={"datetime": True}),
        ):
            if not node:
                continue
            raw = node.get("content") or node.get("datetime") or ""
            m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
            if m:
                page_date = m.group(1)
                break

        for tag in soup.find_all(["nav", "footer", "aside", "script", "style", "header"]):
            tag.decompose()

        body = (
            soup.find("article") or
            soup.find("div", class_=re.compile(r"(article|content|body|text|entry)", re.I)) or
            soup.find("main") or soup
        )
        parts = [
            p.get_text().strip()
            for p in body.find_all("p")
            if len(p.get_text().strip()) > 40
        ]
        return {"text": " ".join(parts)[:5000], "published_date": page_date}
    except Exception as e:
        print(f"  scrape error {url}: {e}")
        return {"text": "", "published_date": ""}


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

    prompt = f"""Eres periodista deportiva española especializada en pádel femenino.

Resume EXCLUSIVAMENTE información del circuito femenino.
No menciones jugadores masculinos ni inventes datos.

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


def _ranking_surnames_from_names(ranking_names: set[str]) -> set[str]:
    surnames = set()
    for full_name in ranking_names:
        parts = full_name.split()
        if len(parts) >= 2 and len(parts[-1]) >= 4:
            surnames.add(parts[-1])
    return surnames


def _female_title_strength(titulo: str, ranking_names: set[str]) -> int:
    """
    3 = female/women/etc. explícito o nombre completo de jugadora
    2 = al menos dos apellidos del ranking femenino
    0 = señal insuficiente
    """
    norm = _normalizar(titulo)
    padded = f" {norm} "

    male_terms = {_normalizar(x) for x in ("masculino","masculina","masculinos","masculinas","men","male")}
    female_terms = {_normalizar(x) for x in FILTRO_FEMENINO}

    explicit_male = any(term and f" {term} " in padded for term in male_terms)
    explicit_female = any(term and f" {term} " in padded for term in female_terms)

    full_hits = {
        name for name in ranking_names
        if name and f" {name} " in padded
    }

    surnames = _ranking_surnames_from_names(ranking_names)
    surname_hits = {
        surname for surname in surnames
        if surname and f" {surname} " in padded
    }

    if explicit_male and not explicit_female and not full_hits and len(surname_hits) < 2:
        return 0
    if explicit_female or full_hits:
        return 3
    if len(surname_hits) >= 2:
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



async def get_latest_news() -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    client = Groq(api_key=api_key) if api_key else None
    print("\n=== PADELfem: inicio pipeline ===")

    raw = []
    vistos_titulos = set()
    vistos_links = set()

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:30]:
                titulo = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not titulo or not link:
                    continue
                if titulo.lower() in vistos_titulos or link in vistos_links:
                    continue
                vistos_titulos.add(titulo.lower())
                vistos_links.add(link)
                raw.append({
                    "titulo": titulo,
                    "texto": clean_text(entry.get("summary", "") or entry.get("description", "")),
                    "url": link,
                    "published": entry.get("published", ""),
                    "published_date": _rss_publication_iso(entry),
                })
        except Exception as e:
            print(f"  feed error {feed_url}: {e}")

    print(f"RSS: {len(raw)} artículos")

    ranking_names = await get_dynamic_ranking_names()
    femeninos = []
    for art in raw:
        titulo = art["titulo"]
        texto = art.get("texto", "")
        if not es_noticia_femenina(titulo, texto, ranking_names):
            continue

        title_norm = _normalizar(titulo)
        padded_title = f" {title_norm} "
        male_title = any(
            f" {_normalizar(term)} " in padded_title
            for term in ("masculino","masculina","masculinos","masculinas","men","male")
        )

        # If the headline is explicitly male, only keep it when the RSS text itself
        # contains a strong female signal; later scraping will isolate the female part.
        if male_title:
            body_norm = _normalizar(texto)
            padded_body = f" {body_norm} "
            female_terms = {_normalizar(term) for term in FILTRO_FEMENINO}
            strong_body = any(
                term and f" {term} " in padded_body for term in female_terms
            ) or any(
                name and f" {name} " in padded_body for name in ranking_names
            )
            if not strong_body:
                continue

        femeninos.append(art)
    print(f"Filtro ranking femenino: {len(femeninos)} noticias")
    for art in femeninos:
        print(f"  ✓ {art['titulo'][:70]}")

    if not femeninos:
        return {"resumen_diario": [], "date": datetime.date.today().isoformat()}

    async with aiohttp.ClientSession() as session:
        textos = await asyncio.gather(
            *[scrape(session, art["url"]) for art in femeninos],
            return_exceptions=True
        )

    depurados = []
    for art, scraped in zip(femeninos, textos):
        scraped_text = scraped.get("text", "") if isinstance(scraped, dict) else ""
        scraped_date = scraped.get("published_date", "") if isinstance(scraped, dict) else ""

        if scraped_date:
            art["published_date"] = scraped_date

        candidato = scraped_text if len(scraped_text) > len(art["texto"]) else art["texto"]
        fragmento = _extraer_fragmentos_femeninos(candidato, ranking_names)

        if not fragmento:
            fragmento = _extraer_fragmentos_femeninos(
                f"{art['titulo']}. {art.get('texto', '')}",
                ranking_names,
            )

        if fragmento:
            art["texto"] = fragmento
            depurados.append(art)

    femeninos = depurados
    print(f"Tras depurar artículos mixtos: {len(femeninos)} noticias")

    if not femeninos:
        return {"resumen_diario": [], "date": datetime.date.today().isoformat()}

    grupos = agrupar(femeninos)[:4]
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

    noticias.sort(key=lambda x: x.get("fecha", ""), reverse=True)

    return {
        "date": datetime.date.today().isoformat(),
        "resumen_diario": noticias,
    }
