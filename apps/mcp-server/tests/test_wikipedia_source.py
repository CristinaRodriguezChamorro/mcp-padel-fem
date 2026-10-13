"""
Pruebas del lector de Wikipedia con páginas de prueba (sin internet).

Las páginas de prueba imitan la estructura que se vio en 'Premier Padel 2026': filas con rowspan
en el ranking (parejas empatadas), celdas en blanco en los resultados, países como código
("ESP") o como nombre ("España"), y torneos cancelados o aún sin jugar.
"""
import asyncio
import os
import sys
import unittest
from datetime import date

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import wikipedia_source as ws

FLAG = '<span class="flagicon"><img alt="" src="x.png"></span>'

RANKING_HTML = f"""
<table class="wikitable">
<tr><th colspan="3">2026 Men's Ranking</th><th colspan="5">Tournaments Won</th></tr>
<tr><th>N.º</th><th>Name</th><th>Points</th><th>P2</th><th>P1</th><th>Majors</th><th>Finals</th><th>Total</th></tr>
<tr><td rowspan="2">1</td><td>{FLAG} ARG Agustín Tapia</td><td rowspan="2">21051</td><td rowspan="2">3</td><td rowspan="2">4</td><td rowspan="2">1</td><td rowspan="2">0</td><td rowspan="2">8</td></tr>
<tr><td>ESP Arturo Coello</td></tr>
</table>
<table class="wikitable">
<tr><th colspan="3">2026 Women's Ranking</th><th colspan="5">Tournaments Won</th></tr>
<tr><th>N.º</th><th>Name</th><th>Points</th><th>P2</th><th>P1</th><th>Majors</th><th>Finals</th><th>Total</th></tr>
<tr><td rowspan="2">1</td><td>{FLAG} ARG Delfina Brea</td><td rowspan="2">18211</td><td rowspan="2">2</td><td rowspan="2">2</td><td rowspan="2">1</td><td rowspan="2">0</td><td rowspan="2">5</td></tr>
<tr><td>ESP Gemma Triay</td></tr>
<tr><td>3</td><td>{FLAG} ESP Beatriz González<sup class="reference">[1]</sup></td><td>14546</td><td rowspan="2">4</td><td rowspan="2">2</td><td rowspan="2">0</td><td rowspan="2">0</td><td rowspan="2">6</td></tr>
<tr><td>4</td><td>ESP Paula Josemaría</td><td>14043</td></tr>
<tr><td>5</td><td>España Ariana Sánchez</td><td>12783</td><td>0</td><td>2</td><td>0</td><td>0</td><td>2</td></tr>
<tr><td>6</td><td>POR Sofia Araújo</td><td>6.863</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>7</td><td>&lt;img src=x onerror=alert(1)&gt;</td><td>6500</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>8</td><td>Jugadora Sin Puntos</td><td>n/d</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</table>
<p>Updated on 22 August 2026</p>
"""


def row(tournament, dates, men=("", "", ""), women=("", "", ""), blank=True):
    cells = [tournament, dates] + ([""] if blank else []) + list(men) + ([""] if blank else []) + list(women)
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


TOURNAMENTS_HTML = f"""
<table class="wikitable">
<tr><th rowspan="2">Tournament</th><th rowspan="2">Date</th><th colspan="4">Men's</th><th colspan="4">Women's</th></tr>
<tr><th>Winners</th><th>Score</th><th>Runners-up</th><th></th><th>Winners</th><th>Score</th><th>Runners-up</th></tr>
{row(FLAG + " KSA Riyadh P1", "7 – 14 February",
     ("ARG Agustín Tapia<br>ESP Arturo Coello", "6–4 / 6–2", "ESP Alejandro Galán<br>ARG Federico Chingotto"),
     ("ESP Ariana Sánchez<br>ESP Andrea Ustero", "3–6 / 6–1 / 6–4", "ARG Delfina Brea<br>ESP Gemma Triay"))}
{row("ESP Gijón P2", "1 – 8 March",
     ("ESP Alejandro Galán ARG Federico Chingotto", "7–5 / 7–6", "ARG Agustín Tapia ESP Arturo Coello"),
     ("ARG Delfina Brea ESP Gemma Triay", "6–4 / 6–7 / 6–3", "ESP Ariana Sánchez ESP Andrea Ustero"), blank=False)}
{row("QAT Qatar Major", "Cancelled", blank=False)}
{row("ESP Madrid P1", "31 August – 6 September", blank=False)}
{row("FRA Paris Major", "7 – 13 September", blank=False)}
{row("ESP Premier Padel Finals", "7 – 13 December", blank=False)}
{row("ESP Tramposo P2", "1 – 8 June",
     ("ESP A B", "6–4", "ESP C D"),
     ("ESP Ana López<br>&lt;img src=x onerror=alert(1)&gt;", "6–4", "ARG Delfina Brea<br>ESP Gemma Triay"), blank=False)}
{row("ESP Fecha Rota P2", "mediados de mayo", blank=False)}
</table>
"""


