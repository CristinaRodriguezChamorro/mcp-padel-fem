"""
Pruebas de la actualización automática desde Wikipedia (sin internet): Wikipedia se simula con
httpx.MockTransport y páginas generadas aquí. Lo importante es lo que NO debe pasar: que datos
dudosos pisen a datos buenos, que un archivo quede a medias, o que algo peligroso llegue a la web.
"""
import asyncio
import json
import os
os.environ.setdefault("MCP_TOOLS", "extended")   # los tests usan todas las herramientas; el valor por defecto son 3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["WIKIPEDIA_INGEST"] = "off"

from tools import ingest
from tools import local_data as ld

TODAY = date(2026, 9, 29)
FLAG = '<span class="flagicon"><img alt="" src="x.png"></span>'


def ranking_html(n=10, names=None, points=None):
    rows = ""
    for i in range(n):
        name = names[i] if names else f"Jugadora Numero{i}"
        pts = points[i] if points else 20000 - i * 500
        rows += f"<tr><td>{i + 1}</td><td>{FLAG} ESP {name}</td><td>{pts}</td><td>0</td></tr>"
    return ('<table class="wikitable"><tr><th colspan="3">2026 Women\'s Ranking</th><th>Tournaments Won</th></tr>'
            '<tr><th>N.º</th><th>Name</th><th>Points</th><th>Total</th></tr>' + rows + "</table>")


def tournaments_html(n=10, year=2026, evil=False):
    rows = ""
    start = date(year, 2, 2)
    for i in range(n):
        s, e = start + timedelta(days=14 * i), start + timedelta(days=14 * i + 6)
        dates = f"{s.day} {s.strftime('%B')} – {e.day} {e.strftime('%B')}" if s.month != e.month else f"{s.day} – {e.day} {s.strftime('%B')}"
        if i < n // 2:
            win = "ESP Ana Garcia<br>ESP Eva Ruiz" if not (evil and i == 0) else "ESP Ana Garcia<br>&lt;img src=x onerror=alert(1)&gt;"
            cells = ["", "ESP A B<br>ESP C D", "6–4", "ESP E F<br>ESP G H", "", win, "6–3 / 6–4", "ARG Delfina Brea<br>ESP Gemma Triay"]
        else:
            cells = [""] * 8
        rows += f"<tr><td>ESP Ciudad{i} P1</td><td>{dates}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return ('<table class="wikitable"><tr><th rowspan="2">Tournament</th><th rowspan="2">Date</th><th colspan="4">Men\'s</th>'
            '<th colspan="4">Women\'s</th></tr><tr><th>Winners</th></tr>' + rows + "</table>")


def page(ranking=None, tournaments=None, updated="Updated on 22 August 2026", title="Premier Padel 2026", year=2026):
    html = (ranking if ranking is not None else ranking_html()) + (tournaments if tournaments is not None else tournaments_html(year=year))
    html += f"<p>{updated}</p>" + "<p>" + "relleno " * 80 + "</p>"
    return {"parse": {"title": title, "revid": 4242, "text": html}}


class Wiki:
    """Wikipedia de mentira: devuelve lo que se le pida y anota las peticiones."""
    def __init__(self, responder):
        self.responder, self.requests = responder, []

    def factory(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.responder(request)
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ok(**kw):
    return Wiki(lambda r: httpx.Response(200, json=page(**kw)))


class IngestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="padel-ingest-")
        self._old = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.dir

    def tearDown(self):
        os.environ.pop("DATA_DIR", None) if self._old is None else os.environ.__setitem__("DATA_DIR", self._old)

    def read(self, name):
        return json.loads(Path(self.dir, f"{name}.json").read_text(encoding="utf-8"))

    def snapshot(self):
        return {p.name: p.read_bytes() for p in Path(self.dir).iterdir() if not p.name.startswith("_")}

    async def run_ok(self):
        status = await ingest.run_once(ok().factory(), today=TODAY)
        self.assertTrue(status["ok"], status)
        return status


