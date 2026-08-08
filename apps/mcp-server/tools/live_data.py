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

            if len(ranking) >= 20:
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


def _calendar_end_date(t: dict) -> date | None:
    MONTHS = {
        "Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
        "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12,
    }
    try:
        month = MONTHS.get(str(t.get("month",""))[:3].title())
        if not month:
            return None
        nums = [int(x) for x in re.findall(r"\d+", str(t.get("day","")))]
        if not nums:
            return None
        end_day = nums[-1]
        return date(date.today().year, month, end_day)
    except Exception:
        return None


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

    # Never show tournaments that already finished.
    today = date.today()
    filtered = []
    for t in torneos:
        end_dt = _calendar_end_date(t)
        if end_dt is None:
            # Keep only if we cannot determine the date and it is explicitly live.
            if t.get("live"):
                filtered.append(t)
            continue
        if end_dt >= today:
            filtered.append(t)

    # If the scraped source is unavailable/outdated, use a current official
    # Premier Padel 2026 fallback from August onward.
    if not filtered:
        filtered = [
            {"day":"3-9","month":"Aug","name":"London P1","place":"London 🇬🇧","badge":"p1","badgeText":"P1","tv":"Red Bull TV · Movistar+","live":_is_live("3-9","Aug")},
            {"day":"31-6","month":"Aug","name":"Madrid P1","place":"Madrid 🇪🇸","badge":"p1","badgeText":"P1","tv":"Red Bull TV · Movistar+","live":False},
            {"day":"7-13","month":"Sep","name":"Paris Major","place":"Paris 🇫🇷","badge":"major","badgeText":"MAJOR","tv":"Red Bull TV · Movistar+","live":False},
            {"day":"28-4","month":"Sep","name":"Rotterdam P2","place":"Rotterdam 🇳🇱","badge":"p2","badgeText":"P2","tv":"Movistar+ · YouTube","live":False},
            {"day":"5-11","month":"Oct","name":"Germany P2","place":"Germany 🇩🇪","badge":"p2","badgeText":"P2","tv":"Movistar+ · YouTube","live":False},
            {"day":"12-18","month":"Oct","name":"Milano P1","place":"Milano 🇮🇹","badge":"p1","badgeText":"P1","tv":"Red Bull TV · Movistar+","live":False},
            {"day":"26-31","month":"Oct","name":"Kuwait Major","place":"Kuwait 🇰🇼","badge":"major","badgeText":"MAJOR","tv":"Red Bull TV · Movistar+","live":False},
            {"day":"8-15","month":"Nov","name":"Dubai P1","place":"Dubai 🇦🇪","badge":"p1","badgeText":"P1","tv":"Red Bull TV · Movistar+","live":False},
            {"day":"23-29","month":"Nov","name":"Mexico Major","place":"Acapulco 🇲🇽","badge":"major","badgeText":"MAJOR","tv":"Red Bull TV · Movistar+","live":False},
            {"day":"7-13","month":"Dec","name":"Premier Padel Finals","place":"Barcelona 🇪🇸","badge":"major","badgeText":"FINALS","tv":"Red Bull TV · Movistar+","live":False},
        ]
        filtered = [t for t in filtered if (_calendar_end_date(t) or today) >= today]

    # Current tournament first, then future tournaments chronologically.
    def sort_key(t):
        end_dt = _calendar_end_date(t) or date.max
        # approximate start date using first number in day range
        try:
            month_map={"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
            month=month_map.get(str(t.get("month",""))[:3].title(),12)
            start_day=int(re.findall(r"\d+",str(t.get("day","")))[0])
            start_dt=date(today.year,month,start_day)
        except Exception:
            start_dt=end_dt
        return (0 if t.get("live") else 1, start_dt)

    filtered.sort(key=sort_key)
    return filtered[:10]


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


async def _browser_fip_womens_results_text(event: dict) -> tuple[str, dict]:
    """
    EN JUEGO ONLY — v39

    Objetivo: recuperar TODOS los partidos finalizados del torneo hasta hoy.

    Estrategia:
      1. Abrir Results.
      2. Leer el estado por defecto.
      3. Activar Female con asociación label->input cuando exista.
      4. Leer el cuadro femenino completo.
      5. Recorrer todos los días disponibles.
      6. Detectar contenedores mínimos que incluyan 4 jugadoras del ranking
         y celdas numéricas de marcador.
      7. El backend decide ganador/perdedor y deduplica.

    Groq no interviene.
    """
    diag = {
        "playwright": False,
        "female_control_found": False,
        "female_control_activated": False,
        "states_scanned": 0,
        "candidate_blocks": 0,
        "female_blocks": 0,
        "source_mode": "full-draw-proximity-v39",
    }

    if async_playwright is None:
        return "", diag

    url = event.get("url", "")
    if not url:
        return "", diag

    try:
        ranking_names = await get_womens_ranking_names(limit=250)
    except Exception:
        ranking_names = []

    ranking_norm = [_norm_person_name(x) for x in ranking_names if x]
    if not ranking_norm:
        return "", diag

    browser = None
    collected = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            diag["playwright"] = True

            page = await browser.new_page(
                viewport={"width": 1600, "height": 2200},
                locale="en-US",
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1600)

            # Results tab.
            for label in ("Results", "Resultados"):
                try:
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count():
                        await loc.click(force=True, timeout=2500)
                        await page.wait_for_timeout(900)
                        break
                except Exception:
                    pass

            async def collect_state(tag: str):
                diag["states_scanned"] += 1
                try:
                    state = await page.evaluate(
                        r"""
                        ({ranking, tag}) => {
                          const norm = s => (s || '')
                            .normalize('NFD')
                            .replace(/[\u0300-\u036f]/g,'')
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g,' ')
                            .replace(/\s+/g,' ')
                            .trim();

                          const clean = s => (s || '').replace(/\s+/g,' ').trim();
                          const ranked = ranking.filter(Boolean);
                          const all = Array.from(document.querySelectorAll('body *'));

                          const leafNodes = el => Array.from(el.querySelectorAll('*'))
                            .filter(x => x.children.length === 0);

                          const scoreValues = el => leafNodes(el)
                            .map(x => clean(x.textContent))
                            .filter(x => /^\d{1,2}$/.test(x))
                            .map(Number)
                            .filter(n => n >= 0 && n <= 20);

                          const rankedPlayers = el => {
                            const text = ' ' + norm(el.innerText || el.textContent || '') + ' ';
                            const hits = [];
                            for (const player of ranked) {
                              if (player && text.includes(' ' + player + ' ')) hits.push(player);
                            }
                            return [...new Set(hits)];
                          };

                          const candidates = [];

                          for (const el of all) {
                            const text = clean(el.innerText);
                            if (!text || text.length < 25 || text.length > 2200) continue;

                            const players = rankedPlayers(el);
                            if (players.length !== 4) continue;

                            const scores = scoreValues(el);
                            if (scores.length < 4 || scores.length > 12) continue;

                            candidates.push({
                              el,
                              tag,
                              text,
                              players,
                              scores
                            });
                          }

                          // El bloque más pequeño suele corresponder a un partido.
                          const minimal = candidates.filter(c =>
                            !candidates.some(o => o !== c && c.el.contains(o.el))
                          );

                          const out = [];
                          const seen = new Set();

                          for (const c of minimal) {
                            // Orden de jugadoras por aparición real en el DOM.
                            const leaves = leafNodes(c.el);
                            const ordered = [];
                            for (const leaf of leaves) {
                              const lt = norm(leaf.textContent || '');
                              for (const player of ranked) {
                                if (!player) continue;
                                if (lt === player || lt.includes(player) || player.includes(lt)) {
                                  if (!ordered.includes(player)) ordered.push(player);
                                  break;
                                }
                              }
                            }

                            // Si los leafs no permiten orden, usar orden por posición en texto.
                            if (ordered.length !== 4) {
                              const full = ' ' + norm(c.text) + ' ';
                              const positioned = c.players
                                .map(p => ({p, i: full.indexOf(' ' + p + ' ')}))
                                .filter(x => x.i >= 0)
                                .sort((a,b) => a.i-b.i)
                                .map(x => x.p);
                              ordered.splice(0, ordered.length, ...positioned);
                            }

                            if (ordered.length !== 4) continue;

                            const key = ordered.join('|') + '::' + c.scores.join(',');
                            if (seen.has(key)) continue;
                            seen.add(key);

                            out.push({
                              tag: c.tag,
                              text: c.text.slice(0,1600),
                              players: ordered,
                              scores: c.scores
                            });
                          }

                          return {
                            candidateCount: candidates.length,
                            blocks: out
                          };
                        }
                        """,
                        {"ranking": ranking_norm, "tag": tag},
                    )
                    diag["candidate_blocks"] += int(state.get("candidateCount", 0))
                    collected.extend(state.get("blocks", []) or [])
                except Exception as exc:
                    print(f"  v39 collect error {tag}: {type(exc).__name__}: {exc}")

            async def collect_dates(prefix: str):
                # Selects that actually look like date selectors.
                try:
                    selects = page.locator("select")
                    for si in range(await selects.count()):
                        sel = selects.nth(si)
                        opts = sel.locator("option")
                        count = await opts.count()
                        if not 2 <= count <= 16:
                            continue

                        labels = []
                        for oi in range(count):
                            try:
                                labels.append(re.sub(r"\s+", " ", (await opts.nth(oi).inner_text()).strip()))
                            except Exception:
                                labels.append("")

                        looks_date = any(re.search(
                            r"(?:\bMon\b|\bTue\b|\bWed\b|\bThu\b|\bFri\b|\bSat\b|\bSun\b|"
                            r"\bMonday\b|\bTuesday\b|\bWednesday\b|\bThursday\b|\bFriday\b|\bSaturday\b|\bSunday\b|"
                            r"\d{1,2}[/-]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9})",
                            lab, re.I
                        ) for lab in labels)
                        if not looks_date:
                            continue

                        for oi, label in enumerate(labels):
                            if not label:
                                continue
                            try:
                                value = await opts.nth(oi).get_attribute("value")
                                if value is not None:
                                    await sel.select_option(value=value)
                                else:
                                    await sel.select_option(label=label)
                                await page.wait_for_timeout(700)
                                await collect_state(f"{prefix}|date:{label}")
                            except Exception:
                                pass
                except Exception:
                    pass

                # Date buttons/chips.
                try:
                    buttons = page.locator("button, [role=button], [role=tab], [class*=date], [class*=day]")
                    seen = set()
                    for bi in range(min(await buttons.count(), 180)):
                        el = buttons.nth(bi)
                        try:
                            label = re.sub(r"\s+", " ", (await el.inner_text()).strip())
                            if not label or label in seen:
                                continue
                            if not re.search(
                                r"(?:\bMon\b|\bTue\b|\bWed\b|\bThu\b|\bFri\b|\bSat\b|\bSun\b|"
                                r"\bMonday\b|\bTuesday\b|\bWednesday\b|\bThursday\b|\bFriday\b|\bSaturday\b|\bSunday\b|"
                                r"\d{1,2}[/-]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9})",
                                label, re.I
                            ):
                                continue
                            if not await el.is_visible():
                                continue
                            seen.add(label)
                            await el.click(force=True, timeout=1800)
                            await page.wait_for_timeout(700)
                            await collect_state(f"{prefix}|date:{label}")
                        except Exception:
                            pass
                except Exception:
                    pass

            # Estado inicial (por si FIP precarga datos).
            await collect_state("default")
            await collect_dates("default")

            # ----------------------------------------------------------
            # Activación precisa de Female.
            # ----------------------------------------------------------
            activated = False

            # A) label exacto + atributo for.
            try:
                labels = page.locator("label")
                for li in range(await labels.count()):
                    lab = labels.nth(li)
                    text = re.sub(r"\s+", " ", (await lab.inner_text()).strip()).lower()
                    if text not in {"female", "women", "femenino", "femenina"}:
                        continue

                    diag["female_control_found"] = True
                    target_id = await lab.get_attribute("for")
                    if target_id:
                        inp = page.locator(f"#{target_id}")
                        if await inp.count():
                            try:
                                await inp.check(force=True, timeout=2500)
                            except Exception:
                                await inp.click(force=True, timeout=2500)
                            activated = True
                    if not activated:
                        await lab.click(force=True, timeout=2500)
                        activated = True
                    if activated:
                        break
            except Exception:
                pass

            # B) texto Female -> ancestro con input.
            if not activated:
                try:
                    result = await page.evaluate(
                        r"""
                        () => {
                          const clean = s => (s || '').replace(/\s+/g,' ').trim().toLowerCase();
                          const all = Array.from(document.querySelectorAll('body *'));
                          const node = all.find(el =>
                            ['female','women','femenino','femenina'].includes(clean(el.textContent))
                          );
                          if (!node) return false;

                          let cur = node;
                          for (let up=0; up<6 && cur; up++, cur=cur.parentElement) {
                            const input = cur.matches('input') ? cur : cur.querySelector('input');
                            if (input) {
                              try { input.click(); } catch(e) {}
                              try { input.checked = true; } catch(e) {}
                              input.dispatchEvent(new Event('input',{bubbles:true}));
                              input.dispatchEvent(new Event('change',{bubbles:true}));
                              return true;
                            }
                          }

                          try {
                            node.click();
                            node.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
                            return true;
                          } catch(e) {}
                          return false;
                        }
                        """
                    )
                    activated = bool(result)
                    diag["female_control_found"] = diag["female_control_found"] or activated
                except Exception:
                    pass

            if activated:
                diag["female_control_activated"] = True
                await page.wait_for_timeout(1400)
                await collect_state("female")
                await collect_dates("female")

            await browser.close()
            browser = None

    except Exception as exc:
        print(f"  FIP v39 capture error: {type(exc).__name__}: {exc}")
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        return "", diag

    # Dedupe after scanning all states/dates.
    unique = []
    seen = set()
    for block in collected:
        key = (
            tuple(block.get("players", [])),
            tuple(block.get("scores", [])),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(block)

    diag["female_blocks"] = len(unique)

    print(
        "  FIP v39:",
        f"female_control={diag['female_control_found']}",
        f"activated={diag['female_control_activated']}",
        f"states={diag['states_scanned']}",
        f"candidates={diag['candidate_blocks']}",
        f"blocks={diag['female_blocks']}",
    )

    return json.dumps({"blocks": unique, "diag": diag}, ensure_ascii=False), diag


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

def _round_from_snapshot(snapshot: str, event: dict) -> str:
    """Infer round using date shown in snapshot + tournament structure."""
    round_aliases = [
        ("semi-final", "Semifinales"),
        ("semifinal", "Semifinales"),
        ("quarter-final", "Cuartos de final"),
        ("quarter final", "Cuartos de final"),
        ("round of 16", "Octavos de final"),
        ("2nd round", "Segunda ronda"),
        ("second round", "Segunda ronda"),
        ("1st round", "Primera ronda"),
        ("first round", "Primera ronda"),
        ("final", "Final"),
        ("qual", "Clasificación"),
    ]

    # If snapshot itself names the round, easiest path.
    low = snapshot.lower()
    for needle, label in round_aliases:
        if needle in low:
            return label

    # Extract day/month from snapshot.
    dm = re.search(
        r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?[,]?\s*(\d{1,2})\s+"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b",
        snapshot,
        re.I,
    )
    if not dm:
        return "Partidos"

    day = str(int(dm.group(1)))
    month = dm.group(2).lower()

    event_text = _clean_text(BeautifulSoup(event.get("html", ""), "html.parser"))
    event_low = event_text.lower()

    # Find the occurrence of the date in tournament structure and inspect context before it.
    date_pat = re.compile(rf"\b{re.escape(day)}\s+{re.escape(month)}[a-z]*\b", re.I)
    for m in date_pat.finditer(event_low):
        ctx = event_low[max(0, m.start() - 180):m.start() + 40]
        for needle, label in round_aliases:
            if needle in ctx:
                return label

    return "Partidos"


async def _extract_official_results(event: dict, gender: str = "female") -> list:
    """
    EN JUEGO ONLY — v39

    Recibe bloques con 4 jugadoras + scores de todos los estados/días.
    Reconstruye pareja ganadora, perdedora, marcador, fecha y ronda.
    """
    try:
        source, capture_diag = await _browser_fip_womens_results_text(event)
    except Exception as exc:
        print(f"  v39 live-source error: {type(exc).__name__}: {exc}")
        source, capture_diag = "", {}

    try:
        blocks = json.loads(source).get("blocks", []) if source else []
    except Exception:
        blocks = []

    month_map = {
        "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
        "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
        "ene":1,"abr":4,"ago":8,"dic":12,
    }

    def date_from_tag(tag: str) -> str:
        raw = str(tag or "")
        year = date.today().year

        m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b", raw)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3) or year)
            try:
                return date(y, mo, d).isoformat()
            except Exception:
                pass

        m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,12})\b", raw)
        if m:
            d = int(m.group(1))
            mo = month_map.get(m.group(2).lower()[:3])
            if mo:
                try:
                    return date(year, mo, d).isoformat()
                except Exception:
                    pass
        return ""

    def infer_round(context: str, match_date: str) -> str:
        low = context.lower()
        if re.search(r"semi[- ]?final", low): return "Semifinales"
        if re.search(r"quarter[- ]?final", low): return "Cuartos de final"
        if re.search(r"round of 16|octav", low): return "Octavos de final"
        if re.search(r"\bfinal\b", low): return "Final"
        if re.search(r"\bqual|clasif", low): return "Clasificación"
        if re.search(r"2nd round|second round|segunda", low): return "Segunda ronda"
        if re.search(r"1st round|first round|primera", low): return "Primera ronda"

        # London P1 / standard Premier Padel end-of-week cadence.
        raw_dates = str(event.get("dates", "") or "")
        m = re.search(
            r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3,12})(?:\s+(20\d{2}))?",
            raw_dates, re.I
        )
        if m and match_date:
            end_day = int(m.group(2))
            mo = month_map.get(m.group(3).lower()[:3])
            y = int(m.group(4) or date.today().year)
            if mo:
                try:
                    end_dt = date(y, mo, end_day)
                    md = date.fromisoformat(match_date)
                    delta = (end_dt - md).days
                    if delta == 0: return "Final"
                    if delta == 1: return "Semifinales"
                    if delta == 2: return "Cuartos de final"
                    if delta == 3: return "Octavos de final"
                    if delta in {4,5}: return "Primera ronda"
                    if delta >= 6: return "Clasificación"
                except Exception:
                    pass

        return "Partidos"

    parsed = []

    for block in blocks:
        players = list(block.get("players", []))
        scores = [int(x) for x in block.get("scores", []) if str(x).isdigit()]

        if len(players) != 4 or len(scores) < 4:
            continue

        # FIP puede ordenar score por pareja o por set.
        layouts = []
        for n in (6,4):
            if len(scores) >= n:
                chunk = scores[:n]
                half = n // 2
                layouts.extend([
                    (chunk[:half], chunk[half:]),
                    (chunk[0::2], chunk[1::2]),
                ])

        chosen = None
        for a_sc, b_sc in layouts:
            if len(a_sc) != len(b_sc) or len(a_sc) < 2:
                continue

            a_sets = b_sets = 0
            plausible = True

            for a,b in zip(a_sc,b_sc):
                hi, lo = max(a,b), min(a,b)

                # Normal set / tie-break set.
                if not ((hi == 6 and 0 <= lo <= 4) or (hi == 7 and lo in {5,6})):
                    plausible = False
                    break

                if a>b: a_sets += 1
                elif b>a: b_sets += 1
                else:
                    plausible = False
                    break

            if plausible and a_sets != b_sets and max(a_sets,b_sets) >= 2:
                chosen = (a_sc,b_sc,a_sets,b_sets)
                break

        if not chosen:
            continue

        a_sc,b_sc,a_sets,b_sets = chosen
        p1,p2,p3,p4 = [str(x).title() for x in players]
        pair_a = f"{p1} / {p2}"
        pair_b = f"{p3} / {p4}"

        if a_sets > b_sets:
            winner, loser = pair_a, pair_b
            display = list(zip(a_sc,b_sc))
        else:
            winner, loser = pair_b, pair_a
            display = list(zip(b_sc,a_sc))

        match_date = date_from_tag(block.get("tag",""))
        if match_date:
            try:
                if date.fromisoformat(match_date) > date.today():
                    continue
            except Exception:
                pass

        context = str(block.get("text","")) + " " + str(block.get("tag",""))
        rnd = infer_round(context, match_date)

        parsed.append({
            "round": rnd,
            "winner": winner,
            "loser": loser,
            "score": "  ".join(f"{a}-{b}" for a,b in display),
            "date": match_date,
        })

    # Dedupe all tournament results.
    valid = []
    seen = set()
    for item in parsed:
        key = (
            item["winner"].casefold(),
            item["loser"].casefold(),
            item["score"],
        )
        if key in seen:
            continue
        seen.add(key)
        valid.append(item)

    valid.sort(
        key=lambda x: (
            x.get("date",""),
            ROUND_ORDER.get(x.get("round","Partidos"),0),
        ),
        reverse=True,
    )

    dates = sorted({x.get("date","") for x in valid if x.get("date")}, reverse=True)

    _LIVE_DEBUG_STATE.update({
        "browser": capture_diag,
        "parser": "full-draw-proximity-v39",
        "raw_blocks": len(blocks),
        "parsed_results": len(parsed),
        "valid_results": len(valid),
        "result_dates": dates,
        "groq_used": False,
    })

    print(
        "  live v39:",
        f"blocks={len(blocks)}",
        f"parsed={len(parsed)}",
        f"valid={len(valid)}",
        f"dates={len(dates)}",
    )

    return valid


