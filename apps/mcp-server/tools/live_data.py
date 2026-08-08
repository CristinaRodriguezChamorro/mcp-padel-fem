"""
live_data.py — Ranking FIP y calendario Premier Padel

Ranking: scraping de padelspeak.com (HTML estático, se actualiza tras cada torneo)
Calendario: scraping de padelfip.com + Groq como fallback
"""

import asyncio
import aiohttp
import os
import re
import json
import io
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from groq import Groq
from pypdf import PdfReader

COUNTRY_FLAGS = {
    "ESP": "🇪🇸", "ARG": "🇦🇷", "POR": "🇵🇹", "ITA": "🇮🇹",
    "BRA": "🇧🇷", "FRA": "🇫🇷", "BEL": "🇧🇪", "URU": "🇺🇾",
    "PAR": "🇵🇾", "CHI": "🇨🇱", "COL": "🇨🇴", "MEX": "🇲🇽",
}


async def _fetch(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
            ) as resp:
                return await resp.text() if resp.status == 200 else ""
    except Exception as e:
        print(f"  _fetch error {url}: {e}")
        return ""



async def get_womens_ranking_names(limit: int = 200) -> list[str]:
    """
    Devuelve nombres del ranking femenino para alimentar otros pipelines,
    especialmente el filtro de Noticias.

    Se obtiene dinámicamente de la misma fuente de ranking y NO modifica
    el top-10 que se pinta en la interfaz.
    """
    html = await _fetch("https://padelspeak.com/en/padel-world-ranking-women/")
    if not html:
        print("  ranking names: fuente no disponible")
        return [p["name"] for p in _fallback_ranking()]

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        print("  ranking names: tabla no encontrada")
        return [p["name"] for p in _fallback_ranking()]

    names = []
    seen = set()
    for row in table.find_all("tr")[1:]:
        cols = row.find_all(["td", "th"])
        if len(cols) < 2:
            continue

        name = re.sub(r"\s+", " ", cols[1].get_text(" ", strip=True)).strip()
        if not name:
            continue

        key = name.casefold()
        if key in seen:
            continue

        seen.add(key)
        names.append(name)

        if len(names) >= limit:
            break

    if not names:
        return [p["name"] for p in _fallback_ranking()]

    print(f"  ranking names: {len(names)} jugadoras disponibles para filtros")
    return names


async def get_ranking_live() -> list:
    html = await _fetch("https://padelspeak.com/en/padel-world-ranking-women/")
    if not html:
        print("  ranking: no se pudo obtener padelspeak.com, usando fallback")
        return _fallback_ranking()

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        print("  ranking: no se encontró tabla, usando fallback")
        return _fallback_ranking()

    ranking = []
    rows = table.find_all("tr")

    for row in rows[1:]:
        cols = row.find_all(["td", "th"])
        if len(cols) < 4:
            continue
        try:
            pos_text = cols[0].get_text(strip=True)
            name_text = cols[1].get_text(strip=True)
            country_text = cols[2].get_text(strip=True).upper()[:3]
            pts_text = cols[3].get_text(strip=True).replace("\xa0", "").replace(" ", "")

            if not pos_text.isdigit() and pos_text not in ["1", "2", "3"]:
                continue

            pos = int(pos_text)
            name_parts = name_text.split()
            name = " ".join(name_parts[:3]) if len(name_parts) > 2 else name_text
            flag = COUNTRY_FLAGS.get(country_text, "🌍")

            try:
                pts_num = int(re.sub(r'[^\d]', '', pts_text))
                pts = f"{pts_num:,}".replace(",", ".")
            except Exception:
                pts = pts_text

            ranking.append({"pos": pos, "name": name, "pair": "", "flag": flag, "pts": pts})

            if len(ranking) >= 10:
                break

        except Exception as e:
            print(f"  ranking row error: {e}")
            continue

    if len(ranking) < 5:
        print(f"  ranking: solo {len(ranking)} filas parseadas, usando fallback")
        return _fallback_ranking()

    _add_pairs(ranking)
    print(f"  ranking: {len(ranking)} jugadoras obtenidas de padelspeak.com")
    return ranking


