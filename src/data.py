"""Descarga de datos de mercado con yfinance (Yahoo Finance)."""
from __future__ import annotations

import re
import warnings

warnings.filterwarnings("ignore", module="yfinance")

import numpy as np
import pandas as pd
import yfinance as yf

from . import macro

MARKETS = ["EE.UU.", "México (BMV)", "Asia", "Cripto", macro.MARKET, "Directo"]

# Bonos del Tesoro en Yahoo: rendimientos (^IRX 13 sem., ^FVX 5 años, ^TNX 10 años, ^TYX 30 años),
# futuros de rendimiento (2YY=F, 10Y=F), futuros de notas/bonos (ZT 2 años, ZN 10 años, ZB 30 años) y ETFs
YAHOO_BONDS = ["^IRX", "^FVX", "^TNX", "^TYX", "2YY=F", "10Y=F", "ZT=F", "ZN=F", "ZB=F", "SHY", "IEF", "TLT"]

# etiqueta: (intervalo de Yahoo, regla de remuestreo, ¿intradía?, periodo por defecto)
INTERVALS = {
    "1 minuto": ("1m", None, True, "5d"),
    "2 minutos": ("2m", None, True, "5d"),
    "5 minutos": ("5m", None, True, "1mo"),
    "15 minutos": ("15m", None, True, "1mo"),
    "30 minutos": ("30m", None, True, "1mo"),
    "1 hora": ("60m", None, True, "6mo"),
    "4 horas": ("60m", "4h", True, "1y"),
    "1 día": ("1d", None, False, "2y"),
    "1 semana": ("1wk", None, False, "10y"),
    "2 semanas": ("1wk", "2W", False, "10y"),
    "1 mes": ("1mo", None, False, "max"),
    "3 meses": ("1mo", "3M", False, "max"),
    "6 meses": ("1mo", "6M", False, "max"),
    "12 meses": ("1mo", "12M", False, "max"),
}

# (clave de Yahoo, etiqueta, días aproximados)
PERIODS = [
    ("1d", "1 día", 1), ("5d", "5 días", 5), ("1mo", "1 mes", 31), ("3mo", "3 meses", 92),
    ("6mo", "6 meses", 183), ("1y", "1 año", 365), ("2y", "2 años", 730), ("5y", "5 años", 1827),
    ("10y", "10 años", 3653), ("ytd", "Año en curso", 365), ("max", "Máximo", 10 ** 7),
]
_PERIOD_DAYS = {k: d for k, _, d in PERIODS}

# Límites de historia que impone Yahoo a los datos intradía
MAX_DAYS = {"1m": 7, "2m": 59, "5m": 59, "15m": 59, "30m": 59, "60m": 729}

POPULAR = {
    "México (BMV)": [
        "^MXX", "AMXB.MX", "WALMEX.MX", "GFNORTEO.MX", "FEMSAUBD.MX", "CEMEXCPO.MX", "GMEXICOB.MX",
        "BIMBOA.MX", "GAPB.MX", "ASURB.MX", "OMAB.MX", "KOFUBL.MX", "AC.MX", "ALSEA.MX", "ELEKTRA.MX",
        "GCARSOA1.MX", "PINFRA.MX", "TLEVISACPO.MX", "LABB.MX", "ORBIA.MX", "KIMBERA.MX", "GRUMAB.MX",
        "BOLSAA.MX", "Q.MX", "VESTA.MX", "CUERVO.MX", "LIVEPOLC-1.MX", "PE&OLES.MX", "MEGACPO.MX",
        "GENTERA.MX", "BBAJIOO.MX", "GFINBURO.MX", "CHDRAUIB.MX", "MXN=X",
    ],
    "EE.UU.": [
        "^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "SPY", "QQQ", "DIA", "IWM", "AAPL", "MSFT", "NVDA",
        "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V", "MA", "NFLX", "AMD", "INTC", "AVGO",
        "COST", "KO", "PEP", "DIS", "BA", "XOM", "PLTR", "UBER", "GC=F", "SI=F", "CL=F",
    ],
    "Asia": [
        # índices: Japón, China / Hong Kong, Corea y otros de la región
        "^N225", "^HSI", "^HSCE", "000001.SS", "399001.SZ", "000300.SS", "^KS11", "^KQ11", "^TWII",
        "^STI", "^BSESN", "^NSEI", "^AXJO", "^JKSE",
        # acciones y divisas
        "7203.T", "6758.T", "9984.T", "8306.T", "0700.HK", "9988.HK", "1810.HK", "3690.HK", "0005.HK",
        "600519.SS", "005930.KS", "000660.KS", "005380.KS", "2330.TW", "JPY=X", "CNY=X", "KRW=X",
    ],
    "Cripto": [
        "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "ADA-USD", "DOGE-USD", "AVAX-USD",
        "DOT-USD", "LINK-USD", "LTC-USD", "TRX-USD", "SHIB-USD", "TON11419-USD", "XLM-USD", "BCH-USD",
        "UNI7083-USD", "ATOM-USD", "NEAR-USD", "BTC-EUR", "ETH-BTC",
    ],
    macro.MARKET: [f"MACRO:{k}" for k in macro.CATALOG] + YAHOO_BONDS,
}