def run(coro):
    return asyncio.run(coro)


class SafeTextTests(unittest.TestCase):
    def test_clean_text_removes_angle_brackets_and_control_chars(self):
        self.assertEqual(ws.clean_text("a<b>c\x00d\u200b e"), "abcd e")   # \u200b es de categoría 'Cf'
        self.assertEqual(ws.clean_text("  uno \n\n\n dos  "), "uno\ndos")

    def test_names_must_look_like_names(self):
        for ok in ("Gemma Triay", "Sofía Araújo", "Marta O'Neil", "Ana-María López", "Riyadh P1"):
            self.assertEqual(ws.safe_name(ok), ok)
        for bad in ("img src=x onerror=alert(1)", "a", "x" * 80, "Nombre; DROP TABLE", "javascript:alert(1)", ""):
            self.assertIsNone(ws.safe_name(bad), bad)

    def test_scores(self):
        self.assertEqual(ws.safe_score("6–4 / 6–7 / 6–3"), "6–4 / 6–7 / 6–3")
        self.assertEqual(ws.safe_score("w/o"), "w/o")
        self.assertIsNone(ws.safe_score("6-4 onerror=alert(1)\"><script>"))


class CountryAndPlayersTests(unittest.TestCase):
    def test_country_prefix_as_code_or_name(self):
        self.assertEqual(ws.split_country("ESP Ariana Sánchez"), ("ES", "Ariana Sánchez"))
        self.assertEqual(ws.split_country("España Ariana Sánchez"), ("ES", "Ariana Sánchez"))
        self.assertEqual(ws.split_country("Argentina Delfina Brea"), ("AR", "Delfina Brea"))
        self.assertEqual(ws.split_country("Delfina Brea"), ("", "Delfina Brea"))
        self.assertEqual(ws.split_country("ESPECIAL Nombre"), ("", "ESPECIAL Nombre"))     # no confunde palabras

    def test_longer_country_names_are_not_left_glued_to_the_tournament_or_player_name(self):
        self.assertEqual(ws.split_country("United Arab Emirates Dubai P1"), ("AE", "Dubai P1"))
        self.assertEqual(ws.split_country("Emiratos Árabes Unidos Dubai P1"), ("AE", "Dubai P1"))
        self.assertEqual(ws.split_country("Great Britain London P1"), ("GB", "London P1"))
        self.assertEqual(ws.split_country("Saudi Arabia Riyadh P1"), ("SA", "Riyadh P1"))
        self.assertEqual(ws.split_country("South Africa Pretoria P1"), ("ZA", "Pretoria P1"))

    def test_flag_emoji(self):
        self.assertEqual(ws.flag_emoji("ES"), "🇪🇸")
        self.assertEqual(ws.flag_emoji("xx" * 2), "")

    def test_two_players_with_or_without_line_breaks(self):
        self.assertEqual([n for _, n in ws.split_players("ESP Ariana Sánchez\nESP Andrea Ustero")],
                         ["Ariana Sánchez", "Andrea Ustero"])
        self.assertEqual([n for _, n in ws.split_players("ESP Ariana Sánchez ESP Andrea Ustero")],
                         ["Ariana Sánchez", "Andrea Ustero"])
        self.assertEqual([n for _, n in ws.split_players("ARG Delfina Brea ESP Gemma Triay")],
                         ["Delfina Brea", "Gemma Triay"])

    def test_a_dangerous_player_is_dropped_not_cleaned(self):
        players = ws.split_players("ESP Ana López\n<img src=x onerror=alert(1)>")
        self.assertEqual([n for _, n in players], ["Ana López"])


