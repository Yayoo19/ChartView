"""Indicadores técnicos implementados únicamente con pandas/numpy.

Cada indicador se registra con el decorador ``@indicator`` y devuelve una lista
de ``Out`` (series a dibujar). El registro se usa para construir los menús y el
diálogo de parámetros de forma automática.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

UP = "#26a69a"
DOWN = "#ef5350"

CATEGORIES = [
    "Medias móviles",
    "Bandas y canales",
    "Tendencia",
    "Osciladores / Momento",
    "Volumen",
    "Volatilidad",
    "Fibonacci y Gann",
]

SOURCES = ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"]


# --------------------------------------------------------------------------- #
# Estructuras
# --------------------------------------------------------------------------- #
def _np(v) -> np.ndarray:
    if isinstance(v, (pd.Series, pd.Index)):
        return v.to_numpy(dtype=float, na_value=np.nan)
    return np.asarray(v, dtype=float)


@dataclass
class Out:
    """Una serie de salida de un indicador.

    kind: line | dots | hist | fill (relleno entre y e y2) | cloud (relleno
    verde/rojo según y >= y2, estilo nube de Ichimoku) | vline (líneas
    verticales; aquí ``y`` son las posiciones X y ``labels`` sus textos).
    text: etiqueta que se escribe al final de la línea.
    autoscale: False para que la serie no influya en el rango vertical.
    """
    name: str
    y: np.ndarray
    kind: str = "line"
    color: str | None = None
    color2: str | None = None
    y2: np.ndarray | None = None
    offset: int = 0
    width: float = 1.5
    style: str = "solid"
    mask: np.ndarray | None = None
    show_value: bool = True
    text: str | None = None
    labels: list | None = None
    autoscale: bool = True

    def __post_init__(self):
        self.y = _np(self.y)
        if self.y2 is not None:
            self.y2 = _np(self.y2)
        if self.mask is not None:
            self.mask = np.asarray(self.mask, dtype=bool)


@dataclass
class Param:
    name: str
    label: str
    default: object
    minimum: float = 1
    maximum: float = 1000
    step: float | None = None
    options: list | None = None

    @property
    def is_int(self) -> bool:
        return isinstance(self.default, int) and not isinstance(self.default, bool)


@dataclass
class IndicatorDef:
    key: str
    name: str
    abbr: str
    category: str
    overlay: bool
    func: Callable
    params: list = field(default_factory=list)
    levels: list = field(default_factory=list)
    yrange: tuple | None = None
    description: str = ""

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.params}

    def label(self, params: dict) -> str:
        vals = []
        for p in self.params:
            v = params.get(p.name, p.default)
            if p.options is not None:
                if v != p.default:
                    vals.append(str(v))
            else:
                vals.append(format(v, "g") if isinstance(v, float) else str(v))
        return f"{self.abbr} {' '.join(vals)}".strip()


@dataclass
class Computed:
    defn: IndicatorDef
    label: str
    outs: list
    color: str | None = None


REGISTRY: dict[str, IndicatorDef] = {}


def P(name, label, default, minimum=1, maximum=1000, step=None, options=None) -> Param:
    return Param(name, label, default, minimum, maximum, step, options)


def indicator(key, name, abbr, category, overlay, params=(), levels=(), yrange=None, description=""):
    def deco(fn):
        REGISTRY[key] = IndicatorDef(key, name, abbr, category, overlay, fn,
                                     list(params), list(levels), yrange, description)
        return fn
    return deco


def compute(key: str, params: dict | None, df: pd.DataFrame, color: str | None = None) -> Computed:
    d = REGISTRY[key]
    p = d.defaults()
    p.update(params or {})
    for prm in d.params:
        if prm.options is None:
            p[prm.name] = int(p[prm.name]) if prm.is_int else float(p[prm.name])
    outs = d.func(df, **p)
    if color and outs:
        outs[0].color = color
    return Computed(d, d.label(p), outs, color)


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def src(df: pd.DataFrame, s: str = "close") -> pd.Series:
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    return {
        "close": c, "open": o, "high": h, "low": l,
        "hl2": (h + l) / 2, "hlc3": (h + l + c) / 3, "ohlc4": (o + h + l + c) / 4,
    }[s]


def sma(s, n):
    return s.rolling(n, min_periods=n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s, n):
    """Media de Wilder (la que usan RSI, ATR y ADX)."""
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rolling_dot(s: pd.Series, w) -> pd.Series:
    """Producto punto móvil; ``w`` va del dato más antiguo al más reciente."""
    v = _np(s)
    w = np.asarray(w, dtype=float)
    n = len(w)
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        out[n - 1:] = np.convolve(v, w[::-1], mode="valid")
    return pd.Series(out, index=s.index)


def wma(s, n):
    w = np.arange(1, n + 1, dtype=float)
    return rolling_dot(s, w / w.sum())


def hma(s, n):
    return wma(2 * wma(s, max(1, n // 2)) - wma(s, n), max(1, int(np.sqrt(n))))


def linreg(s, n):
    """Valor final de la regresión lineal móvil (LSMA)."""
    x = np.arange(n, dtype=float)
    xm = x.mean()
    denom = ((x - xm) ** 2).sum() or 1.0
    sy = s.rolling(n, min_periods=n).sum()
    sxy = rolling_dot(s, x)
    slope = (sxy - xm * sy) / denom
    return sy / n + slope * (n - 1 - xm)


def true_range(df):
    pc = df["Close"].shift()
    return pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()],
                     axis=1).max(axis=1)


def atr(df, n):
    return rma(true_range(df), n)


def rsi(s, n):
    d = s.diff()
    up = rma(d.clip(lower=0), n)
    dn = rma(-d.clip(upper=0), n)
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna())


def stdev(s, n):
    return s.rolling(n, min_periods=n).std(ddof=0)


def _safe_div(a, b):
    return a / b.replace(0, np.nan)


SRC = P("source", "Fuente", "close", options=SOURCES)


# --------------------------------------------------------------------------- #
# Medias móviles (sobre la gráfica)
# --------------------------------------------------------------------------- #
@indicator("sma", "Media móvil simple (SMA)", "SMA", "Medias móviles", True,
           [P("length", "Periodo", 20), SRC], description="Promedio aritmético de los últimos N precios.")
def _sma(df, length, source):
    return [Out("SMA", sma(src(df, source), length))]


@indicator("ema", "Media móvil exponencial (EMA)", "EMA", "Medias móviles", True,
           [P("length", "Periodo", 20), SRC], description="Media que pondera más los datos recientes.")
def _ema(df, length, source):
    return [Out("EMA", ema(src(df, source), length))]


@indicator("wma", "Media móvil ponderada (WMA)", "WMA", "Medias móviles", True,
           [P("length", "Periodo", 20), SRC])
def _wma(df, length, source):
    return [Out("WMA", wma(src(df, source), length))]


@indicator("hma", "Media móvil de Hull (HMA)", "HMA", "Medias móviles", True,
           [P("length", "Periodo", 21), SRC], description="Media muy suave y con poco retraso.")
def _hma(df, length, source):
    return [Out("HMA", hma(src(df, source), length))]


@indicator("dema", "Media exponencial doble (DEMA)", "DEMA", "Medias móviles", True,
           [P("length", "Periodo", 20), SRC])
def _dema(df, length, source):
    e1 = ema(src(df, source), length)
    return [Out("DEMA", 2 * e1 - ema(e1, length))]


@indicator("tema", "Media exponencial triple (TEMA)", "TEMA", "Medias móviles", True,
           [P("length", "Periodo", 20), SRC])
def _tema(df, length, source):
    e1 = ema(src(df, source), length)
    e2 = ema(e1, length)
    return [Out("TEMA", 3 * e1 - 3 * e2 + ema(e2, length))]


@indicator("vwma", "Media ponderada por volumen (VWMA)", "VWMA", "Medias móviles", True,
           [P("length", "Periodo", 20), SRC])
def _vwma(df, length, source):
    v = df["Volume"]
    return [Out("VWMA", _safe_div((src(df, source) * v).rolling(length).sum(), v.rolling(length).sum()))]


@indicator("kama", "Media adaptativa de Kaufman (KAMA)", "KAMA", "Medias móviles", True,
           [P("length", "Periodo ER", 10), P("fast", "Rápida", 2), P("slow", "Lenta", 30), SRC])
def _kama(df, length, fast, slow, source):
    s = src(df, source)
    v = _np(s)
    change = (s - s.shift(length)).abs()
    vol = s.diff().abs().rolling(length).sum()
    er = _np(_safe_div(change, vol).fillna(0))
    fsc, ssc = 2 / (fast + 1), 2 / (slow + 1)
    sc = (er * (fsc - ssc) + ssc) ** 2
    out = np.full(len(v), np.nan)
    if len(v) > length:
        out[length - 1] = v[length - 1]
        for i in range(length, len(v)):
            prev = out[i - 1] if np.isfinite(out[i - 1]) else v[i]
            out[i] = prev + sc[i] * (v[i] - prev)
    return [Out("KAMA", out)]


@indicator("alma", "Media de Arnaud Legoux (ALMA)", "ALMA", "Medias móviles", True,
           [P("length", "Periodo", 9), P("offset", "Offset", 0.85, 0, 1, 0.05), P("sigma", "Sigma", 6.0, 0.5, 50), SRC])
def _alma(df, length, offset, sigma, source):
    m = offset * (length - 1)
    s = length / sigma
    w = np.exp(-((np.arange(length) - m) ** 2) / (2 * s * s))
    return [Out("ALMA", rolling_dot(src(df, source), w / w.sum()))]


@indicator("lsma", "Media de mínimos cuadrados (LSMA)", "LSMA", "Medias móviles", True,
           [P("length", "Periodo", 25), SRC], description="Extremo de la regresión lineal móvil.")
def _lsma(df, length, source):
    return [Out("LSMA", linreg(src(df, source), length))]


@indicator("mcginley", "McGinley Dynamic", "MGD", "Medias móviles", True, [P("length", "Periodo", 14)])
def _mcginley(df, length):
    c = _np(df["Close"])
    out = np.full(len(c), np.nan)
    if len(c):
        out[0] = c[0]
        for i in range(1, len(c)):
            prev = out[i - 1]
            ratio = c[i] / prev if prev else 1.0
            out[i] = prev + (c[i] - prev) / (length * ratio ** 4)
    return [Out("McGinley", out)]


@indicator("ribbon", "Cinta de EMAs (8-13-21-34-55)", "Cinta EMA", "Medias móviles", True, [SRC])
def _ribbon(df, source):
    s = src(df, source)
    cols = ["#00e676", "#66bb6a", "#ffee58", "#ffa726", "#ef5350"]
    return [Out(f"EMA {n}", ema(s, n), color=c, width=1.2) for n, c in zip((8, 13, 21, 34, 55), cols)]


# --------------------------------------------------------------------------- #
# Bandas y canales
# --------------------------------------------------------------------------- #
@indicator("bbands", "Bandas de Bollinger", "BB", "Bandas y canales", True,
           [P("length", "Periodo", 20), P("mult", "Desviaciones", 2.0, 0.1, 10, 0.1), SRC],
           description="Media ± N desviaciones estándar.")
def _bbands(df, length, mult, source):
    s = src(df, source)
    mid = sma(s, length)
    sd = stdev(s, length)
    up, lo = mid + mult * sd, mid - mult * sd
    return [
        Out("Base", mid, color="#ff9800", width=1.2),
        Out("Superior", up, color="#2196f3", width=1.2),
        Out("Inferior", lo, color="#2196f3", width=1.2),
        Out("Relleno", up, kind="fill", y2=lo, color="#2196f318", show_value=False),
    ]


@indicator("keltner", "Canal de Keltner", "KC", "Bandas y canales", True,
           [P("length", "Periodo", 20), P("mult", "Multiplicador", 2.0, 0.1, 10, 0.1), P("atr_len", "Periodo ATR", 10)])
def _keltner(df, length, mult, atr_len):
    mid = ema(df["Close"], length)
    a = atr(df, atr_len)
    up, lo = mid + mult * a, mid - mult * a
    return [
        Out("Base", mid, color="#2962ff", width=1.2),
        Out("Superior", up, color="#26c6da", width=1.2),
        Out("Inferior", lo, color="#26c6da", width=1.2),
        Out("Relleno", up, kind="fill", y2=lo, color="#26c6da14", show_value=False),
    ]


@indicator("donchian", "Canal de Donchian", "DC", "Bandas y canales", True, [P("length", "Periodo", 20)])
def _donchian(df, length):
    up = df["High"].rolling(length).max()
    lo = df["Low"].rolling(length).min()
    return [
        Out("Superior", up, color="#42a5f5", width=1.2),
        Out("Media", (up + lo) / 2, color="#ff7043", width=1.2),
        Out("Inferior", lo, color="#42a5f5", width=1.2),
        Out("Relleno", up, kind="fill", y2=lo, color="#42a5f514", show_value=False),
    ]


@indicator("envelope", "Envolventes (Envelopes)", "ENV", "Bandas y canales", True,
           [P("length", "Periodo", 20), P("pct", "Porcentaje %", 2.5, 0.1, 50, 0.1)])
def _envelope(df, length, pct):
    mid = sma(df["Close"], length)
    return [
        Out("Base", mid, color="#ff9800", width=1.2),
        Out("Superior", mid * (1 + pct / 100), color="#ab47bc", width=1.2),
        Out("Inferior", mid * (1 - pct / 100), color="#ab47bc", width=1.2),
    ]


@indicator("ichimoku", "Ichimoku Kinko Hyo", "Ichimoku", "Bandas y canales", True,
           [P("tenkan", "Tenkan-sen", 9), P("kijun", "Kijun-sen", 26), P("senkou", "Senkou B", 52),
            P("disp", "Desplazamiento", 26)],
           description="Tenkan, Kijun, nube (Senkou A/B desplazadas al futuro) y Chikou.")
def _ichimoku(df, tenkan, kijun, senkou, disp):
    h, l, c = df["High"], df["Low"], df["Close"]

    def mid(n):
        return (h.rolling(n).max() + l.rolling(n).min()) / 2

    t, k = mid(tenkan), mid(kijun)
    a = (t + k) / 2
    b = mid(senkou)
    off = disp - 1
    return [
        Out("Tenkan", t, color="#2962ff", width=1.2),
        Out("Kijun", k, color="#b71c1c", width=1.2),
        Out("Chikou", c, color="#43a047", width=1.0, offset=-off),
        Out("Senkou A", a, color="#a5d6a7", width=1.0, offset=off),
        Out("Senkou B", b, color="#ef9a9a", width=1.0, offset=off),
        Out("Nube", a, kind="cloud", y2=b, color="#43a04733", color2="#e5393533", offset=off, show_value=False),
    ]


@indicator("psar", "Parabolic SAR", "SAR", "Tendencia", True,
           [P("start", "Inicio", 0.02, 0.001, 1, 0.01), P("inc", "Incremento", 0.02, 0.001, 1, 0.01),
            P("max", "Máximo", 0.2, 0.01, 1, 0.01)])
def _psar(df, start, inc, max):  # noqa: A002 - nombre del parámetro
    h, l = _np(df["High"]), _np(df["Low"])
    n = len(h)
    sar = np.full(n, np.nan)
    trend = np.zeros(n)
    if n >= 2:
        up, af, ep = True, start, h[0]
        sar[0] = l[0]
        trend[0] = 1
        for i in range(1, n):
            s = sar[i - 1] + af * (ep - sar[i - 1])
            if up:
                s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
                if l[i] < s:
                    up, s, ep, af = False, ep, l[i], start
                elif h[i] > ep:
                    ep, af = h[i], min(af + inc, max)
            else:
                s = np.max([s, h[i - 1], h[i - 2] if i > 1 else h[i - 1]])
                if h[i] > s:
                    up, s, ep, af = True, ep, h[i], start
                elif l[i] < ep:
                    ep, af = l[i], min(af + inc, max)
            sar[i] = s
            trend[i] = 1 if up else -1
    return [
        Out("Alcista", np.where(trend > 0, sar, np.nan), kind="dots", color=UP),
        Out("Bajista", np.where(trend < 0, sar, np.nan), kind="dots", color=DOWN),
    ]


@indicator("supertrend", "Supertrend", "ST", "Tendencia", True,
           [P("length", "Periodo ATR", 10), P("mult", "Multiplicador", 3.0, 0.1, 20, 0.1)])
def _supertrend(df, length, mult):
    hl2 = _np((df["High"] + df["Low"]) / 2)
    c = _np(df["Close"])
    a = _np(atr(df, length))
    upper, lower = hl2 + mult * a, hl2 - mult * a
    n = len(c)
    fu, fl, st = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    direction = np.zeros(n)
    for i in range(n):
        if not np.isfinite(a[i]):
            continue
        if i == 0 or not np.isfinite(fu[i - 1]):
            fu[i], fl[i], direction[i] = upper[i], lower[i], 1
        else:
            fu[i] = upper[i] if (upper[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
            fl[i] = lower[i] if (lower[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
            if direction[i - 1] < 0:
                direction[i] = 1 if c[i] > fu[i] else -1
            else:
                direction[i] = -1 if c[i] < fl[i] else 1
        st[i] = fl[i] if direction[i] > 0 else fu[i]
    return [
        Out("Alcista", np.where(direction > 0, st, np.nan), color=UP, width=2),
        Out("Bajista", np.where(direction < 0, st, np.nan), color=DOWN, width=2),
    ]


@indicator("chandelier", "Chandelier Exit", "CE", "Tendencia", True,
           [P("length", "Periodo", 22), P("mult", "Multiplicador ATR", 3.0, 0.1, 20, 0.1)])
def _chandelier(df, length, mult):
    a = atr(df, length)
    return [
        Out("Largo", df["High"].rolling(length).max() - mult * a, color=UP, width=1.2, style="dash"),
        Out("Corto", df["Low"].rolling(length).min() + mult * a, color=DOWN, width=1.2, style="dash"),
    ]


@indicator("vwap", "VWAP (precio medio ponderado por volumen)", "VWAP", "Volumen", True,
           [P("anchor", "Anclaje", "Sesión", options=["Sesión", "Semana", "Mes", "Año"])])
def _vwap(df, anchor):
    idx = df.index
    key = {
        "Sesión": idx.date,
        "Semana": idx.to_period("W").astype(str),
        "Mes": idx.to_period("M").astype(str),
        "Año": idx.year,
    }[anchor]
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = (tp * df["Volume"]).groupby(key).cumsum()
    vv = df["Volume"].groupby(key).cumsum()
    return [Out("VWAP", _safe_div(pv, vv), color="#e040fb", width=1.6)]


@indicator("zigzag", "ZigZag", "ZZ", "Tendencia", True,
           [P("dev", "Desviación %", 5.0, 0.1, 100, 0.5)], description="Une máximos y mínimos relevantes.")
def _zigzag(df, dev):
    h, l = _np(df["High"]), _np(df["Low"])
    n = len(h)
    d = dev / 100
    piv = np.full(n, np.nan)
    if n < 2:
        return [Out("ZigZag", piv)]
    direction, hi, lo, ext = 0, 0, 0, 0
    for i in range(n):
        if direction == 0:
            if h[i] >= h[hi]:
                hi = i
            if l[i] <= l[lo]:
                lo = i
            if h[hi] >= l[lo] * (1 + d) and hi > lo:
                piv[lo] = l[lo]
                direction, ext = 1, hi
            elif l[lo] <= h[hi] * (1 - d) and lo > hi:
                piv[hi] = h[hi]
                direction, ext = -1, lo
        elif direction == 1:
            if h[i] >= h[ext]:
                ext = i
            elif l[i] <= h[ext] * (1 - d):
                piv[ext] = h[ext]
                direction, ext = -1, i
        else:
            if l[i] <= l[ext]:
                ext = i
            elif h[i] >= l[ext] * (1 + d):
                piv[ext] = l[ext]
                direction, ext = 1, i
    if direction != 0:
        piv[ext] = h[ext] if direction == 1 else l[ext]
    line = pd.Series(piv).interpolate(limit_area="inside")
    return [Out("ZigZag", line, color="#ffeb3b", width=1.6)]


@indicator("fractals", "Fractales de Williams", "Fractales", "Tendencia", True, [P("n", "Velas a cada lado", 2, 1, 20)])
def _fractals(df, n):
    h, l = df["High"], df["Low"]
    w = 2 * n + 1
    top = h.where(h == h.rolling(w, center=True).max())
    bot = l.where(l == l.rolling(w, center=True).min())
    rng = (h - l).rolling(20, min_periods=1).mean() * 0.3
    return [
        Out("Superior", top + rng, kind="dots", color=DOWN),
        Out("Inferior", bot - rng, kind="dots", color=UP),
    ]


@indicator("pivots", "Puntos pivote clásicos (periodo previo)", "Pivotes", "Bandas y canales", True,
           [P("period", "Periodo base", "Semana", options=["Día", "Semana", "Mes"])])
def _pivots(df, period):
    rule = {"Día": "D", "Semana": "W", "Mes": "M"}[period]
    key = df.index.to_period(rule)
    g = df.groupby(key).agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last"))
    prev = g.shift()
    pp = (prev.H + prev.L + prev.C) / 3
    lv = {
        "PP": pp, "R1": 2 * pp - prev.L, "S1": 2 * pp - prev.H,
        "R2": pp + (prev.H - prev.L), "S2": pp - (prev.H - prev.L),
    }
    colors = {"PP": "#ffb74d", "R1": DOWN, "R2": DOWN, "S1": UP, "S2": UP}
    outs = []
    for name, ser in lv.items():
        y = ser.reindex(key).to_numpy(dtype=float)
        # cortar la línea al cambiar de periodo
        brk = np.r_[False, key[1:] != key[:-1]]
        y = y.copy()
        y[brk] = np.nan
        outs.append(Out(name, y, color=colors[name], width=1.0, style="dash"))
    return outs


# --------------------------------------------------------------------------- #
# Volumen (paneles)
# --------------------------------------------------------------------------- #
@indicator("volume", "Volumen", "Vol", "Volumen", False, [P("ma", "Media del volumen", 20)])
def _volume(df, ma):
    v = df["Volume"]
    return [
        Out("Volumen", v, kind="hist", mask=(df["Close"] >= df["Open"]).to_numpy(),
            color="#26a69a90", color2="#ef535090"),
        Out("Media", sma(v, ma), color="#ffb74d", width=1.2),
    ]


@indicator("obv", "On Balance Volume (OBV)", "OBV", "Volumen", False, [P("ma", "Media de señal", 20)])
def _obv(df, ma):
    o = (np.sign(df["Close"].diff()).fillna(0) * df["Volume"]).cumsum()
    return [Out("OBV", o, color="#2962ff"), Out("Media", sma(o, ma), color="#ff9800", width=1.0)]


@indicator("mfi", "Money Flow Index (MFI)", "MFI", "Volumen", False, [P("length", "Periodo", 14)],
           levels=[20, 80], yrange=(0, 100))
def _mfi(df, length):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    mf = tp * df["Volume"]
    d = tp.diff()
    pos = mf.where(d > 0, 0).rolling(length).sum()
    neg = mf.where(d < 0, 0).rolling(length).sum()
    return [Out("MFI", 100 - 100 / (1 + _safe_div(pos, neg)), color="#ab47bc")]


@indicator("cmf", "Chaikin Money Flow (CMF)", "CMF", "Volumen", False, [P("length", "Periodo", 20)], levels=[0])
def _cmf(df, length):
    h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
    mfm = _safe_div((c - l) - (h - c), h - l).fillna(0)
    return [Out("CMF", _safe_div((mfm * v).rolling(length).sum(), v.rolling(length).sum()), kind="hist")]


def _ad_line(df):
    h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
    mfm = _safe_div((c - l) - (h - c), h - l).fillna(0)
    return (mfm * v).cumsum()


@indicator("ad", "Acumulación / Distribución (A/D)", "A/D", "Volumen", False)
def _ad(df):
    return [Out("A/D", _ad_line(df), color="#26c6da")]


@indicator("chaikin_osc", "Oscilador de Chaikin", "ChaikinOsc", "Volumen", False,
           [P("fast", "Rápida", 3), P("slow", "Lenta", 10)], levels=[0])
def _chaikin_osc(df, fast, slow):
    ad = _ad_line(df)
    return [Out("Chaikin", ema(ad, fast) - ema(ad, slow), color="#ec407a")]


@indicator("force", "Índice de Fuerza (Force Index)", "FI", "Volumen", False, [P("length", "Periodo", 13)], levels=[0])
def _force(df, length):
    return [Out("FI", ema(df["Close"].diff() * df["Volume"], length), kind="hist")]


@indicator("eom", "Ease of Movement (EOM)", "EOM", "Volumen", False,
           [P("length", "Periodo", 14), P("div", "Divisor", 10000, 1, 10 ** 12)], levels=[0])
def _eom(df, length, div):
    h, l, v = df["High"], df["Low"], df["Volume"]
    dm = (h + l) / 2 - (h.shift() + l.shift()) / 2
    box = _safe_div(v / div, h - l)
    return [Out("EOM", sma(_safe_div(dm, box), length), color="#66bb6a")]


@indicator("pvt", "Price Volume Trend (PVT)", "PVT", "Volumen", False)
def _pvt(df):
    return [Out("PVT", (df["Close"].pct_change().fillna(0) * df["Volume"]).cumsum(), color="#7e57c2")]


@indicator("klinger", "Oscilador de Klinger", "KVO", "Volumen", False,
           [P("fast", "Rápida", 34), P("slow", "Lenta", 55), P("signal", "Señal", 13)], levels=[0])
def _klinger(df, fast, slow, signal):
    hlc = df["High"] + df["Low"] + df["Close"]
    sv = np.where(hlc.diff() >= 0, 1, -1) * df["Volume"]
    sv = pd.Series(sv, index=df.index)
    kvo = ema(sv, fast) - ema(sv, slow)
    return [Out("KVO", kvo, color="#2962ff"), Out("Señal", ema(kvo, signal), color="#ff6d00")]


# --------------------------------------------------------------------------- #
# Osciladores / momento (paneles)
# --------------------------------------------------------------------------- #
@indicator("rsi", "Índice de Fuerza Relativa (RSI)", "RSI", "Osciladores / Momento", False,
           [P("length", "Periodo", 14), P("ma", "Media del RSI (0 = no)", 0, 0, 500), SRC],
           levels=[30, 50, 70], yrange=(0, 100), description="Sobrecompra > 70, sobreventa < 30.")
def _rsi(df, length, ma, source):
    r = rsi(src(df, source), length)
    outs = [Out("RSI", r, color="#7e57c2")]
    if ma > 0:
        outs.append(Out("Media", sma(r, ma), color="#ffb74d", width=1.0))
    return outs


@indicator("macd", "MACD", "MACD", "Osciladores / Momento", False,
           [P("fast", "Rápida", 12), P("slow", "Lenta", 26), P("signal", "Señal", 9), SRC], levels=[0])
def _macd(df, fast, slow, signal, source):
    s = src(df, source)
    m = ema(s, fast) - ema(s, slow)
    sig = ema(m, signal)
    return [
        Out("Histograma", m - sig, kind="hist", color="#26a69aa0", color2="#ef5350a0"),
        Out("MACD", m, color="#2962ff"),
        Out("Señal", sig, color="#ff6d00"),
    ]


@indicator("stoch", "Estocástico", "Stoch", "Osciladores / Momento", False,
           [P("k", "%K", 14), P("smooth", "Suavizado %K", 3), P("d", "%D", 3)], levels=[20, 80], yrange=(0, 100))
def _stoch(df, k, smooth, d):
    ll = df["Low"].rolling(k).min()
    hh = df["High"].rolling(k).max()
    kk = sma(100 * _safe_div(df["Close"] - ll, hh - ll), smooth)
    return [Out("%K", kk, color="#2962ff"), Out("%D", sma(kk, d), color="#ff6d00")]


@indicator("stochrsi", "RSI Estocástico", "StochRSI", "Osciladores / Momento", False,
           [P("rsi_len", "Periodo RSI", 14), P("stoch_len", "Periodo estocástico", 14), P("k", "%K", 3), P("d", "%D", 3)],
           levels=[20, 80], yrange=(0, 100))
def _stochrsi(df, rsi_len, stoch_len, k, d):
    r = rsi(df["Close"], rsi_len)
    ll, hh = r.rolling(stoch_len).min(), r.rolling(stoch_len).max()
    kk = sma(100 * _safe_div(r - ll, hh - ll), k)
    return [Out("%K", kk, color="#2962ff"), Out("%D", sma(kk, d), color="#ff6d00")]


@indicator("cci", "Commodity Channel Index (CCI)", "CCI", "Osciladores / Momento", False,
           [P("length", "Periodo", 20)], levels=[-100, 0, 100])
def _cci(df, length):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    m = sma(tp, length)
    mad = tp.rolling(length).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return [Out("CCI", _safe_div(tp - m, 0.015 * mad), color="#26a69a")]


@indicator("willr", "Williams %R", "%R", "Osciladores / Momento", False, [P("length", "Periodo", 14)],
           levels=[-80, -20], yrange=(-100, 0))
def _willr(df, length):
    hh, ll = df["High"].rolling(length).max(), df["Low"].rolling(length).min()
    return [Out("%R", -100 * _safe_div(hh - df["Close"], hh - ll), color="#ab47bc")]


@indicator("roc", "Tasa de cambio (ROC)", "ROC", "Osciladores / Momento", False, [P("length", "Periodo", 12)], levels=[0])
def _roc(df, length):
    c = df["Close"]
    return [Out("ROC", 100 * (c / c.shift(length) - 1), color="#29b6f6")]


@indicator("mom", "Momentum", "MOM", "Osciladores / Momento", False, [P("length", "Periodo", 10)], levels=[0])
def _mom(df, length):
    return [Out("MOM", df["Close"] - df["Close"].shift(length), color="#26c6da")]


@indicator("trix", "TRIX", "TRIX", "Osciladores / Momento", False,
           [P("length", "Periodo", 15), P("signal", "Señal", 9)], levels=[0])
def _trix(df, length, signal):
    e = ema(ema(ema(df["Close"], length), length), length)
    t = 100 * e.pct_change()
    return [Out("TRIX", t, color="#ef5350"), Out("Señal", ema(t, signal), color="#42a5f5", width=1.0)]


@indicator("ao", "Awesome Oscillator", "AO", "Osciladores / Momento", False,
           [P("fast", "Rápida", 5), P("slow", "Lenta", 34)], levels=[0])
def _ao(df, fast, slow):
    hl2 = (df["High"] + df["Low"]) / 2
    ao = sma(hl2, fast) - sma(hl2, slow)
    return [Out("AO", ao, kind="hist", mask=(ao >= ao.shift()).to_numpy())]


@indicator("uo", "Oscilador Último (Ultimate)", "UO", "Osciladores / Momento", False,
           [P("s", "Corto", 7), P("m", "Medio", 14), P("l", "Largo", 28)], levels=[30, 70], yrange=(0, 100))
def _uo(df, s, m, l):
    pc = df["Close"].shift()
    low = pd.concat([df["Low"], pc], axis=1).min(axis=1)
    high = pd.concat([df["High"], pc], axis=1).max(axis=1)
    bp, tr = df["Close"] - low, high - low

    def avg(n):
        return _safe_div(bp.rolling(n).sum(), tr.rolling(n).sum())

    return [Out("UO", 100 * (4 * avg(s) + 2 * avg(m) + avg(l)) / 7, color="#f06292")]


@indicator("aroon", "Aroon", "Aroon", "Tendencia", False, [P("length", "Periodo", 25)], yrange=(0, 100), levels=[50])
def _aroon(df, length):
    up = df["High"].rolling(length + 1).apply(np.argmax, raw=True) * 100 / length
    dn = df["Low"].rolling(length + 1).apply(np.argmin, raw=True) * 100 / length
    return [Out("Arriba", up, color=UP), Out("Abajo", dn, color=DOWN)]


@indicator("aroon_osc", "Oscilador Aroon", "AroonOsc", "Tendencia", False, [P("length", "Periodo", 25)],
           levels=[0], yrange=(-100, 100))
def _aroon_osc(df, length):
    up = df["High"].rolling(length + 1).apply(np.argmax, raw=True) * 100 / length
    dn = df["Low"].rolling(length + 1).apply(np.argmin, raw=True) * 100 / length
    return [Out("Osc", up - dn, kind="hist")]


@indicator("adx", "ADX / DMI (Índice direccional)", "ADX", "Tendencia", False,
           [P("length", "Periodo DI", 14), P("smooth", "Suavizado ADX", 14)], levels=[20, 25])
def _adx(df, length, smooth):
    up = df["High"].diff()
    dn = -df["Low"].diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    ndm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = rma(true_range(df), length)
    pdi = 100 * _safe_div(rma(pdm, length), tr)
    ndi = 100 * _safe_div(rma(ndm, length), tr)
    dx = 100 * _safe_div((pdi - ndi).abs(), pdi + ndi)
    return [Out("ADX", rma(dx, smooth), color="#ffd600", width=1.8),
            Out("+DI", pdi, color=UP, width=1.0), Out("-DI", ndi, color=DOWN, width=1.0)]


@indicator("ppo", "Oscilador de precio porcentual (PPO)", "PPO", "Osciladores / Momento", False,
           [P("fast", "Rápida", 12), P("slow", "Lenta", 26), P("signal", "Señal", 9)], levels=[0])
def _ppo(df, fast, slow, signal):
    c = df["Close"]
    p = 100 * _safe_div(ema(c, fast) - ema(c, slow), ema(c, slow))
    s = ema(p, signal)
    return [Out("Histograma", p - s, kind="hist", color="#26a69aa0", color2="#ef5350a0"),
            Out("PPO", p, color="#2962ff"), Out("Señal", s, color="#ff6d00")]


@indicator("tsi", "True Strength Index (TSI)", "TSI", "Osciladores / Momento", False,
           [P("long", "Largo", 25), P("short", "Corto", 13), P("signal", "Señal", 7)], levels=[0])
def _tsi(df, long, short, signal):
    pc = df["Close"].diff()
    t = 100 * _safe_div(ema(ema(pc, long), short), ema(ema(pc.abs(), long), short))
    return [Out("TSI", t, color="#2962ff"), Out("Señal", ema(t, signal), color="#ef5350")]


@indicator("vortex", "Indicador Vortex", "VI", "Tendencia", False, [P("length", "Periodo", 14)], levels=[1])
def _vortex(df, length):
    vp = (df["High"] - df["Low"].shift()).abs().rolling(length).sum()
    vm = (df["Low"] - df["High"].shift()).abs().rolling(length).sum()
    tr = true_range(df).rolling(length).sum()
    return [Out("VI+", _safe_div(vp, tr), color=UP), Out("VI-", _safe_div(vm, tr), color=DOWN)]


@indicator("elder", "Elder Ray (Bull/Bear Power)", "Elder", "Osciladores / Momento", False,
           [P("length", "Periodo", 13)], levels=[0])
def _elder(df, length):
    e = ema(df["Close"], length)
    return [Out("Bull", df["High"] - e, kind="hist", color="#26a69a90", color2="#26a69a90"),
            Out("Bear", df["Low"] - e, kind="hist", color="#ef535090", color2="#ef535090")]


@indicator("dpo", "Detrended Price Oscillator (DPO)", "DPO", "Osciladores / Momento", False,
           [P("length", "Periodo", 21)], levels=[0])
def _dpo(df, length):
    return [Out("DPO", df["Close"] - sma(df["Close"], length).shift(length // 2 + 1), color="#8d6e63")]


@indicator("coppock", "Curva de Coppock", "Coppock", "Osciladores / Momento", False,
           [P("wma", "WMA", 10), P("long", "ROC largo", 14), P("short", "ROC corto", 11)], levels=[0])
def _coppock(df, wma, long, short):  # noqa: F811 - parámetro con nombre wma
    c = df["Close"]
    r = 100 * (c / c.shift(long) - 1) + 100 * (c / c.shift(short) - 1)
    w = np.arange(1, wma + 1, dtype=float)
    return [Out("Coppock", rolling_dot(r, w / w.sum()), kind="hist")]


@indicator("fisher", "Transformada de Fisher", "Fisher", "Osciladores / Momento", False,
           [P("length", "Periodo", 9)], levels=[0])
def _fisher(df, length):
    hl2 = (df["High"] + df["Low"]) / 2
    mx, mn = _np(hl2.rolling(length).max()), _np(hl2.rolling(length).min())
    v = _np(hl2)
    n = len(v)
    val = np.zeros(n)
    fish = np.full(n, np.nan)
    prev_f = 0.0
    for i in range(n):
        if not (np.isfinite(mx[i]) and np.isfinite(mn[i])):
            continue
        rng = mx[i] - mn[i]
        x = 0.66 * (((v[i] - mn[i]) / rng if rng else 0.5) - 0.5) + 0.67 * (val[i - 1] if i else 0)
        x = min(max(x, -0.999), 0.999)
        val[i] = x
        prev_f = 0.5 * np.log((1 + x) / (1 - x)) + 0.5 * prev_f
        fish[i] = prev_f
    f = pd.Series(fish, index=df.index)
    return [Out("Fisher", f, color="#2962ff"), Out("Disparo", f.shift(), color="#ff6d00", width=1.0)]


@indicator("kst", "Know Sure Thing (KST)", "KST", "Osciladores / Momento", False, [P("signal", "Señal", 9)], levels=[0])
def _kst(df, signal):
    c = df["Close"]

    def roc(n):
        return 100 * (c / c.shift(n) - 1)

    k = sma(roc(10), 10) + 2 * sma(roc(15), 10) + 3 * sma(roc(20), 10) + 4 * sma(roc(30), 15)
    return [Out("KST", k, color="#26a69a"), Out("Señal", sma(k, signal), color="#ef5350")]


@indicator("cmo", "Chande Momentum Oscillator (CMO)", "CMO", "Osciladores / Momento", False,
           [P("length", "Periodo", 9)], levels=[-50, 0, 50], yrange=(-100, 100))
def _cmo(df, length):
    d = df["Close"].diff()
    up = d.clip(lower=0).rolling(length).sum()
    dn = (-d.clip(upper=0)).rolling(length).sum()
    return [Out("CMO", 100 * _safe_div(up - dn, up + dn), color="#29b6f6")]


@indicator("rvi", "Relative Vigor Index (RVI)", "RVGI", "Osciladores / Momento", False,
           [P("length", "Periodo", 10)], levels=[0])
def _rvi(df, length):
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]

    def sym(s):
        return (s + 2 * s.shift(1) + 2 * s.shift(2) + s.shift(3)) / 6

    r = _safe_div(sym(c - o).rolling(length).sum(), sym(h - l).rolling(length).sum())
    return [Out("RVGI", r, color=UP), Out("Señal", sym(r), color=DOWN)]


@indicator("bop", "Balance of Power (BOP)", "BOP", "Osciladores / Momento", False,
           [P("length", "Suavizado", 14)], levels=[0])
def _bop(df, length):
    b = _safe_div(df["Close"] - df["Open"], df["High"] - df["Low"]).fillna(0)
    return [Out("BOP", sma(b, length), kind="hist")]


@indicator("squeeze", "Squeeze Momentum (LazyBear)", "SQZ", "Osciladores / Momento", False,
           [P("bb_len", "Periodo BB", 20), P("bb_mult", "Mult. BB", 2.0, 0.1, 10, 0.1),
            P("kc_len", "Periodo KC", 20), P("kc_mult", "Mult. KC", 1.5, 0.1, 10, 0.1)],
           description="Puntos rojos: compresión de volatilidad (BB dentro de Keltner).")
def _squeeze(df, bb_len, bb_mult, kc_len, kc_mult):
    c = df["Close"]
    basis, dev = sma(c, bb_len), bb_mult * stdev(c, bb_len)
    ma, rng = sma(c, kc_len), sma(true_range(df), kc_len)
    on = ((basis - dev) > (ma - rng * kc_mult)) & ((basis + dev) < (ma + rng * kc_mult))
    mid = ((df["High"].rolling(kc_len).max() + df["Low"].rolling(kc_len).min()) / 2 + sma(c, kc_len)) / 2
    val = linreg(c - mid, kc_len)
    valid = val.notna().to_numpy()
    on = on.to_numpy()
    return [
        Out("Momento", val, kind="hist"),
        Out("Squeeze", np.where(on & valid, 0.0, np.nan), kind="dots", color="#ff1744", show_value=False),
        Out("Libre", np.where(~on & valid, 0.0, np.nan), kind="dots", color="#78909c", show_value=False),
    ]


@indicator("schaff", "Schaff Trend Cycle (STC)", "STC", "Osciladores / Momento", False,
           [P("cycle", "Ciclo", 10), P("fast", "Rápida", 23), P("slow", "Lenta", 50)],
           levels=[25, 75], yrange=(0, 100))
def _schaff(df, cycle, fast, slow):
    m = ema(df["Close"], fast) - ema(df["Close"], slow)

    def stoch_smooth(s):
        ll, hh = s.rolling(cycle).min(), s.rolling(cycle).max()
        k = (100 * _safe_div(s - ll, hh - ll)).ffill()
        return k.ewm(alpha=0.5, adjust=False).mean()

    return [Out("STC", stoch_smooth(stoch_smooth(m)), color="#ffca28")]


@indicator("connors", "Connors RSI", "CRSI", "Osciladores / Momento", False,
           [P("rsi_len", "RSI", 3), P("streak", "RSI de racha", 2), P("rank", "Percentil ROC", 100)],
           levels=[10, 90], yrange=(0, 100))
def _connors(df, rsi_len, streak, rank):
    c = _np(df["Close"])
    st = np.zeros(len(c))
    for i in range(1, len(c)):
        if c[i] > c[i - 1]:
            st[i] = st[i - 1] + 1 if st[i - 1] > 0 else 1
        elif c[i] < c[i - 1]:
            st[i] = st[i - 1] - 1 if st[i - 1] < 0 else -1
    ret = df["Close"].pct_change()
    pr = ret.rolling(rank + 1).apply(lambda x: 100 * np.mean(x[:-1] < x[-1]), raw=True)
    crsi = (rsi(df["Close"], rsi_len) + rsi(pd.Series(st, index=df.index), streak) + pr) / 3
    return [Out("CRSI", crsi, color="#26c6da")]


# --------------------------------------------------------------------------- #
# Volatilidad (paneles)
# --------------------------------------------------------------------------- #
@indicator("atr", "Average True Range (ATR)", "ATR", "Volatilidad", False, [P("length", "Periodo", 14)])
def _atr(df, length):
    return [Out("ATR", atr(df, length), color="#ef5350")]


@indicator("natr", "ATR normalizado (%)", "NATR", "Volatilidad", False, [P("length", "Periodo", 14)])
def _natr(df, length):
    return [Out("NATR %", 100 * _safe_div(atr(df, length), df["Close"]), color="#ff7043")]


@indicator("stddev", "Desviación estándar", "StdDev", "Volatilidad", False, [P("length", "Periodo", 20)])
def _stddev(df, length):
    return [Out("StdDev", stdev(df["Close"], length), color="#42a5f5")]


@indicator("hv", "Volatilidad histórica (anualizada %)", "HV", "Volatilidad", False,
           [P("length", "Periodo", 20), P("annual", "Periodos por año", 252, 1, 600000)])
def _hv(df, length, annual):
    lr = np.log(df["Close"] / df["Close"].shift())
    return [Out("HV %", 100 * lr.rolling(length).std() * np.sqrt(annual), color="#ab47bc")]


@indicator("bbpct", "%B de Bollinger", "%B", "Volatilidad", False,
           [P("length", "Periodo", 20), P("mult", "Desviaciones", 2.0, 0.1, 10, 0.1)], levels=[0, 0.5, 1])
def _bbpct(df, length, mult):
    c = df["Close"]
    mid, sd = sma(c, length), stdev(c, length)
    up, lo = mid + mult * sd, mid - mult * sd
    return [Out("%B", _safe_div(c - lo, up - lo), color="#26a69a")]


@indicator("bbw", "Ancho de Bollinger (BBW)", "BBW", "Volatilidad", False,
           [P("length", "Periodo", 20), P("mult", "Desviaciones", 2.0, 0.1, 10, 0.1)])
def _bbw(df, length, mult):
    c = df["Close"]
    mid, sd = sma(c, length), stdev(c, length)
    return [Out("BBW %", 100 * _safe_div(2 * mult * sd, mid), color="#2196f3")]


@indicator("chop", "Índice de Choppiness", "CHOP", "Volatilidad", False, [P("length", "Periodo", 14)],
           levels=[38.2, 61.8], yrange=(0, 100))
def _chop(df, length):
    s = true_range(df).rolling(length).sum()
    rng = df["High"].rolling(length).max() - df["Low"].rolling(length).min()
    return [Out("CHOP", 100 * np.log10(_safe_div(s, rng)) / np.log10(length), color="#ffb74d")]


@indicator("mass", "Mass Index", "Mass", "Volatilidad", False, [P("fast", "EMA", 9), P("length", "Suma", 25)],
           levels=[26.5, 27])
def _mass(df, fast, length):
    r = df["High"] - df["Low"]
    e1 = ema(r, fast)
    return [Out("Mass", _safe_div(e1, ema(e1, fast)).rolling(length).sum(), color="#8d6e63")]


@indicator("ulcer", "Ulcer Index", "UI", "Volatilidad", False, [P("length", "Periodo", 14)])
def _ulcer(df, length):
    c = df["Close"]
    mx = c.rolling(length).max()
    dd = 100 * (c - mx) / mx
    return [Out("Ulcer", np.sqrt((dd ** 2).rolling(length).mean()), color="#ef5350")]


# --------------------------------------------------------------------------- #
# Fibonacci y Gann
# --------------------------------------------------------------------------- #
FIB = "Fibonacci y Gann"
SI_NO = ["Sí", "No"]
FIB_LEVELS = [(0.0, "#787b86"), (0.236, "#f23645"), (0.382, "#ff9800"), (0.5, "#4caf50"),
              (0.618, "#089981"), (0.786, "#00bcd4"), (1.0, "#787b86")]
FIB_EXT = [(1.272, "#9c27b0"), (1.618, "#2962ff"), (2.0, "#e91e63"), (2.618, "#673ab7")]


def _p(v: float) -> str:
    return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.6g}"


def _swing(df, length):
    """Índices y precios del máximo y mínimo dentro de las últimas ``length`` velas."""
    n = len(df)
    s = max(0, n - int(length))
    h, l = _np(df["High"]), _np(df["Low"])
    hi = s + int(np.nanargmax(h[s:]))
    lo = s + int(np.nanargmin(l[s:]))
    return hi, lo, h[hi], l[lo]


def _hline(n, start, value, end=None):
    y = np.full(n, np.nan)
    y[start:end] = value
    return y


@indicator("fib_retr", "Retroceso de Fibonacci (automático)", "Fibo", FIB, True,
           [P("length", "Velas a analizar", 150, 10, 100000),
            P("ext", "Extensiones", "Sí", options=SI_NO), P("fill", "Relleno", "Sí", options=SI_NO)],
           description="Toma el máximo y el mínimo de las últimas N velas y traza los niveles 23.6%, 38.2%, "
                       "50%, 61.8% y 78.6%. Si el mínimo ocurrió primero (tramo alcista) los retrocesos se "
                       "miden hacia abajo desde el máximo; si no, hacia arriba desde el mínimo. Las "
                       "extensiones (127.2%, 161.8%, 200%, 261.8%) proyectan objetivos más allá del tramo.")
def _fib_retr(df, length, ext, fill):
    n = len(df)
    hi, lo, ph, pl = _swing(df, length)
    rng = ph - pl
    up = lo < hi
    start = min(hi, lo)
    outs, prev = [], None
    for r, color in FIB_LEVELS:
        v = ph - r * rng if up else pl + r * rng
        y = _hline(n, start, v)
        if fill == "Sí" and prev is not None:
            outs.append(Out("", prev, kind="fill", y2=y, color=color + "14", show_value=False))
        outs.append(Out(f"{r * 100:g}%", y, color=color, width=1.0, show_value=False,
                        text=f"{r * 100:g}%  ({_p(v)})"))
        prev = y
    if ext == "Sí":
        for r, color in FIB_EXT:
            v = pl + r * rng if up else ph - r * rng
            outs.append(Out(f"{r * 100:g}%", _hline(n, start, v), color=color, width=1.0, style="dash",
                            show_value=False, autoscale=False, text=f"{r * 100:g}%  ({_p(v)})"))
    return outs


@indicator("fib_time", "Zonas de tiempo de Fibonacci", "FiboTiempo", FIB, True,
           [P("length", "Velas a analizar", 150, 10, 100000),
            P("anchor", "Iniciar en", "Primer pivote", options=["Primer pivote", "Mínimo", "Máximo"])],
           description="Líneas verticales a 1, 2, 3, 5, 8, 13, 21, 34, 55, 89… velas desde un pivote; "
                       "marcan posibles momentos de giro. Se proyectan también hacia el futuro.")
def _fib_time(df, length, anchor):
    hi, lo, _, _ = _swing(df, length)
    start = {"Primer pivote": min(hi, lo), "Mínimo": lo, "Máximo": hi}[anchor]
    fibs = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597]
    limit = len(df) + 75
    pos = [start + f for f in fibs if start + f <= limit]
    return [Out("Zonas", pos, kind="vline", color="#7e57c2", width=1.0, style="dash", show_value=False,
                labels=[str(f) for f in fibs[:len(pos)]])]


@indicator("fib_bands", "Bandas de Fibonacci (ATR)", "FiboBandas", FIB, True,
           [P("length", "Media", 20), P("atr_len", "Periodo ATR", 14)],
           description="Media móvil ± ATR × 1.618, 2.618 y 4.236.")
def _fib_bands(df, length, atr_len):
    mid = sma(df["Close"], length)
    a = atr(df, atr_len)
    outs = [Out("Base", mid, color="#ff9800", width=1.2)]
    for m, c in ((1.618, "#089981"), (2.618, "#2962ff"), (4.236, "#f23645")):
        outs.append(Out(f"+{m}", mid + m * a, color=c, width=1.0))
        outs.append(Out(f"-{m}", mid - m * a, color=c, width=1.0))
    return outs


GANN_ANGLES = [(8, 1, "#f23645"), (4, 1, "#ff5722"), (3, 1, "#ff9800"), (2, 1, "#cddc39"),
               (1, 1, "#ffd600"), (1, 2, "#4caf50"), (1, 3, "#00bcd4"), (1, 4, "#2962ff"), (1, 8, "#9c27b0")]


@indicator("gann_fan", "Abanico de Gann (Gann Fan)", "GannFan", FIB, True,
           [P("length", "Velas a analizar", 150, 10, 100000),
            P("scale", "Precio por vela (0 = auto)", 0.0, 0, 1e9, 0.01)],
           description="Líneas de Gann (8x1, 4x1, 3x1, 2x1, 1x1, 1x2, 1x3, 1x4, 1x8) desde el pivote del "
                       "periodo: desde el mínimo si el tramo es alcista, desde el máximo si es bajista. En "
                       "automático, la 1x1 une el pivote con el extremo opuesto.")
def _gann_fan(df, length, scale):
    n = len(df)
    hi, lo, ph, pl = _swing(df, length)
    up = lo < hi
    pivot, p0 = (lo, pl) if up else (hi, ph)
    unit = scale if scale > 0 else (ph - pl) / max(abs(hi - lo), 1)
    t = np.arange(n, dtype=float) - pivot
    sign = 1 if up else -1
    outs = []
    for a, b, color in GANN_ANGLES:
        y = p0 + sign * unit * a / b * t
        y[(t < 0) | (y <= 0)] = np.nan
        one = a == b
        outs.append(Out(f"{a}x{b}", y, color=color, width=1.6 if one else 1.0, show_value=one,
                        autoscale=False, text=f"{a}x{b}"))
    return outs


@indicator("gann_sq9", "Cuadrado de 9 de Gann (niveles)", "GannSq9", FIB, True,
           [P("base", "Precio base", "Último cierre", options=["Último cierre", "Mínimo del periodo", "Máximo del periodo"]),
            P("step", "Paso (grados)", 45, 1, 360), P("count", "Niveles por lado", 4, 1, 24),
            P("length", "Velas (periodo / ancho)", 100, 5, 100000),
            P("factor", "Escala de precio", "Auto", options=["Auto", "0.01", "0.1", "1", "10", "100", "1000", "10000"])],
           description="Soportes y resistencias del Cuadrado de 9: (√precio ± grados/180)². "
                       "Cada 360° equivale a +2 en la raíz cuadrada. Verde = soporte, rojo = resistencia. "
                       "Con precios altos (p. ej. BTC) se divide el precio entre una escala para que los "
                       "niveles no queden amontonados; «Auto» la lleva al rango 100–999.")
def _gann_sq9(df, base, step, count, length, factor):
    n = len(df)
    hi, lo, ph, pl = _swing(df, length)
    b = {"Último cierre": float(df["Close"].iloc[-1]), "Mínimo del periodo": pl, "Máximo del periodo": ph}[base]
    if factor == "Auto":
        f = 10.0 ** (np.floor(np.log10(b)) - 2) if b >= 1000 else 1.0
    else:
        f = float(factor)
    root = np.sqrt(b / f)
    start = max(0, n - int(length))
    outs = []
    for k in range(-count, count + 1):
        r = root + k * step / 180
        if r <= 0:
            continue
        v = r * r * f
        color = "#d1d4dc" if k == 0 else ("#f23645" if k > 0 else "#089981")
        outs.append(Out(f"{k * step:+g}°", _hline(n, start, v), color=color, width=1.2 if k == 0 else 1.0,
                        style="solid" if k == 0 else "dash", show_value=False, autoscale=False,
                        text=f"{k * step:+g}°  {_p(v)}"))
    return outs


@indicator("gann_hilo", "Gann HiLo Activator", "GannHiLo", FIB, True, [P("length", "Periodo", 3)],
           description="Media de máximos o de mínimos que cambia de lado cuando el cierre la cruza; "
                       "funciona como stop dinámico.")
def _gann_hilo(df, length):
    sh = _np(sma(df["High"], length))
    sl = _np(sma(df["Low"], length))
    c = _np(df["Close"])
    trend = np.zeros(len(c))
    for i in range(1, len(c)):
        if np.isfinite(sh[i - 1]) and c[i] > sh[i - 1]:
            trend[i] = 1
        elif np.isfinite(sl[i - 1]) and c[i] < sl[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    return [Out("Alcista", np.where(trend > 0, sl, np.nan), color=UP, width=1.8),
            Out("Bajista", np.where(trend < 0, sh, np.nan), color=DOWN, width=1.8)]


def by_category() -> dict[str, list[IndicatorDef]]:
    out = {c: [] for c in CATEGORIES}
    for d in REGISTRY.values():
        out.setdefault(d.category, []).append(d)
    for lst in out.values():
        lst.sort(key=lambda d: d.name.lower())
    return out
