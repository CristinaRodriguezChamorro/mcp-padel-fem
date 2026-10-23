"""
tools/padelapi_live.py — consultas a Padel API EN EL MOMENTO, para las herramientas extra del MCP
(perfil, partidos, cuadro y ediciones de un torneo, directo, rankings Elo y RACE, movimientos de la semana, parejas y
predicción). Solo usa lo GRATUITO del plan de Padel API: lo de pago (estadísticas, forma, directo punto a punto,
histórico del ranking) no se usa.

A diferencia del resto de la app, que lee archivos que una tarea guarda cada 24 h, estas herramientas piden el
dato cuando alguien lo pregunta. Por eso este módulo se protege y protege a Padel API:

  - caché: el mismo dato no se vuelve a pedir hasta que caduca (minutos u horas, según el dato);
  - una sola petición por dato aunque lleguen varias preguntas a la vez;
  - presupuesto propio (PADELAPI_LIVE_PER_MIN = 5 por minuto y PADELAPI_LIVE_PER_DAY = 600 por día) muy por
    debajo del límite gratuito (10 por minuto y 2.000 por día), para dejar sitio a la actualización diaria;
  - lo que el plan no incluye (402) se recuerda 30 minutos y se explica; no se insiste;
  - solo pádel FEMENINO, y respuestas recortadas a lo necesario (no se devuelve su base de datos en bruto).

No hay nada de la web de la FIP: solo la API con token.
"""
import asyncio
import json
import math
import os
import time
from collections import deque
from datetime import date, datetime
from typing import Callable, Optional

import httpx

from tools import local_data
from tools import padelapi_source as pa
from tools.http_policy import USER_AGENT
from tools.players import find_players, norm

DEFAULT_PER_MINUTE = 5
DEFAULT_PER_DAY = 600
MAX_WAIT = 15.0                 # segundos que se espera por hueco en el presupuesto antes de decir «vuelve a preguntar»
PLAN_TTL = 1800                 # un dato que el plan no incluye se recuerda 30 min
MISSING_TTL = 120
TTL = {"player": 6 * 3600, "search": 6 * 3600, "matches": 15 * 60, "tournament": 120, "rankings": 3600,
       "simulate": 3600, "pairs": 6 * 3600, "editions": 6 * 3600}

HIDDEN = pa.HIDDEN
ROUNDS = {1: "Final", 2: "Semifinales", 4: "Cuartos de final", 8: "Octavos de final", 16: "Dieciseisavos de final",
          32: "Treintaidosavos de final"}
ROUND_WORDS = {"final": 1, "finales": 1, "semifinal": 2, "semifinales": 2, "semis": 2, "cuartos": 4, "cuarto": 4,
               "cuartos de final": 4, "octavos": 8, "octavos de final": 8, "dieciseisavos": 16,
               "dieciseisavos de final": 16}
STATUS_ES = {"scheduled": "programado", "live": "en juego", "ended": "terminado", "finished": "terminado",
             "retired": "terminado por retirada", "walkover": "terminado por incomparecencia", "bye": "exento",
             "not_played": "no jugado"}
FINISHED = ("finished", "ended", "retired", "walkover")
RANKING_TYPES = {"official": "Ranking oficial", "race": "Ranking RACE (carrera del año)", "elo": "Elo de Padel API"}
SIDES = {"drive": "derecha (drive)", "backhand": "izquierda (revés)"}
HANDS = {"right": "diestra", "left": "zurda"}
PLAN_HINT = "Es un dato que el plan gratuito de Padel API no incluye; esta herramienta solo usa lo gratuito."


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "").strip() or default))
    except ValueError:
        return default


def new_http(transport=None) -> httpx.AsyncClient:
    """Padel API responde 302 cuando una jugadora o un torneo se ha fusionado con otro registro: hay que seguirlo."""
    return httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True, transport=transport)


