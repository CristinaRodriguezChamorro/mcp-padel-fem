"""
local_data.py — Capa de datos SIN scraping.

Sustituye a la antigua `live_data.py`. Ranking, calendario y torneo en juego se leen de
archivos JSON de la carpeta `data/` (o de la ruta indicada en la variable DATA_DIR), con la
misma forma de salida que ya esperan la web (/api/*) y el MCP.

Cada archivo lleva un bloque `_meta`:
    {"tipo": "ejemplo" | "manual" | "api",   # de dónde salen los datos
     "actualizado": "2026-09-29" | null,      # fecha de la última verificación
     "fuente": "texto libre", "nota": "texto libre"}

`tipo: "ejemplo"` marca datos sin verificar: la web muestra un aviso y las tools del MCP lo
indican en cada respuesta. Para pasar a datos reales, edita los archivos (o conecta una API
con licencia que escriba estos mismos archivos) y cambia `_meta.tipo` a "manual" o "api".

Quién escribe esos archivos: la tarea de tools/ingest.py (Wikipedia, una vez al día) o tú a mano.

Este módulo NO hace ninguna petición de red.
"""

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

TOP_N = 20
CALENDAR_MAX = 10
STALE_DAYS = 14                      # a partir de aquí los datos se consideran con retraso
# Los marcadores en directo NO se reproducen: se enlaza a la web oficial.
OFFICIAL_LIVE_URL = "https://www.padelfip.com/calendar-premier-padel/"
DATASETS = ("ranking", "calendario", "torneo")

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data"
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class DataError(Exception):
    """El archivo de datos falta o no es válido."""


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or _DEFAULT_DIR)


