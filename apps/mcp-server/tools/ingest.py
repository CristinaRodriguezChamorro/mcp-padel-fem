"""
ingest.py — Refresca ranking, calendario y resultados, sin depender de las visitas.

Una tarea en segundo plano pide los datos a su fuente, una vez al día (y a las pocas horas si
falla), y los guarda en los archivos de data/. La web y el MCP leen SOLO de esos archivos, así
que el tráfico hacia la fuente no crece con las visitas.

Fuentes (se elige sola):
  - Padel API (padelapi.org) si hay PADELAPI_TOKEN: datos reales con licencia, incluidas las
    semifinales de cada torneo. Es la fuente principal.
  - Wikipedia (CC BY-SA) si no hay token, o como respaldo si Padel API falla y todavía no hay
    datos buenos de la API que conservar. Con retraso de semanas.

Reglas de seguridad:
  - Los datos nuevos se validan antes de guardarse; si algo no cuadra, se conservan los
    anteriores y se anota el motivo (nunca se pisan datos buenos con datos dudosos).
  - Se guardan de forma atómica (archivo temporal + renombrado): nadie lee un archivo a medias.
  - Cada archivo lleva su fuente, su licencia y la fecha que la propia página dice tener.
  - Se puede apagar con WIKIPEDIA_INGEST=off.

Variables de entorno (opcionales):
  WIKIPEDIA_INGEST       "off" desactiva la tarea (por defecto, activa)
  WIKIPEDIA_INGEST_HOURS horas entre ejecuciones correctas (por defecto 24, mínimo 1)
  INGEST_START_DELAY     segundos de espera tras arrancar el servidor (por defecto 15)
"""

import asyncio
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

from tools import padelapi_source as pa
from tools import wikipedia_source as ws
from tools.local_data import _data_dir

STATUS_FILE = "_ingesta.json"
MIN_RANKING_ROWS = 8
MIN_TOURNAMENTS = 8
MAX_SHRINK = 0.6          # una tabla nueva con menos del 60 % de filas que la anterior se considera rota


def enabled() -> bool:
    return os.environ.get("WIKIPEDIA_INGEST", "on").strip().lower() not in ("off", "0", "false", "no")


def interval_hours() -> float:
    try:
        return max(1.0, float(os.environ.get("WIKIPEDIA_INGEST_HOURS", "").strip() or 24))
    except ValueError:
        return 24.0


def _start_delay() -> float:
    try:
        return max(0.0, float(os.environ.get("INGEST_START_DELAY", "").strip() or 15))
    except ValueError:
        return 15.0


# ── validación ───────────────────────────────────────────────────────────────

def _points(row: dict) -> int:
    return int(str(row["pts"]).replace(".", ""))


def validate(ranking: list, tournaments: list, year: int, previous_ranking_rows: int = 0,
             previous_tournaments: int = 0) -> None:
    """Lanza ValueError con el motivo si el conjunto no es de fiar."""
    if len(ranking) < MIN_RANKING_ROWS:
        raise ValueError(f"el ranking tiene solo {len(ranking)} filas (mínimo {MIN_RANKING_ROWS})")
    names = [r["name"] for r in ranking]
    if len(set(names)) != len(names):
        raise ValueError("el ranking tiene jugadoras repetidas")
    ordered = sorted(ranking, key=lambda r: r["pos"])
    if ordered[0]["pos"] != 1:
        raise ValueError("el ranking no empieza en la posición 1")
    pts = [_points(r) for r in ordered]
    if any(b > a for a, b in zip(pts, pts[1:])):
        raise ValueError("los puntos del ranking no van de mayor a menor")
    if previous_ranking_rows and len(ranking) < previous_ranking_rows * MAX_SHRINK:
        raise ValueError(f"el ranking pasó de {previous_ranking_rows} a {len(ranking)} filas")

    if len(tournaments) < MIN_TOURNAMENTS:
        raise ValueError(f"el calendario tiene solo {len(tournaments)} torneos (mínimo {MIN_TOURNAMENTS})")
    tnames = [t["name"] for t in tournaments]
    if len(set(tnames)) != len(tnames):
        raise ValueError("el calendario tiene torneos repetidos")
    for t in tournaments:
        if t.get("cancelado"):
            continue
        start, end = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
        if start.year != year or end < start or (end - start).days > 20:
            raise ValueError(f"fechas incoherentes en '{t['name']}'")
    if previous_tournaments and len(tournaments) < previous_tournaments * MAX_SHRINK:
        raise ValueError(f"el calendario pasó de {previous_tournaments} a {len(tournaments)} torneos")


