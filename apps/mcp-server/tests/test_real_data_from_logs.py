"""
Casos REALES sacados del log de Railway (13/10/2026), que las pruebas anteriores no cubrían:

  1. Padel API devuelve los nombres con los dos apellidos («Gemma Triay Pons», «Claudia Fernandez Sanchez») y la prensa
     escribe «Gemma Triay», «Claudia Fernández», «Ari Sánchez»: el filtro no reconocía a casi nadie.
  2. Titulares de hombres o mixtos («Tapia-Coello…», «Stupa-Sanz…», Chingotto) llegaban a la web.
  3. Groq respondía 404 («the model llama-3.1-8b-instant does not exist») y no se generaba ningún resumen.
  4. No había forma de saber qué versión estaba desplegada.
"""
import asyncio
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
os.environ.setdefault("MCP_TOOLS", "extended")
os.environ["WIKIPEDIA_INGEST"] = "off"

from tools import news
from tools import padelapi_source as pa
from tools.players import mentioned_players, players_from_names
from test_news_policy import OKD, WOMEN_TEXT, PipelineBase, item, rss

# Los 20 nombres que devolvió Padel API en producción (sin tildes y con dos apellidos), tal cual salen del log.
API_WOMEN = ['Alejandra Alonso De Villa', 'Alejandra Salazar Bengoechea', 'Andrea Ustero Prieto', 'Aranzazu Osoro Ulrich',
             'Ariana Sanchez Fallada', 'Beatriz Caldera Sanchez', 'Beatriz Gonzalez Fernandez', 'Carmen Goenaga Garcia',
             'Claudia Fernandez Sanchez', 'Claudia Jensen', 'Delfina Brea Senesi', 'Gemma Triay Pons', 'Marina Guinart',
             'Marta Ortega Gallego', 'Martina Calvo Santamaria', 'Paula Josemaria Martin', 'Sofia Araujo',
             'Tamara Icardo Alcorisa', 'Veronica Virseda', 'Victoria Iglesias Segador']
WOMEN = [pa.short_name(n) for n in API_WOMEN]
# Jugadores con el mismo formato (el segundo apellido es de relleno: lo que se prueba es el formato, no la persona).
API_MEN = [pa.short_name(n) for n in ["Arturo Coello Xx", "Agustin Tapia Xx", "Alejandro Galan Xx", "Federico Chingotto Xx",
                                      "Juan Lebron Xx", "Franco Stupaczuk Xx", "Jon Sanz Xx", "Maximiliano Sanchez Xx"]]
W = {news._pnorm(n) for n in WOMEN}
M = {news._pnorm(n) for n in API_MEN + list(news.KNOWN_MEN)}

# Titulares REALES de mujeres (recortados a 70 caracteres en el log) que deben publicarse
REAL_WOMEN = [
    "La leyenda del pádel Alejandra Salazar cae en cuartos y dice adiós a s",
    "La venganza de París: Claudia Fernández y Martina Calvo se coronan en",
    "Ari Sánchez y Andrea Ustero siguen arrasando y vuelven a amenazar a la",
    "Pasó inadvertido pero es un gesto deportivo top: Ari Sánchez volea a l",
    "Claudia Fernández y Martina Calvo arrasan a Ustero-Sánchez para tomars",
    "A la sexta fue la vencida: Andrea Ustero y Ari Sánchez vuelven a ganar",
    "Claudia Fernández y Martina Calvo salvan dos bolas de partido para tom",
    "La revancha soñada: Claudia Fernández y Martina Calvo se alzan con el",
    "Las finales femeninas más rápidas de la era Premier Padel: Calvo y Fer",
]
# Solo dos apellidos y sin señal femenina en el titular: sale únicamente si el texto confirma que es femenino
NEEDS_TEXT = "Jimena Velasco y Raquel Eugenio frenan en seco a Guinart y Alonso"
# Titulares REALES de hombres o mixtos que se colaban: no deben publicarse NUNCA
REAL_MEN_OR_MIXED = [
    "El dineral que ganan Calvo-Fernández y Tapia-Coello tras campeonar en",
    "Nuevo episodio de El Post Partido de La Chiquita: Tapia-Coello y Uster",
    "Nuevo episodio de El Post Partido de La Chiquita: Tapia y Coello aprov",
    "Este es el dinero que han ganado Coello-Tapia y Calvo-Fernández por co",
    "Stupa-Sanz desafían a Tapia-Coello y Sánchez-Ustero buscan el tercero",
    "Martina Calvo y Claudia Fernández arrasan en Alemania; Coello y Tapia ",
    # el caso que has visto tú
    "Chingotto y Galán, campeones en Madrid; Gemma Triay y Delfina Brea, finalistas",
    "Chingotto: «Claudia Fernández es una gran jugadora»",
    "Fede Chingotto y Ale Galán ganan en Düsseldorf",
]


