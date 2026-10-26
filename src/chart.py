"""Widget de gráficas: velas + indicadores superpuestos + paneles inferiores."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .drawings import DrawingLayer, DrawViewBox
from .indicators import DOWN, UP, Computed, Out

BG = "#131722"
FG = "#b2b5be"
AXIS = "#2a2e39"
# pyqtgraph pinta la cuadrícula con el color del eje y la opacidad elegida; con AXIS (casi igual
# al fondo) no se distinguiría, así que los ejes usan un gris más claro
GRID = "#787b86"
CROSS = "#9598a1"
PALETTE = ["#2962ff", "#ff6d00", "#e040fb", "#00bcd4", "#ffd600", "#ec407a", "#76ff03", "#8d6e63", "#90caf9"]
CHART_TYPES = ["Velas", "Heikin Ashi", "Barras OHLC", "Línea", "Área"]

pg.setConfigOptions(antialias=True, background=BG, foreground=FG)

_STYLES = {"solid": QtCore.Qt.SolidLine, "dash": QtCore.Qt.DashLine, "dot": QtCore.Qt.DotLine}


# --------------------------------------------------------------------------- #
# Formato de números
# --------------------------------------------------------------------------- #
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

    def __init__(self, x, o, h, l, c, bars=False):
        super().__init__()
        self._pic = QtGui.QPicture()
        p = QtGui.QPainter(self._pic)
        pens = {True: pg.mkPen(UP), False: pg.mkPen(DOWN)}
        brushes = {True: pg.mkBrush(UP), False: pg.mkBrush(DOWN)}
        w = 0.36
        QPF = QtCore.QPointF
        for i in range(len(x)):
            oi, hi, li, ci = o[i], h[i], l[i], c[i]
            if not (np.isfinite(oi) and np.isfinite(ci)):
                continue
            xi = float(x[i])
            up = ci >= oi
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

    def tickValues(self, minVal, maxVal, size):
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(BG)
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
        self.grid_alpha = 0.22  # opacidad de la cuadrícula del fondo (0 = sin cuadrícula)
        self.scene().sigMouseMoved.connect(self._on_mouse)
        self.layer = DrawingLayer(self)

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
            ax.setPen(pg.mkPen(GRID))
            ax.setTextPen(pg.mkPen(FG))
        vb = p.getViewBox()
        vb.setMouseEnabled(x=True, y=False)
        vb.disableAutoRange()
        pen = pg.mkPen(CROSS, width=1, style=QtCore.Qt.DashLine)
        v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        p.addItem(v, ignoreBounds=True)
        p.addItem(h, ignoreBounds=True)
        v.setZValue(100)
        h.setZValue(100)
        h.hide()
        self._cross[p] = (v, h)
        lbl = LegendItem(self)
        lbl.setParentItem(vb)
        lbl.setPos(6, 2)
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
            self._draw_out(plot, o, x, color, tf)
            if o.show_value:
                outs.append((o, color if len(color) <= 7 else color[:7]))
        self._entries[plot].append(_Entry(comp.label, outs, comp.index))

    # --------------------------------------------------------------- render
    def set_data(self, df: pd.DataFrame, computed: list[Computed], chart_type: str = "Velas",
                 intraday: bool = False, keep_view: bool = False, title: str = "", log: bool = False,
                 comparisons: list | None = None):
        """``comparisons``: [(símbolo, cierres alineados a df, color)]. Si hay alguna, la gráfica de
        precio pasa a % de cambio desde la primera vela del periodo (la escala log se ignora)."""
        old = None
        if keep_view and self.plots and self.df is not None:
            old = (self.plots[0].getViewBox().viewRange()[0], len(self.df))
        for p in list(self.plots):
            try:
                p.getViewBox().sigXRangeChanged.disconnect(self._on_xrange)
            except (RuntimeError, TypeError):
                pass
        self.clear()
        self.plots, self._series, self._entries, self._labels, self._cross = [], {}, {}, {}, {}
        self._fixed = set()
        self._watermark = (None, None)
        self._alert_items = []
        self.df = df
        self._intraday = intraday
        n = len(df)
        if n == 0:
            return
        x = np.arange(n, dtype=float)
        idx = df.index

        main = self._new_plot(0, idx)
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
        if chart_type in ("Velas", "Heikin Ashi", "Barras OHLC"):
            if chart_type == "Heikin Ashi":
                o, h, l, c = heikin_ashi(df)
            if tf:
                o, h, l, c = tf(o), tf(h), tf(l), tf(c)
            main.addItem(CandleItem(x, o, h, l, c, bars=chart_type == "Barras OHLC"))
            self._reg(main, x, l)
            self._reg(main, x, h)
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

        last = df["Close"].iloc[-1]
        prev = df["Close"].iloc[-2] if n > 1 else last
        lc = UP if last >= prev else DOWN
        last_line = pg.InfiniteLine(
            pos=float(tf(last)) if tf else last, angle=0, movable=False, pen=pg.mkPen(lc, width=1, style=QtCore.Qt.DotLine),
            label=f"{fmt_price(last)}  ({(last / pct_base - 1) * 100:+.2f}%)" if pct_base else fmt_price(last),
            labelOpts={"position": 0.985, "color": "#ffffff", "fill": lc, "anchors": [(1, 1), (1, 1)]})
        main.addItem(last_line, ignoreBounds=True)

        if title:
            wm = pg.TextItem(title, color="#ffffff12", anchor=(0.5, 0.5))
            f = QtGui.QFont()
            f.setPointSize(44)
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

        for k, cp in enumerate(panes, start=1):
            p = self._new_plot(k, idx)
            p.setXLink(main)
            self._draw_computed(p, cp, x, iter(PALETTE * 10))
            for lv in cp.defn.levels:
                p.addItem(pg.InfiniteLine(pos=lv, angle=0, movable=False,
                                          pen=pg.mkPen("#787b86", width=1, style=QtCore.Qt.DashLine)),
                          ignoreBounds=True)
            if cp.defn.yrange:
                lo, hi = cp.defn.yrange
                pad = (hi - lo) * 0.06
                p.setYRange(lo - pad, hi + pad, padding=0)
                self._fixed.add(p)

        # proporciones: el precio ocupa más espacio
        lay = self.ci.layout
        lay.setRowStretchFactor(0, max(3, len(panes) + 1))
        for k in range(1, len(panes) + 1):
            lay.setRowStretchFactor(k, 1)
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
        self._update_labels(n - 1)

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
                label=f"🔔 {text}", labelOpts={"position": 0.86, "color": "#ff9800", "fill": "#131722dd",
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

    def _on_xrange(self, *args):
        if not self.plots:
            return
        x0, x1 = self.plots[0].getViewBox().viewRange()[0]
        for p in self.plots:
            if p in self._fixed:
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
            return
        for p, (v, h) in self._cross.items():
            v.setPos(i)
            v.show()
            h.setVisible(p is hovered)
        self._update_labels(i)

    def leaveEvent(self, ev):
        super().leaveEvent(ev)
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
            legend.setHtml("".join(lines))

        r = df.iloc[ic]
        prev = df["Close"].iloc[ic - 1] if ic > 0 else r["Open"]
        chg = r["Close"] - prev
        pct = chg / prev * 100 if prev else 0
        col = UP if chg >= 0 else DOWN
        date = df.index[ic].strftime("%d/%m/%Y %H:%M" if self._intraday else "%a %d/%m/%Y")

        def kv(k, v):
            return f"<span style='color:{FG}'>{k}</span> <span style='color:{col}'>{v}</span>"

        self.hovered.emit("&nbsp;&nbsp;".join([
            f"<span style='color:#d1d4dc'>{date}</span>",
            kv("A", fmt_price(r["Open"])), kv("Max", fmt_price(r["High"])),
            kv("Min", fmt_price(r["Low"])), kv("C", fmt_price(r["Close"])),
            f"<span style='color:{col}'>{fmt_change(chg)} ({pct:+.2f}%)</span>",
            kv("Vol", fmt_big(r["Volume"])),
        ]))
