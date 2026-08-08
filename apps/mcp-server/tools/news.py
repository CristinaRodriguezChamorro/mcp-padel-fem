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
from groq import Groq
from bs4 import BeautifulSoup
from urllib.parse import urlparse

RSS_FEEDS = [
    "https://e00-marca.uecdn.es/rss/padel.xml",
    "https://www.mundodeportivo.com/rss/padel.xml",
    "https://www.padelspain.net/feed/",
    "https://www.padeladdict.com/feed/",
    "https://www.setpadelmagazine.es/feed/",
]

JUGADORAS = [
    "triay", "brea", "josemaría", "josemaria", "bea gonzález", "bea gonzalez",
    "ari sánchez", "ari sanchez", "ustero", "marta ortega", "martina calvo",
    "salazar", "majo navarro", "claudia fernández", "claudia fernandez",
    "sofía araújo", "sofia araujo", "tamara icardo", "icardo",
    "ariana sánchez", "ariana sanchez", "paula josemaría", "paula josemaria",
    "gemma triay", "delfina brea",
]

MASCULINOS = [
    "galán", "galan", "chingotto", "coello", "tapia", "lebrón", "lebron",
    "augsburger", "paquito", "yanguas", "nieto", "goñi", "tello", "arce",
    "di nenno", "stupaczuk", "libaak", "franco guerrero",
]

BASURA = [
    "horarios", "dónde ver", "donde ver", "cómo ver", "como ver",
    "dónde y cómo", "donde y como", "entradas para", "comprar entradas",
]


FILTRO_FEMENINO = (
    "female", "women", "woman",
    "femenino", "femenina", "femeninos", "femeninas", "mujeres",
)


def es_noticia_femenina(titulo: str, texto: str = "") -> bool:
    """Filtro femenino compartiendo los términos usados en En juego."""
    t = f"{titulo} {texto}".lower()

    # Nombre de jugadora conocida = señal femenina fuerte.
    if any(j in t for j in JUGADORAS):
        return True

    # female / women / woman / femenino / femenina / mujeres.
    if any(f in t for f in FILTRO_FEMENINO):
        return True

    if any(b in titulo.lower() for b in BASURA):
        return False

    # Referencia únicamente masculina: descartar.
    if any(m in t for m in MASCULINOS):
        return False

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

    femeninos = [art for art in raw if es_noticia_femenina(art["titulo"])]
    print(f"Filtro: {len(femeninos)} noticias femeninas")
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