def _add_pairs(ranking: list):
    PAIRS = {
        "gemma triay":    "Triay / Brea",
        "delfina brea":   "Triay / Brea",
        "beatriz gonz":   "González / Josemaría",
        "paula josem":    "González / Josemaría",
        "ariana s":       "Sánchez / Ustero",
        "andrea uster":   "Sánchez / Ustero",
        "claudia fern":   "Araújo / Fernández",
        "sofía araú":     "Araújo / Fernández",
        "sofia arau":     "Araújo / Fernández",
        "marta ortega":   "Calvo / Ortega",
        "martina calvo":  "Calvo / Ortega",
        "alejandra sal":  "Salazar / ?",
        "majo navarro":   "Navarro / ?",
    }
    for player in ranking:
        name_lower = player["name"].lower()
        for key, pair in PAIRS.items():
            if key in name_lower:
                player["pair"] = pair
                break


def _is_live(day_str: str, month_str: str) -> bool:
    """
    Calcula si un torneo está en juego HOY comparando las fechas reales.
    day_str ejemplo: "10-17", "1-7", "26-30"
    month_str ejemplo: "May", "Jun"
    """
    MONTHS = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    try:
        month_num = MONTHS.get(month_str[:3], 0)
        if not month_num:
            return False
        parts = re.findall(r'\d+', day_str)
        if len(parts) < 2:
            return False
        day_start, day_end = int(parts[0]), int(parts[1])
        today = date.today()
        year = today.year
        start = date(year, month_num, day_start)
        end   = date(year, month_num, day_end)
        return start <= today <= end
    except Exception:
        return False


async def get_calendar_live() -> list:
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

    html = await _fetch("https://padelspeak.com/en/premier-padel-calendar/")
    raw_text = ""
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "nav", "footer"]):
            tag.decompose()
        raw_text = soup.get_text(separator=" ", strip=True)[:4000]

    today_str = date.today().strftime("%d %b %Y")

    if raw_text:
        prompt = f"""Datos del calendario Premier Padel 2026:
{raw_text}

Hoy es {today_str}. Extrae los próximos 6 torneos a partir de hoy.
SOLO este JSON sin texto extra, y pon live:false en todos (lo calculo yo):
[{{"day":"10-17","month":"May","name":"Buenos Aires P1","place":"Buenos Aires 🇦🇷","badge":"p1","badgeText":"P1","tv":"Red Bull TV · Movistar+","live":false}},...]
badge: "major", "p1", "p2", "fip"."""
    else:
        prompt = f"""Próximos 6 torneos Premier Padel desde hoy {today_str}.
SOLO JSON, live:false en todos:
[{{"day":"10-17","month":"May","name":"Buenos Aires P1","place":"Buenos Aires 🇦🇷","badge":"p1","badgeText":"P1","tv":"Red Bull TV · Movistar+","live":false}},...]"""

    torneos = None
    try:
        resp = await asyncio.to_thread(lambda: client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=600,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        ))
        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            torneos = json.loads(match.group())
    except Exception as e:
        print(f"  calendar Groq error: {e}")

    if not torneos:
        torneos = _fallback_calendar()

    # Calcular live con fechas reales, ignorando lo que devuelva la IA
    for t in torneos:
        t["live"] = _is_live(t.get("day", ""), t.get("month", ""))

    return torneos