def _normalize_scoreboard_result(item: dict) -> dict | None:
    """Normaliza un partido finalizado para el cuadro visual de En juego."""
    if not isinstance(item, dict):
        return None

    winner = re.sub(r"\s+", " ", str(item.get("winner", ""))).strip()
    loser = re.sub(r"\s+", " ", str(item.get("loser", ""))).strip()
    rnd = re.sub(r"\s+", " ", str(item.get("round", "Partidos"))).strip() or "Partidos"
    raw_score = str(item.get("score", "")).strip()

    if not winner or not loser or not raw_score:
        return None

    sets = re.findall(r"(\d{1,2})\s*[-–]\s*(\d{1,2})", raw_score)
    if len(sets) < 2:
        return None

    return {
        "round": rnd,
        "winner": winner,
        "loser": loser,
        "score": "  ".join(f"{a}–{b}" for a, b in sets[:3]),
        "date": str(item.get("date", "") or ""),
        "status": "finalizado",
    }


def _normalize_next_match(item: dict | None) -> dict | None:
    """Normaliza el próximo partido para el cuadro visual de En juego."""
    if not isinstance(item, dict):
        return None

    pair1 = re.sub(r"\s+", " ", str(item.get("pair1", ""))).strip()
    pair2 = re.sub(r"\s+", " ", str(item.get("pair2", ""))).strip()

    if not pair1 or not pair2:
        return None

    return {
        "round": re.sub(r"\s+", " ", str(item.get("round", "Próximo partido"))).strip() or "Próximo partido",
        "pair1": pair1,
        "pair2": pair2,
        "when": str(item.get("when", "") or "Horario por confirmar"),
        "iso_madrid": str(item.get("iso_madrid", "") or ""),
        "status": "próximo",
    }


