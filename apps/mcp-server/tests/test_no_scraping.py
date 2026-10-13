"""
Barrera contra el scraping: falla si alguien vuelve a introducir código que lea webs de
terceros que no sean las permitidas (feeds RSS de los medios y APIs/imágenes de Wikimedia).
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent           # apps/mcp-server
REPO = ROOT.parent.parent

FORBIDDEN_LIBS = ("playwright", "bs4", "beautifulsoup4", "pypdf", "lxml", "selenium", "scrapy")

# Los ÚNICOS dominios a los que el código puede apuntar. Cada uno tiene una razón; añadir uno nuevo
# obliga a pasar por aquí y, por tanto, a revisar sus condiciones de uso.
ALLOWED_HOSTS = {
    "okdiario.com",
    "padelapi.org",                                                    # datos reales con licencia (token propio)
    "en.wikipedia.org", "es.wikipedia.org", "commons.wikimedia.org",   # datos (CC BY-SA) y fotos
    "creativecommons.org",                                             # enlace a la licencia
    "api.github.com", "github.com",                                    # comprobar el token y enlazar el repo
    "e00-marca.uecdn.es", "www.mundodeportivo.com", "www.padelspain.net",
    "www.padeladdict.com", "www.setpadelmagazine.es",                  # feeds RSS de los medios
}
# La web de la FIP solo puede aparecer como ENLACE al marcador oficial, nunca como fuente de datos.
LINK_ONLY_HOSTS = {"www.padelfip.com": ("tools/local_data.py", "OFFICIAL_LIVE_URL =")}


def python_sources():
    return [p for p in ROOT.rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts]


class NoScraping(unittest.TestCase):
    def test_no_scraping_libraries_in_the_code(self):
        pattern = re.compile(r"^\s*(?:from|import)\s+(playwright|bs4|pypdf|lxml|selenium|scrapy)\b", re.M)
        offenders = [str(p.relative_to(ROOT)) for p in python_sources() if pattern.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_every_url_in_the_code_goes_to_an_allowed_host(self):
        offenders = []
        for path in python_sources():
            rel = str(path.relative_to(ROOT))
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for host in re.findall(r"https?://([A-Za-z0-9.\-]+)", line):
                    if host in ALLOWED_HOSTS:
                        continue
                    only_file, marker = LINK_ONLY_HOSTS.get(host, (None, None))
                    if only_file == rel and marker in line:
                        continue
                    offenders.append(f"{rel}:{number}: {host}")
        self.assertEqual(offenders, [])

    def test_the_fip_site_is_never_a_data_source(self):
        """padelfip.com solo puede estar en la constante del enlace; ni una petición, ni un feed, ni una foto."""
        hits = []
        for path in python_sources():
            for line in path.read_text(encoding="utf-8").splitlines():
                if "padelfip.com" in line or "padelspeak.com" in line:
                    hits.append((str(path.relative_to(ROOT)), line.strip()))
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][0], "tools/local_data.py")
        self.assertTrue(hits[0][1].startswith("OFFICIAL_LIVE_URL ="))

    def test_requirements_and_dockerfile_carry_no_scraping_stack(self):
        reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for lib in FORBIDDEN_LIBS:
            self.assertNotIn(lib, reqs, lib)
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8").lower()
        for word in ("chromium", "playwright"):
            self.assertNotIn(word, docker, word)

    def test_no_leftover_copies_of_the_old_scraper(self):
        for stray in ("live_data.py", "script.js", "mcp-padel-fem-v31-en-juego-cuadro-exacto", "apps/mcp-server.zip"):
            self.assertFalse((REPO / stray).exists(), stray)
        self.assertFalse((ROOT / "tools" / "live_data.py").exists())

    def test_importing_and_using_the_app_never_loads_a_scraping_library(self):
        code = (
            "import sys, tempfile, os, asyncio\n"
            "os.environ['DATA_DIR'] = tempfile.mkdtemp()\n"
            "sys.path.insert(0, %r)\n"
            "import server\n"
            "asyncio.run(server.get_ranking_live()); asyncio.run(server.get_calendar_live()); asyncio.run(server.get_tournament_now())\n"
            "bad = [m for m in ('playwright', 'bs4', 'pypdf', 'lxml') if m in sys.modules]\n"
            "print('CARGADAS:' + ','.join(bad))\n" % str(ROOT)
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertIn("CARGADAS:", out.stdout, out.stderr[-500:])
        self.assertEqual(out.stdout.strip().splitlines()[-1], "CARGADAS:")


if __name__ == "__main__":
    unittest.main()