def _fallback_ranking() -> list:
    return [
        {"pos": 1,  "name": "Gemma Triay",      "pair": "Triay / Brea",         "flag": "🇪🇸", "pts": "17.840"},
        {"pos": 1,  "name": "Delfina Brea",      "pair": "Triay / Brea",         "flag": "🇦🇷", "pts": "17.840"},
        {"pos": 3,  "name": "Beatriz González",  "pair": "González / Josemaría", "flag": "🇪🇸", "pts": "14.880"},
        {"pos": 4,  "name": "Paula Josemaría",   "pair": "González / Josemaría", "flag": "🇪🇸", "pts": "14.720"},
        {"pos": 5,  "name": "Ariana Sánchez",    "pair": "Sánchez / Ustero",     "flag": "🇪🇸", "pts": "13.640"},
        {"pos": 6,  "name": "Claudia Fernández", "pair": "Araújo / Fernández",   "flag": "🇪🇸", "pts": "12.710"},
        {"pos": 7,  "name": "Andrea Ustero",     "pair": "Sánchez / Ustero",     "flag": "🇪🇸", "pts": "7.580"},
        {"pos": 8,  "name": "Sofía Araújo",      "pair": "Araújo / Fernández",   "flag": "🇵🇹", "pts": "7.200"},
        {"pos": 9,  "name": "Marta Ortega",      "pair": "Calvo / Ortega",       "flag": "🇪🇸", "pts": "6.900"},
        {"pos": 10, "name": "Martina Calvo",     "pair": "Calvo / Ortega",       "flag": "🇪🇸", "pts": "6.850"},
    ]


def _fallback_calendar() -> list:
    return [
        {"day": "10-17", "month": "May", "name": "Buenos Aires P1",      "place": "Buenos Aires 🇦🇷", "badge": "p1",    "badgeText": "P1",           "tv": "Red Bull TV · Movistar+", "live": False},
        {"day": "26-30", "month": "May", "name": "FIP Platinum Albania",  "place": "Tirana 🇦🇱",       "badge": "fip",   "badgeText": "FIP Platinum", "tv": "Movistar+",               "live": False},
        {"day": "1-7",   "month": "Jun", "name": "Italy Major",           "place": "Roma 🇮🇹",          "badge": "major", "badgeText": "MAJOR",        "tv": "Red Bull TV · Movistar+", "live": False},
        {"day": "8-14",  "month": "Jun", "name": "Valencia P1",           "place": "Valencia 🇪🇸",      "badge": "p1",    "badgeText": "P1",           "tv": "Red Bull TV · Movistar+", "live": False},
        {"day": "15-21", "month": "Jun", "name": "FIP Platinum Portugal", "place": "Paredes 🇵🇹",       "badge": "fip",   "badgeText": "FIP Platinum", "tv": "Movistar+",               "live": False},
        {"day": "22-28", "month": "Jun", "name": "Sweden Major",          "place": "Gotemburgo 🇸🇪",    "badge": "major", "badgeText": "MAJOR",        "tv": "Red Bull TV · Movistar+", "live": False},
    ]

# ── TORNEO EN JUEGO + ÚLTIMOS RESULTADOS FEMENINOS ───────────────────────────
# Fuentes oficiales únicamente:
#   - FIP (padelfip.com): torneo activo, ubicación, fechas y marcadores.
#   - Premier Padel (premierpadel.com): dónde verlo en España.

from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

FIP_LIVE_URL = "https://www.padelfip.com/live/"
FIP_PREMIER_CALENDAR_URL = "https://www.padelfip.com/calendar-premier-padel/?events-year={year}"
PREMIER_WATCH_URL = "https://premierpadel.com/en/news/where-to-watch-dont-miss-any-of-the-action-at-any-tournament"


def _clean_text(node) -> str:
    if not node:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _parse_fip_dates(text: str):
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s*(?:-|–|to|a|al)\s*(\d{1,2}/\d{1,2}/\d{4})", text, re.I)
    if not m:
        dates = re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text)
        if len(dates) < 2:
            return None
        a, b = dates[0], dates[1]
    else:
        a, b = m.group(1), m.group(2)
    try:
        return datetime.strptime(a, "%d/%m/%Y").date(), datetime.strptime(b, "%d/%m/%Y").date()
    except Exception:
        return None


def _is_premier_name(text: str) -> bool:
    return bool(re.search(r"\b(?:P1|P2|MAJOR|FINALS?)\b", text, re.I))


def _format_dates_es(start, end) -> str:
    months = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
              "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    if start.month == end.month:
        return f"{start.day}–{end.day} {months[start.month]} {end.year}"
    return f"{start.day} {months[start.month]} – {end.day} {months[end.month]} {end.year}"


