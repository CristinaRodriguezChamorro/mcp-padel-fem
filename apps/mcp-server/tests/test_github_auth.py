"""
Pruebas del acceso con token de GitHub, sin internet: GitHub se simula con httpx.MockTransport.
Incluye una conexión completa con un cliente MCP real que envía el token como VS Code.
"""
import asyncio
import contextlib
import io
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["MCP_RATE_PER_MIN"] = "100000"
os.environ["MCP_GLOBAL_RATE_PER_MIN"] = "100000"

import httpx
import uvicorn
from fastapi import FastAPI
from fastmcp import Client

from padel_mcp.auth import GithubTokenVerifier, parse_allowed_users
from padel_mcp.tools import McpGate, build_mcp, gate_from_env

TOKEN_A = "ghp_" + "a" * 36            # cuenta "cris"
TOKEN_B = "github_pat_" + "B" * 40     # cuenta "intrusa"
TOKEN_BAD = "ghp_" + "z" * 36          # GitHub lo rechaza
ACCOUNTS = {TOKEN_A: "cris", TOKEN_B: "Intrusa"}


class FakeGitHub:
    """Imita a api.github.com/user y cuenta las llamadas."""
    def __init__(self, mode="normal"):
        self.calls = 0
        self.mode = mode
        self.seen_auth = []

    def factory(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            self.seen_auth.append(request.headers.get("authorization"))
            if self.mode == "down":
                raise httpx.ConnectError("sin conexión")
            if self.mode == "rate-limited":
                return httpx.Response(403, json={"message": "rate limit"})
            if self.mode == "server-error":
                return httpx.Response(500)
            token = (request.headers.get("authorization") or "").removeprefix("Bearer ")
            if token in ACCOUNTS:
                return httpx.Response(200, json={"login": ACCOUNTS[token], "id": 1})
            return httpx.Response(401, json={"message": "Bad credentials"})
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def verifier(gh, **kw):
    return GithubTokenVerifier(client_factory=gh.factory(), **kw)


class VerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_invalid_and_malformed_tokens(self):
        gh = FakeGitHub()
        v = verifier(gh)
        self.assertEqual(await v.verify(TOKEN_A), ("cris", "ok"))
        self.assertEqual(await v.verify(TOKEN_BAD), (None, "invalid"))
        calls = gh.calls
        for junk in ("", "corto", "ghp_ con espacios " + "a" * 30, "ghp_" + "ñ" * 30, "a" * 400, None, 123):
            self.assertEqual(await v.verify(junk), (None, "invalid"), repr(junk))
        self.assertEqual(gh.calls, calls, "un token mal formado no debe llegar a GitHub")

    async def test_the_token_is_sent_to_github_only_as_a_bearer_header(self):
        gh = FakeGitHub()
        await verifier(gh).verify(TOKEN_A)
        self.assertEqual(gh.seen_auth, [f"Bearer {TOKEN_A}"])

    async def test_valid_and_invalid_results_are_cached(self):
        gh = FakeGitHub()
        v = verifier(gh)
        for _ in range(5):
            self.assertEqual((await v.verify(TOKEN_A))[1], "ok")
        self.assertEqual(gh.calls, 1)
        for _ in range(5):
            self.assertEqual((await v.verify(TOKEN_BAD))[1], "invalid")
        self.assertEqual(gh.calls, 2)

    async def test_cache_expires(self):
        gh = FakeGitHub()
        v = verifier(gh, ttl_ok=0.05)
        await v.verify(TOKEN_A); await asyncio.sleep(0.1); await v.verify(TOKEN_A)
        self.assertEqual(gh.calls, 2)

    async def test_the_cache_never_holds_the_token_itself(self):
        v = verifier(FakeGitHub())
        await v.verify(TOKEN_A)
        self.assertNotIn(TOKEN_A, repr(v._cache))
        self.assertNotIn(TOKEN_A, repr(v.__dict__))

    async def test_failures_on_github_side_are_unavailable_and_not_cached(self):
        for mode in ("down", "rate-limited", "server-error"):
            gh = FakeGitHub(mode)
            v = verifier(gh)
            self.assertEqual(await v.verify(TOKEN_A), (None, "unavailable"), mode)
            await v.verify(TOKEN_A)
            self.assertEqual(gh.calls, 2, f"{mode}: un fallo de GitHub no debe quedar en caché")

    async def test_allowlist_is_case_insensitive(self):
        v = verifier(FakeGitHub(), allowed_users=parse_allowed_users(" Cris , otra "))
        self.assertEqual(await v.verify(TOKEN_A), ("cris", "ok"))
        self.assertEqual(await v.verify(TOKEN_B), (None, "forbidden"))
        self.assertEqual(parse_allowed_users(""), set())

    async def test_the_number_of_checks_against_github_is_capped(self):
        gh = FakeGitHub()
        v = verifier(gh, max_checks_per_min=3)
        results = [(await v.verify("ghp_" + f"{i:036d}"))[1] for i in range(6)]
        self.assertEqual(results, ["invalid"] * 3 + ["unavailable"] * 3)
        self.assertEqual(gh.calls, 3)
        self.assertEqual((await v.verify(TOKEN_BAD))[1], "unavailable")   # sin presupuesto, y sin caché previa

    async def test_cache_size_is_bounded(self):
        v = verifier(FakeGitHub(), max_cache_entries=5, max_checks_per_min=1000)
        for i in range(30):
            await v.verify("ghp_" + f"{i:036d}")
        self.assertLessEqual(len(v._cache), 5)

    async def test_nothing_secret_is_printed_when_things_fail(self):
        for mode in ("down", "rate-limited"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                await verifier(FakeGitHub(mode)).verify(TOKEN_A)
            self.assertNotIn(TOKEN_A, buf.getvalue())
            self.assertNotIn("a" * 20, buf.getvalue())


class GateWithGitHubTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def inner():
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        return app

    async def call(self, gate, auth=None, ip="1.2.3.4"):
        sent = []
        async def send(msg): sent.append(msg)
        async def receive(): return {"type": "http.request", "body": b""}
        headers = [(b"authorization", auth.encode())] if auth is not None else []
        scope = {"type": "http", "headers": headers, "client": (ip, 1)}
        await gate(scope, receive, send)
        return sent[0]["status"], dict(sent[0]["headers"]), scope

    def gate(self, gh=None, **vkw):
        gh = gh or FakeGitHub()
        gk = {k: vkw.pop(k) for k in list(vkw) if k in ("per_ip", "global_limit", "per_user")}
        return McpGate(self.inner(), github=verifier(gh, **vkw), **gk), gh

    async def test_statuses(self):
        gate, _ = self.gate(allowed_users={"cris"})
        self.assertEqual((await self.call(gate))[0], 401)                                  # sin token
        self.assertEqual((await self.call(gate, "Basic abc"))[0], 401)                      # otro esquema
        s, h, _ = await self.call(gate, f"Bearer {TOKEN_BAD}")
        self.assertEqual(s, 401)
        self.assertIn(b'error="invalid_token"', h[b"www-authenticate"])
        self.assertEqual((await self.call(gate, f"Bearer {TOKEN_B}"))[0], 403)              # válido, no permitido
        s, _, scope = await self.call(gate, f"Bearer {TOKEN_A}")
        self.assertEqual(s, 200)
        self.assertEqual(scope["padel_user"], "cris")

    async def test_scheme_is_case_insensitive(self):
        gate, _ = self.gate()
        self.assertEqual((await self.call(gate, f"bearer {TOKEN_A}"))[0], 200)
        self.assertEqual((await self.call(gate, f"BEARER   {TOKEN_A}  "))[0], 200)

    async def test_github_down_gives_503_with_retry(self):
        gate, _ = self.gate(FakeGitHub("down"))
        s, h, _ = await self.call(gate, f"Bearer {TOKEN_A}")
        self.assertEqual(s, 503)
        self.assertEqual(h[b"retry-after"], b"30")

    async def test_static_token_is_ignored_in_github_mode(self):
        gh = FakeGitHub()
        gate = McpGate(self.inner(), token="secreto", github=verifier(gh))
        self.assertEqual((await self.call(gate, "Bearer secreto"))[0], 401)

    async def test_rate_limit_is_per_account_not_per_ip(self):
        gate, _ = self.gate(per_user=3, per_ip=1000)
        a = [(await self.call(gate, f"Bearer {TOKEN_A}", ip=f"10.0.0.{i}"))[0] for i in range(5)]
        self.assertEqual(a, [200, 200, 200, 429, 429])                  # misma cuenta, IPs distintas
        self.assertEqual((await self.call(gate, f"Bearer {TOKEN_B}", ip="10.0.0.9"))[0], 200)   # otra cuenta

    async def test_forged_tokens_from_one_ip_are_throttled_before_reaching_github(self):
        gh = FakeGitHub()
        gate, _ = self.gate(gh, per_ip=5)
        codes = [(await self.call(gate, "Bearer ghp_" + f"{i:036d}", ip="6.6.6.6"))[0] for i in range(40)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(set(codes[5:]), {429})
        self.assertEqual(gh.calls, 5, "GitHub solo debe haber visto los intentos que pasaron el límite por IP")

    async def test_valid_token_does_not_call_github_every_time(self):
        gate, gh = self.gate()
        for _ in range(20):
            await self.call(gate, f"Bearer {TOKEN_A}")
        self.assertEqual(gh.calls, 1)

    async def test_non_http_scopes_pass_through(self):
        reached = []
        async def app(scope, receive, send): reached.append(scope["type"])
        gate = McpGate(app, github=verifier(FakeGitHub()))
        await gate({"type": "lifespan"}, None, None)
        self.assertEqual(reached, ["lifespan"])


class GateFromEnvTests(unittest.TestCase):
    KEYS = ("MCP_AUTH", "MCP_TOKEN", "MCP_GITHUB_USERS")

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def gate(self, **env):
        os.environ.update(env)
        with contextlib.redirect_stdout(io.StringIO()):
            return gate_from_env(lambda *a: None)

    def test_modes(self):
        self.assertIsNone(self.gate().github)
        self.assertEqual(self.gate().token, "")                                      # abierto
        self.assertEqual(self.gate(MCP_TOKEN="t0k").token, "t0k")                    # auto -> token
        g = self.gate(MCP_AUTH="GitHub", MCP_GITHUB_USERS="Cris, otra")
        self.assertEqual(g.github.allowed, {"cris", "otra"})
        self.assertEqual(g.token, "")
        self.assertEqual(self.gate(MCP_AUTH="github", MCP_GITHUB_USERS="").github.allowed, set())

    def test_it_fails_closed_instead_of_opening_by_mistake(self):
        for env in ({"MCP_AUTH": "gtihub"},                       # errata
                    {"MCP_AUTH": "token"},                        # token sin MCP_TOKEN
                    {"MCP_AUTH": "open", "MCP_TOKEN": "x"},       # contradictorio
                    {"MCP_AUTH": "1"}):
            for k in self.KEYS:
                os.environ.pop(k, None)
            with self.assertRaises(ValueError, msg=str(env)):
                self.gate(**env)


class EndToEnd(unittest.IsolatedAsyncioTestCase):
    """Un cliente MCP real con el token en la cabecera, como hace VS Code desde mcp.json."""

    @classmethod
    def setUpClass(cls):
        async def ranking(): return {"data": [{"pos": 1, "name": "Gemma Triay", "pair": "", "flag": "", "pts": "17.840"}],
                                     "age_s": 0, "fallback": None, "tipo": "manual"}
        async def empty(): return {"data": [], "age_s": 0, "fallback": None}

        cls.gh = FakeGitHub()
        mcp = build_mcp({"ranking": ranking, "calendar": empty, "live": empty, "news": empty})
        mcp_app = mcp.http_app(path="/mcp", stateless_http=True)
        app = FastAPI(lifespan=mcp_app.lifespan)
        app.mount("/ai", McpGate(mcp_app, github=verifier(cls.gh, allowed_users={"cris"})))

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        cls.url = f"http://127.0.0.1:{cls.port}/ai/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)

    async def test_allowed_account_can_list_and_call_tools(self):
        async with Client(self.url, auth=TOKEN_A) as c:
            names = {t.name for t in await c.list_tools()}
            self.assertIn("ranking_femenino", names)
            res = await c.call_tool("ranking_femenino", {"top": 1})
            data = res.data if res.data is not None else res.structured_content
            self.assertEqual(data["ranking"][0]["nombre"], "Gemma Triay")

    async def test_everyone_else_is_refused(self):
        for label, token in (("sin token", None), ("token falso", TOKEN_BAD), ("cuenta no permitida", TOKEN_B)):
            with self.assertRaises(Exception, msg=label):
                async with Client(self.url, auth=token) as c:
                    await c.list_tools()

    async def test_the_raw_http_answers_match_what_vscode_would_see(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "1"}}}
        hdr = {"Accept": "application/json, text/event-stream"}
        async with httpx.AsyncClient() as h:
            self.assertEqual((await h.post(self.url, json=body, headers=hdr)).status_code, 401)
            self.assertEqual((await h.post(self.url, json=body, headers={**hdr, "Authorization": f"Bearer {TOKEN_B}"})).status_code, 403)
            self.assertEqual((await h.post(self.url, json=body, headers={**hdr, "Authorization": f"Bearer {TOKEN_A}"})).status_code, 200)


if __name__ == "__main__":
    unittest.main()