class LiveError(Exception):
    """kind: config | auth | plan | notfound | invalid | limit | budget | network | upstream"""

    def __init__(self, kind: str, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.kind, self.message, self.retry_after = kind, message, retry_after


# ── cliente: caché, una petición por dato y presupuesto ──────────────────────

class Client:
    def __init__(self, http_factory: Optional[Callable[[], httpx.AsyncClient]] = None, sleep: Callable = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic, today: Callable[[], date] = date.today):
        self._factory = http_factory or new_http
        self._sleep, self._clock, self._today = sleep, clock, today
        self._stamps: deque = deque()
        self._day, self._day_count = None, 0
        self._cache: dict = {}
        self._locks: dict = {}
        self.sent = self.from_cache = self.refused = 0

    @staticmethod
    def limits() -> tuple:
        return (_int_env("PADELAPI_LIVE_PER_MIN", DEFAULT_PER_MINUTE), _int_env("PADELAPI_LIVE_PER_DAY", DEFAULT_PER_DAY))

    def status(self) -> dict:
        per_min, per_day = self.limits()
        today = self._today()
        return {"activo": bool(pa.token()), "peticiones_hoy": self._day_count if self._day == today else 0,
                "limite_por_minuto": per_min, "limite_por_dia": per_day, "peticiones_totales": self.sent,
                "respuestas_de_cache": self.from_cache, "rechazadas_por_presupuesto": self.refused}

    async def _acquire(self):
        per_min, per_day = self.limits()
        today = self._today()
        if self._day != today:
            self._day, self._day_count = today, 0
        if self._day_count >= per_day:
            self.refused += 1
            raise LiveError("budget", f"Se alcanzó el límite diario de consultas en directo a Padel API ({per_day}); "
                                      "se reinicia mañana.")
        while True:
            now = self._clock()
            while self._stamps and now - self._stamps[0] >= 60:
                self._stamps.popleft()
            if len(self._stamps) < per_min:
                break
            wait = 60 - (now - self._stamps[0]) + 0.05
            if wait > MAX_WAIT:
                self.refused += 1
                raise LiveError("budget", f"Demasiadas consultas seguidas a Padel API: espera {math.ceil(wait)} s y "
                                          "vuelve a preguntar.", retry_after=wait)
            await self._sleep(wait)
        self._stamps.append(self._clock())
        self._day_count += 1

    def _cache_get(self, key):
        hit = self._cache.get(key)
        if hit and hit[0] > self._clock():
            return hit
        self._cache.pop(key, None)
        return None

    async def request(self, method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None,
                      ttl: float = 600):
        key = (method, path, json.dumps(params or {}, sort_keys=True), json.dumps(json_body or {}, sort_keys=True))

        def served(hit):
            self.from_cache += 1
            if hit[1] == "err":
                raise LiveError(*hit[2])
            return hit[2]

        hit = self._cache_get(key)
        if hit:
            return served(hit)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache_get(key)
            if hit:
                return served(hit)
            if not pa.token():
                raise LiveError("config", "El servidor no tiene PADELAPI_TOKEN: no puede consultar a Padel API.")
            await self._acquire()
            try:
                value = await self._send(method, path, params, json_body)
            except LiveError as exc:
                if exc.kind in ("plan", "notfound"):
                    self._cache[key] = (self._clock() + (PLAN_TTL if exc.kind == "plan" else MISSING_TTL), "err",
                                        (exc.kind, exc.message, exc.retry_after))
                raise
            self._cache[key] = (self._clock() + ttl, "ok", value)
            return value

    async def get(self, path: str, params: Optional[dict] = None, ttl: float = 600):
        return await self.request("GET", path, params=params, ttl=ttl)

    async def post(self, path: str, json_body: dict, ttl: float = 600):
        return await self.request("POST", path, json_body=json_body, ttl=ttl)

    async def _send(self, method, path, params, json_body):
        self.sent += 1
        try:
            async with self._factory() as http:
                resp = await http.request(method, pa.BASE + path, params=params, json=json_body, headers={
                    "Authorization": f"Bearer {pa.token()}", "Accept": "application/json", "User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            raise LiveError("network", f"No pude contactar con Padel API ({type(exc).__name__}).") from exc
        code = resp.status_code
        if code == 401:
            raise LiveError("auth", "Padel API rechazó el token del servidor (401).")
        if code in (402, 403):
            raise LiveError("plan", "Este dato no está incluido en el plan de Padel API que usa el servidor.")
        if code == 404:
            raise LiveError("notfound", "Padel API no tiene ese dato (404).")
        if code == 422:
            raise LiveError("invalid", "Padel API rechazó la consulta (422).")
        if code == 429:
            try:
                wait = min(120.0, max(1.0, float(resp.headers.get("Retry-After", "30"))))
            except ValueError:
                wait = 30.0
            raise LiveError("limit", f"Padel API pide esperar {math.ceil(wait)} s: se superó su límite de peticiones.",
                            retry_after=wait)
        if code >= 500:
            raise LiveError("upstream", f"Padel API no responde bien ({code}).")
        if code >= 400:
            raise LiveError("upstream", f"Padel API respondió {code}.")
        try:
            body = resp.json()
        except ValueError as exc:
            raise LiveError("upstream", "Padel API devolvió algo que no es JSON.") from exc
        if not isinstance(body, (dict, list)):
            raise LiveError("upstream", "Padel API devolvió una respuesta inesperada.")
        return body


# ── conversiones puras (se prueban sin red) ──────────────────────────────────

def _err(exc: LiveError) -> dict:
    out = {"error": exc.message, "tipo": exc.kind}
    if exc.kind == "plan":
        out["pista"] = PLAN_HINT
    if exc.retry_after:
        out["reintentar_en_segundos"] = math.ceil(exc.retry_after)
    return out


def _unwrap(body) -> dict:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def _rows(body) -> list:
    data = body.get("data") if isinstance(body, dict) else body
    return [x for x in (data or []) if isinstance(x, dict)]


def _pair_text(team) -> str:
    return " / ".join(pa.short_name(n) for n in pa._names(team))


def _visible(value):
    return None if value in (HIDDEN, "", None) else value


def _tournament_name(match: dict) -> Optional[str]:
    import re
    found = re.search(r"tournaments/(\d+)", json.dumps(match.get("connections") or ""))
    if not found:
        return None
    tid = int(found.group(1))
    return next((r["name"] for r in local_data.tournament_refs() if r["id"] == tid), None)


def match_view(m: dict, me_id: Optional[int] = None) -> dict:
    players = m.get("players") if isinstance(m.get("players"), dict) else {}
    t1, t2 = players.get("team_1"), players.get("team_2")
    winner, score, status = m.get("winner"), m.get("score"), m.get("status")
    hidden = winner == HIDDEN or score == HIDDEN
    out = {"fecha": m.get("played_at"), "ronda": ROUNDS.get(m.get("round"), m.get("round_name")),
           "pareja_1": _pair_text(t1), "pareja_2": _pair_text(t2), "estado": STATUS_ES.get(status, status)}
    if hidden:
        out["resultado"] = None
        out["nota"] = "Resultado oculto: el plan gratuito de Padel API solo enseña los de los últimos 6 meses."
    else:
        result = pa.format_score(score, winner if winner in ("team_1", "team_2") else None)
        if status == "retired":
            result = (result + " (retirada)").strip()
        elif status == "walkover":
            result = "w.o."
        out["resultado"] = result or None
        if winner in ("team_1", "team_2"):
            out["ganadora"] = _pair_text(players.get(winner))
    if me_id is not None:
        mine = next((k for k, team in (("team_1", t1), ("team_2", t2)) if isinstance(team, list)
                     and any(isinstance(p, dict) and p.get("id") == me_id for p in team)), None)
        if mine:
            other = "team_2" if mine == "team_1" else "team_1"
            out["pareja"], out["rival"] = _pair_text(players.get(mine)), _pair_text(players.get(other))
            out["gano"] = (winner == mine) if winner in ("team_1", "team_2") else None
    if m.get("court"):
        out["pista"] = m.get("court")
    if status == "scheduled" and m.get("schedule_label"):
        out["horario"] = m.get("schedule_label")
    return out


def profile_view(p: dict) -> dict:
    return {"nombre": pa.short_name(p.get("name")), "nombre_completo": pa.clean_name(p.get("name")),
            "pais": f"{pa.flag(p.get('nationality'))} {p.get('nationality') or ''}".strip(),
            "posicion": p.get("ranking"), "puntos": p.get("points"), "elo": p.get("elo"), "altura_cm": p.get("height"),
            "lado": SIDES.get(p.get("side")), "mano": HANDS.get(p.get("hand")), "edad": p.get("age"),
            "fecha_nacimiento": p.get("birthdate"), "lugar_nacimiento": p.get("birthplace"),
            "actualizado": p.get("updated_at")}


def ranking_entry_view(r: dict) -> dict:
    hidden = any(r.get(k) == HIDDEN for k in ("ranking", "points", "ranking_diff", "points_diff"))
    out = {"tipo": RANKING_TYPES.get(r.get("type"), r.get("type")), "semana_del": r.get("date"),
           "posicion": _visible(r.get("ranking")), "puntos": _visible(r.get("points")),
           "cambio_posiciones": _visible(r.get("ranking_diff")), "cambio_puntos": _visible(r.get("points_diff"))}
    if hidden:
        out["nota"] = "Oculto: el plan gratuito de Padel API solo enseña la última semana de cada ranking."
    return out


def _pct(x) -> Optional[str]:
    return f"{round(float(x) * 100)} %" if isinstance(x, (int, float)) and not isinstance(x, bool) else None


# ── servicio: lo que llaman las herramientas ─────────────────────────────────

class Service:
    def __init__(self, client: Optional[Client] = None, today: Callable[[], date] = date.today):
        self.client = client or Client(today=today)
        self.today = today

    def status(self) -> dict:
        return self.client.status()

    def _datos(self) -> dict:
        return {"fuente": "Padel API, consultada en directo (con una caché de unos minutos)",
                "consultado": datetime.now().isoformat(timespec="seconds")}

    async def resolve(self, query: str):
        """(jugadora {'id','name'}, None) o (None, {'error': ...}). Primero el Top 20 guardado (sin peticiones)."""
        q = (query or "").strip()
        if not q:
            return None, {"error": "Falta el nombre de la jugadora.", "tipo": "invalid"}
        found = find_players(local_data.player_ids(), q)
        if len(found) == 1:
            return found[0], None
        if len(found) > 1:
            return None, {"error": f"'{q}' puede ser varias jugadoras: sé más concreta.", "tipo": "invalid",
                          "candidatas": [p["name"] for p in found]}
        try:
            body = await self.client.get("/players", {"name": q, "category": "women", "per_page": 10}, TTL["search"])
        except LiveError as exc:
            return None, _err(exc)
        rows = [{"id": r["id"], "name": pa.short_name(r.get("name")), "full": pa.clean_name(r.get("name"))}
                for r in _rows(body) if r.get("category") == "women" and isinstance(r.get("id"), int)]
        if len(rows) == 1:
            return rows[0], None
        pick = find_players(rows, q)
        if len(pick) == 1:
            return pick[0], None
        return None, {"error": f"No pude identificar a una única jugadora para '{q}'.", "tipo": "notfound",
                      "candidatas": [r["name"] for r in (pick or rows)] or None}

    async def _player_get(self, query: str, suffix: str, params: Optional[dict], ttl: float):
        player, err = await self.resolve(query)
        if err:
            return None, None, err
        try:
            body = await self.client.get(f"/players/{player['id']}{suffix}", params, ttl)
        except LiveError as exc:
            return player, None, _err(exc)
        return player, body, None

    async def perfil(self, nombre: str) -> dict:
        player, body, err = await self._player_get(nombre, "", None, TTL["player"])
        if err:
            return err
        data = _unwrap(body)
        if data.get("category") != "women":
            return {"error": "Esta herramienta solo cubre pádel femenino.", "tipo": "invalid"}
        return {"jugadora": profile_view(data), "datos": self._datos()}

    async def partidos(self, nombre: str, limite: int = 5) -> dict:
        limite = max(1, min(int(limite), 10))
        params = {"sort_by": "played_at", "order_by": "desc", "per_page": limite}
        player, body, err = await self._player_get(nombre, "/matches", params, TTL["matches"])
        if err:
            return err
        rows = [m for m in _rows(body) if m.get("category", "women") == "women"][:limite]
        views = []
        for m in rows:
            v = match_view(m, player["id"])
            if _tournament_name(m):
                v["torneo"] = _tournament_name(m)
            views.append(v)
        out = {"jugadora": player["name"], "partidos": views, "datos": self._datos(),
               "nota": "Solo cuadro principal. En el plan gratuito, los resultados de más de 6 meses salen ocultos."}
        if not views:
            out["mensaje"] = "Padel API no devuelve partidos de esta jugadora."
        return out

    async def _tournament_matches(self, ref: dict, extra: Optional[dict] = None):
        params = {"category": "women", "per_page": 50, **(extra or {})}
        body = await self.client.get(f"/tournaments/{ref['id']}/matches", params, TTL["tournament"])
        return [m for m in _rows(body) if m.get("category", "women") == "women"]

    async def cuadro(self, torneo: Optional[str] = None, ronda: Optional[str] = None) -> dict:
        refs = local_data.tournament_refs()
        ref, _ = local_data.pick_tournament(refs, torneo, self.today())
        if not ref:
            return {"error": (f"No encuentro ningún torneo que encaje con '{torneo}'." if torneo
                              else "No hay ningún torneo con identificador en el calendario guardado."),
                    "tipo": "notfound", "torneos": [r["name"] for r in refs[-6:]] or None}
        round_no = None
        if ronda and str(ronda).strip():
            round_no = ROUND_WORDS.get(norm(str(ronda)))
            if round_no is None:
                return {"error": f"No entiendo la ronda '{ronda}'. Usa: final, semifinales, cuartos, octavos.", "tipo": "invalid"}
        try:
            matches = await self._tournament_matches(ref, {"round": round_no} if round_no else None)
        except LiveError as exc:
            return _err(exc)
        if not round_no:
            matches = [m for m in matches if isinstance(m.get("round"), int) and m["round"] <= 4]
        matches.sort(key=lambda m: (m.get("round") if isinstance(m.get("round"), int) else 99, m.get("index") or 0))
        card = {"nombre": ref["name"], "lugar": ref["place"], "tipo": ref["tipo"],
                "fechas": f"{ref['start'].isoformat()} a {ref['end'].isoformat()}"}
        out = {"torneo": card, "partidos": [match_view(m) for m in matches[:20]], "datos": self._datos()}
        if not matches:
            out["mensaje"] = "El cuadro de esa ronda todavía no está publicado (o aún no se ha jugado)."
        out["nota"] = "Solo cuadro principal femenino. En el plan gratuito, los resultados de más de 6 meses salen ocultos."
        return out

    async def directo(self) -> dict:
        today = self.today()
        refs = local_data.tournament_refs()
        live = [r for r in refs if r["start"] <= today <= r["end"]]
        base = {"marcador_oficial": local_data.OFFICIAL_LIVE_URL}
        if not live:
            nxt = next((r for r in refs if r["start"] > today), None)
            return {**base, "mensaje": "Ahora mismo no hay ningún torneo en juego según el calendario.",
                    "proximo": ({"nombre": nxt["name"], "empieza": nxt["start"].isoformat()} if nxt else None)}
        ref = live[0]
        try:
            matches = await self._tournament_matches(ref)
        except LiveError as exc:
            return {**_err(exc), **base}
        day = today.isoformat()
        playing = [m for m in matches if m.get("status") == "live"]
        done = [m for m in matches if m.get("status") in FINISHED and m.get("played_at") == day]
        coming = [m for m in matches if m.get("status") == "scheduled" and m.get("played_at") == day]
        return {**base, "torneo": ref["name"], "en_juego": [match_view(m) for m in playing],
                "terminados_hoy": [match_view(m) for m in done[:8]], "proximos_hoy": [match_view(m) for m in coming[:8]],
                "datos": self._datos(),
                "nota": "Marcador por sets según Padel API; puede ir con unos minutos de retraso. "
                        "El punto a punto, en el marcador oficial."}

    async def ranking_jugadora(self, nombre: str) -> dict:
        player, body, err = await self._player_get(nombre, "/rankings", None, TTL["rankings"])
        if err:
            return err
        entries = [ranking_entry_view(r) for r in _rows(body) if r.get("category") in (None, "women")]
        out = {"jugadora": player["name"], "rankings": entries, "datos": self._datos(),
               "nota": "Última semana de cada ranking. El histórico semanal es de pago y no se usa."}
        if not entries:
            out["mensaje"] = "Padel API no devuelve rankings de esta jugadora."
        return out

    async def _ranking_list(self, kind: str, top: int, sort: tuple = ("ranking", "asc")):
        top = max(1, min(int(top), 20))
        params = {"type": kind, "category": "women", "per_page": top, "sort_by": sort[0], "order_by": sort[1]}
        try:
            body = await self.client.get("/rankings", params, TTL["rankings"])
        except LiveError as exc:
            return None, _err(exc)
        rows = [r for r in _rows(body) if r.get("category") == "women" and r.get("type", kind) == kind][:top]
        return rows, None

    @staticmethod
    def _ranking_row(r: dict) -> dict:
        return {"posicion": _visible(r.get("ranking")), "nombre": pa.short_name(r.get("name")),
                "pais": f"{pa.flag(r.get('nationality'))} {r.get('nationality') or ''}".strip(), "puntos": _visible(r.get("points")),
                "cambio_posiciones": _visible(r.get("ranking_diff")), "cambio_puntos": _visible(r.get("points_diff"))}

    async def ranking_tipo(self, kind: str, top: int = 10) -> dict:
        rows, err = await self._ranking_list(kind, top)
        if err:
            return err
        out = {"tipo": RANKING_TYPES[kind], "semana_del": rows[0].get("date") if rows else None,
               "ranking": [self._ranking_row(r) for r in rows], "datos": self._datos()}
        if kind == "elo":
            out["nota"] = ("'puntos' es el Elo. Solo entran jugadoras con al menos 5 partidos valorados en los últimos "
                           "12 meses.")
        if not rows:
            out["mensaje"] = "Padel API no devuelve este ranking ahora mismo."
        return out

    async def movimientos(self, tipo: str = "official", sentido: str = "suben", top: int = 5) -> dict:
        kind = {"official": "official", "oficial": "official", "race": "race", "elo": "elo"}.get(norm(tipo or "official"))
        way = {"suben": "desc", "suben mas": "desc", "subida": "desc", "ganan": "desc", "bajan": "asc",
               "bajada": "asc", "pierden": "asc"}.get(norm(sentido or "suben"))
        if not kind or not way:
            return {"error": "Usa tipo: oficial, race o elo; y sentido: suben o bajan.", "tipo": "invalid"}
        rows, err = await self._ranking_list(kind, top, ("points_diff", way))
        if err:
            return err
        rows = [r for r in rows if isinstance(r.get("points_diff"), int) and not isinstance(r.get("points_diff"), bool)]
        out = {"tipo": RANKING_TYPES[kind], "sentido": "las que más suben" if way == "desc" else "las que más bajan",
               "semana_del": rows[0].get("date") if rows else None, "ranking": [self._ranking_row(r) for r in rows],
               "datos": self._datos(),
               "nota": "Cambios respecto a la semana anterior, ordenados por puntos ganados o perdidos (cambio_puntos)."}
        if not rows:
            out["mensaje"] = "Padel API no devuelve movimientos ahora mismo."
        return out

    async def parejas(self, nombre: str) -> dict:
        player, body, err = await self._player_get(nombre, "/pairs", None, TTL["pairs"])
        if err:
            return err
        views = []
        for pair in _rows(body):
            members = [p for p in (pair.get("players") or []) if isinstance(p, dict)]
            if any(p.get("category") not in (None, "women") for p in members):
                continue
            partner = [pa.short_name(p.get("name")) for p in members if p.get("id") != player["id"]]
            views.append({"companera": " / ".join(partner) or None, "estado": pair.get("status"),
                          "puntos": _visible(pair.get("points")), "primer_partido": str(pair.get("first_match_at") or "")[:10] or None,
                          "ultimo_partido": str(pair.get("last_match_at") or "")[:10] or None})
        views.sort(key=lambda v: v["ultimo_partido"] or "", reverse=True)
        out = {"jugadora": player["name"], "parejas": views[:10], "datos": self._datos()}
        if not views:
            out["mensaje"] = "Padel API no devuelve parejas de esta jugadora."
        return out

    async def ediciones(self, torneo: Optional[str] = None) -> dict:
        refs = local_data.tournament_refs()
        ref, _ = local_data.pick_tournament(refs, torneo, self.today())
        if not ref:
            return {"error": (f"No encuentro ningún torneo que encaje con '{torneo}'." if torneo
                              else "No hay ningún torneo con identificador en el calendario guardado."),
                    "tipo": "notfound", "torneos": [r["name"] for r in refs[-6:]] or None}
        try:
            body = await self.client.get(f"/tournaments/{ref['id']}/editions", None, TTL["editions"])
        except LiveError as exc:
            return _err(exc)
        views = []
        for t in _rows(body):
            winners = t.get("winners") if isinstance(t.get("winners"), dict) else {}
            prize = t.get("prize") if isinstance(t.get("prize"), dict) else None
            views.append({"edicion": pa.clean_name(t.get("name")), "inicio": t.get("start_date"), "fin": t.get("end_date"),
                          "ciudad": pa.clean_name(t.get("location")), "pais": pa.flag(t.get("country")),
                          "estado": t.get("status"),
                          "premio": f"{prize.get('amount')} {prize.get('currency')}" if prize and prize.get("amount") else None,
                          "campeonas": _pair_text(winners.get("women")) or None})
        views.sort(key=lambda v: v["inicio"] or "", reverse=True)
        out = {"torneo": ref["name"], "ediciones": views[:10], "datos": self._datos(),
               "nota": "Las ediciones se agrupan por nombre, nivel y país: si un torneo cambió de nombre, pueden faltar algunas."}
        if not views:
            out["mensaje"] = "Padel API no devuelve ediciones de este torneo."
        return out

    async def prediccion(self, pareja_a: list, pareja_b: list) -> dict:
        a, b = [x for x in (pareja_a or []) if str(x).strip()], [x for x in (pareja_b or []) if str(x).strip()]
        if not (1 <= len(a) <= 2) or len(a) != len(b):
            return {"error": "Indica una o dos jugadoras por lado, y el mismo número en los dos.", "tipo": "invalid"}
        team = {"team_1": [], "team_2": []}
        for side, names in (("team_1", a), ("team_2", b)):
            for n in names:
                player, err = await self.resolve(n)
                if err:
                    return err
                team[side].append(player)
        ids = [p["id"] for p in team["team_1"] + team["team_2"]]
        if len(set(ids)) != len(ids):
            return {"error": "Una jugadora no puede estar en los dos lados.", "tipo": "invalid"}
        try:
            body = await self.client.post("/matches/simulate", {"team_1": [p["id"] for p in team["team_1"]],
                                                                "team_2": [p["id"] for p in team["team_2"]]}, TTL["simulate"])
        except LiveError as exc:
            return _err(exc)
        sim = _unwrap(body)
        players = sim.get("players") if isinstance(sim.get("players"), dict) else {}
        if any(isinstance(p, dict) and p.get("category") not in (None, "women")
               for side in players.values() if isinstance(side, list) for p in side):
            return {"error": "Esta herramienta solo cubre pádel femenino.", "tipo": "invalid"}
        prob = sim.get("probability") if isinstance(sim.get("probability"), dict) else {}
        names = {k: " / ".join(p["name"] for p in v) for k, v in team.items()}
        fav = {"team_1": names["team_1"], "team_2": names["team_2"]}.get(sim.get("favorite"))
        elos = {pa.short_name(p.get("name")): p.get("elo") for side in players.values() if isinstance(side, list)
                for p in side if isinstance(p, dict)}
        return {"pareja_a": names["team_1"], "pareja_b": names["team_2"], "probabilidad_a": _pct(prob.get("team_1")),
                "probabilidad_b": _pct(prob.get("team_2")), "favorita": fav or "ninguna: están igualadas",
                "diferencia_elo": sim.get("elo_diff"), "elo": elos or None, "datos": self._datos(),
                "nota": "Estimación a partir del Elo de Padel API (no tiene en cuenta lesiones ni el día): no es una garantía."}


_service: Optional[Service] = None


def service() -> Service:
    global _service
    if _service is None:
        _service = Service()
    return _service
