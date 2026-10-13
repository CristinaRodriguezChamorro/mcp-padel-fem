"""
Pruebas del crédito de las fotos (sin internet): Wikimedia se simula con httpx.MockTransport.
La regla que se prueba: ante la duda, sin foto; nunca una foto sin su autor y su licencia.
"""
import asyncio
import os
import sys
import unittest

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import photos

FILE = "File:Gemma Triay 2024.jpg"


def run(coro):
    return asyncio.run(coro)


def meta(artist='<a href="//commons.wikimedia.org/wiki/User:Foo" title="User:Foo">Foo Bar</a>', licence="CC BY-SA 4.0",
         url="https://creativecommons.org/licenses/by-sa/4.0", nonfree=None):
    m = {"Artist": artist, "LicenseShortName": licence, "LicenseUrl": url}
    if nonfree is not None:
        m["NonFree"] = nonfree
    return m


def api_json(extmetadata, missing=False):
    page = {"title": FILE, "missing": True} if missing else {
        "pageid": 1, "title": FILE, "imageinfo": [{"extmetadata": {k: {"value": v, "source": "commons-desc-page"} for k, v in extmetadata.items()}}]}
    return {"query": {"pages": [page]}}


class LicenceRules(unittest.TestCase):
    def test_accepted_and_rejected_licences(self):
        for ok in ("CC BY-SA 4.0", "CC BY 2.0", "CC BY-SA 3.0", "CC BY 4.0", "CC BY-SA 2.5 es", "CC0", "CC0 1.0",
                   "Public domain", "public domain", "PD", "cc by-sa 4.0"):
            self.assertTrue(photos.license_ok(ok), ok)
        for bad in ("", "All rights reserved", "CC BY-NC 4.0", "CC BY-ND 2.0", "CC BY-NC-SA 3.0", "Fair use", "GFDL",
                    "Copyrighted free use", "CC BY-SA", "Attribution", "CC BY-SA 4.0 <script>"):
            self.assertFalse(photos.license_ok(bad), bad)

    def test_only_cc0_and_public_domain_do_not_need_an_author(self):
        for free in ("CC0", "CC0 1.0", "Public domain", "PD"):
            self.assertFalse(photos.needs_author(free), free)
        for attr in ("CC BY 4.0", "CC BY-SA 4.0"):
            self.assertTrue(photos.needs_author(attr), attr)


class CreditFromMetadata(unittest.TestCase):
    def test_a_normal_credit_strips_html_and_keeps_author_licence_and_page(self):
        credit, why = photos.credit_from_metadata(meta(), FILE)
        self.assertEqual(why, "")
        self.assertEqual(credit["autor"], "Foo Bar")
        self.assertEqual(credit["licencia"], "CC BY-SA 4.0")
        self.assertEqual(credit["licencia_url"], "https://creativecommons.org/licenses/by-sa/4.0")
        self.assertEqual(credit["pagina"], "https://commons.wikimedia.org/wiki/File:Gemma_Triay_2024.jpg")
        self.assertEqual(credit["archivo"], FILE)

    def test_dangerous_content_never_survives(self):
        credit, _ = photos.credit_from_metadata(meta(artist='<script>alert(1)</script>Ana <img src=x onerror=alert(2)> López'), FILE)
        self.assertNotIn("<", credit["autor"])
        self.assertNotIn(">", credit["autor"])
        self.assertTrue(credit["autor"].endswith("López"))

    def test_html_entities_and_long_authors(self):
        credit, _ = photos.credit_from_metadata(meta(artist="Jos&eacute; &amp; Mar&iacute;a " + "x" * 400), FILE)
        self.assertTrue(credit["autor"].startswith("José & María"))
        self.assertLessEqual(len(credit["autor"]), 120)

    def test_a_licence_url_outside_creative_commons_is_dropped(self):
        credit, _ = photos.credit_from_metadata(meta(url="https://evil.example/license"), FILE)
        self.assertEqual(credit["licencia_url"], "")
        credit, _ = photos.credit_from_metadata(meta(url="javascript:alert(1)"), FILE)
        self.assertEqual(credit["licencia_url"], "")

    def test_refusals(self):
        cases = {
            "no libre": meta(nonfree="true"),
            "licencia no aceptada": meta(licence="CC BY-NC 4.0"),
            "sin licencia": meta(licence=""),
            "sin autor": meta(artist="  <br> "),
        }
        for label, m in cases.items():
            credit, why = photos.credit_from_metadata(m, FILE)
            self.assertIsNone(credit, label)
            self.assertTrue(why, label)

    def test_markup_inside_the_licence_name_is_cleaned_or_refused_never_trusted(self):
        credit, _ = photos.credit_from_metadata(meta(licence="CC BY-SA 4.0<script>"), FILE)    # queda un valor válido y limpio
        self.assertEqual(credit["licencia"], "CC BY-SA 4.0")
        credit, _ = photos.credit_from_metadata(meta(licence="CC BY-SA 4.0<script>alert(1)</script>"), FILE)
        self.assertIsNone(credit)                                                             # sobra texto: no coincide con ninguna licencia
        credit, _ = photos.credit_from_metadata(meta(licence="<b>Todos los derechos reservados</b>"), FILE)
        self.assertIsNone(credit)

    def test_public_domain_without_author_is_allowed(self):
        credit, why = photos.credit_from_metadata(meta(artist="", licence="Public domain", url=""), FILE)
        self.assertEqual(why, "")
        self.assertEqual(credit["autor"], "Dominio público")

    def test_page_urls_per_host_and_escaping(self):
        self.assertEqual(photos.page_url("File:A b.jpg", "es.wikipedia.org"), "https://es.wikipedia.org/wiki/File:A_b.jpg")
        self.assertEqual(photos.page_url("File:A b.jpg", "host.raro"), "https://commons.wikimedia.org/wiki/File:A_b.jpg")
        self.assertNotIn(" ", photos.page_url("File:Á é ñ?.jpg", "commons.wikimedia.org"))