class NamesFromPadelApi(unittest.TestCase):
    def test_the_stored_names_are_first_name_and_first_surname(self):
        self.assertEqual(WOMEN[:3], ["Alejandra Alonso", "Alejandra Salazar", "Andrea Ustero"])
        for full, short in zip(API_WOMEN, WOMEN):
            self.assertTrue(full.startswith(short), (full, short))
            self.assertLessEqual(len(short.split()), 2, short)

    def test_short_name_cases(self):
        for full, expected in (("Gemma Triay Pons", "Gemma Triay"), ("Marina Guinart", "Marina Guinart"),
                               ("Maria Jose Martinez Lopez", "Maria Jose Martinez"), ("Ana Maria Perez", "Ana Maria Perez"),
                               ("Maria De la Fuente Perez", "Maria De la Fuente"), ("Beatriz Caldera Sanchez", "Beatriz Caldera"),
                               ("  Sofia   Araujo ", "Sofia Araujo"), ("Stupa", "Stupa"), ("", "")):
            self.assertEqual(pa.short_name(full), expected, full)

    def test_the_ranking_parser_stores_short_names_for_women_and_men(self):
        rows = [pa.clean_name(n) for n in API_WOMEN]
        data = [{"category": "women", "type": "official", "ranking": i + 1, "points": 1000 - i, "name": n, "nationality": "ES", "date": "2026-10-05"}
                for i, n in enumerate(API_WOMEN)]
        out = pa.parse_ranking(data)
        self.assertEqual([r["name"] for r in out], WOMEN)
        men = pa.parse_men_ranking([{"category": "men", "type": "official", "ranking": 1, "name": "Arturo Coello Xx"}])
        self.assertEqual(men, [{"pos": 1, "name": "Arturo Coello"}])
        self.assertEqual(rows[0], "Alejandra Alonso De Villa")


class WomenAreRecognisedTheWayThePressWritesThem(unittest.TestCase):
    PLAYERS = players_from_names(WOMEN)

    def hits(self, text):
        return sorted(p["name"] for p, _ in mentioned_players(text, self.PLAYERS))

    def test_names_surnames_and_nicknames(self):
        self.assertEqual(self.hits("Gemma Triay gana"), ["Gemma Triay"])
        self.assertEqual(self.hits("Gemma Triay Pons gana"), ["Gemma Triay"])
        self.assertEqual(self.hits("Ari Sánchez y Andrea Ustero"), ["Andrea Ustero", "Ariana Sanchez"])
        self.assertEqual(self.hits("Delfi Brea"), ["Delfina Brea"])
        self.assertEqual(self.hits("Bea González"), ["Beatriz Gonzalez"])
        self.assertEqual(self.hits("Calvo-Fernández"), ["Claudia Fernandez", "Martina Calvo"])
        self.assertEqual(self.hits("Rufo e Iglesias"), ["Victoria Iglesias"])

    def test_a_shared_surname_alone_is_not_enough(self):
        # hay dos «Beatriz» y una «Claudia» con apellido Sanchez/Fernandez entre sus apellidos: «Sánchez» solo es de Ariana
        self.assertEqual(self.hits("Sánchez gana"), ["Ariana Sanchez"])
        self.assertNotIn("Beatriz Caldera", self.hits("Sánchez gana"), "el segundo apellido no cuenta")

    def test_a_nickname_never_matches_the_wrong_player(self):
        players = players_from_names(["Ariana Sanchez", "Ari Sanchez"])
        got = sorted(p["name"] for p, _ in mentioned_players("Ari Sánchez gana", players))
        self.assertEqual(got, ["Ari Sanchez"])


