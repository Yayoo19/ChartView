"""Series económicas que no están en Yahoo Finance.

Fuentes:
  MACRO:<clave>  catálogo de la Reserva Federal (H.6 dinero, H.4.1 balance, H.15 tasas)
                 descargado de DBnomics (https://db.nomics.world), sin registro.
  FRED:<id>      cualquier serie de FRED (St. Louis Fed); requiere una clave API gratuita.
  CSV:<nombre>   series propias importadas desde un CSV (fecha, valor), p. ej. el ISM PMI.

Cada fuente devuelve una serie de valores con índice de fechas; ``data.py`` la
convierte a velas (OHLC) según el intervalo elegido.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

MARKET = "Macro EE.UU."
PREFIXES = ("MACRO:", "FRED:", "CSV:")
SERIES_DIR = Path.home() / ".chartview" / "series"
FRED_API_KEY = ""  # la app la asigna desde la configuración

_DB = "https://api.db.nomics.world/v22/series/"
_FRED = "https://api.stlouisfed.org/fred/"

# clave: (ruta en DBnomics o par para diferencial, nombre, unidades, factor, fuente)
CATALOG = {
    "M2": ("FED/H6_H6_M2/M2.M", "Oferta monetaria M2 de EE.UU. (desestacionalizada)", "USD", 1e9,
           "Reserva Federal H.6"),
    "M1": ("FED/H6_H6_M2/M1.M", "Oferta monetaria M1 de EE.UU. (desestacionalizada)", "USD", 1e9,
           "Reserva Federal H.6"),
    "FED_BALANCE": ("FED/H41/RESPPMA_N.WW", "Balance de la Reserva Federal: activos totales", "USD", 1e6,
                    "Reserva Federal H.4.1"),
    "FED_FUNDS": ("FED/H15/RIFSPFF_N.B", "Tasa efectiva de fondos federales", "%", 1, "Reserva Federal H.15"),
    "UST3M": ("FED/H15/RIFLGFCM03_N.B", "Tesoro EE.UU. 3 meses: rendimiento", "%", 1, "Reserva Federal H.15"),
    "UST2Y": ("FED/H15/RIFLGFCY02_N.B", "Tesoro EE.UU. 2 años: rendimiento", "%", 1, "Reserva Federal H.15"),
    "UST10Y": ("FED/H15/RIFLGFCY10_N.B", "Tesoro EE.UU. 10 años: rendimiento", "%", 1, "Reserva Federal H.15"),
    "UST30Y": ("FED/H15/RIFLGFCY30_N.B", "Tesoro EE.UU. 30 años: rendimiento", "%", 1, "Reserva Federal H.15"),
    "SPREAD_10Y2Y": (("UST10Y", "UST2Y"), "Diferencial 10 años − 2 años (pendiente de la curva)", "pp", 1,
                     "Reserva Federal H.15"),
    "SPREAD_10Y3M": (("UST10Y", "UST3M"), "Diferencial 10 años − 3 meses", "pp", 1, "Reserva Federal H.15"),
}

_cache: dict[str, tuple[float, pd.Series, dict]] = {}
_TTL = 15 * 60  # las series macro cambian como mucho una vez al día


def is_macro(symbol: str) -> bool:
    return symbol.upper().startswith(PREFIXES)


def _http_json(url: str, timeout: float = 40):
    req = urllib.request.Request(url, headers={"User-Agent": "ChartView/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# --------------------------------------------------------------------------- #
# Fuentes
# --------------------------------------------------------------------------- #
def _dbnomics(path: str) -> pd.Series:
    docs = _http_json(f"{_DB}{path}?observations=1&format=json")["series"]["docs"]
    if not docs:
        raise ValueError(f"DBnomics no tiene la serie {path}")
    d = docs[0]
    idx = pd.to_datetime(d.get("period_start_day") or d["period"])
    vals = pd.to_numeric(pd.Series(d["value"]), errors="coerce").to_numpy(dtype=float)
    s = pd.Series(vals, index=idx)
    s[s <= -999999] = np.nan  # marcador de "sin dato" de la Fed
    return s.dropna()


def _catalog(key: str) -> tuple[pd.Series, dict]:
    if key not in CATALOG:
        raise ValueError(f"«{key}» no está en el catálogo macro. Disponibles: {', '.join(CATALOG)}")
    path, name, units, factor, source = CATALOG[key]
    if isinstance(path, tuple):
        a, _ = get_series("MACRO:" + path[0])
        b, _ = get_series("MACRO:" + path[1])
        s = (a - b).dropna()
    else:
        s = _dbnomics(path) * factor
    return s, {"longName": name, "currency": units, "fullExchangeName": f"{source} · DBnomics"}


def _fred(series_id: str) -> tuple[pd.Series, dict]:
    if not FRED_API_KEY:
        raise ValueError("Para abrir series de FRED necesitas una clave API gratuita. Pídela en "
                         "https://fredaccount.stlouisfed.org/apikeys y escríbela en ⚙ → Clave API de FRED.")
    q = urllib.parse.urlencode({"series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json"})
    try:
        obs = _http_json(f"{_FRED}series/observations?{q}")["observations"]
        info = _http_json(f"{_FRED}series?{q}")["seriess"][0]
    except urllib.error.HTTPError as e:
        # el mensaje no incluye la URL, así la clave no aparece en pantalla
        if e.code == 400:
            raise ValueError(f"FRED rechazó la petición: revisa que «{series_id}» exista y que la clave sea válida.")
        raise ValueError(f"FRED respondió con error {e.code}.")
    s = pd.Series(pd.to_numeric([o["value"] for o in obs], errors="coerce"),
                  index=pd.to_datetime([o["date"] for o in obs])).dropna()
    return s, {"longName": info.get("title", series_id), "currency": info.get("units_short", ""),
               "fullExchangeName": "FRED · St. Louis Fed"}


def _csv(name: str) -> tuple[pd.Series, dict]:
    path = SERIES_DIR / f"{name}.csv"
    if not path.exists():
        raise ValueError(f"No existe la serie importada «{name}». Impórtala con ⚙ → Importar serie desde CSV.")
    df = pd.read_csv(path, parse_dates=["date"])
    s = pd.Series(df["value"].to_numpy(dtype=float), index=pd.DatetimeIndex(df["date"])).dropna()
    return s, {"longName": name.replace("_", " "), "currency": "", "fullExchangeName": "Serie importada (CSV)"}


def get_series(symbol: str) -> tuple[pd.Series, dict]:
    """Serie de valores (índice de fechas ascendente) y metadatos para el encabezado."""
    sym = symbol.upper()
    hit = _cache.get(sym)
    if hit and time.time() - hit[0] < _TTL and not sym.startswith("CSV:"):
        return hit[1], dict(hit[2])
    prefix, _, key = sym.partition(":")
    if prefix == "MACRO":
        s, meta = _catalog(key)
    elif prefix == "FRED":
        s, meta = _fred(key)
    else:
        s, meta = _csv(key)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.empty:
        raise ValueError(f"La serie {symbol} no tiene datos.")
    meta["instrumentType"] = "MACRO"
    _cache[sym] = (time.time(), s, meta)
    return s, dict(meta)


# --------------------------------------------------------------------------- #
# Importar CSV
# --------------------------------------------------------------------------- #
def clean_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_").upper()[:40]


def _day_first(raw: pd.Series) -> bool:
    """¿Las fechas vienen como día/mes? (si es ambiguo se asume día/mes, como en México)."""
    parts = raw.str.extract(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})")
    if parts.isna().all().all():
        return False
    first = pd.to_numeric(parts[0], errors="coerce")
    second = pd.to_numeric(parts[1], errors="coerce")
    if (first > 31).any():  # año primero: 2025-03-01
        return False
    if (second > 12).any():  # 03/25/2025 -> mes/día
        return False
    return True


def read_csv(path: str) -> pd.Series:
    """Lee un CSV con una columna de fechas (la primera) y una de valores (la última numérica)."""
    df = pd.read_csv(path, sep=None, engine="python")
    if df.shape[1] < 2:
        raise ValueError("El CSV necesita al menos dos columnas: fecha y valor.")
    raw_dates = df.iloc[:, 0].astype(str).str.strip()
    dates = pd.to_datetime(raw_dates, errors="coerce", dayfirst=_day_first(raw_dates), format="mixed")
    values = None
    for col in reversed(df.columns[1:]):
        v = pd.to_numeric(df[col].astype(str).str.replace(r"[,%\s]", "", regex=True), errors="coerce")
        if v.notna().mean() > 0.5:
            values = v
            break
    if values is None:
        raise ValueError("No encontré una columna con valores numéricos.")
    s = pd.Series(values.to_numpy(dtype=float), index=dates).dropna()
    s = s[s.index.notna()].sort_index()
    if len(s) < 2:
        raise ValueError("El CSV debe tener al menos dos filas con fecha y valor válidos.")
    return s


def import_csv(path: str, name: str) -> str:
    """Valida el CSV, lo guarda normalizado en ~/.chartview/series y devuelve su símbolo."""
    name = clean_name(name)
    if not name:
        raise ValueError("Escribe un nombre para la serie.")
    s = read_csv(path)
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    s.rename("value").to_csv(SERIES_DIR / f"{name}.csv", index_label="date")
    _cache.pop(f"CSV:{name}", None)
    return f"CSV:{name}"


def imported() -> list[str]:
    if not SERIES_DIR.exists():
        return []
    return sorted(f"CSV:{p.stem}" for p in SERIES_DIR.glob("*.csv"))


# --------------------------------------------------------------------------- #
# Búsqueda
# --------------------------------------------------------------------------- #
def _fold(t: str) -> str:
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()


_KEYWORDS = {
    "M2": "dinero money supply liquidez masa monetaria",
    "M1": "dinero money supply liquidez masa monetaria",
    "FED_BALANCE": "balance sheet walcl activos qe qt reserva federal banco central",
    "FED_FUNDS": "tasa interes fed funds rate politica monetaria banxico",
    "UST3M": "bono bond treasury yield tasa letra 3m",
    "UST2Y": "bono bond treasury yield tasa nota 2y",
    "UST10Y": "bono bond treasury yield tasa nota 10y",
    "UST30Y": "bono bond treasury yield tasa 30y",
    "SPREAD_10Y2Y": "bono bond curva yield curve inversion spread 2s10s",
    "SPREAD_10Y3M": "bono bond curva yield curve inversion spread",
}


def search(query: str) -> list[dict]:
    q = _fold(query.strip())
    out = []
    for key, (_, name, units, _, source) in CATALOG.items():
        hay = _fold(f"{key} {name} {_KEYWORDS.get(key, '')}")
        if q and all(w in hay for w in q.split()):
            out.append({"symbol": f"MACRO:{key}", "name": name, "exchange": source, "type": "Macro"})
    for sym in imported():
        if q in _fold(sym):
            out.append({"symbol": sym, "name": sym[4:].replace("_", " "), "exchange": "CSV", "type": "Importada"})
    if FRED_API_KEY and re.fullmatch(r"[A-Za-z0-9_]{2,30}", query.strip()):
        out.append({"symbol": f"FRED:{query.strip().upper()}", "name": "Serie de FRED (por ID)",
                    "exchange": "FRED", "type": "Macro"})
    return out