# ── escritura ────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        tmp_path = Path(tmp)
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def ingest_status() -> dict:
    status = _read_json(_data_dir() / STATUS_FILE)
    return status or {"estado": "sin ejecutar", "activa": enabled()}


def _save_status(**fields) -> dict:
    path = _data_dir() / STATUS_FILE
    previous = _read_json(path)
    status = {**previous, **fields, "activa": enabled(),
              "ultimo_intento": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if fields.get("ok"):
        status["ultimo_exito"] = status["ultimo_intento"]
    try:
        _atomic_write(path, status)
    except OSError as exc:
        print(f"  ingesta: no se pudo guardar el estado ({type(exc).__name__})")
    return status


# ── ejecución ────────────────────────────────────────────────────────────────

async def _run_wikipedia(client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
                         today: Optional[date] = None) -> dict:
    """Una pasada completa con Wikipedia. Nunca lanza: devuelve el estado (ok o con el motivo del fallo)."""
    today = today or date.today()
    years = [today.year] + ([today.year - 1] if today.month <= 2 else [])
    try:
        page, year, error = None, None, None
        for y in years:
            try:
                page, year = await ws.fetch_page(y, client_factory), y
                break
            except ws.SourceError as exc:
                error = exc
                if "missingtitle" not in str(exc):
                    raise
        if page is None:
            raise error or ws.SourceError("no hay página de temporada")

        ranking = ws.parse_women_ranking(page["html"])
        tournaments = ws.parse_tournaments(page["html"], year)
        data_dir = _data_dir()
        prev_rank = _read_json(data_dir / "ranking.json").get("jugadoras") or []
        prev_cal = _read_json(data_dir / "calendario.json").get("torneos") or []
        prev_is_wiki = _read_json(data_dir / "ranking.json").get("_meta", {}).get("tipo") == "wikipedia"
        validate(ranking, tournaments, year,
                 previous_ranking_rows=len(prev_rank) if prev_is_wiki else 0,
                 previous_tournaments=len(prev_cal) if prev_is_wiki else 0)

        src_date = ws.source_date(page["html"])
        meta = {
            "tipo": "wikipedia",
            "actualizado": src_date.isoformat() if src_date else None,
            "consultado": today.isoformat(),
            "fuente": f"Wikipedia · {page['title'] or 'Premier Padel ' + str(year)}",
            "url": page["url"],
            "revid": page.get("revid"),
            "licencia": ws.LICENSE,
            "licencia_url": ws.LICENSE_URL,
            "nota": ("Datos de Wikipedia (CC BY-SA 4.0), que sus editores actualizan con retraso; el ranking "
                     "original lo publica la FIP. 'actualizado' es la fecha que indica la propia página."),
        }
        _atomic_write(data_dir / "ranking.json", {"_meta": meta, "jugadoras": ranking})
        _atomic_write(data_dir / "calendario.json", {"_meta": meta, "torneos": tournaments})
        status = _save_status(ok=True, estado="correcto", mensaje="Datos actualizados desde Wikipedia.",
                              ranking_filas=len(ranking), torneos=len(tournaments),
                              fecha_fuente=meta["actualizado"], consultado=meta["consultado"])
        print(f"  ingesta: correcta ({len(ranking)} jugadoras, {len(tournaments)} torneos, fuente del {meta['actualizado']})")
        return status
    except asyncio.CancelledError:
        raise
    except (ws.SourceError, ValueError, OSError) as exc:
        print(f"  ingesta: sin cambios, {exc}")
        return _save_status(ok=False, estado="error", mensaje=f"Se conservan los datos anteriores: {exc}")
    except Exception as exc:  # noqa: BLE001 — la tarea nunca debe tumbar el servidor
        print(f"  ingesta: error inesperado {type(exc).__name__}")
        return _save_status(ok=False, estado="error",
                            mensaje=f"Error inesperado ({type(exc).__name__}); se conservan los datos anteriores.")


async def _run_padelapi(client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
                        today: Optional[date] = None, sleep: Callable = asyncio.sleep,
                        interval: Optional[float] = None) -> dict:
    """Una pasada completa con Padel API. Nunca lanza: devuelve el estado (ok o con el motivo del fallo)."""
    today = today or date.today()
    try:
        data_dir = _data_dir()
        prev_rank_doc = _read_json(data_dir / "ranking.json")
        prev_cal = _read_json(data_dir / "calendario.json").get("torneos") or []
        prev_is_api = prev_rank_doc.get("_meta", {}).get("tipo") == "api"
        got = await pa.fetch_all(today, prev_cal if prev_is_api else [], client_factory, sleep=sleep, interval=interval)
        validate(got["ranking"], got["tournaments"], today.year,
                 previous_ranking_rows=len(prev_rank_doc.get("jugadoras") or []) if prev_is_api else 0,
                 previous_tournaments=len(prev_cal) if prev_is_api else 0)

        common = {"tipo": "api", "consultado": today.isoformat(), "fuente": "Padel API (padelapi.org)", "url": pa.SITE}
        rank_meta = {**common, "actualizado": got["snapshot"] or today.isoformat(),
                     "nota": "Ranking mundial femenino (puntos FIP) según Padel API. 'actualizado' es la fecha "
                             "de la instantánea semanal del ranking."}
        cal_meta = {**common, "actualizado": today.isoformat(),
                    "nota": "Calendario y resultados de Premier Padel (Major, P1, P2 y Finals) según Padel API. "
                            "Las semifinalistas (terceras) salen de las semifinales de cada torneo."}
        data_dir_rank, data_dir_cal = data_dir / "ranking.json", data_dir / "calendario.json"
        _atomic_write(data_dir_rank, {"_meta": rank_meta, "jugadoras": got["ranking"]})
        _atomic_write(data_dir_cal, {"_meta": cal_meta, "torneos": got["tournaments"]})
        finals = sum(1 for t in got["tournaments"] if t.get("final_femenina"))
        status = _save_status(ok=True, estado="correcto", mensaje="Datos actualizados desde Padel API.",
                              fuente_activa="padelapi", ranking_filas=len(got["ranking"]),
                              torneos=len(got["tournaments"]), finales=finals, peticiones=got["calls"],
                              avisos=got["notes"], fecha_fuente=rank_meta["actualizado"],
                              consultado=today.isoformat(), intento_padelapi=None)
        print(f"  ingesta (Padel API): correcta ({len(got['ranking'])} jugadoras, {len(got['tournaments'])} torneos, "
              f"{finals} finales, {got['calls']} peticiones)")
        return status
    except asyncio.CancelledError:
        raise
    except (pa.SourceError, ValueError, OSError) as exc:
        print(f"  ingesta (Padel API): sin cambios, {exc}")
        return _save_status(ok=False, estado="error", fuente_activa="padelapi",
                            mensaje=f"Se conservan los datos anteriores: {exc}")
    except Exception as exc:  # noqa: BLE001 — la tarea nunca debe tumbar el servidor
        print(f"  ingesta (Padel API): error inesperado {type(exc).__name__}")
        return _save_status(ok=False, estado="error", fuente_activa="padelapi",
                            mensaje=f"Error inesperado ({type(exc).__name__}); se conservan los datos anteriores.")


async def run_once(client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
                   today: Optional[date] = None) -> dict:
    """
    Una pasada completa con la fuente que toque: Padel API si hay token; si falla y todavía no hay datos
    buenos de la API que conservar, Wikipedia como respaldo. Nunca lanza.
    """
    if not pa.enabled():
        return await _run_wikipedia(client_factory, today)
    status = await _run_padelapi(client_factory, today)
    if status.get("ok"):
        return status
    if _read_json(_data_dir() / "ranking.json").get("_meta", {}).get("tipo") == "api":
        return status                       # ya hay datos buenos de la API: se conservan
    fallback = await _run_wikipedia(client_factory, today)
    return _save_status(intento_padelapi=status.get("mensaje"), fuente_activa=(
        "wikipedia" if fallback.get("ok") else "ninguna"))


async def run_forever(sleep: Callable = asyncio.sleep, first_delay: Optional[float] = None,
                      run: Optional[Callable] = None) -> None:
    """Bucle de la tarea en segundo plano. Se cancela al apagar el servidor."""
    run = run or run_once
    await sleep(_start_delay() if first_delay is None else first_delay)
    failures = 0
    while True:
        status = await run()
        failures = 0 if status.get("ok") else failures + 1
        wait = interval_hours() * 3600 if status.get("ok") else min(6 * 3600, 3600 * 2 ** (failures - 1))
        await sleep(wait)