class FullLegalNamesAreRecognisedToo(unittest.TestCase):
    """Datos guardados antes de acortar los nombres (o de otra fuente) pueden traer los dos apellidos: también se entienden."""
    PLAYERS = players_from_names(API_WOMEN)

    def hits(self, text):
        return sorted(p["name"] for p, _ in mentioned_players(text, self.PLAYERS))

    def test_the_press_form_matches_the_long_name(self):
        self.assertEqual(self.hits("Gemma Triay gana"), ["Gemma Triay Pons"])
        self.assertEqual(self.hits("Triay gana"), ["Gemma Triay Pons"])
        self.assertEqual(self.hits("Ari Sánchez y Andrea Ustero"), ["Andrea Ustero Prieto", "Ariana Sanchez Fallada"])
        self.assertEqual(self.hits("Calvo-Fernández"), ["Claudia Fernandez Sanchez", "Martina Calvo Santamaria"])
        self.assertEqual(self.hits("Guinart y Alonso"), ["Alejandra Alonso De Villa", "Marina Guinart"])
        self.assertEqual(self.hits("Delfi Brea"), ["Delfina Brea Senesi"])

    def test_the_second_surname_is_not_taken_as_the_surname(self):
        self.assertEqual(self.hits("Pons gana"), [], "«Pons» es el segundo apellido de Gemma Triay: la prensa no la nombra así")
        self.assertEqual(self.hits("Fallada gana"), [])
        self.assertEqual(self.hits("De Villa gana"), [])


class RealHeadlines(unittest.TestCase):
    def test_real_women_headlines_are_published_with_the_api_list(self):
        for title in REAL_WOMEN:
            self.assertTrue(news.es_noticia_top20(title, "", W, M), title)

    def test_a_headline_with_only_surnames_needs_the_text_to_confirm_it_is_female(self):
        self.assertFalse(news.es_noticia_top20(NEEDS_TEXT, "", W, M))
        self.assertFalse(news.es_noticia_top20(NEEDS_TEXT, "Crónica de la jornada del torneo.", W, M))
        self.assertTrue(news.es_noticia_top20(NEEDS_TEXT, "Crónica de la jornada del cuadro femenino del torneo.", W, M))

    def test_real_men_or_mixed_headlines_are_never_published(self):
        for title in REAL_MEN_OR_MIXED:
            for text in ("", "Final femenina del torneo. Gemma Triay y Delfina Brea.", "Cuadro femenino y masculino."):
                self.assertFalse(news.es_noticia_top20(title, text, W, M), (title, text))

    def test_even_without_the_list_of_the_api_the_fixed_list_blocks_the_famous_men(self):
        only_fixed = {news._pnorm(n) for n in news.KNOWN_MEN}
        for title in REAL_MEN_OR_MIXED:
            self.assertFalse(news.es_noticia_top20(title, "Final femenina.", W, only_fixed, strict=True), title)
            self.assertFalse(news.es_noticia_top20(title, "Final femenina.", W, only_fixed, strict=False), title)

    def test_the_fixed_list_has_no_duplicate_or_empty_entries(self):
        norms = [news._pnorm(n) for n in news.KNOWN_MEN]
        self.assertEqual(len(norms), len(set(norms)))
        self.assertTrue(all(norms))
        for must in ("chingotto", "stupa", "coello", "tapia", "galan", "lebron", "sanz"):
            self.assertTrue(any(must in n for n in norms), must)


