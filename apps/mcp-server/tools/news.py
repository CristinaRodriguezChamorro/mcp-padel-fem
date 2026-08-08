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
from tools.live_data import get_womens_ranking_names

RSS_FEEDS = [
    "https://e00-marca.uecdn.es/rss/padel.xml",
    "https://www.mundodeportivo.com/rss/padel.xml",
    "https://www.padelspain.net/feed/",
    "https://www.padeladdict.com/feed/",
    "https://www.setpadelmagazine.es/feed/",
]


BASURA = [
    "horarios", "dónde ver", "donde ver", "cómo ver", "como ver",
    "dónde y cómo", "donde y como", "entradas para", "comprar entradas",
]


FILTRO_FEMENINO = (
    "female", "women", "woman",
    "femenino", "femenina", "femeninos", "femeninas", "mujeres",
)

# Cache local: el ranking no necesita consultarse en cada refresh de noticias.
_ranking_names_cache = {"ts": 0.0, "aliases": set()}
RANKING_NAMES_TTL = 6 * 60 * 60


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.casefold()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def _ranking_aliases(names: list[str]) -> set[str]:
    """
    Genera aliases dinámicos desde el ranking:
    - nombre completo
    - apellido final

    El apellido permite detectar titulares deportivos del tipo
    "Triay y Brea ganan...", sin mantener una lista manual.
    """
    full_names = []
    surnames = []

    for name in names:
        norm = _normalizar(name)
        if not norm:
            continue

        full_names.append(norm)
        parts = norm.split()
        if len(parts) >= 2 and len(parts[-1]) >= 4:
            surnames.append(parts[-1])

    # Un apellido solo se usa si no es ambiguo dentro del propio ranking.
    counts = {}
    for surname in surnames:
        counts[surname] = counts.get(surname, 0) + 1

    unique_surnames = {s for s, count in counts.items() if count == 1}
    return set(full_names) | unique_surnames


async def get_dynamic_ranking_aliases() -> set[str]:
    now = time.time()
    if (
        _ranking_names_cache["aliases"]
        and (now - _ranking_names_cache["ts"]) < RANKING_NAMES_TTL
    ):
        return _ranking_names_cache["aliases"]

    try:
        names = await get_womens_ranking_names(limit=200)
        aliases = _ranking_aliases(names)
        _ranking_names_cache["ts"] = now
        _ranking_names_cache["aliases"] = aliases
        print(f"Filtro noticias: {len(aliases)} aliases dinámicos desde ranking femenino")
        return aliases
    except Exception as exc:
        print(f"  ranking filter error: {exc}")
        return _ranking_names_cache["aliases"]


def es_noticia_femenina(
    titulo: str,
    texto: str,
    ranking_aliases: set[str],
) -> bool:
    """
    Filtro determinista:
    1) términos explícitos de competición femenina;
    2) nombres/apellidos obtenidos dinámicamente del ranking femenino.

    Groq NO decide si una noticia es femenina.
    """
    if any(b in titulo.lower() for b in BASURA):
        return False

    contenido = _normalizar(f"{titulo} {texto}")

    female_terms = {_normalizar(term) for term in FILTRO_FEMENINO}
    if any(term in contenido for term in female_terms):
        return True

    padded = f" {contenido} "
    if any(f" {alias} " in padded for alias in ranking_aliases if alias):
        return True

    return False


def clean_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


async def scrape(session: aiohttp.ClientSession, url: str) -> str:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=12),
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        ) as resp:
            if resp.status != 200:
                return ""
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["nav", "footer", "aside", "script", "style", "header"]):
            tag.decompose()

        body = (
            soup.find("article") or
            soup.find("div", class_=re.compile(r"(article|content|body|text|entry)", re.I)) or
            soup.find("main") or soup
        )
        parts = [p.get_text().strip() for p in body.find_all("p") if len(p.get_text().strip()) > 40]
        return " ".join(parts)[:3000]
    except Exception as e:
        print(f"  scrape error {url}: {e}")
        return ""


