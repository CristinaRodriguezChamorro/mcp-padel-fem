"""
padel_mcp/auth.py — Acceso al MCP con un token de GitHub.

Quien se conecta manda su token de GitHub en la cabecera `Authorization: Bearer <token>`
(en VS Code, desde .vscode/mcp.json). El servidor pregunta a GitHub de quién es ese token
(GET https://api.github.com/user) y, si es válido, deja pasar a esa cuenta.

Para qué sirve y para qué NO sirve:
  - Sirve para que el MCP no sea anónimo: cada petición queda asociada a una cuenta de
    GitHub, con su propio límite de uso, y se puede restringir a una lista de cuentas.
  - El servidor NO guarda el token (solo una huella SHA-256 en memoria durante unos minutos),
    no lo escribe en los registros y solo se lo envía a api.github.com.
  - El token no necesita NINGÚN permiso: basta uno de los de "solo identidad".
    Aun así, un token es un secreto: quien administre este servidor podría leerlo en memoria.
    Usa tokens sin permisos y con caducidad corta.

Variables de entorno (las lee `gate_from_env` en padel_mcp/tools.py):
  MCP_AUTH=github         activa este modo
  MCP_GITHUB_USERS=a,b    cuentas permitidas; vacío = cualquier cuenta de GitHub válida
"""

import hashlib
import re
import time
from collections import deque
from typing import Callable, Optional

import httpx

GITHUB_API_USER = "https://api.github.com/user"

# Tokens de GitHub: ghp_..., github_pat_..., gho_..., ghu_... o los clásicos de 40 hex.
# Se rechaza todo lo que tenga otra pinta antes de gastar una petición a GitHub.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,300}$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")

OK, INVALID, FORBIDDEN, UNAVAILABLE = "ok", "invalid", "forbidden", "unavailable"


def parse_allowed_users(raw: str) -> set[str]:
    return {u.strip().lower() for u in (raw or "").split(",") if u.strip()}


class GithubTokenVerifier:
    """
    verify(token) -> (login | None, estado)

    estado:  "ok"           token válido (y cuenta permitida)
             "invalid"      token mal formado o rechazado por GitHub
             "forbidden"    token válido, pero la cuenta no está en la lista permitida
             "unavailable"  no se pudo comprobar (GitHub caído, límite de GitHub, demasiadas comprobaciones)
    """

    def __init__(
        self,
        allowed_users: Optional[set[str]] = None,
        ttl_ok: float = 300.0,
        ttl_invalid: float = 60.0,
        max_checks_per_min: int = 60,
        max_cache_entries: int = 2000,
        api_url: str = GITHUB_API_USER,
        client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
        user_agent: str = "PadelFemBot/1.0",
    ):
        self.allowed = {u.lower() for u in (allowed_users or set())}
        self.ttl_ok = ttl_ok
        self.ttl_invalid = ttl_invalid
        self.max_checks_per_min = max_checks_per_min
        self.max_cache_entries = max_cache_entries
        self.api_url = api_url
        self.user_agent = user_agent
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(5.0)))
        self._cache: dict[str, tuple[float, Optional[str]]] = {}
        self._checks: deque = deque()

    # ── caché (solo huellas, nunca el token) ──
    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _remember(self, key: str, login: Optional[str]) -> None:
        now = time.monotonic()
        ttl = self.ttl_ok if login else self.ttl_invalid
        self._cache[key] = (now + ttl, login)
        if len(self._cache) > self.max_cache_entries:
            for k in [k for k, (exp, _) in self._cache.items() if exp < now]:
                del self._cache[k]
            while len(self._cache) > self.max_cache_entries:
                del self._cache[min(self._cache, key=lambda k: self._cache[k][0])]

    def _check_budget_left(self) -> bool:
        now = time.monotonic()
        while self._checks and self._checks[0] < now - 60:
            self._checks.popleft()
        if len(self._checks) >= self.max_checks_per_min:
            return False
        self._checks.append(now)
        return True

    def _decide(self, login: str):
        if self.allowed and login.lower() not in self.allowed:
            return None, FORBIDDEN
        return login, OK

    async def verify(self, token: str):
        if not isinstance(token, str) or not _TOKEN_RE.match(token):
            return None, INVALID

        key = self._key(token)
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            return (None, INVALID) if cached[1] is None else self._decide(cached[1])

        # Un atacante podría probar miles de tokens falsos y hacer que GitHub bloquee la IP del
        # servidor: se limita el número de comprobaciones por minuto, pase lo que pase.
        if not self._check_budget_left():
            return None, UNAVAILABLE

        try:
            async with self._client_factory() as client:
                resp = await client.get(self.api_url, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": self.user_agent,
                })
        except Exception as exc:  # noqa: BLE001 — nunca se imprime el mensaje: podría contener datos de la petición
            print(f"  github auth: no se pudo contactar con GitHub ({type(exc).__name__})")
            return None, UNAVAILABLE

        if resp.status_code == 401:
            self._remember(key, None)
            return None, INVALID
        if resp.status_code != 200:
            # 403/429 (límite de GitHub), 5xx...: no es culpa del token, y no se guarda.
            print(f"  github auth: GitHub respondió HTTP {resp.status_code}")
            return None, UNAVAILABLE

        try:
            login = resp.json().get("login")
        except Exception:  # noqa: BLE001
            login = None
        if not isinstance(login, str) or not _LOGIN_RE.match(login):
            return None, UNAVAILABLE

        self._remember(key, login)
        return self._decide(login)
