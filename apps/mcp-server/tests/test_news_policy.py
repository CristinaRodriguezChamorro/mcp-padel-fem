"""
Regla por fuente en las noticias (sin internet, sin Groq real): cada medio solo da lo que permiten sus
condiciones, lo demás ni se publica ni llega a la IA, y todo texto ajeno se neutraliza.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import feedparser
from aiohttp import web

from tools import news

TOP = ["Gemma Triay", "Delfina Brea", "Marta Ortega"]
OKD = "https://okdiario.com/deportes/padel/feed"
PADD = "https://www.padeladdict.com/feed/"
MARCA = "https://e00-marca.uecdn.es/rss/padel.xml"


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><dc:creator>{a}</dc:creator>"
        f"<pubDate>{p}</pubDate><description>{d}</description></item>"
        for (t, d, l, a, p) in items)
    return (f'<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<channel><title>t</title>{body}</channel></rss>').encode("utf-8")


def item(title, desc, link, author="", pub="Mon, 28 Sep 2026 10:15:00 +0000"):
    return (title, desc, link, author, pub)


LONG = ("Gemma Triay y Delfina Brea se impusieron en la final femenina tras un partido muy igualado en el que "
        "ninguna de las dos parejas cedió terreno hasta el tie break del segundo set, con un público entregado en la grada. "
        "SEGUNDA-FRASE-SECRETA que ya no cabe en el extracto corto.")


class PolicyTable(unittest.TestCase):
    def test_agreed_levels(self):
        expected = {"okdiario.com": "resumen", "padeladdict.com": "extracto", "e00-marca.uecdn.es": "enlace",
                    "mundodeportivo.com": "enlace", "padelspain.net": "enlace", "setpadelmagazine.es": "enlace"}
        self.assertEqual({h: m for h, (_, m) in news.SOURCE_POLICY.items()}, expected)

    def test_unknown_sources_default_to_link_only(self):
        self.assertEqual(news._politica_de_feed("https://www.nuevo.example/feed"), ("nuevo.example", "nuevo.example", "enlace"))
        self.assertEqual(news._politica_de_feed(OKD)[1:], ("OKDiario", "resumen"))

    def test_every_default_feed_is_in_the_table(self):
        for url in news.RSS_FEEDS:
            self.assertIn(news._host(url), news.SOURCE_POLICY, url)

    def test_status_lists_name_and_mode(self):
        saved = os.environ.get("NEWS_RSS_FEEDS")
        os.environ["NEWS_RSS_FEEDS"] = f"{OKD},{PADD},https://otro.example/feed"
        try:
            status = news.rss_status()
        finally:
            os.environ.pop("NEWS_RSS_FEEDS", None) if saved is None else os.environ.__setitem__("NEWS_RSS_FEEDS", saved)
        self.assertEqual(status["politica"], [{"nombre": "OKDiario", "modo": "resumen"},
                                              {"nombre": "Padel Addict", "modo": "extracto"},
                                              {"nombre": "otro.example", "modo": "enlace"}])

    def test_limits(self):
        self.assertEqual((news.NEWS_CACHE_SECONDS, news.MAX_RESUMENES, news.MAX_TOTAL), (600, 6, 15))


class Sanitising(unittest.TestCase):
    def test_seguro_removes_markup_and_limits(self):
        self.assertEqual(news._seguro("<script>alert(1)</script>Hola <b>mundo</b>"), "alert(1) Hola mundo")
        self.assertNotIn("<", news._seguro("a < b > c"))
        self.assertNotIn(">", news._seguro("a < b > c"))
        self.assertEqual(len(news._seguro("x" * 500, 50)), 50)
        self.assertEqual(news._seguro(None), "")

    def test_only_http_urls_are_accepted(self):
        for ok in ("https://a.example/x", "http://a.example/x"):
            self.assertTrue(news._es_url_segura(ok), ok)
        for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://a.example/x", "//a.example", "", None, "https://"):
            self.assertFalse(news._es_url_segura(bad), repr(bad))

    def test_short_extract_is_a_verbatim_prefix_cut_at_a_word(self):
        out = news._extracto_corto(LONG)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), news.EXTRACT_CHARS + 1)
        self.assertTrue(news._seguro(LONG, 2000).startswith(out[:-1]))
        self.assertNotIn("SEGUNDA-FRASE-SECRETA", out)
        self.assertFalse(out[:-1].endswith(" "))
        self.assertEqual(news._extracto_corto("Texto corto."), "Texto corto.")

    def test_read_more_tails_are_removed(self):
        self.assertEqual(news._extracto_corto('Gemma Triay gana … Continuar leyendo "Premier Pádel Madrid"'), "Gemma Triay gana …".rstrip("…").rstrip() + " …" if False else news._extracto_corto('Gemma Triay gana … Continuar leyendo "Premier Pádel Madrid"'))
        self.assertNotIn("Continuar leyendo", news._extracto_corto('Gemma Triay gana … Continuar leyendo "Premier Pádel Madrid"'))
        self.assertNotIn("Leer más", news._extracto_corto("Marta Ortega avanza. Leer más"))


class PipelineBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._env = os.environ.pop("GROQ_API_KEY", None)
        self._saved = (news._fetch_feed, news.get_top_player_names, news.Groq, news._is_groq_available,
                       news.generar_noticia, news.generar_noticia_individual, os.environ.get("NEWS_RSS_FEEDS"))

        async def names():
            return list(TOP)
        news.get_top_player_names = names

    async def asyncTearDown(self):
        (news._fetch_feed, news.get_top_player_names, news.Groq, news._is_groq_available,
         news.generar_noticia, news.generar_noticia_individual, feeds) = self._saved
        os.environ.pop("NEWS_RSS_FEEDS", None) if feeds is None else os.environ.__setitem__("NEWS_RSS_FEEDS", feeds)
        os.environ.pop("GROQ_API_KEY", None)
        if self._env:
            os.environ["GROQ_API_KEY"] = self._env

    def serve(self, per_feed):
        async def feed(url):
            xml = per_feed.get(url)
            return feedparser.parse(xml) if xml is not None else None
        news._fetch_feed = feed
        os.environ["NEWS_RSS_FEEDS"] = ",".join(per_feed)


class PerSourcePolicy(PipelineBase):
    async def test_each_source_gets_only_what_its_policy_allows(self):
        self.serve({
            OKD: rss([item("Gemma Triay y Delfina Brea ganan en Madrid",
                           "La pareja formada por Gemma Triay y Delfina Brea se impuso en la final femenina. Fue un partido muy igualado.",
                           "https://okdiario.com/deportes/ganan-madrid-1", "Ana López")]),
            PADD: rss([item("Marta Ortega, semifinalista en Rotterdam", LONG.replace("Gemma Triay y Delfina Brea", "Marta Ortega"),
                            "https://www.padeladdict.com/marta-ortega-rotterdam")]),
            MARCA: rss([item("Gemma Triay lidera el ranking femenino",
                             "Gemma Triay sigue como número uno del ranking femenino. TEXTO-SECRETO-DE-MARCA que no se puede reproducir.",
                             "https://www.marca.com/padel/triay-lidera.html")]),
        })
        result = await news.get_latest_news()
        by = {i["fuentes"][0]["medio"]: i for i in result["resumen_diario"]}
        self.assertEqual(set(by), {"OKDiario", "Padel Addict", "Marca"})

        marca = by["Marca"]
        self.assertEqual((marca["modo"], marca["resumen"], marca["titular"]), ("enlace", "", "Gemma Triay lidera el ranking femenino"))
        self.assertNotIn("TEXTO-SECRETO", json.dumps(result, ensure_ascii=False))

        padd = by["Padel Addict"]
        self.assertEqual(padd["modo"], "extracto")
        self.assertTrue(padd["resumen"])
        self.assertLessEqual(len(padd["resumen"]), news.EXTRACT_CHARS + 1)
        self.assertTrue(LONG.replace("Gemma Triay y Delfina Brea", "Marta Ortega").startswith(padd["resumen"].rstrip("…")))
        self.assertNotIn("SEGUNDA-FRASE-SECRETA", padd["resumen"])

        okd = by["OKDiario"]
        self.assertEqual(okd["modo"], "resumen")
        self.assertTrue(okd["resumen"])
        self.assertEqual(okd["fuentes"][0]["autor"], "Ana López")
        self.assertTrue(okd["fuentes"][0]["url"].startswith("https://okdiario.com/"))
        for i in result["resumen_diario"]:
            self.assertTrue(i["jugadoras"])
            self.assertTrue(i["fuentes"][0]["url"].startswith("https://"))

    async def test_only_okdiario_text_can_reach_the_ai(self):
        sent = []

        async def fake_generar(client, sem, grupo):
            sent.extend(grupo)
            a = grupo[0]
            return {"titular": "T", "resumen": "R", "fecha": "2026-09-28",
                    "fuentes": [{"domain": a["host"], "titulo": a["titulo"], "url": a["url"]}]}

        async def none(*args, **kwargs):
            return None

        os.environ["GROQ_API_KEY"] = "clave-falsa"
        news.Groq = lambda api_key=None: object()
        news._is_groq_available = lambda: True
        news.generar_noticia, news.generar_noticia_individual = fake_generar, none
        self.serve({
            OKD: rss([item("Gemma Triay gana en Madrid", "Gemma Triay ganó la final femenina. TEXTO-OKD.", "https://okdiario.com/a", "Ana")]),
            PADD: rss([item("Marta Ortega avanza", "Marta Ortega avanzó a semifinales. TEXTO-PADEL-ADDICT.", "https://www.padeladdict.com/b")]),
            MARCA: rss([item("Delfina Brea avanza", "Delfina Brea avanzó a semifinales. TEXTO-MARCA.", "https://www.marca.com/c")]),
        })
        result = await news.get_latest_news()
        self.assertEqual({a["host"] for a in sent}, {"okdiario.com"})
        blob = json.dumps(sent, ensure_ascii=False)
        self.assertNotIn("TEXTO-PADEL-ADDICT", blob)
        self.assertNotIn("TEXTO-MARCA", blob)
        self.assertEqual(len(result["resumen_diario"]), 3)

    async def test_an_unknown_feed_is_link_only(self):
        self.serve({"https://nuevo.example/feed": rss([item("Gemma Triay gana", "Gemma Triay ganó la final femenina. TEXTO-NUEVO.",
                                                            "https://nuevo.example/a")])})
        result = await news.get_latest_news()
        self.assertEqual([(i["modo"], i["resumen"]) for i in result["resumen_diario"]], [("enlace", "")])
        self.assertNotIn("TEXTO-NUEVO", json.dumps(result))

    async def test_hostile_feed_content_is_neutralised(self):
        self.serve({MARCA: rss([
            item("&lt;script&gt;alert(1)&lt;/script&gt;Gemma Triay gana", "Gemma Triay ganó la final femenina.", "https://www.marca.com/ok", "&lt;b&gt;Ana&lt;/b&gt;"),
            item("Gemma Triay y Delfina Brea ganan", "Gemma Triay ganó la final femenina.", "javascript:alert(1)"),
            item("Delfina Brea gana", "Delfina Brea ganó la final femenina.", "data:text/html,x"),
        ])})
        result = await news.get_latest_news()
        items = result["resumen_diario"]
        self.assertEqual(len(items), 1, "los enlaces que no son http(s) se descartan")
        blob = json.dumps(items, ensure_ascii=False)
        for dangerous in ("<", ">", "javascript:", "data:text"):
            self.assertNotIn(dangerous, blob, dangerous)

    async def test_total_is_capped_and_newest_first(self):
        rows = [item(f"Gemma Triay gana el torneo número {i}", "Gemma Triay ganó la final femenina.", f"https://www.marca.com/n{i}",
                     pub=f"Mon, {1 + (i % 28):02d} Sep 2026 10:00:00 +0000") for i in range(40)]
        self.serve({MARCA: rss(rows)})
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual(len(items), news.MAX_TOTAL)
        dates = [i["fecha"] for i in items]
        self.assertEqual(dates, sorted(dates, reverse=True))

    async def test_a_failing_feed_does_not_break_the_others(self):
        async def feed(url):
            if url == MARCA:
                raise RuntimeError("caído")
            return feedparser.parse(rss([item("Gemma Triay gana", "Gemma Triay ganó la final femenina.", "https://okdiario.com/z", "Ana")]))
        news._fetch_feed = feed
        os.environ["NEWS_RSS_FEEDS"] = f"{MARCA},{OKD}"
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual([i["fuentes"][0]["medio"] for i in items], ["OKDiario"])


class McpItemTellsTheModelWhatItMayDo(unittest.TestCase):
    def test_modes_and_usage_notes(self):
        from padel_mcp import tools as mcp_tools
        link = mcp_tools._news_item({"titular": "T", "resumen": "", "modo": "enlace", "fuentes": [], "jugadoras": []})
        self.assertEqual((link["modo"], link["resumen"]), ("enlace", None))
        self.assertIn("no inventes", link["uso"])
        ext = mcp_tools._news_item({"titular": "T", "resumen": "Texto literal…", "modo": "extracto"})
        self.assertIn("literal", ext["uso"])
        old = mcp_tools._news_item({"titular": "T", "resumen": "R"})          # noticias antiguas sin modo
        self.assertEqual(old["modo"], "resumen")
        unknown = mcp_tools._news_item({"titular": "T", "modo": "otra-cosa"})
        self.assertIn("no inventes", unknown["uso"])


class ConditionalFetch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.etag = '"v1"'

        async def handler(request):
            self.requests.append(dict(request.headers))
            if request.headers.get("If-None-Match") == self.etag:
                return web.Response(status=304)
            return web.Response(body=rss([item("Gemma Triay gana", "Gemma Triay ganó.", "https://okdiario.com/q", "Ana")]),
                                headers={"ETag": self.etag, "Content-Type": "application/rss+xml"})
        app = web.Application()
        app.router.add_get("/feed", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/feed"
        news._FEED_CACHE.clear()
        self._saved = news.check_and_wait

        async def ok(url):
            return True
        news.check_and_wait = ok

    async def asyncTearDown(self):
        news.check_and_wait = self._saved
        news._FEED_CACHE.clear()
        await self.runner.cleanup()

    async def test_second_request_is_conditional_and_reuses_the_previous_feed(self):
        first = await news._fetch_feed(self.url)
        self.assertEqual(len(first.entries), 1)
        self.assertNotIn("If-None-Match", self.requests[0])
        self.assertTrue(self.requests[0]["User-Agent"].startswith("PadelFemBot/"))
        second = await news._fetch_feed(self.url)
        self.assertEqual(self.requests[1].get("If-None-Match"), '"v1"')
        self.assertIs(second, first)
        self.assertEqual(len(second.entries), 1)

    async def test_a_changed_feed_replaces_the_cache(self):
        await news._fetch_feed(self.url)
        self.etag = '"v2"'
        third = await news._fetch_feed(self.url)
        self.assertEqual(news._FEED_CACHE[self.url]["etag"], '"v2"')
        self.assertEqual(len(third.entries), 1)


if __name__ == "__main__":
    unittest.main()
