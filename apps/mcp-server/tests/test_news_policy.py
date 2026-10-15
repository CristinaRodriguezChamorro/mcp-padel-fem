"""
Qué noticias se publican y cómo (sin internet y sin Groq real):
  - solo las fuentes cuyas condiciones permiten resumir o citar (OKDiario: resumen; Padel Addict: extracto);
    cualquier otra se ignora y ni siquiera se pide;
  - cada noticia sale con su fecha REAL, un resumen de al menos unas tres líneas y el enlace directo;
    sin ese resumen, no sale;
  - lo que el medio no permite no llega a la IA, y todo texto ajeno se neutraliza.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MCP_TOOLS", "extended")

import feedparser
from aiohttp import web

from tools import news

TOP = ["Gemma Triay", "Delfina Brea", "Marta Ortega"]
MEN = ["Arturo Coello", "Agustin Tapia", "Alejandro Galan", "Federico Chingotto"]
OKD = "https://okdiario.com/deportes/padel/feed"
PADD = "https://www.padeladdict.com/feed/"
MARCA = "https://e00-marca.uecdn.es/rss/padel.xml"          # una fuente sin permiso verificado: se ignora


def rss(items):
    """items: (titulo, descripcion, enlace, autor, fecha | None [, contenido completo])"""
    body = ""
    for it in items:
        t, d, l, a, p = it[:5]
        c = it[5] if len(it) > 5 else None
        body += (f"<item><title>{t}</title><link>{l}</link><dc:creator>{a}</dc:creator>"
                 + ("" if p is None else f"<pubDate>{p}</pubDate>")
                 + f"<description>{d}</description>"
                 + ("" if c is None else f"<content:encoded><![CDATA[{c}]]></content:encoded>") + "</item>")
    return (f'<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>t</title>{body}</channel></rss>').encode("utf-8")


def item(title, desc, link, author="", pub="Mon, 28 Sep 2026 10:15:00 +0000", content=None):
    return (title, desc, link, author, pub) if content is None else (title, desc, link, author, pub, content)


# Una crónica femenina con varias frases largas que nombran a las jugadoras: da para un resumen de tres líneas.
WOMEN_TEXT = ("La pareja formada por Gemma Triay y Delfina Brea se impuso en la final femenina del Madrid P1 tras un partido muy igualado. "
              "Triay y Brea sellaron el título en dos sets ante sus rivales, con un público entregado en la grada del pabellón. "
              "Delfina Brea dedicó el triunfo a toda su familia después de recibir el trofeo de campeonas. "
              "La pareja suma así su tercer título de la temporada.")
ORTEGA_TEXT = ("Marta Ortega alcanzó las semifinales del torneo de Rotterdam tras vencer en tres sets a la pareja favorita del cuadro femenino. "
               "La jugadora madrileña mostró un gran nivel durante todo el partido y selló el pase con un último juego muy sólido. "
               "Marta Ortega se mostró muy satisfecha con su semana en el torneo y con el apoyo recibido. Más información sobre la jornada.")
# Más de 300 caracteres antes de la última frase: el extracto corto nunca la alcanza.
LONG = ("Marta Ortega y Martina Calvo se impusieron en la final femenina tras un partido muy igualado en el que ninguna de las dos parejas cedió terreno "
        "hasta el tie break del segundo set, con un público entregado en la grada del pabellón y una gran atmósfera durante toda la tarde. "
        "Marta Ortega firmó una de sus mejores actuaciones de la temporada y la crónica femenina destaca su regularidad en la red durante todo el torneo. "
        "SEGUNDA-FRASE-SECRETA que ya no cabe en el extracto corto.")


class PolicyTable(unittest.TestCase):
    def test_only_the_two_sources_that_allow_it_are_published(self):
        self.assertEqual({h: m for h, (_, m) in news.SOURCE_POLICY.items()},
                         {"okdiario.com": "resumen", "padeladdict.com": "extracto"})
        self.assertEqual(news.PUBLISHABLE_MODES, ("resumen", "extracto"))

    def test_unknown_sources_are_not_published(self):
        self.assertEqual(news._politica_de_feed("https://www.nuevo.example/feed"), ("nuevo.example", "nuevo.example", "ninguno"))
        self.assertEqual(news._politica_de_feed(OKD)[1:], ("OKDiario", "resumen"))
        self.assertEqual(news._politica_de_feed(MARCA)[2], "ninguno")

    def test_every_default_feed_is_in_the_table_and_publishable(self):
        for url in news.RSS_FEEDS:
            self.assertIn(news._host(url), news.SOURCE_POLICY, url)
            self.assertIn(news._politica_de_feed(url)[2], news.PUBLISHABLE_MODES)

    def test_status_lists_the_active_sources_and_the_ignored_ones(self):
        saved = os.environ.get("NEWS_RSS_FEEDS")
        os.environ["NEWS_RSS_FEEDS"] = f"{OKD},{PADD},https://otro.example/feed,{MARCA}"
        try:
            status = news.rss_status()
        finally:
            os.environ.pop("NEWS_RSS_FEEDS", None) if saved is None else os.environ.__setitem__("NEWS_RSS_FEEDS", saved)
        self.assertEqual(status["politica"], [{"nombre": "OKDiario", "modo": "resumen"}, {"nombre": "Padel Addict", "modo": "extracto"}])
        self.assertEqual(status["fuentes"], ["OKDiario", "Padel Addict"])
        self.assertEqual(status["ignoradas"], ["e00-marca.uecdn.es", "otro.example"])

    def test_limits(self):
        self.assertEqual((news.NEWS_CACHE_SECONDS, news.MAX_RESUMENES, news.MAX_TOTAL), (600, 6, 15))
        self.assertEqual((news.EXTRACT_CHARS, news.MIN_SUMMARY_CHARS), (300, 200))
        self.assertGreater(news.EXTRACT_CHARS, news.MIN_SUMMARY_CHARS, "el extracto tiene que poder llegar al mínimo")


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
        self.assertGreaterEqual(len(out), news.MIN_SUMMARY_CHARS)
        self.assertTrue(news._seguro(LONG, 2000).startswith(out[:-1]))
        self.assertNotIn("SEGUNDA-FRASE-SECRETA", out)
        self.assertFalse(out[:-1].endswith(" "))
        self.assertEqual(news._extracto_corto("Texto corto."), "Texto corto.")

    def test_read_more_tails_are_removed(self):
        tail = 'Gemma Triay gana … Continuar leyendo "Premier Pádel Madrid"'
        self.assertNotIn("Continuar leyendo", news._extracto_corto(tail))
        self.assertNotIn("Leer más", news._extracto_corto("Marta Ortega avanza. Leer más"))


class Publicable(unittest.TestCase):
    """La última barrera: nada sale sin titular, sin resumen de unas tres líneas o sin enlace directo."""
    GOOD = {"titular": "T", "resumen": "R" * news.MIN_SUMMARY_CHARS, "fuentes": [{"url": "https://okdiario.com/x"}]}

    def test_a_complete_item_is_published(self):
        self.assertTrue(news._publicable(self.GOOD))

    def test_each_missing_piece_blocks_it(self):
        for broken in ({"titular": ""}, {"titular": "   "}, {"resumen": ""}, {"resumen": None},
                       {"resumen": "R" * (news.MIN_SUMMARY_CHARS - 1)}, {"fuentes": []}, {"fuentes": None},
                       {"fuentes": [{"url": ""}]}, {"fuentes": [{"url": "javascript:alert(1)"}]},
                       {"fuentes": [{"url": "ftp://a.example/x"}]}):
            self.assertFalse(news._publicable({**self.GOOD, **broken}), repr(broken))

    def test_the_limit_is_exactly_the_minimum(self):
        self.assertTrue(news._publicable({**self.GOOD, "resumen": "R" * news.MIN_SUMMARY_CHARS}))
        self.assertTrue(news._publicable({**self.GOOD, "resumen": "  " + "R" * news.MIN_SUMMARY_CHARS + "  "}))
        self.assertFalse(news._publicable({**self.GOOD, "resumen": "  " + "R" * (news.MIN_SUMMARY_CHARS - 1) + "  "}))


class PipelineBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._env = os.environ.pop("GROQ_API_KEY", None)
        self._saved = (news._fetch_feed, news.get_top_player_names, news.Groq, news._is_groq_available,
                       news.generar_noticia, news.generar_noticia_individual, os.environ.get("NEWS_RSS_FEEDS"),
                       news.get_top_men_names)
        self.requested = []

        async def names():
            return list(TOP)

        async def men():
            return list(MEN)
        news.get_top_player_names = names
        news.get_top_men_names = men

    async def asyncTearDown(self):
        (news._fetch_feed, news.get_top_player_names, news.Groq, news._is_groq_available,
         news.generar_noticia, news.generar_noticia_individual, feeds, news.get_top_men_names) = self._saved
        os.environ.pop("NEWS_RSS_FEEDS", None) if feeds is None else os.environ.__setitem__("NEWS_RSS_FEEDS", feeds)
        os.environ.pop("GROQ_API_KEY", None)
        if self._env:
            os.environ["GROQ_API_KEY"] = self._env

    def serve(self, per_feed):
        async def feed(url):
            self.requested.append(url)
            xml = per_feed.get(url)
            return feedparser.parse(xml) if xml is not None else None
        news._fetch_feed = feed
        os.environ["NEWS_RSS_FEEDS"] = ",".join(per_feed)


class PerSourcePolicy(PipelineBase):
    async def test_each_published_source_gets_only_what_its_policy_allows(self):
        self.serve({
            OKD: rss([item("Gemma Triay y Delfina Brea ganan en Madrid", WOMEN_TEXT, "https://okdiario.com/deportes/ganan-madrid-1", "Ana López")]),
            PADD: rss([item("Marta Ortega, semifinalista en Rotterdam", LONG, "https://www.padeladdict.com/marta-ortega-rotterdam")]),
            MARCA: rss([item("Gemma Triay lidera el ranking femenino", "Gemma Triay sigue como número uno del ranking femenino. TEXTO-SECRETO-DE-MARCA.",
                             "https://www.marca.com/padel/triay-lidera.html")]),
        })
        result = await news.get_latest_news()
        by = {i["fuentes"][0]["medio"]: i for i in result["resumen_diario"]}
        self.assertEqual(set(by), {"OKDiario", "Padel Addict"}, "Marca no tiene permiso verificado: no se publica")
        self.assertNotIn(MARCA, self.requested, "ni siquiera se le pide el feed")
        self.assertNotIn("TEXTO-SECRETO", json.dumps(result, ensure_ascii=False))

        padd = by["Padel Addict"]
        self.assertEqual(padd["modo"], "extracto")
        self.assertGreaterEqual(len(padd["resumen"]), news.MIN_SUMMARY_CHARS)
        self.assertLessEqual(len(padd["resumen"]), news.EXTRACT_CHARS + 1)
        self.assertTrue(LONG.startswith(padd["resumen"].rstrip("…")), "extracto literal, sin reescribir")
        self.assertNotIn("SEGUNDA-FRASE-SECRETA", padd["resumen"])

        okd = by["OKDiario"]
        self.assertEqual(okd["modo"], "resumen")
        self.assertGreaterEqual(len(okd["resumen"]), news.MIN_SUMMARY_CHARS)
        self.assertEqual(okd["fuentes"][0]["autor"], "Ana López")
        self.assertTrue(okd["fuentes"][0]["url"].startswith("https://okdiario.com/"))

    async def test_every_published_item_has_summary_real_date_and_direct_link(self):
        self.serve({OKD: rss([item("Gemma Triay y Delfina Brea ganan en Madrid", WOMEN_TEXT, "https://okdiario.com/a", "Ana")]),
                    PADD: rss([item("Marta Ortega, semifinalista en Rotterdam", LONG, "https://www.padeladdict.com/b",
                                    pub="Sun, 27 Sep 2026 08:00:00 +0000")])})
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual(len(items), 2)
        self.assertEqual({i["fecha"] for i in items}, {"2026-09-28", "2026-09-27"})
        for i in items:
            self.assertGreaterEqual(len(i["resumen"]), news.MIN_SUMMARY_CHARS, i["titular"])
            self.assertRegex(i["fuentes"][0]["url"], r"^https://")
            self.assertTrue(i["jugadoras"])

    async def test_an_item_without_a_three_line_summary_is_not_published(self):
        short = "Marta Ortega avanza a semifinales en la jornada femenina del torneo."
        self.serve({PADD: rss([item("Marta Ortega, semifinalista en Rotterdam", short, "https://www.padeladdict.com/corta")]),
                    OKD: rss([item("Gemma Triay gana el torneo femenino", "Gemma Triay ganó la final femenina.", "https://okdiario.com/corta")])})
        self.assertEqual((await news.get_latest_news())["resumen_diario"], [])

    async def test_an_extract_without_a_date_has_no_date_either(self):
        self.serve({PADD: rss([item("Marta Ortega, semifinalista en Rotterdam", LONG, "https://www.padeladdict.com/sin-fecha", pub=None)])})
        (only,) = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual(only["modo"], "extracto")
        self.assertIsNone(only["fecha"])

    async def test_the_real_date_is_kept_and_never_replaced_by_today(self):
        self.serve({OKD: rss([item("Gemma Triay y Delfina Brea ganan en Madrid", WOMEN_TEXT, "https://okdiario.com/con-fecha"),
                              item("Marta Ortega, semifinalista en Rotterdam", ORTEGA_TEXT, "https://okdiario.com/sin-fecha", pub=None)])})
        items = {i["fuentes"][0]["url"]: i for i in (await news.get_latest_news())["resumen_diario"]}
        self.assertEqual(items["https://okdiario.com/con-fecha"]["fecha"], "2026-09-28")
        self.assertIsNone(items["https://okdiario.com/sin-fecha"]["fecha"], "sin fecha en el medio no se inventa la de hoy")

    async def test_okdiario_full_text_is_used_for_the_summary_but_padel_addicts_is_not(self):
        short = "Gemma Triay y Delfina Brea ganan en Madrid."
        full = f"<p>{WOMEN_TEXT}</p>"
        self.serve({OKD: rss([item("Gemma Triay y Delfina Brea ganan en Madrid", short, "https://okdiario.com/completo", content=full)]),
                    PADD: rss([item("Marta Ortega, semifinalista en Rotterdam", "Marta Ortega avanza.", "https://www.padeladdict.com/completo",
                                    content=f"<p>{ORTEGA_TEXT}</p>")])})
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual([i["fuentes"][0]["medio"] for i in items], ["OKDiario"],
                         "de Padel Addict solo vale el extracto que ellos publican: sin tres líneas, no sale")
        self.assertGreaterEqual(len(items[0]["resumen"]), news.MIN_SUMMARY_CHARS)

    async def test_only_okdiario_text_can_reach_the_ai(self):
        sent = []

        async def fake_generar(client, sem, grupo):
            sent.extend(grupo)
            a = grupo[0]
            return {"titular": "T", "resumen": "Resumen propio de la crónica. " * 10, "fecha": "2026-09-28",
                    "fuentes": [{"domain": a["host"], "titulo": a["titulo"], "url": a["url"]}]}

        async def none(*args, **kwargs):
            return None

        os.environ["GROQ_API_KEY"] = "clave-falsa"
        news.Groq = lambda api_key=None: object()
        news._is_groq_available = lambda: True
        news.generar_noticia, news.generar_noticia_individual = fake_generar, none
        self.serve({
            OKD: rss([item("Gemma Triay gana en Madrid", WOMEN_TEXT + " TEXTO-OKD.", "https://okdiario.com/a", "Ana")]),
            PADD: rss([item("Marta Ortega avanza en la jornada femenina", ORTEGA_TEXT + " TEXTO-PADEL-ADDICT.", "https://www.padeladdict.com/b")]),
            MARCA: rss([item("Delfina Brea avanza", "Delfina Brea avanzó a semifinales. TEXTO-MARCA.", "https://www.marca.com/c")]),
        })
        result = await news.get_latest_news()
        self.assertEqual({a["host"] for a in sent}, {"okdiario.com"})
        blob = json.dumps(sent, ensure_ascii=False)
        self.assertNotIn("TEXTO-PADEL-ADDICT", blob)
        self.assertNotIn("TEXTO-MARCA", blob)
        self.assertEqual(sorted(i["fuentes"][0]["medio"] for i in result["resumen_diario"]), ["OKDiario", "Padel Addict"])

    async def test_an_ai_summary_that_is_too_short_is_replaced_by_the_deterministic_one(self):
        async def fake_generar(client, sem, grupo):
            return None                         # la IA no da un resumen válido (p. ej. demasiado corto)

        async def none(*args, **kwargs):
            return None
        os.environ["GROQ_API_KEY"] = "clave-falsa"
        news.Groq = lambda api_key=None: object()
        news._is_groq_available = lambda: True
        news.generar_noticia, news.generar_noticia_individual = fake_generar, none
        self.serve({OKD: rss([item("Gemma Triay y Delfina Brea ganan en Madrid", WOMEN_TEXT, "https://okdiario.com/a", "Ana")])})
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual(len(items), 1)
        self.assertGreaterEqual(len(items[0]["resumen"]), news.MIN_SUMMARY_CHARS)

    async def test_an_unknown_feed_is_ignored_and_never_requested(self):
        self.serve({"https://nuevo.example/feed": rss([item("Gemma Triay gana", WOMEN_TEXT + " TEXTO-NUEVO.", "https://nuevo.example/a")])})
        result = await news.get_latest_news()
        self.assertEqual(result["resumen_diario"], [])
        self.assertEqual(self.requested, [])

    async def test_hostile_feed_content_is_neutralised(self):
        self.serve({PADD: rss([
            item("&lt;script&gt;alert(1)&lt;/script&gt;Gemma Triay y Delfina Brea ganan", WOMEN_TEXT, "https://www.padeladdict.com/ok", "&lt;b&gt;Ana&lt;/b&gt;"),
            item("Gemma Triay y Delfina Brea ganan en Madrid", WOMEN_TEXT, "javascript:alert(1)"),
            item("Delfina Brea gana en Madrid", WOMEN_TEXT, "data:text/html,x"),
        ])})
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual(len(items), 1, "los enlaces que no son http(s) se descartan")
        blob = json.dumps(items, ensure_ascii=False)
        for dangerous in ("<", ">", "javascript:", "data:text"):
            self.assertNotIn(dangerous, blob, dangerous)

    async def test_total_is_capped_and_newest_first(self):
        rows = [item(f"Gemma Triay gana el torneo femenino número {i}", WOMEN_TEXT, f"https://www.padeladdict.com/n{i}",
                     pub=f"Mon, {1 + (i % 28):02d} Sep 2026 10:00:00 +0000") for i in range(40)]
        self.serve({PADD: rss(rows)})
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual(len(items), news.MAX_TOTAL)
        dates = [i["fecha"] for i in items]
        self.assertEqual(dates, sorted(dates, reverse=True))

    async def test_a_failing_feed_does_not_break_the_others(self):
        async def feed(url):
            if url == PADD:
                raise RuntimeError("caído")
            return feedparser.parse(rss([item("Gemma Triay y Delfina Brea ganan en Madrid", WOMEN_TEXT, "https://okdiario.com/z", "Ana")]))
        news._fetch_feed = feed
        os.environ["NEWS_RSS_FEEDS"] = f"{PADD},{OKD}"
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual([i["fuentes"][0]["medio"] for i in items], ["OKDiario"])


class AiSummaryIsValidated(unittest.IsolatedAsyncioTestCase):
    """Lo que devuelve la IA solo vale si es un resumen de unas tres líneas, sin puntos suspensivos, y con la fecha REAL."""
    GOOD = json.dumps({"titular": "Triay y Brea ganan el título en Madrid",
                       "resumen": "Gemma Triay y Delfina Brea ganaron la final femenina del Madrid P1 tras un partido muy igualado. "
                                  "La pareja española se impuso en dos sets ante un público entregado en la grada. "
                                  "Con este triunfo suman su tercer título de la temporada en el circuito."})

    def setUp(self):
        self._saved = news._is_groq_available
        news._is_groq_available = lambda: True
        self._clear_cache()

    def tearDown(self):
        news._is_groq_available = self._saved
        self._clear_cache()

    @staticmethod
    def _clear_cache():
        for name in dir(news):
            obj = getattr(news, name)
            if name.startswith("_summary") and isinstance(obj, dict):
                obj.clear()

    @staticmethod
    def client(content):
        class Msg:
            pass
        m = Msg()
        m.content = content
        choice = Msg()
        choice.message = m
        resp = Msg()
        resp.choices = [choice]

        class Completions:
            def create(self, **kw):
                return resp

        class Chat:
            completions = Completions()

        class Client:
            chat = Chat()
        return Client()

    @staticmethod
    def art(date="2026-09-28", n=1):
        return {"titulo": f"Gemma Triay y Delfina Brea ganan {n}", "texto": WOMEN_TEXT, "url": f"https://okdiario.com/ia-{n}",
                "host": "okdiario.com", "medio": "OKDiario", "modo": "resumen", "published_date": date, "jugadoras": ["Gemma Triay"]}

    async def test_a_valid_summary_keeps_the_real_date_and_the_direct_link(self):
        out = await news.generar_noticia(self.client(self.GOOD), news.asyncio.Semaphore(1), [self.art()])
        self.assertEqual(out["fecha"], "2026-09-28")
        self.assertGreaterEqual(len(out["resumen"]), news.MIN_SUMMARY_CHARS)
        self.assertEqual(out["fuentes"][0]["url"], "https://okdiario.com/ia-1")

    async def test_without_a_date_in_the_feed_there_is_no_date_not_today(self):
        out = await news.generar_noticia(self.client(self.GOOD), news.asyncio.Semaphore(1), [self.art(date="", n=2)])
        self.assertIsNone(out["fecha"])

    async def test_individual_retry_also_keeps_the_real_date_or_none(self):
        out = await news.generar_noticia_individual(self.client(self.GOOD), news.asyncio.Semaphore(1), self.art(n=3))
        self.assertEqual(out["fecha"], "2026-09-28")
        out = await news.generar_noticia_individual(self.client(self.GOOD), news.asyncio.Semaphore(1), self.art(date="", n=4))
        self.assertIsNone(out["fecha"])

    async def test_a_summary_that_is_too_short_or_cut_or_broken_is_rejected(self):
        short = json.dumps({"titular": "Triay y Brea ganan", "resumen": "Ganaron la final."})
        cut = json.dumps({"titular": "Triay y Brea ganan", "resumen": "Gemma Triay y Delfina Brea ganaron la final femenina tras un partido " * 4 + "muy…"})
        for label, content in (("corto", short), ("con puntos suspensivos", cut), ("sin JSON", "lo siento, no puedo"),
                               ("JSON sin resumen", json.dumps({"titular": "T"}))):
            for fn, arg in ((news.generar_noticia, [self.art(n=5)]), (news.generar_noticia_individual, self.art(n=6))):
                self.assertIsNone(await fn(self.client(content), news.asyncio.Semaphore(1), arg), label)


class McpItemGivesSummaryDateAndDirectLink(unittest.TestCase):
    def test_item_shape(self):
        from padel_mcp import tools as mcp_tools
        src = [{"medio": "OKDiario", "url": "https://okdiario.com/x", "titulo": "t", "autor": "Ana"}]
        res = mcp_tools._news_item({"titular": "T", "resumen": "R" * 220, "fecha": "2026-09-28", "modo": "resumen", "fuentes": src, "jugadoras": ["Gemma Triay"]})
        self.assertEqual((res["modo"], res["fecha"], res["enlace"]), ("resumen", "2026-09-28", "https://okdiario.com/x"))
        self.assertEqual(len(res["resumen"]), 220)
        self.assertIn("enlace directo", res["uso"])
        ext = mcp_tools._news_item({"titular": "T", "resumen": "Texto literal…", "modo": "extracto", "fuentes": src})
        self.assertIn("literal", ext["uso"])

    def test_old_items_without_mode_or_links_do_not_break(self):
        from padel_mcp import tools as mcp_tools
        old = mcp_tools._news_item({"titular": "T", "resumen": "R"})
        self.assertEqual((old["modo"], old["enlace"]), ("resumen", None))
        self.assertEqual(mcp_tools._news_item({"titular": "T", "resumen": ""})["resumen"], None)
        self.assertEqual(mcp_tools._news_item({"titular": "T", "modo": "otra-cosa"})["modo"], "otra-cosa")


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