async def _get_official_live_event() -> dict | None:
    """
    Localiza el Premier Padel activo HOY por FECHAS, no por la etiqueta "Live".
    Esto es importante porque la portada/calendario de FIP puede tardar en cambiar
    el estado visual de "Registration Closed" a "Live".
    """
    today = date.today()
    calendar_url = FIP_PREMIER_CALENDAR_URL.format(year=today.year)
    html = await _fetch(calendar_url)

    # Fallback a la página de live si el calendario falla.
    if not html:
        html = await _fetch(FIP_LIVE_URL)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/events/" not in href and "/eventos/" not in href:
            continue

        event_url = urljoin(calendar_url, href)
        if event_url in seen:
            continue
        seen.add(event_url)

        node = a
        block = ""
        for _ in range(8):
            node = getattr(node, "parent", None)
            if not node:
                break
            candidate = _clean_text(node)
            if len(candidate) > len(block):
                block = candidate
            if _parse_fip_dates(candidate) and _is_premier_name(candidate):
                block = candidate
                break

        if not _is_premier_name(block):
            continue

        dates = _parse_fip_dates(block)
        if not dates:
            continue
        start, end = dates
        if not (start <= today <= end):
            continue

        event_html = await _fetch(event_url)
        if not event_html:
            continue

        event_soup = BeautifulSoup(event_html, "html.parser")
        event_text = _clean_text(event_soup)

        # El evento debe incluir cuadro femenino.
        if not re.search(r"\b(Female|Women|Femenino|Mujeres)\b", event_text, re.I):
            continue

        h1 = event_soup.find("h1")
        name = _clean_text(h1) if h1 else ""
        if not name or not _is_premier_name(name):
            # El texto del bloque suele empezar por el nombre del torneo.
            m_name = re.search(
                r"([A-ZÁÉÍÓÚÜÑ0-9 .'-]+(?:P1|P2|MAJOR|FINALS))",
                block,
                re.I,
            )
            name = m_name.group(1).strip() if m_name else "Premier Padel"

        place = ""
        # Primero intentamos extraer "Ciudad - País" del encabezado oficial.
        m_place = re.search(
            r"([A-Za-zÀ-ÿ .'-]+?\s*-\s*[A-Za-zÀ-ÿ .'-]+?)\s+"
            r"\d{1,2}/\d{1,2}/\d{4}",
            event_text,
        )
        if m_place:
            place = re.sub(r"\s+", " ", m_place.group(1)).strip()

        return {
            "name": name.upper(),
            "place": place,
            "dates": _format_dates_es(start, end),
            "url": event_url,
            "html": event_html,
        }

    return None


async def _get_watch_official() -> list[str]:
    """Dónde ver Premier Padel desde España según la web oficial del circuito."""
    html = await _fetch(PREMIER_WATCH_URL)
    if not html:
        return ["Premier Padel YouTube", "Red Bull TV", "Movistar+"]

    text = _clean_text(BeautifulSoup(html, "html.parser"))
    watch = []
    if re.search(r"Premier Padel YouTube", text, re.I):
        watch.append("Premier Padel YouTube")
    if re.search(r"Red\s*Bull\s*TV", text, re.I):
        watch.append("Red Bull TV")
    if re.search(r"Movistar", text, re.I) and re.search(r"Spain", text, re.I):
        watch.append("Movistar+")
    return watch or ["Premier Padel YouTube", "Red Bull TV"]



def _gender_aliases(gender: str) -> tuple[str, ...]:
    gender = (gender or "female").strip().lower()
    if gender == "male":
        return ("male", "men", "masculino", "hombres")
    return ("female", "women", "woman", "femenino", "femenina", "mujeres")


def _add_query(url: str, **params) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items()})
    return urlunparse(parsed._replace(query=urlencode(query)))


