"""
Las siete herramientas que consultan a Padel API en el momento (tools/padelapi_live.py), sin internet.
Las respuestas simuladas tienen la forma de la documentación oficial (PlayerResource, MatchResource, rankings...).
Se comprueba, sobre todo, que NO se satura a Padel API (caché, una petición por dato, presupuesto) y que lo que el plan
gratuito no incluye (402, hidden_free_plan) se explica en vez de fallar.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
os.environ.setdefault("MCP_TOOLS", "extended")
os.environ["WIKIPEDIA_INGEST"] = "off"

import httpx

from tools import local_data, padelapi_live as live

TODAY = date(2026, 10, 20)
HIDDEN = "hidden_free_plan"


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class Api:
    """Padel API de mentira: rutas (método, ruta) -> cuerpo | (estado, cuerpo) | función(request)."""

    def __init__(self, routes=None):
        self.calls, self.routes = [], routes or {}

    def handler(self, request):
        path = request.url.path.replace("/api", "", 1)
        self.calls.append({"method": request.method, "path": path, "params": dict(request.url.params),
                           "headers": dict(request.headers), "json": json.loads(request.content) if request.content else None})
        route = self.routes.get((request.method, path))
        if callable(route):
            return route(request)
        if route is None:
            return httpx.Response(404, json={"message": "no existe"})
        status, body = route if isinstance(route, tuple) else (200, route)
        return httpx.Response(status, json=body)

    def factory(self):
        return lambda: live.new_http(httpx.MockTransport(self.handler))

    def paths(self):
        return [c["path"] for c in self.calls]


class SlowApi(Api):
    """Cada respuesta tarda un instante: así las preguntas simultáneas se solapan de verdad."""

    def factory(self):
        async def handler(request):
            await asyncio.sleep(0.02)
            return self.handler(request)
        return lambda: live.new_http(httpx.MockTransport(handler))


def player(pid, name, **kw):
    base = {"id": pid, "self": f"/api/players/{pid}", "name": name, "short_name": name.split()[1], "url": None, "photo_url": None,
            "category": "women", "nationality": "ES", "ranking": "1", "points": 17840, "elo": 1650, "height": 170,
            "side": "drive", "hand": "right", "birthplace": "Madrid", "birthdate": "1996-03-02", "age": "30",
            "updated_at": "2026-10-19T10:00:00Z", "connections": {}}
    base.update(kw)
    return base


def team(*people):
    return [{"id": pid, "self": f"/api/players/{pid}", "name": name, "side": "drive", "connections": ""} for pid, name in people]


TRIAY = (3, "Gemma Triay Pons")
BREA = (4, "Delfina Brea Senesi")
SANCHEZ = (5, "Ariana Sanchez Fallada")
GONZALEZ = (6, "Beatriz Gonzalez Fernandez")


def match(mid, round_no, t1, t2, winner="team_1", score=None, status="finished", played="2026-10-18", **kw):
    base = {"id": mid, "name": "x", "category": "women", "draw": "main", "round": round_no, "round_name": "R", "index": 0,
            "played_at": played, "status": status, "winner": winner,
            "score": score if score is not None else [{"team_1": "6", "team_2": "3"}, {"team_1": "7", "team_2": "5"}],
            "players": {"team_1": team(*t1), "team_2": team(*t2)}, "court": None, "schedule_label": None,
            "connections": {"tournament": "/api/tournaments/101"}}
    base.update(kw)
    return base


class WithEnv(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("PADELAPI_TOKEN", "PADELAPI_LIVE_PER_MIN", "PADELAPI_LIVE_PER_DAY", "DATA_DIR")}
        os.environ["PADELAPI_TOKEN"] = "token-de-prueba"
        os.environ.pop("PADELAPI_LIVE_PER_MIN", None)
        os.environ.pop("PADELAPI_LIVE_PER_DAY", None)
        self.dir = tempfile.mkdtemp(prefix="live-")
        os.environ["DATA_DIR"] = self.dir
        self.write("ranking.json", {"_meta": {"tipo": "api", "actualizado": "2026-10-19"}, "jugadoras": [
            {"pos": 1, "name": "Gemma Triay", "pair": "", "flag": "🇪🇸", "pts": "17.840", "id": 3},
            {"pos": 2, "name": "Delfina Brea", "pair": "", "flag": "🇦🇷", "pts": "17.000", "id": 4},
            {"pos": 3, "name": "Ariana Sanchez", "pair": "", "flag": "🇪🇸", "pts": "16.000", "id": 5},
            {"pos": 4, "name": "Beatriz Gonzalez", "pair": "", "flag": "🇪🇸", "pts": "15.000", "id": 6},
            {"pos": 5, "name": "Ariana Perez", "pair": "", "flag": "🇪🇸", "pts": "14.000", "id": 8},
            {"pos": 6, "name": "Sin Identificador", "pair": "", "flag": "🇪🇸", "pts": "13.000"}]})
        self.write("calendario.json", {"_meta": {"tipo": "api", "actualizado": "2026-10-20"}, "torneos": [
            {"name": "Madrid P1", "place": "Madrid 🇪🇸", "start": "2026-05-11", "end": "2026-05-17", "badge": "p1", "badgeText": "P1", "padelapi_id": 101},
            {"name": "Milano Major", "place": "Milano 🇮🇹", "start": "2026-09-14", "end": "2026-09-20", "badge": "major", "badgeText": "Major", "padelapi_id": 102},
            {"name": "Rotterdam P1", "place": "Rotterdam 🇳🇱", "start": "2026-10-19", "end": "2026-10-25", "badge": "p1", "badgeText": "P1", "padelapi_id": 103},
            {"name": "Sin Identificador Open", "place": "Roma", "start": "2026-11-01", "end": "2026-11-07", "badge": "p2", "badgeText": "P2"},
            {"name": "Cancelado P2", "place": "Lima", "start": "2026-11-10", "end": "2026-11-16", "badgeText": "P2", "padelapi_id": 105, "cancelado": True},
            {"name": "Paris Major", "place": "Paris 🇫🇷", "start": "2026-11-23", "end": "2026-11-29", "badge": "major", "badgeText": "Major", "padelapi_id": 106}]})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def write(self, name, doc):
        Path(self.dir, name).write_text(json.dumps(doc), encoding="utf-8")


def make_client(api, clock=None, today=TODAY):
    clock = clock or Clock()
    return live.Client(http_factory=api.factory(), sleep=clock.sleep, clock=clock, today=lambda: today), clock


def make_service(api, clock=None, today=TODAY):
    client, clock = make_client(api, clock, today)
    return live.Service(client, today=lambda: today), clock


# ───────────────────────────── cliente ─────────────────────────────────────────

class ClientSendsOnlyWhatItNeeds(WithEnv, unittest.IsolatedAsyncioTestCase):
    async def test_request_shape_and_identification(self):
        api = Api({("GET", "/players/3"): {"data": player(3, TRIAY[1])}})
        client, _ = make_client(api)
        body = await client.get("/players/3", {"a": "1"})
        self.assertEqual(body["data"]["id"], 3)
        call = api.calls[0]
        self.assertEqual((call["method"], call["path"], call["params"]), ("GET", "/players/3", {"a": "1"}))
        self.assertEqual(call["headers"]["authorization"], "Bearer token-de-prueba")
        self.assertTrue(call["headers"]["user-agent"].startswith("PadelFemBot/"))

    async def test_post_sends_json(self):
        api = Api({("POST", "/matches/simulate"): {"probability": {"team_1": 0.5, "team_2": 0.5}}})
        client, _ = make_client(api)
        await client.post("/matches/simulate", {"team_1": [3], "team_2": [4]})
        self.assertEqual(api.calls[0]["json"], {"team_1": [3], "team_2": [4]})

    async def test_without_a_token_nothing_is_sent(self):
        os.environ.pop("PADELAPI_TOKEN", None)
        api = Api()
        client, _ = make_client(api)
        with self.assertRaises(live.LiveError) as cm:
            await client.get("/players/3")
        self.assertEqual(cm.exception.kind, "config")
        self.assertEqual(api.calls, [])


class ClientDoesNotSaturatePadelApi(WithEnv, unittest.IsolatedAsyncioTestCase):
    async def test_the_same_request_is_served_from_cache_until_it_expires(self):
        api = Api({("GET", "/players/3"): {"data": {}}})
        client, clock = make_client(api)
        for _ in range(4):
            await client.get("/players/3", None, ttl=100)
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(client.from_cache, 3)
        clock.t += 101
        await client.get("/players/3", None, ttl=100)
        self.assertEqual(len(api.calls), 2)

    async def test_different_parameters_are_different_requests(self):
        api = Api({("GET", "/players/3/matches"): {"data": []}})
        client, _ = make_client(api)
        await client.get("/players/3/matches", {"per_page": 5})
        await client.get("/players/3/matches", {"per_page": 6})
        await client.get("/players/3/matches", {"per_page": 5})
        self.assertEqual(len(api.calls), 2)

    async def test_simultaneous_questions_for_the_same_data_make_one_request(self):
        api = SlowApi({("GET", "/players/3"): {"data": {}}})
        client, _ = make_client(api)
        results = await asyncio.gather(*[client.get("/players/3", None, ttl=60) for _ in range(8)])
        self.assertEqual(len(api.calls), 1)
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(client.status()["peticiones_hoy"], 1, "y solo se gasta una del presupuesto")

    async def test_different_data_at_the_same_time_is_not_serialised_into_one(self):
        api = SlowApi({("GET", f"/p/{i}"): {"data": {}} for i in range(3)})
        client, _ = make_client(api)
        await asyncio.gather(*[client.get(f"/p/{i}") for i in range(3)])
        self.assertEqual(sorted(api.paths()), ["/p/0", "/p/1", "/p/2"])

    async def test_the_per_minute_budget_is_respected_by_waiting_a_little(self):
        os.environ["PADELAPI_LIVE_PER_MIN"] = "3"
        api = Api({("GET", f"/p/{i}"): {"data": {}} for i in range(10)})
        client, clock = make_client(api)
        for i in range(3):
            await client.get(f"/p/{i}")
        clock.t += 50                                       # quedan 10 s para que caduque la primera
        await client.get("/p/3")
        self.assertEqual(len(api.calls), 4)
        self.assertEqual(len(clock.slept), 1)
        self.assertAlmostEqual(clock.slept[0], 10.05, places=1)

    async def test_if_the_wait_is_too_long_it_asks_to_retry_instead_of_blocking(self):
        os.environ["PADELAPI_LIVE_PER_MIN"] = "2"
        api = Api({("GET", f"/p/{i}"): {"data": {}} for i in range(10)})
        client, clock = make_client(api)
        await client.get("/p/0")
        await client.get("/p/1")
        with self.assertRaises(live.LiveError) as cm:
            await client.get("/p/2")
        self.assertEqual(cm.exception.kind, "budget")
        self.assertGreater(cm.exception.retry_after, live.MAX_WAIT)
        self.assertEqual(len(api.calls), 2, "no se envió")
        self.assertEqual(clock.slept, [])
        clock.t += 61
        await client.get("/p/2")
        self.assertEqual(len(api.calls), 3)

    async def test_the_daily_budget_stops_everything_and_resets_the_next_day(self):
        os.environ["PADELAPI_LIVE_PER_DAY"] = "2"
        os.environ["PADELAPI_LIVE_PER_MIN"] = "50"
        api = Api({("GET", f"/p/{i}"): {"data": {}} for i in range(10)})
        day = {"d": TODAY}
        clock = Clock()
        client = live.Client(http_factory=api.factory(), sleep=clock.sleep, clock=clock, today=lambda: day["d"])
        await client.get("/p/0")
        await client.get("/p/1")
        with self.assertRaises(live.LiveError) as cm:
            await client.get("/p/2")
        self.assertIn("límite diario", cm.exception.message)
        self.assertEqual(client.status()["peticiones_hoy"], 2)
        day["d"] = date(2026, 10, 21)
        await client.get("/p/2")
        self.assertEqual(len(api.calls), 3)

    async def test_the_cache_does_not_spend_the_budget(self):
        os.environ["PADELAPI_LIVE_PER_DAY"] = "1"
        api = Api({("GET", "/p/0"): {"data": {}}})
        client, _ = make_client(api)
        for _ in range(5):
            await client.get("/p/0", None, ttl=600)
        self.assertEqual((len(api.calls), client.status()["peticiones_hoy"]), (1, 1))

    async def test_the_defaults_are_well_below_the_free_plan_limits(self):
        self.assertEqual(live.Client.limits(), (5, 600))
        self.assertLess(live.DEFAULT_PER_MINUTE, 10)
        self.assertLess(live.DEFAULT_PER_DAY, 2000)

    async def test_bad_values_in_the_environment_fall_back_to_the_defaults(self):
        os.environ["PADELAPI_LIVE_PER_MIN"] = "muchas"
        os.environ["PADELAPI_LIVE_PER_DAY"] = ""
        self.assertEqual(live.Client.limits(), (5, 600))
        os.environ["PADELAPI_LIVE_PER_MIN"] = "0"
        self.assertEqual(live.Client.limits()[0], 1, "nunca cero: se bloquearía todo")


class ClientExplainsErrors(WithEnv, unittest.IsolatedAsyncioTestCase):
    CASES = {401: "auth", 402: "plan", 403: "plan", 404: "notfound", 422: "invalid", 429: "limit", 500: "upstream",
             502: "upstream", 400: "upstream"}

    async def test_each_status_maps_to_a_clear_kind(self):
        for status, kind in self.CASES.items():
            api = Api({("GET", "/x"): (status, {"message": "m"})})
            client, _ = make_client(api)
            with self.assertRaises(live.LiveError) as cm:
                await client.get("/x")
            self.assertEqual(cm.exception.kind, kind, status)
            self.assertTrue(cm.exception.message and "token-de-prueba" not in cm.exception.message)

    async def test_429_reports_how_long_to_wait(self):
        api = Api({("GET", "/x"): lambda r: httpx.Response(429, headers={"Retry-After": "42"}, json={})})
        client, _ = make_client(api)
        with self.assertRaises(live.LiveError) as cm:
            await client.get("/x")
        self.assertEqual((cm.exception.kind, cm.exception.retry_after), ("limit", 42.0))
        self.assertEqual(live._err(cm.exception)["reintentar_en_segundos"], 42)

    async def test_odd_answers_and_network_failures(self):
        for route, kind in ((lambda r: httpx.Response(200, content=b"no es json"), "upstream"),
                            (lambda r: httpx.Response(200, json="texto"), "upstream"),
                            (lambda r: (_ for _ in ()).throw(httpx.ConnectError("sin red")), "network")):
            api = Api({("GET", "/x"): route})
            client, _ = make_client(api)
            with self.assertRaises(live.LiveError) as cm:
                await client.get("/x")
            self.assertEqual(cm.exception.kind, kind)

    async def test_what_the_plan_does_not_include_is_remembered_and_not_asked_again(self):
        api = Api({("GET", "/players/3/stats"): (402, {"message": "Subscription required."})})
        client, clock = make_client(api)
        for _ in range(4):
            with self.assertRaises(live.LiveError):
                await client.get("/players/3/stats")
        self.assertEqual(len(api.calls), 1)
        clock.t += live.PLAN_TTL + 1
        with self.assertRaises(live.LiveError):
            await client.get("/players/3/stats")
        self.assertEqual(len(api.calls), 2, "pasados 30 min se vuelve a comprobar por si se mejoró el plan")

    async def test_a_missing_item_is_remembered_briefly_but_transient_errors_are_not(self):
        api = Api({("GET", "/a"): (404, {}), ("GET", "/b"): (500, {})})
        client, _ = make_client(api)
        for path, calls in (("/a", 1), ("/b", 3)):
            for _ in range(3):
                with self.assertRaises(live.LiveError):
                    await client.get(path)
            self.assertEqual(api.paths().count(path), calls, path)

    async def test_the_status_counts_requests_and_cache_hits(self):
        api = Api({("GET", "/p"): {"data": {}}})
        client, _ = make_client(api)
        await client.get("/p")
        await client.get("/p")
        st = client.status()
        self.assertEqual((st["peticiones_totales"], st["respuestas_de_cache"], st["activo"]), (1, 1, True))
        self.assertEqual((st["limite_por_minuto"], st["limite_por_dia"]), (5, 600))


# ───────────────────────── datos locales ───────────────────────────────────────

class LocalHelpers(WithEnv):
    def test_player_ids_only_lists_players_that_have_one(self):
        self.assertEqual([p["id"] for p in local_data.player_ids()], [3, 4, 5, 6, 8])

    def test_tournament_refs_skip_cancelled_and_idless_and_are_sorted(self):
        self.assertEqual([r["id"] for r in local_data.tournament_refs()], [101, 102, 103, 106])

    def test_pick_tournament_without_text(self):
        refs = local_data.tournament_refs()
        self.assertEqual(local_data.pick_tournament(refs, None, TODAY)[0]["name"], "Rotterdam P1")          # en juego
        self.assertEqual(local_data.pick_tournament(refs, None, date(2026, 10, 1))[0]["name"], "Milano Major")   # último terminado
        self.assertEqual(local_data.pick_tournament(refs, None, date(2026, 1, 1))[0], None)
        self.assertEqual(local_data.pick_tournament([], None, TODAY), (None, []))

    def test_pick_tournament_with_text(self):
        refs = local_data.tournament_refs()
        self.assertEqual(local_data.pick_tournament(refs, "madrid", TODAY)[0]["id"], 101)
        self.assertEqual(local_data.pick_tournament(refs, "MILANO major", TODAY)[0]["id"], 102)
        self.assertEqual(local_data.pick_tournament(refs, "París", TODAY)[0]["id"], 106)
        self.assertEqual(local_data.pick_tournament(refs, "p1", TODAY)[0]["id"], 103, "el más reciente ya jugado o en juego")
        self.assertEqual(local_data.pick_tournament(refs, "inexistente", TODAY), (None, []))

    def test_files_that_are_missing_or_broken_give_empty_lists(self):
        Path(self.dir, "ranking.json").write_text("{roto", encoding="utf-8")
        Path(self.dir, "calendario.json").unlink()
        self.assertEqual((local_data.player_ids(), local_data.tournament_refs()), ([], []))


# ───────────────────── resolver jugadoras ──────────────────────────────────────

class ResolvePlayers(WithEnv, unittest.IsolatedAsyncioTestCase):
    async def test_the_saved_top_20_needs_no_request(self):
        api = Api()
        svc, _ = make_service(api)
        got, err = await svc.resolve("Gemma Triay")
        self.assertEqual((got["id"], err, api.calls), (3, None, []))
        got, _ = await svc.resolve("triay")
        self.assertEqual(got["id"], 3)

    async def test_an_ambiguous_name_asks_for_more(self):
        svc, _ = make_service(Api())
        got, err = await svc.resolve("Ariana")
        self.assertIsNone(got)
        self.assertEqual(sorted(err["candidatas"]), ["Ariana Perez", "Ariana Sanchez"])

    async def test_empty_name(self):
        got, err = await make_service(Api())[0].resolve("   ")
        self.assertEqual((got, err["tipo"]), (None, "invalid"))

    async def test_a_player_outside_the_top_20_is_searched_in_the_api_women_only(self):
        api = Api({("GET", "/players"): {"data": [player(77, "Jimena Velasco Gil"), player(78, "Jimena Velasco Gil", category="men")]}})
        svc, _ = make_service(api)
        got, err = await svc.resolve("Jimena Velasco")
        self.assertEqual((got["id"], got["name"], err), (77, "Jimena Velasco", None))
        self.assertEqual(api.calls[0]["params"], {"name": "Jimena Velasco", "category": "women", "per_page": "10"})

    async def test_several_candidates_are_narrowed_by_the_words_or_listed(self):
        rows = [player(1, "Raquel Eugenio Gil"), player(2, "Raquel Perez Gil")]
        svc, _ = make_service(Api({("GET", "/players"): {"data": rows}}))
        got, _ = await svc.resolve("Raquel Eugenio")
        self.assertEqual(got["id"], 1)
        got, err = await svc.resolve("Raquel")
        self.assertIsNone(got)
        self.assertEqual(sorted(err["candidatas"]), ["Raquel Eugenio", "Raquel Perez"])

    async def test_nobody_found_and_api_errors(self):
        svc, _ = make_service(Api({("GET", "/players"): {"data": []}}))
        got, err = await svc.resolve("Nadie Conocida")
        self.assertEqual((got, err["tipo"]), (None, "notfound"))
        svc, _ = make_service(Api({("GET", "/players"): (500, {})}))
        got, err = await svc.resolve("Otra Cualquiera")
        self.assertEqual(err["tipo"], "upstream")

    async def test_men_are_never_returned(self):
        svc, _ = make_service(Api({("GET", "/players"): {"data": [player(9, "Arturo Coello Gomez", category="men")]}}))
        got, err = await svc.resolve("Arturo Coello")
        self.assertEqual((got, err["tipo"]), (None, "notfound"))


# ───────────────────── los siete constructores ─────────────────────────────────

class Perfil(WithEnv, unittest.IsolatedAsyncioTestCase):
    async def test_profile(self):
        api = Api({("GET", "/players/3"): {"data": player(3, TRIAY[1], elo=1712, height=168, side="backhand", hand="left")}})
        svc, _ = make_service(api)
        out = await svc.perfil("Gemma Triay")
        p = out["jugadora"]
        self.assertEqual((p["nombre"], p["nombre_completo"], p["elo"], p["altura_cm"], p["lado"], p["mano"]),
                         ("Gemma Triay", "Gemma Triay Pons", 1712, 168, "izquierda (revés)", "zurda"))
        self.assertEqual((p["posicion"], p["puntos"], p["edad"], p["lugar_nacimiento"]), ("1", 17840, "30", "Madrid"))
        self.assertIn("🇪🇸", p["pais"])
        self.assertIn("consultada en directo", out["datos"]["fuente"])
        self.assertEqual(api.paths(), ["/players/3"])

    async def test_missing_fields_stay_empty_and_a_man_is_refused(self):
        api = Api({("GET", "/players/3"): {"data": player(3, TRIAY[1], side=None, hand=None, height=None, elo=None)}})
        out = await make_service(api)[0].perfil("Gemma Triay")
        self.assertEqual((out["jugadora"]["lado"], out["jugadora"]["mano"], out["jugadora"]["altura_cm"]), (None, None, None))
        api = Api({("GET", "/players/3"): {"data": player(3, TRIAY[1], category="men")}})
        out = await make_service(api)[0].perfil("Gemma Triay")
        self.assertEqual(out["tipo"], "invalid")
        self.assertIn("femenino", out["error"])

    async def test_a_bare_resource_without_data_also_works(self):
        api = Api({("GET", "/players/3"): player(3, TRIAY[1])})
        self.assertEqual((await make_service(api)[0].perfil("Gemma Triay"))["jugadora"]["nombre"], "Gemma Triay")

    async def test_errors_are_returned_not_raised(self):
        out = await make_service(Api({("GET", "/players/3"): (404, {})}))[0].perfil("Gemma Triay")
        self.assertEqual(out["tipo"], "notfound")


class Partidos(WithEnv, unittest.IsolatedAsyncioTestCase):
    def api(self, rows):
        return Api({("GET", "/players/3/matches"): {"data": rows}})

    async def test_matches_from_the_players_point_of_view(self):
        rows = [match(1, 1, [TRIAY, BREA], [SANCHEZ, GONZALEZ], "team_1"),
                match(2, 2, [SANCHEZ, GONZALEZ], [TRIAY, BREA], "team_1", played="2026-10-17"),
                match(3, 4, [TRIAY, BREA], [SANCHEZ, GONZALEZ], "team_2", status="retired", played="2026-10-16")]
        api = self.api(rows)
        out = await make_service(api)[0].partidos("Gemma Triay", 3)
        v = out["partidos"]
        self.assertEqual([x["gano"] for x in v], [True, False, False])
        self.assertEqual(v[0]["pareja"], "Gemma Triay / Delfina Brea")
        self.assertEqual(v[0]["rival"], "Ariana Sanchez / Beatriz Gonzalez")
        self.assertEqual((v[0]["ronda"], v[1]["ronda"], v[2]["ronda"]), ("Final", "Semifinales", "Cuartos de final"))
        self.assertEqual(v[0]["resultado"], "6-3 7-5")
        self.assertTrue(v[2]["resultado"].endswith("(retirada)"))
        self.assertEqual(v[0]["torneo"], "Madrid P1", "el nombre sale del calendario guardado")
        self.assertEqual(api.calls[0]["params"], {"sort_by": "played_at", "order_by": "desc", "per_page": "3"})

    async def test_hidden_results_are_explained(self):
        rows = [match(1, 1, [TRIAY, BREA], [SANCHEZ, GONZALEZ], HIDDEN, HIDDEN)]
        v = (await make_service(self.api(rows))[0].partidos("Gemma Triay"))["partidos"][0]
        self.assertIsNone(v["resultado"])
        self.assertIn("6 meses", v["nota"])
        self.assertNotIn("gano", {k: x for k, x in v.items() if x is not None and k == "gano" and x in (True, False)})

    async def test_the_limit_is_clamped_and_men_are_dropped(self):
        rows = [match(i, 1, [TRIAY, BREA], [SANCHEZ, GONZALEZ]) for i in range(1, 4)] + [match(9, 1, [TRIAY, BREA], [SANCHEZ, GONZALEZ], category="men")]
        for asked, sent in ((0, "1"), (99, "10"), (-5, "1"), (4, "4")):
            api = self.api(rows)
            out = await make_service(api)[0].partidos("Gemma Triay", asked)
            self.assertEqual(api.calls[0]["params"]["per_page"], sent, asked)
        self.assertEqual(len(out["partidos"]), 3)

    async def test_no_matches(self):
        out = await make_service(self.api([]))[0].partidos("Gemma Triay")
        self.assertEqual(out["partidos"], [])
        self.assertIn("mensaje", out)

    async def test_the_second_identical_question_uses_the_cache(self):
        api = self.api([match(1, 1, [TRIAY, BREA], [SANCHEZ, GONZALEZ])])
        svc, _ = make_service(api)
        await svc.partidos("Gemma Triay", 5)
        await svc.partidos("Gemma Triay", 5)
        self.assertEqual(len(api.calls), 1)


class Cuadro(WithEnv, unittest.IsolatedAsyncioTestCase):
    ROWS = [match(11, 4, [TRIAY, BREA], [SANCHEZ, GONZALEZ], "team_1", round_name="Quarter"),
            match(12, 1, [TRIAY, BREA], [SANCHEZ, GONZALEZ], "team_1"),
            match(13, 2, [SANCHEZ, GONZALEZ], [TRIAY, BREA], "team_2"),
            match(14, 8, [TRIAY, BREA], [SANCHEZ, GONZALEZ], "team_1"),
            match(15, 4, [TRIAY, BREA], [SANCHEZ, GONZALEZ], "team_1", category="men")]

    def api(self, rows=None):
        return Api({("GET", "/tournaments/103/matches"): {"data": self.ROWS if rows is None else rows},
                    ("GET", "/tournaments/102/matches"): {"data": self.ROWS}})

    async def test_default_is_the_current_tournament_from_cuartos_to_the_final_in_order(self):
        api = self.api()
        out = await make_service(api)[0].cuadro()
        self.assertEqual(out["torneo"]["nombre"], "Rotterdam P1")
        self.assertEqual([m["ronda"] for m in out["partidos"]], ["Final", "Semifinales", "Cuartos de final"], "sin octavos ni hombres")
        self.assertEqual(api.calls[0]["params"], {"category": "women", "per_page": "50"})

    async def test_a_round_is_requested_by_its_number(self):
        api = self.api()
        svc, _ = make_service(api)
        out = await svc.cuadro("Milano", "Semifinales")
        self.assertEqual(api.calls[0]["path"], "/tournaments/102/matches")
        self.assertEqual(api.calls[0]["params"]["round"], "2")
        for word, number in (("final", "1"), ("semis", "2"), ("Cuartos", "4"), ("octavos de final", "8")):
            api2 = self.api()
            await make_service(api2)[0].cuadro(None, word)
            self.assertEqual(api2.calls[0]["params"]["round"], number, word)
        self.assertEqual(out["torneo"]["tipo"], "Major")

    async def test_unknown_tournament_or_round(self):
        svc, _ = make_service(self.api())
        out = await svc.cuadro("Narnia")
        self.assertEqual(out["tipo"], "notfound")
        self.assertTrue(out["torneos"])
        out = await svc.cuadro(None, "mitad")
        self.assertEqual(out["tipo"], "invalid")

    async def test_an_unpublished_draw_says_so(self):
        out = await make_service(self.api([]))[0].cuadro()
        self.assertEqual(out["partidos"], [])
        self.assertIn("todavía no", out["mensaje"])

    async def test_without_any_tournament_with_id(self):
        os.remove(Path(self.dir, "calendario.json"))
        out = await make_service(Api())[0].cuadro()
        self.assertEqual(out["tipo"], "notfound")

    async def test_hidden_results_and_errors(self):
        hidden = [match(12, 1, [TRIAY, BREA], [SANCHEZ, GONZALEZ], HIDDEN, HIDDEN)]
        out = await make_service(self.api(hidden))[0].cuadro()
        self.assertIn("6 meses", out["partidos"][0]["nota"])
        err = await make_service(Api({("GET", "/tournaments/103/matches"): (402, {})}))[0].cuadro()
        self.assertEqual(err["tipo"], "plan")


class Directo(WithEnv, unittest.IsolatedAsyncioTestCase):
    async def test_groups_the_matches_of_today(self):
        rows = [match(1, 8, [TRIAY, BREA], [SANCHEZ, GONZALEZ], None, [{"team_1": "6", "team_2": "4"}, {"team_1": "2", "team_2": "1"}], "live", "2026-10-20", court="Pista Central"),
                match(2, 8, [TRIAY, BREA], [SANCHEZ, GONZALEZ], "team_2", None, "finished", "2026-10-20"),
                match(3, 8, [TRIAY, BREA], [SANCHEZ, GONZALEZ], None, None, "scheduled", "2026-10-20", schedule_label="No antes de las 16:00"),
                match(4, 8, [TRIAY, BREA], [SANCHEZ, GONZALEZ], "team_1", None, "finished", "2026-10-19"),
                match(5, 8, [TRIAY, BREA], [SANCHEZ, GONZALEZ], None, None, "scheduled", "2026-10-21")]
        api = Api({("GET", "/tournaments/103/matches"): {"data": rows}})
        out = await make_service(api)[0].directo()
        self.assertEqual(out["torneo"], "Rotterdam P1")
        self.assertEqual([(m["estado"], m["resultado"], m.get("pista")) for m in out["en_juego"]], [("en juego", "6-4 2-1", "Pista Central")])
        self.assertEqual(len(out["terminados_hoy"]), 1)
        self.assertEqual(out["proximos_hoy"][0]["horario"], "No antes de las 16:00")
        self.assertEqual(len(api.calls), 1, "una sola petición, sin la ruta de pago /live")
        self.assertNotIn("/live", api.paths())
        self.assertIn("padelfip.com", out["marcador_oficial"])
        self.assertIn("retraso", out["nota"])

    async def test_no_tournament_in_play(self):
        out = await make_service(Api(), today=date(2026, 10, 2))[0].directo()
        self.assertIn("no hay ningún torneo", out["mensaje"])
        self.assertEqual(out["proximo"]["nombre"], "Rotterdam P1")
        self.assertEqual(out["marcador_oficial"], local_data.OFFICIAL_LIVE_URL)

    async def test_errors_still_give_the_official_link(self):
        out = await make_service(Api({("GET", "/tournaments/103/matches"): (500, {})}))[0].directo()
        self.assertEqual(out["tipo"], "upstream")
        self.assertIn("padelfip.com", out["marcador_oficial"])

    async def test_men_are_filtered_out(self):
        rows = [match(1, 8, [TRIAY, BREA], [SANCHEZ, GONZALEZ], None, None, "live", "2026-10-20", category="men")]
        out = await make_service(Api({("GET", "/tournaments/103/matches"): {"data": rows}}))[0].directo()
        self.assertEqual(out["en_juego"], [])


class RankingJugadora(WithEnv, unittest.IsolatedAsyncioTestCase):
    ENTRIES = [{"id": 3, "category": "women", "type": "official", "ranking": 1, "ranking_diff": 0, "points": 17840, "points_diff": 120, "date": "2026-10-19"},
               {"id": 3, "category": "women", "type": "race", "ranking": 2, "ranking_diff": -1, "points": 9000, "points_diff": 50, "date": "2026-10-19"},
               {"id": 3, "category": "women", "type": "elo", "ranking": 1, "ranking_diff": 1, "points": 1650, "points_diff": 10, "date": "2026-10-19"}]

    async def test_current_entries(self):
        api = Api({("GET", "/players/3/rankings"): self.ENTRIES})
        out = await make_service(api)[0].ranking_jugadora("Gemma Triay")
        self.assertEqual([e["tipo"] for e in out["rankings"]], ["Ranking oficial", "Ranking RACE (carrera del año)", "Elo de Padel API"])
        self.assertEqual((out["rankings"][0]["posicion"], out["rankings"][0]["puntos"], out["rankings"][1]["cambio_posiciones"]), (1, 17840, -1))
        self.assertEqual(api.calls[0]["params"], {})

    async def test_hidden_values_are_explained(self):
        rows = [{**self.ENTRIES[0], "ranking": HIDDEN, "points": HIDDEN, "ranking_diff": HIDDEN, "points_diff": HIDDEN}]
        out = await make_service(Api({("GET", "/players/3/rankings"): rows}))[0].ranking_jugadora("Gemma Triay")
        e = out["rankings"][0]
        self.assertEqual((e["posicion"], e["puntos"]), (None, None))
        self.assertIn("última semana", e["nota"])

    async def test_men_entries_are_dropped_and_an_empty_answer_says_so(self):
        rows = [{**self.ENTRIES[0], "category": "men"}]
        out = await make_service(Api({("GET", "/players/3/rankings"): rows}))[0].ranking_jugadora("Gemma Triay")
        self.assertEqual(out["rankings"], [])
        self.assertIn("mensaje", out)


class RankingsFree(WithEnv, unittest.IsolatedAsyncioTestCase):
    def rows(self, kind, n=3, **kw):
        names = ["Gemma Triay Pons", "Delfina Brea Senesi", "Ariana Sanchez Fallada", "Beatriz Gonzalez Fernandez"]
        return [{"id": 3 + i, "category": "women", "type": kind, "name": names[i % 4], "nationality": "ES", "ranking": i + 1,
                 "ranking_diff": 0, "points": 1700 - i * 10, "points_diff": 5 - i, "date": "2026-10-19", **kw} for i in range(n)]

    async def test_elo_and_race_ask_for_the_right_type_women_only_and_sorted(self):
        for tool, kind in (("elo", "elo"), ("race", "race")):
            api = Api({("GET", "/rankings"): {"data": self.rows(kind)}})
            out = await make_service(api)[0].ranking_tipo(kind, 3)
            self.assertEqual(api.calls[0]["params"], {"type": kind, "category": "women", "per_page": "3", "sort_by": "ranking", "order_by": "asc"})
            self.assertEqual([r["nombre"] for r in out["ranking"]], ["Gemma Triay", "Delfina Brea", "Ariana Sanchez"])
            self.assertEqual((out["ranking"][0]["posicion"], out["ranking"][0]["puntos"], out["semana_del"]), (1, 1700, "2026-10-19"))
        self.assertIn("Elo", out["tipo"] if kind == "elo" else "Elo")

    async def test_the_elo_note_explains_who_is_included(self):
        out = await make_service(Api({("GET", "/rankings"): {"data": self.rows("elo")}}))[0].ranking_tipo("elo", 3)
        self.assertIn("5 partidos", out["nota"])
        out = await make_service(Api({("GET", "/rankings"): {"data": self.rows("race")}}))[0].ranking_tipo("race", 3)
        self.assertNotIn("nota", out)

    async def test_top_is_clamped_and_men_or_other_types_are_dropped(self):
        rows = self.rows("elo", 4) + [{**self.rows("elo", 1)[0], "category": "men"}, {**self.rows("race", 1)[0]}]
        for asked, sent in ((0, "1"), (99, "20"), (-3, "1"), (7, "7")):
            api = Api({("GET", "/rankings"): {"data": rows}})
            out = await make_service(api)[0].ranking_tipo("elo", asked)
            self.assertEqual(api.calls[0]["params"]["per_page"], sent, asked)
        self.assertTrue(all(r["nombre"] != "" for r in out["ranking"]))
        self.assertEqual(len(out["ranking"]), 4, "sin hombres ni otros tipos")

    async def test_hidden_values_become_empty_and_errors_are_explained(self):
        rows = self.rows("race", 1, ranking=HIDDEN, points=HIDDEN, ranking_diff=HIDDEN, points_diff=HIDDEN)
        out = await make_service(Api({("GET", "/rankings"): {"data": rows}}))[0].ranking_tipo("race", 3)
        self.assertEqual((out["ranking"][0]["posicion"], out["ranking"][0]["puntos"]), (None, None))
        out = await make_service(Api({("GET", "/rankings"): (500, {})}))[0].ranking_tipo("race", 3)
        self.assertEqual(out["tipo"], "upstream")
        out = await make_service(Api({("GET", "/rankings"): {"data": []}}))[0].ranking_tipo("elo", 3)
        self.assertIn("mensaje", out)

    async def test_the_same_ranking_is_cached(self):
        api = Api({("GET", "/rankings"): {"data": self.rows("elo")}})
        svc, _ = make_service(api)
        for _ in range(4):
            await svc.ranking_tipo("elo", 3)
        self.assertEqual(len(api.calls), 1)


class Movimientos(WithEnv, unittest.IsolatedAsyncioTestCase):
    def rows(self, diffs, kind="official"):
        return [{"id": 3 + i, "category": "women", "type": kind, "name": f"Jugadora Numero{i} Xx", "nationality": "ES", "ranking": i + 1,
                 "ranking_diff": 1, "points": 1000, "points_diff": d, "date": "2026-10-19"} for i, d in enumerate(diffs)]

    async def test_who_rises_and_who_falls_sort_by_points_difference(self):
        api = Api({("GET", "/rankings"): {"data": self.rows([300, 200, 100])}})
        svc, _ = make_service(api)
        out = await svc.movimientos("oficial", "suben", 3)
        self.assertEqual(api.calls[0]["params"], {"type": "official", "category": "women", "per_page": "3", "sort_by": "points_diff", "order_by": "desc"})
        self.assertEqual(out["sentido"], "las que más suben")
        api2 = Api({("GET", "/rankings"): {"data": self.rows([-300, -200], "elo")}})
        out = await make_service(api2)[0].movimientos("elo", "bajan", 2)
        self.assertEqual((api2.calls[0]["params"]["type"], api2.calls[0]["params"]["order_by"]), ("elo", "asc"))
        self.assertEqual(out["sentido"], "las que más bajan")
        self.assertEqual(out["ranking"][0]["cambio_puntos"], -300)

    async def test_spanish_aliases_and_defaults(self):
        for tipo, sentido, kind, order in (("Oficial", "SUBEN", "official", "desc"), ("race", "bajan", "race", "asc"), ("elo", "pierden", "elo", "asc"),
                                           ("official", "ganan", "official", "desc"), (None, None, "official", "desc")):
            api = Api({("GET", "/rankings"): {"data": self.rows([1], kind)}})
            await make_service(api)[0].movimientos(tipo, sentido, 1)
            self.assertEqual((api.calls[0]["params"]["type"], api.calls[0]["params"]["order_by"]), (kind, order), (tipo, sentido))

    async def test_invalid_words_never_reach_the_api(self):
        api = Api()
        svc, _ = make_service(api)
        for tipo, sentido in (("mundial", "suben"), ("elo", "se quedan")):
            self.assertEqual((await svc.movimientos(tipo, sentido, 3))["tipo"], "invalid")
        self.assertEqual(api.calls, [])

    async def test_hidden_or_missing_differences_are_left_out(self):
        rows = self.rows([50, HIDDEN, None, 10])
        out = await make_service(Api({("GET", "/rankings"): {"data": rows}}))[0].movimientos("official", "suben", 5)
        self.assertEqual([r["cambio_puntos"] for r in out["ranking"]], [50, 10])

    async def test_top_is_clamped(self):
        api = Api({("GET", "/rankings"): {"data": self.rows([1])}})
        await make_service(api)[0].movimientos("official", "suben", 500)
        self.assertEqual(api.calls[0]["params"]["per_page"], "20")


class Parejas(WithEnv, unittest.IsolatedAsyncioTestCase):
    def pair(self, pid, partner, last, status="active", points=900, category="women", first="2024-01-10"):
        return {"id": f"{pid}-{partner[0]}", "name": "x", "category": category, "status": status, "points": points,
                "first_match_at": f"{first}T00:00:00Z", "last_match_at": f"{last}T00:00:00Z",
                "players": [player(3, TRIAY[1], category=category), player(partner[0], partner[1], category=category)]}

    async def test_partners_sorted_by_last_match_with_dates_and_points(self):
        rows = [self.pair(3, BREA, "2026-05-01", "inactive"), self.pair(3, SANCHEZ, "2026-10-12"), self.pair(3, GONZALEZ, "2025-01-01", points=None)]
        api = Api({("GET", "/players/3/pairs"): rows})
        out = await make_service(api)[0].parejas("Gemma Triay")
        self.assertEqual([p["companera"] for p in out["parejas"]], ["Ariana Sanchez", "Delfina Brea", "Beatriz Gonzalez"])
        top = out["parejas"][0]
        self.assertEqual((top["estado"], top["puntos"], top["primer_partido"], top["ultimo_partido"]), ("active", 900, "2024-01-10", "2026-10-12"))
        self.assertIsNone(out["parejas"][2]["puntos"])
        self.assertEqual(api.paths(), ["/players/3/pairs"])

    async def test_men_pairs_and_more_than_ten_are_dropped(self):
        rows = [self.pair(3, (50 + i, f"Companera Numero{i} Xx"), f"2026-01-{i + 1:02d}") for i in range(14)] + \
               [self.pair(3, (99, "Un Hombre Xx"), "2026-12-01", category="men")]
        out = await make_service(Api({("GET", "/players/3/pairs"): {"data": rows}}))[0].parejas("Gemma Triay")
        self.assertEqual(len(out["parejas"]), 10)
        self.assertNotIn("Un Hombre", json.dumps(out))

    async def test_empty_and_errors(self):
        out = await make_service(Api({("GET", "/players/3/pairs"): []}))[0].parejas("Gemma Triay")
        self.assertEqual(out["parejas"], [])
        self.assertIn("mensaje", out)
        out = await make_service(Api({("GET", "/players/3/pairs"): (404, {})}))[0].parejas("Gemma Triay")
        self.assertEqual(out["tipo"], "notfound")


class Ediciones(WithEnv, unittest.IsolatedAsyncioTestCase):
    def edition(self, tid, year, winners="women", prize=600000, status="finished"):
        return {"id": tid, "name": f"Oysho Milano P1 {year}", "location": "Milano", "country": "IT", "status": status,
                "start_date": f"{year}-09-14", "end_date": f"{year}-09-20",
                "prize": {"amount": prize, "currency": "EUR"} if prize else None,
                "winners": ({winners: team(TRIAY, BREA)} if winners else None)}

    async def test_editions_with_champions_newest_first(self):
        rows = [self.edition(1, 2023), self.edition(2, 2025), self.edition(3, 2024, winners="men")]
        api = Api({("GET", "/tournaments/102/editions"): {"data": rows}})
        out = await make_service(api)[0].ediciones("Milano")
        self.assertEqual(api.paths(), ["/tournaments/102/editions"])
        self.assertEqual([e["inicio"][:4] for e in out["ediciones"]], ["2025", "2024", "2023"])
        e = out["ediciones"][0]
        self.assertEqual((e["campeonas"], e["premio"], e["ciudad"], e["estado"]), ("Gemma Triay / Delfina Brea", "600000 EUR", "Milano", "finished"))
        self.assertIsNone(out["ediciones"][1]["campeonas"], "si solo ganaron ellos, no se enseña")
        self.assertEqual(out["torneo"], "Milano Major")

    async def test_unfinished_editions_have_no_champion_or_prize(self):
        rows = [self.edition(9, 2026, winners=None, prize=None, status="live")]
        out = await make_service(Api({("GET", "/tournaments/103/editions"): {"data": rows}}))[0].ediciones()
        self.assertEqual((out["ediciones"][0]["campeonas"], out["ediciones"][0]["premio"]), (None, None))
        self.assertEqual(out["torneo"], "Rotterdam P1", "sin texto: el torneo en juego")

    async def test_at_most_ten_and_clear_errors(self):
        rows = [self.edition(i, 2000 + i) for i in range(15)]
        out = await make_service(Api({("GET", "/tournaments/102/editions"): {"data": rows}}))[0].ediciones("Milano")
        self.assertEqual(len(out["ediciones"]), 10)
        out = await make_service(Api())[0].ediciones("Narnia")
        self.assertEqual(out["tipo"], "notfound")
        out = await make_service(Api({("GET", "/tournaments/102/editions"): (500, {})}))[0].ediciones("Milano")
        self.assertEqual(out["tipo"], "upstream")
        out = await make_service(Api({("GET", "/tournaments/102/editions"): {"data": []}}))[0].ediciones("Milano")
        self.assertIn("mensaje", out)


class RedirectsAreFollowed(WithEnv, unittest.IsolatedAsyncioTestCase):
    async def test_a_merged_record_is_followed_to_its_new_address(self):
        api = Api({("GET", "/players/123"): lambda r: httpx.Response(302, headers={"Location": "https://padelapi.org/api/players/456"}),
                   ("GET", "/players/456"): {"data": player(456, "Jimena Velasco Gil")}})
        client, _ = make_client(api)
        body = await client.get("/players/123")
        self.assertEqual(body["data"]["id"], 456)
        self.assertEqual(api.paths(), ["/players/123", "/players/456"])
        self.assertEqual(api.calls[1]["headers"]["authorization"], "Bearer token-de-prueba")


class OnlyFreeEndpointsAreEverCalled(WithEnv, unittest.IsolatedAsyncioTestCase):
    ALLOWED = (r"^/players$", r"^/players/\d+$", r"^/players/\d+/matches$", r"^/players/\d+/rankings$", r"^/players/\d+/pairs$",
               r"^/tournaments/\d+/matches$", r"^/tournaments/\d+/editions$", r"^/rankings$", r"^/matches/simulate$")
    PAID = ("/stats", "/form", "/live", "/synergy", "/point-by-point", "/head-to-head", "/watchability")

    async def test_a_full_session_never_touches_a_paid_endpoint(self):
        import re
        ok = {"data": [], "probability": {"team_1": 0.5, "team_2": 0.5}, "players": {}}
        routes = {}
        for method, path in (("GET", "/players"), ("GET", "/players/3"), ("GET", "/players/4"), ("GET", "/players/3/matches"),
                             ("GET", "/players/3/rankings"), ("GET", "/players/3/pairs"), ("GET", "/tournaments/103/matches"),
                             ("GET", "/tournaments/102/matches"), ("GET", "/tournaments/102/editions"), ("GET", "/rankings"),
                             ("POST", "/matches/simulate")):
            routes[(method, path)] = ok if path != "/players/3" and path != "/players/4" else {"data": player(3, TRIAY[1])}
        api = Api(routes)
        svc, _ = make_service(api)
        await svc.perfil("Gemma Triay")
        await svc.partidos("Gemma Triay")
        await svc.cuadro("Milano", "final")
        await svc.cuadro()
        await svc.directo()
        await svc.ranking_jugadora("Gemma Triay")
        await svc.ranking_tipo("elo", 5)
        await svc.ranking_tipo("race", 5)
        await svc.movimientos("elo", "bajan", 5)
        await svc.parejas("Gemma Triay")
        await svc.ediciones("Milano")
        await svc.prediccion(["Gemma Triay"], ["Delfina Brea"])
        await svc.perfil("Jimena Velasco")
        self.assertTrue(api.calls)
        for path in api.paths():
            self.assertTrue(any(re.match(rx, path) for rx in self.ALLOWED), f"endpoint no previsto: {path}")
            self.assertFalse(any(path.endswith(x) for x in self.PAID), f"endpoint de pago: {path}")

    def test_the_service_has_no_method_for_paid_data(self):
        for name in ("estadisticas", "forma", "directo_pro", "cara_a_cara", "sinergia"):
            self.assertFalse(hasattr(live.Service, name), name)
        import inspect
        source = inspect.getsource(live)
        for needle in ("/stats", "/form", "/live\"", "/synergy", "head-to-head"):
            self.assertNotIn(needle, source, needle)


class Prediccion(WithEnv, unittest.IsolatedAsyncioTestCase):
    def sim(self, **kw):
        body = {"name": "Triay/Brea - Sanchez/Gonzalez", "played_at": "2026-10-20", "probability": {"team_1": 0.64, "team_2": 0.36},
                "favorite": "team_1", "elo_diff": 85,
                "players": {"team_1": [player(3, TRIAY[1], elo=1700), player(4, BREA[1], elo=1650)],
                            "team_2": [player(5, SANCHEZ[1], elo=1600), player(6, GONZALEZ[1], elo=1580)]}}
        body.update(kw)
        return Api({("POST", "/matches/simulate"): body})

    async def test_a_pair_against_a_pair(self):
        api = self.sim()
        out = await make_service(api)[0].prediccion(["Gemma Triay", "Delfina Brea"], ["Ariana Sanchez", "Beatriz Gonzalez"])
        self.assertEqual(api.calls[0]["json"], {"team_1": [3, 4], "team_2": [5, 6]})
        self.assertEqual((out["probabilidad_a"], out["probabilidad_b"], out["favorita"], out["diferencia_elo"]),
                         ("64 %", "36 %", "Gemma Triay / Delfina Brea", 85))
        self.assertEqual(out["elo"]["Gemma Triay"], 1700)
        self.assertIn("no es una garantía", out["nota"])
        self.assertEqual(api.paths(), ["/matches/simulate"], "los nombres del Top 20 no cuestan peticiones")

    async def test_single_players_and_a_coin_flip(self):
        api = self.sim(probability={"team_1": 0.5, "team_2": 0.5}, favorite=None, elo_diff=0)
        out = await make_service(api)[0].prediccion(["Gemma Triay"], ["Delfina Brea"])
        self.assertEqual(api.calls[0]["json"], {"team_1": [3], "team_2": [4]})
        self.assertEqual((out["probabilidad_a"], out["favorita"]), ("50 %", "ninguna: están igualadas"))

    async def test_invalid_shapes_never_reach_the_api(self):
        svc_api = self.sim()
        svc, _ = make_service(svc_api)
        for a, b in (([], []), (["Gemma Triay"], []), (["Gemma Triay", "Delfina Brea"], ["Ariana Sanchez"]),
                     (["Gemma Triay", "Delfina Brea", "Ariana Sanchez"], ["Beatriz Gonzalez"]), (["Gemma Triay"], ["Gemma Triay"]),
                     ([" "], ["Delfina Brea"])):
            out = await svc.prediccion(a, b)
            self.assertEqual(out["tipo"], "invalid", (a, b))
        self.assertEqual(svc_api.calls, [])

    async def test_a_name_that_cannot_be_resolved_stops_before_the_request(self):
        api = Api({("GET", "/players"): {"data": []}})
        out = await make_service(api)[0].prediccion(["Gemma Triay"], ["Nadie Conocida"])
        self.assertEqual(out["tipo"], "notfound")
        self.assertNotIn("/matches/simulate", api.paths())

    async def test_a_man_in_the_answer_is_refused(self):
        api = self.sim(players={"team_1": [player(3, TRIAY[1])], "team_2": [player(4, BREA[1], category="men")]})
        out = await make_service(api)[0].prediccion(["Gemma Triay"], ["Delfina Brea"])
        self.assertEqual(out["tipo"], "invalid")

    async def test_the_same_prediction_is_cached(self):
        api = self.sim()
        svc, _ = make_service(api)
        for _ in range(3):
            await svc.prediccion(["Gemma Triay"], ["Delfina Brea"])
        self.assertEqual(len(api.calls), 1)

    async def test_errors(self):
        out = await make_service(Api({("POST", "/matches/simulate"): (422, {})}))[0].prediccion(["Gemma Triay"], ["Delfina Brea"])
        self.assertEqual(out["tipo"], "invalid")


class QuestionsNeverCostMuch(WithEnv, unittest.IsolatedAsyncioTestCase):
    async def test_a_realistic_session_stays_far_below_the_free_limits(self):
        api = Api({("GET", "/players/3"): {"data": player(3, TRIAY[1])}, ("GET", "/players/4"): {"data": player(4, BREA[1])},
                   ("GET", "/players/3/matches"): {"data": []}, ("GET", "/tournaments/103/matches"): {"data": []},
                   ("POST", "/matches/simulate"): {"probability": {"team_1": 0.5, "team_2": 0.5}, "players": {}}})
        svc, _ = make_service(api)
        for _ in range(10):                                   # diez rondas de las mismas preguntas
            await svc.perfil("Gemma Triay")
            await svc.perfil("Delfina Brea")
            await svc.partidos("Gemma Triay")
            await svc.cuadro()
            await svc.directo()
            await svc.prediccion(["Gemma Triay"], ["Delfina Brea"])
        self.assertEqual(len(api.calls), 5, "cada dato distinto se pide una sola vez")
        self.assertLess(svc.status()["peticiones_hoy"], live.DEFAULT_PER_DAY)


# ───────────────────── MCP y servidor ──────────────────────────────────────────

class McpRegistration(WithEnv, unittest.IsolatedAsyncioTestCase):
    NEW = ("perfil_jugadora", "partidos_jugadora", "cuadro_torneo", "partidos_en_directo", "rankings_jugadora",
           "prediccion_partido", "ranking_elo", "ranking_race", "movimientos_ranking", "parejas_jugadora", "ediciones_torneo")

    def build(self, extended, service="default"):
        from padel_mcp.tools import build_mcp

        async def nothing():
            return {"data": [], "age_s": 0, "fallback": None}
        providers = {k: nothing for k in ("ranking", "calendar", "live", "finals", "news", "status")}
        if service != "default":
            providers["padelapi"] = service
        return build_mcp(providers, extended=extended)

    async def names(self, mcp):
        from fastmcp import Client
        async with Client(mcp) as c:
            return {t.name: t for t in await c.list_tools()}

    async def test_the_eleven_tools_exist_only_in_extended_mode(self):
        self.assertFalse(set(self.NEW) & set(await self.names(self.build(False))))
        self.assertTrue(set(self.NEW) <= set(await self.names(self.build(True))))

    async def test_core_and_extra_are_exactly_the_tools_there_are(self):
        from padel_mcp.tools import CORE_TOOLS, EXTRA_TOOLS
        self.assertTrue(set(self.NEW) <= set(EXTRA_TOOLS))
        self.assertEqual(set(await self.names(self.build(True))), set(CORE_TOOLS) | set(EXTRA_TOOLS))
        self.assertEqual(len(CORE_TOOLS) + len(EXTRA_TOOLS), 23)

    async def test_all_of_them_are_read_only_and_say_what_they_need(self):
        tools = await self.names(self.build(True))
        for name in self.NEW:
            self.assertTrue(tools[name].annotations.readOnlyHint, name)
        self.assertNotIn("estadisticas_jugadora", tools, "lo de pago no se ofrece")
        self.assertNotIn("historico_ranking", tools)
        for name in tools:
            self.assertNotIn("DATO DE PAGO", tools[name].description)
        self.assertIn("6 meses", tools["partidos_jugadora"].description)
        self.assertIn("de pago y no se usa", tools["rankings_jugadora"].description)
        self.assertIn("estimación", tools["prediccion_partido"].description)
        props = tools["prediccion_partido"].inputSchema["properties"]
        self.assertEqual(sorted(props), ["pareja_a", "pareja_b"])
        self.assertEqual(props["pareja_a"]["type"], "array")

    async def test_the_server_instructions_tell_the_agent_how_to_react_to_plan_errors(self):
        text = self.build(True).instructions
        for needle in ("'plan'", "'budget'", "'limit'", "no reintentes en bucle", "6 meses", "solo con lo gratuito", "última semana"):
            self.assertIn(needle, text)

    ORIGINAL_INSTRUCTIONS = (
        "Datos del pádel profesional FEMENINO (circuito FIP / Premier Padel). Solo lectura. Tres "
        "herramientas: ranking_femenino (posición, nombre y puntos de cada jugadora), ultimo_torneo (el "
        "último torneo terminado) y proximo_torneo (el siguiente). Los datos los guarda el servidor; cada "
        "respuesta indica su fuente y su antigüedad: si trae un aviso de retraso, dilo. "
        "Al contar un resultado responde con: torneo y dónde fue, qué tipo de torneo es, quién jugó la "
        "final, primeras, segundas y terceras, y el resultado final; si falta un dato (las terceras, por "
        "ejemplo), dilo sin inventarlo. NO hay marcadores en directo: para eso se da el enlace oficial. "
        "Si ves más herramientas (jugadora, noticias, resultado_torneo...), úsalas con sus filtros; cada "
        "noticia trae su resumen (modo 'resumen': resumen propio; 'extracto': texto literal), su fecha y "
        "el enlace directo a la noticia ('enlace'). Cita siempre el medio, la fecha y el enlace.")

    async def test_without_the_new_tools_the_server_instructions_are_exactly_the_original_ones(self):
        self.assertEqual(self.build(False).instructions, self.ORIGINAL_INSTRUCTIONS)
        for name in self.NEW:
            self.assertNotIn(name, self.build(False).instructions)

    async def test_with_them_the_original_text_is_kept_and_a_paragraph_is_appended(self):
        text = self.build(True).instructions
        self.assertTrue(text.startswith(self.ORIGINAL_INSTRUCTIONS))
        self.assertGreater(len(text), len(self.ORIGINAL_INSTRUCTIONS))

    async def test_each_tool_calls_its_method_with_the_arguments(self):
        calls = []

        class Fake:
            def __getattr__(self, name):
                async def method(*args):
                    calls.append((name, args))
                    return {"ok": name}
                return method
        from fastmcp import Client
        async with Client(self.build(True, Fake())) as c:
            for tool, args, expected in (("perfil_jugadora", {"nombre": "A"}, ("perfil", ("A",))),
                                         ("partidos_jugadora", {"nombre": "A", "limite": 3}, ("partidos", ("A", 3))),
                                         ("cuadro_torneo", {"torneo": "Madrid", "ronda": "final"}, ("cuadro", ("Madrid", "final"))),
                                         ("cuadro_torneo", {}, ("cuadro", (None, None))),
                                         ("partidos_en_directo", {}, ("directo", ())),
                                         ("rankings_jugadora", {"nombre": "A"}, ("ranking_jugadora", ("A",))),
                                         ("ranking_elo", {}, ("ranking_tipo", ("elo", 10))),
                                         ("ranking_race", {"top": 4}, ("ranking_tipo", ("race", 4))),
                                         ("movimientos_ranking", {}, ("movimientos", ("oficial", "suben", 5))),
                                         ("movimientos_ranking", {"tipo": "elo", "sentido": "bajan", "top": 3}, ("movimientos", ("elo", "bajan", 3))),
                                         ("parejas_jugadora", {"nombre": "A"}, ("parejas", ("A",))),
                                         ("ediciones_torneo", {"torneo": "Milano"}, ("ediciones", ("Milano",))),
                                         ("ediciones_torneo", {}, ("ediciones", (None,))),
                                         ("prediccion_partido", {"pareja_a": ["A"], "pareja_b": ["B"]}, ("prediccion", (["A"], ["B"])))):
                calls.clear()
                await c.call_tool(tool, args)
                self.assertEqual(calls, [expected], tool)

    async def test_without_a_service_the_tools_answer_with_a_clear_error(self):
        from fastmcp import Client
        async with Client(self.build(True, None)) as c:
            r = await c.call_tool("perfil_jugadora", {"nombre": "Gemma Triay"})
            data = r.data if r.data is not None else r.structured_content
            self.assertEqual(data["tipo"], "config")


class ServerWiring(WithEnv):
    def test_the_status_reports_the_live_budget_and_the_tools_are_wired(self):
        import server
        st = asyncio.run(server.api_data_status())["consultas_en_directo"]
        self.assertEqual((st["limite_por_minuto"], st["limite_por_dia"], st["activo"]), (5, 600, True))
        self.assertNotIn("token-de-prueba", json.dumps(asyncio.run(server.api_data_status())))
        self.assertIn("v8", server.APP_VERSION)
        self.assertIsNotNone(server.padelapi_live.service())

    def test_no_secret_is_ever_in_an_error(self):
        for kind, message in (("auth", "Padel API rechazó el token del servidor (401)."), ("plan", "x")):
            self.assertNotIn("token-de-prueba", json.dumps(live._err(live.LiveError(kind, message))))


if __name__ == "__main__":
    unittest.main()
