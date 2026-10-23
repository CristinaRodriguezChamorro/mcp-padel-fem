"""
padel_mcp/tools.py — Herramientas MCP del pádel femenino (solo lectura).

Este módulo NO scrapea nada por su cuenta: recibe "proveedores" (funciones async
que ya usan las cachés y las fuentes de server.py) y los expone como tools.
Así la web y el MCP comparten datos, cachés y límites, y el módulo se puede
probar con datos falsos.

Cada proveedor devuelve:  {"data": <datos>, "age_s": <segundos desde que se obtuvo>,
                           "fallback": <True si son datos de respaldo> | None}

Además incluye `McpGate`, un envoltorio ASGI para el endpoint MCP con tres modos de acceso
(se elige con MCP_AUTH; ver `gate_from_env`):
  - github: se exige un token de GitHub válido (`Authorization: Bearer <token>`), ver padel_mcp/auth.py
  - token:  se exige el token fijo MCP_TOKEN
  - open:   abierto, solo con límite de peticiones
y, en todos, límite por IP y global (MCP_RATE_PER_MIN / MCP_GLOBAL_RATE_PER_MIN) y, con
GitHub, también por cuenta (MCP_USER_RATE_PER_MIN).
"""

import asyncio
import hmac
import os
import re
import time
from collections import deque
from typing import Optional

from fastmcp import FastMCP

from tools.players import norm as _norm, find_players as _find_players, mentions as _mentions
from tools.local_data import OFFICIAL_LIVE_URL, describe_final, find_final, tournament_card

READ_ONLY = {"readOnlyHint": True, "openWorldHint": True}

# Las tres herramientas del MCP. El resto (jugadora, noticias, calendario...) existe pero está apagado por
# defecto: se activa con MCP_TOOLS=extended. Los tests comprueban que CORE + EXTRA son todas las que hay.
CORE_TOOLS = ("ranking_femenino", "ultimo_torneo", "proximo_torneo")
EXTRA_TOOLS = ("jugadora", "comparar_jugadoras", "torneo_actual", "resultados_recientes", "resultado_torneo",
               "calendario", "noticias", "estado_de_los_datos", "resumen_del_dia",
               # consultan a Padel API en el momento (con caché y un presupuesto de peticiones) y solo usan lo
               # GRATUITO de su plan: tools/padelapi_live.py
               "perfil_jugadora", "partidos_jugadora", "cuadro_torneo", "partidos_en_directo", "rankings_jugadora",
               "prediccion_partido", "ranking_elo", "ranking_race", "movimientos_ranking", "parejas_jugadora",
               "ediciones_torneo")


def extended_tools_enabled() -> bool:
    return os.environ.get("MCP_TOOLS", "").strip().lower() == "extended"


TIMEOUTS = {"ranking": 30, "calendar": 30, "live": 30, "finals": 30, "news": 60}


# ── utilidades ───────────────────────────────────────────────────────────────

def _pts_to_int(value) -> Optional[int]:
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None


def _player_view(p: dict) -> dict:
    return {
        "posicion": p.get("pos"),
        "nombre": p.get("name"),
        "pais": p.get("flag"),
        "puntos": _pts_to_int(p.get("pts")),
        "pareja": p.get("pair") or None,
    }


async def _call(provider, key: str):
    """Ejecuta un proveedor con tiempo máximo. Devuelve (payload | None, error | None)."""
    try:
        payload = await asyncio.wait_for(provider(), timeout=TIMEOUTS.get(key, 45))
        return payload, None
    except asyncio.TimeoutError:
        return None, f"La fuente '{key}' tardó demasiado en responder."
    except Exception as exc:  # noqa: BLE001 — se devuelve como aviso, no se rompe la tool
        return None, f"No se pudo obtener '{key}': {type(exc).__name__}"


