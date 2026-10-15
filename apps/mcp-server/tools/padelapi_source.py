"""
padelapi_source.py — Datos reales de Premier Padel desde Padel API (padelapi.org).

Padel API es un servicio comercial con una API de datos de pádel profesional y un plan gratuito
(ranking actual, calendario y resultados de los últimos 6 meses). Su licencia, en cualquier plan,
permite usar los datos en tu propia aplicación y guardar las respuestas en tus sistemas; NO permite
revenderlos como producto, replicar su base de datos ni extraerla en masa. Por eso aquí:

  - se piden pocos datos y se guardan en data/ (la web y el MCP leen de esos archivos; la API no se
    consulta con cada pregunta de un usuario);
  - un torneo ya completado no se vuelve a pedir;
  - el ritmo es de una petición cada ~6,5 s (el plan gratuito admite 10 por minuto).

Todo error (token malo, límite, red, respuesta rara) se convierte en un SourceError con un mensaje
claro: la tarea de actualización conserva entonces los datos anteriores.

Variables de entorno:
  PADELAPI_TOKEN         el token (¡solo en el servidor, nunca en el repositorio!)
  PADELAPI_INGEST        "off" la desactiva aunque haya token
  PADELAPI_MIN_INTERVAL  segundos entre peticiones (por defecto 6.5)
"""

import asyncio
import os
import re
from datetime import date, timedelta
from typing import Callable, Optional

import httpx

from tools.http_policy import USER_AGENT

BASE = "https://padelapi.org/api"
SITE = "https://padelapi.org"
LEVELS = "major,p1,p2,finals"
LEVEL_TEXT = {"major": "Major", "p1": "P1", "p2": "P2", "finals": "Finals"}
HIDDEN = "hidden_free_plan"           # lo que devuelve el plan gratuito para datos fuera de su alcance
RANKING_ROWS = 20
MEN_ROWS = 50                         # solo sirve para que las noticias no mezclen a jugadores; no se muestra
FREE_WINDOW_DAYS = 175                # el plan gratuito solo enseña resultados de los últimos 6 meses
MAX_FINAL_FETCHES = 12                # torneos nuevos por pasada (cada uno cuesta 2 peticiones)
MAX_PAGES = 4
DEFAULT_INTERVAL = 6.5


class SourceError(Exception):
    """No se pudo obtener el dato (con el motivo, en español)."""


class FatalSourceError(SourceError):
    """Fallo que afecta a todo (token, límite, red): no tiene sentido seguir pidiendo."""


def token() -> str:
    return os.environ.get("PADELAPI_TOKEN", "").strip()


def enabled() -> bool:
    return bool(token()) and os.environ.get("PADELAPI_INGEST", "on").strip().lower() not in ("off", "0", "false", "no")


def _interval() -> float:
    try:
        return max(0.0, float(os.environ.get("PADELAPI_MIN_INTERVAL", DEFAULT_INTERVAL)))
    except ValueError:
        return DEFAULT_INTERVAL


# ── cliente con ritmo, reintento de 429 y errores claros ─────────────────────

class _Api:
    def __init__(self, client: httpx.AsyncClient, sleep: Callable, interval: float):
        self.client, self.sleep, self.interval, self.calls = client, sleep, interval, 0

    async def get(self, path: str, params: Optional[dict] = None):
        resp = None
        for attempt in (1, 2):
            if self.calls:
                await self.sleep(self.interval)
            self.calls += 1
            try:
                resp = await self.client.get(BASE + path, params=params, headers={
                    "Authorization": f"Bearer {token()}", "Accept": "application/json", "User-Agent": USER_AGENT})
            except httpx.HTTPError as exc:
                raise FatalSourceError(f"error de red al llamar a Padel API ({type(exc).__name__})") from exc
            if resp.status_code == 429 and attempt == 1:
                try:
                    wait = min(60.0, max(1.0, float(resp.headers.get("Retry-After", "30"))))
                except ValueError:
                    wait = 30.0
                await self.sleep(wait)
                continue
            break
        code = resp.status_code
        if code == 401:
            raise FatalSourceError("Padel API rechazó el token (401): revisa PADELAPI_TOKEN")
        if code == 429:
            raise FatalSourceError("Padel API: se superó el límite de peticiones (429)")
        if code == 402:
            raise SourceError("Padel API pide un plan de pago para ese dato (402)")
        if code >= 400:
            raise SourceError(f"Padel API respondió {code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SourceError("Padel API devolvió algo que no es JSON") from exc
        if not isinstance(body, dict):
            raise SourceError("Padel API devolvió una respuesta inesperada")
        return body

    async def pages(self, path: str, params: dict) -> list:
        items, page = [], 1
        while page <= MAX_PAGES:
            body = await self.get(path, {**params, "page": page, "per_page": 50})
            data = body.get("data")
            if not isinstance(data, list):
                raise SourceError("Padel API devolvió una lista con una forma inesperada")
            items.extend(x for x in data if isinstance(x, dict))
            if not (body.get("links") or {}).get("next"):
                break
            page += 1
        return items


# ── conversiones puras ───────────────────────────────────────────────────────

def flag(code) -> str:
    code = str(code or "").strip().upper()
    if len(code) != 2 or not code.isalpha() or not code.isascii():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code)


