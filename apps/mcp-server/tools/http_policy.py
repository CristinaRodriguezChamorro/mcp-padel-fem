"""
http_policy.py — Política común para TODAS las peticiones salientes del proyecto.

Objetivo: reducir el riesgo legal y ético del scraping.

  1. User-Agent HONESTO: el robot se identifica como robot, con una URL de
     información y, si se configura, un contacto. Nunca finge ser un navegador.
  2. robots.txt: antes de pedir una URL se comprueba que el sitio la permite
     para este robot. Si no la permite, NO se pide.
  3. Ritmo educado: como mínimo MIN_INTERVAL_SECONDS entre peticiones al mismo
     dominio, o el Crawl-delay que declare el sitio (con un tope).

Variables de entorno (opcionales):
  SCRAPER_CONTACT       email o URL de contacto que se añade al User-Agent
  SCRAPER_INFO_URL      URL informativa del proyecto
  SCRAPER_USER_AGENT    sustituye el User-Agent completo
  SCRAPER_MIN_INTERVAL  segundos mínimos entre peticiones al mismo dominio

Reglas de robots.txt aplicadas (RFC 9309):
  - 200            -> se respetan sus reglas
  - 4xx            -> no hay restricciones declaradas
  - 5xx / error    -> se asume prohibido, y se reintenta a los pocos minutos
"""

import asyncio
import os
import time
from urllib import robotparser
from urllib.parse import urlparse

import aiohttp

BOT_NAME = "PadelFemBot"

_CONTACT = os.environ.get("SCRAPER_CONTACT", "").strip()
# `or` (y no solo el valor por defecto de get): docker-compose pasa "" si la variable no se define.
_INFO_URL = (os.environ.get("SCRAPER_INFO_URL") or "https://github.com/CristinaRodriguezChamorro/mcp-padel-fem").strip()

USER_AGENT = os.environ.get("SCRAPER_USER_AGENT", "").strip() or (
    f"{BOT_NAME}/1.0 (+{_INFO_URL}" + (f"; {_CONTACT}" if _CONTACT else "") + ")"
)

ROBOTS_TTL_OK = 24 * 60 * 60      # robots.txt válido: se recuerda 24 h
ROBOTS_TTL_ERROR = 5 * 60         # robots.txt inaccesible: se reintenta a los 5 min
MAX_CRAWL_DELAY = 10.0            # tope al Crawl-delay que declare un sitio

try:
    MIN_INTERVAL_SECONDS = float(os.environ.get("SCRAPER_MIN_INTERVAL", "1.0"))
except ValueError:
    MIN_INTERVAL_SECONDS = 1.0

# origin -> (caduca_en, parser | None, crawl_delay | None)
# parser None significa "prohibido por precaución".
_robots_cache: dict[str, tuple[float, robotparser.RobotFileParser | None, float | None]] = {}
_host_locks: dict[str, asyncio.Lock] = {}
_host_last: dict[str, float] = {}


def _origin(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


async def _load_robots(origin: str):
    now = time.time()
    cached = _robots_cache.get(origin)
    if cached and cached[0] > now:
        return cached

    entry = None
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            async with session.get(
                origin + "/robots.txt",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                status = resp.status
                if status == 200:
                    text = await resp.text(errors="ignore")
                    rp = robotparser.RobotFileParser()
                    rp.parse(text.splitlines())
                    delay = rp.crawl_delay(BOT_NAME)
                    entry = (now + ROBOTS_TTL_OK, rp, float(delay) if delay else None)
                elif 400 <= status < 500:
                    rp = robotparser.RobotFileParser()
                    rp.parse([])
                    entry = (now + ROBOTS_TTL_OK, rp, None)
                else:
                    print(f"  robots.txt {origin}: HTTP {status}, se asume prohibido")
                    entry = (now + ROBOTS_TTL_ERROR, None, None)
    except Exception as exc:
        print(f"  robots.txt {origin}: {type(exc).__name__}, se asume prohibido")
        entry = (now + ROBOTS_TTL_ERROR, None, None)

    _robots_cache[origin] = entry
    return entry


async def can_fetch(url: str) -> bool:
    """True si el robots.txt del dominio permite a este robot pedir la URL."""
    origin = _origin(url)
    if not origin:
        return False
    _, rp, _ = await _load_robots(origin)
    if rp is None:
        return False
    return rp.can_fetch(BOT_NAME, url)


async def wait_turn(url: str) -> None:
    """Espera lo necesario para no pedir al mismo dominio demasiado seguido."""
    origin = _origin(url)
    if not origin:
        return
    host = urlparse(url).netloc
    _, _, delay = _robots_cache.get(origin, (0, None, None))
    interval = max(MIN_INTERVAL_SECONDS, min(delay or 0.0, MAX_CRAWL_DELAY))

    lock = _host_locks.setdefault(host, asyncio.Lock())
    async with lock:
        elapsed = time.monotonic() - _host_last.get(host, 0.0)
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        _host_last[host] = time.monotonic()


async def check_and_wait(url: str) -> bool:
    """
    Punto de entrada habitual: devuelve False si robots.txt no permite la URL
    (y entonces NO hay que pedirla); si la permite, espera el turno y devuelve True.
    """
    if not await can_fetch(url):
        print(f"  bloqueado por robots.txt (o inaccesible): {url}")
        return False
    await wait_turn(url)
    return True
