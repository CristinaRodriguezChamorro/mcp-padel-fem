"""
El README no puede mentir. Este test lo compara con el código:
  - las herramientas del MCP: las que lista son exactamente las que existen;
  - las variables de entorno: todas las que lee el código están documentadas (con su valor por defecto) y
    todas las documentadas existen;
  - las rutas de la web, los archivos que menciona y el árbol de carpetas;
  - cifras clave (límites, tiempos, caducidades) y nada de lo que ya se quitó.
"""
import asyncio
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MCP_TOOLS", "extended")
os.environ["WIKIPEDIA_INGEST"] = "off"

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent.parent
README = (REPO / "README.md").read_text(encoding="utf-8")


def section(title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", README, re.S | re.M)
    assert m, f"el README no tiene la sección '## {title}'"
    return m.group(1)


def code_files():
    return [p for p in list(APP.glob("*.py")) + list((APP / "padel_mcp").glob("*.py")) + list((APP / "tools").glob("*.py"))]


class Tools(unittest.TestCase):
    def test_the_tables_list_exactly_the_real_tools(self):
        from padel_mcp.tools import CORE_TOOLS, EXTRA_TOOLS
        sec = section("Las herramientas del MCP")
        self.assertEqual(set(re.findall(r"^\| `([a-z_]+)` \|", sec, re.M)), set(CORE_TOOLS) | set(EXTRA_TOOLS))

    def test_core_and_extra_are_in_the_right_table(self):
        from padel_mcp.tools import CORE_TOOLS, EXTRA_TOOLS
        sec = section("Las herramientas del MCP")
        core_part, extra_part = sec.split("MCP_TOOLS=extended", 1)
        self.assertEqual(set(re.findall(r"^\| `([a-z_]+)` \|", core_part, re.M)), set(CORE_TOOLS))
        self.assertEqual(set(re.findall(r"^\| `([a-z_]+)` \|", extra_part, re.M)), set(EXTRA_TOOLS))

    def test_the_default_top_is_the_real_one(self):
        import inspect
        from padel_mcp import tools
        self.assertIn("top: int = 10", inspect.getsource(tools.build_mcp))
        self.assertIn("top 10", section("Las herramientas del MCP"))


class ToolParameters(unittest.TestCase):
    """La columna «Parámetros» del README es la de verdad: nombres, si son opcionales y su valor por defecto."""

    @staticmethod
    def real():
        from fastmcp import Client
        from padel_mcp.tools import build_mcp

        async def nothing():
            return {"data": [], "age_s": 0, "fallback": None}
        providers = {k: nothing for k in ("ranking", "calendar", "live", "finals", "news", "status")}

        async def go():
            async with Client(build_mcp(providers, extended=True)) as c:
                return {t.name: t.inputSchema for t in await c.list_tools()}
        return asyncio.run(go())

    @staticmethod
    def documented():
        out = {}
        for line in section("Las herramientas del MCP").splitlines():
            m = re.match(r"^\| `([a-z_]+)` \| ([^|]*) \|", line)
            if m:
                out[m.group(1)] = {t[0]: (t[1] == "?", t[2]) for t in re.findall(r"`(\w+)(\??)(?:=([^`]*))?`", m.group(2))}
        return out

    def test_every_tool_documents_exactly_its_real_parameters(self):
        real, doc = self.real(), self.documented()
        self.assertEqual(set(doc), set(real))
        for name, schema in real.items():
            props, required = schema.get("properties", {}), set(schema.get("required", []))
            self.assertEqual(set(doc[name]), set(props), f"{name}: parámetros distintos a los del README")
            for param, spec in props.items():
                optional, default = doc[name][param]
                self.assertEqual(optional, param not in required, f"{name}.{param}: ¿opcional?")
                real_default = spec.get("default")
                if real_default is None:
                    self.assertEqual(default, "", f"{name}.{param}: el README da un valor por defecto que no existe")
                else:
                    self.assertEqual(default, str(real_default), f"{name}.{param}: valor por defecto")

    def test_a_tool_without_parameters_says_so(self):
        real = self.real()
        for name, schema in real.items():
            if not schema.get("properties"):
                self.assertRegex(section("Las herramientas del MCP"), rf"\| `{name}` \| — \|")

    def test_the_total_in_the_readme_is_the_real_one(self):
        from padel_mcp.tools import CORE_TOOLS, EXTRA_TOOLS
        text = section("Las herramientas del MCP")
        total = len(CORE_TOOLS) + len(EXTRA_TOOLS)
        self.assertIn(f"Hay **{total}**: {len(CORE_TOOLS)} por defecto y {len(EXTRA_TOOLS)} extra", text)
        for title, count in (("Por defecto", len(CORE_TOOLS)), ("Extra con datos guardados", 9), ("Extra que consultan a Padel API", 11)):
            self.assertRegex(text, rf"### {title}[^\n]*\({count}\)")
        self.assertEqual(9 + 11, len(EXTRA_TOOLS))

    def test_every_table_row_is_closed(self):
        for line in section("Las herramientas del MCP").splitlines():
            if line.startswith("| `"):
                self.assertTrue(line.rstrip().endswith("|"), f"fila de tabla sin cerrar: {line[:60]}")


class Environment(unittest.TestCase):
    @staticmethod
    def read_by_code():
        names = set()
        for p in code_files():
            text = p.read_text(encoding="utf-8")
            names |= set(re.findall(r'os\.environ\.get\(\s*"([A-Z][A-Z0-9_]+)"', text))
            names |= set(re.findall(r'_env_int\(\s*"([A-Z][A-Z0-9_]+)"', text))
        return names

    def rows(self):
        out = {}
        for line in section("Variables de entorno").splitlines():
            if line.startswith("|") and "`" in line:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                for name in re.findall(r"`([A-Z][A-Z0-9_]+)`", cells[0]):
                    out[name] = line
        return out

    def test_every_variable_the_code_reads_is_documented(self):
        missing = self.read_by_code() - set(self.rows())
        self.assertEqual(missing, set(), f"variables que el código lee y el README no documenta: {sorted(missing)}")

    def test_every_documented_variable_exists_in_the_code(self):
        source = "\n".join(p.read_text(encoding="utf-8") for p in code_files())
        compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
        for name in self.rows():
            self.assertTrue(name in source or name in compose, f"{name} está en el README pero no en el código")

    def test_documented_defaults_are_the_real_ones(self):
        source = "\n".join(p.read_text(encoding="utf-8") for p in code_files())
        rows = self.rows()
        for name, default in re.findall(r'_env_int\(\s*"([A-Z0-9_]+)",\s*(\d+)\)', source):
            self.assertIn(default, rows[name], f"{name}: el valor por defecto real es {default}")
        from tools import padelapi_source as pa
        self.assertIn(str(pa.DEFAULT_INTERVAL), rows["PADELAPI_MIN_INTERVAL"])
        from tools import padelapi_live as live
        self.assertIn(str(live.DEFAULT_PER_MINUTE), rows["PADELAPI_LIVE_PER_MIN"])
        self.assertIn(str(live.DEFAULT_PER_DAY), rows["PADELAPI_LIVE_PER_DAY"])
        self.assertIn("8000", rows["PORT"])
        self.assertIn("24", rows["WIKIPEDIA_INGEST_HOURS"])
        self.assertIn("15", rows["INGEST_START_DELAY"])
        self.assertIn("llama-3.1-8b-instant", rows["GROQ_NEWS_MODEL"])

    def test_the_compose_file_only_passes_documented_variables(self):
        compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
        for name in re.findall(r"^\s*-\s*([A-Z][A-Z0-9_]+)=", compose, re.M):
            self.assertIn(name, self.rows(), name)


class Routes(unittest.TestCase):
    DOCS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}

    def test_documented_routes_exist_and_every_route_is_documented(self):
        import server
        real = {r.path for r in server.app.routes if hasattr(r, "methods")}
        documented = set(re.findall(r"^GET (/[\w/\-]*)", section("Endpoints de la web"), re.M))
        self.assertEqual(documented, real - self.DOCS)
        for d in self.DOCS:
            self.assertIn(d, real)
        self.assertIn(d.split("/")[1], README)             # el README habla de las rutas de FastAPI (/docs...)

    def test_the_mcp_path_is_the_real_one(self):
        self.assertIn("/ai/mcp", README)
        server_source = (APP / "server.py").read_text(encoding="utf-8")
        self.assertIn('app.mount("/ai"', server_source)            # el MCP cuelga de /ai...
        self.assertIn('http_app(path="/mcp"', server_source)       # ...y su ruta interna es /mcp: juntas, /ai/mcp


