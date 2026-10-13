"""
wikipedia_source.py — Ranking femenino y calendario desde Wikipedia.

Por qué Wikipedia: es la única fuente que he encontrado con licencia clara para reutilizar
(CC BY-SA 4.0) y con una API pensada para programas (MediaWiki). La web de la FIP prohíbe
robots y copiar su contenido sin licencia escrita, así que NO se lee.

Qué se pide: una sola página (Premier_Padel_<año>) por ejecución, con un User-Agent que
identifica al proyecto, y con `maxlag` para no cargar a Wikipedia si va mal de rendimiento.

Qué NO garantiza: Wikipedia la mantienen voluntarios. Los datos pueden ir con semanas de
retraso (la página indica su propia fecha: "Updated on ...") y las tablas cambian de formato.
Por eso:
  - todo texto extraído se limpia y se rechaza si tiene caracteres raros (la web los pinta
    como HTML, y cualquiera puede editar Wikipedia);
  - las filas que no encajan se descartan en vez de adivinar;
  - quien llama (ingest.py) valida el conjunto antes de guardarlo.

Este módulo no escribe nada en disco.
"""

import re
import unicodedata
from datetime import date
from html.parser import HTMLParser
from typing import Callable, Optional

import httpx

from tools.http_policy import USER_AGENT

API_URL = "https://en.wikipedia.org/w/api.php"
PAGE_TITLE = "Premier_Padel_{year}"
PAGE_URL = "https://en.wikipedia.org/wiki/Premier_Padel_{year}"
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


class SourceError(Exception):
    """No se pudo obtener o entender la página."""


# ── texto seguro ─────────────────────────────────────────────────────────────