def _event_end_date_from_dates(event: dict) -> date | None:
    """Parsea la fecha final del torneo sin tocar la extracción FIP."""
    raw = str(event.get("dates", "") or "")
    month_map = {
        "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
        "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
        "ene":1,"abr":4,"ago":8,"dic":12,
    }

    # 02/08/2026 - 09/08/2026
    nums = re.findall(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", raw)
    if nums:
        d, mo, y = nums[-1]
        try:
            return date(int(y), int(mo), int(d))
        except Exception:
            pass

    # 2-9 August 2026 / 2–9 Aug 2026
    m = re.search(
        r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-zÁÉÍÓÚáéíóú]{3,14})(?:\s+(20\d{2}))?",
        raw, re.I
    )
    if m:
        end_day = int(m.group(2))
        mon = _norm_person_name(m.group(3)).split()[0][:3]
        mo = month_map.get(mon)
        y = int(m.group(4) or date.today().year)
        if mo:
            try:
                return date(y, mo, end_day)
            except Exception:
                pass

    return None


def _stage_from_event(event: dict) -> str:
    """
    Fase del torneo según fecha.
    Premier Padel termina normalmente en domingo:
      domingo Final
      sábado Semifinales
      viernes Cuartos
      jueves Octavos
    """
    end_dt = _event_end_date_from_dates(event)
    if not end_dt:
        return "Partidos"

    delta = (end_dt - date.today()).days
    if delta <= 0:
        return "Final"
    if delta == 1:
        return "Semifinales"
    if delta == 2:
        return "Cuartos de final"
    if delta == 3:
        return "Octavos de final"
    return "Primera ronda"