def _load(name: str) -> dict:
    path = _data_dir() / f"{name}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataError(f"falta {path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError(f"{path.name} no es un JSON válido") from exc
    if not isinstance(doc, dict):
        raise DataError(f"{path.name} debe ser un objeto JSON")
    return doc


def _meta(doc: dict) -> dict:
    meta = doc.get("_meta")
    return meta if isinstance(meta, dict) else {}


def _parse_date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def dataset_status(today: Optional[date] = None) -> dict:
    """Estado de cada conjunto de datos: tipo, fecha de actualización y antigüedad en días."""
    today = today or date.today()
    out = {}
    for name in DATASETS:
        try:
            meta = _meta(_load(name))
            updated = _parse_date(meta.get("actualizado"))
            age = (today - updated).days if updated else None
            out[name] = {
                "disponible": True,
                "tipo": meta.get("tipo") or "manual",
                "actualizado": updated.isoformat() if updated else None,
                "antiguedad_dias": age,
                "desactualizado": bool(age is not None and age > STALE_DAYS),
                "consultado": meta.get("consultado"),
                "fuente": meta.get("fuente"),
                "url": meta.get("url"),
                "licencia": meta.get("licencia"),
                "licencia_url": meta.get("licencia_url"),
                "nota": meta.get("nota"),
            }
        except DataError as exc:
            out[name] = {"disponible": False, "tipo": None, "actualizado": None,
                         "antiguedad_dias": None, "desactualizado": False, "consultado": None,
                         "fuente": None, "url": None, "licencia": None, "licencia_url": None, "nota": str(exc)}
    return out


def data_age_seconds(name: str, today: Optional[date] = None) -> Optional[int]:
    """Segundos desde la última verificación declarada en `_meta.actualizado` (None si no consta)."""
    updated = _parse_date(dataset_status(today).get(name, {}).get("actualizado"))
    if not updated:
        return None
    return int((datetime.now() - datetime.combine(updated, datetime.min.time())).total_seconds())


# ── ranking ──────────────────────────────────────────────────────────────────

def _ranking_rows(limit: int = TOP_N) -> list:
    doc = _load("ranking")
    rows = []
    for raw in doc.get("jugadoras") or []:
        if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
            continue
        try:
            pos = int(raw.get("pos"))
        except (TypeError, ValueError):
            pos = len(rows) + 1
        rows.append({
            "pos": pos,
            "name": str(raw["name"]).strip(),
            "pair": str(raw.get("pair") or ""),
            "flag": str(raw.get("flag") or ""),
            "pts": str(raw.get("pts") or ""),
            "photo": "",   # las fotos se resuelven aparte (/api/player-photo-image)
        })
    rows.sort(key=lambda r: r["pos"])
    return rows[:limit]


async def get_ranking_live() -> list:
    """Top 20 del ranking femenino (mismo formato que devolvía el antiguo scraper)."""
    try:
        return _ranking_rows(TOP_N)
    except DataError as exc:
        print(f"  ranking: {exc}")
        return []


async def get_womens_ranking_names(limit: int = 200) -> list[str]:
    try:
        return [r["name"] for r in _ranking_rows(min(limit, TOP_N))]
    except DataError as exc:
        print(f"  ranking names: {exc}")
        return []


# ── calendario ───────────────────────────────────────────────────────────────

def _calendar_item(raw: dict, today: date) -> Optional[dict]:
    start, end = _parse_date(raw.get("start")), _parse_date(raw.get("end"))
    name = str(raw.get("name", "")).strip()
    if not (start and end and name) or end < start or raw.get("cancelado"):
        return None
    return {
        "day": f"{start.day}-{end.day}",
        "month": _MONTH_ABBR[start.month - 1],
        "name": name,
        "place": str(raw.get("place") or ""),
        "badge": str(raw.get("badge") or "fip"),
        "badgeText": str(raw.get("badgeText") or ""),
        "tv": str(raw.get("tv") or ""),
        "live": start <= today <= end,
        "inicio": start.isoformat(),
        "fin": end.isoformat(),
        "_start": start,
        "_end": end,
    }


async def get_calendar_live(today: Optional[date] = None) -> list:
    """Torneos en curso y futuros (los ya terminados no se muestran), el actual primero."""
    today = today or date.today()
    try:
        doc = _load("calendario")
    except DataError as exc:
        print(f"  calendario: {exc}")
        return []
    items = [i for i in (_calendar_item(t, today) for t in doc.get("torneos") or [] if isinstance(t, dict)) if i]
    items = [i for i in items if i["_end"] >= today]
    items.sort(key=lambda i: (0 if i["live"] else 1, i["_start"]))
    for i in items:
        del i["_start"], i["_end"]
    return items[:CALENDAR_MAX]


# ── torneo en juego ──────────────────────────────────────────────────────────

def _inactive(gender: str, message: str, error: bool = False) -> dict:
    return {
        "active": False,
        "message": message,
        "name": "", "place": "", "dates": "",
        "watch": [], "results": [],
        "next_match": None, "current_match": None,
        "updated": date.today().isoformat(),
        "gender": gender,
        "source": "Datos locales",
        "source_url": "",
        "official_url": OFFICIAL_LIVE_URL,
        **({"error": True} if error else {}),
    }


def _fmt_range(start: date, end: date) -> str:
    return f"{start.day} {_MONTH_ABBR[start.month - 1]} – {end.day} {_MONTH_ABBR[end.month - 1]}"


async def get_tournament_now(gender: str = "female", today: Optional[date] = None) -> dict:
    """
    Torneo en curso. Sin marcadores en directo: la FIP no permite reproducirlos, así que se informa
    del torneo (sacado del calendario) y se enlaza a su marcador oficial. Si en data/torneo.json hay
    un torneo marcado a mano ("active": true), ese tiene prioridad.
    """
    today = today or date.today()
    if gender != "female":
        return _inactive(gender, "Solo hay datos del circuito femenino.")
    try:
        doc = _load("torneo")
    except DataError as exc:
        print(f"  torneo: {exc}")
        doc = {}

    if doc.get("active"):
        meta = _meta(doc)
        return {
            "active": True,
            "name": str(doc.get("name") or ""),
            "place": str(doc.get("place") or ""),
            "dates": str(doc.get("dates") or ""),
            "watch": [str(w) for w in doc.get("watch") or []],
            "results": [r for r in doc.get("results") or [] if isinstance(r, dict)],
            "next_match": doc.get("next_match") if isinstance(doc.get("next_match"), dict) else None,
            "current_match": doc.get("current_match") if isinstance(doc.get("current_match"), dict) else None,
            "updated": meta.get("actualizado") or today.isoformat(),
            "gender": gender,
            "source": str(doc.get("source") or meta.get("fuente") or "Datos locales"),
            "source_url": str(doc.get("source_url") or ""),
            "official_url": OFFICIAL_LIVE_URL,
            "structure": doc.get("structure") if isinstance(doc.get("structure"), dict) else {},
        }

    try:
        cal_doc = _load("calendario")
    except DataError as exc:
        return _inactive(gender, "No hay torneos en juego en este momento." if doc else f"No se pudo leer el calendario ({exc}).",
                         error=not doc)
    for raw in cal_doc.get("torneos") or []:
        if not isinstance(raw, dict) or raw.get("cancelado"):
            continue
        start, end = _parse_date(raw.get("start")), _parse_date(raw.get("end"))
        if start and end and start <= today <= end:
            meta = _meta(cal_doc)
            return {
                "active": True,
                "message": "Esta web no muestra marcadores en directo: consulta el marcador oficial.",
                "name": str(raw.get("name") or ""),
                "place": str(raw.get("place") or ""),
                "dates": _fmt_range(start, end),
                "watch": [t.strip() for t in str(raw.get("tv") or "").split("·") if t.strip()],
                "results": [], "next_match": None, "current_match": None,
                "updated": today.isoformat(),
                "gender": gender,
                "source": str(meta.get("fuente") or "Calendario local"),
                "source_url": str(meta.get("url") or ""),
                "official_url": OFFICIAL_LIVE_URL,
                "structure": {},
            }
    return _inactive(gender, "No hay torneos en juego en este momento.")


async def get_recent_finals(limit: int = 30, today: Optional[date] = None) -> list:
    """Finales femeninas ya jugadas (las más recientes primero), con ganadoras, finalistas y resultado."""
    today = today or date.today()
    try:
        doc = _load("calendario")
    except DataError as exc:
        print(f"  finales: {exc}")
        return []
    finals = []
    for raw in doc.get("torneos") or []:
        if not isinstance(raw, dict) or raw.get("cancelado") or not isinstance(raw.get("final_femenina"), dict):
            continue
        end = _parse_date(raw.get("end"))
        start = _parse_date(raw.get("start"))
        f = raw["final_femenina"]
        if not end or end > today or not (f.get("ganadoras") and f.get("finalistas")):
            continue
        finals.append({
            "torneo": str(raw.get("name") or ""), "sede": str(raw.get("place") or ""),
            "categoria": str(raw.get("badgeText") or ""), "fecha_fin": end.isoformat(),
            "fecha_inicio": start.isoformat() if start else None,
            "ganadoras": str(f["ganadoras"]), "finalistas": str(f["finalistas"]),
            "resultado": str(f.get("resultado") or ""),
            # opcional: parejas semifinalistas (las terceras), si alguna fuente las aporta
            "semifinalistas": [str(x).strip() for x in (f.get("semifinalistas") or []) if isinstance(x, str) and x.strip()][:4],
        })
    finals.sort(key=lambda x: x["fecha_fin"], reverse=True)
    return finals[:max(1, min(int(limit), 30))]


# ── Ficha de un torneo ya jugado (lógica pura, sin red) ───────────────────────
# Contexto de cada tipo de torneo: solo lo que dicen las fuentes públicas (Red Bull TV y Wikipedia,
# "Premier Padel 2026"): categorías por orden de importancia y de puntos de ranking. No se dan cifras
# de puntos porque el sistema de puntos de 2026 es nuevo y no se ha podido verificar.
CIRCUIT_CONTEXT = ("Premier Padel es el circuito profesional oficial de pádel, organizado junto a la FIP; "
                   "los puntos de cada torneo cuentan para el ranking FIP.")
TOURNAMENT_TYPES = {
    "major": "Los Major son los torneos de más categoría del circuito: los que más puntos de ranking y más prestigio dan.",
    "p1": "Los P1 son la segunda categoría del circuito: por debajo de los Major, reparten más puntos de ranking que un P2.",
    "p2": "Los P2 son la tercera categoría del circuito: la que menos puntos de ranking reparte de las tres (Major, P1 y P2).",
    "finals": "El Premier Padel Finals es el torneo que cierra la temporada.",
}
TOURNAMENT_CONTEXT_SOURCE = "Red Bull TV y Wikipedia (Premier Padel 2026)"
COUNTRIES = {
    "ES": "España", "GB": "Reino Unido", "IT": "Italia", "FR": "Francia", "MX": "México", "QA": "Catar",
    "AE": "Emiratos Árabes Unidos", "KW": "Kuwait", "SA": "Arabia Saudí", "EG": "Egipto", "BE": "Bélgica",
    "AR": "Argentina", "ZA": "Sudáfrica", "NL": "Países Bajos", "DE": "Alemania", "US": "Estados Unidos",
    "PT": "Portugal", "CL": "Chile", "BR": "Brasil",
}
WATCH_NOTE = ("Premier Padel se emite en Red Bull TV: cuartos, semifinales y finales de cada torneo, también las "
              "femeninas (según el calendario de Red Bull TV de 2026). El enlace al marcador oficial lleva al directo.")
THIRD_PLACE_NOTE = ("En pádel no se juega partido por el tercer puesto: quedan terceras las dos parejas que pierden en "
                    "semifinales. La fuente de datos actual solo recoge ganadoras y finalistas, así que no tengo sus "
                    "nombres: no los inventes; si hace falta, búscalas en las noticias.")


def _fold(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", str(text or ""))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower().strip()


def split_place(place: str) -> dict:
    """'London 🇬🇧' -> ciudad, país (por la bandera) y código. Sin bandera, solo la ciudad."""
    text = str(place or "").strip()
    flags = [c for c in text if 0x1F1E6 <= ord(c) <= 0x1F1FF]
    code = "".join(chr(ord(c) - 0x1F1E6 + 65) for c in flags[:2]) if len(flags) >= 2 else ""
    city = "".join(c for c in text if not (0x1F1E6 <= ord(c) <= 0x1F1FF)).strip()
    return {"ciudad": city or None, "pais": COUNTRIES.get(code, code or None), "codigo_pais": code or None}


def split_pair(text: str) -> list:
    return [n.strip() for n in str(text or "").split("/") if n.strip()]


def tournament_type(category: str) -> dict:
    key = _fold(category)
    return {"categoria": (category or "").strip() or None, "descripcion": TOURNAMENT_TYPES.get(key),
            "circuito": CIRCUIT_CONTEXT, "fuente": TOURNAMENT_CONTEXT_SOURCE}


def find_final(finals: list, query: str) -> Optional[dict]:
    """La final más reciente cuyo torneo o ciudad contiene el texto buscado (sin tildes ni mayúsculas)."""
    q = _fold(query)
    if not q:
        return None
    for f in finals:
        if q in _fold(f.get("torneo")) or q in _fold(split_place(f.get("sede")).get("ciudad") or ""):
            return f
    return None


def _sentence(f: dict, place: dict, result: str) -> str:
    where = ", ".join(x for x in (place.get("ciudad"), place.get("pais")) if x)
    text = f"{f.get('ganadoras')} ganaron el {f.get('torneo')}" + (f" ({where})" if where else "")
    text += f" a {f.get('finalistas')}"
    return text + (f" por {result}." if result else ".")


def describe_final(f: dict) -> dict:
    """Ficha completa de una final femenina: dónde, qué torneo es, quién jugó, podio y resultado."""
    win, run = split_pair(f.get("ganadoras")), split_pair(f.get("finalistas"))
    semis = [x for x in (f.get("semifinalistas") or []) if x]
    result = str(f.get("resultado") or "").strip()
    place = split_place(f.get("sede"))
    return {
        "torneo": f.get("torneo"),
        "resumen_en_una_frase": _sentence(f, place, result),
        "donde": place,
        "fechas": {"inicio": f.get("fecha_inicio"), "fin": f.get("fecha_fin")},
        "tipo_de_torneo": tournament_type(f.get("categoria")),
        "partido": f"Final femenina: {' / '.join(win)} vs {' / '.join(run)}",
        "podio": {
            "primeras": {"pareja": " / ".join(win), "jugadoras": win},
            "segundas": {"pareja": " / ".join(run), "jugadoras": run},
            "terceras": [{"pareja": x, "jugadoras": split_pair(x)} for x in semis] or None,
            "nota_terceras": None if semis else THIRD_PLACE_NOTE,
        },
        "resultado_final": result or None,
        "nota_resultado": None if result else "La fuente no recoge el marcador de esta final.",
    }


def tournament_card(t: dict, today: Optional[date] = None) -> dict:
    """Ficha de un torneo del calendario (próximo o en juego): dónde, cuándo, cuántos días faltan y de qué tipo es."""
    today = today or date.today()
    start = _parse_date(t.get("inicio"))
    return {
        "torneo": t.get("name"),
        "donde": split_place(t.get("place")),
        "fechas": {"inicio": t.get("inicio"), "fin": t.get("fin")},
        "dias_para_empezar": (start - today).days if start else None,
        "en_juego": bool(t.get("live")),
        "tipo_de_torneo": tournament_type(t.get("badgeText")),
        "donde_verlo": t.get("tv") or WATCH_NOTE,
    }