class FullPipelineWithTheApiNames(PipelineBase):
    async def test_real_names_produce_published_news_and_men_stay_out(self):
        async def names():
            return list(WOMEN)

        async def men():
            return list(API_MEN)
        news.get_top_player_names, news.get_top_men_names = names, men
        men_text = ("Chingotto y Galán ganaron la final masculina de Düsseldorf ante Coello y Tapia en un partido muy igualado de principio a fin. " * 3)
        self.serve({OKD: rss([
            item("Chingotto y Galán, campeones en Düsseldorf", men_text, "https://okdiario.com/m1"),
            item("Stupa-Sanz desafían a Tapia-Coello y Sánchez-Ustero buscan el tercero", men_text, "https://okdiario.com/m2"),
            item("La venganza de París: Claudia Fernández y Martina Calvo se coronan en Düsseldorf",
                 "Claudia Fernández y Martina Calvo se coronaron en la final femenina de Düsseldorf tras un partido muy igualado ante Ustero y Sánchez. "
                 "Martina Calvo y Claudia Fernández sellaron el título en tres sets ante un público entregado en la grada del pabellón. "
                 "Claudia Fernández dedicó el triunfo a toda su familia después de recibir el trofeo de campeonas.", "https://okdiario.com/w1", "Ana"),
            item("Ari Sánchez y Andrea Ustero siguen arrasando", WOMEN_TEXT.replace("Gemma Triay", "Ari Sánchez").replace("Delfina Brea", "Andrea Ustero")
                 .replace("Triay y Brea", "Sánchez y Ustero"), "https://okdiario.com/w2", "Ana"),
        ])})
        result = await news.get_latest_news()
        blob = json.dumps(result, ensure_ascii=False)
        for gone in ("Chingotto", "Galán", "Coello", "Tapia", "Stupa"):
            self.assertNotIn(gone, blob, gone)
        items = result["resumen_diario"]
        self.assertEqual(len(items), 2, [i["titular"] for i in items])
        for i in items:
            self.assertGreaterEqual(len(i["resumen"]), news.MIN_SUMMARY_CHARS)
            self.assertTrue(i["fuentes"][0]["url"].startswith("https://okdiario.com/"))
            self.assertTrue(i["fecha"])


class FixedListIsAlwaysAddedToTheApiList(PipelineBase):
    async def test_a_famous_man_missing_from_the_api_list_is_still_blocked(self):
        async def names():
            return list(WOMEN)

        async def men():
            return ["Arturo Coello"]                     # la API (aún) no ha dado a Chingotto ni a Galán
        news.get_top_player_names, news.get_top_men_names = names, men
        text = ("Final femenina del torneo de Düsseldorf: Gemma Triay y Delfina Brea ganaron un partido muy igualado ante un público entregado. " * 3)
        self.serve({OKD: rss([item("Chingotto y Galán, campeones en Düsseldorf", text, "https://okdiario.com/m1")])})
        self.assertEqual((await news.get_latest_news())["resumen_diario"], [])


