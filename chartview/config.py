"""Configuración persistente en ~/.chartview/config.json."""
from __future__ import annotations

import json
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


def load() -> dict:
    cfg = deepcopy(DEFAULT)
    try:
        with open(PATH, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
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


def save(cfg: dict) -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
