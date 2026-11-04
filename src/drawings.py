"""Herramientas de dibujo sobre la gráfica de precio y los paneles de indicadores.

Los dibujos se guardan en coordenadas de mercado (fecha en segundos epoch y
precio real), así sobreviven a cambios de intervalo, de periodo o de escala
logarítmica. La capa los traduce a coordenadas de la vista al dibujarlos.
Los de un panel llevan la clave ``pane`` (p. ej. "rsi#1") y guardan el valor del indicador.
"""
from __future__ import annotations

import shutil
import uuid
from copy import deepcopy
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .indicators import FIB_LEVELS

IMG_DIR = Path.home() / ".chartview" / "images"

# (clave, texto del botón, descripción)
TOOLS = [
    ("cursor", "↖", "Cursor: mover y editar dibujos (Esc)"),
    ("hline", "─", "Línea horizontal (clic)"),
    ("hray", "⊢", "Rayo horizontal: desde el punto hacia la derecha (clic)"),
    ("vline", "│", "Línea vertical (clic)"),
    ("trend", "╱", "Línea de tendencia (arrastrar)"),
    ("ray", "⟶", "Rayo: línea que se extiende a la derecha (arrastrar)"),
    ("arrow", "↗", "Flecha (arrastrar)"),
    ("channel", "▱", "Canal paralelo (3 clics: dos para la línea y uno para el ancho)"),
    ("pitchfork", "Ψ", "Tridente de Andrews (3 clics: pivote, máximo y mínimo)"),
    ("rect", "▭", "Rectángulo (arrastrar)"),
    ("ellipse", "◯", "Círculo / elipse (arrastrar)"),
    ("pen", "✎", "Dibujo libre (arrastrar)"),
    ("text", "T", "Texto (clic)"),
    ("image", "🖼", "Imagen (clic o arrastrar para el tamaño)"),
    ("fib", "Fib", "Retroceso de Fibonacci (arrastrar de inicio a fin)"),
    ("fibext", "FE", "Extensión de Fibonacci por tendencia (3 clics: inicio, fin y retroceso)"),
    ("measure", "📏", "Medir precio, % y velas (dos clics o arrastrar; también Shift+clic)"),
    ("long", "▲", "Posición larga con riesgo/beneficio (clic o arrastrar hasta el objetivo)"),
    ("short", "▼", "Posición corta con riesgo/beneficio (clic o arrastrar hasta el objetivo)"),
    ("avwap", "⚓", "VWAP anclado: clic en la vela de inicio"),
    ("eraser", "⌫", "Borrador: clic sobre un dibujo"),
]
# grupos de la barra de dibujo: un botón por grupo; la flechita abre las demás herramientas del grupo
TOOL_GROUPS = [
    ("Cursor", ["cursor"]),
    ("Líneas", ["hline", "hray", "vline", "trend", "ray", "arrow"]),
    ("Canales", ["channel", "pitchfork"]),
    ("Formas", ["rect", "ellipse", "pen"]),
    ("Texto e imagen", ["text", "image"]),
    ("Fibonacci", ["fib", "fibext"]),
    ("Trading", ["measure", "long", "short", "avwap"]),
    ("Borrador", ["eraser"]),
]
DRAG_TOOLS = {"trend", "ray", "arrow", "rect", "ellipse", "pen", "fib", "image", "long", "short"}
STICKY_TOOLS = {"pen", "eraser"}  # siguen activas después de usarlas
THREE_CLICK_TOOLS = {"channel", "pitchfork", "fibext"}
FIB_EXTENSIONS = [(0, "#787b86"), (0.236, "#f44336"), (0.382, "#ff9800"), (0.5, "#4caf50"), (0.618, "#009688"),
                  (0.786, "#00bcd4"), (1, "#787b86"), (1.272, "#2962ff"), (1.618, "#9c27b0"), (2, "#e91e63"),
                  (2.618, "#f44336")]
GAIN, LOSS = "#26a69a", "#ef5350"


class DrawingHistory:
    """Deshacer y rehacer por símbolo; lo comparten todas las gráficas que lo muestran."""
    LIMIT = 100

    def __init__(self):
        self._by_symbol: dict = {}

    def baseline(self, symbol, drawings):
        if symbol not in self._by_symbol:
            self._by_symbol[symbol] = {"undo": [], "redo": [], "now": deepcopy(drawings)}

    def record(self, symbol, drawings):
        self.baseline(symbol, [])
        h = self._by_symbol[symbol]
        h["undo"].append(h["now"])
        del h["undo"][:-self.LIMIT]
        h["redo"].clear()
        h["now"] = deepcopy(drawings)

    def step(self, symbol, back: bool):
        """El estado anterior (o el siguiente), o None si no hay."""
        h = self._by_symbol.get(symbol)
        if not h or not h["undo" if back else "redo"]:
            return None
        h["redo" if back else "undo"].append(h["now"])
        h["now"] = h["undo" if back else "redo"].pop()
        return deepcopy(h["now"])


def _fmt(v):
    from .chart import fmt_price
    return fmt_price(v)


def _load_image(path: str) -> QtGui.QImage | None:
    img = QtGui.QImage(path)
    if img.isNull():
        return None
    # el eje Y de la vista crece hacia arriba: se voltea para verla derecha
    return img.flipped(QtCore.Qt.Vertical) if hasattr(img, "flipped") else img.mirrored(False, True)


# --------------------------------------------------------------------------- #
# Elementos gráficos
# --------------------------------------------------------------------------- #
class DrawViewBox(pg.ViewBox):
    """ViewBox que entrega los clics y arrastres a la capa de dibujo."""

    def __init__(self, layer: "DrawingLayer", *a, **k):
        super().__init__(*a, **k)
        self.layer = layer

    def mouseClickEvent(self, ev):
        if self.layer.click(self, ev):
            ev.accept()
            return
        super().mouseClickEvent(ev)

    def mouseDragEvent(self, ev, axis=None):
        if axis is None and self.layer.drag(self, ev):
            ev.accept()
            return
        super().mouseDragEvent(ev, axis)

    def wheelEvent(self, ev, axis=None):
        super().wheelEvent(ev, 0 if axis is None else axis)  # la rueda sobre la gráfica solo acerca en horizontal


