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

REGLA CRÍTICA:
- Resume EXCLUSIVAMENTE información del circuito femenino.
- No menciones jugadores masculinos, parejas masculinas ni resultados masculinos.
- Si alguna fuente contiene información mixta, ignora por completo la parte masculina.
- No inventes nombres ni resultados.

Ahora escribe sobre estos artículos ya filtrados:
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

    ranking_names = await get_dynamic_ranking_names()
    femeninos = [
        art for art in raw
        if es_noticia_femenina(
            art["titulo"],
            art.get("texto", ""),
            ranking_names,
        )
    ]
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
    for art, texto in zip(femeninos, textos):
        candidato = texto if isinstance(texto, str) and len(texto) > len(art["texto"]) else art["texto"]
        fragmento = _extraer_fragmentos_femeninos(candidato, ranking_names)

        # Si el scraping devuelve un artículo mixto sin contenido femenino
        # verificable, no se manda al modelo.
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
