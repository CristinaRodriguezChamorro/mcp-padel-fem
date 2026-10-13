"""
Fuente de datos Padel API (padelapi.org): conversiones puras, la pasada completa contra una API de mentira
(con la forma que documenta la API real) y su integración en la tarea de actualización.
Sin internet y sin token real.
"""
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # para reutilizar los ayudantes de test_ingest
os.environ["WIKIPEDIA_INGEST"] = "off"

import httpx

from tools import ingest, local_data
from tools import padelapi_source as pa

TODAY = date(2026, 10, 6)
TOKEN = "tok-secreto-123"


def run(coro):
    return asyncio.run(coro)


# ── datos de mentira con la forma de la API ──────────────────────────────────

def rk(name, pos, pts, nat="ES", day="2026-10-05", category="women", type_="official"):
    return {"id": pos, "self": f"/api/players/{pos}", "name": name, "category": category, "nationality": nat,
            "type": type_, "ranking": pos, "ranking_diff": 0, "points": pts, "points_diff": 0, "date": day,
            "connections": {"rankings": "x"}}


def ranking_rows(n=20):
    return [rk(f"Jugadora {i:02d}", i, 20000 - i * 500, nat=("ES", "AR", "PT")[i % 3]) for i in range(1, n + 1)]


def tour(tid, name, level, status, start, end, location="Madrid", country="ES", fmt="pairs"):
    return {"id": tid, "name": name, "level": level, "status": status, "start_date": start, "end_date": end,
            "location": location, "country": country, "format": fmt, "venue": None, "winners": None}


def season():
    levels = ["p1", "p2", "major", "p1", "p2", "p1", "p2", "major", "p1", "p2", "p1", "p2"]
    out = []
    for i, level in enumerate(levels):
        start = date(2026, 2, 9) + timedelta(days=21 * i)
        out.append(tour(100 + i, f"Torneo {i:02d} {level.upper()}", level, "finished", start.isoformat(),
                        (start + timedelta(days=6)).isoformat(), location=f"Ciudad{i}", country=("ES", "IT", "FR")[i % 3]))
    out += [tour(200, "Torneo 12 P1", "p1", "pending", "2026-10-19", "2026-10-25", "Milano", "IT"),
            tour(201, "Torneo 13 P1", "p1", "pending", "2026-11-09", "2026-11-15", "Dubai", "AE"),
            tour(202, "Premier Padel Finals", "finals", "pending", "2026-12-07", "2026-12-13", "Barcelona", "ES")]
    return out


def pair(*names):
    return [{"id": i, "self": "", "name": n, "side": None, "connections": ""} for i, n in enumerate(names)]


def match(rnd, winner, t1, t2, score, status="finished"):
    return {"id": rnd * 1000 + hash((t1, t2)) % 997, "round": rnd, "round_name": "x", "category": "women", "draw": "main",
            "status": status, "score": score, "winner": winner, "players": {"team_1": pair(*t1), "team_2": pair(*t2)}}


def sets(*pairs_):
    return [{"team_1": str(a), "team_2": str(b)} for a, b in pairs_]


