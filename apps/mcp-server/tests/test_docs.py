"""
La explicación para todos (/como-funciona) y la letra pequeña de la web no pueden contradecir al código.
Estas pruebas atan cada cifra o afirmación de la explicación a la constante o al comportamiento real.
"""
import inspect
import os
os.environ.setdefault("MCP_TOOLS", "extended")   # los tests usan todas las herramientas; el valor por defecto son 3
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["WIKIPEDIA_INGEST"] = "off"

from tools import ingest, local_data, news

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "static" / "como-funciona.html").read_text(encoding="utf-8")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class ExplanationMatchesTheCode(unittest.TestCase):
    def setUp(self):
        self.text = text_of(PAGE)

    def test_numbers_in_the_story_are_the_real_ones(self):
        self.assertEqual(ingest.MIN_RANKING_ROWS, 8)
        self.assertIn("al menos ocho jugadoras", self.text)
        self.assertEqual(local_data.STALE_DAYS, 14)
        self.assertIn("más de dos semanas", self.text)
        saved = os.environ.pop("WIKIPEDIA_INGEST_HOURS", None)
        try:
            self.assertEqual(ingest.interval_hours(), 24.0)
        finally:
            if saved is not None:
                os.environ["WIKIPEDIA_INGEST_HOURS"] = saved
        self.assertIn("Una vez al día", self.text)
        import server
        self.assertEqual(news.NEWS_CACHE_SECONDS, 10 * 60)
        self.assertIn("NEWS_CACHE_SECONDS", inspect.getsource(server.api_news))
        self.assertIn("cada diez minutos", self.text)

    def test_the_count_of_checks_is_not_an_exaggeration(self):
        total = unittest.defaultTestLoader.discover(str(ROOT / "tests")).countTestCases()
        self.assertGreater(total, 100, "la explicación dice 'más de cien' comprobaciones")
        self.assertIn("más de cien", self.text)

    def test_the_limits_it_admits_are_stated_plainly(self):
        for phrase in ("No enseña marcadores en directo", "No es una web oficial", "Solo enseña las finales",
                       "pedir permiso a la FIP"):
            self.assertIn(phrase, self.text, phrase)

    def test_every_source_is_named(self):
        for phrase in ("Padel API", "Wikipedia", "periódicos", "marcador oficial", "Railway", "GitHub", "Docker", "MCP", "token"):
            self.assertIn(phrase, self.text, phrase)

    def test_internal_links_point_at_real_routes(self):
        import server
        routes = {r.path for r in server.app.routes}
        links = set(re.findall(r'href="(/[^"#]*)"', PAGE)) | set(re.findall(r"href='(/[^'#]*)'", INDEX))
        for link in links:
            self.assertTrue(link in routes or link.startswith("/static"), f"enlace roto: {link}")
        for expected in ("/api/data-status", "/"):
            self.assertIn(expected, links)
        # el enlace del pie de la web hacia esta explicación también debe existir
        self.assertIn("/como-funciona", routes)
        self.assertIn("'/como-funciona'", INDEX)

    def test_the_diagram_is_valid_and_fits_its_canvas(self):
        svg = re.search(r"<svg.*?</svg>", PAGE, re.S).group(0).replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        _, _, width, height = (float(v) for v in root.attrib["viewBox"].split())
        rects = root.findall(f".//{ns}rect")
        self.assertGreaterEqual(len(rects), 6)
        for r in rects:
            x, y, w, h = (float(r.attrib[k]) for k in ("x", "y", "width", "height"))
            self.assertTrue(x >= 0 and y >= 0 and x + w <= width and y + h <= height, f"fuera del lienzo: {r.attrib}")
        for t in root.findall(f".//{ns}text"):
            self.assertTrue(0 <= float(t.attrib["x"]) <= width and 0 <= float(t.attrib["y"]) <= height)
        self.assertIsNotNone(root.find(f"{ns}title"))
        self.assertIsNotNone(root.find(f"{ns}desc"))

    def test_the_page_is_readable_by_design(self):
        self.assertRegex(PAGE, r"font:\s*20px")
        self.assertIn('lang="es"', PAGE)
        self.assertIn("viewport", PAGE)
        self.assertNotRegex(PAGE, r"<script")           # una página de lectura no necesita código


class SmallPrintInTheWeb(unittest.TestCase):
    def test_index_has_one_small_print_footer_with_every_source_and_the_disclaimer(self):
        for needle in ("f.id='fuentes'", "font:10px", "Wikipedia", "web oficial de la FIP",
                       "Web no oficial, sin relación con la FIP ni con Premier Padel", "/como-funciona",
                       "resúmenes automáticos de", "Cada noticia enlaza a su original",
                       "ni con las marcas mencionadas"):
            self.assertIn(needle, INDEX, needle)
        self.assertEqual(INDEX.count("f.id='fuentes'"), 1)

    def test_nothing_in_the_web_still_claims_official_fip_data(self):
        self.assertNotIn("Datos oficiales FIP", INDEX)
        self.assertNotIn("actualización automática", INDEX)