class SuccessTests(IngestBase):
    async def test_a_good_page_writes_both_files_with_provenance_and_the_pages_own_date(self):
        status = await self.run_ok()
        self.assertEqual((status["ranking_filas"], status["torneos"]), (10, 10))
        meta = self.read("ranking")["_meta"]
        self.assertEqual(meta["tipo"], "wikipedia")
        self.assertEqual(meta["actualizado"], "2026-08-22")          # la fecha de la página, no la de hoy
        self.assertEqual(meta["consultado"], "2026-09-29")
        self.assertEqual(meta["licencia"], "CC BY-SA 4.0")
        self.assertEqual(meta["url"], "https://en.wikipedia.org/wiki/Premier_Padel_2026")
        self.assertEqual(self.read("calendario")["_meta"], meta)
        self.assertEqual(status["fecha_fuente"], "2026-08-22")

    async def test_what_is_written_is_read_back_by_the_data_layer(self):
        await self.run_ok()
        ranking = await ld.get_ranking_live()
        self.assertEqual((ranking[0]["name"], ranking[0]["pts"], ranking[0]["flag"]), ("Jugadora Numero0", "20.000", "🇪🇸"))
        finals = await ld.get_recent_finals(today=date(2026, 12, 31))
        self.assertEqual(len(finals), 5)
        self.assertEqual(finals[0]["ganadoras"], "Ana Garcia / Eva Ruiz")
        self.assertEqual(finals[0]["finalistas"], "Delfina Brea / Gemma Triay")
        st = ld.dataset_status(TODAY)
        self.assertEqual((st["ranking"]["tipo"], st["ranking"]["antiguedad_dias"], st["ranking"]["desactualizado"]),
                         ("wikipedia", 38, True))

    async def test_only_one_polite_request_to_the_wikipedia_api(self):
        wiki = ok()
        await ingest.run_once(wiki.factory(), today=TODAY)
        self.assertEqual(len(wiki.requests), 1)
        r = wiki.requests[0]
        self.assertEqual((r.url.host, r.url.path), ("en.wikipedia.org", "/w/api.php"))
        self.assertTrue(r.headers["user-agent"].startswith("PadelFemBot/"))

    async def test_a_page_without_its_own_date_leaves_the_update_date_empty_instead_of_inventing_one(self):
        status = await ingest.run_once(ok(updated="sin fecha").factory(), today=TODAY)
        self.assertTrue(status["ok"])
        self.assertIsNone(self.read("ranking")["_meta"]["actualizado"])

    async def test_dangerous_text_in_the_page_never_reaches_the_files(self):
        wiki = ok(tournaments=tournaments_html(evil=True),
                  ranking=ranking_html(names=["Jugadora Uno", "<img src=x onerror=alert(1)>"] + [f"Jugadora Numero{i}" for i in range(2, 10)]))
        status = await ingest.run_once(wiki.factory(), today=TODAY)
        self.assertTrue(status["ok"], status)                              # 9 filas válidas bastan
        raw = Path(self.dir, "ranking.json").read_text(encoding="utf-8") + Path(self.dir, "calendario.json").read_text(encoding="utf-8")
        for bad in ("onerror", "<img", "alert("):
            self.assertNotIn(bad, raw)
        self.assertNotIn("final_femenina", self.read("calendario")["torneos"][0])