class ShapeROI(pg.ROI):
    """Rectángulo, elipse o imagen redimensionable con relleno."""

    def __init__(self, pos, size, kind, color, width, image=None):
        super().__init__(pos, size, pen=pg.mkPen(color, width=width),
                         hoverPen=pg.mkPen(color, width=width + 1.5), handlePen=pg.mkPen(color),
                         movable=True, rotatable=False, resizable=False, removable=False, invertible=True)
        self.kind = kind
        c = QtGui.QColor(color)
        c.setAlpha(45)
        self.fill = QtGui.QBrush(c)
        self.image = image
        for a, b in (([1, 1], [0, 0]), ([0, 0], [1, 1]), ([1, 0], [0, 1]), ([0, 1], [1, 0])):
            self.addScaleHandle(a, b)

    def paint(self, p, *args):
        w, h = self.state["size"]
        r = QtCore.QRectF(0, 0, w, h).normalized()
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        if self.kind == "image" and self.image is not None:
            p.drawImage(r, self.image)
            if self.mouseHovering:
                p.setPen(self.currentPen)
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRect(r)
            return
        p.setPen(self.currentPen)
        p.setBrush(self.fill)
        if self.kind == "ellipse":
            p.drawEllipse(r)
        else:
            p.drawRect(r)


class GeometryROI(pg.ROI):
    """Dibujo de varios puntos con asas libres. ``geometry(puntos)`` devuelve lo que hay que pintar, en
    coordenadas de la vista: (líneas [(a, b, pluma o None)], rellenos [(polígono, color)]).
    ``solid``: también se agarra por los rellenos, no solo por las líneas."""

    def __init__(self, pts, geometry, pen, hover, handle_pen, solid=False):
        super().__init__((0, 0), (1, 1), pen=pen, hoverPen=hover, handlePen=handle_pen,
                         movable=True, rotatable=False, resizable=False, removable=False)
        self.geometry = geometry
        self.solid = solid
        for pt in pts:
            self.addFreeHandle(pt)

    def _points(self):
        return [(q.x(), q.y()) for q in (self.mapToParent(h["item"].pos()) for h in self.handles)]

    def paint(self, p, *args):
        lines, fills = self.geometry(self._points())
        o = self.pos()
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.translate(-o.x(), -o.y())
        p.setPen(QtCore.Qt.NoPen)
        for poly, color in fills:
            p.setBrush(QtGui.QColor(color))
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(*q) for q in poly]))
        for a, b, pen in lines:
            p.setPen(pen if pen is not None else self.currentPen)
            p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))

    def shape(self):
        """Zona que se puede agarrar: una franja de pocos píxeles alrededor de cada línea."""
        path = QtGui.QPainterPath()
        path.setFillRule(QtCore.Qt.WindingFill)
        vb = self.getViewBox()
        size = vb.viewPixelSize() if vb is not None else None
        if not size or not size[0] or not size[1]:
            return path
        px, py = size
        lines, fills = self.geometry(self._points())
        for a, b, _pen in lines:
            dx, dy = (b[0] - a[0]) / px, (b[1] - a[1]) / py
            length = float(np.hypot(dx, dy))
            if not length or not np.isfinite(length):
                continue
            nx, ny = -dy / length * 5 * px, dx / length * 5 * py
            path.addPolygon(QtGui.QPolygonF([QtCore.QPointF(a[0] + nx, a[1] + ny), QtCore.QPointF(b[0] + nx, b[1] + ny),
                                             QtCore.QPointF(b[0] - nx, b[1] - ny), QtCore.QPointF(a[0] - nx, a[1] - ny)]))
        if self.solid:
            for poly, _color in fills:
                path.addPolygon(QtGui.QPolygonF([QtCore.QPointF(*q) for q in poly]))
        o = self.pos()
        return path.translated(-o.x(), -o.y())

    def boundingRect(self):
        return self.shape().boundingRect()

    def viewTransformChanged(self):  # la franja se mide en píxeles: cambia con el zoom
        self.prepareGeometryChange()
        super().viewTransformChanged()

    def stateChanged(self, finish=True):
        self.prepareGeometryChange()
        super().stateChanged(finish)


class DraggableText(pg.TextItem):
    sigMoved = QtCore.Signal(object)

    def mouseDragEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            ev.ignore()
            return
        ev.accept()
        vb = self.getViewBox()
        a = vb.mapSceneToView(ev.lastScenePos())
        b = vb.mapSceneToView(ev.scenePos())
        self.setPos(self.pos() + (b - a))
        if ev.isFinish():
            self.sigMoved.emit(self)