def format_points(value) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return f"{value:,}".replace(",", ".")


def clean_name(value) -> str:
    text = re.sub(r"[<>]", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:80]


_PARTICLES = {"de", "del", "la", "las", "los", "da", "do", "dos", "y"}
_COMPOUND_SECOND = {"maria", "jose", "ana", "juan", "luis", "carlos", "angel", "carmen", "isabel", "pilar", "belen",
                    "teresa", "miguel", "manuel", "antonio", "francisco", "javier", "pablo", "pedro"}


def short_name(full: str) -> str:
    """
    Padel API da los nombres con los dos apellidos ('Gemma Triay Pons'); la prensa y la gente usan el nombre y el
    primer apellido ('Gemma Triay'). Con nombres compuestos ('Maria Jose Martinez Lopez') se conserva el compuesto.
    """
    words = clean_name(full).split()
    if len(words) <= 2:
        return " ".join(words)
    keep = 2 if words[1].lower() in _COMPOUND_SECOND and len(words) >= 3 else 1
    i = keep
    surname = [words[i]]
    while words[i].lower() in _PARTICLES and i < len(words) - 1:     # 'De la Fuente'
        i += 1
        surname.append(words[i])
    return " ".join(words[:keep] + surname)


def parse_ranking(data: list, limit: int = RANKING_ROWS) -> list:
    rows = []
    for r in data:
        if r.get("category") != "women" or r.get("type", "official") != "official":
            continue
        pos, pts = r.get("ranking"), format_points(r.get("points"))
        name = short_name(r.get("name"))
        if not name or pts is None or isinstance(pos, bool) or not isinstance(pos, int) or pos < 1:
            continue                      # incluye los valores 'hidden_free_plan'
        rows.append({"pos": pos, "name": name, "pair": "", "flag": flag(r.get("nationality")), "pts": pts})
    rows.sort(key=lambda x: x["pos"])
    return rows[:limit]


def parse_men_ranking(data: list, limit: int = MEN_ROWS) -> list:
    """Nombres de los jugadores del ranking masculino oficial (para que las noticias no los mezclen)."""
    rows = []
    for r in data:
        if r.get("category") != "men" or r.get("type", "official") != "official":
            continue
        pos, name = r.get("ranking"), short_name(r.get("name"))
        if not name or isinstance(pos, bool) or not isinstance(pos, int) or pos < 1:
            continue
        rows.append({"pos": pos, "name": name})
    rows.sort(key=lambda x: x["pos"])
    return rows[:limit]


def snapshot_date(data: list) -> Optional[str]:
    dates = sorted(str(r.get("date"))[:10] for r in data if re.match(r"^\d{4}-\d{2}-\d{2}", str(r.get("date") or "")))
    return dates[-1] if dates else None


def _iso(value) -> Optional[str]:
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return None


def parse_tournament(t: dict) -> Optional[dict]:
    level = str(t.get("level") or "")
    start, end = _iso(t.get("start_date")), _iso(t.get("end_date"))
    name = clean_name(t.get("name"))
    if level not in LEVEL_TEXT or not (start and end and name) or t.get("format") == "teams":
        return None
    place = f"{clean_name(t.get('location'))} {flag(t.get('country'))}".strip()
    out = {"name": name, "place": place, "start": start, "end": end, "badge": level, "badgeText": LEVEL_TEXT[level],
           "tv": "", "padelapi_id": t.get("id") if isinstance(t.get("id"), int) else None}
    if t.get("status") in ("cancelled", "postponed"):
        out["cancelado"] = True          # aplazado = sus fechas ya no valen: no se muestra como próximo
    return out


def _names(team) -> list:
    if not isinstance(team, list):
        return []
    return [n for n in (clean_name(p.get("name")) for p in team if isinstance(p, dict)) if n]


def format_score(score, winner) -> str:
    """Una lista de sets {'team_1','team_2'} -> '6-3 7-5', siempre con el marcador de la ganadora primero."""
    if score in (None, "", HIDDEN):
        return ""
    if isinstance(score, str):
        return re.sub(r"[^0-9()\- ]", "", score).strip()[:40]
    if not isinstance(score, list):
        return ""
    sets = []
    for s in score:
        if not isinstance(s, dict):
            continue
        a, b = (re.sub(r"[^0-9()]", "", str(s.get(k, ""))) for k in ("team_1", "team_2"))
        if a and b:
            sets.append(f"{b}-{a}" if winner == "team_2" else f"{a}-{b}")
    return " ".join(sets)