def _meta(payload: dict) -> dict:
    meta = {"antiguedad_segundos": payload.get("age_s")}
    if payload.get("tipo"):
        meta["tipo_de_datos"] = payload["tipo"]
    if payload.get("fuente"):
        meta["fuente"] = payload["fuente"]
    if payload.get("licencia"):
        meta["licencia"] = payload["licencia"]
    if payload.get("desactualizado"):
        meta.setdefault("aviso", "Datos con retraso: la fuente lleva más de dos semanas sin actualizarse.")
    if payload.get("aviso"):
        meta["aviso"] = payload["aviso"]
    elif payload.get("fallback"):
        meta["aviso"] = "Datos de respaldo: la fuente no estaba disponible."
    return meta


_USO = {
    "resumen": "Resumen propio, con la fecha de la noticia, su autor y el enlace directo a la noticia original.",
    "extracto": "Extracto literal de la fuente (sin reescribir), con la fecha de la noticia y el enlace directo para leerla entera.",
}


def _news_item(item: dict) -> dict:
    mode = item.get("modo") or "resumen"
    return {
        "titular": item.get("titular") or item.get("titulo"),
        "resumen": item.get("resumen") or None,
        "fecha": item.get("fecha"),
        "fuentes": item.get("fuentes", []),
        "jugadoras": item.get("jugadoras", []),
        "modo": mode,
        "uso": _USO.get(mode, _USO["resumen"]),
        "enlace": next((f.get("url") for f in (item.get("fuentes") or []) if f.get("url")), None),
    }


def _news_about(item: dict, player: dict, players: list) -> bool:
    """Usa las etiquetas del pipeline de noticias; si la noticia no las trae, analiza el texto."""
    tags = item.get("jugadoras")
    if tags is not None:
        return _norm(player.get("name", "")) in {_norm(t) for t in tags}
    return _mentions(f"{item.get('titular', '')} {item.get('titulo', '')} {item.get('resumen', '')}",
                     player, players)


# ── construcción del servidor MCP ────────────────────────────────────────────