ASIA_INDEXES = {"^N225", "^HSI", "^HSCE", "^KS11", "^KQ11", "^TWII", "^STI", "^BSESN", "^NSEI", "^AXJO", "^JKSE"}


def resolve_symbol(text: str, market: str) -> str:
    """Convierte lo que escribe el usuario en un símbolo de Yahoo.

    México: WALMEX -> WALMEX.MX · Cripto: BTC -> BTC-USD · EE.UU./Asia/Directo: sin cambios
    (en Asia el sufijo depende de la bolsa: .T Tokio, .HK Hong Kong, .SS/.SZ China, .KS Corea).
    """
    s = text.strip().upper().replace(" ", "")
    if not s:
        return s
    already_full = any(ch in s for ch in ".-^=:")
    if market == macro.MARKET and not already_full and s not in YAHOO_BONDS:
        # M2, FED_BALANCE, UST10Y… del catálogo; cualquier otro ID se busca en FRED
        return f"MACRO:{s}" if s in macro.CATALOG else f"FRED:{s}"
    if market.startswith("México") and not already_full:
        s += ".MX"
    elif market == "Cripto" and not already_full:
        s += "-USD"
    return s


def guess_market(symbol: str, meta: dict | None = None) -> str:
    t = (meta or {}).get("instrumentType", "")
    if t == "CRYPTOCURRENCY":
        return "Cripto"
    s = symbol.upper()
    if macro.is_macro(s) or s in YAHOO_BONDS:
        return macro.MARKET
    if s.endswith(".MX") or s == "^MXX" or s.startswith("MXN"):
        return "México (BMV)"
    asia_suffix = (".T", ".HK", ".SS", ".SZ", ".KS", ".KQ", ".TW", ".SI", ".BO", ".NS", ".AX", ".JK")
    if s.endswith(asia_suffix) or s in ASIA_INDEXES or s in ("JPY=X", "CNY=X", "KRW=X", "HKD=X"):
        return "Asia"
    if "-" in s and s.split("-")[-1] in ("USD", "USDT", "MXN", "EUR", "BTC", "ETH"):
        return "Cripto"
    return "EE.UU."


def clamp_period(yf_interval: str, period: str) -> str:
    limit = MAX_DAYS.get(yf_interval)
    if limit is None or _PERIOD_DAYS.get(period, 0) <= limit:
        return period
    allowed = [k for k, _, d in PERIODS if d <= limit and k != "ytd"]
    return allowed[-1] if allowed else "1d"


_OHLCV = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}


def _group_months(df: pd.DataFrame, months: int) -> pd.DataFrame:
    """Agrupa velas mensuales en bloques de calendario (trimestres, semestres, años).

    Cada vela queda fechada al inicio de su bloque: 3 meses = ene/abr/jul/oct,
    6 meses = ene/jul, 12 meses = 1 de enero.
    """
    per_year = 12 // months
    key = df.index.year * per_year + (df.index.month - 1) // months
    out = df.groupby(key).agg(_OHLCV).dropna(subset=["Close"])
    years, slot = np.divmod(out.index.to_numpy(), per_year)
    out.index = pd.to_datetime({"year": years, "month": slot * months + 1, "day": 1})
    return out


_WEEK0 = pd.Timestamp("1970-01-05")  # un lunes: ancla fija para que los bloques no cambien al recargar