class FakeApi:
    """Padel API de mentira: responde como la real y anota cada petición."""
    def __init__(self, ranking=None, tournaments=None, matches=None, tour_pages=None, overrides=None):
        self.ranking = ranking if ranking is not None else ranking_rows()
        self.tournaments = tournaments if tournaments is not None else season()
        self.matches = matches or self.default_matches
        self.tour_pages = tour_pages
        self.overrides = overrides or {}
        self.calls = []

    @staticmethod
    def default_matches(tid, rnd):
        if rnd == 1:
            return [match(1, "team_1", (f"Ganadora {tid} A", f"Ganadora {tid} B"), (f"Finalista {tid} A", f"Finalista {tid} B"),
                          sets((6, 3), (7, 5)))]
        return [match(2, "team_1", (f"Ganadora {tid} A", f"Ganadora {tid} B"), (f"Semi {tid} A1", f"Semi {tid} A2"), sets((6, 1), (6, 2))),
                match(2, "team_2", (f"Semi {tid} B1", f"Semi {tid} B2"), (f"Ganadora {tid} A", f"Ganadora {tid} B"), sets((4, 6), (3, 6)))]

    def factory(self):
        def handler(request: httpx.Request) -> httpx.Response:
            params = dict(request.url.params)
            self.calls.append({"path": request.url.path, "params": params, "headers": dict(request.headers)})
            for key, responder in self.overrides.items():
                if key(request.url.path, params):
                    result = responder(request)
                    if result is not None:
                        return result
            path = request.url.path
            if path == "/api/rankings":
                return httpx.Response(200, json={"data": self.ranking, "links": {"next": None}, "meta": {}})
            if path == "/api/tournaments":
                if self.tour_pages:
                    page = int(params.get("page", "1"))
                    data = self.tour_pages[page - 1]
                    nxt = "next" if page < len(self.tour_pages) else None
                    return httpx.Response(200, json={"data": data, "links": {"next": nxt}, "meta": {}})
                return httpx.Response(200, json={"data": self.tournaments, "links": {"next": None}, "meta": {}})
            m = re.match(r"^/api/tournaments/(\d+)/matches$", path)
            if m:
                return httpx.Response(200, json={"data": self.matches(int(m.group(1)), int(params["round"])), "links": {"next": None}, "meta": {}})
            return httpx.Response(404, json={"message": "no"})
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def match_calls(self):
        return [c for c in self.calls if c["path"].endswith("/matches")]


class WithToken(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("PADELAPI_TOKEN", "PADELAPI_INGEST", "PADELAPI_MIN_INTERVAL")}
        os.environ["PADELAPI_TOKEN"] = TOKEN
        os.environ.pop("PADELAPI_INGEST", None)
        os.environ["PADELAPI_MIN_INTERVAL"] = "0"

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


# ── conversiones puras ───────────────────────────────────────────────────────