# --------------------------------------------------------------------------- #
# Capa de dibujo
# --------------------------------------------------------------------------- #
class DrawingLayer(QtCore.QObject):
    changed = QtCore.Signal()       # se modificó la lista de dibujos (para guardar)
    toolReset = QtCore.Signal()     # la herramienta volvió a "cursor"
    message = QtCore.Signal(str)
    alertRequested = QtCore.Signal(float)  # «Crear alerta en este precio» desde una línea horizontal

    def __init__(self, chart):
        super().__init__(chart)
        self.chart = chart
        self.tool = "cursor"
        self.color = "#2962ff"
        self.width = 2
        self.magnet = "off"  # "off" | "weak" | "strong"
        self.visible = True
        self.drawings: list[dict] = []
        self.main = None
        self.panes: list = []
        self._pane_plots: dict = {}  # clave del panel -> gráfica
        self.df = None
        self.log = False
        self._ts = np.array([])
        self._step = 1.0
        self._items: dict[str, list] = {}
        self._owner: dict = {}
        self._preview = None
        self._pen_pts: list = []
        self.symbol = ""
        self.history = DrawingHistory()
        self._pending = None  # dibujo de 3 clics en curso, en coordenadas de mercado
        self._measure = None  # medición: se ve mientras se usa y no se guarda
        self._measure_items: list = []
        self._shift = False
        chart.scene().sigMouseMoved.connect(self._on_move)

    # ------------------------------------------------------------ contexto
    def bind(self, symbol: str, drawings: list[dict]):
        """Dibujos del símbolo que va a mostrar la gráfica."""
        if symbol != self.symbol:
            self._pending = None
            self._measure = None
        self.symbol = symbol
        self.drawings = drawings
        self.history.baseline(symbol, drawings)

    def set_tool(self, tool):
        self._cancel_preview()
        self._pending = None
        if self._measure and not self._measure["fixed"]:
            self.clear_measure()
        self.tool = tool
        self._refresh_edit_state()

    def _refresh_edit_state(self):
        for items in self._items.values():
            for it, _plot in items:
                self._apply_edit_state(it)

    def _editable(self):
        # al medir, un dibujo que esté debajo del mouse no debe quedarse con el clic
        return self.tool in ("cursor", "eraser") and not self._measure and not self._shift

    def set_shift(self, down: bool):
        """Shift presionado: el próximo clic o arrastre es para medir, también encima de un dibujo."""
        if down != self._shift:
            self._shift = down
            self._refresh_edit_state()

    def _apply_edit_state(self, it):
        """Mientras se dibuja, los dibujos existentes no deben robarse el clic o el arrastre.

        pyqtgraph manda el hover incluso a elementos deshabilitados, así que
        también hay que quitarles la movilidad y ocultar las asas de los ROI.
        """
        d = self._find(self._owner.get(it))
        ed = self._editable() and not (d and d.get("locked"))
        it.setEnabled(ed)
        if getattr(it, "_cv_movable", False):
            it.setMovable(ed)
        if isinstance(it, pg.ROI):
            it.translatable = ed
            for h in it.handles:
                h["item"].setVisible(ed)

    def set_drawings(self, drawings: list[dict]):
        """Sustituye los dibujos (otra gráfica abierta en el mismo símbolo los cambió)."""
        self._cancel_preview()
        for did in list(self._items):
            self._unrender(did)
        self.drawings = drawings
        self.render_all()

    def set_context(self, main, panes, df, log, pct_base=None):
        """Se llama después de cada redibujado de la gráfica (los elementos anteriores ya no existen).

        ``pct_base``: en modo comparar, precio de la primera vela (la vista está en % de cambio).
        """
        self.main, self.panes, self.df, self.log = main, panes, df, log
        self.pct_base = pct_base
        self._pane_plots = {self.chart._pane_key[p]: p for p in panes}
        self._ts = df.index.values.astype("datetime64[s]").astype(np.int64).astype(float)
        gaps = np.diff(self._ts)
        gaps = gaps[gaps > 0]
        self._step = float(np.median(gaps)) if gaps.size else 86400.0
        self._items, self._owner, self._preview = {}, {}, None
        self._measure_items = []  # la gráfica se reconstruyó: hay que volver a ponerla
        self.render_all()
        self._draw_measure()

    # -------------------------------------------------------- coordenadas
    def t2x(self, t):
        ts, n = self._ts, len(self._ts)
        if n == 0:
            return 0.0
        if n == 1 or t <= ts[0]:
            return (t - ts[0]) / self._step
        if t >= ts[-1]:
            return n - 1 + (t - ts[-1]) / self._step
        i = int(np.searchsorted(ts, t, "right")) - 1
        return i + (t - ts[i]) / (ts[i + 1] - ts[i])

    def x2t(self, x):
        ts, n = self._ts, len(self._ts)
        if n == 0:
            return 0.0
        if x <= 0:
            return ts[0] + x * self._step
        if x >= n - 1:
            return ts[-1] + (x - (n - 1)) * self._step
        i = int(np.floor(x))
        return ts[i] + (x - i) * (ts[i + 1] - ts[i])

    def p2y(self, price):
        if getattr(self, "pct_base", None):
            return (price / self.pct_base - 1) * 100
        if not self.log:
            return price
        return float(np.log10(price)) if price > 0 else float("nan")

    def y2p(self, y):
        if getattr(self, "pct_base", None):
            return float(self.pct_base * (1 + y / 100))
        return float(10 ** y) if self.log else float(y)

    def to_view(self, pt, pane=None):
        return self.t2x(pt[0]), float(pt[1]) if pane else self.p2y(pt[1])

    def to_market(self, x, y, pane=None):
        return [float(self.x2t(x)), float(y) if pane else self.y2p(y)]

    # ------------------------------------------------------------ eventos
    def _target(self, vb):
        """(se puede dibujar aquí, clave del panel o None si es la gráfica de precio)."""
        if self.main is not None and vb is self.main.getViewBox():
            return True, None
        for key, plot in self._pane_plots.items():
            if plot.getViewBox() is vb:
                return True, key
        return False, None

    def _plot(self, pane):
        return self._pane_plots.get(pane) if pane else self.main

    def click(self, vb, ev) -> bool:
        ok, pane = self._target(vb)
        if not ok:
            return False
        if ev.button() == QtCore.Qt.RightButton:
            did = self._hit(ev.scenePos())
            if did:
                self._context_menu(did, ev.screenPos())
                return True
            if self.tool != "cursor":
                self._reset_tool()
                return True
            return False
        if ev.button() != QtCore.Qt.LeftButton:
            return False
        if self._measure or self.tool == "measure" or self._shift or ev.modifiers() & QtCore.Qt.ShiftModifier:
            self._measure_click(vb, ev, pane)
            return True
        if self.tool == "cursor":
            return False
        p = self._snap(vb, ev.scenePos(), pane)
        x, y = p.x(), p.y()
        t = self.tool
        if t == "eraser":
            did = self._hit(ev.scenePos())
            if did:
                d = self._find(did)
                if d and d.get("locked"):
                    self.message.emit("Ese dibujo está bloqueado. Desbloquéalo con clic derecho.")
                else:
                    self.remove(did)
            return True
        if t in THREE_CLICK_TOOLS:
            self._three_click(vb, x, y, pane)
            return True
        if t == "hline":
            self._add({"type": "hline", "points": [self.to_market(x, y, pane)]}, pane)
        elif t == "hray":
            self._add({"type": "hray", "points": [self.to_market(x, y, pane)]}, pane)
        elif t == "avwap":
            # el VWAP se calcula con el precio de las velas, así que vive en la gráfica de precio
            self._add({"type": "avwap", "points": [self.to_market(x, self.p2y(self._typical_price(x)))]})
        elif t in ("long", "short"):
            self._add_position(t, vb, x, y, pane)
        elif t == "vline":
            # una fecha cruza todos los paneles: se guarda en la gráfica de precio, a media altura
            self._add({"type": "vline", "points": [self.to_market(x, sum(self.main.getViewBox().viewRange()[1]) / 2)]})
        elif t == "text":
            text, ok = QtWidgets.QInputDialog.getMultiLineText(self.chart, "Texto", "Escribe el texto:")
            if ok and text.strip():
                self._add({"type": "text", "points": [self.to_market(x, y, pane)], "text": text}, pane)
        elif t == "image":
            self._place_image(vb, x, y, pane=pane)
        else:
            self.message.emit("Mantén presionado el botón y arrastra para dibujar.")
            return True
        if t not in STICKY_TOOLS:
            self._reset_tool()
        return True

    MAGNET_PX = 12  # imán débil: distancia máxima (en píxeles) para pegarse a la vela

    def _snap(self, vb, scene_pos, pane=None) -> QtCore.QPointF:
        """Punto en coordenadas de la vista, pegado a la apertura/máximo/mínimo/cierre más cercano
        de la vela bajo el cursor (en un panel, a las líneas del indicador) si el imán está activo
        (débil: solo si está cerca; fuerte: siempre)."""
        p = vb.mapSceneToView(scene_pos)
        if self.magnet == "off" or self.tool in ("pen", "eraser") or self.df is None or not len(self.df):
            return p
        i = int(round(p.x()))
        if not 0 <= i < len(self.df):  # zona sin velas (futuro): sin imán
            return p
        if pane:
            values = [float(ys[j]) for xs, ys in self.chart._series.get(self._plot(pane), [])
                      for j in np.flatnonzero(xs == i)]
        else:
            row = self.df.iloc[i]
            values = [self.p2y(float(row[k])) for k in ("Open", "High", "Low", "Close")]
        best = None
        for y in values:
            if not np.isfinite(y):
                continue
            sp = vb.mapViewToScene(QtCore.QPointF(i, y))
            dist = float(np.hypot(sp.x() - scene_pos.x(), sp.y() - scene_pos.y()))
            if best is None or dist < best[0]:
                best = (dist, y)
        if best and (self.magnet == "strong" or best[0] <= self.MAGNET_PX):
            return QtCore.QPointF(i, best[1])
        return p

    def drag(self, vb, ev) -> bool:
        ok, pane = self._target(vb)
        if ok and ev.button() == QtCore.Qt.LeftButton and (self.tool == "measure" or self._shift):
            self._measure_drag(vb, ev, pane)
            return True
        if not ok or self.tool not in DRAG_TOOLS or ev.button() != QtCore.Qt.LeftButton:
            return False
        a = self._snap(vb, ev.buttonDownScenePos(), pane)
        b = self._snap(vb, ev.scenePos(), pane)
        t = self.tool
        if ev.isStart():
            self._cancel_preview()
            pen = pg.mkPen(self.color, width=self.width, style=QtCore.Qt.DashLine if t != "pen" else QtCore.Qt.SolidLine)
            self._preview = pg.PlotCurveItem(pen=pen)
            self._preview.setZValue(60)
            self._preview_plot = self._plot(pane)
            self._preview_plot.addItem(self._preview, ignoreBounds=True)
            self._pen_pts = [(a.x(), a.y())]
        if self._preview is None:
            return True
        if t == "pen":
            self._pen_pts.append((b.x(), b.y()))
            xs, ys = zip(*self._pen_pts)
            self._preview.setData(np.array(xs), np.array(ys))
        elif t in ("rect", "ellipse", "image"):
            xs = [a.x(), b.x(), b.x(), a.x(), a.x()]
            ys = [a.y(), a.y(), b.y(), b.y(), a.y()]
            self._preview.setData(np.array(xs), np.array(ys))
        else:
            self._preview.setData(np.array([a.x(), b.x()]), np.array([a.y(), b.y()]))
        if ev.isFinish():
            self._cancel_preview()
            p1, p2 = self.to_market(a.x(), a.y(), pane), self.to_market(b.x(), b.y(), pane)
            if t == "pen":
                if len(self._pen_pts) > 1:
                    self._add({"type": "pen", "points": [self.to_market(x, y, pane) for x, y in self._pen_pts]}, pane)
            elif t == "image":
                self._place_image(vb, a.x(), a.y(), b.x(), b.y(), pane)
            elif t in ("long", "short"):
                self._add_position(t, vb, a.x(), a.y(), pane, b.x() - a.x(), b.y() - a.y())
            else:
                self._add({"type": t, "points": [p1, p2]}, pane)
            if t not in STICKY_TOOLS:
                self._reset_tool()
        return True

    # ---------------------------------------------- herramientas de 3 clics
    def _three_click(self, vb, x, y, pane):
        if self._pending is None:
            self._pending = {"tool": self.tool, "pane": pane, "points": []}
            self.message.emit("Faltan 2 clics. Esc o clic derecho para cancelar.")
        elif self._pending["pane"] != pane:
            self.message.emit("Termina el dibujo en el mismo panel donde lo empezaste.")
            return
        self._pending["points"].append(self.to_market(x, y, pane))
        if len(self._pending["points"]) == 3:
            pending, self._pending = self._pending, None
            self._cancel_preview()
            self._add({"type": pending["tool"], "points": pending["points"]}, pane)
            self._reset_tool()

    def _on_move(self, pos):
        if self._pending is not None:
            self._move_pending(pos)
        if self._measure and not self._measure["fixed"]:
            plot = self._plot(self._measure["pane"])
            if plot is not None:
                p = self._snap(plot.getViewBox(), pos, self._measure["pane"])
                self._measure["b"] = self.to_market(p.x(), p.y(), self._measure["pane"])
                self._draw_measure()

    def _move_pending(self, pos):
        pane = self._pending["pane"]
        plot = self._plot(pane)
        if plot is None:
            return
        if self._preview is None:  # también si una actualización reconstruyó la gráfica
            self._preview = pg.PlotCurveItem(pen=pg.mkPen(self.color, width=self.width, style=QtCore.Qt.DashLine))
            self._preview.setZValue(60)
            self._preview_plot = plot
            plot.addItem(self._preview, ignoreBounds=True)
        p = self._snap(plot.getViewBox(), pos, pane)
        pts = [self.to_view(q, pane) for q in self._pending["points"]] + [(p.x(), p.y())]
        self._preview.setData(np.array([q[0] for q in pts]), np.array([q[1] for q in pts]))

    # ------------------------------------------------------------- medición
    def _measure_click(self, vb, ev, pane):
        m = self._measure
        if m and m["fixed"]:  # el clic siguiente quita la medición
            self.clear_measure()
            return
        if m:
            m["fixed"] = True
            if self.tool == "measure":
                self._reset_tool()
            return
        p = self._snap(vb, ev.scenePos(), pane)
        pt = self.to_market(p.x(), p.y(), pane)
        self._measure = {"pane": pane, "a": pt, "b": list(pt), "fixed": False}
        self._refresh_edit_state()
        self.message.emit("Mueve el mouse y haz clic para fijar la medición. Otro clic la quita.")

    def _measure_drag(self, vb, ev, pane):
        if ev.isStart():
            self.clear_measure()
            a = self._snap(vb, ev.buttonDownScenePos(), pane)
            self._measure = {"pane": pane, "a": self.to_market(a.x(), a.y(), pane), "b": None, "fixed": False}
            self._refresh_edit_state()
        if self._measure is None:
            return
        b = self._snap(vb, ev.scenePos(), pane)
        self._measure["b"] = self.to_market(b.x(), b.y(), pane)
        self._draw_measure()
        if ev.isFinish():
            self._measure["fixed"] = True
            if self.tool == "measure":
                self._reset_tool()

    def clear_measure(self):
        had = self._measure is not None
        self._measure = None
        self._remove_measure_items()
        if had:
            self._refresh_edit_state()

    def _remove_measure_items(self):
        for it, plot in self._measure_items:
            try:
                plot.removeItem(it)
            except (RuntimeError, ValueError):
                pass
        self._measure_items = []

    def _draw_measure(self):
        self._remove_measure_items()
        m = self._measure
        plot = self._plot(m["pane"]) if m and m.get("b") else None
        if plot is None or self.main is None:
            return
        (x1, y1), (x2, y2) = self.to_view(m["a"], m["pane"]), self.to_view(m["b"], m["pane"])
        if not all(np.isfinite(v) for v in (x1, y1, x2, y2)):
            return
        p1, p2 = m["a"][1], m["b"][1]
        color = GAIN if p2 >= p1 else LOSS
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setCosmetic(True)
        fill = QtGui.QColor(color)
        fill.setAlpha(50)
        rect = QtWidgets.QGraphicsRectItem(QtCore.QRectF(x1, y1, x2 - x1, y2 - y1).normalized())
        rect.setPen(pen)
        rect.setBrush(fill)
        diag = pg.PlotCurveItem(np.array([x1, x2]), np.array([y1, y2]), pen=pg.mkPen(color, width=1))
        bars = int(round(abs(x2 - x1)))
        pct = f" ({(p2 / p1 - 1) * 100:+.2f}%)" if p1 else ""
        text = (f"{p2 - p1:+,.{2 if abs(p2 - p1) >= 1 else 4}f}{pct}\n"
                f"{bars} {'vela' if bars == 1 else 'velas'} · {self._duration(abs(m['b'][0] - m['a'][0]))}")
        label = pg.TextItem(text, color="#ffffff", fill=QtGui.QColor(color), anchor=(0.5, 1 if y2 >= y1 else 0))
        label.setPos((x1 + x2) / 2, max(y1, y2) if y2 >= y1 else min(y1, y2))
        for it in (rect, diag, label):
            it.setZValue(70)
            plot.addItem(it, ignoreBounds=True)
            self._measure_items.append((it, plot))

    @staticmethod
    def _duration(secs: float) -> str:
        days = secs / 86400
        if days >= 1:
            return f"{days:,.0f} {'día' if round(days) == 1 else 'días'}"
        hours = secs / 3600
        return f"{hours:.0f} h" if hours >= 1 else f"{secs / 60:.0f} min"

    # --------------------------------------------- posición y VWAP anclado
    def _add_position(self, tool, vb, x, y, pane, dx=0.0, dy=0.0):
        (vx0, vx1), (vy0, vy1) = vb.viewRange()
        reach = abs(dy) or (vy1 - vy0) * 0.06
        width = abs(dx) if abs(dx) >= 3 else max((vx1 - vx0) * 0.2, 5)
        sign = 1 if tool == "long" else -1
        view = [(x, y), (x, y + sign * reach), (x, y - sign * reach), (x + width, y)]
        self._add({"type": "position", "points": [self.to_market(a, b, pane) for a, b in view]}, pane)

    def _typical_price(self, x) -> float:
        row = self.df.iloc[min(max(int(round(x)), 0), len(self.df) - 1)]
        return float(row["High"] + row["Low"] + row["Close"]) / 3

    def _anchored_vwap(self, x):
        """(xs, valores en la vista, último precio) del VWAP desde la vela ``x``, o None si no aplica."""
        n = len(self.df)
        i = max(int(round(x)), 0)
        if i >= n:
            return None
        part = self.df.iloc[i:]
        vol = part["Volume"].to_numpy(float)
        vol = np.where(np.isfinite(vol), vol, 0.0)
        if vol.sum() <= 0:
            return None
        typical = ((part["High"] + part["Low"] + part["Close"]) / 3).to_numpy(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            prices = np.cumsum(typical * vol) / np.cumsum(vol)
        return np.arange(i, n, dtype=float), np.array([self.p2y(v) for v in prices]), float(prices[-1])

    def _reset_tool(self):
        self.set_tool("cursor")
        self.toolReset.emit()

    def _cancel_preview(self):
        if self._preview is not None:
            try:
                self._preview_plot.removeItem(self._preview)
            except (RuntimeError, AttributeError):
                pass
            self._preview = None

    # ------------------------------------------------------------- edición
    def _add(self, d, pane=None):
        if pane:
            d["pane"] = pane
        d.setdefault("color", self.color)
        d.setdefault("width", self.width)
        d["id"] = uuid.uuid4().hex
        self.drawings.append(d)
        self._render(d)
        self._changed()

    def _changed(self):
        self.history.record(self.symbol, self.drawings)
        self.changed.emit()

    def _unrender(self, did):
        for it, plot in self._items.pop(did, []):
            self._owner.pop(it, None)
            try:
                plot.removeItem(it)
            except (RuntimeError, ValueError):
                pass

    def remove(self, did):
        self._unrender(did)
        self.drawings = [d for d in self.drawings if d["id"] != did]
        self._changed()

    def undo(self):
        self._step_history(True)

    def redo(self):
        self._step_history(False)

    def _step_history(self, back: bool):
        state = self.history.step(self.symbol, back)
        if state is None:
            self.message.emit("Nada que deshacer." if back else "Nada que rehacer.")
            return
        self.set_drawings(state)
        self.changed.emit()  # se guarda y se avisa a las demás gráficas, sin volverlo un paso del historial

    def clear(self):
        for did in list(self._items):
            self._unrender(did)
        self.drawings = []
        self._changed()

    def duplicate(self, d):
        """Copia un poco a la derecha y abajo del original, para que se distinga."""
        pane = d.get("pane")
        plot = self._plot(pane)
        if plot is None:
            return
        (vy0, vy1) = plot.getViewBox().viewRange()[1]
        copy = deepcopy(d)
        copy.pop("locked", None)
        dy = 0.0 if d["type"] == "vline" else (vy1 - vy0) * 0.03
        moved = []
        for pt in d["points"]:
            x, y = self.to_view(pt, pane)
            moved.append(self.to_market(x + 5, y - dy, pane))
        copy["points"] = moved
        self._add(copy, pane)

    def set_visible(self, on: bool):
        self.visible = on
        for items in self._items.values():
            for it, _plot in items:
                it.setVisible(on)

    def _hit(self, scene_pos):
        for it in self.chart.scene().items(scene_pos):
            cur = it
            while cur is not None:
                if cur in self._owner:
                    return self._owner[cur]
                cur = cur.parentItem()
        return None

    def _find(self, did):
        return next((d for d in self.drawings if d["id"] == did), None)

    def _context_menu(self, did, screen_pos):
        d = self._find(did)
        if d is None:
            return
        m = QtWidgets.QMenu(self.chart)
        if d["type"] in ("hline", "hray") and not d.get("pane"):
            m.addAction("🔔 Crear alerta en este precio", lambda: self.alertRequested.emit(float(d["points"][0][1])))
            m.addSeparator()
        m.addAction("Cambiar color…", lambda: self._recolor(d))
        wm = m.addMenu("Grosor")
        for w in (1, 2, 3, 4, 6):
            wm.addAction(str(w), lambda w=w: self._restyle(d, width=w))
        if d["type"] == "text":
            m.addAction("Editar texto…", lambda: self._edit_text(d))
        m.addSeparator()
        m.addAction("Duplicar", lambda: self.duplicate(d))
        m.addAction("Desbloquear" if d.get("locked") else "Bloquear", lambda: self._restyle(d, locked=not d.get("locked")))
        m.addSeparator()
        m.addAction("Eliminar", lambda: self.remove(did))
        m.exec(QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y())))

    def _recolor(self, d):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(d.get("color", self.color)), self.chart, "Color")
        if c.isValid():
            self._restyle(d, color=c.name())

    def _edit_text(self, d):
        text, ok = QtWidgets.QInputDialog.getMultiLineText(self.chart, "Texto", "Texto:", d.get("text", ""))
        if ok and text.strip():
            self._restyle(d, text=text)

    def _restyle(self, d, **changes):
        d.update(changes)
        self._unrender(d["id"])
        self._render(d)
        self._changed()

    def _place_image(self, vb, x1, y1, x2=None, y2=None, pane=None):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.chart, "Elegir imagen", "", "Imágenes (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg)")
        if not path:
            return
        img = QtGui.QImage(path)
        if img.isNull():
            self.message.emit("No se pudo abrir la imagen.")
            return
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        dest = IMG_DIR / f"{uuid.uuid4().hex}{Path(path).suffix.lower()}"
        shutil.copy2(path, dest)
        if x2 is None or abs(x2 - x1) < 1e-9:
            # tamaño por defecto: 1/5 del ancho visible, conservando la proporción en pantalla
            (vx0, vx1), _ = vb.viewRange()
            px_w, px_h = vb.viewPixelSize()
            w = (vx1 - vx0) / 5
            h = w / px_w * (img.height() / img.width()) * px_h
            x2, y2 = x1 + w, y1 - h
        self._add({"type": "image", "points": [self.to_market(x1, y1, pane), self.to_market(x2, y2, pane)],
                   "image": str(dest)}, pane)

    # ----------------------------------------------------------- render
    def render_all(self):
        if self.main is None:
            return
        for d in self.drawings:
            try:
                self._render(d)
            except Exception:  # noqa: BLE001 - un dibujo dañado no debe tumbar la gráfica
                pass

    def _own(self, did, item, plot=None):
        plot = plot or self.main
        item.setZValue(max(item.zValue(), 50))
        plot.addItem(item, ignoreBounds=True)
        item.setVisible(self.visible)
        self._items.setdefault(did, []).append((item, plot))
        self._owner[item] = did
        if isinstance(item, pg.InfiniteLine) and item.movable:
            item._cv_movable = True
        self._apply_edit_state(item)
        return item

    def _commit(self, d, points):
        d["points"] = points
        self._changed()

    def _render(self, d):
        if self.main is None:
            return
        did, t = d["id"], d["type"]
        pane = d.get("pane")
        plot = self._plot(pane)
        if plot is None:
            return  # el indicador no está en la gráfica: el dibujo se conserva para cuando vuelva
        y2p, p2y = (float, float) if pane else (self.y2p, self.p2y)
        color, width = d.get("color", self.color), d.get("width", self.width)
        pen = pg.mkPen(color, width=width)
        hover = pg.mkPen(color, width=width + 2)
        pts = [self.to_view(p, pane) for p in d["points"]]
        if any(not np.isfinite(v) for p in pts for v in p):
            return

        if t == "hline":
            price = d["points"][0][1]
            line = pg.InfiniteLine(pos=pts[0][1], angle=0, movable=True, pen=pen, hoverPen=hover,
                                   label=_fmt(price),
                                   labelOpts={"position": 0.04, "color": color, "fill": "#131722cc",
                                              "anchors": [(0, 1), (0, 1)]})

            def moved(ln=line):
                ln.label.setFormat(_fmt(y2p(ln.value())))
                ln.label.valueChanged()

            line.sigPositionChanged.connect(lambda *_: moved())
            line.sigPositionChangeFinished.connect(
                lambda ln: self._commit(d, [[d["points"][0][0], y2p(ln.value())]]))
            self._own(did, line, plot)

        elif t == "vline":
            line = pg.InfiniteLine(pos=pts[0][0], angle=90, movable=True, pen=pen, hoverPen=hover)
            copies = [self._own(did, pg.InfiniteLine(pos=pts[0][0], angle=90, movable=False, pen=pen), plot=p)
                      for p in self.panes]
            line.sigPositionChanged.connect(lambda ln: [c.setPos(ln.value()) for c in copies])
            line.sigPositionChangeFinished.connect(
                lambda ln: self._commit(d, [[float(self.x2t(ln.value())), d["points"][0][1]]]))
            self._own(did, line)

        elif t in ("trend", "ray"):
            roi = pg.LineSegmentROI(pts, pen=pen, hoverPen=hover, handlePen=pg.mkPen(color))
            self._own(did, roi, plot)
            if t == "ray":
                ext = self._own(did, pg.PlotCurveItem(pen=pg.mkPen(color, width=width)), plot)

                def update_ray(r=roi, e=ext):
                    (x1, y1), (x2, y2) = self._roi_points(r)
                    if abs(x2 - x1) < 1e-12:
                        e.setData([], [])
                        return
                    far = len(self._ts) + 1000 if x2 > x1 else -1000
                    e.setData(np.array([x2, far]), np.array([y2, y1 + (y2 - y1) / (x2 - x1) * (far - x1)]))

                roi.sigRegionChanged.connect(lambda *_: update_ray())
                update_ray()
            roi.sigRegionChangeFinished.connect(
                lambda r: self._commit(d, [self.to_market(x, y, pane) for x, y in self._roi_points(r)]))

        elif t in ("rect", "ellipse", "image"):
            (x1, y1), (x2, y2) = pts
            img = None
            if t == "image":
                img = _load_image(d.get("image", ""))
                if img is None:
                    return
            roi = ShapeROI((min(x1, x2), min(y1, y2)), (abs(x2 - x1), abs(y2 - y1)), t, color, width, img)
            self._own(did, roi, plot)

            def fin(r):
                pos, size = r.pos(), r.size()
                a = (pos.x(), pos.y())
                b = (pos.x() + size.x(), pos.y() + size.y())
                self._commit(d, [self.to_market(*a, pane), self.to_market(*b, pane)])

            roi.sigRegionChangeFinished.connect(fin)

        elif t == "pen":
            xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
            item = pg.PlotCurveItem(xs, ys, pen=pen)
            self._own(did, item, plot)

        elif t == "text":
            size = 9 + 2 * width
            item = DraggableText(d.get("text", ""), color=color, anchor=(0, 0))
            f = QtGui.QFont()
            f.setPointSize(size)
            f.setBold(width >= 3)
            item.setFont(f)
            item.setPos(*pts[0])
            item.sigMoved.connect(lambda it: self._commit(d, [self.to_market(it.pos().x(), it.pos().y(), pane)]))
            self._own(did, item, plot)

        elif t == "fib":
            roi = pg.LineSegmentROI(pts, pen=pg.mkPen(color, width=1, style=QtCore.Qt.DashLine),
                                    hoverPen=hover, handlePen=pg.mkPen(color))
            self._own(did, roi, plot)
            levels = []
            for r, c in FIB_LEVELS:
                ln = self._own(did, pg.PlotCurveItem(pen=pg.mkPen(c, width=1)), plot)
                tx = self._own(did, pg.TextItem("", color=c, anchor=(1, 1)), plot)
                levels.append((r, ln, tx))

            def update_fib(rr=roi):
                (x1, y1), (x2, y2) = self._roi_points(rr)
                p1, p2 = y2p(y1), y2p(y2)
                xa, xb = min(x1, x2), max(x1, x2)
                for r, ln, tx in levels:
                    price = p2 - (p2 - p1) * r
                    yv = p2y(price)
                    ln.setData(np.array([xa, xb]), np.array([yv, yv]))
                    tx.setText(f"{r * 100:g}%  ({_fmt(price)})")
                    tx.setPos(xb, yv)

            roi.sigRegionChanged.connect(lambda *_: update_fib())
            roi.sigRegionChangeFinished.connect(
                lambda r: self._commit(d, [self.to_market(x, y, pane) for x, y in self._roi_points(r)]))
            update_fib()

        elif t in ("hray", "arrow", "channel", "pitchfork", "fibext", "position", "avwap"):
            self._render_geometry(d, t, pts, plot, pane, color, width, pen, hover, y2p, p2y)

    # ------------------------------------------- dibujos de varios puntos
    def _far_x(self, going_right=True):
        return len(self._ts) + 1000 if going_right else -1000

    def _render_geometry(self, d, t, pts, plot, pane, color, width, pen, hover, y2p, p2y):
        did = d["id"]
        vb = plot.getViewBox()
        thin = pg.mkPen(color, width=1)
        dashed = pg.mkPen(color, width=1, style=QtCore.Qt.DashLine)
        tint = QtGui.QColor(color)
        tint.setAlpha(28)
        solid = False
        follow = None  # se llama cada vez que cambian los puntos

        if t == "hray":
            def geometry(p):
                return [(p[0], (self._far_x(), p[0][1]), None)], []

            label = self._own(did, pg.TextItem("", color=color, anchor=(0, 1)), plot)

            def follow(p):
                label.setText(_fmt(y2p(p[0][1])))
                label.setPos(*p[0])

        elif t == "arrow":
            def geometry(p):
                (x1, y1), (x2, y2) = p
                lines = [(p[0], p[1], None)]
                px, py = vb.viewPixelSize()
                dx, dy = (x2 - x1) / px, (y2 - y1) / py
                length = float(np.hypot(dx, dy))
                if length and np.isfinite(length):
                    for ang in (0.45, -0.45):  # punta de tamaño fijo en pantalla
                        ux, uy = dx / length, dy / length
                        hx = (ux * np.cos(ang) - uy * np.sin(ang)) * 12
                        hy = (ux * np.sin(ang) + uy * np.cos(ang)) * 12
                        lines.append(((x2 - hx * px, y2 - hy * py), p[1], None))
                return lines, []

        elif t == "channel":
            def geometry(p):
                (x1, y1), (x2, y2), (x3, y3) = p
                slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
                off = y3 - (y1 + slope * (x3 - x1))
                a2, b2 = (x1, y1 + off), (x2, y2 + off)
                mid = ((x1, y1 + off / 2), (x2, y2 + off / 2), dashed)
                return [(p[0], p[1], None), (a2, b2, None), mid], [([p[0], p[1], b2, a2], tint)]

        elif t == "pitchfork":
            def geometry(p):
                (ax, ay), (bx, by), (cx, cy) = p
                mx, my = (bx + cx) / 2, (by + cy) / 2
                dx, dy = mx - ax, my - ay
                if abs(dx) > 1e-9:
                    k = max((self._far_x(dx > 0) - mx) / dx, 1.0)
                else:
                    k = 3.0
                far = lambda x, y: (x + dx * k, y + dy * k)  # noqa: E731
                far_b, far_c = far(bx, by), far(cx, cy)
                lines = [(p[0], far(mx, my), None), (p[1], far_b, None), (p[2], far_c, None), (p[1], p[2], dashed)]
                return lines, [([p[1], far_b, far_c, p[2]], tint)]

        elif t == "fibext":
            texts = [(r, c, self._own(did, pg.TextItem("", color=c, anchor=(1, 1)), plot)) for r, c in FIB_EXTENSIONS]

            def levels(p):
                (x1, y1), (x2, y2), (x3, y3) = p
                move = y2p(y2) - y2p(y1)
                end = x3 + max(abs(x2 - x1), 10)
                for r, c, tx in texts:
                    yield r, c, tx, y2p(y3) + move * r, x3, end

            def geometry(p):
                lines = [(p[0], p[1], dashed), (p[1], p[2], dashed)]
                for _r, c, _tx, price, x3, end in levels(p):
                    y = p2y(price)
                    if np.isfinite(y):
                        lines.append(((x3, y), (end, y), pg.mkPen(c, width=1)))
                return lines, []

            def follow(p):
                for r, _c, tx, price, _x3, end in levels(p):
                    y = p2y(price)
                    tx.setVisible(bool(np.isfinite(y)) and self.visible)
                    if np.isfinite(y):
                        tx.setText(f"{r:g}  ({_fmt(price)})")
                        tx.setPos(end, y)

        elif t == "position":
            solid = True
            t_gain, t_loss = QtGui.QColor(GAIN), QtGui.QColor(LOSS)
            t_gain.setAlpha(55)
            t_loss.setAlpha(55)
            tx_target = self._own(did, pg.TextItem("", color=GAIN, anchor=(0, 1)), plot)
            tx_stop = self._own(did, pg.TextItem("", color=LOSS, anchor=(0, 0)), plot)
            tx_ratio = self._own(did, pg.TextItem("", color="#ffffff", anchor=(0, 0.5)), plot)

            def geometry(p):
                (x0, ye), (_a, yt), (_b, ys), (x1, _c) = p
                return ([((x0, ye), (x1, ye), thin)],
                        [([(x0, ye), (x1, ye), (x1, yt), (x0, yt)], t_gain),
                         ([(x0, ye), (x1, ye), (x1, ys), (x0, ys)], t_loss)])

            def follow(p):
                (x0, ye), (_a, yt), (_b, ys), (x1, _c) = p
                # las asas siguen a la caja: objetivo y stop a la izquierda, ancho sobre la entrada
                if not guard[0]:
                    guard[0] = True
                    for i, want in ((1, (x0, yt)), (2, (x0, ys)), (3, (x1, ye))):
                        if abs(p[i][0] - want[0]) > 1e-9 or abs(p[i][1] - want[1]) > 1e-12:
                            roi.movePoint(roi.handles[i]["item"], QtCore.QPointF(*want), finish=False, coords="parent")
                    guard[0] = False
                entry, target, stop = y2p(ye), y2p(yt), y2p(ys)
                pct = lambda v: f"{(v / entry - 1) * 100:+.2f}%" if entry else ""  # noqa: E731
                tx_target.setText(f"Objetivo {_fmt(target)}  {pct(target)}")
                tx_target.setPos(x0, yt)
                tx_target.setAnchor((0, 1) if yt >= ye else (0, 0))
                tx_stop.setText(f"Stop {_fmt(stop)}  {pct(stop)}")
                tx_stop.setPos(x0, ys)
                tx_stop.setAnchor((0, 0) if ys <= ye else (0, 1))
                risk = abs(entry - stop)
                ratio = f"{abs(target - entry) / risk:.2f}" if risk else "—"
                tx_ratio.setText(f"{'Larga' if target >= entry else 'Corta'}  R/B {ratio}")
                tx_ratio.setPos(x1, ye)

        else:  # avwap
            curve = self._own(did, pg.PlotCurveItem(pen=pg.mkPen(color, width=max(width, 1.5))), plot)
            label = self._own(did, pg.TextItem("", color=color, anchor=(0, 0.5)), plot)

            def geometry(p):
                return [], []

            def follow(p):
                got = self._anchored_vwap(p[0][0])
                if got is None:
                    curve.setData([], [])
                    label.setText("VWAP: sin volumen" if p[0][0] < len(self._ts) else "")
                    label.setPos(*p[0])
                    return
                xs, ys, last = got
                curve.setData(xs, ys, connect="finite")
                label.setText(f"VWAP {_fmt(last)}")
                label.setPos(xs[-1] + 1, ys[-1])
                if not guard[0] and np.isfinite(ys[0]):  # el asa queda sobre el inicio de la línea
                    guard[0] = True
                    roi.movePoint(roi.handles[0]["item"], QtCore.QPointF(xs[0], ys[0]), finish=False, coords="parent")
                    guard[0] = False

        guard = [False]
        roi = GeometryROI(pts, geometry, pen, hover, pg.mkPen(color), solid=solid)
        self._own(did, roi, plot)
        if follow is not None:
            roi.sigRegionChanged.connect(lambda *_: follow(self._roi_points(roi)))
            follow(pts)
        roi.sigRegionChangeFinished.connect(
            lambda r: self._commit(d, [self.to_market(x, y, pane) for x, y in self._roi_points(r)]))

    @staticmethod
    def _roi_points(roi):
        out = []
        for h in roi.handles:
            p = roi.mapToParent(h["item"].pos())
            out.append((p.x(), p.y()))
        return out
