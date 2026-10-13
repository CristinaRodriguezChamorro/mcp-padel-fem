"""
Pruebas de la capa de datos local (sin red): formato de salida, filtros del calendario,
límites y tolerancia a archivos rotos.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import local_data as ld

TODAY = date(2026, 9, 29)


def run(coro):
    return asyncio.run(coro)


class LocalDataTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="padel-ld-")
        self._old = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = self._old

    def write(self, name, doc):
        Path(self.dir, f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    # ── ranking ──
    def test_ranking_is_sorted_limited_to_20_and_has_the_web_shape(self):
        rows = [{"pos": i, "name": f"Jugadora {i}", "pts": "1.000"} for i in range(30, 0, -1)]
        self.write("ranking", {"_meta": {}, "jugadoras": rows + [{"pos": 2, "name": "  "}, "basura", {"pos": "x", "name": "Sin posición"}]})
        ranking = run(ld.get_ranking_live())
        self.assertEqual(len(ranking), 20)
        self.assertEqual([r["pos"] for r in ranking[:3]], [1, 2, 3])
        self.assertEqual(set(ranking[0]), {"pos", "name", "pair", "flag", "pts", "photo"})
        self.assertEqual(len(run(ld.get_womens_ranking_names(limit=5))), 5)

    def test_missing_or_broken_files_give_empty_results_not_exceptions(self):
        self.assertEqual(run(ld.get_ranking_live()), [])
        self.assertEqual(run(ld.get_calendar_live(TODAY)), [])
        t = run(ld.get_tournament_now())
        self.assertFalse(t["active"])
        self.assertTrue(t["error"])
        Path(self.dir, "ranking.json").write_text("{roto", encoding="utf-8")
        self.assertEqual(run(ld.get_ranking_live()), [])
        Path(self.dir, "ranking.json").write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(run(ld.get_ranking_live()), [])

    # ── calendario ──
    def test_calendar_hides_finished_marks_live_sorts_and_formats(self):
        def t(name, start, end):
            return {"name": name, "place": "X", "start": start, "end": end, "badge": "p1", "badgeText": "P1", "tv": "TV"}
        self.write("calendario", {"torneos": [
            t("Futuro lejano", "2026-11-08", "2026-11-15"),
            t("Terminado", "2026-09-01", "2026-09-06"),
            t("En curso", "2026-09-28", "2026-10-04"),
            t("Cruza de mes", "2026-10-26", "2026-11-01"),
            t("Fechas rotas", "no-es-fecha", "2026-11-01"),
            t("Fin antes que inicio", "2026-12-10", "2026-12-01"),
        ]})
        items = run(ld.get_calendar_live(TODAY))
        self.assertEqual([i["name"] for i in items], ["En curso", "Cruza de mes", "Futuro lejano"])
        self.assertTrue(items[0]["live"])
        self.assertFalse(items[1]["live"])
        self.assertEqual((items[0]["day"], items[0]["month"]), ("28-4", "Sep"))
        self.assertEqual(items[1]["day"], "26-1")
        self.assertTrue(all("_start" not in i and "_end" not in i for i in items))

    def test_calendar_is_capped_at_ten(self):
        self.write("calendario", {"torneos": [
            {"name": f"T{i}", "start": f"2026-10-{i + 1:02d}", "end": f"2026-10-{i + 1:02d}"} for i in range(15)]})
        self.assertEqual(len(run(ld.get_calendar_live(TODAY))), 10)

    # ── torneo ──
    def test_tournament_inactive_active_and_non_female(self):
        self.write("torneo", {"active": False})
        t = run(ld.get_tournament_now())
        self.assertFalse(t["active"])
        self.assertEqual(t["results"], [])
        self.write("torneo", {"_meta": {"actualizado": "2026-09-28"}, "active": True, "name": "N", "place": "P",
                              "dates": "D", "watch": ["TV"], "results": [{"round": "F"}, "basura"],
                              "next_match": {"round": "SF"}, "structure": {"draw_size": 16}})
        t = run(ld.get_tournament_now())
        self.assertTrue(t["active"])
        self.assertEqual(len(t["results"]), 1)
        self.assertEqual(t["structure"]["draw_size"], 16)
        self.assertEqual(t["updated"], "2026-09-28")
        self.assertFalse(run(ld.get_tournament_now("male"))["active"])

    def test_tournament_is_deduced_from_the_calendar_without_live_scores(self):
        self.write("torneo", {"active": False})
        self.write("calendario", {"_meta": {"fuente": "Wikipedia · Prueba", "url": "https://en.wikipedia.org/wiki/X"}, "torneos": [
            {"name": "Cruza de mes", "place": "Ciudad", "start": "2026-09-27", "end": "2026-10-03", "tv": "Canal A · Canal B"},
            {"name": "Cancelado", "start": "2026-09-20", "end": "2026-10-20", "cancelado": True},
            {"name": "Futuro", "start": "2026-11-01", "end": "2026-11-07"}]})
        t = run(ld.get_tournament_now(today=TODAY))
        self.assertTrue(t["active"])
        self.assertEqual((t["name"], t["dates"]), ("Cruza de mes", "27 Sep – 3 Oct"))
        self.assertEqual(t["watch"], ["Canal A", "Canal B"])
        self.assertEqual((t["results"], t["next_match"], t["current_match"]), ([], None, None))
        self.assertEqual(t["official_url"], ld.OFFICIAL_LIVE_URL)
        self.assertIn("Wikipedia", t["source"])
        self.assertFalse(run(ld.get_tournament_now(today=date(2026, 10, 10)))["active"])
        self.assertEqual(run(ld.get_tournament_now(today=date(2026, 10, 10)))["official_url"], ld.OFFICIAL_LIVE_URL)

    def test_a_manual_tournament_takes_priority_over_the_calendar(self):
        self.write("calendario", {"torneos": [{"name": "Del calendario", "start": "2026-09-27", "end": "2026-10-03"}]})
        self.write("torneo", {"active": True, "name": "A mano", "results": [{"round": "F"}]})
        t = run(ld.get_tournament_now(today=TODAY))
        self.assertEqual(t["name"], "A mano")
        self.assertEqual(len(t["results"]), 1)
        self.assertEqual(t["official_url"], ld.OFFICIAL_LIVE_URL)

    def test_recent_finals_are_past_only_newest_first_and_skip_incomplete_ones(self):
        def t(name, end, final=None, **extra):
            item = {"name": name, "place": "X", "badgeText": "P1", "start": end, "end": end, **extra}
            if final is not None:
                item["final_femenina"] = final
            return item
        good = lambda w: {"ganadoras": w, "finalistas": "Otra / Pareja", "resultado": "6–4 / 6–4"}
        self.write("calendario", {"torneos": [
            t("Antigua", "2026-03-01", good("A / B")),
            t("Reciente", "2026-09-20", good("C / D")),
            t("Futura con final", "2026-12-01", good("E / F")),
            t("Sin final", "2026-09-10"),
            t("Cancelada", "2026-09-15", good("G / H"), cancelado=True),
            t("Final incompleta", "2026-09-12", {"ganadoras": "", "finalistas": "X / Y"}),
        ]})
        finals = run(ld.get_recent_finals(today=TODAY))
        self.assertEqual([f["torneo"] for f in finals], ["Reciente", "Antigua"])
        self.assertEqual(finals[0]["ganadoras"], "C / D")
        self.assertEqual(len(run(ld.get_recent_finals(limit=1, today=TODAY))), 1)
        self.assertEqual(run(ld.get_recent_finals(today=date(2025, 1, 1))), [])

    def test_recent_finals_without_a_calendar_file_is_empty_not_an_error(self):
        self.assertEqual(run(ld.get_recent_finals(today=TODAY)), [])

    def test_status_reports_staleness_source_and_licence(self):
        self.write("ranking", {"_meta": {"tipo": "wikipedia", "actualizado": "2026-08-22", "consultado": "2026-09-29",
                                         "fuente": "Wikipedia · X", "url": "https://en.wikipedia.org/wiki/X",
                                         "licencia": "CC BY-SA 4.0", "licencia_url": "https://creativecommons.org/licenses/by-sa/4.0/"},
                               "jugadoras": []})
        st = ld.dataset_status(TODAY)["ranking"]
        self.assertEqual((st["antiguedad_dias"], st["desactualizado"]), (38, True))
        self.assertEqual((st["fuente"], st["licencia"], st["consultado"]), ("Wikipedia · X", "CC BY-SA 4.0", "2026-09-29"))
        self.write("ranking", {"_meta": {"tipo": "wikipedia", "actualizado": "2026-09-20"}, "jugadoras": []})
        self.assertFalse(ld.dataset_status(TODAY)["ranking"]["desactualizado"])
        self.write("ranking", {"_meta": {"tipo": "wikipedia", "actualizado": None}, "jugadoras": []})
        st = ld.dataset_status(TODAY)["ranking"]
        self.assertEqual((st["antiguedad_dias"], st["desactualizado"]), (None, False))

    # ── estado ──
    def test_dataset_status_reports_type_age_and_missing_files(self):
        self.write("ranking", {"_meta": {"tipo": "ejemplo", "actualizado": "2026-09-20", "nota": "n"}, "jugadoras": []})
        self.write("torneo", {"active": False})
        st = ld.dataset_status(TODAY)
        self.assertEqual(st["ranking"]["tipo"], "ejemplo")
        self.assertEqual(st["ranking"]["antiguedad_dias"], 9)
        self.assertTrue(st["torneo"]["disponible"])
        self.assertIsNone(st["torneo"]["actualizado"])
        self.assertFalse(st["calendario"]["disponible"])

    def test_shipped_data_files_are_valid_and_clearly_labelled(self):
        os.environ.pop("DATA_DIR", None)
        os.environ["DATA_DIR"] = str(Path(__file__).resolve().parent.parent / "data")
        st = ld.dataset_status()
        for name in ("ranking", "calendario"):
            self.assertTrue(st[name]["disponible"], name)
            self.assertEqual(st[name]["tipo"], "ejemplo", f"{name}: los datos sin verificar deben estar marcados como ejemplo")
        self.assertEqual(len(run(ld.get_ranking_live())), 10)
        example = json.loads(Path(os.environ["DATA_DIR"], "torneo.ejemplo.json").read_text(encoding="utf-8"))
        self.assertEqual(example["_meta"]["tipo"], "ejemplo")


if __name__ == "__main__":
    unittest.main()
