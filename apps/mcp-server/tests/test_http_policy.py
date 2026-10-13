"""
Pruebas sin internet: levantan un servidor local que imita webs con distintos robots.txt.
Ejecutar desde apps/mcp-server:   python -m unittest discover -s tests -v
"""
import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aiohttp import web
import feedparser

from tools import http_policy as hp

ROBOTS = {
    "/site_a/robots.txt": (200, "User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n"),
    "/site_b/robots.txt": (404, ""),
    "/site_c/robots.txt": (500, "boom"),
    "/site_d/robots.txt": (200, "User-agent: PadelFemBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"),
}
SEEN_UA = []


async def _handler(request):
    SEEN_UA.append(request.headers.get("User-Agent", ""))
    status, body = ROBOTS.get(request.path, (200, "ok"))
    return web.Response(status=status, text=body)


class HttpPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", _handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.port = self.site._server.sockets[0].getsockname()[1]
        hp._robots_cache.clear(); hp._host_last.clear()
        SEEN_UA.clear()

    async def asyncTearDown(self):
        await self.runner.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    # Cada "sitio" usa su propio robots.txt; como el origen es el mismo host:puerto,
    # se prueba cambiando la caché a mano con el contenido de cada caso.
    async def _with_robots(self, key):
        status, body = ROBOTS[key]
        ROBOTS["/robots.txt"] = (status, body)
        hp._robots_cache.clear()

    async def test_user_agent_is_honest(self):
        await self._with_robots("/site_b/robots.txt")
        await hp.can_fetch(self.url("/x"))
        self.assertTrue(SEEN_UA)
        for ua in SEEN_UA:
            self.assertTrue(ua.startswith("PadelFemBot/"), ua)
            self.assertNotIn("Mozilla", ua)

    async def test_disallowed_path_is_blocked_and_allowed_path_is_not(self):
        await self._with_robots("/site_a/robots.txt")
        self.assertFalse(await hp.can_fetch(self.url("/private/page")))
        self.assertTrue(await hp.can_fetch(self.url("/public/page")))

    async def test_missing_robots_means_allowed(self):
        await self._with_robots("/site_b/robots.txt")
        self.assertTrue(await hp.can_fetch(self.url("/anything")))

    async def test_server_error_means_blocked(self):
        await self._with_robots("/site_c/robots.txt")
        self.assertFalse(await hp.can_fetch(self.url("/anything")))

    async def test_rule_addressed_to_our_bot_is_respected(self):
        await self._with_robots("/site_d/robots.txt")
        self.assertFalse(await hp.can_fetch(self.url("/anything")))

    async def test_non_http_urls_are_refused(self):
        self.assertFalse(await hp.can_fetch("file:///etc/passwd"))
        self.assertFalse(await hp.can_fetch("not a url"))

    async def test_rate_limit_spaces_requests(self):
        await self._with_robots("/site_b/robots.txt")
        hp.MIN_INTERVAL_SECONDS, old = 0.4, hp.MIN_INTERVAL_SECONDS
        try:
            t0 = time.monotonic()
            for _ in range(3):
                self.assertTrue(await hp.check_and_wait(self.url("/p")))
            self.assertGreaterEqual(time.monotonic() - t0, 0.75)
        finally:
            hp.MIN_INTERVAL_SECONDS = old

    async def test_crawl_delay_is_honoured(self):
        await self._with_robots("/site_a/robots.txt")
        hp.MIN_INTERVAL_SECONDS, old = 0.1, hp.MIN_INTERVAL_SECONDS
        try:
            await hp.check_and_wait(self.url("/public/1"))
            t0 = time.monotonic()
            await hp.check_and_wait(self.url("/public/2"))
            self.assertGreaterEqual(time.monotonic() - t0, 1.8)   # Crawl-delay: 2
        finally:
            hp.MIN_INTERVAL_SECONDS = old


class NewsPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_news_uses_only_rss_and_never_downloads_articles(self):
        os.environ.pop("GROQ_API_KEY", None)
        from tools import news

        rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
        <item><title>Gemma Triay gana el torneo femenino de pádel</title>
        <link>https://medio.example/gemma-triay</link>
        <pubDate>Mon, 28 Sep 2026 10:15:00 +0000</pubDate>
        <description>La jugadora Gemma Triay se impuso en la final femenina del circuito.
        Fue un partido muy igualado en Madrid.</description></item></channel></rss>""".encode("utf-8")

        calls = {"article_requests": 0}

        async def fake_feed(url):
            return feedparser.parse(rss)

        async def fake_names():
            return ["Gemma Triay", "Delfina Brea"]

        async def spy_check(url):
            calls["article_requests"] += 1
            return True

        for attr in ("_fetch_feed", "get_top_player_names", "check_and_wait"):
            self.addCleanup(setattr, news, attr, getattr(news, attr))   # no contaminar otros tests
        news._fetch_feed = fake_feed
        news.get_top_player_names = fake_names
        news.check_and_wait = spy_check          # si scrape() llamara a la red, pasaría por aquí

        result = await news.get_latest_news()
        self.assertEqual(calls["article_requests"], 0, "no se debe pedir ninguna página de artículo")
        self.assertIn("resumen_diario", result)
        self.assertGreaterEqual(len(result["resumen_diario"]), 1)
        self.assertFalse(hasattr(news, "scrape"), "ya no debe existir código que descargue artículos")


if __name__ == "__main__":
    unittest.main()