class Conversions(unittest.TestCase):
    def test_flag_and_points(self):
        self.assertEqual(pa.flag("ES"), "🇪🇸")
        self.assertEqual(pa.flag("ar"), "🇦🇷")
        for bad in ("", None, "E", "ESP", "1A", "ñÑ"):
            self.assertEqual(pa.flag(bad), "", repr(bad))
        self.assertEqual(pa.format_points(17840), "17.840")
        self.assertEqual(pa.format_points(950), "950")
        self.assertEqual(pa.format_points(1234567), "1.234.567")
        for bad in ("17840", None, True, -1, 12.5, pa.HIDDEN):
            self.assertIsNone(pa.format_points(bad), repr(bad))

    def test_clean_name(self):
        self.assertEqual(pa.clean_name("  Gemma   Triay "), "Gemma Triay")
        self.assertEqual(pa.clean_name("<script>alert(1)</script>Ana"), "scriptalert(1)/scriptAna")
        self.assertNotIn("<", pa.clean_name("<b>x</b>"))
        self.assertEqual(len(pa.clean_name("x" * 500)), 80)
        self.assertEqual(pa.clean_name(None), "")

    def test_ranking_keeps_women_official_visible_rows_in_order(self):
        rows = [rk("Tercera", 3, 100), rk("Primera", 1, 300), rk("Hombre", 1, 999, category="men"), rk("Elo", 2, 5, type_="elo"),
                rk("Oculta", 4, pa.HIDDEN), rk("Sin posicion", 5, 10), rk("Segunda", 2, 200), rk("", 6, 1)]
        rows[5]["ranking"] = pa.HIDDEN
        out = pa.parse_ranking(rows)
        self.assertEqual([r["name"] for r in out], ["Primera", "Segunda", "Tercera"])
        self.assertEqual(out[0], {"pos": 1, "name": "Primera", "pair": "", "flag": "🇪🇸", "pts": "300"})

    def test_ranking_limit_and_snapshot_date(self):
        self.assertEqual(len(pa.parse_ranking(ranking_rows(40), limit=20)), 20)
        self.assertEqual(pa.snapshot_date([rk("a", 1, 1, day="2026-09-28"), rk("b", 2, 1, day="2026-10-05")]), "2026-10-05")
        self.assertIsNone(pa.snapshot_date([{"date": "ayer"}, {}]))

    def test_tournament_levels_places_and_cancellations(self):
        t = pa.parse_tournament(tour(1, "Oysho Milano P1", "p1", "pending", "2026-10-12", "2026-10-18", "Milano", "IT"))
        self.assertEqual(t, {"name": "Oysho Milano P1", "place": "Milano 🇮🇹", "start": "2026-10-12", "end": "2026-10-18",
                             "badge": "p1", "badgeText": "P1", "tv": "", "padelapi_id": 1})
        self.assertEqual(pa.parse_tournament(tour(2, "X", "major", "live", "2026-10-12", "2026-10-18"))["badgeText"], "Major")
        self.assertEqual(pa.parse_tournament(tour(3, "X", "finals", "pending", "2026-12-07", "2026-12-13"))["badgeText"], "Finals")
        self.assertTrue(pa.parse_tournament(tour(4, "X", "p2", "cancelled", "2026-10-12", "2026-10-18"))["cancelado"])
        self.assertTrue(pa.parse_tournament(tour(5, "X", "p2", "postponed", "2026-10-12", "2026-10-18"))["cancelado"])
        self.assertNotIn("cancelado", pa.parse_tournament(tour(6, "X", "p2", "finished", "2026-10-12", "2026-10-18")))

    def test_tournaments_that_do_not_belong_are_skipped(self):
        for bad in (tour(1, "X", "fip_gold", "pending", "2026-10-12", "2026-10-18"),
                    tour(2, "X", "wpt_master", "finished", "2026-10-12", "2026-10-18"),
                    tour(3, "X", "p1", "pending", "2026-10-12", "2026-10-18", fmt="teams"),
                    tour(4, "X", "p1", "pending", "pronto", "2026-10-18"),
                    tour(5, "", "p1", "pending", "2026-10-12", "2026-10-18")):
            self.assertIsNone(pa.parse_tournament(bad))

    def test_score_is_shown_with_the_winner_first(self):
        s = sets((6, 3), (7, 5))
        self.assertEqual(pa.format_score(s, "team_1"), "6-3 7-5")
        self.assertEqual(pa.format_score(s, "team_2"), "3-6 5-7")
        self.assertEqual(pa.format_score(sets(("7(5)", 6), (3, 6), (6, 4)), "team_1"), "7(5)-6 3-6 6-4")
        for empty in (None, "", pa.HIDDEN, [], 12):
            self.assertEqual(pa.format_score(empty, "team_1"), "", repr(empty))
        self.assertEqual(pa.format_score("6-3 7-5", "team_1"), "6-3 7-5")
        self.assertEqual(pa.format_score("<b>6-3</b>", "team_1"), "6-3")
        self.assertEqual(pa.format_score([{"team_1": "", "team_2": "6"}, "basura", {"team_1": "6", "team_2": "2"}], "team_1"), "6-2")

    def test_final_with_winners_losers_score_and_semifinalists(self):
        f = pa.parse_final(FakeApi.default_matches(7, 1), FakeApi.default_matches(7, 2))
        self.assertEqual(f, {"ganadoras": "Ganadora 7 A / Ganadora 7 B", "finalistas": "Finalista 7 A / Finalista 7 B",
                             "resultado": "6-3 7-5", "semifinalistas": ["Semi 7 A1 / Semi 7 A2", "Semi 7 B1 / Semi 7 B2"]})

    def test_final_where_team_two_wins_is_oriented_to_the_winner(self):
        m = match(1, "team_2", ("Perdedora A", "Perdedora B"), ("Campeona A", "Campeona B"), sets((3, 6), (4, 6)))
        f = pa.parse_final([m], [])
        self.assertEqual((f["ganadoras"], f["finalistas"], f["resultado"]), ("Campeona A / Campeona B", "Perdedora A / Perdedora B", "6-3 6-4"))
        self.assertEqual(f["semifinalistas"], [])

    def test_retirement_and_walkover_are_said(self):
        m = match(1, "team_1", ("A", "B"), ("C", "D"), sets((6, 2), (3, 1)), status="retired")
        self.assertEqual(pa.parse_final([m], [])["resultado"], "6-2 3-1 (retirada)")
        m = match(1, "team_1", ("A", "B"), ("C", "D"), None, status="walkover")
        self.assertEqual(pa.parse_final([m], [])["resultado"], "w.o.")

    def test_undecided_or_incomplete_finals_are_none(self):
        self.assertIsNone(pa.parse_final([], []))
        self.assertIsNone(pa.parse_final([match(1, None, ("A", "B"), ("C", "D"), None, status="scheduled")], []))
        self.assertIsNone(pa.parse_final([match(1, "team_1", ("A", "B"), (), None)], []))
        self.assertIsNone(pa.parse_final([match(2, "team_1", ("A", "B"), ("C", "D"), sets((6, 0)))], []), "una semifinal no es la final")

    def test_hidden_score_still_gives_the_final_without_a_score(self):
        m = match(1, "team_1", ("A", "B"), ("C", "D"), pa.HIDDEN)
        self.assertEqual(pa.parse_final([m], [])["resultado"], "")