class GroqModelThatNoLongerExists(unittest.IsolatedAsyncioTestCase):
    REAL_ERROR = ("Error code: 404 - {'error': {'message': 'The model `llama-3.1-8b-instant` does not exist or you do not have access to it.', "
                  "'type': 'invalid_request_error', 'code': 'model_not_found'}}")

    def setUp(self):
        self._saved = (news._groq_disabled_until, dict(news._groq_model_state), news.GROQ_MODEL)
        news._groq_disabled_until = 0.0
        news._groq_model_state["id"] = None
        news.GROQ_MODEL = "llama-3.1-8b-instant"

    def tearDown(self):
        news._groq_disabled_until, state, news.GROQ_MODEL = self._saved
        news._groq_model_state.update(state)

    @staticmethod
    def client(alive=("llama-3.3-70b-versatile",), listing=None, list_raises=False, error=None):
        calls = {"create": [], "list": 0}

        class Resp:
            choices = [type("C", (), {"message": type("M", (), {"content": '{"titular":"t","resumen":"r"}'})()})()]

        class Completions:
            def create(self, **kw):
                calls["create"].append(kw["model"])
                if kw["model"] in alive:
                    return Resp()
                raise Exception(error or GroqModelThatNoLongerExists.REAL_ERROR)

        class Models:
            def list(self):
                calls["list"] += 1
                if list_raises:
                    raise Exception("sin red")
                ids = listing if listing is not None else list(alive)
                return type("L", (), {"data": [type("X", (), {"id": i})() for i in ids]})()

        class Client:
            chat = type("Chat", (), {"completions": Completions()})()
            models = Models()
        return Client(), calls

    def test_the_real_error_text_is_recognised(self):
        self.assertTrue(news._is_model_missing_error(Exception(self.REAL_ERROR)))
        self.assertFalse(news._is_model_missing_error(Exception("Error code: 429 - rate_limit_exceeded")))
        self.assertFalse(news._is_model_missing_error(Exception("Error code: 500 internal")))

    async def test_another_model_is_discovered_used_and_remembered(self):
        c, calls = self.client()
        await news._groq_chat(c, "hola")
        self.assertEqual(calls["create"], ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"])
        self.assertEqual(news._groq_model_state["id"], "llama-3.3-70b-versatile")
        await news._groq_chat(c, "otra vez")
        self.assertEqual(calls["create"][-1], "llama-3.3-70b-versatile")
        self.assertEqual((len(calls["create"]), calls["list"]), (3, 1), "no se vuelve a descubrir ni a probar el modelo muerto")

    async def test_without_any_usable_model_the_ai_is_switched_off_for_a_while(self):
        c, calls = self.client(alive=(), listing=["whisper-large-v3", "llama-guard-4-12b"])
        with self.assertRaises(Exception):
            await news._groq_chat(c, "hola")
        self.assertFalse(news._is_groq_available())

    async def test_if_the_listing_fails_too_the_ai_is_switched_off(self):
        c, _ = self.client(alive=(), list_raises=True)
        with self.assertRaises(Exception):
            await news._groq_chat(c, "hola")
        self.assertFalse(news._is_groq_available())

    async def test_other_errors_do_not_trigger_discovery(self):
        c, calls = self.client(alive=(), error="Error code: 500 internal")
        with self.assertRaises(Exception):
            await news._groq_chat(c, "hola")
        self.assertEqual((calls["create"], calls["list"]), (["llama-3.1-8b-instant"], 0))
        self.assertTrue(news._is_groq_available())

    def test_model_choice(self):
        pick = news._pick_groq_model
        c = lambda ids: self.client(listing=ids)[0]
        self.assertEqual(pick(c(["whisper-large-v3", "llama-3.1-8b-instant", "llama-3.3-70b-versatile"])), "llama-3.3-70b-versatile")
        self.assertEqual(pick(c(["whisper-large-v3", "playai-tts", "llama-guard-4", "meta-llama/llama-9-70b-versatile", "qwen/qwen9-32b"])),
                         "meta-llama/llama-9-70b-versatile")
        self.assertIsNone(pick(c(["whisper-large-v3", "playai-tts", "llama-guard-4"])))
        self.assertIsNone(pick(c([])))
        self.assertEqual(pick(c(["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]), excluded="llama-3.3-70b-versatile"), "llama-3.1-8b-instant")
        self.assertIsNone(pick(self.client(list_raises=True)[0]))


class WithoutAnyWorkingAiTheNewsStillHaveSummaries(PipelineBase):
    async def test_dead_model_and_no_listing_still_publishes_three_line_summaries(self):
        os.environ["GROQ_API_KEY"] = "clave-falsa"
        dead, _ = GroqModelThatNoLongerExists.client(alive=(), list_raises=True)
        news.Groq = lambda api_key=None: dead
        saved = (news._groq_disabled_until, dict(news._groq_model_state))
        news._groq_disabled_until = 0.0
        news._groq_model_state["id"] = None
        try:
            self.serve({OKD: rss([item("Gemma Triay y Delfina Brea ganan en Madrid", WOMEN_TEXT, "https://okdiario.com/a", "Ana")])})
            items = (await news.get_latest_news())["resumen_diario"]
        finally:
            news._groq_disabled_until, state = saved
            news._groq_model_state.update(state)
        self.assertEqual(len(items), 1)
        self.assertGreaterEqual(len(items[0]["resumen"]), news.MIN_SUMMARY_CHARS)
        self.assertTrue(items[0]["fecha"])


class VersionIsVisible(unittest.TestCase):
    def test_the_version_is_served_and_shown(self):
        import server
        self.assertNotIn("v96", server.APP_VERSION)
        self.assertEqual(asyncio.run(server.api_version()), {"version": server.APP_VERSION})
        self.assertEqual(asyncio.run(server.api_data_status())["version"], server.APP_VERSION)
        html = open(os.path.join(HERE, "..", "static", "index.html"), encoding="utf-8").read()
        self.assertIn("Versión '+s.version", html)


if __name__ == "__main__":
    unittest.main()
