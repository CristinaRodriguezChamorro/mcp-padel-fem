"""
Ni un hombre en las noticias. Casos reales de por qué se colaban (apellidos compartidos como González o Sánchez,
una jugadora nombrada de pasada en una noticia de hombres...) y la regla que lo impide: manda el titular.
Sin internet: los medios y Padel API se simulan.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
os.environ["WIKIPEDIA_INGEST"] = "off"

import httpx

from tools import ingest, local_data, news
from tools import padelapi_source as pa
from test_news_policy import MARCA, OKD, PADD, PipelineBase, item, rss
import test_padelapi_source as tp

WOMEN = ["Gemma Triay", "Delfina Brea", "Ariana Sanchez", "Paula Josemaria", "Beatriz Gonzalez", "Claudia Fernandez",
         "Marta Ortega", "Lucia Sainz", "Andrea Ustero", "Martina Calvo"]
MEN = ["Arturo Coello", "Agustin Tapia", "Alejandro Galan", "Federico Chingotto", "Juan Lebron", "Maximiliano Sanchez",
       "Mohamed Gonzalez", "Martin Di Nenno", "Franco Stupaczuk", "Javier Garrido", "Jon Sanz"]
W = {news._pnorm(n) for n in WOMEN}
M = {news._pnorm(n) for n in MEN}
SOME_MEN = {news._pnorm(n) for n in ["Arturo Coello", "Agustin Tapia"]}      # una lista de hombres incompleta

# titulares de HOMBRES que, antes de esta regla, llegaban a la web
MEN_STORIES = [
    ("Maxi Sánchez y Momo González arrasan en el Milano P1", "La pareja masculina se impuso en la final."),
    ("Arturo Coello: «Paula Josemaría es mi pareja y mi apoyo»", "El número uno habla de su vida fuera de la pista."),
    ("Coello y Tapia ganan en Madrid", "En el cuadro masculino, final ante Galán y Chingotto. Entre el público, Paula Josemaría."),
    ("Fernández y Ortega, la nueva pareja del circuito masculino", "Los dos jugadores debutan juntos."),
    ("Fernández y Ortega debutan juntos en el P2", "Los dos jugadores empiezan el torneo."),
    ("Galán y Chingotto reinan en Madrid", "Entre las asistentes estaba Delfina Brea."),
    ("Premier Padel: Coello y Tapia y Brea y Triay, campeones de Madrid", "Dos finales para el recuerdo."),
    ("Gran victoria en el Milano P1", "Final del cuadro masculino. Paula Josemaría vio el partido desde la grada."),
]
WOMEN_STORIES = [
    ("Gemma Triay y Delfina Brea ganan en Madrid", "Final femenina."),
    ("Brea y Triay, campeonas del P1", "Las números uno vuelven a ganar."),
    ("Marta Ortega, semifinalista en Rotterdam", "La española avanza."),
]


class HeadlineRule(unittest.TestCase):
    def test_no_men_story_passes_with_the_list_of_men(self):
        for title, text in MEN_STORIES:
            self.assertFalse(news.es_noticia_top20(title, text, W, M), title)

    def test_no_men_story_passes_without_the_list_either(self):
        for title, text in MEN_STORIES:
            self.assertFalse(news.es_noticia_top20(title, text, W, ()), title)

    def test_no_men_story_passes_with_an_incomplete_list_of_men(self):
        for title, text in MEN_STORIES:
            self.assertFalse(news.es_noticia_top20(title, text, W, SOME_MEN), title)

    def test_women_stories_pass_with_the_list(self):
        for title, text in WOMEN_STORIES:
            self.assertTrue(news.es_noticia_top20(title, text, W, M), title)

    def test_two_surnames_alone_never_prove_anything(self):
        self.assertFalse(news.es_noticia_top20("Triay y Brea ganan en Rotterdam", "Final.", W, M))
        self.assertFalse(news.es_noticia_top20("Sánchez y González ganan en Rotterdam", "Final.", W, M))
        self.assertTrue(news.es_noticia_top20("Triay y Brea, campeonas en Rotterdam", "Final.", W, M))

    def test_a_masculine_word_in_the_headline_always_excludes(self):
        for title in ("Masculino: Gemma Triay viaja a ver el partido", "Circuito masculino: Marta Ortega, invitada",
                      "Hombres y mujeres: Delfina Brea opina"):
            self.assertFalse(news.es_noticia_top20(title, "Final femenina.", W, M), title)

    def test_a_woman_named_only_in_the_text_needs_an_explicit_female_signal_and_no_men(self):
        self.assertFalse(news.es_noticia_top20("Gran victoria en Madrid", "Paula Josemaría vio el partido.", W, M))
        self.assertTrue(news.es_noticia_top20("Gran victoria en Madrid", "Final femenina: Paula Josemaría ganó.", W, M))
        self.assertFalse(news.es_noticia_top20("Gran victoria en Madrid",
                                               "Final femenina: Paula Josemaría ganó. Coello y Tapia, en el masculino.", W, M))

    def test_a_full_name_in_the_headline_survives_men_mentioned_in_the_text(self):
        self.assertTrue(news.es_noticia_top20("Gemma Triay y Delfina Brea ganan en Madrid",
                                              "Final femenina. Mientras, Coello y Tapia ganaban el cuadro masculino.", W, M))

    def test_a_surname_shared_with_a_man_needs_the_first_name(self):
        # «González» es de Beatriz González y de Mohamed González: solo cuenta con el nombre de pila
        self.assertFalse(news.es_noticia_top20("González gana en Milán", "Final femenina.", W, M))
        self.assertTrue(news.es_noticia_top20("Beatriz González gana en Milán", "Final femenina.", W, M))
        self.assertFalse(news.es_noticia_top20("Mohamed González gana en Milán", "Final femenina.", W, M))


class StrictModeWithoutTheListOfMen(unittest.TestCase):
    """Sin la lista de jugadores no se puede descartar a un hombre: solo pasa lo que diga expresamente que es femenino."""

    def test_needs_an_explicit_female_signal(self):
        self.assertFalse(news.es_noticia_top20("Gemma Triay gana en Madrid", "Gran final.", W, ()))
        self.assertTrue(news.es_noticia_top20("Gemma Triay gana en Madrid", "Gran final femenina.", W, ()))
        self.assertTrue(news.es_noticia_top20("Triay y Brea, campeonas del P1", "Final.", W, ()))

    def test_a_masculine_signal_anywhere_excludes(self):
        self.assertFalse(news.es_noticia_top20("Gemma Triay gana en Madrid", "Final femenina y también masculina.", W, ()))

    def test_every_female_word_counts(self):
        for word in ("femenino", "femenina", "mujeres", "campeonas", "jugadoras", "ellas", "women"):
            self.assertTrue(news.es_noticia_top20("Gemma Triay gana en Madrid", f"Final con {word}.", W, ()), word)


class FragmentsAreFreeOfMen(unittest.TestCase):
    TEXT = ("Gemma Triay ganó la final femenina. Arturo Coello y Agustín Tapia ganaron la masculina. "
            "Paula Josemaría y Coello celebraron juntos. Delfina Brea dedicó el título a su familia.")

    def test_only_sentences_about_women_alone_survive(self):
        out = news._extraer_fragmentos_femeninos(self.TEXT, W, M)
        self.assertIn("Gemma Triay", out)
        self.assertIn("Delfina Brea", out)
        for gone in ("Coello", "Tapia", "masculina"):
            self.assertNotIn(gone, out)

    def test_a_sentence_with_a_masculine_word_is_dropped_even_without_the_list(self):
        out = news._extraer_fragmentos_femeninos("Gemma Triay ganó la final masculina del club. Delfina Brea ganó la femenina.", W, ())
        self.assertNotIn("masculina", out)
        self.assertIn("Delfina Brea", out)


class FinalSafeguard(unittest.TestCase):
    UNI = news._universe(WOMEN, MEN)

    def test_a_published_headline_never_names_a_man(self):
        out = news._centrar_en_top20([{"titular": "Coello y Gemma Triay celebran", "resumen": "", "fuentes": []}], [], self.UNI)
        self.assertEqual(out, [])

    def test_an_extract_that_names_a_man_is_blanked_but_the_item_stays(self):
        n = {"titular": "Gemma Triay gana en Madrid", "resumen": "Gemma Triay ganó, igual que Arturo Coello.",
             "modo": "extracto", "fuentes": []}
        out = news._centrar_en_top20([n], [], self.UNI)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["resumen"], "")

    def test_a_generated_summary_that_names_a_man_is_dropped(self):
        n = {"titular": "Gemma Triay gana en Madrid", "resumen": "Gemma Triay ganó, igual que Arturo Coello.", "fuentes": []}
        self.assertEqual(news._centrar_en_top20([n], [], self.UNI), [])

    def test_tags_only_name_women(self):
        n = {"titular": "Gemma Triay y Delfina Brea ganan en Madrid", "resumen": "Final.", "fuentes": []}
        out = news._centrar_en_top20([n], [], self.UNI)
        self.assertEqual(out[0]["jugadoras"], ["Gemma Triay", "Delfina Brea"])
        self.assertEqual(news._nombres_jugadoras("Arturo Coello y Gemma Triay", self.UNI), ["Gemma Triay"])


class WholePipeline(PipelineBase):
    async def test_the_published_news_contain_no_men_in_any_mode(self):
        self.serve({
            OKD: rss([item("Coello y Tapia ganan en Madrid", "En el cuadro masculino, final ante Galán. Paula Josemaría, en la grada.",
                           "https://okdiario.com/m1", "Ana"),
                      item("Gemma Triay y Delfina Brea ganan en Madrid", "La pareja formada por Gemma Triay y Delfina Brea se impuso en la final femenina. Fue un partido muy igualado.",
                           "https://okdiario.com/w1", "Ana")]),
            PADD: rss([item("Arturo Coello: «Marta Ortega es una gran amiga»", "El número uno habla de la jugadora Marta Ortega.",
                            "https://www.padeladdict.com/m2"),
                       item("Marta Ortega, semifinalista en Rotterdam", "Marta Ortega avanza. Arturo Coello la felicitó en redes. Más texto de la crónica femenina.",
                            "https://www.padeladdict.com/w2")]),
            MARCA: rss([item("Momo González y Maxi Sánchez, a semifinales", "Los jugadores masculinos avanzan.", "https://www.marca.com/m3"),
                        item("Delfina Brea lidera el ranking femenino", "Delfina Brea sigue como número uno del ranking femenino.",
                             "https://www.marca.com/w3")]),
        })
        import tools.news as n
        original_men = n.get_top_men_names

        async def men():
            return MEN + ["Mohamed Gonzalez", "Maximiliano Sanchez"]
        n.get_top_men_names = men
        try:
            result = await news.get_latest_news()
        finally:
            n.get_top_men_names = original_men
        blob = json.dumps(result, ensure_ascii=False)
        for gone in ("Coello", "Tapia", "Galán", "Momo", "Maxi", "Mohamed"):
            self.assertNotIn(gone, blob, gone)
        titles = sorted(i["titular"] for i in result["resumen_diario"])
        self.assertEqual(len(titles), 3, titles)
        self.assertTrue(all("Coello" not in t and "Momo" not in t for t in titles))

    async def test_the_extract_of_a_woman_story_that_names_a_man_is_blanked(self):
        self.serve({PADD: rss([item("Marta Ortega, semifinalista en Rotterdam",
                                    "Marta Ortega avanza en la jornada femenina. Arturo Coello la felicitó en redes.",
                                    "https://www.padeladdict.com/w2")])})
        result = await news.get_latest_news()
        (only,) = result["resumen_diario"]
        self.assertEqual(only["modo"], "extracto")
        self.assertNotIn("Coello", json.dumps(result, ensure_ascii=False))

    async def test_without_the_list_of_men_only_explicitly_female_news_pass(self):
        async def nobody():
            return []
        news.get_top_men_names = nobody
        self.serve({
            MARCA: rss([item("Gemma Triay gana en Madrid", "Gran final.", "https://www.marca.com/a"),
                        item("Arturo Coello: «Paula Josemaría es mi pareja»", "El jugador habla.", "https://www.marca.com/b"),
                        item("Delfina Brea gana en Madrid", "Gran final femenina.", "https://www.marca.com/c")])})
        items = (await news.get_latest_news())["resumen_diario"]
        self.assertEqual([i["titular"] for i in items], ["Delfina Brea gana en Madrid"])


class MenNamesAreLoadedSafely(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="men-")
        self._old = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.dir
        news._men_names_cache.update(ts=0.0, display=None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop("DATA_DIR", None) if self._old is None else os.environ.__setitem__("DATA_DIR", self._old)
        news._men_names_cache.update(ts=0.0, display=None)

    def write(self, doc):
        Path(self.dir, "ranking_masculino.json").write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")

    async def test_names_come_from_the_file(self):
        self.write({"_meta": {"tipo": "api"}, "jugadores": [{"pos": 1, "name": "Arturo Coello"}, {"pos": 2, "name": "Agustin Tapia"}]})
        self.assertEqual(await local_data.get_mens_ranking_names(), ["Arturo Coello", "Agustin Tapia"])
        self.assertEqual(await local_data.get_mens_ranking_names(limit=1), ["Arturo Coello"])

    async def test_missing_broken_or_odd_files_give_an_empty_list(self):
        self.assertEqual(await local_data.get_mens_ranking_names(), [])
        self.write("{esto no es json")
        self.assertEqual(await local_data.get_mens_ranking_names(), [])
        self.write({"jugadores": [{"name": 7}, {"name": "  "}, "x", {"pos": 1}, {"name": "Arturo Coello"}]})
        self.assertEqual(await local_data.get_mens_ranking_names(), ["Arturo Coello"])

    async def test_an_empty_list_is_not_cached_so_a_fresh_deploy_recovers_by_itself(self):
        self.assertEqual(await news.get_top_men_names(), [])               # justo tras el despliegue: aún no hay archivo
        self.write({"jugadores": [{"pos": 1, "name": "Arturo Coello"}]})   # la actualización termina unos minutos después
        self.assertEqual(await news.get_top_men_names(), ["Arturo Coello"])

    async def test_a_good_list_is_cached_for_a_few_minutes(self):
        self.write({"jugadores": [{"pos": 1, "name": "Arturo Coello"}]})
        self.assertEqual(await news.get_top_men_names(), ["Arturo Coello"])
        os.remove(Path(self.dir, "ranking_masculino.json"))
        self.assertEqual(await news.get_top_men_names(), ["Arturo Coello"])
        self.assertLessEqual(news.RANKING_NAMES_TTL, 10 * 60)


class MenRankingFromPadelApi(tp.WithToken):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="men-api-")
        self._dd = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.dir

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop("DATA_DIR", None) if self._dd is None else os.environ.__setitem__("DATA_DIR", self._dd)
        super().tearDown()

    def read(self):
        return json.loads(Path(self.dir, "ranking_masculino.json").read_text(encoding="utf-8"))

    def test_parse_men_ranking(self):
        rows = [tp.rk("Tercero", 3, 1, category="men"), tp.rk("Primero", 1, 1, category="men"),
                tp.rk("Mujer", 1, 1), tp.rk("Elo", 2, 1, category="men", type_="elo"),
                {**tp.rk("Oculto", 4, 1, category="men"), "ranking": pa.HIDDEN}, tp.rk("", 5, 1, category="men")]
        self.assertEqual(pa.parse_men_ranking(rows), [{"pos": 1, "name": "Primero"}, {"pos": 3, "name": "Tercero"}])
        self.assertEqual(len(pa.parse_men_ranking(tp.men_rows(80), limit=50)), 50)

    def test_the_run_stores_the_names_and_nothing_else(self):
        status = tp.run(ingest.run_once(tp.FakeApi().factory(), today=tp.TODAY))
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["ranking_masculino_filas"], 50)
        doc = self.read()
        self.assertEqual(doc["_meta"]["tipo"], "api")
        self.assertIn("no se muestra", doc["_meta"]["nota"])
        self.assertEqual(doc["jugadores"][0], {"pos": 1, "name": "Jugador 01"})
        self.assertEqual(set(doc["jugadores"][0]), {"pos", "name"}, "ni puntos ni países: solo lo necesario")
        self.assertEqual(asyncio.run(local_data.get_mens_ranking_names())[0], "Jugador 01")

    def test_the_men_are_never_shown_by_the_app(self):
        tp.run(ingest.run_once(tp.FakeApi().factory(), today=tp.TODAY))
        ranking = asyncio.run(local_data.get_ranking_live())
        import re
        self.assertFalse(any(re.match(r"Jugador \d", r["name"]) for r in ranking), "los hombres no salen en el ranking")
        self.assertTrue(all(r["name"].startswith("Jugadora ") for r in ranking))

    def test_if_the_men_request_fails_the_rest_still_works(self):
        api = tp.FakeApi(overrides={lambda p, q: q.get("category") == "men": lambda r: httpx.Response(500)})
        status = tp.run(ingest.run_once(api.factory(), today=tp.TODAY))
        self.assertTrue(status["ok"], status)
        self.assertFalse(Path(self.dir, "ranking_masculino.json").exists())
        self.assertTrue(any("ranking masculino" in a for a in status["avisos"]))

    def test_a_suspiciously_short_list_does_not_replace_a_good_one(self):
        tp.run(ingest.run_once(tp.FakeApi().factory(), today=tp.TODAY))
        before = Path(self.dir, "ranking_masculino.json").read_bytes()
        status = tp.run(ingest.run_once(tp.FakeApi(men=tp.men_rows(4)).factory(), today=tp.TODAY))
        self.assertTrue(status["ok"])
        self.assertEqual(Path(self.dir, "ranking_masculino.json").read_bytes(), before)
        self.assertTrue(any("solo 4 filas" in a for a in status["avisos"]))

    def test_a_bad_token_on_the_men_request_still_stops_everything(self):
        api = tp.FakeApi(overrides={lambda p, q: q.get("category") == "men": lambda r: httpx.Response(401)})
        status = tp.run(ingest.run_once(api.factory(), today=tp.TODAY))
        self.assertFalse(status["ok"])
        self.assertIn("PADELAPI_TOKEN", status["intento_padelapi"])      # sin datos que conservar se probó Wikipedia
        self.assertNotIn(tp.TOKEN, json.dumps(status))

    def test_the_generated_file_is_not_committed(self):
        gitignore = (Path(HERE).parent.parent.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("apps/mcp-server/data/ranking_masculino.json", gitignore)


if __name__ == "__main__":
    unittest.main()