class SponsorsSectionIsNotAdvertising(unittest.TestCase):
    FORBIDDEN = ("contrato", "fichaje", "fichó", "fichada", "renov", "embajador", "exclusiv", "más vendida",
                 "marketin", "patrocin", "apuesta", "atada", "pale de", "oferta", "descuento", "comprar")

    def data_block(self):
        start = INDEX.index("const SPONSORS_DATA=")
        return INDEX[start:INDEX.index("];", start) + 2]

    def section_html(self):
        start = INDEX.index('<div id="sponsors" class="section">')
        return INDEX[start:INDEX.index('<div id="sabiasque"', start)]

    def test_no_note_survives_and_there_are_no_links_or_prices(self):
        block = self.data_block()
        self.assertEqual(len(re.findall(r'\{name:"[^"]+",pair:', block)), 10)
        self.assertEqual(re.findall(r"note:'([^']*)'", block), [""] * 10)
        self.assertNotIn("http", block)
        self.assertNotRegex(block, r"€|\$\d|precio")

    def test_nothing_in_the_data_or_the_section_talks_about_contracts_or_deals(self):
        text = (self.data_block() + self.section_html()).lower()
        for word in self.FORBIDDEN:
            self.assertNotIn(word, text, word)

    def test_the_tab_is_renamed_and_the_disclaimer_is_visible(self):
        self.assertIn(">Sponsors</button>", INDEX)
        self.assertIn("Sponsors de las jugadoras", INDEX)
        self.assertNotIn("Sponsors & marcas", INDEX)
        for phrase in ("sin verificar con las jugadoras ni con las marcas", "puede estar desactualizada",
                       "no tiene relación con las marcas ni recibe remuneración"):
            self.assertIn(phrase, self.section_html(), phrase)


class NewsCardsShowDateAndDirectLink(unittest.TestCase):
    def test_the_cards_show_the_real_date_only_when_there_is_one_and_always_a_direct_link(self):
        for needle in ("${featured.fecha?`<div class=\"news-date\">", "${item.fecha?`<div class=\"news-date\">",
                       "${leerNoticia(featured)}", "${leerNoticia(item)}", "Leer la noticia completa",
                       "function leerNoticia(item)", 'rel="noopener noreferrer"'):
            self.assertIn(needle, INDEX, needle)
        self.assertNotIn("nofollow", INDEX)

    def test_the_summary_is_always_shown(self):
        self.assertIn("${featured.resumen?`<div class=\"resumen\">${esc(featured.resumen)}</div>`:''}", INDEX)
        self.assertIn("${item.resumen?`<div class=\"resumen\">${esc(item.resumen)}</div>`:''}", INDEX)

    def test_there_is_no_link_only_note_any_more(self):
        self.assertNotIn("Solo titular y enlace", INDEX)


class ThirdPartyTextIsEscapedInTheWeb(unittest.TestCase):
    def test_news_templates_escape_everything_and_check_urls(self):
        for raw in ("${featured.titular}", "${item.titular}", "${featured.resumen}", "${item.resumen}",
                    "${f.url}", "${f.titulo}", "${f.domain}", "${f.autor}"):
            self.assertNotIn(raw, INDEX, raw)
        for needed in ("const esc=", "const safeUrl=", 'rel="noopener noreferrer"', "esc(featured.titular)", "esc(item.titular)"):
            self.assertIn(needed, INDEX, needed)

    def test_attribution_links_are_never_marked_nofollow(self):
        self.assertNotIn("nofollow", INDEX)