async def _get_gender_filtered_event_html(event: dict, gender: str = "female") -> str:
    # Filtro usado SOLO por la pestaña En juego.
    base_html = event.get("html", "")
    base_url = event.get("url", "")
    if not base_html or not base_url:
        return base_html

    aliases = _gender_aliases(gender)
    soup = BeautifulSoup(base_html, "html.parser")
    candidates = []

    for el in soup.find_all(True):
        for attr in ("href", "src", "data-url", "data-href", "data-src", "data-endpoint", "data-ajax-url", "action"):
            value = el.get(attr)
            if isinstance(value, str) and any(a in value.lower() for a in aliases):
                candidates.append(urljoin(base_url, value))

    canonical = "female" if gender != "male" else "male"
    candidates.extend([
        _add_query(base_url, gender=canonical),
        _add_query(base_url, sex=canonical),
        _add_query(base_url, category=canonical),
        _add_query(base_url, division=canonical),
    ])

    base_host = urlparse(base_url).netloc.lower()
    unique = []
    seen = set()
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        host = urlparse(url).netloc.lower()
        if host and host != base_host:
            continue
        unique.append(url)

    def score(html: str) -> int:
        if not html:
            return -1
        text = _clean_text(BeautifulSoup(html, "html.parser")).lower()
        gender_hits = sum(text.count(a) for a in aliases)
        result_hits = sum(text.count(k) for k in ("result", "draw", "score", "round"))
        return gender_hits + (result_hits * 3)

    best_html = base_html
    best_score = score(base_html)

    for url in unique[:8]:
        html = await _fetch(url)
        sc = score(html)
        if sc > best_score:
            best_html = html
            best_score = sc

    return best_html


ROUND_ORDER = {
    "Final": 100,
    "Semifinales": 90,
    "Cuartos de final": 80,
    "Octavos de final": 70,
    "Segunda ronda": 60,
    "Primera ronda": 50,
    "Clasificación": 40,
}

PLACE_TIMEZONES = {
    "london": "Europe/London",
    "madrid": "Europe/Madrid",
    "valencia": "Europe/Madrid",
    "gijón": "Europe/Madrid",
    "gijon": "Europe/Madrid",
    "málaga": "Europe/Madrid",
    "malaga": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "roma": "Europe/Rome",
    "rome": "Europe/Rome",
    "paris": "Europe/Paris",
    "bordeaux": "Europe/Paris",
    "brussels": "Europe/Brussels",
    "rotterdam": "Europe/Amsterdam",
    "milano": "Europe/Rome",
    "milan": "Europe/Rome",
    "malmö": "Europe/Stockholm",
    "malmo": "Europe/Stockholm",
    "doha": "Asia/Qatar",
    "dubai": "Asia/Dubai",
    "riyadh": "Asia/Riyadh",
    "asunción": "America/Asuncion",
    "asuncion": "America/Asuncion",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "miami": "America/New_York",
    "new york": "America/New_York",
}


def _normalize_round(value: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    low = raw.lower()
    if not raw:
        return "Partidos"
    if re.search(r"\b(final|f)\b", low) and "semi" not in low and "quarter" not in low:
        return "Final"
    if "semi" in low or re.search(r"\bsf\b", low):
        return "Semifinales"
    if "quarter" in low or "cuarto" in low or re.search(r"\bqf\b", low):
        return "Cuartos de final"
    if "round of 16" in low or "r16" in low or "octav" in low:
        return "Octavos de final"
    if "2nd round" in low or "second round" in low or "segunda" in low or re.search(r"\br2\b", low):
        return "Segunda ronda"
    if "1st round" in low or "first round" in low or "primera" in low or re.search(r"\br1\b", low):
        return "Primera ronda"
    if "qual" in low or "clasif" in low:
        return "Clasificación"
    return raw[:60]


def _event_timezone(place: str) -> str:
    low = (place or "").lower()
    for key, tz in PLACE_TIMEZONES.items():
        if key in low:
            return tz
    return "Europe/Madrid"


def _extract_pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        print(f"  PDF extract error: {exc}")
        return ""


async def _fetch_bytes(url: str) -> bytes:
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "Mozilla/5.0 (compatible; PadelFem/1.0)"}
        ) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return b""
                return await resp.read()
    except Exception as exc:
        print(f"  fetch bytes error {url}: {exc}")
        return b""