def _decided(m: dict) -> Optional[tuple]:
    """(ganadora, perdedora, resultado) si el partido tiene ganador claro y las dos parejas con nombre."""
    winner = m.get("winner")
    teams = m.get("players") if isinstance(m.get("players"), dict) else {}
    if winner not in ("team_1", "team_2"):
        return None
    loser = "team_2" if winner == "team_1" else "team_1"
    w, l = _names(teams.get(winner)), _names(teams.get(loser))
    if not w or not l:
        return None
    result = format_score(m.get("score"), winner)
    status = m.get("status")
    if status == "retired":
        result = (result + " (retirada)").strip()
    elif status == "walkover":
        result = "w.o."
    return " / ".join(w), " / ".join(l), result


def parse_final(final_matches: list, semi_matches: list) -> Optional[dict]:
    """final_femenina (ganadoras, finalistas, resultado, semifinalistas) a partir de los partidos de la API."""
    final = next((d for d in (_decided(m) for m in final_matches if m.get("round") == 1)
                  if d is not None), None)
    if not final:
        return None
    semis = []
    for m in semi_matches:
        if m.get("round") != 2:
            continue
        d = _decided(m)
        if d and d[1] not in semis:
            semis.append(d[1])
    return {"ganadoras": final[0], "finalistas": final[1], "resultado": final[2], "semifinalistas": semis[:4]}


# ── la pasada completa ───────────────────────────────────────────────────────

async def fetch_all(today: date, previous_tournaments: Optional[list] = None,
                    client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
                    sleep: Callable = asyncio.sleep, interval: Optional[float] = None) -> dict:
    """
    Pide el ranking femenino y el calendario de la temporada y, solo de los torneos terminados que aún
    no se conocen, la final y las semifinales femeninas. Devuelve ranking, torneos, fecha de la
    instantánea del ranking, nº de peticiones y avisos.
    """
    if not token():
        raise FatalSourceError("falta PADELAPI_TOKEN")
    interval = _interval() if interval is None else interval
    known = {t.get("padelapi_id"): t["final_femenina"] for t in (previous_tournaments or [])
             if isinstance(t, dict) and t.get("padelapi_id") is not None and isinstance(t.get("final_femenina"), dict)}
    notes = []
    factory = client_factory or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(20.0)))
    async with factory() as client:
        api = _Api(client, sleep, interval)

        body = await api.get("/rankings", {"type": "official", "category": "women", "sort_by": "ranking",
                                           "order_by": "asc", "per_page": RANKING_ROWS})
        raw_ranking = [x for x in (body.get("data") or []) if isinstance(x, dict)]
        ranking = parse_ranking(raw_ranking)

        men = []
        try:
            men_body = await api.get("/rankings", {"type": "official", "category": "men", "sort_by": "ranking",
                                                   "order_by": "asc", "per_page": MEN_ROWS})
            men = parse_men_ranking([x for x in (men_body.get("data") or []) if isinstance(x, dict)])
        except FatalSourceError:
            raise
        except SourceError as exc:
            notes.append(f"ranking masculino: {exc}")

        raw_tournaments = await api.pages("/tournaments", {
            "level": LEVELS, "after_date": f"{today.year - 1}-12-31", "before_date": f"{today.year + 1}-01-01",
            "sort_by": "start_date", "order_by": "asc"})
        tournaments, ids = [], {}
        for raw in raw_tournaments:
            t = parse_tournament(raw)
            if t:
                tournaments.append(t)
                ids[t["name"]] = (raw.get("status"), t)

        cutoff = today - timedelta(days=FREE_WINDOW_DAYS)
        todo = []
        for t in tournaments:
            tid = t.get("padelapi_id")
            if tid in known:
                t["final_femenina"] = known[tid]
            elif ids[t["name"]][0] == "finished" and tid is not None and date.fromisoformat(t["end"]) >= cutoff:
                todo.append(t)
        todo.sort(key=lambda t: t["end"], reverse=True)
        for t in todo[:MAX_FINAL_FETCHES]:
            try:
                base = {"category": "women", "per_page": 10}
                finals = (await api.get(f"/tournaments/{t['padelapi_id']}/matches", {**base, "round": 1})).get("data") or []
                semis = (await api.get(f"/tournaments/{t['padelapi_id']}/matches", {**base, "round": 2})).get("data") or []
            except FatalSourceError:
                raise
            except SourceError as exc:
                notes.append(f"{t['name']}: {exc}")
                continue
            final = parse_final([m for m in finals if isinstance(m, dict)], [m for m in semis if isinstance(m, dict)])
            if final:
                t["final_femenina"] = final
            else:
                notes.append(f"{t['name']}: la final femenina aún no tiene ganadora clara en la fuente")
        if len(todo) > MAX_FINAL_FETCHES:
            notes.append(f"{len(todo) - MAX_FINAL_FETCHES} torneos se completarán en la próxima pasada")
        return {"ranking": ranking, "men": men, "tournaments": tournaments, "snapshot": snapshot_date(raw_ranking),
                "calls": api.calls, "notes": notes}