class RefusalTests(IngestBase):
    async def seed(self):
        await self.run_ok()
        return self.snapshot()

    async def refuses(self, wiki, fragment):
        before = await self.seed()
        status = await ingest.run_once(wiki.factory(), today=TODAY + timedelta(days=1))
        self.assertFalse(status["ok"])
        self.assertIn(fragment, status["mensaje"])
        self.assertIn("se conservan", status["mensaje"].lower())
        self.assertEqual(self.snapshot(), before, "los datos anteriores no deben tocarse")
        self.assertEqual(status["ranking_filas"], 10, "el estado conserva los datos del último éxito")

    async def test_too_few_players(self):
        await self.refuses(ok(ranking=ranking_html(3)), "solo 3 filas")

    async def test_table_that_shrank_a_lot(self):
        wiki = ok(ranking=ranking_html(14))
        await self.run_ok_with(ranking_html(30))
        before = self.snapshot()
        status = await ingest.run_once(wiki.factory(), today=TODAY)
        self.assertFalse(status["ok"])
        self.assertIn("pasó de 30 a 14", status["mensaje"])
        self.assertEqual(self.snapshot(), before)

    async def run_ok_with(self, ranking):
        status = await ingest.run_once(ok(ranking=ranking).factory(), today=TODAY)
        self.assertTrue(status["ok"], status)

    async def test_points_not_in_descending_order(self):
        points = [20000, 21000] + [19000 - i * 100 for i in range(8)]
        await self.refuses(ok(ranking=ranking_html(points=points)), "de mayor a menor")

    async def test_repeated_players(self):
        names = ["Jugadora Uno"] * 2 + [f"Jugadora Numero{i}" for i in range(8)]
        await self.refuses(ok(ranking=ranking_html(names=names)), "repetidas")

    async def test_too_few_tournaments(self):
        await self.refuses(ok(tournaments=tournaments_html(4)), "solo 4 torneos")

    async def test_no_tables_at_all(self):
        await self.refuses(ok(ranking="<p>nada</p>", tournaments="<p>nada</p>"), "solo 0 filas")

    async def test_wikipedia_errors(self):
        for label, responder in {
            "http": lambda r: httpx.Response(503),
            "maxlag": lambda r: httpx.Response(200, json={"error": {"code": "maxlag"}}),
            "garbage": lambda r: httpx.Response(200, text="<html>"),
            "down": lambda r: (_ for _ in ()).throw(httpx.ConnectError("sin red")),
        }.items():
            before = await self.seed()
            status = await ingest.run_once(Wiki(responder).factory(), today=TODAY)
            self.assertFalse(status["ok"], label)
            self.assertEqual(self.snapshot(), before, label)

    async def test_it_never_raises_even_on_unexpected_errors(self):
        class Boom(Wiki):
            def factory(self):
                def f():
                    raise RuntimeError("fallo imprevisto")
                return f
        status = await ingest.run_once(Boom(None).factory(), today=TODAY)
        self.assertFalse(status["ok"])
        self.assertIn("RuntimeError", status["mensaje"])

    async def test_an_unexpected_bug_in_the_parser_is_contained_too(self):
        before = await self.seed()
        real = ingest.ws.parse_women_ranking
        ingest.ws.parse_women_ranking = lambda html: {}["clave que no existe"]
        try:
            status = await ingest.run_once(ok().factory(), today=TODAY)
        finally:
            ingest.ws.parse_women_ranking = real
        self.assertFalse(status["ok"])
        self.assertIn("Error inesperado (KeyError)", status["mensaje"])
        self.assertEqual(self.snapshot(), before)

    async def test_status_remembers_the_last_success_after_a_failure(self):
        await self.run_ok()
        first = ingest.ingest_status()["ultimo_exito"]
        await ingest.run_once(Wiki(lambda r: httpx.Response(503)).factory(), today=TODAY)
        st = ingest.ingest_status()
        self.assertFalse(st["ok"])
        self.assertEqual(st["ultimo_exito"], first)
        self.assertEqual(st["estado"], "error")

    async def test_unwritable_data_dir_is_a_failure_not_a_crash(self):
        os.environ["DATA_DIR"] = "/proc/no-se-puede-escribir/aqui"
        status = await ingest.run_once(ok().factory(), today=TODAY)
        self.assertFalse(status["ok"])


class SeasonFallbackTests(IngestBase):
    async def test_in_january_a_missing_new_page_falls_back_to_last_season(self):
        def responder(request):
            if request.url.params["page"] == "Premier_Padel_2027":
                return httpx.Response(200, json={"error": {"code": "missingtitle", "info": "no existe"}})
            return httpx.Response(200, json=page(year=2026))
        wiki = Wiki(responder)
        status = await ingest.run_once(wiki.factory(), today=date(2027, 1, 10))
        self.assertTrue(status["ok"], status)
        self.assertEqual([r.url.params["page"] for r in wiki.requests], ["Premier_Padel_2027", "Premier_Padel_2026"])
        self.assertTrue(self.read("ranking")["_meta"]["url"].endswith("Premier_Padel_2026"))

    async def test_outside_january_and_february_it_does_not_look_at_other_seasons(self):
        wiki = Wiki(lambda r: httpx.Response(200, json={"error": {"code": "missingtitle"}}))
        status = await ingest.run_once(wiki.factory(), today=date(2026, 9, 29))
        self.assertFalse(status["ok"])
        self.assertEqual(len(wiki.requests), 1)