def build_mcp(providers: dict, extended: Optional[bool] = None) -> FastMCP:
    """
    providers: {'ranking','calendar','live','finals','news','status'} -> funciones async sin argumentos.
    extended: True registra también las herramientas extra; por defecto lo decide MCP_TOOLS=extended.
    """
    # Las instrucciones de siempre NO cambian. El párrafo sobre las herramientas que consultan a Padel API solo se
    # añade cuando esas herramientas están activas (MCP_TOOLS=extended): sin ellas, el agente no lee nada nuevo.
    instructions = (
        "Datos del pádel profesional FEMENINO (circuito FIP / Premier Padel). Solo lectura. Tres "
        "herramientas: ranking_femenino (posición, nombre y puntos de cada jugadora), ultimo_torneo (el "
        "último torneo terminado) y proximo_torneo (el siguiente). Los datos los guarda el servidor; cada "
        "respuesta indica su fuente y su antigüedad: si trae un aviso de retraso, dilo. "
        "Al contar un resultado responde con: torneo y dónde fue, qué tipo de torneo es, quién jugó la "
        "final, primeras, segundas y terceras, y el resultado final; si falta un dato (las terceras, por "
        "ejemplo), dilo sin inventarlo. NO hay marcadores en directo: para eso se da el enlace oficial. "
        "Si ves más herramientas (jugadora, noticias, resultado_torneo...), úsalas con sus filtros; cada "
        "noticia trae su resumen (modo 'resumen': resumen propio; 'extracto': texto literal), su fecha y "
        "el enlace directo a la noticia ('enlace'). Cita siempre el medio, la fecha y el enlace."
    )
    if extended_tools_enabled() if extended is None else extended:
        instructions += (
            " Las herramientas perfil_jugadora, partidos_jugadora, cuadro_torneo, partidos_en_directo, "
            "rankings_jugadora, prediccion_partido, ranking_elo, ranking_race, movimientos_ranking, "
            "parejas_jugadora y ediciones_torneo consultan a Padel API en el momento, solo con lo gratuito de su "
            "plan: si devuelven un error de tipo 'plan', 'budget' o 'limit', explícaselo a la persona y no "
            "reintentes en bucle; los resultados de más de 6 meses salen ocultos en el plan gratuito y de los "
            "rankings solo se ve la última semana."
        )
    mcp = FastMCP("PadelFemMCP", instructions=instructions)

    async def ranking_players():
        payload, err = await _call(providers["ranking"], "ranking")
        if err:
            return None, None, err
        return payload["data"], payload, None

    async def one_player(players, query):
        found = _find_players(players, query)
        if len(found) != 1:
            return None, {"error": f"No pude identificar a una única jugadora para '{query}' en el Top {len(players)}.",
                          "candidatas": [p.get("name") for p in found] or None}
        return found[0], None

    @mcp.tool(annotations=READ_ONLY)
    async def ranking_femenino(top: int = 10) -> dict:
        """Ranking mundial femenino: posición, nombre, país y puntos de cada jugadora, de la 1.ª a la 'top'
        (por defecto 10, máximo 20). Úsala para '¿quiénes son las 10 mejores?', '¿quién es la número 3?' o
        '¿cuántos puntos tiene X?'. Mira 'datos': indica de cuándo es el ranking y si va con retraso."""
        players, payload, err = await ranking_players()
        if err:
            return {"error": err}
        top = max(1, min(int(top), 20))
        return {"ranking": [_player_view(p) for p in players[:top]], "datos": _meta(payload)}

    @mcp.tool(annotations=READ_ONLY)
    async def jugadora(nombre: str) -> dict:
        """Ficha de UNA jugadora del ranking: su posición y puntos, las finales que ha jugado esta temporada
        y las noticias recientes que la mencionan. Acepta nombre completo o apellido.
        Úsala para '¿cómo va Delfina Brea?' o '¿qué finales ha jugado Triay?'."""
        players, payload, err = await ranking_players()
        if err:
            return {"error": err}
        found = _find_players(players, nombre)
        if not found:
            return {"encontrada": False, "mensaje": f"'{nombre}' no aparece en el ranking disponible ({len(players)} jugadoras).",
                    "top": [p.get("name") for p in players]}
        if len(found) > 1:
            return {"encontrada": False, "mensaje": "Hay varias coincidencias; indica nombre y apellido.",
                    "candidatas": [p.get("name") for p in found]}
        p = found[0]
        out = {"encontrada": True, "jugadora": _player_view(p), "datos_ranking": _meta(payload)}

        finals, ferr = await _call(providers["finals"], "finals")
        if ferr:
            out["finales"] = {"aviso": ferr}
        else:
            mine = [f for f in finals["data"] if _mentions(f"{f.get('ganadoras', '')} {f.get('finalistas', '')}", p, players)]
            out["finales"] = {"lista": mine[:6], "datos": _meta(finals)}

        news, news_err = await _call(providers["news"], "news")
        if news_err:
            out["noticias"] = {"aviso": news_err}
        else:
            out["noticias"] = [_news_item(n) for n in news["data"].get("resumen_diario", []) if _news_about(n, p, players)][:5]
        return out

    @mcp.tool(annotations=READ_ONLY)
    async def comparar_jugadoras(jugadora_a: str, jugadora_b: str) -> dict:
        """Compara dos jugadoras del ranking: posición, puntos y diferencia de puntos."""
        players, payload, err = await ranking_players()
        if err:
            return {"error": err}
        views = []
        for query in (jugadora_a, jugadora_b):
            found = _find_players(players, query)
            if len(found) != 1:
                return {"error": f"No pude identificar a una única jugadora para '{query}' en el ranking disponible.",
                        "candidatas": [p.get("name") for p in found] or None}
            views.append(_player_view(found[0]))
        a, b = views
        diff = abs(a["puntos"] - b["puntos"]) if a["puntos"] is not None and b["puntos"] is not None else None
        return {"a": a, "b": b, "diferencia_puntos": diff, "datos": _meta(payload)}

    @mcp.tool(annotations=READ_ONLY)
    async def ultimo_torneo() -> dict:
        """Quién ganó el último torneo de pádel femenino ya terminado: cómo se llama, dónde fue, qué tipo de
        torneo es (Major, P1, P2 o Finals, con una explicación), quién jugó la final, primeras, segundas y
        terceras, y el resultado final (por cuánto ganaron). Si falta un dato lo dice en una nota: no lo
        inventes. Mira 'datos': indica la antigüedad de la fuente."""
        finals, err = await _call(providers["finals"], "finals")
        if err:
            return {"error": err}
        if not finals["data"]:
            return {"error": "No hay ninguna final femenina ya jugada en los datos disponibles.", "datos": _meta(finals)}
        return {**describe_final(finals["data"][0]), "datos": _meta(finals)}

    @mcp.tool(annotations=READ_ONLY)
    async def proximo_torneo() -> dict:
        """Cuándo y dónde es el próximo torneo de Premier Padel: nombre, ciudad y país, fechas, cuántos días
        faltan, qué tipo de torneo es (con una explicación) y dónde verlo. Si ahora mismo hay uno en juego lo
        devuelve aparte, junto con los siguientes y el enlace al marcador oficial."""
        cal, err = await _call(providers["calendar"], "calendar")
        if err:
            return {"error": err}
        live = next((x for x in cal["data"] if x.get("live")), None)
        upcoming = [x for x in cal["data"] if not x.get("live")]
        if not live and not upcoming:
            return {"mensaje": "No hay torneos próximos en el calendario disponible.", "datos": _meta(cal)}
        return {"proximo": tournament_card(upcoming[0]) if upcoming else None,
                "en_juego_ahora": tournament_card(live) if live else None,
                "despues": [x.get("name") for x in upcoming[1:4]],
                "marcador_oficial": OFFICIAL_LIVE_URL, "datos": _meta(cal)}

    @mcp.tool(annotations=READ_ONLY)
    async def torneo_actual() -> dict:
        """Torneo Premier Padel que se está jugando ahora (nombre, sede, fechas), con el enlace al marcador oficial.
        NO incluye marcadores ni partidos en directo: esa información no se reproduce, solo se enlaza."""
        live, err = await _call(providers["live"], "live")
        if err:
            return {"error": err}
        d = live["data"]
        return {
            "activo": bool(d.get("active")),
            "mensaje": d.get("message"),
            "nombre": d.get("name"),
            "sede": d.get("place"),
            "fechas": d.get("dates"),
            "donde_ver": d.get("watch", []),
            "marcador_oficial": d.get("official_url"),
            "resultados": d.get("results", [])[:15],
            "fuente": d.get("source"),
            "datos": _meta(live),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def resultados_recientes(jugadora: Optional[str] = None, limite: int = 5) -> dict:
        """Finales femeninas ya jugadas esta temporada (torneo, ganadoras, finalistas y resultado), las más recientes
        primero. Con 'jugadora' solo las finales en las que ella jugó. No incluye partidos de rondas previas.
        Para el detalle de un torneo (dónde, tipo, podio y resultado) usa resultado_torneo."""
        limite = max(1, min(int(limite), 20))
        finals, err = await _call(providers["finals"], "finals")
        if err:
            return {"error": err}
        items = finals["data"]
        if jugadora:
            players, _, rerr = await ranking_players()
            if rerr:
                return {"error": rerr}
            p, bad = await one_player(players, jugadora)
            if bad:
                return bad
            items = [f for f in items if _mentions(f"{f.get('ganadoras', '')} {f.get('finalistas', '')}", p, players)]
        return {"finales": items[:limite], "datos": _meta(finals)}

    @mcp.tool(annotations=READ_ONLY)
    async def resultado_torneo(torneo: Optional[str] = None, jugadora: Optional[str] = None) -> dict:
        """Ficha de la final femenina de un torneo ya jugado: dónde fue, qué tipo de torneo es (Major, P1, P2...),
        quién jugó la final, quién quedó primeras, segundas y terceras, y el resultado final. Sin parámetros da el
        torneo más reciente; con 'torneo' (p. ej. 'Madrid' o 'Valencia P1') busca ese; con 'jugadora', el último en
        cuya final jugó. Si falta un dato (por ejemplo las terceras, que la fuente no recoge) lo indica en una nota:
        no lo inventes. Solo hay datos de las finales, no de rondas previas ni de marcadores en directo."""
        finals, err = await _call(providers["finals"], "finals")
        if err:
            return {"error": err}
        items = finals["data"]
        if jugadora:
            players, _, rerr = await ranking_players()
            if rerr:
                return {"error": rerr}
            p, bad = await one_player(players, jugadora)
            if bad:
                return bad
            items = [f for f in items if _mentions(f"{f.get('ganadoras', '')} {f.get('finalistas', '')}", p, players)]
        found = find_final(items, torneo) if torneo else (items[0] if items else None)
        if not found:
            return {"error": "No tengo la final de ese torneo entre las ya jugadas esta temporada.",
                    "torneos_disponibles": [f["torneo"] for f in finals["data"][:12]], "datos": _meta(finals)}
        return {**describe_final(found),
                "otros_torneos": [f["torneo"] for f in items if f is not found][:8],
                "datos": _meta(finals)}

    @mcp.tool(annotations=READ_ONLY)
    async def calendario(limite: int = 6) -> dict:
        """Próximos torneos Premier Padel (fechas, sede y categoría); el que está en juego sale primero."""
        limite = max(1, min(int(limite), 10))
        cal, err = await _call(providers["calendar"], "calendar")
        if err:
            return {"error": err}
        items = [
            {"fechas": f"{t.get('day', '')} {t.get('month', '')}".strip(), "torneo": t.get("name"),
             "sede": t.get("place"), "categoria": t.get("badgeText"), "tv": t.get("tv") or None,
             "en_juego": bool(t.get("live"))}
            for t in cal["data"][:limite]
        ]
        return {"torneos": items, "datos": _meta(cal)}

    @mcp.tool(annotations=READ_ONLY)
    async def noticias(jugadora: Optional[str] = None, limite: int = 5) -> dict:
        """Noticias recientes sobre las jugadoras del Top 20 (solo pádel femenino). Cada noticia trae un resumen de
        unas tres líneas (resumen propio o extracto literal, según la fuente), su fecha real y el enlace directo a
        la noticia original; cada una indica de qué jugadoras habla. Con 'jugadora' solo devuelve las que hablan de ella."""
        limite = max(1, min(int(limite), 10))
        news, err = await _call(providers["news"], "news")
        if err:
            return {"error": err}
        items = news["data"].get("resumen_diario", [])
        if jugadora:
            players, _, rerr = await ranking_players()
            if rerr:
                return {"error": rerr}
            p, bad = await one_player(players, jugadora)
            if bad:
                return bad
            items = [n for n in items if _news_about(n, p, players)]
        return {"noticias": [_news_item(n) for n in items[:limite]], "datos": _meta(news)}

    @mcp.tool(annotations=READ_ONLY)
    async def estado_de_los_datos() -> dict:
        """Estado de las fuentes: de dónde sale cada dato (Wikipedia, ejemplo, manual), cuándo lo actualizó su fuente,
        si hay retraso, el resultado de la última actualización automática y si las noticias (RSS) están activas.
        Úsala para saber hasta qué punto fiarte de las demás respuestas."""
        provider = providers.get("status")
        if not provider:
            return {"error": "Estado no disponible."}
        payload, err = await _call(provider, "ranking")
        return {"error": err} if err else payload["data"]

    @mcp.tool(annotations=READ_ONLY)
    async def resumen_del_dia() -> dict:
        """Panorama rápido: Top 5 del ranking, torneo en juego (con enlace al marcador oficial), la última final
        jugada y las 3 noticias más recientes."""
        out = {}
        players, payload, err = await ranking_players()
        out["top5"] = {"error": err} if err else {"jugadoras": [_player_view(p) for p in players[:5]], "datos": _meta(payload)}

        live, lerr = await _call(providers["live"], "live")
        if lerr:
            out["torneo"] = {"error": lerr}
        else:
            d = live["data"]
            out["torneo"] = {"activo": bool(d.get("active")), "nombre": d.get("name"), "fechas": d.get("dates"),
                             "marcador_oficial": d.get("official_url")}

        finals, ferr = await _call(providers["finals"], "finals")
        out["ultima_final"] = {"error": ferr} if ferr else (finals["data"][0] if finals["data"] else None)

        news, nerr = await _call(providers["news"], "news")
        out["noticias"] = ({"error": nerr} if nerr else
                           [_news_item(n) for n in news["data"].get("resumen_diario", [])[:3]])
        return out

    # ── Padel API en el momento (solo con MCP_TOOLS=extended) ────────────────
    live_service = providers.get("padelapi")

    async def _live(method: str, *args):
        if live_service is None:
            return {"error": "Las consultas en directo a Padel API no están activas en este servidor.", "tipo": "config"}
        return await getattr(live_service, method)(*args)

    @mcp.tool(annotations=READ_ONLY)
    async def perfil_jugadora(nombre: str) -> dict:
        """Ficha de una jugadora (cualquiera, no solo del Top 20): país, puesto y puntos, Elo, altura, lado de la
        pista (derecha/izquierda), mano, edad y lugar de nacimiento. Consulta a Padel API en el momento."""
        return await _live("perfil", nombre)

    @mcp.tool(annotations=READ_ONLY)
    async def partidos_jugadora(nombre: str, limite: int = 5) -> dict:
        """Los últimos partidos de una jugadora (de 1 a 10): fecha, ronda, con quién jugó, rival, resultado y si ganó.
        Solo cuadro principal. En el plan gratuito de Padel API, los resultados de más de 6 meses salen ocultos."""
        return await _live("partidos", nombre, limite)

    @mcp.tool(annotations=READ_ONLY)
    async def cuadro_torneo(torneo: Optional[str] = None, ronda: Optional[str] = None) -> dict:
        """Partidos del cuadro principal FEMENINO de un torneo, por ronda (cuartos, semifinales y final por defecto).
        'torneo' es parte del nombre o de la ciudad ('Madrid', 'Milano'); sin él, el torneo en juego o el último
        terminado. 'ronda': final, semifinales, cuartos u octavos."""
        return await _live("cuadro", torneo, ronda)

    @mcp.tool(annotations=READ_ONLY)
    async def partidos_en_directo() -> dict:
        """Qué se está jugando ahora en el torneo en curso (solo femenino): partidos en juego con su marcador por
        sets, los terminados hoy y los que quedan. Puede ir con unos minutos de retraso; sin punto a punto."""
        return await _live("directo")

    @mcp.tool(annotations=READ_ONLY)
    async def rankings_jugadora(nombre: str) -> dict:
        """Posición y puntos de una jugadora en el ranking oficial, el ranking RACE y el Elo, con lo que han cambiado
        respecto a la semana anterior. Solo la última semana (el histórico semanal es de pago y no se usa)."""
        return await _live("ranking_jugadora", nombre)

    @mcp.tool(annotations=READ_ONLY)
    async def ranking_elo(top: int = 10) -> dict:
        """Clasificación femenina por Elo de Padel API (de la 1.ª a la 'top', máximo 20): mide la fuerza real de
        cada jugadora partido a partido. Solo entran las que tienen al menos 5 partidos valorados en 12 meses."""
        return await _live("ranking_tipo", "elo", top)

    @mcp.tool(annotations=READ_ONLY)
    async def ranking_race(top: int = 10) -> dict:
        """Clasificación femenina RACE (puntos de la FIP de la temporada, de la 1.ª a la 'top', máximo 20), distinta
        del ranking oficial. Trae el cambio de posiciones y de puntos respecto a la semana anterior."""
        return await _live("ranking_tipo", "race", top)

    @mcp.tool(annotations=READ_ONLY)
    async def movimientos_ranking(tipo: str = "oficial", sentido: str = "suben", top: int = 5) -> dict:
        """Las jugadoras que más han subido o bajado esta semana, por puntos ganados o perdidos. 'tipo': oficial,
        race o elo. 'sentido': suben o bajan. 'top': hasta 20."""
        return await _live("movimientos", tipo, sentido, top)

    @mcp.tool(annotations=READ_ONLY)
    async def parejas_jugadora(nombre: str) -> dict:
        """Con quién ha jugado una jugadora: sus compañeras, desde y hasta cuándo, y los puntos de cada pareja
        (las 10 más recientes)."""
        return await _live("parejas", nombre)

    @mcp.tool(annotations=READ_ONLY)
    async def ediciones_torneo(torneo: Optional[str] = None) -> dict:
        """Ediciones anteriores de un torneo con sus campeonas, fechas, ciudad y premio. 'torneo' es parte del
        nombre o de la ciudad ('Madrid', 'Milano'); sin él, el torneo en juego o el último terminado."""
        return await _live("ediciones", torneo)

    @mcp.tool(annotations=READ_ONLY)
    async def prediccion_partido(pareja_a: list[str], pareja_b: list[str]) -> dict:
        """Probabilidad de victoria de un partido entre dos parejas (o dos jugadoras): una o dos jugadoras por lado,
        con el mismo número en cada uno. Se calcula con el Elo de Padel API: es una estimación, no una garantía."""
        return await _live("prediccion", pareja_a, pareja_b)

    if not (extended_tools_enabled() if extended is None else extended):
        mcp.disable(names=set(EXTRA_TOOLS), components={"tool"})
    return mcp


# ── puerta de entrada del endpoint MCP ───────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    """Lee un entero del entorno; vacío o no numérico (docker-compose pasa "" si no se define) -> valor por defecto."""
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


class McpGate:
    """
    Envoltorio ASGI para el endpoint MCP.

    Orden de las comprobaciones (de la más barata a la más cara):
      1. límite por IP y global      -> 429   (también frena los intentos con tokens falsos)
      2. identidad
           - modo GitHub: token válido de una cuenta permitida -> 401 / 403 / 503
           - modo token:  coincide con MCP_TOKEN               -> 401
           - modo abierto: no se pide nada
      3. límite por cuenta de GitHub -> 429
    """

    def __init__(self, app, token: Optional[str] = None,
                 per_ip: Optional[int] = None, global_limit: Optional[int] = None,
                 github=None, per_user: Optional[int] = None):
        self.app = app
        self.github = github
        # Con GitHub activado el token fijo no interviene.
        self.token = "" if github else (token if token is not None else os.environ.get("MCP_TOKEN", "")).strip()
        self.per_ip = per_ip if per_ip is not None else _env_int("MCP_RATE_PER_MIN", 120)
        self.global_limit = global_limit if global_limit is not None else _env_int("MCP_GLOBAL_RATE_PER_MIN", 600)
        self.per_user = per_user if per_user is not None else _env_int("MCP_USER_RATE_PER_MIN", 120)
        self._hits: dict[str, deque] = {}
        self._user_hits: dict[str, deque] = {}
        self._all: deque = deque()

    @staticmethod
    def _header(scope, name: bytes) -> str:
        for key, value in scope.get("headers", []):
            if key == name:
                return value.decode("latin-1")
        return ""

    @classmethod
    def _client_ip(cls, scope) -> str:
        forwarded = cls._header(scope, b"x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or "?"
        client = scope.get("client")
        return client[0] if client else "?"

    @classmethod
    def _bearer(cls, scope) -> str:
        """El token de `Authorization: Bearer <token>` ('' si falta o usa otro esquema)."""
        scheme, _, value = cls._header(scope, b"authorization").partition(" ")
        return value.strip() if scheme.lower() == "bearer" else ""

    @staticmethod
    def _window_hit(table: dict, key: str, limit: int, now: float) -> bool:
        """Registra un golpe en la ventana de 60 s de `key`. True si ya superó el límite."""
        hits = table.setdefault(key, deque())
        while hits and hits[0] < now - 60.0:
            hits.popleft()
        if len(table) > 5000:
            for k in [k for k, v in table.items() if not v]:
                del table[k]
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False

    def _rate_limited(self, ip: str) -> bool:
        now = time.monotonic()
        while self._all and self._all[0] < now - 60.0:
            self._all.popleft()
        if len(self._all) >= self.global_limit:
            return True
        if self._window_hit(self._hits, ip, self.per_ip, now):
            return True
        self._all.append(now)
        return False

    def _user_limited(self, login: str) -> bool:
        return self._window_hit(self._user_hits, login.lower(), self.per_user, time.monotonic())

    async def _reject(self, send, status: int, message: str, extra: Optional[list] = None):
        body = ('{"error": "%s"}' % message).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        headers += extra or []
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        if self._rate_limited(self._client_ip(scope)):
            return await self._reject(send, 429, "Demasiadas peticiones; espera un minuto",
                                      [(b"retry-after", b"60")])

        bearer = self._bearer(scope)

        if self.github:
            challenge = b'Bearer realm="padel-fem-mcp"'
            if not bearer:
                return await self._reject(send, 401, "Falta el token de GitHub", [(b"www-authenticate", challenge)])
            login, status = await self.github.verify(bearer)
            if status == "invalid":
                return await self._reject(send, 401, "Token de GitHub no válido",
                                          [(b"www-authenticate", challenge + b', error="invalid_token"')])
            if status == "forbidden":
                return await self._reject(send, 403, "Tu cuenta de GitHub no está autorizada")
            if status != "ok":
                return await self._reject(send, 503, "No se pudo comprobar el token con GitHub; reintenta en un momento",
                                          [(b"retry-after", b"30")])
            if self._user_limited(login):
                return await self._reject(send, 429, "Demasiadas peticiones de esta cuenta; espera un minuto",
                                          [(b"retry-after", b"60")])
            scope["padel_user"] = login

        elif self.token:
            if not hmac.compare_digest(bearer.encode(), self.token.encode()):
                return await self._reject(send, 401, "No autorizado",
                                          [(b"www-authenticate", b'Bearer realm="padel-fem-mcp"')])

        return await self.app(scope, receive, send)


def gate_from_env(app) -> McpGate:
    """
    Crea la puerta del MCP según el entorno. Nunca "se abre por error": un valor desconocido
    o contradictorio detiene el arranque en lugar de dejar el endpoint sin protección.

      MCP_AUTH=github   token de GitHub (MCP_GITHUB_USERS opcional: cuentas permitidas)
      MCP_AUTH=token    token fijo MCP_TOKEN (obligatorio en este modo)
      MCP_AUTH=open     sin identificación (no se puede combinar con MCP_TOKEN)
      sin MCP_AUTH      "token" si MCP_TOKEN está definido, y si no "open"
    """
    mode = os.environ.get("MCP_AUTH", "").strip().lower()
    token = os.environ.get("MCP_TOKEN", "").strip()
    if mode in ("", "auto"):
        mode = "token" if token else "open"

    if mode == "github":
        from padel_mcp.auth import GithubTokenVerifier, parse_allowed_users
        from tools.http_policy import USER_AGENT
        users = parse_allowed_users(os.environ.get("MCP_GITHUB_USERS", ""))
        print("  MCP: acceso con token de GitHub (" +
              (f"{len(users)} cuenta(s) permitida(s)" if users else "cualquier cuenta de GitHub") + ")")
        return McpGate(app, github=GithubTokenVerifier(allowed_users=users, user_agent=USER_AGENT))
    if mode == "token":
        if not token:
            raise ValueError("MCP_AUTH=token requiere definir MCP_TOKEN")
        print("  MCP: acceso con token fijo (MCP_TOKEN)")
        return McpGate(app, token=token)
    if mode == "open":
        if token:
            raise ValueError("MCP_AUTH=open no se puede combinar con MCP_TOKEN: quita uno de los dos")
        print("  MCP: endpoint /ai/mcp ABIERTO (solo lectura, con límite de peticiones)")
        return McpGate(app, token="")
    raise ValueError(f"MCP_AUTH={mode!r} no es válido: usa github, token u open")
