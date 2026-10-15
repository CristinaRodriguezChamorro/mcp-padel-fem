"""
Ficha de un torneo (resultado_torneo): dónde, qué tipo de torneo es, quién jugó, podio y resultado.
La lógica es pura (sin red); el contexto de cada categoría solo dice lo que dicen las fuentes públicas.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["WIKIPEDIA_INGEST"] = "off"

from tools import local_data
from tools.local_data import describe_final, find_final, split_pair, split_place, tournament_card, tournament_type


def final(**kw):
    base = {"torneo": "Madrid P1", "sede": "Madrid 🇪🇸", "categoria": "P1", "fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-07",
            "ganadoras": "Delfina Brea / Gemma Triay", "finalistas": "Ari Sánchez / Andrea Ustero", "resultado": "6–3 6–4",
            "semifinalistas": []}
    base.update(kw)
    return base


class Place(unittest.TestCase):
    def test_city_and_country_from_the_flag(self):
        self.assertEqual(split_place("Madrid 🇪🇸"), {"ciudad": "Madrid", "pais": "España", "codigo_pais": "ES"})
        self.assertEqual(split_place("London 🇬🇧")["pais"], "Reino Unido")
        self.assertEqual(split_place("Kuwait City 🇰🇼")["ciudad"], "Kuwait City")
        self.assertEqual(split_place("Doha 🇶🇦")["pais"], "Catar")

    def test_unknown_flag_keeps_the_code_and_no_flag_keeps_only_the_city(self):
        self.assertEqual(split_place("Lima 🇵🇪")["pais"], "PE")
        self.assertEqual(split_place("TBC"), {"ciudad": "TBC", "pais": None, "codigo_pais": None})
        self.assertEqual(split_place(""), {"ciudad": None, "pais": None, "codigo_pais": None})
        self.assertEqual(split_place(None)["ciudad"], None)

    def test_pairs(self):
        self.assertEqual(split_pair("Delfina Brea / Gemma Triay"), ["Delfina Brea", "Gemma Triay"])
        self.assertEqual(split_pair(" A /  / B "), ["A", "B"])
        self.assertEqual(split_pair(None), [])


class TournamentType(unittest.TestCase):
    def test_every_category_has_a_description_and_the_circuit_context(self):
        for cat in ("Major", "MAJOR", "P1", "p2", "Finals"):
            t = tournament_type(cat)
            self.assertTrue(t["descripcion"], cat)
            self.assertIn("Premier Padel", t["circuito"])
            self.assertTrue(t["fuente"])

    def test_the_description_only_claims_what_the_sources_say(self):
        text = " ".join(local_data.TOURNAMENT_TYPES.values()) + local_data.CIRCUIT_CONTEXT
        self.assertNotRegex(text, r"\d{3,}", "sin cifras de puntos: el sistema de 2026 no se ha podido verificar")
        self.assertIn("más puntos", local_data.TOURNAMENT_TYPES["major"])
        self.assertIn("segunda categoría", local_data.TOURNAMENT_TYPES["p1"])
        self.assertIn("tercera categoría", local_data.TOURNAMENT_TYPES["p2"])

    def test_unknown_category_still_gives_the_circuit_but_no_invented_description(self):
        t = tournament_type("Platinum")
        self.assertIsNone(t["descripcion"])
        self.assertEqual(t["categoria"], "Platinum")
        self.assertIn("Premier Padel", t["circuito"])
        self.assertIsNone(tournament_type("")["categoria"])


class DescribeFinal(unittest.TestCase):
    def test_full_card(self):
        card = describe_final(final())
        self.assertEqual(card["torneo"], "Madrid P1")
        self.assertEqual((card["donde"]["ciudad"], card["donde"]["pais"]), ("Madrid", "España"))
        self.assertEqual(card["fechas"], {"inicio": "2026-09-01", "fin": "2026-09-07"})
        self.assertEqual(card["tipo_de_torneo"]["categoria"], "P1")
        self.assertEqual(card["partido"], "Final femenina: Delfina Brea / Gemma Triay vs Ari Sánchez / Andrea Ustero")
        self.assertEqual(card["podio"]["primeras"], {"pareja": "Delfina Brea / Gemma Triay", "jugadoras": ["Delfina Brea", "Gemma Triay"]})
        self.assertEqual(card["podio"]["segundas"]["jugadoras"], ["Ari Sánchez", "Andrea Ustero"])
        self.assertEqual(card["resultado_final"], "6–3 6–4")
        self.assertIsNone(card["nota_resultado"])

    def test_thirds_are_never_invented(self):
        card = describe_final(final())
        self.assertIsNone(card["podio"]["terceras"])
        self.assertIn("no los inventes", card["podio"]["nota_terceras"])
        self.assertIn("no se juega partido por el tercer puesto", card["podio"]["nota_terceras"])

    def test_thirds_appear_when_a_source_provides_them(self):
        card = describe_final(final(semifinalistas=["Paula Josemaría / Bea González", "Marta Ortega / Martina Calvo"]))
        self.assertEqual([t["pareja"] for t in card["podio"]["terceras"]], ["Paula Josemaría / Bea González", "Marta Ortega / Martina Calvo"])
        self.assertEqual(card["podio"]["terceras"][0]["jugadoras"], ["Paula Josemaría", "Bea González"])
        self.assertIsNone(card["podio"]["nota_terceras"])

    def test_missing_score_is_said_not_made_up(self):
        card = describe_final(final(resultado=""))
        self.assertIsNone(card["resultado_final"])
        self.assertIn("no recoge el marcador", card["nota_resultado"])

    def test_missing_dates_or_place_do_not_break_it(self):
        card = describe_final(final(sede="", fecha_inicio=None))
        self.assertEqual(card["fechas"]["inicio"], None)
        self.assertIsNone(card["donde"]["ciudad"])


class OneSentence(unittest.TestCase):
    def test_the_sentence_says_who_beat_whom_where_and_by_how_much(self):
        self.assertEqual(describe_final(final())["resumen_en_una_frase"],
                         "Delfina Brea / Gemma Triay ganaron el Madrid P1 (Madrid, España) a Ari Sánchez / Andrea Ustero por 6–3 6–4.")

    def test_without_score_or_place_the_sentence_does_not_invent_them(self):
        text = describe_final(final(resultado="", sede=""))["resumen_en_una_frase"]
        self.assertEqual(text, "Delfina Brea / Gemma Triay ganaron el Madrid P1 a Ari Sánchez / Andrea Ustero.")
        self.assertNotIn("por", text)
        self.assertNotIn("(", text)


class TournamentCard(unittest.TestCase):
    TODAY = date(2026, 10, 6)
    NEXT = {"name": "Milan P1", "place": "Milano 🇮🇹", "badgeText": "P1", "inicio": "2026-10-12", "fin": "2026-10-18", "tv": "", "live": False}

    def test_next_tournament_card(self):
        card = tournament_card(self.NEXT, self.TODAY)
        self.assertEqual(card["torneo"], "Milan P1")
        self.assertEqual((card["donde"]["ciudad"], card["donde"]["pais"]), ("Milano", "Italia"))
        self.assertEqual(card["fechas"], {"inicio": "2026-10-12", "fin": "2026-10-18"})
        self.assertEqual(card["dias_para_empezar"], 6)
        self.assertFalse(card["en_juego"])
        self.assertEqual(card["tipo_de_torneo"]["categoria"], "P1")
        self.assertIn("segunda categoría", card["tipo_de_torneo"]["descripcion"])

    def test_where_to_watch_uses_the_own_value_or_the_verified_general_note(self):
        self.assertEqual(tournament_card({**self.NEXT, "tv": "Movistar+"}, self.TODAY)["donde_verlo"], "Movistar+")
        general = tournament_card(self.NEXT, self.TODAY)["donde_verlo"]
        self.assertIn("Red Bull TV", general)
        self.assertNotIn("puntos", general, "sin cifras de puntos que no se han podido verificar")

    def test_in_progress_and_missing_dates(self):
        live = tournament_card({**self.NEXT, "inicio": "2026-10-05", "live": True}, self.TODAY)
        self.assertEqual((live["dias_para_empezar"], live["en_juego"]), (-1, True))
        self.assertIsNone(tournament_card({"name": "X"}, self.TODAY)["dias_para_empezar"])
        self.assertIsNone(tournament_card({"name": "X"}, self.TODAY)["donde"]["ciudad"])


class FindFinal(unittest.TestCase):
    FINALS = [final(), final(torneo="Valencia P1", sede="Valencia 🇪🇸", ganadoras="A / B"),
              final(torneo="Rotterdam P2", sede="Rotterdam 🇳🇱", categoria="P2", ganadoras="C / D"),
              final(torneo="Qatar Major", sede="Doha 🇶🇦", categoria="Major", ganadoras="E / F")]

    def test_by_name_or_city_without_accents_or_case(self):
        self.assertEqual(find_final(self.FINALS, "valencia")["ganadoras"], "A / B")
        self.assertEqual(find_final(self.FINALS, "ROTTERDAM p2")["torneo"], "Rotterdam P2")
        self.assertEqual(find_final(self.FINALS, "doha")["torneo"], "Qatar Major")
        self.assertEqual(find_final(self.FINALS, "major")["torneo"], "Qatar Major")

    def test_first_match_is_the_most_recent_and_misses_return_none(self):
        self.assertEqual(find_final(self.FINALS, "p1")["torneo"], "Madrid P1")
        self.assertIsNone(find_final(self.FINALS, "Tokio"))
        self.assertIsNone(find_final(self.FINALS, ""))
        self.assertIsNone(find_final([], "madrid"))


class RecentFinalsHaveTheNewFields(unittest.TestCase):
    def test_start_date_and_optional_semifinalists_are_exposed(self):
        today = date.today()
        doc = {"_meta": {"tipo": "wikipedia", "actualizado": today.isoformat(), "fuente": "x"}, "torneos": [
            {"name": "Madrid P1", "place": "Madrid 🇪🇸", "badge": "p1", "badgeText": "P1",
             "start": (today - timedelta(days=10)).isoformat(), "end": (today - timedelta(days=4)).isoformat(),
             "final_femenina": {"ganadoras": "A / B", "finalistas": "C / D", "resultado": "6–3 6–4",
                                "semifinalistas": ["E / F", "G / H", 7, "", "  "]}},
            {"name": "Sin fecha de inicio", "place": "X", "badge": "p2", "badgeText": "P2", "end": (today - timedelta(days=30)).isoformat(),
             "final_femenina": {"ganadoras": "A / B", "finalistas": "C / D"}}]}
        tmp = tempfile.mkdtemp()
        saved = os.environ.get("DATA_DIR")
        try:
            os.environ["DATA_DIR"] = tmp
            with open(os.path.join(tmp, "calendario.json"), "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            local_data._cache.clear() if hasattr(local_data, "_cache") else None
            finals = asyncio.run(local_data.get_recent_finals())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            os.environ.pop("DATA_DIR", None) if saved is None else os.environ.__setitem__("DATA_DIR", saved)
        self.assertEqual(finals[0]["fecha_inicio"], (today - timedelta(days=10)).isoformat())
        self.assertEqual(finals[0]["semifinalistas"], ["E / F", "G / H"])
        self.assertIsNone(finals[1]["fecha_inicio"])
        self.assertEqual(finals[1]["semifinalistas"], [])


if __name__ == "__main__":
    unittest.main()
