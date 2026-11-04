"""Gráficas que no siguen el tiempo: Renko, Kagi, Point & Figure y Line Break.

Convierten las velas en barras sintéticas (una por ladrillo, tramo, columna o línea) que se usan
como cualquier vela: los indicadores y los dibujos se calculan sobre ellas. Cada barra lleva la fecha
de la vela donde se formó; si salen varias de una misma vela, se separan por un segundo para que el
índice siga siendo único y creciente.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRANSFORMED = ("Renko", "Kagi", "Point & Figure", "Line Break")
MAX_BARS = 20000  # un tamaño diminuto generaría millones de barras

# (clave, etiqueta, valor por omisión). Un tamaño en 0 se calcula con el ATR de 14 velas.
PARAM_SPECS = {
    "Renko": [("box", "Tamaño del ladrillo (0 = automático)", 0.0)],
    "Kagi": [("rev", "Reversa en precio (0 = automático)", 0.0)],
    "Point & Figure": [("box", "Tamaño del cuadro (0 = automático)", 0.0),
                       ("rev_boxes", "Cuadros para revertir", 3)],
    "Line Break": [("lines", "Líneas para revertir", 3)],
}


def auto_size(df: pd.DataFrame) -> float:
    """ATR de 14 velas, redondeado a 2 cifras para que no cambie con cada actualización."""
    h, l, c = (df[k].to_numpy(float) for k in ("High", "Low", "Close"))
    prev = np.concatenate(([c[0]], c[:-1]))
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev), np.abs(l - prev)]), axis=0)
    tr = tr[np.isfinite(tr)][-14:]
    size = float(tr.mean()) if tr.size else 0.0
    if size <= 0:
        size = abs(float(np.nanmean(c))) * 0.01 or 1.0
    return round(size, 1 - int(np.floor(np.log10(size))))


def _frame(rows: list, df: pd.DataFrame) -> pd.DataFrame:
    """rows: (posición de la vela, apertura, máximo, mínimo, cierre, volumen)."""
    if not rows:
        return df.iloc[0:0]
    arr = np.array(rows, dtype=float)
    secs = df.index.values[arr[:, 0].astype(int)].astype("datetime64[s]").astype(np.int64)
    for i in range(1, len(secs)):
        secs[i] = max(secs[i], secs[i - 1] + 1)
    return pd.DataFrame(arr[:, 1:], columns=["Open", "High", "Low", "Close", "Volume"],
                        index=pd.to_datetime(secs, unit="s"))


def _check_size(df: pd.DataFrame, size: float):
    span = float(np.nanmax(df["High"].to_numpy(float)) - np.nanmin(df["Low"].to_numpy(float)))
    if span / size > MAX_BARS:
        raise ValueError(f"el tamaño {size:g} es demasiado chico para este rango de precios")


def renko(df: pd.DataFrame, box: float) -> pd.DataFrame:
    closes, vols = df["Close"].to_numpy(float), df["Volume"].to_numpy(float)
    start = next((c for c in closes if np.isfinite(c)), None)
    if start is None:
        return df.iloc[0:0]
    top = bottom = np.floor(start / box) * box
    rows, pending = [], 0.0
    for i, (c, v) in enumerate(zip(closes, vols)):
        pending += v if np.isfinite(v) else 0.0
        if not np.isfinite(c):
            continue
        ups = int(np.floor((c - top) / box + 1e-9))
        downs = int(np.floor((bottom - c) / box + 1e-9))
        n = ups if ups > 0 else downs if downs > 0 else 0
        for k in range(n):
            if ups > 0:
                o, cl = top, top + box
                bottom, top = o, cl
            else:
                o, cl = bottom, bottom - box
                top, bottom = o, cl
            rows.append((i, o, max(o, cl), min(o, cl), cl, pending / n))
        if n:
            pending = 0.0
    return _frame(rows, df)


def line_break(df: pd.DataFrame, lines: int) -> pd.DataFrame:
    closes, opens, vols = (df[k].to_numpy(float) for k in ("Close", "Open", "Volume"))
    rows, pending = [], 0.0
    for i, (o, c, v) in enumerate(zip(opens, closes, vols)):
        pending += v if np.isfinite(v) else 0.0
        if not np.isfinite(c):
            continue
        if not rows:
            o = o if np.isfinite(o) else c
            rows.append((i, o, max(o, c), min(o, c), c, pending))
            pending = 0.0
            continue
        recent = rows[-lines:]
        last_o, last_c = rows[-1][1], rows[-1][4]
        if c > max(r[2] for r in recent):
            start = max(last_o, last_c)
        elif c < min(r[3] for r in recent):
            start = min(last_o, last_c)
        else:
            continue
        rows.append((i, start, max(start, c), min(start, c), c, pending))
        pending = 0.0
    return _frame(rows, df)


def kagi(df: pd.DataFrame, rev: float) -> pd.DataFrame:
    closes, vols = df["Close"].to_numpy(float), df["Volume"].to_numpy(float)
    rows: list = []
    start = end = None
    direction, pos, vol = 0, 0, 0.0
    for i, (c, v) in enumerate(zip(closes, vols)):
        vol += v if np.isfinite(v) else 0.0
        if not np.isfinite(c):
            continue
        if start is None:
            start = end = c
            pos = i
            continue
        if direction == 0:
            if abs(c - start) >= rev:
                direction, end, pos = (1 if c > start else -1), c, i
        elif (c - end) * direction > 0:  # sigue en la misma dirección
            end, pos = c, i
        elif (end - c) * direction >= rev:  # reversa: se cierra el tramo y empieza otro
            rows.append((pos, start, max(start, end), min(start, end), end, vol))
            start, end, pos, vol, direction = end, c, i, 0.0, -direction
    if direction:
        rows.append((pos, start, max(start, end), min(start, end), end, vol))
    return _frame(rows, df)


def point_figure(df: pd.DataFrame, box: float, rev_boxes: int) -> tuple[pd.DataFrame, np.ndarray]:
    """Una barra por columna: Open y Close son la primera y la última marca. Usa máximos y mínimos."""
    highs, lows, closes, vols = (df[k].to_numpy(float) for k in ("High", "Low", "Close", "Volume"))
    columns: list = []  # [posición, primera marca, última marca, sube, volumen] en cuadros enteros
    ref, up, first, last, pos, vol = None, None, 0, 0, 0, 0.0
    for i in range(len(df)):
        vol += vols[i] if np.isfinite(vols[i]) else 0.0
        h, l = highs[i], lows[i]
        if not (np.isfinite(h) and np.isfinite(l)):
            continue
        hi, lo = int(np.floor(h / box + 1e-9)), int(np.ceil(l / box - 1e-9))
        if ref is None:
            ref = int(np.floor(closes[i] / box + 1e-9)) if np.isfinite(closes[i]) else lo
            continue
        if up is None:
            if hi > ref:
                up, first, last, pos = True, ref, hi, i
            elif lo < ref:
                up, first, last, pos = False, ref, lo, i
        elif up:
            if hi > last:
                last, pos = hi, i
            elif lo <= last - rev_boxes:
                columns.append([pos, first, last, up, vol])
                up, first, last, pos, vol = False, last - 1, lo, i, 0.0
        else:
            if lo < last:
                last, pos = lo, i
            elif hi >= last + rev_boxes:
                columns.append([pos, first, last, up, vol])
                up, first, last, pos, vol = True, last + 1, hi, i, 0.0
    if up is not None:
        columns.append([pos, first, last, up, vol])
    half = box / 2
    rows = [(p, a * box, max(a, b) * box + half, min(a, b) * box - half, b * box, v) for p, a, b, _u, v in columns]
    return _frame(rows, df), np.array([c[3] for c in columns], dtype=bool)


def transform(df: pd.DataFrame, chart_type: str, params: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Devuelve las barras sintéticas y sus datos: ``text`` para informar y lo que necesita el dibujo."""
    params = params or {}
    if chart_type == "Line Break":
        lines = max(1, int(params.get("lines", 3)))
        return line_break(df, lines), {"text": f"{lines} líneas"}
    key = "rev" if chart_type == "Kagi" else "box"
    size = float(params.get(key) or 0)
    auto = size <= 0
    if auto:
        size = auto_size(df)
    _check_size(df, size)
    how = f"{size:g}" + (" (automático)" if auto else "")
    if chart_type == "Renko":
        return renko(df, size), {"text": f"ladrillo de {how}"}
    if chart_type == "Kagi":
        return kagi(df, size), {"text": f"reversa de {how}"}
    if chart_type == "Point & Figure":
        rev_boxes = max(1, int(params.get("rev_boxes", 3)))
        frame, ups = point_figure(df, size, rev_boxes)
        return frame, {"box": size, "up": ups, "text": f"cuadro de {how}, reversa de {rev_boxes}"}
    raise ValueError(chart_type)