# ── la pasada completa ───────────────────────────────────────────────────────

class FetchAll(WithToken):
    def go(self, api=None, previous=None, **kw):
        api = api or FakeApi()
        slept = []

        async def sleep(s):
            slept.append(s)
        kw.setdefault("interval", 6.5)
        got = run(pa.fetch_all(TODAY, previous, api.factory(), sleep=sleep, **kw))
        return got, api, slept

    def test_happy_path_requests_and_result(self):
        got, api, slept = self.go()
        first, second = api.calls[0], api.calls[1]
        self.assertEqual((first["path"], first["params"]["category"], first["params"]["type"]), ("/api/rankings", "women", "official"))
        self.assertEqual(second["path"], "/api/tournaments")
        self.assertEqual(second["params"]["level"], "major,p1,p2,finals")
        self.assertEqual((second["params"]["after_date"], second["params"]["before_date"]), ("2025-12-31", "2027-01-01"))
        self.assertEqual(len(got["ranking"]), 20)
        self.assertEqual(got["snapshot"], "2026-10-05")
        self.assertEqual(len(got["tournaments"]), 15)
        with_final = [t for t in got["tournaments"] if t.get("final_femenina")]
        self.assertEqual(len(with_final), 9, "los terminados dentro de los últimos 6 meses")
        self.assertEqual(got["calls"], len(api.calls))
        self.assertEqual(len(api.match_calls()), 18)
        self.assertEqual(len(slept), len(api.calls) - 1, "ni una espera antes de la primera petición")
        self.assertTrue(all(s == 6.5 for s in slept), "ritmo de una petición cada 6,5 s (10 por minuto)")

    def test_every_request_carries_the_token_and_an_honest_user_agent(self):
        _, api, _ = self.go()
        for c in api.calls:
            self.assertEqual(c["headers"]["authorization"], f"Bearer {TOKEN}")
            self.assertTrue(c["headers"]["user-agent"].startswith("PadelFemBot/"))

    def test_only_women_final_and_semifinals_of_the_main_draw_are_requested(self):
        _, api, _ = self.go()
        for c in api.match_calls():
            self.assertEqual(c["params"]["category"], "women")
            self.assertIn(c["params"]["round"], ("1", "2"))
            self.assertNotIn("draw", c["params"], "el plan gratuito solo da el cuadro principal")

    def test_a_known_tournament_is_never_asked_again(self):
        got1, _, _ = self.go()
        got2, api2, _ = self.go(previous=got1["tournaments"])
        self.assertEqual(api2.match_calls(), [])
        self.assertEqual(len(api2.calls), 2)
        self.assertEqual(got2["tournaments"], got1["tournaments"])

    def test_only_new_finished_tournaments_cost_requests(self):
        got1, _, _ = self.go()
        previous = [dict(t) for t in got1["tournaments"]]
        gone = next(t for t in previous if t["padelapi_id"] == 111)
        gone.pop("final_femenina")
        got2, api2, _ = self.go(previous=previous)
        self.assertEqual({c["path"] for c in api2.match_calls()}, {"/api/tournaments/111/matches"})
        self.assertEqual(len(api2.match_calls()), 2)
        self.assertTrue(next(t for t in got2["tournaments"] if t["padelapi_id"] == 111).get("final_femenina"))

    def test_results_older_than_the_free_window_are_not_asked(self):
        got, api, _ = self.go()
        asked = {int(re.search(r"/tournaments/(\d+)/", c["path"]).group(1)) for c in api.match_calls()}
        self.assertTrue({100, 101, 102}.isdisjoint(asked))
        self.assertTrue(asked.issuperset(set(range(103, 112))))

    def test_pending_and_live_tournaments_are_not_asked_for_results(self):
        got, api, _ = self.go()
        asked = {int(re.search(r"/tournaments/(\d+)/", c["path"]).group(1)) for c in api.match_calls()}
        self.assertTrue({200, 201, 202}.isdisjoint(asked))

    def test_cap_per_run_newest_first_with_a_note(self):
        many = [tour(300 + i, f"Reciente {i}", "p2", "finished", (TODAY - timedelta(days=60 - i * 2)).isoformat(),
                     (TODAY - timedelta(days=54 - i * 2)).isoformat()) for i in range(15)]
        got, api, _ = self.go(FakeApi(tournaments=many))
        self.assertEqual(len(api.match_calls()), 2 * pa.MAX_FINAL_FETCHES)
        asked = [int(re.search(r"/tournaments/(\d+)/", c["path"]).group(1)) for c in api.match_calls()[::2]]
        self.assertEqual(asked, list(range(314, 314 - pa.MAX_FINAL_FETCHES, -1)), "los más recientes primero")
        self.assertTrue(any("próxima pasada" in n for n in got["notes"]))

    def test_tournaments_are_followed_across_pages(self):
        s = season()
        got, api, _ = self.go(FakeApi(tour_pages=[s[:8], s[8:]]))
        self.assertEqual(len(got["tournaments"]), 15)
        self.assertEqual([c["params"].get("page") for c in api.calls if c["path"] == "/api/tournaments"], ["1", "2"])
        self.assertEqual(api.calls[1]["params"]["per_page"], "50")

    def test_hidden_scores_keep_the_final_but_have_no_score(self):
        api = FakeApi(matches=lambda tid, rnd: [match(rnd, "team_1", ("A", "B"), ("C", "D"), pa.HIDDEN)])
        got, _, _ = self.go(api)
        finals = [t["final_femenina"] for t in got["tournaments"] if t.get("final_femenina")]
        self.assertTrue(finals)
        self.assertTrue(all(f["resultado"] == "" for f in finals))

    def test_an_undecided_final_leaves_a_note_and_no_final(self):
        api = FakeApi(matches=lambda tid, rnd: [match(rnd, None, ("A", "B"), ("C", "D"), None, status="scheduled")])
        got, _, _ = self.go(api)
        self.assertFalse(any(t.get("final_femenina") for t in got["tournaments"]))
        self.assertTrue(any("sin ganadora clara" in n or "ganadora clara" in n for n in got["notes"]))


