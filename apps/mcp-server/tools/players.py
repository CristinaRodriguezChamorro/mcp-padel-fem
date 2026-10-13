"""
players.py — Reconoce jugadoras del Top 20 en un texto (noticias, resultados...).

Lo usan el pipeline de noticias y las tools del MCP, para que ambos entiendan
igual "esta noticia habla de X".

Fuerza de la mención:
  2 = nombre completo ("Gemma Triay")
  1 = solo apellido o inicial+apellido, y sin ambigüedad dentro del Top 20
  0 = no se menciona, o no se puede saber cuál de varias jugadoras con el mismo
      apellido es (p. ej. "Sánchez" con dos Sánchez en el Top 20)
"""

import re
import unicodedata


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def players_from_names(names) -> list:
    """['Gemma Triay', ...] -> [{'name': 'Gemma Triay'}, ...]"""
    return [{"name": n} for n in names if n]


def find_players(players: list, query: str) -> list:
    """Jugadoras del ranking que encajan con el texto buscado (nombre completo, o todas sus palabras)."""
    q = norm(query)
    if not q:
        return []
    exact = [p for p in players if norm(p.get("name", "")) == q]
    if exact:
        return exact[:1]
    q_tokens = set(q.split())
    return [p for p in players if q_tokens <= set(norm(p.get("name", "")).split())]


def mention_strength(text: str, player: dict, players: list) -> int:
    t = f" {norm(text)} "
    name = norm(player.get("name", ""))
    tokens = name.split()
    if not tokens:
        return 0
    if f" {name} " in t:
        return 2
    surname = tokens[-1]
    if f" {surname} " not in t:
        return 0
    sharing = [
        other for other in players
        if other is not player and norm(other.get("name", "")).split()[-1:] == [surname]
    ]
    if not sharing:
        return 1
    first = tokens[0]
    if re.search(rf"\b{re.escape(first)}\b.{{0,3}}\b{re.escape(surname)}\b", t) or \
       re.search(rf"\b{re.escape(surname)}\b.{{0,3}}\b{re.escape(first)}\b", t):
        return 1
    # Solo la inicial ("A. Sánchez"): vale únicamente si ninguna otra jugadora con el mismo
    # apellido empieza por la misma letra; si no, no se puede saber de cuál habla.
    initial = first[:1]
    clash = any(norm(o.get("name", "")).split()[:1] and norm(o.get("name", "")).split()[0][:1] == initial
                for o in sharing)
    if (not clash) and re.search(rf"\b{re.escape(initial)}\b {re.escape(surname)}\b", t):
        return 1
    return 0


def mentions(text: str, player: dict, players: list) -> bool:
    return mention_strength(text, player, players) > 0


def mentioned_players(text: str, players: list) -> list:
    """Jugadoras del Top 20 mencionadas en el texto, con su fuerza: [(jugadora, fuerza), ...]."""
    out = []
    for p in players:
        s = mention_strength(text, p, players)
        if s:
            out.append((p, s))
    return out
