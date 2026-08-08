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
import unicodedata
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from groq import Groq
from pypdf import PdfReader
try:
    from playwright.async_api import async_playwright
except Exception:
    async_playwright = None

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
    """
    Calendario SIN Groq.
    Intenta parsear directamente padelspeak.com y cae al calendario local si la
    estructura externa cambia.
    """
    html = await _fetch("https://padelspeak.com/en/premier-padel-calendar/")
    torneos = []

    if html:
        try:
            soup = BeautifulSoup(html, "html.parser")

            # 1) Tables
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                for row in rows[1:]:
                    cols = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip()
                            for c in row.find_all(["td", "th"])]
                    if len(cols) < 2:
                        continue
                    text = " | ".join(cols)
                    if not re.search(r"\b(P1|P2|Major|Finals?|FIP)\b", text, re.I):
                        continue

                    # Date/range
                    dm = re.search(
                        r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s+"
                        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
                        text, re.I
                    )
                    if not dm:
                        continue
                    d1, d2, mon = dm.group(1), dm.group(2), dm.group(3).title()
                    day = f"{d1}-{d2}" if d2 else d1

                    # Tournament name = cell containing event class.
                    name = next((c for c in cols if re.search(r"\b(P1|P2|Major|Finals?|FIP)\b", c, re.I)), "")
                    place = cols[-1] if cols[-1] != name else ""

                    badge = "major" if "major" in name.lower() else ("p2" if "p2" in name.lower() else ("fip" if "fip" in name.lower() else "p1"))
                    badge_text = "MAJOR" if badge == "major" else ("FIP" if badge == "fip" else badge.upper())

                    torneos.append({
                        "day": day,
                        "month": mon,
                        "name": name,
                        "place": place,
                        "badge": badge,
                        "badgeText": badge_text,
                        "tv": "Red Bull TV · Movistar+",
                        "live": False,
                    })

            # 2) Cards/articles fallback
            if not torneos:
                for node in soup.find_all(["article", "li", "div"]):
                    text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                    if len(text) > 500 or not re.search(r"\b(P1|P2|Major|Finals?)\b", text, re.I):
                        continue
                    dm = re.search(
                        r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s+"
                        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
                        text, re.I
                    )
                    nm = re.search(r"([A-Za-zÀ-ÿ0-9 .'-]+?\b(?:P1|P2|Major|Finals?))", text, re.I)
                    if not dm or not nm:
                        continue
                    d1, d2, mon = dm.group(1), dm.group(2), dm.group(3).title()
                    name = re.sub(r"\s+", " ", nm.group(1)).strip()
                    badge = "major" if "major" in name.lower() else ("p2" if "p2" in name.lower() else "p1")
                    torneos.append({
                        "day": f"{d1}-{d2}" if d2 else d1,
                        "month": mon,
                        "name": name,
                        "place": "",
                        "badge": badge,
                        "badgeText": "MAJOR" if badge == "major" else badge.upper(),
                        "tv": "Red Bull TV · Movistar+",
                        "live": False,
                    })
        except Exception as exc:
            print(f"  calendar parser error: {exc}")

    # Deduplicate and keep future/current 6.
    if not torneos:
        torneos = _fallback_calendar()
    else:
        dedup = []
        seen = set()
        for t in torneos:
            key = (t["name"].lower(), t["day"], t["month"])
            if key not in seen:
                seen.add(key)
                dedup.append(t)
        torneos = dedup

    for t in torneos:
        t["live"] = _is_live(t.get("day", ""), t.get("month", ""))

    # Current/live first, then as source order; cap to 6 for UI.
    live_now = [t for t in torneos if t["live"]]
    rest = [t for t in torneos if not t["live"]]
    return (live_now + rest)[:6]


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

