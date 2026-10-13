"""
Prueba de punta a punta en un proceso aparte: servidor real + tarea de Wikipedia (simulada) + endpoints.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parent / "_e2e_server.py"


def run(mode):
    env = {k: v for k, v in os.environ.items() if k not in ("DATA_DIR", "MCP_AUTH", "MCP_TOKEN")}
    out = subprocess.run([sys.executable, str(HELPER), mode], capture_output=True, text=True, timeout=90, env=env)
    line = next((l for l in out.stdout.splitlines() if l.startswith("RESULT:")), None)
    assert line, f"sin resultado\nSTDOUT:\n{out.stdout[-1500:]}\nSTDERR:\n{out.stderr[-1500:]}"
    return json.loads(line[len("RESULT:"):])


class ServerWithIngestion(unittest.TestCase):
    def test_a_real_server_refreshes_its_own_data_from_wikipedia_and_serves_it(self):
        r = run("ok")
        self.assertEqual(r["asked"], ["en.wikipedia.org/w/api.php"], "solo una petición, y solo a la API de Wikipedia")
        ing = r["data_status"]["ingesta"]
        self.assertTrue(ing["ok"], ing)
        self.assertEqual(ing["ranking_filas"], 10)
        self.assertEqual(r["data_status"]["datasets"]["ranking"]["tipo"], "wikipedia")
        self.assertEqual(r["data_status"]["datasets"]["ranking"]["licencia"], "CC BY-SA 4.0")
        self.assertEqual(r["ranking"][0], [1, "Jugadora Numero0", "20.000"])
        self.assertEqual(r["ranking_len"], 10)
        self.assertTrue(set(r["codes"].values()) == {200}, r["codes"])
        self.assertIn("official_url", r["live"])

    def test_if_wikipedia_is_down_the_server_keeps_serving_the_previous_data(self):
        r = run("down")
        ing = r["data_status"]["ingesta"]
        self.assertFalse(ing["ok"])
        self.assertIn("Se conservan los datos anteriores", ing["mensaje"])
        self.assertEqual(r["data_status"]["datasets"]["ranking"]["tipo"], "ejemplo", "siguen los datos de arranque")
        self.assertGreaterEqual(r["ranking_len"], 8)
        self.assertTrue(set(r["codes"].values()) == {200}, r["codes"])


if __name__ == "__main__":
    unittest.main()