class TableTests(unittest.TestCase):
    def test_rowspan_is_expanded(self):
        grid = ws.expand(ws.parse_tables(RANKING_HTML)[1])
        self.assertEqual(grid[2][:3], ["1", "ARG Delfina Brea", "18211"])
        self.assertEqual(grid[3][:3], ["1", "ESP Gemma Triay", "18211"])     # hereda rango y puntos
        self.assertEqual(grid[5][:3], ["4", "ESP Paula Josemaría", "14043"])  # rango y puntos propios, estadísticas heredadas
        self.assertEqual(grid[5][3], "4")

    def test_references_are_ignored(self):
        grid = ws.expand(ws.parse_tables(RANKING_HTML)[1])
        self.assertNotIn("[1]", grid[4][1])

    def test_nested_tables_do_not_break_parsing(self):
        html = "<table><tr><td>a<table><tr><td>x</td></tr></table>b</td><td>c</td></tr></table>"
        grid = ws.expand(ws.parse_tables(html)[0])
        self.assertEqual(len(grid[0]), 2)


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.rows = ws.parse_women_ranking(RANKING_HTML)

    def test_picks_the_women_table_not_the_men_one(self):
        names = [r["name"] for r in self.rows]
        self.assertIn("Delfina Brea", names)
        self.assertNotIn("Agustín Tapia", names)

    def test_pairs_with_shared_points_and_players_with_own_points(self):
        by = {r["name"]: r for r in self.rows}
        self.assertEqual((by["Delfina Brea"]["pos"], by["Delfina Brea"]["pts"]), (1, "18.211"))
        self.assertEqual((by["Gemma Triay"]["pos"], by["Gemma Triay"]["pts"]), (1, "18.211"))
        self.assertEqual((by["Beatriz González"]["pos"], by["Beatriz González"]["pts"]), (3, "14.546"))
        self.assertEqual((by["Paula Josemaría"]["pos"], by["Paula Josemaría"]["pts"]), (4, "14.043"))

    def test_flags_names_as_country_and_thousand_separators(self):
        by = {r["name"]: r for r in self.rows}
        self.assertEqual(by["Delfina Brea"]["flag"], "🇦🇷")
        self.assertEqual(by["Ariana Sánchez"]["flag"], "🇪🇸")          # "España" como nombre
        self.assertEqual(by["Sofia Araújo"]["pts"], "6.863")           # "6.863" -> 6863 -> "6.863"

    def test_unsafe_and_incomplete_rows_are_dropped(self):
        names = [r["name"] for r in self.rows]
        self.assertFalse(any("onerror" in n or "img" in n for n in names))
        self.assertNotIn("Jugadora Sin Puntos", names)
        self.assertEqual(len(self.rows), 6)      # Brea, Triay, González, Josemaría, Sánchez, Araújo

    def test_no_table_gives_empty_list(self):
        self.assertEqual(ws.parse_women_ranking("<p>nada</p>"), [])
        self.assertEqual(ws.parse_women_ranking(""), [])

    def test_source_date(self):
        self.assertEqual(ws.source_date(RANKING_HTML), date(2026, 8, 22))
        self.assertIsNone(ws.source_date("<p>sin fecha</p>"))