class Failures(WithToken):
    def attempt(self, api):
        async def sleep(s):
            self.slept.append(s)
        self.slept = []
        return pa.fetch_all(TODAY, None, api.factory(), sleep=sleep, interval=0)

    def expect(self, api, exc, text):
        with self.assertRaises(exc) as ctx:
            run(self.attempt(api))
        self.assertIn(text, str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception), "el token no puede salir en ningún mensaje")
        return ctx.exception

    def test_no_token(self):
        os.environ.pop("PADELAPI_TOKEN")
        self.assertFalse(pa.enabled())
        with self.assertRaises(pa.FatalSourceError):
            run(pa.fetch_all(TODAY, None, FakeApi().factory(), sleep=asyncio.sleep, interval=0))

    def test_enabled_needs_a_token_and_can_be_switched_off(self):
        self.assertTrue(pa.enabled())
        for off in ("off", "OFF", "0", "false", "no"):
            os.environ["PADELAPI_INGEST"] = off
            self.assertFalse(pa.enabled(), off)
        os.environ.pop("PADELAPI_INGEST")
        os.environ["PADELAPI_TOKEN"] = "   "
        self.assertFalse(pa.enabled())

    def test_bad_token(self):
        api = FakeApi(overrides={lambda p, q: True: lambda r: httpx.Response(401, json={"message": "Unauthenticated"})})
        self.expect(api, pa.FatalSourceError, "PADELAPI_TOKEN")
        self.assertEqual(len(api.calls), 1, "no se insiste con un token malo")

    def test_rate_limit_is_waited_once_and_then_it_works(self):
        state = {"n": 0}

        def first_time(r):
            state["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "3"}) if state["n"] == 1 else None
        api = FakeApi(overrides={lambda p, q: p == "/api/rankings": first_time})
        got = run(self.attempt(api))
        self.assertEqual(len(got["ranking"]), 20)
        self.assertIn(3.0, self.slept)

    def test_rate_limit_wait_is_bounded_and_a_second_429_is_fatal(self):
        api = FakeApi(overrides={lambda p, q: p == "/api/rankings": lambda r: httpx.Response(429, headers={"Retry-After": "99999"})})
        self.expect(api, pa.FatalSourceError, "429")
        self.assertIn(60.0, self.slept)
        self.assertEqual(max(self.slept), 60.0)

    def test_server_error_garbage_and_non_object_json(self):
        for responder, text in ((lambda r: httpx.Response(500), "500"),
                                (lambda r: httpx.Response(200, text="<html>"), "no es JSON"),
                                (lambda r: httpx.Response(200, json=[1, 2]), "inesperada")):
            api = FakeApi(overrides={lambda p, q: p == "/api/rankings": responder})
            self.expect(api, pa.SourceError, text)

    def test_unexpected_tournament_list_shape(self):
        api = FakeApi(overrides={lambda p, q: p == "/api/tournaments": lambda r: httpx.Response(200, json={"data": "nada"})})
        self.expect(api, pa.SourceError, "forma inesperada")

    def test_network_down(self):
        def down(r):
            raise httpx.ConnectError("sin red")
        api = FakeApi(overrides={lambda p, q: True: down})
        self.expect(api, pa.FatalSourceError, "ConnectError")

    def test_payment_required_on_one_tournament_skips_it_and_continues(self):
        api = FakeApi(overrides={lambda p, q: p == "/api/tournaments/105/matches": lambda r: httpx.Response(402, json={"message": "Subscription required."})})
        got = run(self.attempt(api))
        by_id = {t["padelapi_id"]: t for t in got["tournaments"]}
        self.assertNotIn("final_femenina", by_id[105])
        self.assertIn("final_femenina", by_id[106])
        self.assertTrue(any("402" in n for n in got["notes"]))

    def test_a_5xx_on_one_tournament_skips_it_and_continues(self):
        api = FakeApi(overrides={lambda p, q: p == "/api/tournaments/104/matches": lambda r: httpx.Response(503)})
        got = run(self.attempt(api))
        by_id = {t["padelapi_id"]: t for t in got["tournaments"]}
        self.assertNotIn("final_femenina", by_id[104])
        self.assertIn("final_femenina", by_id[105])

    def test_a_bad_token_in_the_middle_stops_everything(self):
        api = FakeApi(overrides={lambda p, q: p.endswith("/matches"): lambda r: httpx.Response(401)})
        self.expect(api, pa.FatalSourceError, "PADELAPI_TOKEN")


# ── integración con la tarea de actualización ────────────────────────────────

class IngestWithPadelApi(WithToken):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="padel-api-")
        self._dd = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.dir

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop("DATA_DIR", None) if self._dd is None else os.environ.__setitem__("DATA_DIR", self._dd)
        super().tearDown()

    def read(self, name):
        return json.loads(Path(self.dir, f"{name}.json").read_text(encoding="utf-8"))

    def files(self):
        return {p.name: p.read_bytes() for p in Path(self.dir).iterdir() if not p.name.startswith("_")}

    def test_a_good_run_writes_the_data_files_in_the_apps_own_format(self):
        status = run(ingest.run_once(FakeApi().factory(), today=TODAY))
        self.assertTrue(status["ok"], status)
        self.assertEqual((status["fuente_activa"], status["ranking_filas"], status["torneos"], status["finales"]), ("padelapi", 20, 15, 9))
        rank, cal = self.read("ranking"), self.read("calendario")
        self.assertEqual((rank["_meta"]["tipo"], rank["_meta"]["actualizado"], rank["_meta"]["fuente"]), ("api", "2026-10-05", "Padel API (padelapi.org)"))
        self.assertEqual(cal["_meta"]["tipo"], "api")
        self.assertEqual(rank["jugadoras"][0], {"pos": 1, "name": "Jugadora 01", "pair": "", "flag": "🇦🇷", "pts": "19.500"})
        t = next(x for x in cal["torneos"] if x["padelapi_id"] == 111)
        self.assertEqual((t["badgeText"], t["place"], t["final_femenina"]["resultado"]), ("P2", "Ciudad11 🇫🇷", "6-3 7-5"))
        self.assertEqual(len(t["final_femenina"]["semifinalistas"]), 2)

    def test_the_rest_of_the_app_can_read_what_was_written(self):
        run(ingest.run_once(FakeApi().factory(), today=TODAY))
        ranking = run(local_data.get_ranking_live())
        self.assertEqual(ranking[0]["name"], "Jugadora 01")
        finals = run(local_data.get_recent_finals(today=TODAY))
        self.assertEqual(finals[0]["torneo"], "Torneo 11 P2")
        self.assertEqual(finals[0]["categoria"], "P2")
        self.assertEqual(len(finals[0]["semifinalistas"]), 2)
        card = local_data.describe_final(finals[0])
        self.assertEqual(len(card["podio"]["terceras"]), 2)
        self.assertIsNone(card["podio"]["nota_terceras"])
        cal = run(local_data.get_calendar_live(today=TODAY))
        self.assertEqual([c["name"] for c in cal][:2], ["Torneo 12 P1", "Torneo 13 P1"])
        status = local_data.dataset_status(today=TODAY)
        self.assertEqual(status["ranking"]["tipo"], "api")
        self.assertFalse(status["ranking"]["desactualizado"])

    def test_the_second_run_only_asks_for_what_is_new(self):
        run(ingest.run_once(FakeApi().factory(), today=TODAY))
        api2 = FakeApi()
        status = run(ingest.run_once(api2.factory(), today=TODAY))
        self.assertTrue(status["ok"])
        self.assertEqual(api2.match_calls(), [])
        self.assertEqual(status["peticiones"], 2)
        self.assertEqual(status["finales"], 9)

    def test_a_failure_keeps_the_previous_api_data_untouched(self):
        run(ingest.run_once(FakeApi().factory(), today=TODAY))
        before = self.files()
        bad = FakeApi(overrides={lambda p, q: True: lambda r: httpx.Response(401)})
        status = run(ingest.run_once(bad.factory(), today=TODAY))
        self.assertFalse(status["ok"])
        self.assertIn("PADELAPI_TOKEN", status["mensaje"])
        self.assertNotIn(TOKEN, json.dumps(status))
        self.assertEqual(self.files(), before)
        self.assertEqual(status["fuente_activa"], "padelapi")

    def test_a_suspicious_dataset_is_refused_and_nothing_is_overwritten(self):
        run(ingest.run_once(FakeApi().factory(), today=TODAY))
        before = self.files()
        broken = ranking_rows(20)
        broken[3]["points"] = 99999                       # una jugadora de más abajo con más puntos que las de arriba
        status = run(ingest.run_once(FakeApi(ranking=broken).factory(), today=TODAY))
        self.assertFalse(status["ok"])
        self.assertIn("mayor a menor", status["mensaje"])
        self.assertEqual(self.files(), before)

    def test_too_few_players_or_tournaments_are_refused(self):
        # sin datos buenos de la API guardados se prueba Wikipedia (aquí no responde); el motivo de la API se conserva
        s1 = run(ingest.run_once(FakeApi(ranking=ranking_rows(5)).factory(), today=TODAY))
        self.assertFalse(s1["ok"])
        self.assertIn("solo 5 filas", s1["intento_padelapi"])
        self.assertEqual(s1["fuente_activa"], "ninguna")
        s2 = run(ingest.run_once(FakeApi(tournaments=season()[:3]).factory(), today=TODAY))
        self.assertFalse(s2["ok"])
        self.assertIn("solo 3 torneos", s2["intento_padelapi"])
        # con datos buenos de la API ya guardados no hay respaldo: el motivo sale directamente
        run(ingest.run_once(FakeApi().factory(), today=TODAY))
        s3 = run(ingest.run_once(FakeApi(ranking=ranking_rows(5)).factory(), today=TODAY))
        self.assertIn("solo 5 filas", s3["mensaje"])

    def test_without_a_token_the_old_wikipedia_path_is_used(self):
        os.environ.pop("PADELAPI_TOKEN")
        api = FakeApi()
        wiki_calls = []

        def handler(request):
            wiki_calls.append(request.url.host)
            return httpx.Response(500)
        status = run(ingest.run_once(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)), today=TODAY))
        self.assertFalse(status["ok"])
        self.assertTrue(wiki_calls and all("wikipedia" in h for h in wiki_calls))
        self.assertEqual(api.calls, [])

    def test_if_the_api_fails_and_there_is_nothing_to_keep_wikipedia_is_the_fallback(self):
        import test_ingest
        seen = []

        def handler(request):
            seen.append(request.url.host)
            if request.url.host == "padelapi.org":
                return httpx.Response(500)
            return httpx.Response(200, json=test_ingest.page(year=2026))
        status = run(ingest.run_once(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)), today=test_ingest.TODAY))
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["fuente_activa"], "wikipedia")
        self.assertIn("500", status["intento_padelapi"])
        self.assertEqual(self.read("ranking")["_meta"]["tipo"], "wikipedia")
        self.assertIn("padelapi.org", seen)

    def test_with_good_api_data_stored_wikipedia_is_not_used_as_fallback(self):
        run(ingest.run_once(FakeApi().factory(), today=TODAY))
        hosts = []

        def handler(request):
            hosts.append(request.url.host)
            return httpx.Response(503)
        status = run(ingest.run_once(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)), today=TODAY))
        self.assertFalse(status["ok"])
        self.assertEqual(set(hosts), {"padelapi.org"})
        self.assertEqual(self.read("ranking")["_meta"]["tipo"], "api")


if __name__ == "__main__":
    unittest.main()