def _round_capacity(round_name: str) -> int:
    return {
        "Final": 1,
        "Semifinales": 2,
        "Cuartos de final": 4,
        "Octavos de final": 8,
    }.get(round_name, 0)


def _annotate_result_rounds(results: list, next_match: dict | None, event: dict) -> list:
    """
    NO toca la captura. Solo etiqueta los resultados ya extraídos.

    Regla:
    - si FIP ya dio ronda, se respeta;
    - los que llegan como "Partidos" se reparten desde el final del cuadro;
    - si el próximo partido es de la ronda actual, asumimos que aún queda al menos
      un partido de esa ronda y no marcamos todos como completados.
    """
    if not results:
        return results

    unresolved = [r for r in results if (r.get("round") or "Partidos") == "Partidos"]
    if not unresolved:
        return results

    stage = _stage_from_event(event)
    next_round = (next_match or {}).get("round") or ""

    # Complete previous rounds in every case.
    round_chain = ["Octavos de final", "Cuartos de final", "Semifinales", "Final"]
    stage_idx = round_chain.index(stage) if stage in round_chain else -1

    completed_before = []
    if stage_idx >= 0:
        completed_before = round_chain[:stage_idx]

    # Current-round completed matches:
    # - if next_match is still same round, at most capacity-1 are complete;
    # - if next_match already points to next round, current round is complete;
    # - otherwise infer conservatively from available tail.
    current_capacity = _round_capacity(stage)
    if current_capacity:
        if next_round == stage:
            current_done_max = max(0, current_capacity - 1)
        elif next_round and next_round != stage:
            current_done_max = current_capacity
        else:
            # No reliable OOP: do not force a full current round.
            current_done_max = max(0, current_capacity - 1)
    else:
        current_done_max = 0

    # Number of slots occupied by fully-completed later rounds before current stage.
    previous_sizes = sum(_round_capacity(r) for r in completed_before)

    n = len(unresolved)

    # The earliest leftover block is Primera ronda.
    # Allocate from the END backwards, because v39 returns cumulative draw order.
    allocations = []
    if current_capacity:
        # At least zero, at most current_done_max.
        current_done = min(current_done_max, max(0, n - previous_sizes))
    else:
        current_done = 0

    # Reserve full completed rounds immediately before current stage.
    tail_needed = previous_sizes + current_done
    first_count = max(0, n - tail_needed)

    pos = 0
    if first_count:
        allocations.append(("Primera ronda", first_count))
        pos += first_count

    for rnd in completed_before:
        size = _round_capacity(rnd)
        if size:
            allocations.append((rnd, size))
            pos += size

    if current_done:
        allocations.append((stage, current_done))
        pos += current_done

    # Anything remaining belongs to the current stage, but only if we have evidence
    # that the current round is already complete / next round has started.
    if pos < n and next_round and next_round != stage:
        allocations.append((stage, n - pos))
        pos = n

    # Apply in source order.
    cursor = 0
    for rnd, count in allocations:
        end = min(n, cursor + count)
        for item in unresolved[cursor:end]:
            item["round"] = rnd
        cursor = end

    # If some remain unresolved, label them with the current stage instead of the
    # meaningless "Partidos" ONLY when today's stage is known.
    if stage != "Partidos":
        for item in unresolved[cursor:]:
            item["round"] = stage

    _LIVE_DEBUG_STATE["round_annotation"] = {
        "stage_today": stage,
        "next_round": next_round,
        "unresolved_before": n,
        "allocations": allocations,
    }

    return results


