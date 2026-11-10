"""Configuración persistente en ~/.chartview/config.json."""
from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from pathlib import Path

PATH = Path.home() / ".chartview" / "config.json"

DEFAULT = {
    "symbol": "BTC-USD",
    "market": "Cripto",
    "interval": "1 día",
    "period": "2y",
    "chart_type": "Velas",
    "auto_refresh": False,
    "refresh_secs": 60,
    "indicators": [
        {"key": "ema", "params": {"length": 20}, "color": None},
        {"key": "ema", "params": {"length": 50}, "color": None},
        {"key": "sma", "params": {"length": 200}, "color": "#ffffff"},
        {"key": "volume", "params": {}, "color": None},
        {"key": "rsi", "params": {}, "color": None},
        {"key": "macd", "params": {}, "color": None},
    ],
    "drawings": {},
    # varias listas de seguimiento: nombre -> {grupo de mercado -> símbolos}
    "active_watchlist": "Principal",
    "watchlists": {
        "Principal": {
            "México (BMV)": ["^MXX", "AMXB.MX", "WALMEX.MX", "GFNORTEO.MX", "FEMSAUBD.MX", "CEMEXCPO.MX",
                             "GMEXICOB.MX", "BIMBOA.MX", "MXN=X"],
            "EE.UU.": ["^GSPC", "^IXIC", "SPY", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA", "META"],
            "Asia": ["^N225", "^HSI", "^KS11", "000001.SS"],
            "Cripto": ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "DOGE-USD", "ADA-USD"],
        },
        "Índices del mundo": {
            "México (BMV)": ["^MXX"],
            "EE.UU.": ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"],
            "Asia": ["^N225", "^HSI", "^HSCE", "000001.SS", "399001.SZ", "000300.SS", "^KS11", "^KQ11",
                     "^TWII", "^STI", "^BSESN", "^NSEI", "^AXJO", "^JKSE"],
        },
        "Macro y bonos": {
            "Macro EE.UU.": ["MACRO:M2", "MACRO:FED_BALANCE", "MACRO:FED_FUNDS", "MACRO:UST3M", "MACRO:UST2Y",
                             "MACRO:UST10Y", "MACRO:UST30Y", "MACRO:SPREAD_10Y2Y", "2YY=F", "^TNX", "ZT=F",
                             "ZN=F", "TLT"],
        },
    },
}


load_warning = ""  # aviso para el usuario si el archivo estaba dañado al abrir
last_error = ""  # por qué falló el último guardado ("" si salió bien)


def _backup() -> Path:
    """Copia de la configuración anterior a la última que se guardó."""
    return PATH.with_name(PATH.name + ".bak")


def _read(path: Path) -> dict | None:
    """El contenido del archivo, None si no existe; ValueError si no es una configuración válida."""
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ValueError(str(e)) from e
    if not isinstance(saved, dict):
        raise ValueError("no es un objeto JSON")
    return saved


def _read_saved() -> dict | None:
    """Lee la configuración; si está dañada la aparta (no se sobrescribe) y usa el respaldo."""
    global load_warning
    load_warning = ""
    try:
        saved = _read(PATH)
    except ValueError:
        kept = PATH.with_name(f"{PATH.stem}.dañado-{time.strftime('%Y%m%d-%H%M%S')}{PATH.suffix}")
        try:
            os.replace(PATH, kept)
            where = f"El archivo dañado se conservó como {kept.name}."
        except OSError:
            where = "No se pudo apartar el archivo dañado."
        load_warning = f"El archivo de configuración {PATH} estaba dañado. {where}"
    else:
        if saved is not None:
            return saved
    # sin archivo principal (primer arranque, cierre a medio guardar o archivo dañado): el respaldo
    try:
        saved = _read(_backup())
    except ValueError:
        saved = None
    if load_warning:
        load_warning += ("\n\nSe recuperó el respaldo del guardado anterior." if saved is not None else
                         "\n\nNo había un respaldo válido: se abrió la configuración inicial.")
    return saved


def load() -> dict:
    cfg = deepcopy(DEFAULT)
    saved = _read_saved()
    if saved is None:
        return cfg
    # versión anterior: una sola lista en "watchlist" -> se vuelve la lista "Principal"
    old = saved.pop("watchlist", None)
    if old is not None and "watchlists" not in saved:
        old.setdefault("Asia", list(DEFAULT["watchlists"]["Principal"]["Asia"]))
        saved["watchlists"] = {"Principal": old,
                               "Índices del mundo": deepcopy(DEFAULT["watchlists"]["Índices del mundo"])}
    cfg.update(saved)
    if not cfg.get("watchlists"):
        cfg["watchlists"] = deepcopy(DEFAULT["watchlists"])
    # listas nuevas que se agregan una sola vez (si después la borras, no vuelve)
    seeded = cfg.setdefault("seeded_lists", [])
    for name in ("Macro y bonos",):
        if name not in seeded:
            seeded.append(name)
            cfg["watchlists"].setdefault(name, deepcopy(DEFAULT["watchlists"][name]))
    return cfg


def save(cfg: dict) -> bool:
    """Guarda sin dejar nunca un archivo a medias: escribe en un temporal y lo cambia de nombre.

    La configuración anterior queda como respaldo (.bak). Devuelve False si no se pudo guardar;
    el motivo queda en ``last_error`` y el archivo anterior sigue intacto.
    """
    global last_error
    tmp = PATH.with_name(PATH.name + ".tmp")
    try:
        text = json.dumps(cfg, ensure_ascii=False, indent=2)  # si algo no se puede guardar, falla antes de tocar el disco
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if PATH.exists():
            os.replace(PATH, _backup())
        os.replace(tmp, PATH)
    except (OSError, TypeError, ValueError) as e:
        last_error = str(e) or e.__class__.__name__
        return False
    last_error = ""
    return True
