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
from datetime import date
from bs4 import BeautifulSoup
from groq import Groq

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