def _fallback_next_match(results: list, next_match: dict | None, event: dict) -> dict | None:
    """
    Si Order of Play no devuelve nada, la web sigue diciendo al menos:
    - qué ronda toca;
    - si es hoy/mañana;
    - que el horario exacto está por confirmar.

    No inventa parejas.
    """
    if next_match:
        return next_match

    stage = _stage_from_event(event)
    if stage == "Partidos":
        return None

    end_dt = _event_end_date_from_dates(event)
    today = date.today()

    if end_dt:
        delta = (end_dt - today).days
        if stage == "Final":
            day_label = "Hoy" if delta == 0 else "Próximamente"
        else:
            day_label = "Hoy"
    else:
        day_label = "Hoy"

    return {
        "round": stage,
        "pair1": "Pareja por confirmar",
        "pair2": "Pareja por confirmar",
        "date": today.isoformat(),
        "time": "",
        "time_type": "unknown",
        "when": f"{day_label} · horario por confirmar (hora de España)",
        "iso_madrid": "",
        "source": "fase del torneo",
    }


async def _extract_current_womens_match(event: dict) -> dict | None:
    """
    Consulta exclusivamente la vista Live Score de FIP.

    NO modifica ni reutiliza el extractor de resultados históricos.
    Devuelve partido femenino actual solo si FIP muestra 4 jugadoras
    del ranking femenino en el mismo bloque de live score.
    """
    if async_playwright is None:
        return None

    url = event.get("url", "")
    if not url:
        return None

    try:
        ranking_names = await get_womens_ranking_names(limit=250)
    except Exception:
        ranking_names = []

    ranking_norm = [_norm_person_name(x) for x in ranking_names if x]
    if not ranking_norm:
        return None

    browser = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            page = await browser.new_page(
                viewport={"width": 1440, "height": 1600},
                locale="en-US",
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1200)

            # Open Live Score.
            live_opened = False
            for label in ("Live Score", "Live", "Marcador en directo", "Directo"):
                try:
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(force=True, timeout=2500)
                        await page.wait_for_timeout(1000)
                        live_opened = True
                        break
                except Exception:
                    pass

            if not live_opened:
                # Some FIP pages expose Live Score as a tab/anchor with surrounding text.
                try:
                    live_opened = await page.evaluate(
                        r"""
                        () => {
                          const clean=s=>(s||'').replace(/\s+/g,' ').trim().toLowerCase();
                          const nodes=Array.from(document.querySelectorAll('a,button,[role=tab],[role=button]'));
                          const el=nodes.find(x=>['live score','live','directo','marcador en directo'].includes(clean(x.textContent)));
                          if(!el)return false;
                          el.click();
                          el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
                          return true;
                        }
                        """
                    )
                    if live_opened:
                        await page.wait_for_timeout(1000)
                except Exception:
                    pass

            # Activate Female ONLY inside Live Score probe.
            for label in ("Female", "Women", "Femenino", "Femenina"):
                try:
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(force=True, timeout=2200)
                        await page.wait_for_timeout(900)
                        break
                except Exception:
                    pass

            # Find the smallest visible container with exactly four ranked women.
            state = await page.evaluate(
                r"""
                (ranking) => {
                  const norm=s=>(s||'')
                    .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
                    .toLowerCase().replace(/[^a-z0-9]+/g,' ')
                    .replace(/\s+/g,' ').trim();
                  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
                  const all=Array.from(document.querySelectorAll('body *'));
                  const candidates=[];

                  for(const el of all){
                    const style=getComputedStyle(el);
                    if(style.display==='none'||style.visibility==='hidden')continue;
                    const text=clean(el.innerText);
                    if(!text||text.length<25||text.length>1800)continue;
                    const nt=' '+norm(text)+' ';
                    const players=[...new Set(ranking.filter(p=>p && nt.includes(' '+p+' ')))];
                    if(players.length!==4)continue;

                    const scoreCells=Array.from(el.querySelectorAll('*'))
                      .filter(x=>x.children.length===0)
                      .map(x=>clean(x.textContent))
                      .filter(x=>/^\d{1,2}$/.test(x))
                      .map(Number)
                      .filter(n=>n>=0&&n<=20);

                    candidates.push({el,text,players,scores:scoreCells});
                  }

                  const minimal=candidates.filter(c=>
                    !candidates.some(o=>o!==c && c.el.contains(o.el))
                  );

                  if(!minimal.length)return null;

                  // Prefer blocks that explicitly look live/in progress.
                  minimal.sort((a,b)=>{
                    const score=t=>/\b(live|in progress|playing|set|court|directo|en juego)\b/i.test(t)?1:0;
                    return score(b.text)-score(a.text);
                  });

                  const c=minimal[0];

                  // Player order by occurrence in visible text.
                  const nt=' '+norm(c.text)+' ';
                  const ordered=c.players
                    .map(p=>({p,i:nt.indexOf(' '+p+' ')}))
                    .filter(x=>x.i>=0)
                    .sort((a,b)=>a.i-b.i)
                    .map(x=>x.p);

                  return {
                    text:c.text,
                    players:ordered,
                    scores:c.scores
                  };
                }
                """,
                ranking_norm,
            )

            await browser.close()
            browser = None

            if not state or len(state.get("players", [])) != 4:
                return None

            players = [str(x).title() for x in state["players"]]
            text = str(state.get("text", ""))
            scores = [int(x) for x in state.get("scores", []) if str(x).isdigit()]

            pair1 = f"{players[0]} / {players[1]}"
            pair2 = f"{players[2]} / {players[3]}"

            # Display raw current score conservatively.
            # We do not infer winner because the match is still live.
            score_text = ""
            if len(scores) >= 2:
                if len(scores) % 2 == 0:
                    score_text = "  ".join(
                        f"{scores[i]}-{scores[i+1]}"
                        for i in range(0, min(len(scores), 6), 2)
                    )
                else:
                    score_text = " · ".join(str(x) for x in scores[:6])

            low = text.lower()
            if re.search(r"semi[- ]?final", low):
                rnd = "Semifinales"
            elif re.search(r"quarter[- ]?final", low):
                rnd = "Cuartos de final"
            elif re.search(r"round of 16|octav", low):
                rnd = "Octavos de final"
            elif re.search(r"\bfinal\b", low):
                rnd = "Final"
            else:
                rnd = _stage_from_event(event)

            return {
                "status": "live",
                "status_label": "EN DIRECTO",
                "round": rnd,
                "pair1": pair1,
                "pair2": pair2,
                "score": score_text,
                "when": "Ahora · hora de España",
                "iso_madrid": datetime.now(ZoneInfo("Europe/Madrid")).isoformat(),
                "status_detail": "Cuando termine, el resultado pasará automáticamente al bloque de resultados.",
                "source": "FIP Live Score",
            }

    except Exception as exc:
        print(f"  live-score probe error: {type(exc).__name__}: {exc}")
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    return None


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
        _extract_current_womens_match(event) if gender in {"female", "women", "woman"} else asyncio.sleep(0, result=None),
        return_exceptions=True,
    )

    watch, results, next_match, current_match = parts

    if isinstance(watch, Exception):
        print(f"  live watch error: {watch}")
        watch = []
    if isinstance(results, Exception):
        print(f"  live results error: {results}")
        results = []
    if isinstance(next_match, Exception):
        print(f"  live next-match error: {next_match}")
        next_match = None
    if isinstance(current_match, Exception):
        print(f"  current live-match error: {current_match}")
        current_match = None

    # IMPORTANT: extractor v39 remains untouched.
    # Round labels and next-match fallback are applied only AFTER extraction.
    results = _annotate_result_rounds(results or [], next_match, event)
    next_match = _fallback_next_match(results, next_match, event)

    normalized_results = []
    for result in (results or []):
        normalized = _normalize_scoreboard_result(result)
        if normalized:
            normalized_results.append(normalized)

    normalized_next = _normalize_next_match(next_match)
    normalized_current = current_match if isinstance(current_match, dict) else None

    return {
        "active": True,
        "name": event["name"],
        "place": event["place"],
        "dates": event["dates"],
        "watch": watch or [],
        "results": normalized_results,
        "next_match": normalized_next,
        "current_match": normalized_current,
        "updated": today_str,
        "gender": gender,
        "source": "FIP · Premier Padel",
        "source_url": event["url"],
        "_debug": dict(_LIVE_DEBUG_STATE),
    }