class AtomicWriteTests(IngestBase):
    def test_a_failure_while_writing_leaves_the_original_and_no_leftovers(self):
        path = Path(self.dir, "ranking.json")
        path.write_text('{"original": true}', encoding="utf-8")
        real = ingest.json.dump
        ingest.json.dump = lambda *a, **k: (_ for _ in ()).throw(OSError("disco lleno"))
        try:
            with self.assertRaises(OSError):
                ingest._atomic_write(path, {"nuevo": True})
        finally:
            ingest.json.dump = real
        self.assertEqual(path.read_text(encoding="utf-8"), '{"original": true}')
        self.assertEqual([p.name for p in Path(self.dir).iterdir()], ["ranking.json"])

    def test_a_normal_write_replaces_the_file_whole(self):
        path = Path(self.dir, "x.json")
        ingest._atomic_write(path, {"a": "ñ"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": "ñ"})
        self.assertEqual([p.name for p in Path(self.dir).iterdir()], ["x.json"])


class ConfigAndLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("WIKIPEDIA_INGEST", "WIKIPEDIA_INGEST_HOURS", "INGEST_START_DELAY")}

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_env_parsing(self):
        for value, expected in (("", True), ("on", True), ("off", False), ("OFF", False), ("0", False), ("false", False), ("no", False)):
            os.environ["WIKIPEDIA_INGEST"] = value
            self.assertEqual(ingest.enabled(), expected, value)
        for value, expected in (("", 24.0), ("abc", 24.0), ("0.2", 1.0), ("6", 6.0), ("48", 48.0)):
            os.environ["WIKIPEDIA_INGEST_HOURS"] = value
            self.assertEqual(ingest.interval_hours(), expected, value)
        for value, expected in (("", 15.0), ("x", 15.0), ("-3", 0.0), ("2", 2.0)):
            os.environ["INGEST_START_DELAY"] = value
            self.assertEqual(ingest._start_delay(), expected, value)

    async def test_the_loop_waits_then_backs_off_on_failures_and_returns_to_the_normal_pace(self):
        os.environ["WIKIPEDIA_INGEST_HOURS"] = "24"
        waits, results = [], iter([{"ok": False}] * 5 + [{"ok": True}] * 3)

        async def fake_sleep(seconds):
            waits.append(seconds)
            if len(waits) >= 9:
                raise asyncio.CancelledError

        async def fake_run():
            return next(results)

        with self.assertRaises(asyncio.CancelledError):
            await ingest.run_forever(sleep=fake_sleep, first_delay=7, run=fake_run)
        h = 3600
        self.assertEqual(waits, [7, 1 * h, 2 * h, 4 * h, 6 * h, 6 * h, 24 * h, 24 * h, 24 * h])

    async def test_the_server_lifespan_starts_and_stops_the_task_and_respects_the_switch(self):
        from fastapi import FastAPI
        import server
        from padel_mcp.tools import build_mcp

        async def empty():
            return {"data": [], "age_s": 0, "fallback": None}

        events = []

        async def fake_forever():
            events.append("start")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("cancelled")
                raise

        real = server.ingest.run_forever
        server.ingest.run_forever = fake_forever
        try:
            for value, expected in (("on", ["start", "cancelled"]), ("off", [])):
                events.clear()
                os.environ["WIKIPEDIA_INGEST"] = value
                mcp = build_mcp({k: empty for k in ("ranking", "calendar", "live", "finals", "news")})
                life = server.make_lifespan(mcp.http_app(path="/mcp", stateless_http=True))
                async with life(FastAPI()):
                    await asyncio.sleep(0.05)
                self.assertEqual(events, expected, value)
        finally:
            server.ingest.run_forever = real


if __name__ == "__main__":
    unittest.main()
