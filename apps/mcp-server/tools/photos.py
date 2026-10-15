"""
photos.py — Autor y licencia de las fotos de Wikimedia.

Las fotos de Wikipedia/Commons son de licencia libre, pero casi todas (CC BY, CC BY-SA) exigen
citar al autor y la licencia. Este módulo averigua ambas cosas para un archivo y decide si la
foto se puede mostrar:

  - solo se aceptan CC0, dominio público, CC BY y CC BY-SA;
  - una licencia que exige autor y no lo tiene -> NO se muestra;
  - si Wikimedia no responde o la respuesta no se entiende -> NO se muestra;
  - todo texto que viene de Wikimedia se limpia (cualquiera puede editar esas páginas).

Es decir: ante la duda, sin foto. Nunca una foto sin su crédito.
"""

import html
import re
from typing import Callable, Optional
from urllib.parse import quote, urlparse

import httpx

from tools.http_policy import USER_AGENT
from tools.wikipedia_source import clean_text

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# El archivo puede estar en Commons o, más raramente, solo en una Wikipedia.
WIKI_APIS = (COMMONS_API, "https://es.wikipedia.org/w/api.php", "https://en.wikipedia.org/w/api.php")
_PAGE_BASE = {
    "commons.wikimedia.org": "https://commons.wikimedia.org/wiki/",
    "es.wikipedia.org": "https://es.wikipedia.org/wiki/",
    "en.wikipedia.org": "https://en.wikipedia.org/wiki/",
}

_FILE_TITLE = re.compile(r"^File:[^|<>\[\]{}#\x00-\x1f]{3,200}$")
_ATTRIBUTION_FREE = re.compile(r"^(?:CC0|public domain|PD)\b", re.IGNORECASE)
_ACCEPTED = (
    _ATTRIBUTION_FREE,
    re.compile(r"^CC[ -]BY(?:[ -]SA)?[ -]\d(?:\.\d)?(?:[ -][A-Za-z]{2,3})?$", re.IGNORECASE),
)
_SAFE_LICENSE = re.compile(r"^[A-Za-z0-9 .\-]{2,40}$")


def license_ok(short_name: str) -> bool:
    return any(p.match((short_name or "").strip()) for p in _ACCEPTED)


def needs_author(short_name: str) -> bool:
    return not _ATTRIBUTION_FREE.match((short_name or "").strip())


def _plain(value, limit: int = 120) -> str:
    """HTML de Wikimedia -> texto en una línea, sin etiquetas y con longitud acotada."""
    text = re.sub(r"<[^>]*>", " ", str(value or ""))
    text = clean_text(html.unescape(text)).replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip(" ,;·-")
    return text[:limit].rstrip()


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes")


def page_url(file_title: str, host: str) -> str:
    base = _PAGE_BASE.get(host, _PAGE_BASE["commons.wikimedia.org"])
    return base + quote(file_title.replace(" ", "_"), safe=":_()'-.,")


def credit_from_metadata(meta: dict, file_title: str, host: str = "commons.wikimedia.org"):
    """
    meta: {'Artist': html, 'LicenseShortName': texto, 'LicenseUrl': url, 'NonFree': 'true'?}
    Devuelve (crédito, "") si la foto se puede mostrar, o (None, motivo).
    """
    if _truthy(meta.get("NonFree")):
        return None, "imagen no libre"
    licence = _plain(meta.get("LicenseShortName"), 40)
    if not licence or not license_ok(licence) or not _SAFE_LICENSE.match(licence):
        return None, f"licencia no aceptada ({licence or 'sin indicar'})"
    author = _plain(meta.get("Artist"), 120)
    if not author and needs_author(licence):
        return None, "sin autor"
    licence_url = str(meta.get("LicenseUrl") or "").strip()
    if not licence_url.startswith("https://creativecommons.org/"):
        licence_url = ""
    return {
        "autor": author or "Dominio público",
        "licencia": licence,
        "licencia_url": licence_url,
        "pagina": page_url(file_title, host),
        "archivo": file_title,
    }, ""


async def fetch_credit(file_title: str, client_factory: Optional[Callable[[], httpx.AsyncClient]] = None):
    """Pregunta a Wikimedia por el autor y la licencia del archivo. Devuelve (crédito | None, motivo)."""
    if not _FILE_TITLE.match(file_title or ""):
        return None, "título de archivo no válido"
    factory = client_factory or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(12.0)))
    reason = "archivo no encontrado"
    async with factory() as client:
        for api in WIKI_APIS:
            try:
                resp = await client.get(api, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, params={
                    "action": "query", "titles": file_title, "prop": "imageinfo", "iiprop": "extmetadata",
                    "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|NonFree",
                    "format": "json", "formatversion": "2", "maxlag": "5",
                })
            except Exception as exc:  # noqa: BLE001
                reason = f"error de red ({type(exc).__name__})"
                continue
            if resp.status_code != 200:
                reason = f"HTTP {resp.status_code}"
                continue
            try:
                pages = resp.json()["query"]["pages"]
                page = pages[0] if isinstance(pages, list) and pages else None
            except (ValueError, KeyError, TypeError):
                reason = "respuesta inesperada"
                continue
            if not isinstance(page, dict) or page.get("missing") or not page.get("imageinfo"):
                reason = "archivo no encontrado"
                continue
            raw = ((page["imageinfo"] or [{}])[0] or {}).get("extmetadata") or {}
            flat = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in raw.items()}
            return credit_from_metadata(flat, file_title, urlparse(api).netloc)
    return None, reason