def _official_document_urls(event: dict) -> dict:
    """Extrae del HTML oficial enlaces de Results/Draw y Order of Play."""
    soup = BeautifulSoup(event.get("html", ""), "html.parser")
    base = event.get("url", "")
    result_docs, oop_docs = [], []

    for a in soup.find_all("a", href=True):
        href = urljoin(base, a.get("href", ""))
        text = _clean_text(a).lower()
        low = href.lower()
        if not href:
            continue
        if ("order of play" in text or "order-of-play" in low or "order_of_play" in low):
            oop_docs.append(href)
        if (
            "result" in text or "result" in low or
            "draw" in text or "main-draw" in low or "main_draw" in low
        ):
            result_docs.append(href)

    # Hidden/dynamic attributes often contain the Female endpoint/PDF.
    for el in soup.find_all(True):
        blob = " ".join(
            str(el.get(k, "")) for k in
            ("data-url", "data-href", "data-pdf", "data-src", "data-endpoint", "href")
        )
        if not blob:
            continue
        for candidate in re.findall(r'https?://[^\s"\']+|/[^\s"\']+', blob):
            url = urljoin(base, candidate)
            low = url.lower()
            if "order" in low and "play" in low:
                oop_docs.append(url)
            if any(k in low for k in ("result", "draw")):
                result_docs.append(url)

    return {
        "results": list(dict.fromkeys(result_docs)),
        "oop": list(dict.fromkeys(oop_docs)),
    }


async def _collect_official_result_text(event: dict, gender: str) -> str:
    """
    Reúne datos de Results/Draws desde la fuente oficial.
    Prioriza documentos/URLs con female/women y conserva el HTML filtrado como fallback.
    """
    gender = "female" if gender in {"female", "women", "woman"} else gender
    aliases = _gender_aliases(gender)
    docs = _official_document_urls(event)["results"]

    def priority(url: str) -> tuple:
        low = url.lower()
        has_gender = any(a in low for a in aliases)
        is_pdf = low.endswith(".pdf") or ".pdf?" in low
        return (has_gender, is_pdf)

    docs = sorted(docs, key=priority, reverse=True)
    chunks = []

    for url in docs[:12]:
        low = url.lower()
        # Evitamos documentos explícitamente masculinos.
        if gender == "female" and any(x in low for x in ("-men-", "_men_", "/men/", "male")) and not any(
            x in low for x in ("women", "female")
        ):
            continue

        if ".pdf" in low:
            raw = await _fetch_bytes(url)
            text = _extract_pdf_text(raw)
        else:
            html = await _fetch(url)
            text = _clean_text(BeautifulSoup(html, "html.parser")) if html else ""

        if text and len(text) > 150:
            chunks.append(f"FUENTE {url}\n{text}")

    filtered_html = await _get_gender_filtered_event_html(event, gender)
    filtered_text = _clean_text(BeautifulSoup(filtered_html, "html.parser")) if filtered_html else ""
    if filtered_text:
        chunks.append("PÁGINA DEL EVENTO / RESULTS\n" + filtered_text)

    return "\n\n".join(chunks)[:140000]