class Files(unittest.TestCase):
    RUNTIME = {"data/_ingesta.json", "data/ranking_masculino.json", ".vscode/mcp.json"}   # los crea la app o tú

    def test_every_path_it_mentions_exists(self):
        paths = set(re.findall(r"`((?:apps|examples|data|tools|padel_mcp|static|tests)/[\w./\-]+\.(?:json|html|py|toml|yml|md))`", README))
        self.assertTrue(paths)
        for p in paths - self.RUNTIME:
            self.assertTrue((REPO / p).exists() or (APP / p).exists(), f"el README menciona {p} y no existe")

    def test_the_tree_lists_the_real_files(self):
        m = re.search(r"## Estructura del proyecto.*?```text\n(.*?)```", README, re.S)
        tree = m.group(1)
        listed = set(re.findall(r"[├└]── ([\w.\-]+\.(?:py|html|json|toml|yml|txt|md))\b", tree))
        existing = {p.name for p in REPO.rglob("*") if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
                    and ".venv" not in p.parts and "tests" not in p.parts}
        for name in listed:
            self.assertIn(name, existing, f"el árbol del README lista {name} y no existe")
        for folder in ("padel_mcp", "tools", "static", "data"):
            for f in (APP / folder).iterdir():
                if f.is_file() and f.name not in ("_ingesta.json",) and "__pycache__" not in f.parts:
                    self.assertIn(f.name, listed | {"__init__.py"}, f"{folder}/{f.name} existe y el árbol del README no lo lista")

    def test_it_does_not_talk_about_things_that_were_removed(self):
        for gone in ("live_data", "scraper.py", "Playwright", "playwright", "start.sh", "mcp_tools.py", "mcp_auth.py",
                     "padelspeak", "Chromium", "apps/web", "PLAYER_PHOTOS", "creditos-fotos", "player-photo", "photos.py",
                     "Wikimedia", "foto de"):
            self.assertNotIn(gone, README, gone)


