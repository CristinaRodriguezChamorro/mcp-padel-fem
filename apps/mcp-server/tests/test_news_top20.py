"""
Pruebas del filtro de noticias centrado en el Top 20 (sin internet, sin Groq).
Ejecutar desde apps/mcp-server:   python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import feedparser

from tools import news
from tools.players import mention_strength, mentioned_players, players_from_names

TOP = ["Gemma Triay", "Delfina Brea", "Ariana Sánchez", "Ari Sánchez", "Marta Ortega"]
NORMS = {news._pnorm(n) for n in TOP}
MEN = {news._pnorm(n) for n in ["Arturo Coello", "Agustin Tapia", "Alejandro Galan", "Federico Chingotto", "Juan Lebron",
                                "Maximiliano Sanchez", "Mohamed Gonzalez"]}     # la lista de ellos, como la da Padel API


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>https://medio.test/{i}</link>"
        f"<pubDate>Mon, 28 Sep 2026 10:15:00 +0000</pubDate><description>{d}</description></item>"
        for i, (t, d) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode("utf-8")


class FilterRules(unittest.TestCase):
    def test_full_name_passes(self):
        self.assertTrue(news.es_noticia_top20("Gemma Triay gana en Madrid", "Gran final.", NORMS, MEN))

    def test_generic_womens_news_without_a_top20_player_is_rejected(self):
        self.assertFalse(news.es_noticia_top20("El pádel femenino crece en España", "Más licencias de mujeres.", NORMS))

    def test_male_news_is_rejected(self):
        self.assertFalse(news.es_noticia_top20("Coello y Tapia ganan en Madrid", "Final masculina.", NORMS))

    def test_lone_surname_needs_corroboration(self):
        self.assertFalse(news.es_noticia_top20("Triay gana en Rotterdam", "Sin más contexto.", NORMS, MEN))
        self.assertTrue(news.es_noticia_top20("Triay gana en Rotterdam", "Torneo femenino de Rotterdam.", NORMS, MEN))
        # varios apellidos juntos NO bastan: muchos apellidos son también de hombres
        self.assertFalse(news.es_noticia_top20("Triay y Brea ganan en Rotterdam", "Final.", NORMS, MEN))
        self.assertTrue(news.es_noticia_top20("Triay y Brea, campeonas en Rotterdam", "Final.", NORMS, MEN))

    def test_ambiguous_surname_is_rejected(self):
        self.assertFalse(news.es_noticia_top20("Sánchez gana en Rotterdam", "Torneo femenino.", NORMS))
        self.assertTrue(news.es_noticia_top20("Ari Sánchez gana en Rotterdam", "Torneo femenino.", NORMS))

    def test_title_strength_no_longer_accepts_generic_female_text(self):
        self.assertEqual(news._female_title_strength("Crece el pádel femenino", NORMS), 0)
        self.assertEqual(news._female_title_strength("Gemma Triay lidera", NORMS), 3)

    def test_fragments_keep_only_sentences_about_top20_players(self):
        text = "Coello ganó ayer. Gemma Triay ganó la final femenina. El clima fue soleado."
        self.assertEqual(news._extraer_fragmentos_femeninos(text, NORMS), "Gemma Triay ganó la final femenina.")

    def test_strength_levels(self):
        players = players_from_names(TOP)
        triay = players[0]
        self.assertEqual(mention_strength("Gemma Triay", triay, players), 2)
        self.assertEqual(mention_strength("Triay", triay, players), 1)
        self.assertEqual(mention_strength("Coello", triay, players), 0)
        self.assertEqual([p["name"] for p, _ in mentioned_players("Ari Sánchez y Ortega", players)],
                         ["Ari Sánchez", "Marta Ortega"])


class CentrarEnTop20(unittest.TestCase):
    def test_tags_come_from_sources_and_own_text_and_untagged_items_are_dropped(self):
        players = players_from_names(TOP)
        articulos = [{"url": "https://medio.test/a", "jugadoras": ["Gemma Triay"]}]
        noticias = [
            {"titular": "Victoria en Madrid", "resumen": "Ganó la final.",
             "fuentes": [{"url": "https://medio.test/a"}]},                       # etiqueta por su fuente
            {"titular": "Marta Ortega avanza", "resumen": "Pasa de ronda.", "fuentes": []},   # por su texto
            {"titular": "Noticia sin jugadoras", "resumen": "Nada del Top 20.",
             "fuentes": [{"url": "https://medio.test/otra"}]},                    # se descarta
        ]
        out = news._centrar_en_top20(noticias, articulos, players)
        self.assertEqual([n["titular"] for n in out], ["Victoria en Madrid", "Marta Ortega avanza"])
        self.assertEqual(out[0]["jugadoras"], ["Gemma Triay"])
        self.assertEqual(out[1]["jugadoras"], ["Marta Ortega"])


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ.pop("GROQ_API_KEY", None)
        self._saved = (news._fetch_feed, news.get_top_player_names)

        async def names():
            return list(TOP)
        news.get_top_player_names = names

    async def asyncTearDown(self):
        news._fetch_feed, news.get_top_player_names = self._saved

    async def run_with(self, items):
        async def feed(url):
            return feedparser.parse(rss(items)) if url == news.RSS_FEEDS[0] else None
        news._fetch_feed = feed
        return await news.get_latest_news()

    async def test_only_top20_news_survive_and_are_tagged(self):
        result = await self.run_with([
            ("Gemma Triay y Delfina Brea ganan el torneo de Madrid",
             "La pareja formada por Gemma Triay y Delfina Brea se impuso en la final femenina. Fue un partido muy igualado en Madrid."),
            ("El pádel femenino crece en España",
             "Cada año hay más licencias de mujeres en los clubes españoles. Los clubes ya notan el aumento."),
            ("Coello y Tapia ganan en Madrid",
             "La pareja masculina se impuso en la final. Fue un partido muy igualado en Madrid."),
            ("Marta Ortega, semifinalista en Rotterdam",
             "La jugadora Marta Ortega alcanzó las semifinales del torneo. Cayó tras un partido muy disputado."),
        ])
        items = result["resumen_diario"]
        titulares = " | ".join(i["titular"] for i in items)
        self.assertNotIn("crece", titulares)
        self.assertNotIn("Coello", titulares)
        self.assertEqual(len(items), 2)
        tags = sorted(tuple(i["jugadoras"]) for i in items)
        self.assertIn(("Gemma Triay", "Delfina Brea"), tags)
        self.assertIn(("Marta Ortega",), tags)
        for i in items:
            self.assertTrue(i["jugadoras"])

    async def test_without_a_top20_list_nothing_is_published(self):
        async def none():
            return []
        news.get_top_player_names = none
        result = await self.run_with([("Gemma Triay gana", "Gemma Triay ganó la final femenina en Madrid ayer.")])
        self.assertEqual(result["resumen_diario"], [])


class TopNamesCache(unittest.IsolatedAsyncioTestCase):
    async def test_uses_last_known_names_when_the_source_fails_and_limits_to_20(self):
        real = news.get_womens_ranking_names
        news._ranking_names_cache.update({"ts": 0.0, "display": None})
        try:
            seen = {}

            async def many(limit=200):
                seen["limit"] = limit
                return [f"Jugadora {i}" for i in range(50)]
            news.get_womens_ranking_names = many
            names = await news.get_top_player_names()
            self.assertEqual(seen["limit"], 20)
            self.assertEqual(len(names), 20)

            async def boom(limit=200):
                raise RuntimeError("fuente caída")
            news.get_womens_ranking_names = boom
            news._ranking_names_cache["ts"] = 0.0          # caché caducada
            self.assertEqual(await news.get_top_player_names(), names)
        finally:
            news.get_womens_ranking_names = real
            news._ranking_names_cache.update({"ts": 0.0, "display": None})


if __name__ == "__main__":
    unittest.main()