async def generar_noticia(client: Groq, sem: asyncio.Semaphore, articulos: list) -> dict | None:
    """Groq/Llama resume y homogeneiza noticias YA clasificadas como femeninas."""
    texto_articulos = ""
    for i, art in enumerate(articulos[:3], 1):
        texto_articulos += f"\n--- Artículo {i} ---\nTítulo: {art['titulo']}\nContenido: {art['texto'][:1200]}\n"

    prompt = f"""Eres periodista deportiva española especializada en pádel femenino.
Escribes para una web dirigida a aficionadas españolas. Tu estilo es directo, natural y periodístico — como lo haría un redactor de Marca o As, pero sin ser rimbombante.

IDIOMA: español de España. Sin calcos del inglés ni traducciones literales.

EJEMPLOS de buen titular:
- "Triay y Brea arrasan en Roma y se acercan al récord de títulos"
- "Josemaría vuelve a ganar después de dos meses de baja por lesión"
- "Marta Ortega y Martina Calvo se meten en la final del Major de Suecia"

EJEMPLOS de buen resumen:
- "Gemma Triay y Delfina Brea no dieron opción en la final del Major de Roma. Las número 1 del mundo ganaron 6-2 6-1 en menos de una hora y suman su quinto título de la temporada. Con este resultado se colocan a solo un torneo del récord de victorias consecutivas."
- "Paula Josemaría vuelve a la competición tras perderse los últimos dos meses por una lesión en el hombro. La madrileña debutó ayer en Buenos Aires con victoria y confirmó que llega en buena forma al tramo final de la temporada."

Ahora escribe sobre estos artículos:
{texto_articulos}

Genera SOLO este JSON:
{{
  "titular": "Titular de máximo 12 palabras que cuente LO QUE PASÓ. NUNCA solo un nombre.",
  "resumen": "3-4 frases contando la noticia con datos concretos. Tono directo y natural, como si se lo contaras a una aficionada."
}}"""

    async with sem:
        try:
            resp = await asyncio.to_thread(lambda: client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                max_tokens=400,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            ))
            raw = resp.choices[0].message.content.strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                titular = data.get("titular", "").strip()
                resumen = data.get("resumen", "").strip()
                if titular and resumen:
                    fuentes = []
                    vistos = set()
                    for art in articulos[:4]:
                        url = art.get("url", "")
                        if url and url not in vistos:
                            domain = urlparse(url).netloc.replace("www.", "")
                            fuentes.append({"domain": domain, "titulo": art["titulo"], "url": url})
                            vistos.add(url)
                    return {"titular": titular, "resumen": resumen, "fuentes": fuentes}
        except Exception as e:
            print(f"  generar error: {e}")
    return None


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


async def get_latest_news() -> dict:
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
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
                })
        except Exception as e:
            print(f"  feed error {feed_url}: {e}")

    print(f"RSS: {len(raw)} artículos")

    ranking_aliases = await get_dynamic_ranking_aliases()
    femeninos = [
        art for art in raw
        if es_noticia_femenina(
            art["titulo"],
            art.get("texto", ""),
            ranking_aliases,
        )
    ]
    print(f"Filtro dinámico ranking + términos: {len(femeninos)} noticias femeninas")
    for art in femeninos:
        print(f"  ✓ {art['titulo'][:70]}")

    if not femeninos:
        return {"resumen_diario": [], "date": datetime.date.today().isoformat()}

    async with aiohttp.ClientSession() as session:
        textos = await asyncio.gather(
            *[scrape(session, art["url"]) for art in femeninos],
            return_exceptions=True
        )

    for art, texto in zip(femeninos, textos):
        if isinstance(texto, str) and len(texto) > len(art["texto"]):
            art["texto"] = texto

    grupos = agrupar(femeninos)[:4]
    print(f"Grupos: {len(grupos)}")

    sem = asyncio.Semaphore(3)
    generados = await asyncio.gather(
        *[generar_noticia(client, sem, g) for g in grupos],
        return_exceptions=True
    )

    noticias = [r for r in generados if isinstance(r, dict)]
    print(f"Noticias generadas: {len(noticias)}")

    # Fallback robusto: si Groq falla, se queda sin cuota o devuelve algo no parseable,
    # NO dejamos vacía la pestaña Noticias. Mostramos directamente las noticias
    # femeninas encontradas en los RSS con su titular, un resumen corto y la fuente.
    if not noticias:
        print("  noticias: usando fallback RSS sin IA")
        for art in femeninos[:4]:
            resumen = clean_text(art.get("texto", ""))
            if not resumen:
                resumen = "Última información publicada sobre el circuito profesional femenino de pádel."
            if len(resumen) > 420:
                resumen = resumen[:417].rsplit(" ", 1)[0] + "..."
            url = art.get("url", "")
            domain = urlparse(url).netloc.replace("www.", "") if url else ""
            fuentes = [{"domain": domain, "titulo": art.get("titulo", ""), "url": url}] if url else []
            noticias.append({
                "titular": art.get("titulo", "Noticia de pádel femenino"),
                "resumen": resumen,
                "fuentes": fuentes,
            })

    return {
        "date": datetime.date.today().isoformat(),
        "resumen_diario": noticias,
    }
