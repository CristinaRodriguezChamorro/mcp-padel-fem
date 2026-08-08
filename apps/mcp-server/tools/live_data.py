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


async def _extract_official_results(event: dict, gender: str = "female") -> list:
    """
    Devuelve los partidos femeninos YA TERMINADOS del torneo que sigue en curso.

    Importante: no espera a que termine el torneo. En cuanto la fuente oficial FIP
    publica un partido finalizado (primera ronda, octavos, cuartos, etc.), ese
    resultado puede aparecer en "En juego" en la siguiente actualización.
    """
    gender = (gender or "female").strip().lower()
    if gender == "women":
        gender = "female"
    if gender not in {"female", "male"}:
        gender = "female"
    filtered_html = await _get_gender_filtered_event_html(event, gender)
    soup = BeautifulSoup(filtered_html, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    official_text = _clean_text(soup)
    if not official_text:
        return []

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("  live results: GROQ_API_KEY no configurada")
        return []

    # La página de FIP coloca normalmente primero el cuadro masculino y después
    # el femenino. En la versión anterior cortábamos los primeros 60k caracteres,
    # por lo que muchas veces Groq ni siquiera recibía los partidos femeninos.
    #
    # Intentamos extraer contenedores cuyo id/class/atributos indiquen female/women.
    gender_chunks = []
    for el in soup.find_all(True):
        attrs = " ".join([
            str(el.get("id", "")),
            " ".join(el.get("class", []) if isinstance(el.get("class", []), list) else [str(el.get("class", ""))]),
            str(el.get("data-gender", "")),
            str(el.get("data-category", "")),
            str(el.get("aria-label", "")),
        ]).lower()
        if any(k in attrs for k in _gender_aliases(gender)):
            txt = _clean_text(el)
            if len(txt) > 120:
                gender_chunks.append(txt)

    # Quitamos duplicados conservando orden.
    unique_chunks = []
    seen_chunks = set()
    for chunk in gender_chunks:
        key = chunk[:500]
        if key not in seen_chunks:
            seen_chunks.add(key)
            unique_chunks.append(chunk)

    if unique_chunks:
        source = "\n\n".join(unique_chunks)[:90000]
    else:
        # Fallback: usar la parte final del documento, donde FIP suele renderizar
        # el cuadro femenino, en vez de la parte inicial dominada por el masculino.
        source = official_text[-90000:]

    client = Groq(api_key=api_key)
    requested_label = "FEMENINO" if gender == "female" else "MASCULINO"
    prompt = f"""Extrae resultados del circuito {requested_label} únicamente del siguiente texto de la web OFICIAL FIP.
No uses conocimiento externo. No deduzcas marcadores. No completes nombres.

FUENTE OFICIAL FIP:
{source}

Devuelve SOLO JSON con TODOS los partidos FEMENINOS FINALIZADOS que aparezcan claramente en la fuente, ordenados de la ronda más reciente a la más antigua:
[{{"round":"Semifinal", "winner":"Apellido / Apellido", "loser":"Apellido / Apellido", "score":"6-1, 7-5"}}]

Reglas:
- Cada pareja debe contener exactamente 2 jugadoras.
- winner es la pareja marcada como vencedora en FIP.
- score es el resultado por sets desde el punto de vista de winner.
- No incluyas partidos sin resultado final.
- No incluyas el género contrario al solicitado ({requested_label}).
- Si no puedes demostrar un partido con el texto, omítelo.
- Si no hay resultados femeninos claros, devuelve []."""

    try:
        resp = await asyncio.to_thread(lambda: client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=5000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        ))
        raw = resp.choices[0].message.content.strip()
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            return []
        parsed = json.loads(m.group())
    except Exception as e:
        print(f"  official FIP result extraction error: {e}")
        return []

    normalized_source = re.sub(r"\s+", " ", official_text).lower()
    valid = []
    for item in parsed[:40]:
        winner = str(item.get("winner", "")).strip()
        loser = str(item.get("loser", "")).strip()
        score = str(item.get("score", "")).strip()
        rnd = str(item.get("round", "")).strip()
        if not winner or not loser or not score or "/" not in winner or "/" not in loser:
            continue
        if not re.fullmatch(r"\d{1,2}-\d{1,2}(?:\s*,\s*\d{1,2}-\d{1,2}){1,2}", score):
            continue

        names = [x.strip().lower() for x in re.split(r"/", winner + "/" + loser) if x.strip()]
        surnames = [n.split()[-1] for n in names if n.split()]
        if len(surnames) != 4 or not all(tok in normalized_source for tok in surnames):
            continue

        valid.append({"round": rnd, "winner": winner, "loser": loser, "score": score})
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
            "watch": [], "results": [], "updated": today_str,
            "gender": gender,
            "source": "FIP", "source_url": FIP_PREMIER_CALENDAR_URL.format(year=date.today().year),
        }

    watch, results = await asyncio.gather(
        _get_watch_official(),
        _extract_official_results(event, gender),
    )
    return {
        "active": True,
        "name": event["name"],
        "place": event["place"],
        "dates": event["dates"],
        "watch": watch,
        "results": results,
        "updated": today_str,
        "gender": gender,
        "source": "FIP · Premier Padel",
        "source_url": event["url"],
    }
