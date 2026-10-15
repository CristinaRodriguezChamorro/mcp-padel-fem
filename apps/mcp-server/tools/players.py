"""
players.py — Reconoce jugadoras del Top 20 en un texto (noticias, resultados...).

Lo usan el pipeline de noticias y las tools del MCP, para que ambos entiendan
igual "esta noticia habla de X".

Fuerza de la mención:
  2 = nombre completo ("Gemma Triay Pons"), o nombre de pila (o su apodo: "Ari", "Delfi", "Bea") + primer
      apellido ("Gemma Triay", "Ari Sánchez")
  1 = solo apellido o inicial+apellido, y sin ambigüedad dentro del Top 20
  0 = no se menciona, o no se puede saber cuál de varias jugadoras con el mismo
      apellido es (p. ej. "Sánchez" con dos Sánchez en el Top 20)

Los nombres pueden venir con los dos apellidos ("Claudia Fernandez Sanchez": así los da Padel API) y la prensa
usa el nombre y el PRIMER apellido ("Claudia Fernández"): el primer apellido es la palabra que sigue al nombre de pila.
"""

import re
import unicodedata


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


PARTICLES = {"de", "del", "la", "las", "los", "da", "do", "dos", "y", "van", "von"}


def split_name(name: str) -> tuple:
    """'Claudia Fernandez Sanchez' -> ('claudia', 'fernandez', [tokens]). Un solo token ('Stupa') -> ('', 'stupa', ...)."""
    tokens = norm(name).split()
    if not tokens:
        return "", "", tokens
    if len(tokens) == 1:
        return "", tokens[0], tokens
    surname = next((t for t in tokens[1:] if t not in PARTICLES), tokens[-1])
    return tokens[0], surname, tokens


def _is_nickname_of(first: str, word: str) -> bool:
    """'ari' es un apodo de 'ariana', 'delfi' de 'delfina', 'bea' de 'beatriz' (un comienzo de al menos 3 letras)."""
    return len(word) >= 3 and first.startswith(word)


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
    first, surname, tokens = split_name(player.get("name", ""))
    if not tokens:
        return 0
    if f" {' '.join(tokens)} " in t:
        return 2
    sharing = [
        other for other in players
        if other is not player and split_name(other.get("name", ""))[1] == surname
    ]
    if first:
        # nombre de pila (o su apodo) + primer apellido: «Gemma Triay», «Ari Sánchez», «Delfi Brea».
        # Un apodo no vale si OTRA jugadora con ese apellido se llama exactamente así (Ari ≠ Ariana si hay una Ari).
        other_firsts = {split_name(o.get("name", ""))[0] for o in sharing}
        words = t.split()
        for before, after in zip(words, words[1:]):
            if after == surname and _is_nickname_of(first, before) and (before == first or before not in other_firsts):
                return 2
    if f" {surname} " not in t:
        return 0
    if not sharing:
        return 1
    if not first:
        return 1
    if re.search(rf"\b{re.escape(first)}\b.{{0,3}}\b{re.escape(surname)}\b", t) or \
       re.search(rf"\b{re.escape(surname)}\b.{{0,3}}\b{re.escape(first)}\b", t):
        return 1
    # Solo la inicial ("A. Sánchez"): vale únicamente si ninguna otra jugadora con el mismo
    # apellido empieza por la misma letra; si no, no se puede saber de cuál habla.
    initial = first[:1]
    clash = any(split_name(o.get("name", ""))[0][:1] == initial for o in sharing)
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
