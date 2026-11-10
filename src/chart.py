"""Widget de gráficas: velas + indicadores superpuestos + paneles inferiores."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .drawings import DrawingLayer, DrawViewBox
from .indicators import DOWN, UP, Computed, Out  # noqa: F401 - UP y DOWN los importan otros módulos desde aquí
from .volprofile import VisibleProfileItem

BG = "#131722"
FG = "#b2b5be"
AXIS = "#2a2e39"
# pyqtgraph pinta la cuadrícula con el color del eje y la opacidad elegida; con AXIS (casi igual
# al fondo) no se distinguiría, así que los ejes usan un gris más claro
GRID = "#787b86"
CROSS = "#9598a1"
PALETTE = ["#2962ff", "#ff6d00", "#e040fb", "#00bcd4", "#ffd600", "#ec407a", "#76ff03", "#8d6e63", "#90caf9"]
CHART_TYPES = ["Velas", "Velas huecas", "Heikin Ashi", "Barras OHLC", "Línea", "Área", "Línea base",
               "Renko", "Kagi", "Point & Figure", "Line Break"]
CANDLE_TYPES = ("Velas", "Velas huecas", "Heikin Ashi", "Barras OHLC", "Renko", "Line Break")

pg.setConfigOptions(antialias=True, background=BG, foreground=FG)

_STYLES = {"solid": QtCore.Qt.SolidLine, "dash": QtCore.Qt.DashLine, "dot": QtCore.Qt.DotLine}


# --------------------------------------------------------------------------- #
# Formato de números
# --------------------------------------------------------------------------- #
DIVIDER_GRAB = 4      # píxeles alrededor del borde entre paneles donde se puede arrastrar
MIN_PANE_HEIGHT = 50
PRICE_TAG_STYLE = "background:{color}; color:#ffffff; padding:1px 5px; border-radius:2px; font-size:11px;"
COLLAPSED_HEIGHT = 22  # panel plegado con doble clic: solo queda la franja con su nombre
DEFAULT_PANE_FRACTION = 0.12  # panel nuevo cuando las alturas ya se ajustaron a mano


def fmt_price(v) -> str:
    if v is None or not np.isfinite(v):
        return "—"
    a = abs(v)
    if a >= 1e9:  # agregados macro (M2, balance de la Fed): 23.34T
        return fmt_big(v)
    if a >= 1000:
        return f"{v:,.2f}"
    if a >= 10:
        return f"{v:.2f}"
    if a >= 1:
        return f"{v:.4f}"
    if a == 0:
        return "0"
    digits = int(-np.floor(np.log10(a))) + 3
    return f"{v:.{min(digits, 10)}f}"


def fmt_big(v) -> str:
    if v is None or not np.isfinite(v):
        return "—"
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v / div:.2f}{suf}"
    return f"{v:.0f}" if a >= 100 else fmt_price(v)


_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def fmt_date(ts, intraday: bool = False) -> str:
    """«jue 06/08/2026» o «jue 06/08/2026 14:30» (día de la semana en español, sin depender del sistema)."""
    return f"{_DIAS[ts.weekday()]} {ts:%d/%m/%Y}" + (f" {ts:%H:%M}" if intraday else "")


def fmt_change(v) -> str:
    """Cambio con signo: +1.25, +0.0034 o, para agregados macro, +124.90B."""
    if abs(v) >= 1e6:
        return ("+" if v >= 0 else "-") + fmt_big(abs(v))
    return f"{v:+.{4 if abs(v) < 1 else 2}f}"


def fmt_value(v) -> str:
    if v is None or not np.isfinite(v):
        return "—"
    return fmt_big(v) if abs(v) >= 1e5 else fmt_price(v)


def heikin_ashi(df: pd.DataFrame):
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    hc = (o + h + l + c) / 4
    ho = np.empty_like(hc)
    if len(hc):
        ho[0] = (o[0] + c[0]) / 2
        for i in range(1, len(hc)):
            ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return ho, np.maximum.reduce([h, ho, hc]), np.minimum.reduce([l, ho, hc]), hc


# --------------------------------------------------------------------------- #
# Elementos gráficos
# --------------------------------------------------------------------------- #
class CandleItem(pg.GraphicsObject):
    """Velas japonesas o barras OHLC pre-renderizadas en un QPicture."""

    def __init__(self, x, o, h, l, c, bars=False, hollow=False):
        """``hollow``: velas huecas. El color compara con el cierre anterior y el cuerpo va
        vacío si la vela cerró arriba de su apertura."""
        super().__init__()
        self._pic = QtGui.QPicture()
        p = QtGui.QPainter(self._pic)
        pens = {True: pg.mkPen(theme.up()), False: pg.mkPen(theme.down())}
        brushes = {True: pg.mkBrush(theme.up()), False: pg.mkBrush(theme.down())}
        w = 0.36
        QPF = QtCore.QPointF
        for i in range(len(x)):
            oi, hi, li, ci = o[i], h[i], l[i], c[i]
            if not (np.isfinite(oi) and np.isfinite(ci)):
                continue
            xi = float(x[i])
            up = ci >= oi
            if hollow:
                prev = c[i - 1] if i else oi
                p.setPen(pens[bool(ci >= prev)])
                top, bottom = max(oi, ci), min(oi, ci)
                if hi > top:  # la mecha no cruza el cuerpo vacío
                    p.drawLine(QPF(xi, top), QPF(xi, hi))
                if li < bottom:
                    p.drawLine(QPF(xi, li), QPF(xi, bottom))
                if ci == oi:
                    p.drawLine(QPF(xi - w, oi), QPF(xi + w, oi))
                else:
                    p.setBrush(QtCore.Qt.NoBrush if up else brushes[bool(ci >= prev)])
                    p.drawRect(QtCore.QRectF(xi - w, oi, 2 * w, ci - oi))
                continue
            p.setPen(pens[up])
            # Qt fusiona en un bloque gigante los rectángulos y líneas de tamaño cero
            # seguidos dentro de un QPicture (p. ej. meses sin cotización, O=H=L=C):
            # las mechas nulas no se dibujan y los cuerpos nulos van como línea.
            if hi != li:
                p.drawLine(QPF(xi, li), QPF(xi, hi))
            if bars:
                p.drawLine(QPF(xi - w, oi), QPF(xi, oi))
                p.drawLine(QPF(xi, ci), QPF(xi + w, ci))
            elif ci == oi:
                p.drawLine(QPF(xi - w, oi), QPF(xi + w, oi))
            else:
                p.setBrush(brushes[up])
                p.drawRect(QtCore.QRectF(xi - w, oi, 2 * w, ci - oi))
        p.end()
        lo, hi = np.nanmin(l) if len(l) else 0, np.nanmax(h) if len(h) else 1
        self._rect = QtCore.QRectF(-1, lo, len(x) + 1, hi - lo)

    def paint(self, p, *args):
        p.drawPicture(0, 0, self._pic)

    def boundingRect(self):
        return self._rect


class BaselineItem(pg.GraphicsObject):
    """Línea base: verde arriba del nivel y rojo abajo. El nivel sigue a lo que se ve."""

    def __init__(self, x, y):
        super().__init__()
        m = np.isfinite(y)
        self._pts = [QtCore.QPointF(float(a), float(b)) for a, b in zip(x[m], y[m])]
        self._base = float(np.nanmean(y)) if m.any() else 0.0
        lo, hi = (float(y[m].min()), float(y[m].max())) if m.any() else (0.0, 1.0)
        self._rect = QtCore.QRectF(float(x[0]) - 1 if len(x) else 0, lo, len(x) + 2, max(hi - lo, 1e-12))

    def set_base(self, y: float):
        if np.isfinite(y) and y != self._base:
            self._base = float(y)
            self.update()

    def paint(self, p, *args):
        if len(self._pts) < 2:
            return
        r, base = self._rect, self._base
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        poly = QtGui.QPolygonF(self._pts)
        area = QtGui.QPolygonF([QtCore.QPointF(self._pts[0].x(), base)] + self._pts
                               + [QtCore.QPointF(self._pts[-1].x(), base)])
        left, width = r.left() - 1, r.width() + 2
        bands = ((QtCore.QRectF(left, base, width, max(r.bottom() - base, 0) + 1), theme.up()),
                 (QtCore.QRectF(left, r.top() - 1, width, max(base - r.top(), 0) + 1), theme.down()))
        for band, color in bands:
            p.save()
            p.setClipRect(band)
            fill = QtGui.QColor(color)
            fill.setAlpha(45)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(fill)
            p.drawPolygon(area)
            p.setPen(pg.mkPen(color, width=2))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawPolyline(poly)
            p.restore()
        p.setPen(pg.mkPen("#787b86", width=1, style=QtCore.Qt.DashLine))
        p.drawLine(QtCore.QPointF(left, base), QtCore.QPointF(left + width, base))

    def boundingRect(self):
        return self._rect.united(QtCore.QRectF(self._rect.left(), self._base, 1, 0)).adjusted(-1, -1, 1, 1)


class KagiItem(pg.GraphicsObject):
    """Kagi: la línea engruesa (verde) al superar el último hombro y adelgaza (roja) al perder la última cintura."""

    def __init__(self, x, o, c):
        super().__init__()
        self._pic = QtGui.QPicture()
        p = QtGui.QPainter(self._pic)
        yang_pen, yin_pen = pg.mkPen(theme.up(), width=2.5), pg.mkPen(theme.down(), width=1.2)
        QPF = QtCore.QPointF
        yang = bool(len(c) and c[0] >= o[0])
        shoulder = waist = None
        for i in range(len(x)):
            a, b, xi = o[i], c[i], float(x[i])
            if not (np.isfinite(a) and np.isfinite(b)):
                continue
            if i:
                p.setPen(yang_pen if yang else yin_pen)
                p.drawLine(QPF(float(x[i - 1]), a), QPF(xi, a))
            # el grosor cambia justo en el nivel que se rompe, a media línea
            level = None
            if b > a and not yang and shoulder is not None and b > shoulder:
                level = max(a, shoulder)
            elif b < a and yang and waist is not None and b < waist:
                level = min(a, waist)
            start = a
            if level is not None:
                if level != a:
                    p.setPen(yang_pen if yang else yin_pen)
                    p.drawLine(QPF(xi, a), QPF(xi, level))
                yang = not yang
                start = level
            if start != b:
                p.setPen(yang_pen if yang else yin_pen)
                p.drawLine(QPF(xi, start), QPF(xi, b))
            if b > a:
                shoulder = b
            elif b < a:
                waist = b
        p.end()
        both = np.concatenate([o, c]) if len(o) else np.array([0.0, 1.0])
        lo, hi = float(np.nanmin(both)), float(np.nanmax(both))
        self._rect = QtCore.QRectF(-1, lo, len(x) + 1, max(hi - lo, 1e-12))

    def paint(self, p, *args):
        p.drawPicture(0, 0, self._pic)

    def boundingRect(self):
        return self._rect


class PointFigureItem(pg.GraphicsObject):
    """Point & Figure: columnas de X (suben) y O (bajan), una marca por cuadro."""

    def __init__(self, x, o, c, ups, box, tf=None):
        super().__init__()
        self._pic = QtGui.QPicture()
        p = QtGui.QPainter(self._pic)
        up_pen, down_pen = pg.mkPen(theme.up(), width=1.4), pg.mkPen(theme.down(), width=1.4)
        QPF = QtCore.QPointF
        lo, hi = np.inf, -np.inf
        w = 0.36
        conv = (lambda v: float(tf(np.array([v]))[0])) if tf else float
        for i in range(len(x)):
            first, last = round(o[i] / box), round(c[i] / box)
            xi = float(x[i])
            p.setPen(up_pen if ups[i] else down_pen)
            p.setBrush(QtCore.Qt.NoBrush)
            for k in range(min(first, last), max(first, last) + 1):
                a, b = conv((k - 0.5) * box), conv((k + 0.5) * box)
                if not (np.isfinite(a) and np.isfinite(b)):
                    continue
                lo, hi = min(lo, a), max(hi, b)
                if ups[i]:
                    p.drawLine(QPF(xi - w, a), QPF(xi + w, b))
                    p.drawLine(QPF(xi - w, b), QPF(xi + w, a))
                else:
                    p.drawEllipse(QtCore.QRectF(xi - w, a, 2 * w, b - a))
        p.end()
        if not np.isfinite(lo):
            lo, hi = 0.0, 1.0
        self._rect = QtCore.QRectF(-1, lo, len(x) + 1, max(hi - lo, 1e-12))

    def paint(self, p, *args):
        p.drawPicture(0, 0, self._pic)

    def boundingRect(self):
        return self._rect


class DateAxis(pg.AxisItem):
    """Eje X por índice de vela (sin huecos de fines de semana) mostrando fechas."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.labels: list[str] = []

    def set_dates(self, idx: pd.DatetimeIndex, intraday: bool):
        if intraday:
            fmt = "%d/%m %H:%M"
        elif len(idx) > 1 and (idx[-1] - idx[0]).days / (len(idx) - 1) >= 25:
            fmt = "%m/%Y"  # velas mensuales o más largas
        else:
            fmt = "%d/%m/%Y"
        self.labels = list(idx.strftime(fmt))

    def tickValues(self, minVal, maxVal, size):
        vals = super().tickValues(minVal, maxVal, size)
        return [(sp, [v for v in vs if float(v).is_integer()]) for sp, vs in vals if sp >= 1]

    def tickStrings(self, values, scale, spacing):
        n = len(self.labels)
        out = []
        for v in values:
            i = int(round(v))
            out.append(self.labels[i] if 0 <= i < n else "")
        return out