def _group_weeks(df: pd.DataFrame, weeks: int) -> pd.DataFrame:
    """Agrupa velas semanales en bloques de ``weeks`` semanas que empiezan en lunes.

    Los bloques se cuentan desde una fecha fija, así una vela de 2 semanas siempre
    cubre las mismas dos semanas sin importar el periodo que se descargue.
    """
    days = (df.index.normalize() - _WEEK0).days
    key = (days // (7 * weeks)).to_numpy()
    out = df.groupby(key).agg(_OHLCV).dropna(subset=["Close"])
    out.index = _WEEK0 + pd.to_timedelta(out.index.to_numpy() * 7 * weeks, unit="D")
    return out


def _series_to_ohlc(s: pd.Series, interval_label: str, period: str) -> pd.DataFrame:
    """Convierte una serie de valores (macro) en velas del intervalo pedido.

    Si el intervalo es más fino que la serie (p. ej. M2 mensual en 1 día) se deja
    en su frecuencia original; si es más grueso, cada vela resume los valores del
    periodo (apertura, máximo, mínimo y cierre).
    """
    if period == "ytd":
        s = s[s.index >= pd.Timestamp(s.index[-1].year, 1, 1)]
    elif _PERIOD_DAYS.get(period, 10 ** 7) < 10 ** 6:
        s = s[s.index >= s.index[-1] - pd.Timedelta(days=_PERIOD_DAYS[period])]
    df = pd.DataFrame({"Open": s, "High": s, "Low": s, "Close": s, "Volume": 0.0})
    if interval_label in ("1 semana", "2 semanas"):
        df = df.resample("W-MON", label="left", closed="left").agg(_OHLCV)
        if interval_label == "2 semanas":
            df = _group_weeks(df.dropna(subset=["Close"]), 2)
    elif interval_label in ("1 mes", "3 meses", "6 meses", "12 meses"):
        df = df.resample("MS").agg(_OHLCV)
        rule = INTERVALS[interval_label][1]
        if rule:
            df = _group_months(df.dropna(subset=["Close"]), int(rule[:-1]))
    return df.dropna(subset=["Close"])


def load(symbol: str, interval_label: str, period: str):
    """Devuelve (DataFrame OHLCV, metadatos, periodo usado)."""
    if macro.is_macro(symbol):
        s, meta = macro.get_series(symbol)
        df = _series_to_ohlc(s, interval_label, period)
        if df.empty:
            raise ValueError(f"«{symbol}» no tiene datos en el periodo elegido; prueba con un periodo más largo.")
        return df, meta, period
    yi, rule, _, _ = INTERVALS[interval_label]
    period = clamp_period(yi, period)
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval=yi, auto_adjust=False, actions=False)
    if df is None or df.empty:
        raise ValueError(f"No se encontraron datos para «{symbol}». Revisa el símbolo o el mercado elegido.")
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["Close"])
    for col in ("Open", "High", "Low"):
        df[col] = df[col].fillna(df["Close"])
    df["Volume"] = df["Volume"].fillna(0)
    if rule and rule.endswith("M"):
        df = _group_months(df, int(rule[:-1]))
    elif rule and rule.endswith("W"):
        df = _group_weeks(df, int(rule[:-1]))
    elif rule:
        df = df.resample(rule, origin="start_day").agg(_OHLCV).dropna(subset=["Close"])
    try:
        meta = dict(t.history_metadata or {})
    except Exception:
        meta = {}
    return df, meta, period


_CORP_SUFFIX = re.compile(
    r"[,\s]+(inc\.?|corp\.?|corporation|ltd\.?|limited|plc|holdings?|co\.?|ag|se|n\.?\s?v\.?|"
    r"s\.?\s?a\.?\s?b\.?(\s?de\s?c\.?\s?v\.?)?|s\.?\s?a\.?(\s?de\s?c\.?\s?v\.?)?|de\s?c\.?\s?v\.?)$", re.I)


def short_name(name: str) -> str:
    """«Wal-Mart de México, S.A.B. de C.V.» -> «Wal-Mart de México» (para la lista de seguimiento)."""
    name = (name or "").strip()
    prev = None
    while name and name != prev:
        prev = name
        name = _CORP_SUFFIX.sub("", name).strip(" ,")
    return name or prev or ""


def lookup_names(symbols: list[str]) -> dict[str, str]:
    """Nombre legible de cada símbolo (catálogo macro o búsqueda exacta en Yahoo)."""
    out = {}
    for s in symbols:
        if macro.is_macro(s):
            prefix, _, key = s.upper().partition(":")
            if prefix == "MACRO" and key in macro.CATALOG:
                out[s] = macro.CATALOG[key][1]
            elif prefix == "CSV":
                out[s] = key.replace("_", " ").title()
            else:
                out[s] = "Serie de FRED"
            continue
        try:
            res = yf.Search(s, max_results=8, news_count=0)
            q = next((q for q in res.quotes or [] if q.get("symbol", "").upper() == s.upper()), None)
            if q:
                out[s] = short_name(q.get("longname") or q.get("shortname") or "")
        except Exception:
            continue
    return out


def search(query: str) -> list[dict]:
    out = macro.search(query)  # catálogo macro primero; no requiere red
    try:
        res = yf.Search(query, max_results=25, news_count=0)
    except Exception:
        if out:
            return out
        raise
    for q in res.quotes or []:
        sym = q.get("symbol")
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": q.get("longname") or q.get("shortname") or "",
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
            "type": q.get("typeDisp") or q.get("quoteType") or "",
        })
    return out


def quotes(symbols: list[str]) -> dict[str, tuple[float, float]]:
    """Último precio y % de cambio diario para la lista de seguimiento."""
    out: dict[str, tuple[float, float]] = {}
    for s in [s for s in symbols if macro.is_macro(s)]:
        try:
            ser, _ = macro.get_series(s)
            last = float(ser.iloc[-1])
            prev = float(ser.iloc[-2]) if len(ser) > 1 else np.nan
            out[s] = (last, (last / prev - 1) * 100 if prev else np.nan)
        except Exception:
            continue
    symbols = [s for s in symbols if not macro.is_macro(s)]
    if not symbols:
        return out
    df = yf.download(symbols, period="5d", interval="1d", group_by="ticker",
                     progress=False, threads=True, auto_adjust=False)
    if df is None or df.empty:
        return out
    for s in symbols:
        try:
            sub = df[s] if isinstance(df.columns, pd.MultiIndex) else df
            c = sub["Close"].dropna()
            if c.empty:
                continue
            last = float(c.iloc[-1])
            chg = (last / float(c.iloc[-2]) - 1) * 100 if len(c) > 1 else np.nan
            out[s] = (last, chg)
        except Exception:
            continue
    return out
