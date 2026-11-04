"""Velas de cripto de Binance (API pública de datos de mercado, sin registro).

Los símbolos siguen con el formato de Yahoo que usa toda la app (BTC-USD) y aquí se traducen
al par de Binance. Binance casi no opera contra USD: «-USD» se toma del par en USDT, una moneda
estable que cotiza muy cerca del dólar, así que el precio puede diferir unas décimas de punto.

Cualquier falla devuelve None o se omite el símbolo para que ``data.py`` use Yahoo como respaldo.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .macro import _http_json

_URL = "https://data-api.binance.vision/api/v3/klines"
_LIMIT = 1000  # velas por petición
_TIMEOUT = 8
_DAY_MS = 86_400_000

# intervalo de Yahoo: (intervalo de Binance, duración máxima de una vela). 2 minutos no existe en Binance.
_INTERVALS = {
    "1m": ("1m", pd.Timedelta(minutes=1)), "5m": ("5m", pd.Timedelta(minutes=5)),
    "15m": ("15m", pd.Timedelta(minutes=15)), "30m": ("30m", pd.Timedelta(minutes=30)),
    "60m": ("1h", pd.Timedelta(hours=1)), "1d": ("1d", pd.Timedelta(days=1)),
    "1wk": ("1w", pd.Timedelta(days=7)), "1mo": ("1M", pd.Timedelta(days=31)),
}
_QUOTES = {"USD": "USDT", "USDT": "USDT", "EUR": "EUR", "BTC": "BTC", "ETH": "ETH"}
_ALIASES = {"UNI7083": "UNI"}  # Yahoo distingue algunas monedas con un número
_SYMBOL = re.compile(r"^([A-Z0-9]+)-([A-Z]+)$")

_WORKERS = 6
_CACHE_SIZE = 16  # (par, intervalo) en memoria
_cache: dict[tuple[str, str], tuple[int, list]] = {}  # (par, intervalo): (inicio pedido, velas)


def _parts(symbol: str) -> tuple[str, str] | None:
    """BTC-USD -> (BTC, USDT) · None si el símbolo no tiene par posible en Binance."""
    m = _SYMBOL.match(symbol.upper())
    if not m or m[2] not in _QUOTES:
        return None
    return _ALIASES.get(m[1], m[1]), _QUOTES[m[2]]


def _klines(pair_: str, interval: str, **params) -> list:
    q = urllib.parse.urlencode({"symbol": pair_, "interval": interval, **params})
    return _http_json(f"{_URL}?{q}", timeout=_TIMEOUT)


def _pages(pair_: str, interval: str, step_ms: int, start_ms: int) -> list:
    """Velas desde ``start_ms``; cada petición tarda cerca de 1 s, así que se piden en paralelo."""
    if not start_ms:  # toda la historia: se empieza donde el par se listó, no en 1970
        first = _klines(pair_, interval, startTime=0, limit=1)
        if not first:
            return []
        start_ms = first[0][0]
    size = _LIMIT * step_ms
    now = int(time.time() * 1000)

    def page(start):
        # sin el tope, Binance responde vacío si el fin queda muy adelante (velas mensuales)
        return _klines(pair_, interval, startTime=start, endTime=min(start + size - 1, now), limit=_LIMIT)

    with ThreadPoolExecutor(_WORKERS) as pool:
        return [r for batch in pool.map(page, range(start_ms, now, size)) for r in batch]


def _download(pair_: str, interval: str, step_ms: int, start_ms: int) -> list:
    """Velas desde ``start_ms``; si ya se bajaron, solo se pide de la última (aún abierta) en adelante."""
    key = (pair_, interval)
    since, rows = _cache.get(key, (None, None))
    if rows and since <= start_ms:
        last = rows[-1][0]
        rows = [r for r in rows if r[0] < last] + _pages(pair_, interval, step_ms, last)
    else:
        since, rows = start_ms, _pages(pair_, interval, step_ms, start_ms)
    _cache.pop(key, None)
    _cache[key] = (since, rows)
    if len(_cache) > _CACHE_SIZE:
        _cache.pop(next(iter(_cache)), None)  # el usado hace más tiempo
    return [r for r in rows if r[0] >= start_ms]


def _is_live(rows: list) -> bool:
    """Falso si no hay velas o el par dejó de operar (Binance conserva los pares retirados)."""
    return bool(rows) and rows[-1][6] >= time.time() * 1000 - _DAY_MS


def candles(symbol: str, yf_interval: str, start: pd.Timestamp | None):
    """(DataFrame OHLCV, metadatos, ¿cubre todo el periodo?) o None si Binance no puede darlo.

    ``start`` es la fecha UTC más antigua pedida; None significa toda la historia, que nunca se
    considera cubierta porque Binance solo tiene datos desde que listó el par (BTC: 2017).
    El volumen es el de la moneda de cotización, igual que en Yahoo se expresa en dinero.
    """
    parts = _parts(symbol)
    if not parts or yf_interval not in _INTERVALS:
        return None
    base, quote = parts
    p = base + quote
    interval, step = _INTERVALS[yf_interval]
    start_ms = 0 if start is None else int(start.tz_localize("UTC").timestamp() * 1000)
    try:
        rows = _download(p, interval, int(step.total_seconds() * 1000), start_ms)
        if not _is_live(rows):
            return None
        meta = {"longName": f"{base}/{quote}", "currency": quote, "fullExchangeName": "Binance",
                "exchangeTimezoneName": "UTC", "instrumentType": "CRYPTOCURRENCY"}
        if step < pd.Timedelta(days=1):
            # el encabezado intradía compara contra el cierre de ayer, no contra la vela anterior
            daily = _klines(p, "1d", limit=2)
            if len(daily) > 1:
                meta["previousClose"] = float(daily[-2][4])
    except Exception:  # noqa: BLE001 - sin red, par inexistente o límite de peticiones: responde Yahoo
        return None
    df = pd.DataFrame({"Open": [r[1] for r in rows], "High": [r[2] for r in rows], "Low": [r[3] for r in rows],
                       "Close": [r[4] for r in rows], "Volume": [r[7] for r in rows]},
                      index=pd.to_datetime([r[0] for r in rows], unit="ms"), dtype=float)
    complete = start is not None and df.index[0] <= start + step
    return df, meta, complete


def quotes(symbols: list[str]) -> dict[str, tuple[float, float]]:
    """Último precio y % de cambio contra el cierre de ayer; omite lo que Binance no pueda dar."""
    out: dict[str, tuple[float, float]] = {}
    for s in symbols:
        parts = _parts(s)
        if not parts:
            continue
        try:
            rows = _klines("".join(parts), "1d", limit=2)
        except urllib.error.HTTPError:
            continue  # par inexistente: los demás sí pueden responder
        except Exception:  # noqa: BLE001 - sin conexión: no se espera el tiempo límite por cada símbolo
            break
        if _is_live(rows):
            last = float(rows[-1][4])
            out[s] = (last, (last / float(rows[-2][4]) - 1) * 100 if len(rows) > 1 else np.nan)
    return out