class TournamentTests(unittest.TestCase):
    def setUp(self):
        self.items = {t["name"]: t for t in ws.parse_tournaments(TOURNAMENTS_HTML, 2026)}

    def test_dates_simple_and_across_months(self):
        self.assertEqual((self.items["Riyadh P1"]["start"], self.items["Riyadh P1"]["end"]), ("2026-02-07", "2026-02-14"))
        self.assertEqual((self.items["Madrid P1"]["start"], self.items["Madrid P1"]["end"]), ("2026-08-31", "2026-09-06"))

    def test_women_final_with_blank_cells_and_with_nine_cells(self):
        f = self.items["Riyadh P1"]["final_femenina"]
        self.assertEqual(f["ganadoras"], "Ariana Sánchez / Andrea Ustero")
        self.assertEqual(f["finalistas"], "Delfina Brea / Gemma Triay")
        self.assertEqual(f["resultado"], "3–6 / 6–1 / 6–4")
        g = self.items["Gijón P2"]["final_femenina"]
        self.assertEqual(g["ganadoras"], "Delfina Brea / Gemma Triay")

    def test_unplayed_cancelled_and_broken_rows(self):
        self.assertNotIn("final_femenina", self.items["Madrid P1"])
        self.assertTrue(self.items["Qatar Major"]["cancelado"])
        self.assertNotIn("Fecha Rota P2", self.items)                    # fecha ilegible: se descarta

    def test_a_final_with_a_dangerous_player_is_not_published(self):
        self.assertNotIn("final_femenina", self.items["Tramposo P2"])

    def test_badges_and_places(self):
        self.assertEqual((self.items["Riyadh P1"]["badge"], self.items["Riyadh P1"]["badgeText"]), ("p1", "P1"))
        self.assertEqual(self.items["Paris Major"]["badgeText"], "MAJOR")
        self.assertEqual(self.items["Premier Padel Finals"]["badgeText"], "FINALS")
        self.assertEqual(self.items["Riyadh P1"]["place"], "Riyadh 🇸🇦")
        self.assertEqual(self.items["Paris Major"]["place"], "Paris 🇫🇷")

    def test_date_parser_edge_cases(self):
        self.assertEqual(ws.parse_date_range("28 September – 4 October", 2026), (date(2026, 9, 28), date(2026, 10, 4)))
        self.assertEqual(ws.parse_date_range("1 – 8 Mar", 2026), (date(2026, 3, 1), date(2026, 3, 8)))
        for bad in ("", "Cancelled", "31 – 40 March", "8 – 1 March", "12 December – 3 January", "mañana"):
            self.assertIsNone(ws.parse_date_range(bad, 2026), bad)

    def test_no_table_gives_empty_list(self):
        self.assertEqual(ws.parse_tournaments("<p>nada</p>", 2026), [])


class FetchTests(unittest.TestCase):
    def client(self, handler):
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_request_is_polite_and_identifies_the_project(self):
        seen = {}

        def handler(request):
            seen["ua"], seen["q"] = request.headers["user-agent"], dict(request.url.params)
            return httpx.Response(200, json={"parse": {"title": "Premier Padel 2026", "revid": 7, "text": "x" * 800}})

        page = run(ws.fetch_page(2026, self.client(handler)))
        self.assertTrue(seen["ua"].startswith("PadelFemBot/"))
        self.assertNotIn("Mozilla", seen["ua"])
        self.assertEqual(seen["q"]["page"], "Premier_Padel_2026")
        self.assertEqual(seen["q"]["maxlag"], "5")
        self.assertEqual(seen["q"]["action"], "parse")
        self.assertEqual(page["url"], "https://en.wikipedia.org/wiki/Premier_Padel_2026")
        self.assertEqual(page["revid"], 7)

    def test_every_failure_becomes_a_source_error(self):
        cases = {
            "http": lambda r: httpx.Response(503),
            "no json": lambda r: httpx.Response(200, text="<html>"),
            "api error": lambda r: httpx.Response(200, json={"error": {"code": "maxlag", "info": "x"}}),
            "short": lambda r: httpx.Response(200, json={"parse": {"text": "corto"}}),
            "no parse": lambda r: httpx.Response(200, json={}),
        }
        for label, handler in cases.items():
            with self.assertRaises(ws.SourceError, msg=label):
                run(ws.fetch_page(2026, self.client(handler)))

        def down(request):
            raise httpx.ConnectError("sin red")
        with self.assertRaises(ws.SourceError):
            run(ws.fetch_page(2026, self.client(down)))


if __name__ == "__main__":
    unittest.main()