class FetchCredit(unittest.TestCase):
    def factory(self, responder):
        calls = []

        def handler(request):
            calls.append(request)
            return responder(request)
        return (lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))), calls

    def test_a_good_answer_gives_a_credit_and_the_request_is_polite(self):
        factory, calls = self.factory(lambda r: httpx.Response(200, json=api_json(meta())))
        credit, why = run(photos.fetch_credit(FILE, factory))
        self.assertEqual((credit["autor"], why), ("Foo Bar", ""))
        self.assertEqual(len(calls), 1)
        r = calls[0]
        self.assertEqual((r.url.host, r.url.params["titles"], r.url.params["prop"]), ("commons.wikimedia.org", FILE, "imageinfo"))
        self.assertEqual(r.url.params["maxlag"], "5")
        self.assertTrue(r.headers["user-agent"].startswith("PadelFemBot/"))

    def test_if_commons_does_not_have_the_file_the_wikipedias_are_asked(self):
        def responder(request):
            if request.url.host == "commons.wikimedia.org":
                return httpx.Response(200, json=api_json({}, missing=True))
            if request.url.host == "es.wikipedia.org":
                return httpx.Response(200, json=api_json(meta()))
            return httpx.Response(500)
        factory, calls = self.factory(responder)
        credit, _ = run(photos.fetch_credit(FILE, factory))
        self.assertEqual([c.url.host for c in calls], ["commons.wikimedia.org", "es.wikipedia.org"])
        self.assertTrue(credit["pagina"].startswith("https://es.wikipedia.org/wiki/"))

    def test_every_failure_means_no_photo(self):
        cases = {
            "http": lambda r: httpx.Response(503),
            "no json": lambda r: httpx.Response(200, text="<html>"),
            "shape": lambda r: httpx.Response(200, json={"query": {}}),
            "empty pages": lambda r: httpx.Response(200, json={"query": {"pages": []}}),
            "missing": lambda r: httpx.Response(200, json=api_json({}, missing=True)),
            "bad licence": lambda r: httpx.Response(200, json=api_json(meta(licence="CC BY-NC 4.0"))),
            "no author": lambda r: httpx.Response(200, json=api_json(meta(artist=""))),
        }
        for label, responder in cases.items():
            factory, _ = self.factory(responder)
            credit, why = run(photos.fetch_credit(FILE, factory))
            self.assertIsNone(credit, label)
            self.assertTrue(why, label)

        def down(request):
            raise httpx.ConnectError("sin red")
        factory, _ = self.factory(down)
        credit, why = run(photos.fetch_credit(FILE, factory))
        self.assertIsNone(credit)
        self.assertIn("ConnectError", why)

    def test_a_dangerous_title_is_never_sent(self):
        factory, calls = self.factory(lambda r: httpx.Response(200, json=api_json(meta())))
        for bad in ("", "Gemma.jpg", "File:", "File:<script>.jpg", "File:a|b.jpg", "File:" + "x" * 300, None):
            credit, why = run(photos.fetch_credit(bad, factory))
            self.assertIsNone(credit, repr(bad))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