async def _extract_next_womens_match(event: dict) -> dict | None:
    """
    Lee el Order of Play oficial y obtiene el siguiente partido femenino.
    Las horas se convierten a Europe/Madrid.
    """
    docs = _official_document_urls(event)["oop"]
    if not docs:
        return None

    texts = []
    for url in docs[:6]:
        if ".pdf" in url.lower():
            raw = await _fetch_bytes(url)
            txt = _extract_pdf_text(raw)
        else:
            html = await _fetch(url)
            txt = _clean_text(BeautifulSoup(html, "html.parser")) if html else ""
        if txt and re.search(r"\b(Women|Female)\b", txt, re.I):
            texts.append(txt)

    if not texts:
        return None

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None

    source = "\n\n".join(texts)[:50000]
    client = Groq(api_key=api_key)
    prompt = f"""Extrae SOLO los partidos FEMENINOS del Order of Play oficial FIP.
No inventes nada.

DATOS:
{source}

Devuelve SOLO JSON:
[
  {{
    "round":"SF",
    "pair1":"Apellido / Apellido",
    "pair2":"Apellido / Apellido",
    "date":"2026-08-08",
    "time":"12:00",
    "time_type":"exact"
  }}
]

Reglas:
- pair1 y pair2: exactamente dos jugadoras separadas por " / ".
- round puede ser F, SF, QF, R16, R2, R1, Qualifying.
- date ISO YYYY-MM-DD si se puede leer.
- time en HH:MM 24h solo si hay hora exacta o "not before".
- time_type: "exact", "not_before" o "followed_by".
- Si dice "Followed by", time puede quedar vacío.
- Solo mujeres.
"""
    try:
        resp = await asyncio.to_thread(lambda: client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=2000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        ))
        raw = resp.choices[0].message.content.strip()
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        matches = json.loads(m.group()) if m else []
    except Exception as exc:
        print(f"  next match extraction error: {exc}")
        return None

    event_tz = ZoneInfo(_event_timezone(event.get("place", "")))
    madrid_tz = ZoneInfo("Europe/Madrid")
    now_madrid = datetime.now(madrid_tz)

    normalized = []
    for item in matches:
        pair1 = str(item.get("pair1", "")).strip()
        pair2 = str(item.get("pair2", "")).strip()
        rnd = _normalize_round(str(item.get("round", "")))
        d = str(item.get("date", "")).strip()
        tm = str(item.get("time", "")).strip()
        time_type = str(item.get("time_type", "exact")).strip()

        if "/" not in pair1 or "/" not in pair2:
            continue

        dt_madrid = None
        if d and tm and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and re.fullmatch(r"\d{2}:\d{2}", tm):
            try:
                local_dt = datetime.strptime(f"{d} {tm}", "%Y-%m-%d %H:%M").replace(tzinfo=event_tz)
                dt_madrid = local_dt.astimezone(madrid_tz)
            except Exception:
                pass

        normalized.append({
            "round": rnd,
            "pair1": pair1,
            "pair2": pair2,
            "time_type": time_type,
            "datetime_madrid": dt_madrid,
        })

    if not normalized:
        return None

    # Primero un partido que todavía no haya empezado; si no hay hora exacta,
    # mantenemos el orden oficial.
    future = [m for m in normalized if m["datetime_madrid"] and m["datetime_madrid"] >= now_madrid - timedelta(minutes=15)]
    chosen = min(future, key=lambda x: x["datetime_madrid"]) if future else normalized[0]

    dt = chosen.pop("datetime_madrid", None)
    if dt:
        today = now_madrid.date()
        if dt.date() == today:
            day_label = "Hoy"
        elif dt.date() == today + timedelta(days=1):
            day_label = "Mañana"
        else:
            day_label = dt.strftime("%d/%m")
        prefix = "No antes de " if chosen.get("time_type") == "not_before" else ""
        chosen["when"] = f"{day_label} · {prefix}{dt.strftime('%H:%M')} (hora de España)"
        chosen["iso_madrid"] = dt.isoformat()
    else:
        chosen["when"] = "A continuación · hora por confirmar"
        chosen["iso_madrid"] = ""

    return chosen


