"""Perfil de volumen: cuánto volumen se negoció en cada nivel de precio.

El volumen de cada vela se reparte de forma pareja entre su mínimo y su máximo. Las series sin
volumen (índices, tasas, series macro) cuentan cada vela como 1: queda el tiempo que el precio
pasó en cada nivel.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui

from . import theme

POC_COLOR = "#ffb300"


def compute(ylo, yhi, vol, up, rows: int):
    """(bordes de las filas, volumen alcista por fila, volumen bajista por fila), o None si no hay datos.
    ``ylo``/``yhi``: mínimo y máximo de cada vela en coordenadas de la vista (con log ya aplicado)."""
    ylo, yhi = np.asarray(ylo, float), np.asarray(yhi, float)
    vol, up = np.asarray(vol, float), np.asarray(up, bool)
    m = np.isfinite(ylo) & np.isfinite(yhi)
    if not m.any():
        return None
    ylo, yhi, vol, up = ylo[m], yhi[m], vol[m], up[m]
    vol = np.where(np.isfinite(vol) & (vol > 0), vol, 0.0)
    if vol.sum() <= 0:
        vol = np.ones(len(ylo))
    lo, hi = float(ylo.min()), float(yhi.max())
    if not hi > lo:
        return None
    rows = int(max(rows, 1))
    edges = np.linspace(lo, hi, rows + 1)
    overlap = np.clip(np.minimum(yhi[:, None], edges[None, 1:]) - np.maximum(ylo[:, None], edges[None, :-1]), 0, None)
    span = yhi - ylo
    flat = span <= 0  # vela sin rango: todo su volumen va a la fila donde está
    frac = overlap / np.where(flat, 1.0, span)[:, None]
    if flat.any():
        frac[flat] = 0.0
        frac[np.flatnonzero(flat), np.clip(np.searchsorted(edges, ylo[flat], "right") - 1, 0, rows - 1)] = 1.0
    return edges, (frac * (vol * up)[:, None]).sum(0), (frac * (vol * ~up)[:, None]).sum(0)


def value_area(total, pct: float):
    """(fila con más volumen —POC—, primera y última fila del área de valor). El área crece desde el
    POC hacia la fila vecina con más volumen hasta juntar ``pct`` % del total."""
    total = np.asarray(total, float)
    poc = int(np.argmax(total))
    lo = hi = poc
    acc, target = total[poc], total.sum() * pct / 100
    while acc < target and (lo > 0 or hi < len(total) - 1):
        below = total[lo - 1] if lo > 0 else -1.0
        above = total[hi + 1] if hi < len(total) - 1 else -1.0
        if above >= below:
            hi += 1
            acc += above
        else:
            lo -= 1
            acc += below
    return poc, lo, hi


def bars(prof, va_pct: float, x_base: float, max_width: float):
    """Rectángulos a pintar: [(x, y, ancho, alto, color)] y la altura del POC.
    Las barras nacen en ``x_base`` y crecen ``max_width`` (negativo = hacia la izquierda)."""
    edges, upv, dnv = prof
    total = upv + dnv
    peak = float(total.max())
    if peak <= 0:
        return [], None
    poc, va_lo, va_hi = value_area(total, va_pct)
    step = edges[1] - edges[0]
    out = []
    for i in range(len(total)):
        if total[i] <= 0:
            continue
        alpha = "b0" if va_lo <= i <= va_hi else "48"  # fuera del área de valor, más tenue
        y = edges[i] + step * 0.06
        w_up = max_width * upv[i] / peak
        w_dn = max_width * dnv[i] / peak
        out.append((x_base, y, w_up, step * 0.88, theme.up()[:7] + alpha))
        out.append((x_base + w_up, y, w_dn, step * 0.88, theme.down()[:7] + alpha))
    return out, float(edges[poc] + step / 2)


class VisibleProfileItem(pg.GraphicsObject):
    """Perfil de volumen de las velas que se ven; se recalcula al mover o acercar la gráfica."""

    def __init__(self, ylo, yhi, vol, up, rows=24, va_pct=70.0, width_pct=30.0, right=True):
        super().__init__()
        self._data = (np.asarray(ylo, float), np.asarray(yhi, float), np.asarray(vol, float), np.asarray(up, bool))
        self.rows, self.va_pct, self.width_pct, self.right = rows, va_pct, width_pct, right
        self._key = None
        self._prof = None
        self.setZValue(-5)  # detrás de las velas

    def _profile(self, x0, x1):
        n = len(self._data[0])
        a, b = max(int(np.ceil(x0)), 0), min(int(np.floor(x1)), n - 1)
        if (a, b) != self._key:
            self._key = (a, b)
            self._prof = compute(*(d[a:b + 1] for d in self._data), self.rows) if b >= a else None
        return self._prof

    def paint(self, p, *args):
        vb = self.getViewBox()
        if vb is None:
            return
        x0, x1 = vb.viewRange()[0]
        prof = self._profile(x0, x1)
        if prof is None:
            return
        width = (x1 - x0) * self.width_pct / 100
        rects, poc = bars(prof, self.va_pct, x1 if self.right else x0, -width if self.right else width)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        for x, y, w, h, color in rects:
            p.fillRect(QtCore.QRectF(x, y, w, h).normalized(), pg.mkColor(color))
        if poc is not None:
            pen = QtGui.QPen(QtGui.QColor(POC_COLOR), 1, QtCore.Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawLine(QtCore.QPointF(x0, poc), QtCore.QPointF(x1, poc))

    def boundingRect(self):
        vb = self.getViewBox()
        return vb.viewRect() if vb is not None else QtCore.QRectF()

    def viewTransformChanged(self):
        self.prepareGeometryChange()
        self.update()