class PadelApiIsVisibleAndSecretsStayOut(unittest.TestCase):
    def test_small_print_and_late_banner_know_about_padel_api(self):
        for needle in ("if(d.tipo==='api'&&!api)api=d;", "if(api){", "link('Padel API'", "}else if(wiki){",
                       "(d.tipo==='wikipedia'||d.tipo==='api')&&d.desactualizado", "padelapi.org"):
            self.assertIn(needle, INDEX, needle)

    def test_the_explanation_says_who_the_data_provider_is_and_that_wikipedia_is_the_backup(self):
        text = text_of(PAGE)
        self.assertIn("Padel API", text)
        self.assertIn("El proveedor de datos", text)
        self.assertRegex(text, r"(?i)si el proveedor no está disponible.*Wikipedia")

    def test_no_secret_value_is_written_anywhere_in_the_repository(self):
        repo = ROOT.parent.parent
        files = [p for p in list(repo.glob("*.md")) + list(repo.glob("*.yml")) + list(repo.glob("*.toml")) + list(repo.glob("*.sh"))
                 + list((repo / "examples").glob("*")) + list(ROOT.rglob("*.html")) if p.is_file()]
        self.assertTrue(files)
        pattern = re.compile(r"(PADELAPI_TOKEN|MCP_TOKEN|GROQ_API_KEY)\s*[=:]\s*[\"']?(?![$<{]|tu[_-]|TU[_-]|\.\.\.|\s)[A-Za-z0-9_\-]{10,}")
        for path in files:
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8", errors="ignore")), f"posible secreto en {path.name}")

    def test_the_compose_file_passes_the_token_through_as_a_variable_only(self):
        compose = (ROOT.parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("PADELAPI_TOKEN=${PADELAPI_TOKEN}", compose)
        self.assertIn("MCP_TOOLS=${MCP_TOOLS}", compose)


class DeploymentFilesPointAtRealFiles(unittest.TestCase):
    """Si una ruta de railway.toml o de un COPY no existe, el despliegue falla sin avisar antes."""
    REPO = ROOT.parent.parent

    @staticmethod
    def copies(dockerfile: Path):
        out = []
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*COPY\s+(?!--)(.+?)\s+(\S+)\s*$", line)
            if m:
                out.extend(m.group(1).split())
        return out

    def test_railway_toml_points_at_an_existing_dockerfile(self):
        text = (self.REPO / "railway.toml").read_text(encoding="utf-8")
        path = re.search(r'dockerfilePath\s*=\s*"([^"]+)"', text).group(1)
        self.assertEqual(path, "apps/mcp-server/Dockerfile")
        self.assertTrue((self.REPO / path).is_file(), path)

    def test_dockerfile_copies_only_paths_that_exist_from_its_own_folder(self):
        sources = self.copies(ROOT / "Dockerfile")
        self.assertIn("requirements.txt", sources)
        for src in sources:
            self.assertTrue((ROOT / src).exists(), f"COPY {src}: no existe desde apps/mcp-server")

    def test_dockerfile_starts_the_server_as_a_non_root_user(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "server.py"]', text)
        self.assertIn("USER appuser", text)
        self.assertTrue((ROOT / "server.py").is_file())

    def test_compose_builds_from_the_app_folder(self):
        self.assertIn("build: ./apps/mcp-server", (self.REPO / "docker-compose.yml").read_text(encoding="utf-8"))


class ThereArePhotosNowhere(unittest.TestCase):
    """Las fotos se quitaron del todo: ni rutas, ni código, ni imágenes, ni peticiones desde el navegador."""

    def test_no_photo_route_exists(self):
        import server
        paths = {r.path for r in server.app.routes if hasattr(r, "methods")}
        for gone in ("/api/player-photo", "/api/player-photo-image", "/api/player-photo-credit",
                     "/api/sponsor-photo-image", "/api/photo", "/creditos-fotos"):
            self.assertNotIn(gone, paths)
        self.assertFalse([p for p in paths if "photo" in p or "foto" in p])

    def test_the_code_never_mentions_photos_or_wikimedia(self):
        self.assertFalse((ROOT / "tools" / "photos.py").exists())
        files = list(ROOT.glob("*.py")) + list((ROOT / "padel_mcp").glob("*.py")) + list((ROOT / "tools").glob("*.py"))
        for path in files:
            text = path.read_text(encoding="utf-8").lower()
            for word in ("wikimedia", "photo", "player_photos"):
                self.assertNotIn(word, text, f"{path.name} menciona '{word}'")

    def test_the_web_loads_no_images_and_asks_nobody_for_photos(self):
        for needle in ("<img", "new Image", "player-photo", "getWikiPhoto", "wikimedia", "pageimages", "buildSlideshow",
                       "creditos-fotos", "es.wikipedia.org/w/api.php"):
            self.assertNotIn(needle, INDEX, needle)

    def test_the_explanation_does_not_talk_about_photos(self):
        for needle in ("foto", "Foto", "Wikimedia", "álbum"):
            self.assertNotIn(needle, PAGE, needle)


class OutletNames(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("NEWS_RSS_FEEDS")

    def tearDown(self):
        os.environ.pop("NEWS_RSS_FEEDS", None) if self.saved is None else os.environ.__setitem__("NEWS_RSS_FEEDS", self.saved)

    def test_default_feeds_are_listed_by_their_readable_names(self):
        os.environ.pop("NEWS_RSS_FEEDS", None)
        names = news.rss_status()["fuentes"]
        self.assertEqual(names, ["OKDiario", "Padel Addict"])
        self.assertFalse(any("uecdn" in n or "." in n for n in names))

    def test_an_unknown_feed_is_listed_by_its_host(self):
        os.environ["NEWS_RSS_FEEDS"] = "https://www.ejemplo.test/feed.xml, https://okdiario.com/deportes/padel/feed"
        status = news.rss_status()
        self.assertEqual(status["fuentes"], ["OKDiario"])
        self.assertEqual(status["ignoradas"], ["ejemplo.test"], "una fuente sin permiso verificado se ignora y se dice")

    def test_every_default_feed_has_a_readable_name(self):
        from urllib.parse import urlparse
        for url in news.RSS_FEEDS:
            host = urlparse(url).netloc.replace("www.", "")
            self.assertIn(host, news.SOURCE_POLICY, f"falta el nombre legible de {host}")


if __name__ == "__main__":
    unittest.main()