_LIVE_DEBUG_STATE = {}

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
    Extrae el próximo partido femenino SIN LLM.
    Usa Order of Play oficial (PDF/HTML) y parsing determinista.
    """
    docs = _official_document_urls(event)["oop"]
    if not docs:
        return None

    source_parts = []
    for url in docs[:8]:
        try:
            if ".pdf" in url.lower():
                raw = await _fetch_bytes(url)
                txt = _extract_pdf_text(raw)
            else:
                html = await _fetch(url)
                txt = _clean_text(BeautifulSoup(html, "html.parser")) if html else ""
            if txt:
                source_parts.append(txt)
        except Exception:
            continue

    source = "\n".join(source_parts)
    if not source:
        return None

    lines = [re.sub(r"\s+", " ", ln).strip() for ln in source.splitlines()]
    lines = [ln for ln in lines if ln]

    # Find female/women section if explicit.
    female_idx = None
    for i, ln in enumerate(lines):
        if re.search(r"\b(women|female|femenin)", ln, re.I):
            female_idx = i
            break
    if female_idx is not None:
        lines = lines[female_idx:]

    # Round aliases.
    round_patterns = [
        (r"\bfinal\b", "Final"),
        (r"\bsemi[- ]?finals?\b|\bsf\b", "Semifinales"),
        (r"\bquarter[- ]?finals?\b|\bqf\b", "Cuartos de final"),
        (r"\bround of 16\b|\br16\b", "Octavos de final"),
        (r"\b2nd round\b|\bsecond round\b|\br2\b", "Segunda ronda"),
        (r"\b1st round\b|\bfirst round\b|\br1\b", "Primera ronda"),
        (r"\bqual", "Clasificación"),
    ]

    current_round = ""
    current_date = ""
    current_time = ""
    current_time_type = "exact"

    # Simple date recognizers.
    date_re_iso = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
    date_re_dmy = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b")
    time_re = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

    # Candidate player line heuristic: name-like line, no scores/times/headers.
    def is_player_line(ln: str) -> bool:
        if len(ln) < 3 or len(ln) > 80:
            return False
        if time_re.search(ln):
            return False
        if re.search(r"\b(court|center|centre|order of play|women|female|men|male|final|semi|quarter|round|qual|not before|followed by)\b", ln, re.I):
            return False
        if re.search(r"\b\d{1,2}[-–]\d{1,2}\b", ln):
            return False
        return bool(re.search(r"[A-Za-zÀ-ÿ]", ln))

    candidates = []

    for i, ln in enumerate(lines):
        low = ln.lower()

        for pat, label in round_patterns:
            if re.search(pat, low, re.I):
                current_round = label
                break

        m = date_re_iso.search(ln)
        if m:
            current_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        else:
            m = date_re_dmy.search(ln)
            if m:
                current_date = f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

        tm = time_re.search(ln)
        if tm:
            current_time = f"{int(tm.group(1)):02d}:{int(tm.group(2)):02d}"
            current_time_type = "not_before" if "not before" in low else "exact"

        # Pair patterns: "A / B vs C / D" or "A & B vs C & D"
        pair_match = re.search(
            r"(.+?(?:/|&).+?)\s+(?:vs\.?|v\.?)\s+(.+?(?:/|&).+)",
            ln,
            re.I,
        )
        if pair_match:
            p1 = re.sub(r"\s*&\s*", " / ", pair_match.group(1)).strip(" -")
            p2 = re.sub(r"\s*&\s*", " / ", pair_match.group(2)).strip(" -")
            candidates.append({
                "round": current_round or "Partido",
                "pair1": p1,
                "pair2": p2,
                "date": current_date,
                "time": current_time,
                "time_type": current_time_type,
            })
            continue

        # Fallback: four consecutive player-like lines around a time.
        if current_time and i + 3 < len(lines):
            block = lines[i:i+4]
            if all(is_player_line(x) for x in block):
                candidates.append({
                    "round": current_round or "Partido",
                    "pair1": f"{block[0]} / {block[1]}",
                    "pair2": f"{block[2]} / {block[3]}",
                    "date": current_date,
                    "time": current_time,
                    "time_type": current_time_type,
                })

    if not candidates:
        return None

    event_tz = ZoneInfo(_event_timezone(event.get("place", "")))
    madrid_tz = ZoneInfo("Europe/Madrid")
    now_madrid = datetime.now(madrid_tz)

    normalized = []
    seen = set()
    for item in candidates:
        key = (item["pair1"].lower(), item["pair2"].lower(), item["date"], item["time"])
        if key in seen:
            continue
        seen.add(key)

        dt_madrid = None
        if item["date"] and item["time"]:
            try:
                local_dt = datetime.strptime(
                    f"{item['date']} {item['time']}",
                    "%Y-%m-%d %H:%M"
                ).replace(tzinfo=event_tz)
                dt_madrid = local_dt.astimezone(madrid_tz)
            except Exception:
                pass

        item["datetime_madrid"] = dt_madrid
        normalized.append(item)

    future = [
        x for x in normalized
        if x["datetime_madrid"] and x["datetime_madrid"] >= now_madrid - timedelta(minutes=15)
    ]
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
        chosen["when"] = "Horario por confirmar"
        chosen["iso_madrid"] = ""

    return chosen


def _norm_person_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value).lower()
    return re.sub(r"\s+", " ", value).strip()


async def _browser_fip_womens_results_text(event: dict) -> str:
    """
    FIP carga Results / Female / fecha mediante JavaScript.

    Requests/BeautifulSoup solo reciben de forma consistente el cuadro masculino
    inicial. Por eso esta función reproduce exactamente lo que hace una persona:
    abre el evento, entra en Results, selecciona Female y recorre los días
    disponibles, recogiendo el texto ya renderizado por el navegador.
    """
    if async_playwright is None:
        print("  FIP browser: Playwright no disponible")
        return ""

    url = event.get("url", "")
    if not url:
        return ""

    snapshots = []
    browser = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(
                viewport={"width": 1440, "height": 1200},
                locale="en-US",
            )

            # tab=Results ayuda a aterrizar en la pestaña correcta cuando FIP lo respeta.
            results_url = url + ("&" if "?" in url else "?") + "tab=Results"
            await page.goto(results_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1800)

            # 1) Abrir Results si la URL no ha activado la pestaña.
            for label in ("Results", "Resultados"):
                try:
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(timeout=2500)
                        await page.wait_for_timeout(1000)
                        break
                except Exception:
                    pass

            # 2) Seleccionar Female/Femenino. FIP usa controles custom, por eso
            # probamos label/button/text y también inputs cercanos.
            female_clicked = False
            for label in ("Female", "Femenino", "Women"):
                try:
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(timeout=2500)
                        await page.wait_for_timeout(1200)
                        female_clicked = True
                        break
                except Exception:
                    pass

            if not female_clicked:
                # Fallback: labels asociados a radio/checkbox.
                try:
                    labels = page.locator("label")
                    for i in range(await labels.count()):
                        txt = (await labels.nth(i).inner_text()).strip().lower()
                        if txt in {"female", "femenino", "women"}:
                            await labels.nth(i).click()
                            await page.wait_for_timeout(1200)
                            female_clicked = True
                            break
                except Exception:
                    pass

            async def capture(tag: str):
                try:
                    body = await page.locator("body").inner_text(timeout=5000)
                    if body and len(body) > 200:
                        snapshots.append(f"\n=== {tag} ===\n{body}")
                except Exception:
                    pass

            await capture("female-current")

            # 3) Recorrer selects que parezcan fechas/días.
            selects = page.locator("select")
            for si in range(await selects.count()):
                sel = selects.nth(si)
                try:
                    options = await sel.locator("option").all()
                    option_data = []
                    for opt in options:
                        txt = (await opt.inner_text()).strip()
                        val = await opt.get_attribute("value")
                        if txt and re.search(
                            r"(?:\bMon\b|\bTue\b|\bWed\b|\bThu\b|\bFri\b|\bSat\b|\bSun\b|"
                            r"\bMonday\b|\bTuesday\b|\bWednesday\b|\bThursday\b|\bFriday\b|\bSaturday\b|\bSunday\b|"
                            r"\d{1,2}[/-]\d{1,2}|\d{1,2}\s+(?:Aug|August))",
                            txt, re.I
                        ):
                            option_data.append((val, txt))
                    # Limitamos a días del torneo, no selects genéricos.
                    if 1 <= len(option_data) <= 12:
                        for val, txt in option_data:
                            try:
                                if val is not None:
                                    await sel.select_option(value=val)
                                else:
                                    await sel.select_option(label=txt)
                                await page.wait_for_timeout(900)
                                await capture(f"date:{txt}")
                            except Exception:
                                continue
                except Exception:
                    continue

            # 4) Algunos controles de fecha son botones/chips, no <select>.
            date_candidates = page.locator("button, [role=button], .date, [class*=date]")
            seen_labels = set()
            count = min(await date_candidates.count(), 80)
            for i in range(count):
                el = date_candidates.nth(i)
                try:
                    txt = re.sub(r"\s+", " ", (await el.inner_text()).strip())
                    if not txt or txt in seen_labels:
                        continue
                    if not re.search(
                        r"(?:\bMon\b|\bTue\b|\bWed\b|\bThu\b|\bFri\b|\bSat\b|\bSun\b|"
                        r"\d{1,2}[/-]\d{1,2}|\d{1,2}\s+(?:Aug|August))",
                        txt, re.I
                    ):
                        continue
                    seen_labels.add(txt)
                    if await el.is_visible():
                        await el.click(timeout=1800)
                        await page.wait_for_timeout(800)
                        await capture(f"date-button:{txt}")
                except Exception:
                    continue

            await browser.close()
            browser = None

    except Exception as exc:
        print(f"  FIP browser extraction error: {type(exc).__name__}: {exc}")
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        return ""

    # Dedupe snapshots because FIP may expose the same day through two controls.
    unique = []
    seen = set()
    for snap in snapshots:
        key = re.sub(r"\s+", " ", snap)[:3000]
        if key not in seen:
            seen.add(key)
            unique.append(snap)

    print(f"  FIP browser: {len(unique)} snapshots de Results/Female")
    return "\n".join(unique)[:180000]


async def _womens_ranking_validation_sets() -> tuple[set[str], set[str], bool]:
    """Ranking femenino; strict solo si la lista es suficientemente completa."""
    try:
        names = await get_womens_ranking_names(limit=250)
    except Exception:
        names = []

    full = {_norm_person_name(n) for n in names if n}
    surnames = {n.split()[-1] for n in full if n.split()}
    return full, surnames, len(full) >= 40


def _pair_is_womens_ranking_pair(pair: str, ranking_full: set[str], ranking_surnames: set[str]) -> bool:
    """
    Result output uses 'Apellido / Apellido'. Validate both sides against
    the women's ranking so a male result can never leak into En juego.
    """
    pieces = [p.strip() for p in pair.split("/") if p.strip()]
    if len(pieces) != 2:
        return False

    for piece in pieces:
        norm = _norm_person_name(piece)
        if not norm:
            return False

        # Exact/full-name containment.
        if any(norm == full or norm in full or full in norm for full in ranking_full):
            continue

        # Surname fallback for compact display ("Triay / Brea").
        last = norm.split()[-1]
        if last not in ranking_surnames:
            return False

    return True

async def _extract_official_results(event: dict, gender: str = "female") -> list:
    """
    Extrae resultados finalizados SIN Groq.

    Fuente principal: Playwright renderiza FIP Results -> Female -> fechas.
    Parsing: texto/DOM/XHR determinista en Python.
    """
    gender = (gender or "female").strip().lower()
    if gender in {"women", "woman"}:
        gender = "female"
    if gender not in {"female", "male"}:
        gender = "female"

    source = ""
    browser_diag = {}

    if gender == "female":
        try:
            result = await _browser_fip_womens_results_text(event)
            # Compatibilidad con versiones que devuelvan str o (str, diag).
            if isinstance(result, tuple):
                source, browser_diag = result
            else:
                source = result or ""
        except Exception as exc:
            print(f"  live browser results error: {exc}")

    if not source:
        source = await _collect_official_result_text(event, gender)
        browser_diag = {"fallback": "static"}

    if not source:
        _LIVE_DEBUG_STATE.update({
            "browser": browser_diag,
            "parsed_results": 0,
            "valid_results": 0,
            "source_chars": 0,
        })
        return []

    # Normalize source into lines while retaining possible JSON/XHR chunks.
    raw = source
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]

    # If JSON payloads are present, flatten simple scalar strings as extra lines.
    json_strings = []
    for m in re.finditer(r'[\{\[].*?[\}\]]', raw, re.DOTALL):
        chunk = m.group(0)
        if len(chunk) > 60000:
            continue
        try:
            obj = json.loads(chunk)
        except Exception:
            continue

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    json_strings.append(str(k))
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, (str, int, float)):
                json_strings.append(str(x))
        walk(obj)

    if json_strings:
        lines.extend(re.sub(r"\s+", " ", x).strip() for x in json_strings if str(x).strip())

    round_patterns = [
        (r"\bfinal\b", "Final"),
        (r"\bsemi[- ]?finals?\b|\bsf\b", "Semifinales"),
        (r"\bquarter[- ]?finals?\b|\bqf\b", "Cuartos de final"),
        (r"\bround of 16\b|\br16\b", "Octavos de final"),
        (r"\b2nd round\b|\bsecond round\b|\br2\b", "Segunda ronda"),
        (r"\b1st round\b|\bfirst round\b|\br1\b", "Primera ronda"),
        (r"\bqual", "Clasificación"),
    ]

    score_re = re.compile(
        r"(?<!\d)(\d{1,2})\s*[-–]\s*(\d{1,2})(?:\s*[,;/ ]+\s*(\d{1,2})\s*[-–]\s*(\d{1,2}))?(?:\s*[,;/ ]+\s*(\d{1,2})\s*[-–]\s*(\d{1,2}))?"
    )
    date_iso = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
    date_dmy = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b")

    current_round = "Partidos"
    current_date = ""
    parsed = []

    # First pass: detect dense lines containing pair-vs-pair + score.
    pair_score_patterns = [
        re.compile(
            r"(?P<winner>[^|\n]{2,80}?(?:/|&)[^|\n]{2,80}?)\s+(?:def\.?|beat|beats|d\.?)\s+(?P<loser>[^|\n]{2,80}?(?:/|&)[^|\n]{2,80}?)\s+(?P<score>\d{1,2}\s*[-–]\s*\d{1,2}.*)$",
            re.I,
        ),
        re.compile(
            r"(?P<p1>[^|\n]{2,80}?(?:/|&)[^|\n]{2,80}?)\s+(?:vs\.?|v\.?)\s+(?P<p2>[^|\n]{2,80}?(?:/|&)[^|\n]{2,80}?).*?(?P<score>\d{1,2}\s*[-–]\s*\d{1,2}.*)$",
            re.I,
        ),
    ]

    for i, ln in enumerate(lines):
        low = ln.lower()

        for pat, label in round_patterns:
            if re.search(pat, low):
                current_round = label
                break

        m = date_iso.search(ln)
        if m:
            current_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        else:
            m = date_dmy.search(ln)
            if m:
                current_date = f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

        matched = False
        for pat in pair_score_patterns:
            pm = pat.search(ln)
            if not pm:
                continue

            gd = pm.groupdict()
            score_txt = gd.get("score", "")
            sm = score_re.search(score_txt)
            if not sm:
                continue

            sets = []
            vals = sm.groups()
            for a, b in zip(vals[0::2], vals[1::2]):
                if a is not None and b is not None:
                    sets.append((int(a), int(b)))
            if len(sets) < 2:
                continue

            if "winner" in gd and gd.get("winner"):
                winner = gd["winner"]
                loser = gd["loser"]
            else:
                p1, p2 = gd["p1"], gd["p2"]
                p1_sets = sum(1 for a,b in sets if a > b)
                p2_sets = sum(1 for a,b in sets if b > a)
                if p1_sets == p2_sets:
                    continue
                winner, loser = (p1, p2) if p1_sets > p2_sets else (p2, p1)
                if p2_sets > p1_sets:
                    sets = [(b,a) for a,b in sets]

            parsed.append({
                "round": current_round,
                "winner": re.sub(r"\s*&\s*", " / ", winner).strip(" -|"),
                "loser": re.sub(r"\s*&\s*", " / ", loser).strip(" -|"),
                "score": "  ".join(f"{a}-{b}" for a,b in sets),
                "date": current_date,
            })
            matched = True
            break

        if matched:
            continue

        # Second pass fallback: score line with nearby player lines.
        sm = score_re.fullmatch(ln)
        if sm and i >= 4:
            vals = sm.groups()
            sets = []
            for a, b in zip(vals[0::2], vals[1::2]):
                if a is not None and b is not None:
                    sets.append((int(a), int(b)))
            if len(sets) < 2:
                continue

            prev = [x for x in lines[max(0, i-8):i] if x]
            # Exclude obvious headers.
            players = []
            for x in reversed(prev):
                if re.search(r"\b(women|female|men|male|result|court|round|final|semi|quarter|qual|score)\b", x, re.I):
                    continue
                if re.search(r"\d{1,2}:\d{2}", x):
                    continue
                if score_re.search(x):
                    continue
                if len(x) < 2 or len(x) > 70:
                    continue
                if re.search(r"[A-Za-zÀ-ÿ]", x):
                    players.append(x)
                if len(players) >= 4:
                    break

            if len(players) >= 4:
                players = list(reversed(players[:4]))
                p1 = f"{players[0]} / {players[1]}"
                p2 = f"{players[2]} / {players[3]}"
                p1_sets = sum(1 for a,b in sets if a>b)
                p2_sets = sum(1 for a,b in sets if b>a)
                if p1_sets != p2_sets:
                    if p1_sets > p2_sets:
                        winner, loser = p1, p2
                    else:
                        winner, loser = p2, p1
                        sets = [(b,a) for a,b in sets]
                    parsed.append({
                        "round": current_round,
                        "winner": winner,
                        "loser": loser,
                        "score": "  ".join(f"{a}-{b}" for a,b in sets),
                        "date": current_date,
                    })

    # Dedupe + sanity.
    valid = []
    seen = set()
    for item in parsed:
        winner = re.sub(r"\s+", " ", item["winner"]).strip()
        loser = re.sub(r"\s+", " ", item["loser"]).strip()
        score = item["score"].strip()

        if not winner or not loser or winner == loser:
            continue
        if "/" not in winner or "/" not in loser:
            continue
        if not re.search(r"\d+-\d+", score):
            continue

        key = (item["round"], winner.lower(), loser.lower(), score, item["date"])
        if key in seen:
            continue
        seen.add(key)

        valid.append({
            "round": item["round"],
            "winner": winner,
            "loser": loser,
            "score": score,
            "date": item["date"],
        })

    valid.sort(
        key=lambda x: (ROUND_ORDER.get(x["round"], 0), x.get("date", "")),
        reverse=True,
    )

    _LIVE_DEBUG_STATE.update({
        "browser": browser_diag,
        "parser": "python-deterministic-v20",
        "parsed_results": len(parsed),
        "valid_results": len(valid),
        "source_chars": len(source),
        "groq_used": False,
    })

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
        "_debug": dict(_LIVE_DEBUG_STATE),
    }
