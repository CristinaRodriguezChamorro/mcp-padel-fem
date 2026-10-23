"""
Pruebas de la app real (web + MCP) sin internet: arrancan el servidor en local con archivos de
datos temporales (DATA_DIR) y hablan con él por HTTP. La tarea de Wikipedia va apagada.
Ejecutar desde apps/mcp-server:   python -m unittest discover -s tests -v
"""
import json
import os
os.environ.setdefault("MCP_TOOLS", "extended")   # los tests usan todas las herramientas; el valor por defecto son 3
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.pop("MCP_TOKEN", None)
os.environ.pop("MCP_AUTH", None)
os.environ["WIKIPEDIA_INGEST"] = "off"
# Cada llamada a una tool usa varias peticiones HTTP: sin esto la propia suite agotaría el límite.
os.environ["MCP_RATE_PER_MIN"] = "100000"
os.environ["MCP_GLOBAL_RATE_PER_MIN"] = "100000"

DATA = tempfile.mkdtemp(prefix="padel-data-")
os.environ["DATA_DIR"] = DATA

import httpx
import uvicorn
from fastmcp import Client

import server
from padel_mcp.tools import McpGate
from tools.players import mentions as _mentions

TODAY = date.today()
RANKING = [
    {"pos": 1, "name": "Gemma Triay", "pair": "", "flag": "🇪🇸", "pts": "17.840"},
    {"pos": 1, "name": "Delfina Brea", "pair": "", "flag": "🇦🇷", "pts": "17.840"},
    {"pos": 3, "name": "Ariana Sánchez", "pair": "", "flag": "🇪🇸", "pts": "13.640"},
    {"pos": 4, "name": "Ari Sánchez", "pair": "", "flag": "🇪🇸", "pts": "13.100"},
    {"pos": 5, "name": "Marta Ortega", "pair": "", "flag": "🇪🇸", "pts": "9.500"},
]
# Torneo manual (data/torneo.json con "active": true): tiene prioridad sobre el deducido del calendario.
LIVE = {
    "active": True, "name": "Torneo manual", "place": "Rotterdam", "dates": "28 Sep - 4 Oct",
    "watch": ["Movistar+"], "source": "Prueba", "source_url": "https://example.test/e", "structure": {"draw_size": 16},
    "results": [
        {"round": "R16", "winner": "Gemma Triay / Delfina Brea", "loser": "X / Y", "score": "6–2  6–3"},
        {"round": "R16", "winner": "Ari Sánchez / P. Josemaría", "loser": "Z / W", "score": "6–4  7–5"},
    ],
    "next_match": None, "current_match": None,
}


def d(days):
    return (TODAY + timedelta(days=days)).isoformat()


CALENDAR = [
    {"name": "Rotterdam P2", "place": "Rotterdam 🇳🇱", "badge": "p2", "badgeText": "P2", "tv": "Movistar+ · YouTube",
     "start": d(-1), "end": d(5)},
    {"name": "Madrid P1", "place": "Madrid 🇪🇸", "badge": "p1", "badgeText": "P1", "tv": "", "start": d(-10), "end": d(-4),
     "final_femenina": {"ganadoras": "Delfina Brea / Gemma Triay", "finalistas": "Ari Sánchez / Andrea Ustero",
                        "resultado": "6–3 / 6–4"}},
    {"name": "Valencia P1", "place": "Valencia 🇪🇸", "badge": "p1", "badgeText": "P1", "tv": "", "start": d(-40), "end": d(-34),
     "final_femenina": {"ganadoras": "Ariana Sánchez / Marta Ortega", "finalistas": "Delfina Brea / Gemma Triay",
                        "resultado": "7–5 / 6–4"}},
    {"name": "Qatar Major", "badge": "major", "badgeText": "MAJOR", "cancelado": True},
    {"name": "Dubai P1", "place": "Dubai 🇦🇪", "badge": "p1", "badgeText": "P1", "tv": "", "start": d(30), "end": d(36)},
]
NEWS = {"resumen_diario": [
    {"titular": "Delfina Brea lidera el circuito", "resumen": "La jugadora Delfina Brea sigue arriba.",
     "fecha": "2026-09-28", "fuentes": [{"domain": "medio.test", "url": "https://medio.test/a"}],
     "jugadoras": ["Delfina Brea"]},
    {"titular": "Una pareja española gana en Rotterdam", "resumen": "Victoria sin citar nombres en el texto.",
     "fecha": "2026-09-27", "fuentes": [], "jugadoras": ["Marta Ortega"]},
]}


