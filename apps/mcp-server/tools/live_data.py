"""
live_data.py — Ranking FIP y calendario Premier Padel

Ranking: scraping de padelspeak.com (HTML estático, se actualiza tras cada torneo)
Calendario: scraping de padelfip.com + Groq como fallback
"""

import asyncio
import aiohttp
import os
import re
import time
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


_LAST_GOOD_LIVE_RESULTS = {"results": [], "ts": 0.0}


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
            # Keep the complete player name from the ranking source.
            # Truncating to 3 tokens produced broken names such as
            # "Alejandra Alonso De", which also made photo resolution fail.
            name = re.sub(r"\s+", " ", name_text).strip()
            flag = COUNTRY_FLAGS.get(country_text, "🌍")

            # Try to get the player's image from the SAME PadelSpeak ranking row.
            # This avoids matching names against external image sources.
            photo = ""
            try:
                img = row.find("img")
                if img:
                    candidates = [
                        img.get("data-src"),
                        img.get("data-lazy-src"),
                        img.get("data-original"),
                        img.get("src"),
                    ]
                    # srcset often contains the highest quality image at the end.
                    srcset = img.get("srcset") or img.get("data-srcset")
                    if srcset:
                        parts = [x.strip().split(" ")[0] for x in srcset.split(",") if x.strip()]
                        if parts:
                            candidates.insert(0, parts[-1])

                    photo = next(
                        (
                            x for x in candidates
                            if x and not str(x).startswith("data:")
                            and "placeholder" not in str(x).lower()
                            and "logo" not in str(x).lower()
                        ),
                        ""
                    )

                    if photo.startswith("//"):
                        photo = "https:" + photo
                    elif photo.startswith("/"):
                        photo = "https://padelspeak.com" + photo
            except Exception:
                photo = ""

            try:
                pts_num = int(re.sub(r'[^\d]', '', pts_text))
                pts = f"{pts_num:,}".replace(",", ".")
            except Exception:
                pts = pts_text

            ranking.append({
                "pos": pos,
                "name": name,
                "pair": "",
                "flag": flag,
                "pts": pts,
                "photo": photo,
            })

            if len(ranking) >= 20:
                break

        except Exception as e:
            print(f"  ranking row error: {e}")
            continue

    if len(ranking) < 5:
        print(f"  ranking: solo {len(ranking)} filas parseadas, usando fallback")
        return _fallback_ranking()

    _add_pairs(ranking)
    photos = sum(1 for p in ranking if p.get("photo"))
    print(f"  ranking: {len(ranking)} jugadoras obtenidas de padelspeak.com · fotos={photos}")
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
        "source_mode": "full-draw-alias-v77",
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

    ranking_profiles = _player_schedule_profiles(ranking_names)
    if not ranking_profiles:
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
                diag.setdefault("state_tags", []).append(tag)
                try:
                    state = await page.evaluate(
                        r"""
                        ({profiles, tag}) => {
                          const norm = s => (s || '')
                            .normalize('NFD')
                            .replace(/[\u0300-\u036f]/g,'')
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g,' ')
                            .replace(/\s+/g,' ')
                            .trim();

                          const clean = s => (s || '').replace(/\s+/g,' ').trim();
                          const all = Array.from(document.querySelectorAll('body *'));

                          const leafNodes = el => Array.from(el.querySelectorAll('*'))
                            .filter(x => x.children.length === 0);

                          const scoreValues = el => leafNodes(el)
                            .map(x => clean(x.textContent))
                            .filter(x => /^\d{1,2}$/.test(x))
                            .map(Number)
                            .filter(n => n >= 0 && n <= 20);

                          const profileMatchIn = textValue => {
                            const text = ' ' + norm(textValue) + ' ';
                            const hits = [];
                            for (const p of profiles) {
                              let bestIndex = -1;
                              let matchedAlias = '';
                              for (const alias of (p.aliases || [])) {
                                if (!alias || alias.length < 4) continue;
                                const idx = text.indexOf(' ' + alias + ' ');
                                if (idx >= 0 && (bestIndex < 0 || idx < bestIndex)) {
                                  bestIndex = idx;
                                  matchedAlias = alias;
                                }
                              }
                              if (bestIndex >= 0) {
                                hits.push({
                                  canonical: p.canonical,
                                  display: p.display,
                                  index: bestIndex,
                                  alias: matchedAlias
                                });
                              }
                            }
                            return hits;
                          };

                          const roundFromText = value => {
                            const t = clean(value);
                            if (/\bsemi[- ]?finals?\b/i.test(t)) return 'Semifinales';
                            if (/\bquarter[- ]?finals?\b/i.test(t)) return 'Cuartos de final';
                            if (/\bround of 16\b|\boctav/i.test(t)) return 'Octavos de final';
                            if (/\bsecond round\b|\b2nd round\b/i.test(t)) return 'Segunda ronda';
                            if (/\bfirst round\b|\b1st round\b/i.test(t)) return 'Primera ronda';
                            if (/\bqual/i.test(t)) return 'Clasificación';
                            // FINAL must be checked after SEMIFINAL.
                            if (/\bfinal\b/i.test(t)) return 'Final';
                            return '';
                          };

                          const nearestRound = el => {
                            // A) Small parent containers often include WOMEN + SEMIFINALS/FINAL.
                            let p = el;
                            for (let depth=0; depth<5 && p; depth++, p=p.parentElement) {
                              const txt = clean(p.innerText || p.textContent || '');
                              if (txt.length <= 700) {
                                const r = roundFromText(txt);
                                if (r) return r;
                              }
                            }

                            // B) Scan backwards in DOM order for the nearest short round heading.
                            const idx = all.indexOf(el);
                            for (let j=idx-1; j>=0 && j>=idx-90; j--) {
                              const txt = clean(all[j].innerText || all[j].textContent || '');
                              if (!txt || txt.length > 120) continue;
                              const r = roundFromText(txt);
                              if (r) return r;
                            }

                            return '';
                          };

                          const candidates = [];

                          for (const el of all) {
                            const text = clean(el.innerText);
                            if (!text || text.length < 25 || text.length > 2200) continue;

                            const players = profileMatchIn(text);
                            const unique = [];
                            const seenCanon = new Set();
                            for (const p of players.sort((a,b)=>a.index-b.index)) {
                              if (!seenCanon.has(p.canonical)) {
                                seenCanon.add(p.canonical);
                                unique.push(p);
                              }
                            }
                            if (unique.length !== 4) continue;

                            const scores = scoreValues(el);
                            if (scores.length < 4 || scores.length > 12) continue;

                            candidates.push({
                              el,
                              tag,
                              text,
                              players: unique,
                              scores,
                              roundHint: nearestRound(el)
                            });
                          }

                          const minimal = candidates.filter(c =>
                            !candidates.some(o => o !== c && c.el.contains(o.el))
                          );

                          const out = [];
                          const seen = new Set();

                          for (const c of minimal) {
                            // Order by actual appearance of any accepted alias.
                            const ordered = [...c.players]
                              .sort((a,b)=>a.index-b.index)
                              .map(x=>x.display);

                            if (ordered.length !== 4) continue;

                            const key = ordered.join('|') + '::' + c.scores.join(',');
                            if (seen.has(key)) continue;
                            seen.add(key);

                            out.push({
                              tag: c.tag,
                              text: c.text.slice(0,1600),
                              players: ordered,
                              scores: c.scores,
                              aliases: c.players.map(x=>x.alias),
                              roundHint: c.roundHint || ''
                            });
                          }

                          return {
                            candidateCount: candidates.length,
                            blocks: out
                          };
                        }
                        """,
                        {"profiles": ranking_profiles, "tag": tag},
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
                            r"\d{1,2}[/-]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}|[A-Za-z]{3,9}\s+\d{1,2})",
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
                                r"\d{1,2}[/-]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}|[A-Za-z]{3,9}\s+\d{1,2})",
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
        f"tags={diag.get('state_tags', [])[:20]}",
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


async def _browser_fip_flat_results_fallback(event: dict) -> list:
    """
    Fallback SOLO si el extractor v39 devuelve cero bloques.

    Lee los nodos visibles en orden DOM después de abrir Results + Female.
    En vez de exigir un contenedor perfecto con 4 jugadoras, usa el ✓ como ancla
    y busca las cuatro jugadoras del ranking más cercanas y los números de score.
    """
    if async_playwright is None:
        return []

    url = event.get("url", "")
    if not url:
        return []

    try:
        ranking_names = await get_womens_ranking_names(limit=250)
    except Exception:
        ranking_names = []

    ranking_profiles = _player_schedule_profiles(ranking_names)
    if not ranking_profiles:
        return []

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(
                viewport={"width": 1600, "height": 2200},
                locale="en-US",
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1400)

            # Results
            for label in ("Results", "Resultados"):
                try:
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count():
                        await loc.click(force=True, timeout=2200)
                        await page.wait_for_timeout(800)
                        break
                except Exception:
                    pass

            # Female
            activated = False
            try:
                labels = page.locator("label")
                for i in range(await labels.count()):
                    lab = labels.nth(i)
                    txt = re.sub(r"\s+"," ",(await lab.inner_text()).strip()).lower()
                    if txt not in {"female","women","femenino","femenina"}:
                        continue
                    target = await lab.get_attribute("for")
                    if target:
                        inp = page.locator(f"#{target}")
                        if await inp.count():
                            try:
                                await inp.check(force=True, timeout=2000)
                            except Exception:
                                await inp.click(force=True, timeout=2000)
                            activated = True
                    if not activated:
                        await lab.click(force=True, timeout=2000)
                        activated = True
                    break
            except Exception:
                pass

            if not activated:
                for label in ("Female","Women","Femenino","Femenina"):
                    try:
                        loc=page.get_by_text(label, exact=True).first
                        if await loc.count():
                            await loc.click(force=True, timeout=2000)
                            activated=True
                            break
                    except Exception:
                        pass

            await page.wait_for_timeout(1200)

            blocks = await page.evaluate(
                r"""
                (ranking) => {
                  const norm=s=>(s||'')
                    .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
                    .toLowerCase().replace(/[^a-z0-9]+/g,' ')
                    .replace(/\s+/g,' ').trim();
                  const clean=s=>(s||'').replace(/\s+/g,' ').trim();

                  const leaves=Array.from(document.querySelectorAll('body *'))
                    .filter(el=>el.children.length===0)
                    .filter(el=>{
                      const st=getComputedStyle(el);
                      return st.display!=='none' && st.visibility!=='hidden';
                    })
                    .map((el,i)=>({i,el,text:clean(el.textContent),n:norm(el.textContent)}))
                    .filter(x=>x.text);

                  const playerAt = x => {
                    for(const p of ranking){
                      if(!p)continue;
                      if(x.n===p || x.n.includes(p) || p.includes(x.n)){
                        // avoid tiny fragments matching long player names
                        if(x.n.length<4) continue;
                        return p;
                      }
                    }
                    return null;
                  };

                  const playerHits=leaves.map(x=>({...x,player:playerAt(x)})).filter(x=>x.player);
                  const ticks=leaves.filter(x=>x.text.includes('✓'));
                  const out=[];
                  const seen=new Set();

                  for(const tick of ticks){
                    const near=playerHits
                      .map(p=>({...p,dist:Math.abs(p.i-tick.i)}))
                      .filter(p=>p.dist<=120)
                      .sort((a,b)=>a.dist-b.dist);

                    const chosen=[];
                    for(const p of near){
                      if(!chosen.some(c=>c.player===p.player)) chosen.push(p);
                      if(chosen.length===4) break;
                    }
                    if(chosen.length!==4) continue;

                    chosen.sort((a,b)=>a.i-b.i);
                    const minI=Math.min(...chosen.map(x=>x.i),tick.i)-35;
                    const maxI=Math.max(...chosen.map(x=>x.i),tick.i)+35;
                    const scores=leaves
                      .filter(x=>x.i>=minI && x.i<=maxI && /^\d{1,2}$/.test(x.text))
                      .map(x=>Number(x.text))
                      .filter(n=>n>=0&&n<=20);

                    if(scores.length<4) continue;

                    const players=chosen.map(x=>x.player);
                    const key=players.join('|')+'::'+scores.slice(0,8).join(',');
                    if(seen.has(key))continue;
                    seen.add(key);

                    const context=leaves
                      .filter(x=>x.i>=Math.max(0,minI) && x.i<=maxI)
                      .map(x=>x.text).join(' ').slice(0,1600);

                    out.push({
                      tag:'flat-fallback',
                      text:context,
                      players,
                      scores:scores.slice(0,8)
                    });
                  }
                  return out;
                }
                """,
                [_norm_person_name(x.get("display","")) for x in ranking_profiles],
            )

            await browser.close()
            browser = None

            print(f"  FIP flat fallback: blocks={len(blocks or [])}")
            return blocks or []

    except Exception as exc:
        print(f"  FIP flat fallback error: {type(exc).__name__}: {exc}")
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        return []


async def _browser_fip_womens_draw_blocks(event: dict) -> tuple[list[dict], dict]:
    """
    Primary production extractor for completed women's main-draw matches.

    Instead of trying to infer the round from dates/results pages, this reads
    FIP's DRAW itself. The draw has visible column headings (ROUND 16,
    QUARTER-FINALS, SEMI-FINALS, FINAL). We assign every detected match card
    to the nearest heading by horizontal position.

    This directly matches the bracket UI shown by FIP and is much harder to
    misclassify than date-based inference.
    """
    diag = {
        "playwright": False,
        "female_activated": False,
        "main_activated": False,
        "headings": [],
        "candidates": 0,
        "blocks": 0,
    }

    if async_playwright is None:
        return [], diag

    url = event.get("url", "")
    if not url:
        return [], diag

    try:
        ranking_names = await get_womens_ranking_names(limit=250)
    except Exception:
        ranking_names = []

    profiles = _player_schedule_profiles(ranking_names)
    if not profiles:
        return [], diag

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            diag["playwright"] = True

            page = await browser.new_page(
                viewport={"width": 2200, "height": 1800},
                locale="en-US",
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1400)

            # Open Draws.
            for label in ("Draws", "Cuadros"):
                try:
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(force=True, timeout=2500)
                        await page.wait_for_timeout(1000)
                        break
                except Exception:
                    pass

            # Activate Female/Women.
            try:
                labels = page.locator("label")
                for li in range(await labels.count()):
                    lab = labels.nth(li)
                    text = re.sub(r"\s+", " ", (await lab.inner_text()).strip()).lower()
                    if text not in {"female", "women", "femenino", "femenina"}:
                        continue
                    target_id = await lab.get_attribute("for")
                    if target_id:
                        inp = page.locator(f"#{target_id}")
                        if await inp.count():
                            try:
                                await inp.check(force=True, timeout=2200)
                            except Exception:
                                await inp.click(force=True, timeout=2200)
                            diag["female_activated"] = True
                    if not diag["female_activated"]:
                        await lab.click(force=True, timeout=2200)
                        diag["female_activated"] = True
                    break
            except Exception:
                pass

            if not diag["female_activated"]:
                try:
                    activated = await page.evaluate(
                        r"""
                        () => {
                          const clean=s=>(s||'').replace(/\s+/g,' ').trim().toLowerCase();
                          const nodes=Array.from(document.querySelectorAll('label,button,[role=button],[role=tab]'));
                          const el=nodes.find(x=>['female','women','femenino','femenina'].includes(clean(x.innerText||x.textContent)));
                          if(!el)return false;
                          el.click();
                          el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
                          return true;
                        }
                        """
                    )
                    diag["female_activated"] = bool(activated)
                except Exception:
                    pass

            await page.wait_for_timeout(900)

            # Activate Main draw if a Qualify/Main selector exists.
            try:
                result = await page.evaluate(
                    r"""
                    () => {
                      const clean=s=>(s||'').replace(/\s+/g,' ').trim().toLowerCase();
                      const nodes=Array.from(document.querySelectorAll('label,button,[role=button],[role=tab]'));
                      const el=nodes.find(x=>['main','main draw','cuadro principal'].includes(clean(x.innerText||x.textContent)));
                      if(!el)return false;
                      el.click();
                      el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
                      return true;
                    }
                    """
                )
                diag["main_activated"] = bool(result)
            except Exception:
                pass

            await page.wait_for_timeout(1000)

            result = await page.evaluate(
                r"""
                (profiles) => {
                  const norm=s=>(s||'')
                    .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
                    .toLowerCase().replace(/[^a-z0-9]+/g,' ')
                    .replace(/\s+/g,' ').trim();
                  const clean=s=>(s||'').replace(/\s+/g,' ').trim();

                  const visible=el=>{
                    const st=getComputedStyle(el);
                    const r=el.getBoundingClientRect();
                    return st.display!=='none' && st.visibility!=='hidden' &&
                           r.width>0 && r.height>0;
                  };

                  const roundName=text=>{
                    const t=clean(text).toLowerCase();
                    if(/\bround\s*16\b|\br16\b/.test(t)) return 'Octavos de final';
                    if(/quarter[\s-]*final|1\/4\s*final|cuartos?/.test(t)) return 'Cuartos de final';
                    if(/semi[\s-]*final|semifinal/.test(t)) return 'Semifinales';
                    if(/\bfinal\b/.test(t) && !/semi|quarter|cuarto/.test(t)) return 'Final';
                    if(/\bround\s*32\b|\br32\b/.test(t)) return 'Primera ronda';
                    return '';
                  };

                  const all=Array.from(document.querySelectorAll('body *')).filter(visible);

                  // Visible bracket headings and their X centers.
                  const headings=[];
                  for(const el of all){
                    const txt=clean(el.innerText||el.textContent);
                    if(!txt || txt.length>70)continue;
                    const round=roundName(txt);
                    if(!round)continue;
                    const r=el.getBoundingClientRect();
                    headings.push({round,x:r.left+r.width/2,text:txt});
                  }

                  // Dedupe headings by round/x.
                  const hd=[];
                  for(const h of headings.sort((a,b)=>a.x-b.x)){
                    if(!hd.some(x=>x.round===h.round && Math.abs(x.x-h.x)<80))hd.push(h);
                  }

                  const matchPlayer = text => {
                    const n=norm(text);
                    if(!n || n.length<3)return null;
                    for(const p of profiles){
                      for(const alias of (p.aliases||[])){
                        if(!alias || alias.length<4)continue;
                        if(n===alias || n.includes(alias) || alias.includes(n)){
                          return p;
                        }
                      }
                    }
                    return null;
                  };

                  const leafNodes=el=>Array.from(el.querySelectorAll('*'))
                    .filter(x=>x.children.length===0 && visible(x));

                  const candidates=[];

                  for(const el of all){
                    const text=clean(el.innerText);
                    if(!text || text.length<25 || text.length>1800)continue;

                    const leaves=leafNodes(el).map(x=>{
                      const r=x.getBoundingClientRect();
                      return {
                        text:clean(x.textContent),
                        x:r.left+r.width/2,
                        y:r.top+r.height/2,
                      };
                    }).filter(x=>x.text);

                    const players=[];
                    for(const leaf of leaves){
                      const p=matchPlayer(leaf.text);
                      if(p && !players.some(q=>q.canonical===p.canonical)){
                        players.push({
                          canonical:p.canonical,
                          display:p.display,
                          x:leaf.x,
                          y:leaf.y,
                        });
                      }
                    }

                    if(players.length!==4)continue;

                    const scores=leaves
                      .filter(x=>/^\d{1,2}$/.test(x.text))
                      .map(x=>Number(x.text))
                      .filter(n=>n>=0 && n<=20);

                    if(scores.length<4 || scores.length>12)continue;

                    const r=el.getBoundingClientRect();
                    candidates.push({
                      el,
                      text,
                      players,
                      scores,
                      x:r.left+r.width/2,
                      area:r.width*r.height,
                    });
                  }

                  // Keep smallest DOM containers representing one match.
                  const minimal=candidates.filter(c=>
                    !candidates.some(o=>o!==c && c.el.contains(o.el))
                  );

                  const blocks=[];
                  const seen=new Set();

                  for(const c of minimal){
                    if(!hd.length)continue;

                    // Closest bracket column heading by horizontal center.
                    const heading=[...hd].sort((a,b)=>Math.abs(a.x-c.x)-Math.abs(b.x-c.x))[0];
                    if(!heading)continue;

                    const ordered=[...c.players].sort((a,b)=>a.y-b.y || a.x-b.x);
                    const playerNames=ordered.map(x=>x.display);

                    const key=heading.round+'|'+playerNames.join('|')+'|'+c.scores.join(',');
                    if(seen.has(key))continue;
                    seen.add(key);

                    blocks.push({
                      tag:'draw:'+heading.round,
                      text:c.text.slice(0,1600),
                      players:playerNames,
                      scores:c.scores,
                      roundHint:heading.round,
                    });
                  }

                  return {
                    headings:hd,
                    candidateCount:candidates.length,
                    blocks,
                  };
                }
                """,
                profiles,
            )

            await browser.close()
            browser = None

            diag["headings"] = result.get("headings", []) or []
            diag["candidates"] = int(result.get("candidateCount", 0) or 0)
            blocks = result.get("blocks", []) or []
            diag["blocks"] = len(blocks)

            print(
                "  FIP draw:",
                f"female={diag['female_activated']}",
                f"main={diag['main_activated']}",
                f"headings={[x.get('round') for x in diag['headings']]}",
                f"candidates={diag['candidates']}",
                f"blocks={diag['blocks']}",
            )

            return blocks, diag

    except Exception as exc:
        print(f"  FIP draw error: {type(exc).__name__}: {exc}")
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        return [], diag


async def _extract_official_results(event: dict, gender: str = "female") -> list:
    """
    EN JUEGO ONLY — v39

    Recibe bloques con 4 jugadoras + scores de todos los estados/días.
    Reconstruye pareja ganadora, perdedora, marcador, fecha y ronda.
    """
    # PRIMARY: FIP Draws tab, where the round is explicit by bracket column.
    try:
        blocks, draw_diag = await _browser_fip_womens_draw_blocks(event)
    except Exception as exc:
        print(f"  draw live-source error: {type(exc).__name__}: {exc}")
        blocks, draw_diag = [], {}

    capture_diag = {"draw": draw_diag}

    # FALLBACK: existing v39 Results parser.
    if not blocks:
        try:
            source, v39_diag = await _browser_fip_womens_results_text(event)
        except Exception as exc:
            print(f"  v39 live-source error: {type(exc).__name__}: {exc}")
            source, v39_diag = "", {}

        capture_diag["v39"] = v39_diag

        try:
            blocks = json.loads(source).get("blocks", []) if source else []
        except Exception:
            blocks = []

    if not blocks:
        for attempt in range(1, 3):
            print(f"  live results: v39 devolvió 0 bloques; reintento {attempt}/2")
            await asyncio.sleep(1.2 * attempt)
            try:
                retry_source, retry_diag = await _browser_fip_womens_results_text(event)
                retry_blocks = json.loads(retry_source).get("blocks", []) if retry_source else []
            except Exception as exc:
                print(f"  live retry {attempt} error: {type(exc).__name__}: {exc}")
                retry_blocks = []

            if retry_blocks:
                blocks = retry_blocks
                capture_diag["v39_retry"] = retry_diag
                print(f"  live retry {attempt}: recuperados {len(blocks)} bloques")
                break

    if not blocks:
        print("  live results: reintentos v39 sin datos; activando flat fallback")
        blocks = await _browser_fip_flat_results_fallback(event)

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

        # 8 Aug / 8 AUGUST
        m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,12})\b", raw)
        if m:
            d = int(m.group(1))
            mo = month_map.get(m.group(2).lower()[:3])
            if mo:
                try:
                    return date(year, mo, d).isoformat()
                except Exception:
                    pass

        # AUG 8 / AUGUST 8 — format used by FIP date chips.
        m = re.search(r"\b([A-Za-z]{3,12})\s+(\d{1,2})\b", raw)
        if m:
            mo = month_map.get(m.group(1).lower()[:3])
            d = int(m.group(2))
            if mo:
                try:
                    return date(year, mo, d).isoformat()
                except Exception:
                    pass

        return ""

    def round_from_official_date(match_date: str) -> str:
        """
        Main-draw round from the selected FIP date chip.

        Premier Padel's closing sequence is:
          event end date     -> Final
          end - 1 day        -> Semifinales
          end - 2 days       -> Cuartos de final
          end - 3 days       -> Octavos de final

        Unlike the old logic, this uses the actual date state selected on FIP,
        not the number/order of scraped matches.
        """
        if not match_date:
            return ""

        end_dt = _event_end_date_from_dates(event)
        if not end_dt:
            return ""

        try:
            md = date.fromisoformat(match_date)
        except Exception:
            return ""

        delta = (end_dt - md).days
        if delta == 0:
            return "Final"
        if delta == 1:
            return "Semifinales"
        if delta == 2:
            return "Cuartos de final"
        if delta == 3:
            return "Octavos de final"
        if delta == 4:
            return "Segunda ronda"
        if delta >= 5:
            return "Primera ronda"
        return ""


    def infer_round(context: str, match_date: str = "") -> str:
        """
        Production rule: rounds are only assigned from explicit FIP text/DOM.

        We intentionally do NOT infer a round from today's date or from the number
        of matches. A missing label is safer as "Partidos" than a wrong semifinal.
        """
        low = (context or "").lower()
        if re.search(r"semi[- ]?final", low): return "Semifinales"
        if re.search(r"quarter[- ]?final", low): return "Cuartos de final"
        if re.search(r"round of 16|octav", low): return "Octavos de final"
        if re.search(r"2nd round|second round|segunda", low): return "Segunda ronda"
        if re.search(r"1st round|first round|primera", low): return "Primera ronda"
        if re.search(r"\bqual|clasif", low): return "Clasificación"
        if re.search(r"\bfinal\b", low): return "Final"
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

        explicit_round = str(block.get("roundHint", "") or "").strip()
        context = (
            explicit_round + " " +
            str(block.get("text","")) + " " +
            str(block.get("tag",""))
        )

        date_round = round_from_official_date(match_date)

        # Production priority:
        # 1) explicit Draws-column roundHint
        # 2) selected FIP date
        # 3) local card text
        # 4) unknown
        if explicit_round:
            rnd = explicit_round
            round_source = "fip-draw-column"
        elif date_round:
            rnd = date_round
            round_source = "fip-date"
        else:
            rnd = infer_round(context, match_date)
            round_source = "fip-text" if rnd != "Partidos" else "unknown"

        parsed.append({
            "round": rnd,
            "round_source": round_source,
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

    valid = _validate_explicit_rounds(valid)

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
        "parser": "fip-draw-column-v92",
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

    global _LAST_GOOD_LIVE_RESULTS

    if valid:
        _LAST_GOOD_LIVE_RESULTS = {
            "results": [dict(x) for x in valid],
            "ts": time.time(),
        }
        return valid

    cached = _LAST_GOOD_LIVE_RESULTS.get("results") or []
    age = time.time() - float(_LAST_GOOD_LIVE_RESULTS.get("ts") or 0)
    if cached and age <= 6 * 60 * 60:
        print(f"  live stale-cache: usando {len(cached)} resultados previos · age={int(age)}s")
        return [dict(x) for x in cached]

    return valid


def _round_pair_signature(pair: str) -> tuple[str, ...]:
    players = [x.strip() for x in re.split(r"\s*/\s*", pair or "") if x.strip()]
    sig = []
    for player in players:
        parts = _norm_person_name(player).split()
        if len(parts) >= 2:
            sig.append(parts[1])
        elif parts:
            sig.append(parts[0])
    return tuple(sorted(sig))


def _validate_explicit_rounds(results: list) -> list:
    """
    Production integrity guard for the knockout draw.

    In women's singles-style knockout structure:
      Final            -> max 1 match
      Semifinales      -> max 2
      Cuartos de final -> max 4
      Octavos de final -> max 8

    Also prevents the same pair from appearing twice in one knockout round.
    Any conflicting/overflow item is kept, but moved to generic "Partidos"
    instead of publishing an impossible bracket.
    """
    capacities = {
        "Final": 1,
        "Semifinales": 2,
        "Cuartos de final": 4,
        "Octavos de final": 8,
    }

    seen_by_round = {rnd: set() for rnd in capacities}
    count_by_round = {rnd: 0 for rnd in capacities}
    out = []

    for item in results or []:
        row = dict(item)
        rnd = row.get("round") or "Partidos"

        if rnd not in capacities:
            out.append(row)
            continue

        w = _round_pair_signature(str(row.get("winner","")))
        l = _round_pair_signature(str(row.get("loser","")))

        duplicate_pair = (
            (w and w in seen_by_round[rnd]) or
            (l and l in seen_by_round[rnd])
        )
        overflow = count_by_round[rnd] >= capacities[rnd]

        if duplicate_pair or overflow:
            reason = "duplicate-pair" if duplicate_pair else "round-capacity"
            print(
                "  ROUND CONFLICT:",
                f"reason={reason}",
                f"round={rnd}",
                f"{row.get('winner','')} vs {row.get('loser','')}",
                "-> movido a Partidos",
            )
            row["round"] = "Partidos"
            row["round_source"] = f"conflict-guard:{reason}"
        else:
            count_by_round[rnd] += 1
            if w:
                seen_by_round[rnd].add(w)
            if l:
                seen_by_round[rnd].add(l)

        out.append(row)

    print(
        "  round counts:",
        " ".join(f"{rnd}={count_by_round[rnd]}" for rnd in capacities),
    )
    return out


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
        "round_source": str(item.get("round_source", "") or ""),
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
        "date": str(item.get("date", "") or ""),
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


def _repair_current_round_from_previous(results: list, event: dict) -> list:
    """
    Repair round labels using bracket membership, not DOM wording.

    On semifinal day, any completed match whose two pairs are both quarterfinal
    winners is a semifinal. This lets us recognize that BOTH semifinals are done
    even when FIP's stale OOP still points at one of them.
    """
    stage = _stage_from_event(event)
    previous_round = _round_before(stage)
    if stage not in {"Cuartos de final", "Semifinales", "Final"} or not previous_round:
        return results

    previous = [x for x in results if x.get("round") == previous_round]
    previous_winners = [
        str(x.get("winner", "") or "").strip()
        for x in previous
        if _concrete_pair(str(x.get("winner", "") or ""))
    ]

    if len(previous_winners) < 2:
        return results

    repaired = 0
    for item in results:
        winner = str(item.get("winner", "") or "").strip()
        loser = str(item.get("loser", "") or "").strip()
        if not (_concrete_pair(winner) and _concrete_pair(loser)):
            continue

        winner_from_prev = any(_same_pair(winner, p) for p in previous_winners)
        loser_from_prev = any(_same_pair(loser, p) for p in previous_winners)

        if winner_from_prev and loser_from_prev and item.get("round") != stage:
            item["round"] = stage
            repaired += 1

    if repaired:
        print(f"  round repair: {repaired} partidos relabelados como {stage}")

    return results


def _pair_key(value: str) -> str:
    """Canonical full pair key, independent of accents/order spacing."""
    parts = [
        _norm_person_name(x)
        for x in re.split(r"\s*/\s*", value or "")
        if _norm_person_name(x)
    ]
    return " / ".join(parts)


def _player_first_surname(value: str) -> str:
    """
    Stable identity for FIP/ranking name variants.

    Examples:
      Claudia Fernandez Sanchez -> fernandez
      Claudia Fernandez         -> fernandez
      Gemma Triay Pons          -> triay
      Gemma Triay               -> triay
    """
    parts = _norm_person_name(value).split()
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else ""


def _pair_signature(value: str) -> tuple[str, ...]:
    """
    Compare a pair by the two first surnames, ignoring player order and
    extra civil surnames. This is much more robust than full-string equality.
    """
    players = [
        x.strip()
        for x in re.split(r"\s*/\s*", value or "")
        if x.strip()
    ]
    surnames = [_player_first_surname(x) for x in players]
    surnames = [x for x in surnames if x]
    return tuple(sorted(surnames))


def _same_pair(a: str, b: str) -> bool:
    sa = _pair_signature(a)
    sb = _pair_signature(b)
    return bool(len(sa) == 2 and sa == sb)


def _concrete_pair(value: str) -> bool:
    """True only when a real pair is known, not a placeholder."""
    key = _pair_key(value)
    return bool(key and "confirmar" not in key and len(key.split("/")) >= 2)


def _next_round_after(round_name: str) -> str | None:
    chain = [
        "Octavos de final",
        "Cuartos de final",
        "Semifinales",
        "Final",
    ]
    try:
        idx = chain.index(round_name)
    except ValueError:
        return None
    return chain[idx + 1] if idx + 1 < len(chain) else None


def _round_before(round_name: str) -> str | None:
    chain = [
        "Octavos de final",
        "Cuartos de final",
        "Semifinales",
        "Final",
    ]
    try:
        idx = chain.index(round_name)
    except ValueError:
        return None
    return chain[idx - 1] if idx > 0 else None


def _match_already_completed(results: list, match: dict | None) -> bool:
    """
    True if both proposed pairs already appear together in a completed result.

    Pair comparison uses first-surname signatures, so:
      Gemma Triay Pons / Delfina Brea Senesi
    matches:
      Gemma Triay / Delfina Brea
    """
    if not match:
        return False

    p1 = str(match.get("pair1", "") or "").strip()
    p2 = str(match.get("pair2", "") or "").strip()
    if not (_concrete_pair(p1) and _concrete_pair(p2)):
        return False

    for item in results or []:
        winner = str(item.get("winner", "") or "").strip()
        loser = str(item.get("loser", "") or "").strip()
        if not (_concrete_pair(winner) and _concrete_pair(loser)):
            continue

        same_order = _same_pair(p1, winner) and _same_pair(p2, loser)
        swapped = _same_pair(p1, loser) and _same_pair(p2, winner)

        if same_order or swapped:
            return True

    return False


def _derive_next_match_from_bracket(results: list, event: dict) -> dict | None:
    """
    Deterministic fallback based on completed FIP results.

    This avoids depending on FIP Order-of-Play DOM, which has proved unstable.

    Example on semifinal day:
      - 4 quarterfinal winners are known.
      - 1 semifinal is already completed.
      - The 2 quarterfinal winners not present in that completed semifinal
        MUST be the remaining semifinal.

    When the whole current round is complete, the winners form the next round.
    """
    if not results:
        return None

    stage = _stage_from_event(event)
    if stage not in {"Cuartos de final", "Semifinales", "Final"}:
        return None

    current = [x for x in results if (x.get("round") or "") == stage]
    capacity = _round_capacity(stage)

    # If today's round is already complete, derive the next round from its winners.
    if capacity and len(current) >= capacity:
        next_round = _next_round_after(stage)
        if not next_round:
            return None

        winners = []
        seen = set()
        for item in current:
            pair = str(item.get("winner", "") or "").strip()
            key = _pair_key(pair)
            if _concrete_pair(pair) and key not in seen:
                seen.add(key)
                winners.append(pair)

        if len(winners) >= 2:
            tomorrow = date.today() + timedelta(days=1)
            return {
                "round": next_round,
                "pair1": winners[0],
                "pair2": winners[1],
                "date": tomorrow.isoformat(),
                "time": "",
                "time_type": "unknown",
                "when": "Mañana · horario por confirmar (hora de España)",
                "iso_madrid": "",
                "source": "cuadro FIP · ganadoras de la ronda anterior",
                "derived_from_bracket": True,
            }
        return None

    previous_round = _round_before(stage)
    if not previous_round:
        return None

    previous = [x for x in results if (x.get("round") or "") == previous_round]
    previous_capacity = _round_capacity(previous_round)
    if previous_capacity and len(previous) < previous_capacity:
        return None

    prev_winners = []
    seen = set()
    for item in previous:
        pair = str(item.get("winner", "") or "").strip()
        key = _pair_key(pair)
        if _concrete_pair(pair) and key not in seen:
            seen.add(key)
            prev_winners.append(pair)

    if len(prev_winners) < 2:
        return None

    # Remove every pair that has already appeared in a completed current-round
    # match. Compare by first surnames so FIP full-name variants still match.
    used_pairs = []
    for item in current:
        for field in ("winner", "loser"):
            pair = str(item.get(field, "") or "").strip()
            if _concrete_pair(pair):
                used_pairs.append(pair)

    remaining = [
        p for p in prev_winners
        if not any(_same_pair(p, used) for used in used_pairs)
    ]

    print(
        "  bracket derive:",
        f"stage={stage}",
        f"prev_winners={len(prev_winners)}",
        f"current_done={len(current)}",
        f"used={len(used_pairs)}",
        f"remaining={len(remaining)}",
        "remaining_pairs=" + " || ".join(remaining),
    )

    # Best case: only two pairs remain -> the next match is unambiguous.
    if len(remaining) == 2:
        print(f"  bracket next CONFIRMADO: {remaining[0]} vs {remaining[1]}")
        return {
            "round": stage,
            "pair1": remaining[0],
            "pair2": remaining[1],
            "date": date.today().isoformat(),
            "time": "",
            "time_type": "unknown",
            "when": "Hoy · horario por confirmar (hora de España)",
            "iso_madrid": "",
            "source": "cuadro FIP · cruce restante",
            "derived_from_bracket": True,
        }

    # If no current match has finished yet, bracket order is deterministic:
    # QF1/QF2 feed SF1, QF3/QF4 feed SF2; same principle for later rounds.
    if not current and len(prev_winners) >= 2:
        pairings = [
            (prev_winners[i], prev_winners[i + 1])
            for i in range(0, len(prev_winners) - 1, 2)
        ]
        if pairings:
            p1, p2 = pairings[0]
            return {
                "round": stage,
                "pair1": p1,
                "pair2": p2,
                "date": date.today().isoformat(),
                "time": "",
                "time_type": "unknown",
                "when": "Hoy · horario por confirmar (hora de España)",
                "iso_madrid": "",
                "source": "cuadro FIP · siguiente cruce",
                "derived_from_bracket": True,
            }

    return None


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


def _player_schedule_profiles(names: list[str]) -> list[dict]:
    """
    Build aliases that also match abbreviated FIP names:
      Claudia Fernandez Sanchez -> "c fernandez sanchez"
      Paula Josemaria Martin     -> "p josemaria martin"
    """
    out = []
    seen = set()
    for raw in names or []:
        canon = _norm_person_name(raw)
        parts = canon.split()
        if len(parts) < 2 or canon in seen:
            continue
        seen.add(canon)

        aliases = {canon}
        first_initial = parts[0][:1]
        if first_initial:
            aliases.add(" ".join([first_initial] + parts[1:]))
            aliases.add(f"{first_initial} {parts[1]}")

        # Public/sporting name.
        aliases.add(" ".join(parts[:2]))

        out.append({
            "canonical": canon,
            "display": re.sub(r"\s+", " ", raw).strip(),
            "aliases": sorted(aliases, key=len, reverse=True),
        })
    return out


async def _scan_fip_schedule_frames(page, profiles: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    FIP's Order of Play can render inside an iframe/widget rather than in the
    event page DOM. Scan every frame, including cross-page iframe documents.
    Returns (cards, diagnostics).
    """
    diagnostics = []
    all_cards = []

    parser_js = r"""
    (profiles) => {
      const norm=s=>(s||'')
        .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
        .toLowerCase().replace(/[^a-z0-9]+/g,' ')
        .replace(/\s+/g,' ').trim();
      const clean=s=>(s||'').replace(/\s+/g,' ').trim();

      const matchPlayer = text => {
        const n=norm(text);
        if(!n || n.length<3)return null;
        for(const p of profiles){
          for(const a of p.aliases){
            if(n===a || n.includes(a) || a.includes(n)){
              if(Math.min(n.length,a.length)>=4)return p;
            }
          }
        }
        return null;
      };

      const visible=el=>{
        const st=getComputedStyle(el);
        const r=el.getBoundingClientRect();
        return st.display!=='none' && st.visibility!=='hidden' &&
               r.width>0 && r.height>0;
      };

      const all=Array.from(document.querySelectorAll('body *')).filter(visible);
      const candidates=[];

      for(const el of all){
        const text=clean(el.innerText);
        if(!text || text.length<28 || text.length>2200)continue;

        // Match either explicit WOMEN/FEMALE or a 4-player card made entirely
        // from known female ranking players.
        const leaves=Array.from(el.querySelectorAll('*'))
          .filter(x=>x.children.length===0 && visible(x))
          .map(x=>{
            const r=x.getBoundingClientRect();
            return {
              text:clean(x.textContent),
              n:norm(x.textContent),
              y:r.top+r.height/2,
              x:r.left+r.width/2
            };
          })
          .filter(x=>x.text);

        const hits=[];
        for(const leaf of leaves){
          const p=matchPlayer(leaf.text);
          if(p && !hits.some(h=>h.canonical===p.canonical)){
            hits.push({...p,y:leaf.y,x:leaf.x,leafText:leaf.text});
          }
        }

        const explicitWomen=/\b(women|female|femenin)\b/i.test(text);
        if(!explicitWomen && hits.length!==4)continue;
        if(hits.length<2 || hits.length>4)continue;

        const scoreLeaves=leaves
          .filter(x=>/^\d{1,2}$/.test(x.text))
          .map(x=>({...x,value:Number(x.text)}))
          .filter(x=>x.value>=0 && x.value<=20);

        const completed=/\b(completed|finished|finalizado|finalizada)\b/i.test(text);
        const explicitLive=/\b(live|in progress|playing|on court|directo|en juego|en curso)\b/i.test(text);

        const status=completed
          ? 'completed'
          : ((explicitLive || scoreLeaves.length>=2) ? 'live' : 'upcoming');

        let round='Partido';
        if(/\bsemi[- ]?finals?\b/i.test(text))round='Semifinales';
        else if(/\bquarter[- ]?finals?\b/i.test(text))round='Cuartos de final';
        else if(/\bround of 16\b|\boctav/i.test(text))round='Octavos de final';
        else if(/\bfinal\b/i.test(text))round='Final';
        else if(/\bsecond round\b|\b2nd round\b/i.test(text))round='Segunda ronda';
        else if(/\bfirst round\b|\b1st round\b/i.test(text))round='Primera ronda';
        else if(/\bqual/i.test(text))round='Clasificación';

        candidates.push({el,text,hits,scoreLeaves,status,round});
      }

      const minimal=candidates.filter(c=>
        !candidates.some(o=>o!==c && c.el.contains(o.el))
      );

      const out=[];
      const seen=new Set();

      for(const c of minimal){
        const ordered=[...c.hits].sort((a,b)=>a.y-b.y || a.x-b.x);

        let teamA=[],teamB=[];
        if(ordered.length>=4){
          teamA=ordered.slice(0,2);
          teamB=ordered.slice(2,4);
        }else{
          teamA=ordered.slice(0,2);
        }

        const pair1=teamA.map(x=>x.display).join(' / ');
        const pair2=teamB.length
          ? teamB.map(x=>x.display).join(' / ')
          : 'Pareja por confirmar';

        let aScores=[],bScores=[];
        if(teamA.length && teamB.length && c.scoreLeaves.length){
          const ay=teamA.reduce((a,x)=>a+x.y,0)/teamA.length;
          const by=teamB.reduce((a,x)=>a+x.y,0)/teamB.length;

          aScores=c.scoreLeaves
            .filter(sc=>Math.abs(sc.y-ay)<=Math.abs(sc.y-by))
            .sort((a,b)=>a.x-b.x)
            .map(sc=>sc.value);

          bScores=c.scoreLeaves
            .filter(sc=>Math.abs(sc.y-by)<Math.abs(sc.y-ay))
            .sort((a,b)=>a.x-b.x)
            .map(sc=>sc.value);
        }

        const key=[c.round,pair1,pair2,c.status,aScores.join(','),bScores.join(',')].join('|');
        if(seen.has(key))continue;
        seen.add(key);

        out.push({
          round:c.round,
          pair1,pair2,
          status:c.status,
          team_a_scores:aScores,
          team_b_scores:bScores,
          raw:c.text.slice(0,1400)
        });
      }

      return out;
    }
    """

    for idx, frame in enumerate(page.frames):
        try:
            body = re.sub(r"\s+", " ", (await frame.locator("body").inner_text()).strip())
        except Exception:
            body = ""

        diag = {
            "index": idx,
            "url": frame.url,
            "chars": len(body),
            "women": bool(re.search(r"\b(women|female|femenin)", body, re.I)),
            "completed": "completed" in body.lower(),
            "semifinals": "semifinals" in body.lower(),
        }

        try:
            cards = await frame.evaluate(parser_js, profiles)
        except Exception as exc:
            cards = []
            diag["error"] = f"{type(exc).__name__}: {exc}"

        diag["cards"] = len(cards or [])
        diagnostics.append(diag)
        all_cards.extend(cards or [])

    # Dedupe cards across parent frame + iframe(s).
    deduped = []
    seen = set()
    for card in all_cards:
        key = (
            card.get("round",""),
            card.get("pair1",""),
            card.get("pair2",""),
            card.get("status",""),
            tuple(card.get("team_a_scores") or []),
            tuple(card.get("team_b_scores") or []),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(card)

    return deduped, diagnostics


async def _extract_fip_today_womens_cards(event: dict) -> list[dict]:
    """
    Read today's WOMEN match cards directly from FIP.

    FIP exposes cards such as:
      WOMEN / SEMIFINALS
      C. Fernandez Sanchez / M. Calvo
      6 6
      P. Josemaria Martin / B. Gonzalez Fernandez
      2 4
      COMPLETED

    We use that card itself as source of truth for:
      - round
      - COMPLETED / LIVE / UPCOMING
      - pairs
      - current score / final score

    No LLM and no score inference from unrelated page text.
    """
    if async_playwright is None:
        return []

    url = event.get("url", "")
    if not url:
        return []

    try:
        ranking_names = await get_womens_ranking_names(limit=250)
    except Exception:
        ranking_names = []

    profiles = _player_schedule_profiles(ranking_names)
    if not profiles:
        return []

    madrid_now = datetime.now(ZoneInfo("Europe/Madrid"))
    event_tz = ZoneInfo(_event_timezone(event.get("place", "")))
    local_now = madrid_now.astimezone(event_tz)

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(
                viewport={"width": 1600, "height": 2400},
                locale="en-US",
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1400)

            # Open Order of Play robustly. FIP changes the tab markup often,
            # so do not depend on exact visible text.
            oop_clicked = False
            try:
                oop_clicked = await page.evaluate(
                    r"""
                    () => {
                      const clean=s=>(s||'').replace(/\s+/g,' ').trim().toLowerCase();
                      const nodes=Array.from(document.querySelectorAll(
                        'a,button,[role=button],[role=tab],li,label,div'
                      ));
                      const wanted=[
                        'order of play','order ofplay','schedule','matches',
                        'orden de juego','partidos'
                      ];
                      // Prefer clickable/small nodes, not page wrappers.
                      const matches=nodes.filter(el=>{
                        const t=clean(el.innerText||el.textContent);
                        if(!t || t.length>80)return false;
                        return wanted.some(w=>t===w || t.includes(w));
                      });
                      const el=matches.find(x=>x.matches('a,button,[role=button],[role=tab]')) || matches[0];
                      if(!el)return false;
                      el.click();
                      el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
                      return true;
                    }
                    """
                )
                if oop_clicked:
                    await page.wait_for_timeout(1200)
            except Exception:
                pass

            # If the tab is a real link and the JS click did not expose the
            # schedule, follow an href containing order/schedule/matches.
            try:
                body_probe = (await page.locator("body").inner_text()).lower()
            except Exception:
                body_probe = ""
            if not any(x in body_probe for x in ("women", "semifinals", "quarterfinals", "completed")):
                try:
                    href = await page.evaluate(
                        r"""
                        () => {
                          const links=Array.from(document.querySelectorAll('a[href]'));
                          const a=links.find(x=>/order[-_ ]?of[-_ ]?play|schedule|matches/i.test(
                            (x.innerText||'')+' '+(x.getAttribute('href')||'')
                          ));
                          return a ? a.href : '';
                        }
                        """
                    )
                    if href:
                        await page.goto(href, wait_until="domcontentloaded", timeout=45000)
                        await page.wait_for_timeout(1200)
                except Exception:
                    pass

            # Select today's date if date buttons are exposed.
            today_tokens = [
                local_now.strftime("%b").upper() + " " + str(local_now.day),
                local_now.strftime("%b").upper() + " " + f"{local_now.day:02d}",
                str(local_now.day) + " " + local_now.strftime("%b").upper(),
            ]
            try:
                clicked_today = await page.evaluate(
                    r"""
                    (tokens) => {
                      const clean=s=>(s||'').replace(/\s+/g,' ').trim().toUpperCase();
                      const nodes=Array.from(document.querySelectorAll(
                        'button,a,[role=button],[role=tab],label'
                      ));
                      const el=nodes.find(x=>{
                        const t=clean(x.innerText||x.textContent);
                        return tokens.some(tok=>t.includes(tok));
                      });
                      if(!el)return false;
                      el.click();
                      el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
                      return true;
                    }
                    """,
                    today_tokens,
                )
                if clicked_today:
                    await page.wait_for_timeout(900)
            except Exception:
                pass

            try:
                probe_text = re.sub(r"\s+", " ", (await page.locator("body").inner_text()).strip())
                print(
                    "  FIP today probe:",
                    f"oop_clicked={oop_clicked}",
                    f"url={page.url}",
                    f"women={'women' in probe_text.lower()}",
                    f"completed={'completed' in probe_text.lower()}",
                    f"semifinals={'semifinals' in probe_text.lower()}",
                    f"chars={len(probe_text)}",
                    f"frames={len(page.frames)}",
                )
            except Exception:
                pass

            cards, frame_diag = await _scan_fip_schedule_frames(page, profiles)

            for fd in frame_diag:
                print(
                    "  FIP frame probe:",
                    f"idx={fd.get('index')}",
                    f"cards={fd.get('cards',0)}",
                    f"women={fd.get('women')}",
                    f"completed={fd.get('completed')}",
                    f"semifinals={fd.get('semifinals')}",
                    f"chars={fd.get('chars',0)}",
                    f"url={fd.get('url','')[:180]}",
                )


            await browser.close()
            browser = None

            # Normalize and make score strings.
            normalized = []
            for card in cards or []:
                a = [int(x) for x in card.get("team_a_scores", [])]
                b = [int(x) for x in card.get("team_b_scores", [])]
                score = ""
                if a and b:
                    n = min(len(a), len(b))
                    score = "  ".join(f"{a[i]}-{b[i]}" for i in range(n))

                normalized.append({
                    "round": card.get("round") or "Partido",
                    "pair1": card.get("pair1") or "",
                    "pair2": card.get("pair2") or "Pareja por confirmar",
                    "status": card.get("status") or "upcoming",
                    "score": score,
                    "team_a_scores": a,
                    "team_b_scores": b,
                    "raw": card.get("raw",""),
                })

            status_counts = {
                st: len([x for x in normalized if x.get("status")==st])
                for st in ("completed","live","upcoming")
            }
            print(
                "  FIP today cards:",
                f"total={len(normalized)}",
                f"completed={status_counts['completed']}",
                f"live={status_counts['live']}",
                f"upcoming={status_counts['upcoming']}",
            )
            return normalized

    except Exception as exc:
        print(f"  FIP today cards error: {type(exc).__name__}: {exc}")
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        return []


def _completed_card_to_result(card: dict) -> dict | None:
    """Convert a FIP completed card into the existing results schema."""
    if not card or card.get("status") != "completed":
        return None

    pair_a = card.get("pair1","")
    pair_b = card.get("pair2","")
    a = card.get("team_a_scores") or []
    b = card.get("team_b_scores") or []
    if not pair_a or not pair_b or not a or not b:
        return None

    n=min(len(a),len(b))
    if not n:
        return None

    a_sets=sum(1 for i in range(n) if a[i]>b[i])
    b_sets=sum(1 for i in range(n) if b[i]>a[i])
    if a_sets==b_sets:
        return None

    if a_sets>b_sets:
        winner,loser=pair_a,pair_b
        display=[(a[i],b[i]) for i in range(n)]
    else:
        winner,loser=pair_b,pair_a
        display=[(b[i],a[i]) for i in range(n)]

    return {
        "round": card.get("round") or "Partido",
        "winner": winner,
        "loser": loser,
        "score": "  ".join(f"{x}-{y}" for x,y in display),
        "date": date.today().isoformat(),
    }


def _card_to_top_match(card: dict) -> dict | None:
    if not card:
        return None

    status=card.get("status")
    if status=="live":
        return {
            "status":"live",
            "status_label":"EN DIRECTO",
            "round":card.get("round") or "Partido",
            "pair1":card.get("pair1") or "Pareja por confirmar",
            "pair2":card.get("pair2") or "Pareja por confirmar",
            "score":card.get("score") or "",
            "when":"Ahora · hora de España",
            "iso_madrid":datetime.now(ZoneInfo("Europe/Madrid")).isoformat(),
            "status_detail":"Partido en curso · al finalizar pasará automáticamente a resultados.",
            "source":"FIP Order of Play",
        }

    if status=="upcoming":
        return {
            "round":card.get("round") or "Próximo partido",
            "pair1":card.get("pair1") or "Pareja por confirmar",
            "pair2":card.get("pair2") or "Pareja por confirmar",
            "when":"Horario por confirmar",
            "iso_madrid":"",
            "source":"FIP Order of Play",
        }

    return None


async def _extract_current_womens_match(event: dict) -> dict | None:
    """
    Consulta exclusivamente Live Score de FIP.

    Solo devuelve un partido como EN DIRECTO si hay evidencia explícita de que el
    bloque está en curso. No deduce "live" por la hora.
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
                        await page.wait_for_timeout(900)
                        live_opened = True
                        break
                except Exception:
                    pass

            if not live_opened:
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
                        await page.wait_for_timeout(900)
                except Exception:
                    pass

            # Female only inside this probe.
            for label in ("Female", "Women", "Femenino", "Femenina"):
                try:
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(force=True, timeout=2200)
                        await page.wait_for_timeout(800)
                        break
                except Exception:
                    pass

            state = await page.evaluate(
                r"""
                (ranking) => {
                  const norm=s=>(s||'')
                    .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
                    .toLowerCase().replace(/[^a-z0-9]+/g,' ')
                    .replace(/\s+/g,' ').trim();
                  const clean=s=>(s||'').replace(/\s+/g,' ').trim();

                  const liveRx=/\b(live|in progress|playing|on court|court \d+|set \d+|directo|en juego|en curso)\b/i;
                  const finishedRx=/\b(finalizado|finished|completed|winner|ganador)\b/i;

                  const all=Array.from(document.querySelectorAll('body *'));
                  const candidates=[];

                  for(const el of all){
                    const style=getComputedStyle(el);
                    if(style.display==='none'||style.visibility==='hidden')continue;

                    const text=clean(el.innerText);
                    if(!text||text.length<25||text.length>1800)continue;
                    if(!liveRx.test(text) || finishedRx.test(text))continue;

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

                  // Prefer the most compact true-live block.
                  minimal.sort((a,b)=>a.text.length-b.text.length);
                  const c=minimal[0];

                  const nt=' '+norm(c.text)+' ';
                  const ordered=c.players
                    .map(p=>({p,i:nt.indexOf(' '+p+' ')}))
                    .filter(x=>x.i>=0)
                    .sort((a,b)=>a.i-b.i)
                    .map(x=>x.p);

                  return {text:c.text,players:ordered,scores:c.scores};
                }
                """,
                [_norm_person_name(x.get("display","")) for x in ranking_profiles],
            )

            await browser.close()
            browser = None

            if not state or len(state.get("players", [])) != 4:
                print("  live-score: no hay partido femenino en directo confirmado")
                return None

            players = [str(x).title() for x in state["players"]]
            text = str(state.get("text", ""))
            scores = [int(x) for x in state.get("scores", []) if str(x).isdigit()]

            pair1 = f"{players[0]} / {players[1]}"
            pair2 = f"{players[2]} / {players[3]}"

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

            print(f"  live-score CONFIRMADO: {pair1} vs {pair2} · {rnd} · {score_text or 'sin score visible'}")

            return {
                "status": "live",
                "status_label": "EN DIRECTO",
                "round": rnd,
                "pair1": pair1,
                "pair2": pair2,
                "score": score_text,
                "when": "Ahora · hora de España",
                "iso_madrid": datetime.now(ZoneInfo("Europe/Madrid")).isoformat(),
                "status_detail": "Partido en curso · al finalizar pasará automáticamente a resultados.",
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
    Results-only En juego.

    The page now shows only completed women's matches.
    No Live Score probe, no next-match inference, no Order of Play dependency.
    """
    today_str = date.today().strftime("%d/%m/%Y")
    event = await _get_official_live_event()

    if not event:
        return {
            "active": False,
            "name": "",
            "place": "",
            "dates": "",
            "watch": [],
            "results": [],
            "next_match": None,
            "current_match": None,
            "updated": today_str,
            "gender": gender,
            "source": "FIP",
            "source_url": FIP_PREMIER_CALENDAR_URL.format(year=date.today().year),
        }

    parts = await asyncio.gather(
        _get_watch_official(),
        _extract_official_results(event, gender),
        return_exceptions=True,
    )

    watch, results = parts

    if isinstance(watch, Exception):
        print(f"  live watch error: {watch}")
        watch = []

    if isinstance(results, Exception):
        print(f"  live results error: {results}")
        results = []

    # IMPORTANT: rounds already come from explicit FIP DOM/text.
    # Never reconstruct them from match counts or today's tournament stage.
    results = _validate_explicit_rounds(results or [])

    normalized_results = []
    for result in (results or []):
        normalized = _normalize_scoreboard_result(result)
        if normalized:
            normalized_results.append(normalized)

    _LIVE_DEBUG_STATE["mode"] = "results-only"
    _LIVE_DEBUG_STATE["results_count"] = len(normalized_results)

    print(
        "  live results-only:",
        f"results={len(normalized_results)}",
        f"stage={_stage_from_event(event)}",
    )

    return {
        "active": True,
        "name": event["name"],
        "place": event["place"],
        "dates": event["dates"],
        "watch": watch or [],
        "results": normalized_results,
        "next_match": None,
        "current_match": None,
        "updated": today_str,
        "gender": gender,
        "source": "FIP · Premier Padel",
        "source_url": event["url"],
        "_debug": dict(_LIVE_DEBUG_STATE),
    }