_SAFE_NAME = re.compile(r"^[\w'’. \-]{2,60}$", re.UNICODE)
_SAFE_SCORE = re.compile(r"^[\dA-Za-z–\-/ ().]{1,40}$")


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    text = text.replace("\xa0", " ").replace("<", "").replace(">", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def safe_name(text: str) -> Optional[str]:
    text = clean_text(text).replace("\n", " ")
    return text if _SAFE_NAME.match(text) else None


def safe_score(text: str) -> Optional[str]:
    text = clean_text(text).replace("\n", " ")
    return text if _SAFE_SCORE.match(text) else None


# ── países ───────────────────────────────────────────────────────────────────

# código COI -> ISO-2 (para pintar la bandera) y nombres en inglés/español que Wikipedia puede mostrar
_IOC_TO_ISO2 = {
    "ESP": "ES", "ARG": "AR", "POR": "PT", "BRA": "BR", "ITA": "IT", "FRA": "FR", "MEX": "MX",
    "CHI": "CL", "CHL": "CL", "PAR": "PY", "URU": "UY", "GBR": "GB", "NED": "NL", "GER": "DE",
    "BEL": "BE", "EGY": "EG", "KSA": "SA", "QAT": "QA", "UAE": "AE", "KUW": "KW", "KWT": "KW",
    "USA": "US", "RSA": "ZA", "ZAF": "ZA", "SWE": "SE", "DEN": "DK", "AUT": "AT", "SUI": "CH",
    "POL": "PL", "ROU": "RO", "SWI": "CH", "AND": "AD", "BOL": "BO", "COL": "CO", "PER": "PE", "IRL": "IE",
    "CAN": "CA", "JPN": "JP", "CHN": "CN", "TUR": "TR", "GRE": "GR", "NOR": "NO", "FIN": "FI",
}
_COUNTRY_NAMES = {
    "españa": "ES", "spain": "ES", "argentina": "AR", "portugal": "PT", "brasil": "BR", "brazil": "BR",
    "italia": "IT", "italy": "IT", "francia": "FR", "france": "FR", "méxico": "MX", "mexico": "MX",
    "chile": "CL", "paraguay": "PY", "uruguay": "UY", "reino unido": "GB", "united kingdom": "GB",
    "países bajos": "NL", "netherlands": "NL", "alemania": "DE", "germany": "DE", "bélgica": "BE",
    "belgium": "BE", "egipto": "EG", "egypt": "EG", "arabia saudí": "SA", "saudi arabia": "SA",
    "catar": "QA", "qatar": "QA", "kuwait": "KW", "estados unidos": "US", "united states": "US",
    "sudáfrica": "ZA", "south africa": "ZA", "suecia": "SE", "sweden": "SE",
    "emiratos árabes unidos": "AE", "united arab emirates": "AE", "gran bretaña": "GB", "great britain": "GB",
    "inglaterra": "GB", "england": "GB", "escocia": "GB", "scotland": "GB", "gales": "GB", "wales": "GB",
    "colombia": "CO", "perú": "PE", "peru": "PE", "bolivia": "BO", "canadá": "CA", "canada": "CA",
    "japón": "JP", "japan": "JP", "china": "CN", "turquía": "TR", "turkey": "TR", "grecia": "GR", "greece": "GR",
    "noruega": "NO", "norway": "NO", "finlandia": "FI", "finland": "FI", "dinamarca": "DK", "denmark": "DK",
    "austria": "AT", "suiza": "CH", "switzerland": "CH", "polonia": "PL", "poland": "PL", "rumanía": "RO",
    "romania": "RO", "andorra": "AD", "irlanda": "IE", "ireland": "IE",
}
_CODE_RE = r"(?:%s)" % "|".join(sorted(_IOC_TO_ISO2, key=len, reverse=True))
_NAME_RE = r"(?:%s)" % "|".join(sorted((re.escape(n) for n in _COUNTRY_NAMES), key=len, reverse=True))
# un código ("ESP") o un nombre de país ("España") delante de otra palabra
_COUNTRY_TOKEN = re.compile(rf"^(?:(?P<code>{_CODE_RE})|(?P<name>{_NAME_RE}))\s+(?=\S)", re.IGNORECASE)
_SPLIT_BEFORE_COUNTRY = re.compile(rf"(?<=\S)\s+(?=(?:{_CODE_RE})\s+[A-ZÁÉÍÓÚÑ])")


def flag_emoji(iso2: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper()) if len(iso2) == 2 else ""


def split_country(text: str) -> tuple[str, str]:
    """'ESP Ariana Sánchez' -> ('ES', 'Ariana Sánchez'); sin país -> ('', texto)."""
    text = clean_text(text).replace("\n", " ")
    m = _COUNTRY_TOKEN.match(text)
    if not m:
        return "", text
    iso = _IOC_TO_ISO2.get((m.group("code") or "").upper()) or _COUNTRY_NAMES.get((m.group("name") or "").lower(), "")
    return iso, text[m.end():].strip()


def split_players(cell: str) -> list[tuple[str, str]]:
    """Celda con una o dos jugadoras -> [(iso2, nombre), ...]; descarta las que no pasan el filtro de seguridad."""
    cell = clean_text(cell)
    parts = [p for p in cell.split("\n") if p.strip()]
    if len(parts) < 2:                       # sin saltos de línea: se separa antes de cada código de país
        parts = _SPLIT_BEFORE_COUNTRY.split(cell.replace("\n", " "))
    players = []
    for part in parts:
        iso, name = split_country(part)
        name = safe_name(name)
        if name:
            players.append((iso, name))
    return players


# ── tablas HTML (solo biblioteca estándar) ───────────────────────────────────

class _Cell:
    __slots__ = ("text", "rowspan", "colspan", "header")

    def __init__(self, rowspan: int, colspan: int, header: bool):
        self.text, self.rowspan, self.colspan, self.header = "", rowspan, colspan, header


class _TableParser(HTMLParser):
    """Extrae las tablas (sin anidar) con sus celdas, rowspan y colspan. Ignora referencias [1]."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[_Cell]]] = []
        self._depth = 0
        self._row: Optional[list[_Cell]] = None
        self._cell: Optional[_Cell] = None
        self._skip = 0
        self._flag = 0

    @staticmethod
    def _span(attrs, key) -> int:
        try:
            return max(1, min(int(dict(attrs).get(key) or 1), 20))
        except ValueError:
            return 1

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif self._depth == 1:
            if tag == "tr":
                self._row = []
                self.tables[-1].append(self._row)
            elif tag in ("td", "th") and self._row is not None:
                self._cell = _Cell(self._span(attrs, "rowspan"), self._span(attrs, "colspan"), tag == "th")
                self._row.append(self._cell)
            elif tag in ("sup", "style", "script"):
                self._skip += 1
            elif tag == "br" and self._cell is not None and not self._skip:
                self._cell.text += "\n"
            elif tag == "span" and "flagicon" in (a.get("class") or ""):
                self._flag += 1
            elif tag == "img" and self._flag and self._cell is not None and not self._skip:
                alt = (a.get("alt") or "").strip()
                if alt:
                    self._cell.text += alt + " "

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth = max(0, self._depth - 1)
        elif self._depth == 1:
            if tag in ("td", "th"):
                self._cell = None
            elif tag == "tr":
                self._row = None
            elif tag in ("sup", "style", "script") and self._skip:
                self._skip -= 1
            elif tag == "span" and self._flag:
                self._flag -= 1

    def handle_data(self, data):
        if self._depth == 1 and self._cell is not None and not self._skip:
            self._cell.text += data


def parse_tables(html: str) -> list[list[list[_Cell]]]:
    parser = _TableParser()
    parser.feed(html or "")
    parser.close()
    return parser.tables


def expand(rows: list[list[_Cell]]) -> list[list[str]]:
    """Convierte las filas con rowspan/colspan en una cuadrícula rectangular de textos."""
    grid, pending = [], {}
    for row in rows:
        out, col, queue, qi = [], 0, list(row), 0
        while True:
            if col in pending:
                text, remaining = pending[col]
                out.append(text)
                if remaining <= 1:
                    del pending[col]
                else:
                    pending[col][1] = remaining - 1
                col += 1
                continue
            if qi >= len(queue):
                if any(c > col for c in pending):
                    out.append("")
                    col += 1
                    continue
                break
            cell = queue[qi]
            qi += 1
            text = clean_text(cell.text)
            for _ in range(cell.colspan):
                out.append(text)
                if cell.rowspan > 1:
                    pending[col] = [text, cell.rowspan - 1]
                col += 1
        grid.append(out)
    return grid


# ── fechas ───────────────────────────────────────────────────────────────────

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"])}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})


def _month(name: str) -> Optional[int]:
    return _MONTHS.get(name.strip().lower().rstrip("."))


def parse_date_range(text: str, year: int) -> Optional[tuple[date, date]]:
    """'7 – 14 February' / '31 August – 6 September' -> (inicio, fin); None si no se entiende."""
    t = clean_text(text).replace("\n", " ")
    try:
        m = re.match(r"^(\d{1,2})\s*[–—-]\s*(\d{1,2})\s+([A-Za-z]+)$", t)
        if m and _month(m.group(3)):
            mo = _month(m.group(3))
            start, end = date(year, mo, int(m.group(1))), date(year, mo, int(m.group(2)))
            return (start, end) if end >= start else None
        m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s*[–—-]\s*(\d{1,2})\s+([A-Za-z]+)$", t)
        if m and _month(m.group(2)) and _month(m.group(4)):
            start = date(year, _month(m.group(2)), int(m.group(1)))
            end = date(year, _month(m.group(4)), int(m.group(3)))
            return (start, end) if end >= start else None
    except ValueError:
        return None
    return None


def source_date(html: str) -> Optional[date]:
    """La fecha que la propia página dice tener ('Updated on 22 August 2026')."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    m = re.search(r"Updated on\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if m and _month(m.group(2)):
        try:
            return date(int(m.group(3)), _month(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


# ── ranking ──────────────────────────────────────────────────────────────────

def parse_women_ranking(html: str) -> list[dict]:
    """Filas {'pos','name','flag','pts'} del ranking femenino; vacío si no se encuentra la tabla."""
    for table in parse_tables(html):
        grid = expand(table)
        head = " ".join(" ".join(r) for r in grid[:2]).lower()
        if "women" not in head or "ranking" not in head:
            continue
        header_idx = next((i for i, r in enumerate(grid[:4])
                           if any(c.lower() == "name" for c in r) and any(c.lower() == "points" for c in r)), None)
        if header_idx is None:
            continue
        header = [c.lower() for c in grid[header_idx]]
        i_pos = next((i for i, c in enumerate(header) if c.startswith("n.") or c in ("#", "no.", "rank", "pos")), 0)
        i_name, i_pts = header.index("name"), header.index("points")

        rows = []
        for r in grid[header_idx + 1:]:
            if len(r) <= max(i_name, i_pts, i_pos):
                continue
            pos_digits, pts_digits = re.sub(r"\D", "", r[i_pos]), re.sub(r"\D", "", r[i_pts])
            iso, name = split_country(r[i_name])
            name = safe_name(name)
            if not (pos_digits and pts_digits and name):
                continue
            rows.append({"pos": int(pos_digits), "name": name, "flag": flag_emoji(iso),
                         "pts": f"{int(pts_digits):,}".replace(",", ".")})
        if rows:
            return rows
    return []


# ── calendario y finales ─────────────────────────────────────────────────────

def _badge(name: str) -> tuple[str, str]:
    low = name.lower()
    if "finals" in low:
        return "major", "FINALS"
    if "major" in low:
        return "major", "MAJOR"
    if re.search(r"\bp1\b", low):
        return "p1", "P1"
    if re.search(r"\bp2\b", low):
        return "p2", "P2"
    return "fip", "FIP"


def parse_tournaments(html: str, year: int) -> list[dict]:
    """Torneos con fechas y, si ya se jugó, la final femenina."""
    for table in parse_tables(html):
        grid = expand(table)
        head = " ".join(" ".join(r) for r in grid[:3]).lower()
        if not ("tournament" in head and "date" in head and "women" in head):
            continue
        out = []
        for r in grid:
            if len(r) < 2 or r[0].lower() in ("tournament", ""):
                continue
            iso, name = split_country(r[0])
            name = safe_name(name)
            if not name:
                continue
            item = {"name": name, "place": "", "badge": "", "badgeText": "", "tv": ""}
            item["badge"], item["badgeText"] = _badge(name)
            city = re.sub(r"\b(P1|P2|Major|Premier Padel Finals|Finals)\b", "", name, flags=re.I).strip() or name
            item["place"] = f"{city} {flag_emoji(iso)}".strip()

            if "cancel" in r[1].lower() or any("cancel" in c.lower() for c in r[1:3]):
                item["cancelado"] = True
                dates = None
            else:
                dates = parse_date_range(r[1], year)
            if dates:
                item["start"], item["end"] = dates[0].isoformat(), dates[1].isoformat()
            elif not item.get("cancelado"):
                continue

            filled = [c for c in r[2:] if c.strip()]
            if len(filled) >= 6:
                winners, score, runners = filled[3:6]
                w, ru, sc = split_players(winners), split_players(runners), safe_score(score)
                if len(w) == 2 and len(ru) == 2 and sc:
                    item["final_femenina"] = {
                        "ganadoras": " / ".join(n for _, n in w),
                        "finalistas": " / ".join(n for _, n in ru),
                        "resultado": sc,
                    }
            out.append(item)
        if out:
            return out
    return []


# ── descarga ─────────────────────────────────────────────────────────────────

async def fetch_page(year: int, client_factory: Optional[Callable[[], httpx.AsyncClient]] = None) -> dict:
    """Pide la página a la API de MediaWiki. Devuelve {'html','revid','title','url'} o lanza SourceError."""
    factory = client_factory or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(20.0)))
    params = {
        "action": "parse", "page": PAGE_TITLE.format(year=year), "prop": "text|revid", "format": "json",
        "formatversion": "2", "redirects": "1", "maxlag": "5",
    }
    try:
        async with factory() as client:
            resp = await client.get(API_URL, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"no se pudo contactar con Wikipedia ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise SourceError(f"Wikipedia respondió HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise SourceError("respuesta de Wikipedia que no es JSON") from exc
    if "error" in data:
        raise SourceError(f"Wikipedia devolvió el error '{(data['error'] or {}).get('code', '?')}'")
    parsed = data.get("parse") or {}
    html = parsed.get("text")
    if not isinstance(html, str) or len(html) < 500:
        raise SourceError("la página llegó vacía o demasiado corta")
    return {"html": html, "revid": parsed.get("revid"), "title": parsed.get("title") or "",
            "url": PAGE_URL.format(year=year)}