def write_data(tipo="wikipedia", torneo=None, age_days=0, ranking=None):
    meta = {"tipo": tipo, "actualizado": d(-age_days), "fuente": "Wikipedia · Prueba", "licencia": "CC BY-SA 4.0",
            "licencia_url": "https://creativecommons.org/licenses/by-sa/4.0/", "url": "https://en.wikipedia.org/wiki/Prueba",
            "nota": f"datos de prueba ({tipo})"}
    files = {
        "ranking": {"_meta": meta, "jugadoras": ranking if ranking is not None else RANKING},
        "calendario": {"_meta": meta, "torneos": CALENDAR},
        "torneo": {"_meta": meta, **(torneo if torneo is not None else {"active": False})},
    }
    for name, doc in files.items():
        Path(DATA, f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    server._cache.clear()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AppEndToEnd(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        async def news(): return dict(NEWS)
        server.get_latest_news = news

        cls.port = _free_port()
        config = uvicorn.Config(server.app, host="127.0.0.1", port=cls.port, log_level="warning")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        cls.url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)

    def setUp(self):
        write_data("wikipedia")
        os.environ.pop("PLAYER_PHOTOS", None)

    async def call(self, tool, **args):
        async with Client(f"{self.url}/ai/mcp") as c:
            res = await c.call_tool(tool, args)
            return res.data if res.data is not None else res.structured_content

    # ── web ──────────────────────────────────────────────────────────────────
    async def test_web_keeps_its_api_shapes(self):
        async with httpx.AsyncClient() as h:
            self.assertEqual((await h.get(f"{self.url}/")).status_code, 200)
            self.assertEqual((await h.get(f"{self.url}/api/version")).status_code, 200)

            ranking = (await h.get(f"{self.url}/api/ranking")).json()
            self.assertEqual(len(ranking), len(RANKING))
            self.assertEqual(set(ranking[0]), {"pos", "name", "pair", "flag", "pts"})

            calendar = (await h.get(f"{self.url}/api/calendar")).json()
            self.assertTrue({"day", "month", "name", "place", "badge", "badgeText", "tv", "live"} <= set(calendar[0]))
            self.assertTrue(calendar[0]["live"])
            self.assertNotIn("Qatar Major", [t["name"] for t in calendar])         # cancelado: no se muestra
            self.assertNotIn("Madrid P1", [t["name"] for t in calendar])           # terminado: no se muestra

    async def test_tournament_card_answers_where_what_who_podium_and_score(self):
        r = await self.call("resultado_torneo")                       # el más reciente
        self.assertEqual(r["torneo"], "Madrid P1")
        self.assertEqual((r["donde"]["ciudad"], r["donde"]["pais"]), ("Madrid", "España"))
        self.assertEqual(r["tipo_de_torneo"]["categoria"], "P1")
        self.assertIn("segunda categoría", r["tipo_de_torneo"]["descripcion"])
        self.assertEqual(r["partido"], "Final femenina: Delfina Brea / Gemma Triay vs Ari Sánchez / Andrea Ustero")
        self.assertEqual(r["podio"]["primeras"]["jugadoras"], ["Delfina Brea", "Gemma Triay"])
        self.assertEqual(r["podio"]["segundas"]["jugadoras"], ["Ari Sánchez", "Andrea Ustero"])
        self.assertIsNone(r["podio"]["terceras"])
        self.assertIn("no los inventes", r["podio"]["nota_terceras"])
        self.assertEqual(r["resultado_final"], "6–3 / 6–4")
        self.assertIn("Valencia P1", r["otros_torneos"])
        self.assertTrue(r["datos"]["fuente"])

        v = await self.call("resultado_torneo", torneo="valencia")
        self.assertEqual((v["torneo"], v["podio"]["primeras"]["pareja"]), ("Valencia P1", "Ariana Sánchez / Marta Ortega"))

        j = await self.call("resultado_torneo", jugadora="Marta Ortega")
        self.assertEqual(j["torneo"], "Valencia P1", "la última final en la que ella jugó")

        missing = await self.call("resultado_torneo", torneo="Tokio")
        self.assertIn("error", missing)
        self.assertIn("Madrid P1", missing["torneos_disponibles"])

    async def test_server_instructions_tell_the_model_how_to_answer_a_result(self):
        async with Client(f"{self.url}/ai/mcp") as c:
            text = " ".join((c.instructions or "").split())
        for phrase in ("ranking_femenino", "ultimo_torneo", "proximo_torneo", "dónde fue", "qué tipo de torneo es",
                       "quién jugó la final", "primeras, segundas y terceras", "resultado final", "dilo sin inventarlo",
                       "NO hay marcadores en directo"):
            self.assertIn(phrase, text, phrase)

    async def test_ranking_top_n_gives_position_name_and_points(self):
        r = await self.call("ranking_femenino", top=3)
        self.assertEqual(len(r["ranking"]), 3)
        for row, expected in zip(r["ranking"], RANKING[:3]):
            self.assertEqual((row["posicion"], row["nombre"]), (expected["pos"], expected["name"]))
            self.assertEqual(row["puntos"], int(expected["pts"].replace(".", "")))
            self.assertIn("pais", row)
        self.assertTrue(r["datos"]["fuente"])
        default = await self.call("ranking_femenino")
        self.assertEqual(len(default["ranking"]), min(10, len(RANKING)), "por defecto, el top 10")

    async def test_last_tournament_answers_who_won_by_how_much_and_who_lost(self):
        r = await self.call("ultimo_torneo")
        self.assertEqual(r["torneo"], "Madrid P1")
        self.assertEqual(r["resumen_en_una_frase"],
                         "Delfina Brea / Gemma Triay ganaron el Madrid P1 (Madrid, España) a Ari Sánchez / Andrea Ustero por 6–3 / 6–4.")
        self.assertEqual((r["donde"]["ciudad"], r["donde"]["pais"]), ("Madrid", "España"))
        self.assertEqual(r["tipo_de_torneo"]["categoria"], "P1")
        self.assertTrue(r["tipo_de_torneo"]["descripcion"])
        self.assertEqual(r["podio"]["primeras"]["jugadoras"], ["Delfina Brea", "Gemma Triay"])
        self.assertEqual(r["podio"]["segundas"]["jugadoras"], ["Ari Sánchez", "Andrea Ustero"])
        self.assertEqual(r["resultado_final"], "6–3 / 6–4")
        self.assertIsNone(r["podio"]["terceras"])
        self.assertIn("no los inventes", r["podio"]["nota_terceras"])
        self.assertTrue(r["datos"]["fuente"])

    async def test_next_tournament_answers_when_where_and_what_kind(self):
        r = await self.call("proximo_torneo")
        nxt = r["proximo"]
        self.assertEqual(nxt["torneo"], "Dubai P1")
        self.assertEqual((nxt["donde"]["ciudad"], nxt["donde"]["pais"]), ("Dubai", "Emiratos Árabes Unidos"))
        self.assertEqual(nxt["dias_para_empezar"], 30)
        self.assertFalse(nxt["en_juego"])
        self.assertEqual(nxt["fechas"]["inicio"], d(30))
        self.assertEqual(nxt["tipo_de_torneo"]["categoria"], "P1")
        self.assertIn("Red Bull TV", nxt["donde_verlo"], "sin dato propio de TV se da el dato general verificado")
        live = r["en_juego_ahora"]
        self.assertEqual((live["torneo"], live["en_juego"], live["donde_verlo"]), ("Rotterdam P2", True, "Movistar+ · YouTube"))
        self.assertTrue(r["marcador_oficial"].startswith("https://"))
        self.assertTrue(r["datos"]["fuente"])

    async def test_the_explanation_page_for_everyone_is_served(self):
        async with httpx.AsyncClient() as h:
            r = await h.get(f"{self.url}/como-funciona")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/html"))
        self.assertIn("¿Cómo funciona esta web?", r.text)
        self.assertIn("<svg", r.text)

    async def test_live_tab_is_deduced_from_the_calendar_and_links_to_the_official_scoreboard(self):
        async with httpx.AsyncClient() as h:
            live = (await h.get(f"{self.url}/api/live")).json()
        self.assertTrue(live["active"])
        self.assertEqual(live["name"], "Rotterdam P2")
        self.assertEqual(live["results"], [])
        self.assertEqual(live["watch"], ["Movistar+", "YouTube"])
        self.assertTrue(live["official_url"].startswith("https://"))
        self.assertIn("Wikipedia", live["source"])

    async def test_a_manual_tournament_takes_priority_and_keeps_the_old_shape(self):
        write_data("manual", torneo=LIVE)
        async with httpx.AsyncClient() as h:
            live = (await h.get(f"{self.url}/api/live")).json()
        self.assertEqual(live["name"], "Torneo manual")
        self.assertEqual(live["structure"]["draw_size"], 16)
        self.assertEqual(len(live["results"]), 2)

    async def test_web_shows_notices_only_for_example_or_stale_data(self):
        async with httpx.AsyncClient() as h:
            s = (await h.get(f"{self.url}/api/data-status")).json()
            self.assertEqual({v["tipo"] for v in s["datasets"].values()}, {"wikipedia"})
            self.assertFalse(any(v["desactualizado"] for v in s["datasets"].values()))
            self.assertIn("ingesta", s)
            write_data("ejemplo", age_days=40)
            s = (await h.get(f"{self.url}/api/data-status")).json()
            self.assertEqual({v["tipo"] for v in s["datasets"].values()}, {"ejemplo"})
            self.assertTrue(all(v["desactualizado"] for v in s["datasets"].values()))
            self.assertIn("fuentes", s["noticias"])
















    # ── MCP ──────────────────────────────────────────────────────────────────
    async def test_tools_are_listed_and_read_only(self):
        async with Client(f"{self.url}/ai/mcp") as c:
            tools = await c.list_tools()
        names = {t.name for t in tools}
        self.assertEqual(names, {"ranking_femenino", "jugadora", "comparar_jugadoras", "torneo_actual",
                                 "resultados_recientes", "resultado_torneo", "calendario", "noticias",
                                 "resumen_del_dia", "estado_de_los_datos", "ultimo_torneo", "proximo_torneo",
                                 "perfil_jugadora", "partidos_jugadora", "cuadro_torneo", "partidos_en_directo",
                                 "rankings_jugadora", "prediccion_partido", "ranking_elo", "ranking_race",
                                 "movimientos_ranking", "parejas_jugadora", "ediciones_torneo"})
        self.assertNotIn("proximo_partido", names, "sin marcadores en directo no hay próximo partido que ofrecer")
        for t in tools:
            self.assertTrue(t.annotations and t.annotations.read_only_hint, t.name)

    async def test_ranking_and_comparison(self):
        r = await self.call("ranking_femenino", top=2)
        self.assertEqual(len(r["ranking"]), 2)
        self.assertEqual(r["ranking"][0]["puntos"], 17840)
        self.assertEqual(r["datos"]["tipo_de_datos"], "wikipedia")
        self.assertEqual(r["datos"]["fuente"], "Wikipedia · Prueba")
        self.assertEqual(r["datos"]["licencia"], "CC BY-SA 4.0")
        self.assertNotIn("aviso", r["datos"])
        self.assertIsNotNone(r["datos"]["antiguedad_segundos"])
        c = await self.call("comparar_jugadoras", jugadora_a="Gemma Triay", jugadora_b="Marta Ortega")
        self.assertEqual(c["diferencia_puntos"], 8340)

    async def test_stale_data_is_flagged_in_every_answer(self):
        write_data("wikipedia", age_days=45)
        for tool in ("ranking_femenino", "calendario", "torneo_actual", "resultados_recientes"):
            r = await self.call(tool)
            self.assertIn("Datos con retraso", r["datos"]["aviso"], tool)

    async def test_jugadora_card_has_her_finals_and_her_news(self):
        r = await self.call("jugadora", nombre="Delfina Brea")
        self.assertTrue(r["encontrada"])
        self.assertEqual(len(r["finales"]["lista"]), 2)                     # jugó las dos
        self.assertEqual(len(r["noticias"]), 1)
        self.assertEqual(r["noticias"][0]["fuentes"][0]["url"], "https://medio.test/a")
        r = await self.call("jugadora", nombre="Marta Ortega")
        self.assertEqual([f["torneo"] for f in r["finales"]["lista"]], ["Valencia P1"])

    async def test_unknown_and_ambiguous_players(self):
        r = await self.call("jugadora", nombre="Persona Inventada")
        self.assertFalse(r["encontrada"])
        r = await self.call("jugadora", nombre="Sánchez")
        self.assertFalse(r["encontrada"])
        self.assertEqual(len(r["candidatas"]), 2)

    async def test_recent_finals_newest_first_and_same_surname_players_are_not_mixed_up(self):
        r = await self.call("resultados_recientes")
        self.assertEqual([f["torneo"] for f in r["finales"]], ["Madrid P1", "Valencia P1"])
        self.assertEqual(r["finales"][0]["resultado"], "6–3 / 6–4")
        r = await self.call("resultados_recientes", jugadora="Ari Sánchez")
        self.assertEqual([f["torneo"] for f in r["finales"]], ["Madrid P1"])
        r = await self.call("resultados_recientes", jugadora="Ariana Sánchez")
        self.assertEqual([f["torneo"] for f in r["finales"]], ["Valencia P1"])
        self.assertEqual((await self.call("resultados_recientes", limite=1))["finales"][0]["torneo"], "Madrid P1")

    async def test_current_tournament_links_to_the_official_scoreboard_and_shows_no_scores(self):
        t = await self.call("torneo_actual")
        self.assertTrue(t["activo"])
        self.assertEqual(t["nombre"], "Rotterdam P2")
        self.assertTrue(t["marcador_oficial"].startswith("https://"))
        self.assertEqual(t["resultados"], [])
        self.assertIn("no", t["mensaje"].lower())

    async def test_calendar_news_and_daily_summary(self):
        cal = await self.call("calendario")
        self.assertEqual([t["torneo"] for t in cal["torneos"]], ["Rotterdam P2", "Dubai P1"])
        self.assertTrue(cal["torneos"][0]["en_juego"])
        self.assertEqual(len((await self.call("noticias"))["noticias"]), 2)
        self.assertEqual(len((await self.call("noticias", jugadora="Delfina Brea"))["noticias"]), 1)
        r = await self.call("noticias", jugadora="Marta Ortega")       # filtra por etiquetas, no por texto
        self.assertEqual(r["noticias"][0]["jugadoras"], ["Marta Ortega"])
        s = await self.call("resumen_del_dia")
        self.assertEqual(len(s["top5"]["jugadoras"]), 5)
        self.assertEqual(s["torneo"]["nombre"], "Rotterdam P2")
        self.assertEqual(s["ultima_final"]["torneo"], "Madrid P1")
        self.assertEqual(len(s["noticias"]), 2)

    async def test_example_data_is_flagged_in_every_tool(self):
        write_data("ejemplo")
        r = await self.call("ranking_femenino", top=3)
        self.assertEqual(r["datos"]["tipo_de_datos"], "ejemplo")
        self.assertIn("aviso", r["datos"])
        self.assertIn("aviso", (await self.call("torneo_actual"))["datos"])
        self.assertIn("aviso", (await self.call("calendario"))["datos"])

    async def test_data_status_tool_includes_the_last_ingestion(self):
        s = await self.call("estado_de_los_datos")
        self.assertEqual(s["datasets"]["ranking"]["tipo"], "wikipedia")
        self.assertEqual(s["datasets"]["ranking"]["licencia"], "CC BY-SA 4.0")
        self.assertIn("ingesta", s)
        self.assertIn("activo", s["noticias"])

    async def test_broken_or_missing_data_is_reported_not_raised(self):
        Path(DATA, "torneo.json").write_text("{esto no es json", encoding="utf-8")
        server._cache.clear()
        t = await self.call("torneo_actual")
        self.assertTrue(t["activo"], "con el archivo manual roto se sigue deduciendo del calendario")
        Path(DATA, "calendario.json").unlink()
        server._cache.clear()
        self.assertFalse((await self.call("torneo_actual"))["activo"])
        self.assertEqual((await self.call("resultados_recientes"))["finales"], [])
        Path(DATA, "ranking.json").unlink()
        server._cache.clear()
        r = await self.call("ranking_femenino")
        self.assertEqual(r["ranking"], [])
        self.assertIn("aviso", r["datos"])
        s = await self.call("estado_de_los_datos")
        self.assertFalse(s["datasets"]["ranking"]["disponible"])

    async def test_source_failure_is_reported_not_raised(self):
        real = server.get_tournament_now
        async def boom(gender="female"): raise RuntimeError("fuente caída")
        server.get_tournament_now = boom
        try:
            self.assertIn("error", await self.call("torneo_actual"))
        finally:
            server.get_tournament_now = real
            server._cache.clear()

    async def test_works_behind_a_proxy_with_a_foreign_host_header(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "1"}}}
        async with httpx.AsyncClient() as h:
            r = await h.post(f"{self.url}/ai/mcp", json=body, headers={
                "Host": "padel-fem.up.railway.app", "Accept": "application/json, text/event-stream",
                "X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(r.status_code, 200, r.text[:200])


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, gate, headers=None, ip="1.2.3.4"):
        sent = []
        async def send(msg): sent.append(msg)
        async def receive(): return {"type": "http.request", "body": b""}
        scope = {"type": "http", "headers": headers or [], "client": (ip, 1)}
        await gate(scope, receive, send)
        return sent[0]["status"]

    def _inner(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        return app

    async def test_token_mode(self):
        gate = McpGate(self._inner(), token="secreto", per_ip=100, global_limit=100)
        self.assertEqual(await self._run(gate), 401)
        self.assertEqual(await self._run(gate, [(b"authorization", b"Bearer mal")]), 401)
        self.assertEqual(await self._run(gate, [(b"authorization", b"Bearer secreto")]), 200)

    async def test_open_mode_rate_limits_per_ip(self):
        gate = McpGate(self._inner(), token="", per_ip=3, global_limit=100)
        self.assertEqual([await self._run(gate) for _ in range(4)], [200, 200, 200, 429])
        self.assertEqual(await self._run(gate, ip="9.9.9.9"), 200)

    async def test_global_limit_and_forwarded_ip(self):
        gate = McpGate(self._inner(), token="", per_ip=100, global_limit=2)
        hdr = [(b"x-forwarded-for", b"5.5.5.5, 10.0.0.1")]
        self.assertEqual([await self._run(gate, hdr), await self._run(gate, hdr, ip="7.7.7.7"),
                          await self._run(gate, hdr, ip="8.8.8.8")], [200, 200, 429])


class GateConfigTests(unittest.TestCase):
    def test_empty_or_invalid_env_values_fall_back_to_defaults(self):
        keys = ("MCP_RATE_PER_MIN", "MCP_GLOBAL_RATE_PER_MIN")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            for value, expected in (("", (120, 600)), ("   ", (120, 600)), ("abc", (120, 600)), ("7", (7, 7))):
                os.environ["MCP_RATE_PER_MIN"] = value
                os.environ["MCP_GLOBAL_RATE_PER_MIN"] = value
                gate = McpGate(None)
                self.assertEqual((gate.per_ip, gate.global_limit), expected, repr(value))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class MentionTests(unittest.TestCase):
    def test_surname_rules(self):
        ari, ariana = RANKING[3], RANKING[2]
        self.assertTrue(_mentions("Ari Sánchez gana", ari, RANKING))
        self.assertFalse(_mentions("Ari Sánchez gana", ariana, RANKING))
        self.assertFalse(_mentions("Sánchez / Ortega", ari, RANKING))
        self.assertFalse(_mentions("A. Sánchez / Ortega", ari, RANKING))      # misma inicial: ambiguo
        self.assertTrue(_mentions("Ortega gana", RANKING[4], RANKING))         # apellido único


if __name__ == "__main__":
    unittest.main()


class CoreToolsAreTheDefault(unittest.IsolatedAsyncioTestCase):
    """Por defecto el MCP expone solo las tres herramientas; las demás se activan con MCP_TOOLS=extended."""

    @staticmethod
    def providers():
        async def empty():
            return {"data": [], "age_s": 0}
        return {k: empty for k in ("ranking", "calendar", "live", "finals", "news", "status")}

    async def names(self, **kw):
        from fastmcp import Client as C
        from padel_mcp.tools import build_mcp
        async with C(build_mcp(self.providers(), **kw)) as c:
            return {t.name for t in await c.list_tools()}, c

    async def test_default_is_exactly_the_three_core_tools(self):
        from padel_mcp.tools import CORE_TOOLS
        names, _ = await self.names(extended=False)
        self.assertEqual(names, {"ranking_femenino", "ultimo_torneo", "proximo_torneo"})
        self.assertEqual(names, set(CORE_TOOLS))

    async def test_an_extra_tool_is_hidden_and_cannot_be_called(self):
        from fastmcp import Client as C
        from padel_mcp.tools import build_mcp
        async with C(build_mcp(self.providers(), extended=False)) as c:
            with self.assertRaises(Exception):
                await c.call_tool("noticias", {})

    async def test_core_plus_extra_are_all_the_tools_there_are(self):
        from padel_mcp.tools import CORE_TOOLS, EXTRA_TOOLS
        names, _ = await self.names(extended=True)
        self.assertEqual(names, set(CORE_TOOLS) | set(EXTRA_TOOLS))
        self.assertEqual(len(names), len(CORE_TOOLS) + len(EXTRA_TOOLS), "sin nombres repetidos entre los dos grupos")

    async def test_the_environment_switch_decides_when_not_told(self):
        saved = os.environ.get("MCP_TOOLS")
        try:
            os.environ.pop("MCP_TOOLS", None)
            self.assertEqual(len((await self.names())[0]), 3)
            for value in ("extended", "EXTENDED", " extended "):
                os.environ["MCP_TOOLS"] = value
                self.assertEqual(len((await self.names())[0]), 23, value)
            os.environ["MCP_TOOLS"] = "todas"
            self.assertEqual(len((await self.names())[0]), 3, "un valor desconocido no abre nada")
        finally:
            os.environ.pop("MCP_TOOLS", None) if saved is None else os.environ.__setitem__("MCP_TOOLS", saved)