async def _extract_official_results(event: dict, gender: str = "female") -> list:
    """
    Resultados finalizados del torneo en curso.
    Lee Results/Draws oficiales y conserva la ronda para agruparlos en la interfaz.
    """
    gender = (gender or "female").strip().lower()
    if gender in {"women", "woman"}:
        gender = "female"
    if gender not in {"female", "male"}:
        gender = "female"

    source = await _collect_official_result_text(event, gender)
    if not source:
        return []

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("  live results: GROQ_API_KEY no configurada")
        return []

    client = Groq(api_key=api_key)
    requested_label = "FEMENINO" if gender == "female" else "MASCULINO"
    prompt = f"""Extrae TODOS los partidos FINALIZADOS del cuadro {requested_label}
que estén demostrados en estas fuentes OFICIALES FIP.

La página Results puede contener varios días. No esperes al final del torneo:
si un partido ya está finalizado, inclúyelo.

FUENTE FIP:
{source}

Devuelve SOLO JSON:
[
  {{
    "round":"Quarter-finals",
    "winner":"Apellido / Apellido",
    "loser":"Apellido / Apellido",
    "score":"6-3, 7-5",
    "date":"2026-08-07"
  }}
]

Reglas:
- Solo {requested_label}.
- winner y loser son parejas de exactamente 2 jugadoras/jugadores.
- winner debe ser el vencedor oficial.
- score desde el punto de vista del ganador.
- Solo partidos con marcador final.
- round: Final, Semi-finals, Quarter-finals, Round of 16, 2nd Round, 1st Round o Qualifying.
- date ISO si aparece el día; si no, "".
- No inventes nombres, ronda, fecha ni marcador.
"""

    try:
        resp = await asyncio.to_thread(lambda: client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=7000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        ))
        raw = resp.choices[0].message.content.strip()
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        parsed = json.loads(m.group()) if m else []
    except Exception as exc:
        print(f"  official FIP result extraction error: {exc}")
        return []

    normalized_source = re.sub(r"\s+", " ", source).lower()
    valid = []
    seen = set()

    for item in parsed[:80]:
        winner = str(item.get("winner", "")).strip()
        loser = str(item.get("loser", "")).strip()
        score = str(item.get("score", "")).strip()
        rnd = _normalize_round(str(item.get("round", "")))
        match_date = str(item.get("date", "")).strip()

        if not winner or not loser or not score or "/" not in winner or "/" not in loser:
            continue
        if not re.fullmatch(r"\d{1,2}-\d{1,2}(?:\s*,\s*\d{1,2}-\d{1,2}){1,2}", score):
            continue

        # Validación mínima contra la fuente oficial.
        names = [x.strip().lower() for x in re.split(r"/", winner + "/" + loser) if x.strip()]
        surnames = [n.split()[-1] for n in names if n.split()]
        if len(surnames) != 4 or not all(tok in normalized_source for tok in surnames):
            continue

        key = (rnd.lower(), winner.lower(), loser.lower(), score)
        if key in seen:
            continue
        seen.add(key)

        valid.append({
            "round": rnd,
            "winner": winner,
            "loser": loser,
            "score": score.replace(",", "  "),
            "date": match_date,
        })

    # Ronda más avanzada primero; dentro de ronda, fecha más reciente primero.
    valid.sort(
        key=lambda x: (
            ROUND_ORDER.get(x["round"], 0),
            x.get("date", ""),
        ),
        reverse=True,
    )
    return valid


async def get_tournament_now(gender: str = "female") -> dict:
    """
    Torneo actual + resultados femeninos acumulados mientras el torneo está en curso.
    No se espera a la final: cada partido terminado se muestra en cuanto FIP lo publica.
    """
    today_str = date.today().strftime("%d/%m/%Y")
    event = await _get_official_live_event()
    if not event:
        return {
            "active": False, "name": "", "place": "", "dates": "",
            "watch": [], "results": [], "next_match": None, "updated": today_str,
            "gender": gender,
            "source": "FIP", "source_url": FIP_PREMIER_CALENDAR_URL.format(year=date.today().year),
        }

    parts = await asyncio.gather(
        _get_watch_official(),
        _extract_official_results(event, gender),
        _extract_next_womens_match(event) if gender in {"female", "women", "woman"} else asyncio.sleep(0, result=None),
        return_exceptions=True,
    )

    watch, results, next_match = parts

    if isinstance(watch, Exception):
        print(f"  live watch error: {watch}")
        watch = []
    if isinstance(results, Exception):
        print(f"  live results error: {results}")
        results = []
    if isinstance(next_match, Exception):
        print(f"  live next-match error: {next_match}")
        next_match = None

    return {
        "active": True,
        "name": event["name"],
        "place": event["place"],
        "dates": event["dates"],
        "watch": watch or [],
        "results": results or [],
        "next_match": next_match,
        "updated": today_str,
        "gender": gender,
        "source": "FIP · Premier Padel",
        "source_url": event["url"],
    }