def _log10(v):
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.asarray(v, dtype=float)
        return np.where(a > 0, np.log10(np.where(a > 0, a, 1.0)), np.nan)


def _nice_linear(lo, hi, count):
    raw = (hi - lo) / max(count, 1)
    mag = 10 ** np.floor(np.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    return np.arange(np.ceil(lo / step) * step, hi + step * 0.001, step)


class ValueAxis(pg.AxisItem):
    """Eje Y que abrevia números grandes (volumen, OBV…) y soporta escala logarítmica.

    En modo log los datos ya vienen como log10(precio); el eje coloca marcas en
    precios "redondos" y las etiqueta con el precio real.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.log = False
        self.percent = False  # modo comparar: los valores son % de cambio
        self.view_base = None  # escala %: los valores son precios y se etiquetan contra este precio

    def set_view_base(self, base):
        if base != self.view_base:
            self.view_base = base
            self.picture = None
            self.update()

    def tickValues(self, minVal, maxVal, size):
        if self.view_base:
            b = self.view_base
            lo, hi = (minVal / b - 1) * 100, (maxVal / b - 1) * 100
            if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
                return []
            ticks = _nice_linear(lo, hi, max(3, int(size / 38)))
            return [(1.0, [b * (1 + t / 100) for t in ticks])]
        if not self.log or not (np.isfinite(minVal) and np.isfinite(maxVal)) or maxVal <= minVal:
            return super().tickValues(minVal, maxVal, size)
        lo, hi = 10 ** minVal, 10 ** maxVal
        max_ticks = max(3, int(size / 38))
        if hi / lo < 3:
            ticks = _nice_linear(lo, hi, min(max_ticks, 7))
        else:
            ticks = None
            decades = np.arange(np.floor(minVal), np.ceil(maxVal) + 1)
            for mant in ([1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 5, 7], [1, 2, 5], [1, 3], [1]):
                cand = np.array([m * 10 ** d for d in decades for m in mant])
                cand = cand[(cand >= lo) & (cand <= hi)]
                if len(cand) <= max_ticks:
                    ticks = cand
                    break
            if ticks is None:  # muchas décadas: una marca cada N décadas
                step = int(np.ceil(len(decades) / max_ticks))
                ticks = 10.0 ** decades[::step]
                ticks = ticks[(ticks >= lo) & (ticks <= hi)]
        ticks = ticks[ticks > 0]
        return [(1.0, list(np.log10(ticks)))]

    def tickStrings(self, values, scale, spacing):
        if self.view_base:
            pcts = [(v / self.view_base - 1) * 100 for v in values]
            gap = min((abs(a - b) for a, b in zip(pcts, pcts[1:])), default=1.0)
            return [f"{v + 0.0:+.{0 if gap >= 1 else 1 if gap >= 0.1 else 2}f}%" for v in pcts]
        if self.percent:
            return [f"{v:+.{0 if spacing >= 1 else 1}f}%" for v in values]
        if self.log:
            out = []
            for v in values:
                p = 10 ** v
                out.append(f"{p:,.0f}" if p >= 1000 else fmt_price(p))
            return out
        if values and max(abs(v) for v in values) >= 1e5:
            return [fmt_big(v) for v in values]
        return super().tickStrings(values, scale, spacing)


class _Entry:
    def __init__(self, label: str, outs: list[tuple[Out, str]], index: int = -1, hidden: bool = False,
                 compare=None, color: str = FG, removable: bool = False):
        self.label = label
        self.outs = outs
        self.index = index          # indicador: posición en la lista de indicadores activos
        self.hidden = hidden
        self.compare = compare      # comparación: (% de cambio, precio) por vela
        self.color = color
        self.removable = removable  # comparación que se puede quitar desde la leyenda

    @property
    def interactive(self) -> bool:
        return self.index >= 0 or self.removable


class LegendItem(pg.TextItem):
    """Leyenda de un panel. Cada indicador es un renglón; al pasar el mouse aparecen
    ⚙ configurar · 👁 ocultar/mostrar · ✕ quitar, y con clic derecho, un menú."""

    def __init__(self, chart: "ChartWidget"):
        super().__init__(anchor=(0, 0))
        self.chart = chart
        self.entries: list[_Entry] = []
        self.hover_row: int | None = None
        f = QtGui.QFont()
        f.setPointSize(9)
        self.setFont(f)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton | QtCore.Qt.RightButton)

    def _doc_point(self, pos):
        return self.textItem.mapFromParent(pos)

    def _row_at(self, pos):
        doc = self.textItem.document()
        c = doc.documentLayout().hitTest(self._doc_point(pos), QtCore.Qt.FuzzyHit)
        return None if c < 0 else doc.findBlock(c).blockNumber()

    def _anchor_at(self, pos) -> str:
        return self.textItem.document().documentLayout().anchorAt(self._doc_point(pos))

    def hoverEvent(self, ev):
        row = None if ev.isExit() else self._row_at(ev.pos())
        if row is not None and (row >= len(self.entries) or not self.entries[row].interactive):
            row = None
        if row != self.hover_row:
            self.hover_row = row
            self.chart.refresh_labels()
        on_icon = not ev.isExit() and bool(self._anchor_at(ev.pos()))
        self.setCursor(QtCore.Qt.PointingHandCursor if on_icon else QtCore.Qt.ArrowCursor)

    def mouseClickEvent(self, ev):
        row = self._row_at(ev.pos())
        if row is None or row >= len(self.entries) or not self.entries[row].interactive:
            ev.ignore()
            return
        entry = self.entries[row]
        if ev.button() == QtCore.Qt.RightButton:
            ev.accept()
            self.chart.legend_menu(entry, ev.screenPos())
            return
        action = self._anchor_at(ev.pos())
        if not action:
            ev.ignore()
            return
        ev.accept()
        if action == "rmcmp":
            self.chart.compareRemoved.emit(entry.label)
        else:
            self.chart.legendAction.emit(action, entry.index)


# --------------------------------------------------------------------------- #
# Widget principal
# --------------------------------------------------------------------------- #
class ChartWidget(pg.GraphicsLayoutWidget):
    hovered = QtCore.Signal(str)
    legendAction = QtCore.Signal(str, int)  # ("cfg" | "hide" | "del", índice del indicador)
    compareRemoved = QtCore.Signal(str)     # ✕ en una comparación de la leyenda
    panesResized = QtCore.Signal()          # cambió la altura de los paneles (para guardarla)
    cursorTime = QtCore.Signal(float)       # fecha (epoch) bajo el cursor; NaN si salió de la gráfica
    rangeChanged = QtCore.Signal()          # el usuario movió o acercó el eje de tiempo
    pane_heights: dict = {}  # valor de clase: pyqtgraph llama a resizeEvent antes de terminar __init__
    collapsed: set = set()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(theme.c(BG))
        self._cursor_i = None  # vela donde está la línea vertical del cursor
        self.ci.layout.setContentsMargins(2, 2, 2, 2)
        self.ci.layout.setSpacing(1)
        self.df: pd.DataFrame | None = None
        self.plots: list[pg.PlotItem] = []
        self._series: dict = {}
        self._entries: dict = {}
        self._labels: dict = {}
        self._cross: dict = {}
        self._fixed: set = set()
        self._intraday = False
        self.default_bars = 150
        self.watermark_size = 44  # puntos del símbolo de fondo (menor con varias gráficas)
        self.legend_top = 2  # la leyenda del precio empieza debajo del encabezado, que va sobre la gráfica
        self._synced_range = None  # último rango puesto por sincronización (no se reenvía)
        self.grid_alpha = 0.22  # opacidad de la cuadrícula del fondo (0 = sin cuadrícula)
        # ajuste manual de los paneles inferiores; se guarda por indicador porque la gráfica
        # se reconstruye en cada actualización
        self.pane_heights: dict = {}   # fracción de la altura de cada fila ("main" = precio)
        self._pane_y: dict = {}        # escala vertical elegida a mano por panel
        self.collapsed: set = set()    # paneles plegados con doble clic (por indicador, p. ej. "rsi#1")
        self._hidden_by_collapse: dict = {}  # panel -> elementos que se ocultaron al plegarlo
        self._pane_key: dict = {}
        self._pane_default: dict = {}  # escala propia del indicador (RSI 0-100…)
        self._manual: set = set()
        self._drag = None
        self.scene().sigMouseMoved.connect(self._on_mouse)
        self.layer = DrawingLayer(self)
        # último precio y cuenta regresiva, sobre el eje de precios para no tapar las velas;
        # se crea antes que las del cursor para que esas queden encima
        self._pricetag = self._make_tag()
        # etiquetas del cursor sobre los ejes: precio a la derecha y fecha abajo (como TradingView)
        self._ytag = self._make_tag()
        self._xtag = self._make_tag()
        # cuenta regresiva al cierre de la vela actual, en la etiqueta del último precio
        self._last_line = None
        self._last_text = ""
        self._candle_end = None
        self._countdown = QtCore.QTimer(self, interval=1000)
        self._countdown.timeout.connect(self._tick_countdown)
        self._countdown.start()

    def set_countdown(self, end):
        """``end``: hora UTC (pd.Timestamp) en que cierra la vela actual, o None para no mostrarla."""
        self._candle_end = end
        self._tick_countdown()

    def _tick_countdown(self):
        if self._last_line is None:
            return
        text = self._last_text
        if self._candle_end is not None:
            secs = int((self._candle_end - pd.Timestamp.now(tz="UTC")).total_seconds())
            if secs > 0:
                d, rem = divmod(secs, 86400)
                h, rem = divmod(rem, 3600)
                m, s = divmod(rem, 60)
                left = (f"{d}d {h:02d}:{m:02d}:{s:02d}" if d else f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}")
                text = f"{text}\n{left}"
            else:
                self._candle_end = None  # la vela ya cerró: se quita hasta la próxima actualización
        if self._pricetag.text() != text:
            self._pricetag.setText(text)
            self._pricetag.adjustSize()
        self._place_price_tag()

    def _place_price_tag(self, *_a):
        """Pone la etiqueta del último precio sobre el eje derecho, a la altura de la línea punteada.
        Si el precio queda fuera de la vista, se pega al borde del eje."""
        tag, line = self._pricetag, self._last_line
        if line is None or not self.plots:
            tag.hide()
            return
        try:
            vb = self.plots[0].getViewBox()
            y = self.mapFromScene(vb.mapViewToScene(QtCore.QPointF(0, line.value()))).y()
        except RuntimeError:  # la gráfica se está redibujando
            tag.hide()
            return
        r = self._axis_rect(self.plots[0].getAxis("right"))
        top = int(min(max(y - tag.height() / 2, r.top()), r.bottom() - tag.height()))
        tag.move(min(r.left() + 1, r.right() - tag.width()), top)
        tag.show()

    # ------------------------------------------------------- paneles a mano
    def _zone_at(self, pos):
        """Qué hay bajo el cursor: ("divider", fila), ("axis", panel) o None."""
        if len(self.plots) < 2:
            return None
        pos = pos.toPoint()
        for row, p in enumerate(self.plots[1:]):
            if self._is_collapsed(p) or self._is_collapsed(self.plots[row]):
                continue
            bottom = self.mapFromScene(self.plots[row].getViewBox().sceneBoundingRect().bottomLeft()).y()
            top = self.mapFromScene(p.getViewBox().sceneBoundingRect().topLeft()).y()
            if abs(pos.y() - (top + bottom) / 2) <= DIVIDER_GRAB:
                return "divider", row
        for p in self.plots[1:]:
            if not self._is_collapsed(p) and self._axis_rect(p.getAxis("right")).contains(pos):
                return "axis", p
        return None

    def _is_collapsed(self, plot) -> bool:
        return self._pane_key.get(plot) in self.collapsed

    def _pane_at(self, pos):
        """Panel inferior bajo el punto (coordenadas del widget), o None."""
        sp = self.mapToScene(pos.toPoint())
        return next((p for p in self.plots[1:] if p.sceneBoundingRect().contains(sp)), None)

    def toggle_pane(self, plot):
        """Pliega el panel a una franja con su nombre, o lo vuelve a abrir."""
        key = self._pane_key.get(plot)
        if key is None:
            return
        self.collapsed ^= {key}
        self._apply_heights()
        self.panesResized.emit()

    def _apply_collapsed(self):
        """Oculta lo dibujado en los paneles plegados (queda la leyenda) y lo devuelve al abrirlos."""
        for p in self.plots[1:]:
            vb, ax = p.getViewBox(), p.getAxis("right")
            if self._is_collapsed(p):
                cross = self._cross.get(p, ())  # la cruz del cursor se muestra y oculta sola
                hidden = [it for it in vb.childGroup.childItems() if it.isVisible() and it not in cross]
                for it in hidden:
                    it.hide()
                if p not in self._hidden_by_collapse:
                    self._tick_len = ax.style["tickLength"]
                self._hidden_by_collapse.setdefault(p, []).extend(hidden)
                ax.setStyle(showValues=False, tickLength=0)
                p.showGrid(x=False, y=False)
                vb.setMouseEnabled(x=False, y=False)
            elif not self._is_collapsed(p) and p in self._hidden_by_collapse:
                for it in self._hidden_by_collapse.pop(p):
                    try:
                        it.show()
                    except RuntimeError:
                        pass
                ax.setStyle(showValues=True, tickLength=getattr(self, "_tick_len", -5))
                self._apply_grid(p)
                vb.setMouseEnabled(x=True, y=p in self._manual)

    def _set_pane_range(self, plot, lo: float, hi: float):
        self._manual.add(plot)
        plot.getViewBox().setMouseEnabled(x=True, y=True)  # con escala manual, arrastrar también la mueve en vertical
        plot.setYRange(lo, hi, padding=0)
        self._pane_y[self._pane_key[plot]] = (lo, hi)

    def _on_pane_yrange(self, plot):
        if plot in self._manual and plot in self._pane_key:
            self._pane_y[self._pane_key[plot]] = tuple(plot.getViewBox().viewRange()[1])

    def _reset_pane(self, plot):
        """Devuelve el panel a su escala automática."""
        self._manual.discard(plot)
        self._pane_y.pop(self._pane_key.get(plot), None)
        plot.getViewBox().setMouseEnabled(x=True, y=False)
        if plot in self._pane_default:
            plot.setYRange(*self._pane_default[plot], padding=0)
        else:
            self._on_xrange()

    def _row_keys(self) -> list:
        return [self._pane_key.get(p, "main") for p in self.plots]

    def _apply_heights(self):
        """Reparte la altura: por defecto el precio ocupa más; ajustada a mano, en píxeles exactos
        (el reparto proporcional de Qt movería también los paneles que no se tocaron)."""
        lay = self.ci.layout
        for row in range(lay.rowCount()):
            lay.setRowMinimumHeight(row, 0)
            lay.setRowMaximumHeight(row, 16777215)
        folded = [self._is_collapsed(p) for p in self.plots]
        if not self.pane_heights and not any(folded):
            lay.setRowStretchFactor(0, max(3, len(self.plots)))
            for row in range(1, len(self.plots)):
                lay.setRowStretchFactor(row, 1)
        else:
            if self.pane_heights:
                fractions = [self.pane_heights.get(k, DEFAULT_PANE_FRACTION) for k in self._row_keys()]
            else:  # mismo reparto que el automático
                fractions = [max(3, len(self.plots)) if row == 0 else 1 for row in range(len(self.plots))]
            # el último panel lleva además el eje de fechas
            strip = [COLLAPSED_HEIGHT + (p.getAxis("bottom").height() if row == len(self.plots) - 1 else 0)
                     for row, p in enumerate(self.plots)]
            margins = lay.getContentsMargins()
            free = self.viewport().height() - margins[1] - margins[3] - lay.verticalSpacing() * (len(fractions) - 1)
            free -= sum(h for h, c in zip(strip, folded) if c)
            total = sum(f for f, c in zip(fractions, folded) if not c) or 1
            for row, f in enumerate(fractions):
                lay.setRowFixedHeight(row, strip[row] if folded[row] else max(MIN_PANE_HEIGHT, free * f / total))
        lay.invalidate()
        self._apply_collapsed()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if (self.pane_heights or self.collapsed) and self.plots:
            self._apply_heights()
        QtCore.QTimer.singleShot(0, self._place_price_tag)  # después de que el layout acomode los ejes

    def reset_panes(self):
        """Altura y escala originales en todos los paneles inferiores."""
        self.pane_heights = {}
        self.collapsed = set()
        for p in list(self.plots[1:]):
            self._reset_pane(p)
        self._pane_y.clear()
        self._apply_heights()
        self.panesResized.emit()

    def _drag_divider(self, drag, dy: float):
        row = drag["row"]
        if len(drag["heights"]) != len(self.plots):
            return  # cambiaron los indicadores durante el arrastre
        heights = list(drag["heights"])
        pair = heights[row] + heights[row + 1]
        heights[row] = min(max(heights[row] + dy, MIN_PANE_HEIGHT), pair - MIN_PANE_HEIGHT)
        heights[row + 1] = pair - heights[row]
        old = dict(self.pane_heights)
        self.pane_heights = {k: h / sum(heights) for k, h in zip(self._row_keys(), heights)}
        for k in self.collapsed & set(self.pane_heights):  # el plegado conserva la altura que tenía abierto
            self.pane_heights[k] = old.get(k, DEFAULT_PANE_FRACTION)
        self._apply_heights()

    def _drag_axis(self, drag, dy: float):
        plot = next((p for p, k in self._pane_key.items() if k == drag["key"]), None)
        if plot is None:
            return
        lo, hi = drag["range"]
        half = (hi - lo) / 2 * 1.006 ** dy  # hacia abajo se comprime, hacia arriba se estira
        self._set_pane_range(plot, (lo + hi) / 2 - half, (lo + hi) / 2 + half)

    def mousePressEvent(self, ev):
        shift = bool(ev.modifiers() & QtCore.Qt.ShiftModifier)
        self.layer.set_shift(shift)
        if shift:  # pyqtgraph ya eligió quién recibe el arrastre al pasar el mouse: se le hace elegir de nuevo
            self.scene().lastHoverEvent = None
        zone = self._zone_at(ev.position()) if ev.button() == QtCore.Qt.LeftButton else None
        if zone and zone[0] == "divider":
            self._drag = {"kind": "divider", "row": zone[1], "y": ev.position().y(),
                          "heights": [p.geometry().height() for p in self.plots]}
        elif zone:
            self._drag = {"kind": "axis", "key": self._pane_key[zone[1]], "y": ev.position().y(),
                          "range": tuple(zone[1].getViewBox().viewRange()[1])}
        else:
            super().mousePressEvent(ev)
            return
        ev.accept()

    def mouseMoveEvent(self, ev):
        if self._drag:
            dy = ev.position().y() - self._drag["y"]
            if dy:
                (self._drag_divider if self._drag["kind"] == "divider" else self._drag_axis)(self._drag, dy)
            ev.accept()
            return
        super().mouseMoveEvent(ev)
        zone = self._zone_at(ev.position())
        if zone:
            self.viewport().setCursor(QtCore.Qt.SplitVCursor if zone[0] == "divider" else QtCore.Qt.SizeVerCursor)
        else:
            self.viewport().unsetCursor()

    def mouseReleaseEvent(self, ev):
        if self._drag:
            if self._drag["kind"] == "divider":
                self.panesResized.emit()
            self._drag = None
            ev.accept()
            self.layer.set_shift(False)
            return
        super().mouseReleaseEvent(ev)
        self.layer.set_shift(False)  # después: pyqtgraph entrega el clic al soltar el botón

    def mouseDoubleClickEvent(self, ev):
        zone = self._zone_at(ev.position())
        if zone and zone[0] == "axis":
            self._reset_pane(zone[1])
        elif zone:
            self.pane_heights = {}
            self._apply_heights()
            self.panesResized.emit()
        elif ev.button() == QtCore.Qt.LeftButton and self.layer.tool == "cursor" and self._pane_at(ev.position()):
            self.toggle_pane(self._pane_at(ev.position()))
        else:
            super().mouseDoubleClickEvent(ev)
            return
        ev.accept()

    def wheelEvent(self, ev):
        zone = self._zone_at(ev.position())
        if zone and zone[0] == "axis":
            lo, hi = zone[1].getViewBox().viewRange()[1]
            half = (hi - lo) / 2 * 0.999 ** ev.angleDelta().y()
            self._set_pane_range(zone[1], (lo + hi) / 2 - half, (lo + hi) / 2 + half)
            ev.accept()
            return
        super().wheelEvent(ev)

    def _make_tag(self) -> QtWidgets.QLabel:
        tag = QtWidgets.QLabel(self)
        theme.style(tag, "background:#434651; color:#ffffff; padding:1px 5px; border-radius:2px; font-size:11px;")
        tag.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        tag.hide()
        return tag

    def _hide_tags(self):
        self._ytag.hide()
        self._xtag.hide()

    def _axis_rect(self, axis) -> QtCore.QRect:
        """Rectángulo del eje en píxeles del widget. No se usa su boundingRect porque, con la
        cuadrícula encendida, abarca todo el panel (el eje es quien dibuja las líneas)."""
        local = QtCore.QRectF(QtCore.QPointF(0, 0), axis.size())
        return self.mapFromScene(axis.mapRectToScene(local)).boundingRect()

    def _show_tags(self, plot, pos, y_value: float, i: int):
        """Etiqueta con el valor del cursor sobre el eje derecho del panel y su fecha sobre el eje de abajo."""
        ax = plot.getAxis("right")
        if getattr(ax, "view_base", None):
            text = f"{(y_value / ax.view_base - 1) * 100:+.2f}%"
        elif getattr(ax, "percent", False):
            text = f"{y_value:+.2f}%"
        elif getattr(ax, "log", False):
            text = fmt_price(10 ** y_value)
        else:
            text = fmt_value(y_value)
        r = self._axis_rect(ax)
        ypix = self.mapFromScene(pos).y()
        self._ytag.setText(text)
        self._ytag.adjustSize()
        top = min(max(ypix - self._ytag.height() // 2, r.top()), r.bottom() - self._ytag.height())
        self._ytag.move(r.left() + 1, top)
        self._ytag.show()
        self._ytag.raise_()

        self._show_date_tag(plot, i)

    def _show_date_tag(self, plot, i: int):
        n = len(self.df)
        if not 0 <= i < n:  # zona sin velas (p. ej. la nube de Ichimoku proyectada)
            self._xtag.hide()
            return
        bottom = self._axis_rect(self.plots[-1].getAxis("bottom"))
        self._xtag.setText(fmt_date(self.df.index[i], self._intraday))
        self._xtag.adjustSize()
        xpix = self.mapFromScene(plot.getViewBox().mapViewToScene(QtCore.QPointF(i, 0))).x()
        left = min(max(xpix - self._xtag.width() // 2, bottom.left()), bottom.right() - self._xtag.width())
        self._xtag.move(left, bottom.top() + 1)
        self._xtag.show()
        self._xtag.raise_()

    def apply_theme(self):
        """Fondo y etiquetas del tema actual; lo dibujado se actualiza al redibujar la gráfica."""
        self.setBackground(theme.c(BG))

    # ------------------------------------------------------------------ util
    def _new_plot(self, row: int, n_dates) -> pg.PlotItem:
        axis = DateAxis(orientation="bottom")
        axis.set_dates(n_dates, self._intraday)
        p = self.addPlot(row=row, col=0, viewBox=DrawViewBox(self.layer),
                         axisItems={"bottom": axis, "right": ValueAxis(orientation="right")})
        p.showAxis("right")
        p.hideAxis("left")
        p.getAxis("right").setWidth(80)
        self._apply_grid(p)
        p.setMenuEnabled(False)
        p.hideButtons()
        for name in ("right", "bottom"):
            ax = p.getAxis(name)
            ax.setPen(pg.mkPen(theme.c(GRID)))
            ax.setTextPen(pg.mkPen(theme.c(FG)))
        vb = p.getViewBox()
        vb.setMouseEnabled(x=True, y=False)
        vb.disableAutoRange()
        pen = pg.mkPen(theme.c(CROSS), width=1, style=QtCore.Qt.DashLine)
        v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        p.addItem(v, ignoreBounds=True)
        p.addItem(h, ignoreBounds=True)
        v.setZValue(100)
        h.setZValue(100)
        v.hide()  # sin esto, al redibujar quedaría una línea fija en la primera vela hasta mover el mouse
        h.hide()
        self._cross[p] = (v, h)
        lbl = LegendItem(self)
        lbl.setParentItem(vb)
        lbl.setPos(6, self.legend_top if not self.plots else 2)  # aquí self.plots aún no incluye este panel
        lbl.setZValue(200)
        self._labels[p] = lbl
        self._series[p] = []
        self._entries[p] = []
        self.plots.append(p)
        return p

    def _apply_grid(self, plot):
        on = self.grid_alpha > 0
        plot.showGrid(x=on, y=on, alpha=self.grid_alpha if on else None)

    def set_grid(self, alpha: float):
        """Cambia la cuadrícula de todos los paneles sin redibujar la gráfica."""
        self.grid_alpha = alpha
        for p in self.plots:
            self._apply_grid(p)

    def _reg(self, plot, xs, ys):
        self._series[plot].append((np.asarray(xs, float), np.asarray(ys, float)))

    def _draw_out(self, plot, out: Out, x, color, tf=None):
        xs = x + out.offset
        if out.kind == "vprofile":
            df, o = self.df, out.opts or {}
            lo, hi = df["Low"].to_numpy(float), df["High"].to_numpy(float)
            item = VisibleProfileItem(tf(lo) if tf else lo, tf(hi) if tf else hi, df["Volume"].to_numpy(float),
                                      (df["Close"] >= df["Open"]).to_numpy(), int(o.get("rows", 24)),
                                      float(o.get("va", 70)), float(o.get("width", 30)), bool(o.get("right", True)))
            plot.addItem(item, ignoreBounds=True)
            return
        if out.kind == "vline":
            pen = pg.mkPen(color, width=out.width, style=_STYLES.get(out.style, QtCore.Qt.SolidLine))
            labels = out.labels or [None] * len(out.y)
            for pos, text in zip(out.y, labels):
                opts = {"position": 0.97, "color": color, "anchors": [(0, 0), (0, 0)]} if text else None
                plot.addItem(pg.InfiniteLine(pos=float(pos), angle=90, movable=False, pen=pen,
                                             label=text, labelOpts=opts), ignoreBounds=True)
            return
        y = tf(out.y) if tf else out.y
        if out.kind == "line":
            pen = pg.mkPen(color, width=out.width, style=_STYLES.get(out.style, QtCore.Qt.SolidLine))
            plot.addItem(pg.PlotDataItem(xs, y, pen=pen, connect="finite"))
            if out.autoscale:
                self._reg(plot, xs, y)
            if out.text:
                fin = np.flatnonzero(np.isfinite(y))
                if fin.size:
                    j = fin[-1]
                    t = pg.TextItem(out.text, color=color[:7], anchor=(1, 1))
                    f = QtGui.QFont()
                    f.setPointSize(8)
                    t.setFont(f)
                    t.setPos(xs[j], y[j])
                    plot.addItem(t, ignoreBounds=True)
        elif out.kind == "dots":
            m = np.isfinite(y)
            plot.addItem(pg.ScatterPlotItem(x=xs[m], y=y[m], size=4, pen=None, brush=pg.mkBrush(color)))
            self._reg(plot, xs[m], y[m])
        elif out.kind == "hist":
            m = np.isfinite(y) & (y != 0)  # barras de altura cero: mismo problema de Qt que las velas
            up = out.mask if out.mask is not None else (y >= 0)
            c_up = out.color or "#26a69aa0"
            c_dn = out.color2 or "#ef5350a0"
            for sel, c in ((m & up, c_up), (m & ~up, c_dn)):
                if sel.any():
                    plot.addItem(pg.BarGraphItem(x=xs[sel], height=y[sel], width=0.7,
                                                 brush=pg.mkBrush(c), pen=pg.mkPen(None)))
            self._reg(plot, xs[m], y[m])
            self._reg(plot, xs[m], np.zeros(m.sum()))
        elif out.kind in ("fill", "cloud"):
            y2 = tf(out.y2) if tf else out.y2
            m = np.isfinite(y) & np.isfinite(y2)
            if not m.any():
                return
            xa, a, b = xs[m], y[m], y2[m]
            if out.kind == "fill":
                pairs = [(a, b, out.color or "#2962ff20")]
            else:
                pairs = [(a, np.where(a > b, b, a), out.color or "#43a04733"),
                         (b, np.where(b > a, a, b), out.color2 or "#e5393533")]
            for up_, lo_, c in pairs:
                f = pg.FillBetweenItem(pg.PlotCurveItem(xa, up_), pg.PlotCurveItem(xa, lo_), brush=pg.mkBrush(c))
                f.setZValue(-10)
                plot.addItem(f)

    def _draw_computed(self, plot, comp: Computed, x, palette_iter, tf=None):
        outs = []
        if comp.hidden:  # oculto: no se dibuja, pero queda en la leyenda para volver a mostrarlo
            self._entries[plot].append(_Entry(comp.label, [], comp.index, True))
            return
        for o in comp.outs:
            color = o.color
            if color is None and o.kind in ("line", "dots"):
                color = next(palette_iter)
            if color is None:
                color = "#2962ff"
            color = theme.c(color)  # una línea blanca no se vería en el tema claro
            self._draw_out(plot, o, x, color, tf)
            if o.show_value:
                outs.append((o, color if len(color) <= 7 else color[:7]))
        self._entries[plot].append(_Entry(comp.label, outs, comp.index))

    # --------------------------------------------------------------- render
    def set_data(self, df: pd.DataFrame, computed: list[Computed], chart_type: str = "Velas",
                 intraday: bool = False, keep_view: bool = False, title: str = "", log: bool = False,
                 comparisons: list | None = None, pct_scale: bool = False, last_price: float | None = None,
                 opts: dict | None = None, inverted: bool = False):
        """``comparisons``: [(símbolo, cierres alineados a df, color)]. Si hay alguna, la gráfica de
        precio pasa a % de cambio desde la primera vela del periodo (la escala log se ignora).
        ``pct_scale``: etiqueta el eje en % contra la primera vela visible.
        ``last_price``: precio real, si difiere del último cierre de ``df`` (Renko, Kagi…).
        ``opts``: datos del tipo de gráfica (cuadro y dirección de las columnas de Point & Figure)."""
        old = None
        if keep_view and self.plots and self.df is not None:
            old = (self.plots[0].getViewBox().viewRange()[0], len(self.df))
        for p in list(self.plots):
            try:
                p.getViewBox().sigXRangeChanged.disconnect(self._on_xrange)
                if p is self.plots[0]:
                    p.getViewBox().sigXRangeChanged.disconnect(self._emit_range)
            except (RuntimeError, TypeError):
                pass
        self.clear()
        self._hide_tags()
        self._last_line = None
        self._pricetag.hide()
        self.plots, self._series, self._entries, self._labels, self._cross = [], {}, {}, {}, {}
        self._fixed = set()
        self._pane_key, self._pane_default, self._manual = {}, {}, set()
        self._hidden_by_collapse = {}
        if not keep_view:
            self._pane_y.clear()  # otro símbolo o intervalo: la escala elegida ya no aplica
        self._watermark = (None, None)
        self._alert_items = []
        self._baseline = None
        self._pct_closes = None
        self._cursor_i = None  # las líneas son nuevas y empiezan ocultas
        self.df = df
        self._intraday = intraday
        n = len(df)
        if n == 0:
            return
        x = np.arange(n, dtype=float)
        idx = df.index

        main = self._new_plot(0, idx)
        main.getViewBox().invertY(inverted)  # escala invertida: los máximos quedan abajo
        # Modo comparar: todo lo que va sobre el precio se expresa en % desde la primera vela.
        # Escala log: el precio principal se dibuja como log10(precio) y el eje
        # muestra los precios reales. Los paneles inferiores siguen lineales.
        closes = df["Close"].to_numpy(float)
        first = closes[np.isfinite(closes)]
        pct_base = float(first[0]) if comparisons and first.size and first[0] else None
        if pct_base:
            log = False
            tf = lambda y, b=pct_base: (np.asarray(y, dtype=float) / b - 1) * 100  # noqa: E731
        else:
            tf = _log10 if log else None
        main.getAxis("right").log = log
        main.getAxis("right").percent = bool(pct_base)
        o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        if chart_type in CANDLE_TYPES:
            if chart_type == "Heikin Ashi":
                o, h, l, c = heikin_ashi(df)
            if tf:
                o, h, l, c = tf(o), tf(h), tf(l), tf(c)
            main.addItem(CandleItem(x, o, h, l, c, bars=chart_type == "Barras OHLC",
                                    hollow=chart_type == "Velas huecas"))
            self._reg(main, x, l)
            self._reg(main, x, h)
        elif chart_type == "Kagi":
            if tf:
                o, h, l, c = tf(o), tf(h), tf(l), tf(c)
            main.addItem(KagiItem(x, o, c))
            self._reg(main, x, l)
            self._reg(main, x, h)
        elif chart_type == "Point & Figure":
            main.addItem(PointFigureItem(x, o, c, (opts or {})["up"], (opts or {})["box"], tf))
            self._reg(main, x, tf(l) if tf else l)
            self._reg(main, x, tf(h) if tf else h)
        elif chart_type == "Línea base":
            if tf:
                c = tf(c)
            self._baseline = BaselineItem(x, c)
            main.addItem(self._baseline)
            self._reg(main, x, c)
        else:
            pen = pg.mkPen("#2962ff", width=2)
            if tf:
                c = tf(c)
            if chart_type == "Área":
                item = pg.PlotDataItem(x, c, pen=pen, fillLevel=float(np.nanmin(c)),
                                       brush=pg.mkBrush("#2962ff30"))
            else:
                item = pg.PlotDataItem(x, c, pen=pen)
            main.addItem(item)
            self._reg(main, x, c)

        last = df["Close"].iloc[-1] if last_price is None else last_price
        prev = df["Close"].iloc[-2] if n > 1 else last
        lc = theme.up() if last >= prev else theme.down()
        last_line = pg.InfiniteLine(
            pos=float(tf(last)) if tf else last, angle=0, movable=False, pen=pg.mkPen(lc, width=1, style=QtCore.Qt.DotLine))
        main.addItem(last_line, ignoreBounds=True)
        self._last_line = last_line
        # texto base de la etiqueta del eje; la cuenta regresiva se agrega abajo
        self._last_text = f"{fmt_price(last)}\n{(last / pct_base - 1) * 100:+.2f}%" if pct_base else fmt_price(last)
        theme.style(self._pricetag, PRICE_TAG_STYLE.format(color=lc))
        self._pricetag.setText("")
        main.getViewBox().sigRangeChanged.connect(self._place_price_tag)
        self._last_value = float(last)
        if pct_scale and not log and not pct_base:
            self._pct_closes = closes
        self._candle_end = None

        if title:
            wm = pg.TextItem(title, color="#00000012" if theme.is_light() else "#ffffff12", anchor=(0.5, 0.5))
            f = QtGui.QFont()
            f.setPointSize(self.watermark_size)
            f.setBold(True)
            wm.setFont(f)
            wm.setParentItem(main.getViewBox())
            wm.setZValue(-100)
            self._watermark = (wm, main.getViewBox())
            main.getViewBox().sigResized.connect(self._place_watermark)
            self._place_watermark()

        # en línea/área el precio ya usa el primer azul: los indicadores empiezan con el siguiente color
        pal = iter((PALETTE[1:] if chart_type in ("Línea", "Área") else PALETTE) * 20)
        overlays = [cp for cp in computed if cp.defn.overlay]
        panes = [cp for cp in computed if not cp.defn.overlay]
        max_off = 0
        for cp in overlays:
            self._draw_computed(main, cp, x, pal, tf)
            max_off = max([max_off] + [o.offset for o in cp.outs])

        if pct_base:
            main.addItem(pg.InfiniteLine(pos=0, angle=0, movable=False,
                                         pen=pg.mkPen("#787b86", width=1, style=QtCore.Qt.DashLine)), ignoreBounds=True)
            rows = [_Entry(title or "Precio", [], compare=(tf(closes), closes), color="#d1d4dc")]
            for sym, arr, color in comparisons:
                arr = np.asarray(arr, dtype=float)
                fin = arr[np.isfinite(arr)]
                if not fin.size or not fin[0]:
                    continue
                pct = (arr / fin[0] - 1) * 100  # cada símbolo arranca en 0 % en su primer dato del periodo
                main.addItem(pg.PlotDataItem(x, pct, pen=pg.mkPen(color, width=1.8), connect="finite"))
                self._reg(main, x, pct)
                rows.append(_Entry(sym, [], compare=(pct, arr), color=color, removable=True))
            self._entries[main][:0] = rows  # las comparaciones van arriba de los indicadores

        seen: dict = {}
        for k, cp in enumerate(panes, start=1):
            p = self._new_plot(k, idx)
            p.setXLink(main)
            seen[cp.defn.key] = seen.get(cp.defn.key, 0) + 1
            self._pane_key[p] = f"{cp.defn.key}#{seen[cp.defn.key]}"  # dos RSI son dos paneles distintos
            p.getViewBox().sigYRangeChanged.connect(lambda *_a, plot=p: self._on_pane_yrange(plot))
            self._draw_computed(p, cp, x, iter(PALETTE * 10))
            for lv in cp.defn.levels:
                p.addItem(pg.InfiniteLine(pos=lv, angle=0, movable=False,
                                          pen=pg.mkPen("#787b86", width=1, style=QtCore.Qt.DashLine)),
                          ignoreBounds=True)
            if cp.defn.yrange:
                lo, hi = cp.defn.yrange
                pad = (hi - lo) * 0.06
                p.setYRange(lo - pad, hi + pad, padding=0)
                self._pane_default[p] = (lo - pad, hi + pad)
                self._fixed.add(p)
            if self._pane_key[p] in self._pane_y:
                self._set_pane_range(p, *self._pane_y[self._pane_key[p]])

        self._apply_heights()  # el precio ocupa más espacio, salvo que se haya ajustado a mano
        for p in self.plots[:-1]:
            # solo el último panel muestra las fechas; en los demás el eje queda activo pero sin
            # texto ni altura, porque un eje oculto no dibuja las líneas verticales de la cuadrícula
            ax = p.getAxis("bottom")
            ax.setStyle(showValues=False, tickLength=0)
            ax.setHeight(0)
        for p in self.plots:
            p.getViewBox().setLimits(xMin=-50, xMax=n + max_off + 80, minXRange=8)

        main.getViewBox().sigXRangeChanged.connect(self._on_xrange)
        if old is not None:
            (x0, x1), old_n = old
            shift = (n - old_n) if x1 >= old_n - 3 else 0
            main.setXRange(x0 + shift, x1 + shift, padding=0)
        elif pct_base:
            self.fit_all()  # comparando se ve todo el periodo: las líneas arrancan en 0 % a la izquierda
        else:
            self.show_last(self.default_bars)
        self._on_xrange()
        self.layer.set_context(main, self.plots[1:], df, log, pct_base)
        self._apply_collapsed()  # los dibujos de los paneles ya están puestos: también se ocultan
        self._update_labels(n - 1)
        self._synced_range = None
        # se conecta al final: armar la gráfica no cuenta como un movimiento del usuario
        main.getViewBox().sigXRangeChanged.connect(self._emit_range)

    def set_alert_lines(self, levels: list[tuple[float, str]]):
        """Líneas punteadas naranjas con 🔔 para las alertas de precio del símbolo actual."""
        main = self.plots[0] if self.plots else None
        for it in getattr(self, "_alert_items", []):
            try:
                main.removeItem(it)
            except (RuntimeError, AttributeError, ValueError):
                pass
        self._alert_items = []
        if main is None:
            return
        for price, text in levels:
            y = self.layer.p2y(price)
            if not np.isfinite(y):
                continue
            line = pg.InfiniteLine(
                pos=y, angle=0, movable=False, pen=pg.mkPen("#ff9800", width=1, style=QtCore.Qt.DashDotLine),
                label=f"🔔 {text}", labelOpts={"position": 0.86, "color": "#ff9800", "fill": theme.c("#131722dd"),
                                               "anchors": [(1, 1), (1, 1)]})
            line.setZValue(40)
            main.addItem(line, ignoreBounds=True)
            self._alert_items.append(line)

    def _place_watermark(self, *a):
        wm, vb = getattr(self, "_watermark", (None, None))
        if wm is None:
            return
        try:
            r = vb.boundingRect()
            wm.setPos(r.center())
        except RuntimeError:
            pass

    # ---------------------------------------------------------------- vista
    def show_last(self, bars: int):
        if not self.plots or self.df is None:
            return
        n = len(self.df)
        self.plots[0].setXRange(max(-1, n - bars), n + 4, padding=0)

    def fit_all(self):
        if self.plots and self.df is not None:
            self.plots[0].setXRange(-2, len(self.df) + 2, padding=0)

    def set_legend_top(self, y: int):
        self.legend_top = y
        if self.plots:
            self._labels[self.plots[0]].setPos(6, y)

    def set_compact(self, compact: bool):
        """Símbolo de fondo más chico cuando hay varias gráficas en la ventana."""
        self.watermark_size = 24 if compact else 44
        wm = getattr(self, "_watermark", (None, None))[0]
        if wm is not None:
            f = wm.textItem.font()
            f.setPointSize(self.watermark_size)
            wm.setFont(f)
            self._place_watermark()

    # ------------------------------------------------------- sincronización
    def visible_times(self):
        """(inicio, fin) de lo que se ve, en fechas epoch; None si aún no hay datos."""
        if not self.plots or self.df is None or not len(self.df):
            return None
        x0, x1 = self.plots[0].getViewBox().viewRange()[0]
        return float(self.layer.x2t(x0)), float(self.layer.x2t(x1))

    def show_times(self, t0: float, t1: float):
        """Muestra ese rango de fechas sin avisar de vuelta a las demás gráficas."""
        if not self.plots or self.df is None or not len(self.df):
            return
        vb = self.plots[0].getViewBox()
        self._synced_range = "aplicando"
        self.plots[0].setXRange(self.layer.t2x(t0), self.layer.t2x(t1), padding=0)
        self._synced_range = tuple(vb.viewRange()[0])  # ya con los límites de esta gráfica aplicados

    def _emit_range(self, *_a):
        s = self._synced_range
        if s == "aplicando":
            return
        if s is not None and np.allclose(self.plots[0].getViewBox().viewRange()[0], s):
            return  # el eco de show_times, si pyqtgraph lo avisa hasta el siguiente dibujado
        self._synced_range = None
        self.rangeChanged.emit()

    def show_cursor_at(self, t: float):
        """Cursor puesto desde otra gráfica: línea vertical y valores de la vela que contiene esa fecha."""
        if not self.plots or self.df is None or not len(self.df):
            return
        if not np.isfinite(t):
            self.clear_cursor()
            return
        if not len(self.layer._ts):
            return
        i = int(np.floor(self.layer.t2x(t)))  # la vela que contiene esa fecha, también en la última
        self._erase_cursor_column()
        self._cursor_i = i
        for v, h in self._cross.values():
            v.setPos(i)
            v.show()
            h.hide()
        self._ytag.hide()
        self._show_date_tag(self.plots[0], i)
        self._update_labels(i)

    def _on_xrange(self, *args):
        if not self.plots:
            return
        x0, x1 = self.plots[0].getViewBox().viewRange()[0]
        self._update_pct_base(x0)
        for p in self.plots:
            if p in self._fixed or p in self._manual:
                continue
            lo, hi = np.inf, -np.inf
            for xs, ys in self._series.get(p, []):
                a = np.searchsorted(xs, x0, "left")
                b = np.searchsorted(xs, x1, "right")
                seg = ys[a:b]
                seg = seg[np.isfinite(seg)]
                if seg.size:
                    lo = min(lo, seg.min())
                    hi = max(hi, seg.max())
            if np.isfinite(lo) and np.isfinite(hi):
                if hi - lo <= 0:
                    d = abs(hi) * 0.01 or 1.0
                    lo, hi = lo - d, hi + d
                p.setYRange(lo, hi, padding=0.07)
                if p is self.plots[0] and self._baseline is not None:
                    self._baseline.set_base((lo + hi) / 2)

    def _update_pct_base(self, x0: float):
        """Escala %: el 0 % es el cierre de la primera vela visible, como en TradingView."""
        closes = self._pct_closes
        axis = self.plots[0].getAxis("right")
        if closes is None or not len(closes):
            axis.set_view_base(None)
            return
        i = min(max(int(np.ceil(x0)), 0), len(closes) - 1)
        base = float(closes[i])
        base = base if np.isfinite(base) and base > 0 else None
        axis.set_view_base(base)
        if base and self._last_line is not None:
            self._last_text = f"{fmt_price(self._last_value)}\n{(self._last_value / base - 1) * 100:+.2f}%"
            self._tick_countdown()

    # ------------------------------------------------------------- cursor
    def _on_mouse(self, pos):
        if not self.plots or self.df is None:
            return
        hovered, i = None, None
        for p in self.plots:
            vb = p.getViewBox()
            if vb.sceneBoundingRect().contains(pos):
                pt = vb.mapSceneToView(pos)
                hovered, i = p, int(round(pt.x()))
                self._cross[p][1].setPos(pt.y())
                break
        if hovered is None:
            self._hide_tags()
            return
        self._erase_cursor_column()
        self._cursor_i = i
        for p, (v, h) in self._cross.items():
            v.setPos(i)
            v.show()
            h.setVisible(p is hovered)
        self._show_tags(hovered, pos, pt.y(), i)
        self._update_labels(i)
        self.cursorTime.emit(float(self.layer.x2t(i)))

    def leaveEvent(self, ev):
        super().leaveEvent(ev)
        self.clear_cursor()
        self.cursorTime.emit(float("nan"))

    def _erase_cursor_column(self):
        """Pide repintar la columna donde estaba la línea. En una gráfica que sigue al cursor de otra,
        Qt no siempre borra la posición anterior y quedaban líneas fijas en pantalla. Repintar toda la
        vista en cada movimiento costaría unos 40 ms por gráfica; una columna, casi nada."""
        if self._cursor_i is None or not self.plots:
            return
        vb = self.plots[0].getViewBox()
        x = self.mapFromScene(vb.mapViewToScene(QtCore.QPointF(self._cursor_i, 0))).x()
        self.viewport().update(QtCore.QRect(x - 4, 0, 9, self.viewport().height()))

    def clear_cursor(self):
        self._erase_cursor_column()
        self._cursor_i = None
        self._hide_tags()
        for v, h in self._cross.values():
            v.hide()
            h.hide()
        if self.df is not None and len(self.df):
            self._update_labels(len(self.df) - 1)

    def refresh_labels(self):
        if self.df is not None and len(self.df):
            self._update_labels(getattr(self, "_last_i", len(self.df) - 1))

    def legend_menu(self, entry: _Entry, screen_pos):
        m = QtWidgets.QMenu(self)
        if entry.removable:
            m.addAction(f"✕  Quitar comparación con {entry.label}", lambda: self.compareRemoved.emit(entry.label))
            m.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))
            return
        m.addAction("⚙  Configurar…", lambda: self.legendAction.emit("cfg", entry.index))
        m.addAction("◉  Mostrar" if entry.hidden else "👁  Ocultar", lambda: self.legendAction.emit("hide", entry.index))
        m.addSeparator()
        m.addAction("✕  Quitar", lambda: self.legendAction.emit("del", entry.index))
        m.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))

    def _update_labels(self, i: int):
        df = self.df
        n = len(df)
        ic = min(max(i, 0), n - 1)
        self._last_i = i
        icon = "color:#b2b5be; text-decoration:none"
        for p, entries in self._entries.items():
            legend = self._labels[p]
            legend.entries = entries
            lines = []
            for row, e in enumerate(entries):
                if e.compare is not None:
                    pct, raw = e.compare
                    pv = pct[i] if 0 <= i < len(pct) else np.nan
                    rv = raw[i] if 0 <= i < len(raw) else np.nan
                    pv_txt = f"{pv:+.2f}%" if np.isfinite(pv) else "—"
                    text = (f"<span style='color:{e.color}'>{e.label}&nbsp; {pv_txt}</span>"
                            f"&nbsp; <span style='color:#787b86'>{fmt_price(rv)}</span>")
                    if row == legend.hover_row and e.removable:
                        text += f"&nbsp;&nbsp; <a href='rmcmp' style='{icon}'>✕</a>"
                    lines.append(f"<div>{text}</div>")
                    continue
                if e.hidden:
                    text = f"<span style='color:#5d606b'>{e.label}&nbsp; oculto</span>"
                else:
                    vals = []
                    for o, color in e.outs:
                        j = i - o.offset
                        v = o.y[j] if 0 <= j < len(o.y) else np.nan
                        vals.append(f"<span style='color:{color}'>{fmt_value(v)}</span>")
                    text = f"<span style='color:{FG}'>{e.label}</span>&nbsp; " + "&nbsp; ".join(vals)
                if row == legend.hover_row:
                    text += (f"&nbsp;&nbsp; <a href='cfg' style='{icon}'>⚙</a>&nbsp; "
                             f"<a href='hide' style='{icon}'>{'◉' if e.hidden else '👁'}</a>&nbsp; "
                             f"<a href='del' style='{icon}'>✕</a>")
                lines.append(f"<div>{text}</div>")  # un bloque por renglón: así se sabe qué indicador se tocó
            # como con la línea del cursor: si el texto se acorta, Qt puede no borrar el final del anterior
            before = legend.sceneBoundingRect()
            legend.setHtml(theme.css("".join(lines)))
            area = self.mapFromScene(before.united(legend.sceneBoundingRect())).boundingRect()
            self.viewport().update(area.adjusted(-2, -2, 2, 2))

        r = df.iloc[ic]
        prev = df["Close"].iloc[ic - 1] if ic > 0 else r["Open"]
        chg = r["Close"] - prev
        pct = chg / prev * 100 if prev else 0
        col = theme.up() if chg >= 0 else theme.down()
        date = fmt_date(df.index[ic], self._intraday)

        def kv(k, v):
            return f"<span style='color:{FG}'>{k}</span> <span style='color:{col}'>{v}</span>"

        self.hovered.emit(theme.css("&nbsp;&nbsp;".join([
            f"<span style='color:#d1d4dc'>{date}</span>",
            kv("A", fmt_price(r["Open"])), kv("Max", fmt_price(r["High"])),
            kv("Min", fmt_price(r["Low"])), kv("C", fmt_price(r["Close"])),
            f"<span style='color:{col}'>{fmt_change(chg)} ({pct:+.2f}%)</span>",
            kv("Vol", fmt_big(r["Volume"])),
        ])))