class Facts(unittest.TestCase):
    def test_key_numbers_match_the_code(self):
        from tools import local_data, news, ingest, padelapi_source as pa
        self.assertIn(f"{local_data.STALE_DAYS} días", README)
        self.assertIn(f"{news.NEWS_CACHE_SECONDS // 60} minutos", README)
        self.assertIn(str(news.MAX_TOTAL), section("De dónde salen los datos"))
        self.assertIn(str(pa.MAX_FINAL_FETCHES * 2), README + str(pa.MAX_FINAL_FETCHES * 2))   # peticiones de la 1.ª pasada: orden de magnitud
        self.assertIn("6,5", README)
        self.assertEqual(ingest.MIN_RANKING_ROWS, 8)

    def test_news_policy_in_the_readme_is_the_real_one(self):
        from tools import news
        sec = section("De dónde salen los datos")
        for host, (name, mode) in news.SOURCE_POLICY.items():
            self.assertIn(name, sec, name)
        self.assertEqual({m for _, m in news.SOURCE_POLICY.values()}, {"resumen", "extracto"})
        for phrase in ("resumen", "extracto", "tres líneas", "fecha real", "enlace directo", f"{news.EXTRACT_CHARS} caracteres"):
            self.assertIn(phrase, sec, phrase)
        for left_out in ("Marca", "Mundo Deportivo", "Padel Spain", "Set Padel Magazine"):
            self.assertIn(left_out, sec, f"el README debe decir que {left_out} se dejó fuera")

    def test_the_three_mcp_auth_modes_are_explained(self):
        sec = section("Quién puede conectarse (`MCP_AUTH`)")
        for mode in ("github", "token", "open"):
            self.assertIn(f"`MCP_AUTH={mode}`" if mode != "token" else "`MCP_TOKEN", sec)

    def test_no_secret_looking_value(self):
        self.assertIsNone(re.search(r"(PADELAPI_TOKEN|MCP_TOKEN|GROQ_API_KEY)\s*[=:]\s*[\"']?(?![$<{]|tu[_-]|TU[_-]|\.\.\.|\s)[A-Za-z0-9_\-]{10,}", README))


if __name__ == "__main__":
    unittest.main()
