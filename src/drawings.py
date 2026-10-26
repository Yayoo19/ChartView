"""Herramientas de dibujo sobre la gráfica de precio.

Los dibujos se guardan en coordenadas de mercado (fecha en segundos epoch y
precio real), así sobreviven a cambios de intervalo, de periodo o de escala
logarítmica. La capa los traduce a coordenadas de la vista al dibujarlos.
"""
from __future__ import annotations

import shutil
import uuid
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
    ("vline", "│", "Línea vertical (clic)"),
    ("trend", "╱", "Línea de tendencia (arrastrar)"),
    ("ray", "⟶", "Rayo: línea que se extiende a la derecha (arrastrar)"),
    ("rect", "▭", "Rectángulo (arrastrar)"),
    ("ellipse", "◯", "Círculo / elipse (arrastrar)"),
    ("pen", "✎", "Dibujo libre (arrastrar)"),
    ("text", "T", "Texto (clic)"),
    ("image", "🖼", "Imagen (clic o arrastrar para el tamaño)"),
    ("fib", "Fib", "Retroceso de Fibonacci (arrastrar de inicio a fin)"),
    ("eraser", "⌫", "Borrador: clic sobre un dibujo"),
]
# grupos de la barra de dibujo: un botón por grupo; la flechita abre las demás herramientas del grupo
TOOL_GROUPS = [
    ("Cursor", ["cursor"]),
    ("Líneas", ["hline", "vline", "trend", "ray"]),
    ("Formas", ["rect", "ellipse", "pen"]),
    ("Texto e imagen", ["text", "image"]),
    ("Fibonacci", ["fib"]),
    ("Borrador", ["eraser"]),
]
DRAG_TOOLS = {"trend", "ray", "rect", "ellipse", "pen", "fib", "image"}
STICKY_TOOLS = {"pen", "eraser"}  # siguen activas después de usarlas


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
        self.visible = True
        self.drawings: list[dict] = []
        self.main = None
        self.panes: list = []
        self.df = None
        self.log = False
        self._ts = np.array([])
        self._step = 1.0
        self._items: dict[str, list] = {}
        self._owner: dict = {}
        self._preview = None
        self._pen_pts: list = []

    # ------------------------------------------------------------ contexto
    def set_tool(self, tool):
        self._cancel_preview()
        self.tool = tool
        for items in self._items.values():
            for it, _plot in items:
                self._apply_edit_state(it)

    def _editable(self):
        return self.tool in ("cursor", "eraser")

    def _apply_edit_state(self, it):
        """Mientras se dibuja, los dibujos existentes no deben robarse el clic o el arrastre.

        pyqtgraph manda el hover incluso a elementos deshabilitados, así que
        también hay que quitarles la movilidad y ocultar las asas de los ROI.
        """
        ed = self._editable()
        it.setEnabled(ed)
        if getattr(it, "_cv_movable", False):
            it.setMovable(ed)
        if isinstance(it, pg.ROI):
            it.translatable = ed
            for h in it.handles:
                h["item"].setVisible(ed)

    def set_drawings(self, drawings: list[dict]):
        self.drawings = drawings
        self.render_all()

    def set_context(self, main, panes, df, log):
        """Se llama después de cada redibujado de la gráfica (los elementos anteriores ya no existen)."""
        self.main, self.panes, self.df, self.log = main, panes, df, log
        self._ts = df.index.values.astype("datetime64[s]").astype(np.int64).astype(float)
        self._step = float(np.median(np.diff(self._ts))) if len(self._ts) > 1 else 86400.0
        self._items, self._owner, self._preview = {}, {}, None
        self.render_all()

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
        if not self.log:
            return price
        return float(np.log10(price)) if price > 0 else float("nan")

    def y2p(self, y):
        return float(10 ** y) if self.log else float(y)

    def to_view(self, pt):
        return self.t2x(pt[0]), self.p2y(pt[1])

    def to_market(self, x, y):
        return [float(self.x2t(x)), self.y2p(y)]

    # ------------------------------------------------------------ eventos
    def _is_main(self, vb):
        return self.main is not None and vb is self.main.getViewBox()

    def click(self, vb, ev) -> bool:
        if not self._is_main(vb):
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
        if ev.button() != QtCore.Qt.LeftButton or self.tool == "cursor":
            return False
        p = vb.mapSceneToView(ev.scenePos())
        x, y = p.x(), p.y()
        t = self.tool
        if t == "eraser":
            did = self._hit(ev.scenePos())
            if did:
                self.remove(did)
            return True
        if t == "hline":
            self._add({"type": "hline", "points": [self.to_market(x, y)]})
        elif t == "vline":
            self._add({"type": "vline", "points": [self.to_market(x, y)]})
        elif t == "text":
            text, ok = QtWidgets.QInputDialog.getMultiLineText(self.chart, "Texto", "Escribe el texto:")
            if ok and text.strip():
                self._add({"type": "text", "points": [self.to_market(x, y)], "text": text})
        elif t == "image":
            self._place_image(vb, x, y)
        else:
            self.message.emit("Mantén presionado el botón y arrastra para dibujar.")
            return True
        if t not in STICKY_TOOLS:
            self._reset_tool()
        return True

    def drag(self, vb, ev) -> bool:
        if not self._is_main(vb) or self.tool not in DRAG_TOOLS or ev.button() != QtCore.Qt.LeftButton:
            return False
        a = vb.mapSceneToView(ev.buttonDownScenePos())
        b = vb.mapSceneToView(ev.scenePos())
        t = self.tool
        if ev.isStart():
            self._cancel_preview()
            pen = pg.mkPen(self.color, width=self.width, style=QtCore.Qt.DashLine if t != "pen" else QtCore.Qt.SolidLine)
            self._preview = pg.PlotCurveItem(pen=pen)
            self._preview.setZValue(60)
            self.main.addItem(self._preview, ignoreBounds=True)
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
            p1, p2 = self.to_market(a.x(), a.y()), self.to_market(b.x(), b.y())
            if t == "pen":
                if len(self._pen_pts) > 1:
                    self._add({"type": "pen", "points": [self.to_market(x, y) for x, y in self._pen_pts]})
            elif t == "image":
                self._place_image(vb, a.x(), a.y(), b.x(), b.y())
            else:
                self._add({"type": t, "points": [p1, p2]})
            if t not in STICKY_TOOLS:
                self._reset_tool()
        return True

    def _reset_tool(self):
        self.set_tool("cursor")
        self.toolReset.emit()

    def _cancel_preview(self):
        if self._preview is not None:
            try:
                self.main.removeItem(self._preview)
            except (RuntimeError, AttributeError):
                pass
            self._preview = None

    # ------------------------------------------------------------- edición
    def _add(self, d):
        d.setdefault("color", self.color)
        d.setdefault("width", self.width)
        d["id"] = uuid.uuid4().hex
        self.drawings.append(d)
        self._render(d)
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
        self.changed.emit()

    def undo(self):
        if self.drawings:
            self.remove(self.drawings[-1]["id"])

    def clear(self):
        for did in list(self._items):
            self._unrender(did)
        self.drawings = []
        self.changed.emit()

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
        if d["type"] == "hline":
            m.addAction("🔔 Crear alerta en este precio", lambda: self.alertRequested.emit(float(d["points"][0][1])))
            m.addSeparator()
        m.addAction("Cambiar color…", lambda: self._recolor(d))
        wm = m.addMenu("Grosor")
        for w in (1, 2, 3, 4, 6):
            wm.addAction(str(w), lambda w=w: self._restyle(d, width=w))
        if d["type"] == "text":
            m.addAction("Editar texto…", lambda: self._edit_text(d))
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
        self.changed.emit()

    def _place_image(self, vb, x1, y1, x2=None, y2=None):
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
        self._add({"type": "image", "points": [self.to_market(x1, y1), self.to_market(x2, y2)], "image": str(dest)})

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
        if isinstance(item, pg.InfiniteLine) and item.movable:
            item._cv_movable = True
        self._apply_edit_state(item)
        self._items.setdefault(did, []).append((item, plot))
        self._owner[item] = did
        return item

    def _commit(self, d, points):
        d["points"] = points
        self.changed.emit()

    def _render(self, d):
        if self.main is None:
            return
        did, t = d["id"], d["type"]
        color, width = d.get("color", self.color), d.get("width", self.width)
        pen = pg.mkPen(color, width=width)
        hover = pg.mkPen(color, width=width + 2)
        pts = [self.to_view(p) for p in d["points"]]
        if any(not np.isfinite(v) for p in pts for v in p):
            return

        if t == "hline":
            price = d["points"][0][1]
            line = pg.InfiniteLine(pos=pts[0][1], angle=0, movable=True, pen=pen, hoverPen=hover,
                                   label=_fmt(price),
                                   labelOpts={"position": 0.04, "color": color, "fill": "#131722cc",
                                              "anchors": [(0, 1), (0, 1)]})

            def moved(ln=line):
                ln.label.setFormat(_fmt(self.y2p(ln.value())))
                ln.label.valueChanged()

            line.sigPositionChanged.connect(lambda *_: moved())
            line.sigPositionChangeFinished.connect(
                lambda ln: self._commit(d, [[d["points"][0][0], self.y2p(ln.value())]]))
            self._own(did, line)

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
            self._own(did, roi)
            if t == "ray":
                ext = self._own(did, pg.PlotCurveItem(pen=pg.mkPen(color, width=width)))

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
                lambda r: self._commit(d, [self.to_market(x, y) for x, y in self._roi_points(r)]))

        elif t in ("rect", "ellipse", "image"):
            (x1, y1), (x2, y2) = pts
            img = None
            if t == "image":
                img = _load_image(d.get("image", ""))
                if img is None:
                    return
            roi = ShapeROI((min(x1, x2), min(y1, y2)), (abs(x2 - x1), abs(y2 - y1)), t, color, width, img)
            self._own(did, roi)

            def fin(r):
                pos, size = r.pos(), r.size()
                a = (pos.x(), pos.y())
                b = (pos.x() + size.x(), pos.y() + size.y())
                self._commit(d, [self.to_market(*a), self.to_market(*b)])

            roi.sigRegionChangeFinished.connect(fin)

        elif t == "pen":
            xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
            item = pg.PlotCurveItem(xs, ys, pen=pen)
            self._own(did, item)

        elif t == "text":
            size = 9 + 2 * width
            item = DraggableText(d.get("text", ""), color=color, anchor=(0, 0))
            f = QtGui.QFont()
            f.setPointSize(size)
            f.setBold(width >= 3)
            item.setFont(f)
            item.setPos(*pts[0])
            item.sigMoved.connect(lambda it: self._commit(d, [self.to_market(it.pos().x(), it.pos().y())]))
            self._own(did, item)

        elif t == "fib":
            roi = pg.LineSegmentROI(pts, pen=pg.mkPen(color, width=1, style=QtCore.Qt.DashLine),
                                    hoverPen=hover, handlePen=pg.mkPen(color))
            self._own(did, roi)
            levels = []
            for r, c in FIB_LEVELS:
                ln = self._own(did, pg.PlotCurveItem(pen=pg.mkPen(c, width=1)))
                tx = self._own(did, pg.TextItem("", color=c, anchor=(1, 1)))
                levels.append((r, ln, tx))

            def update_fib(rr=roi):
                (x1, y1), (x2, y2) = self._roi_points(rr)
                p1, p2 = self.y2p(y1), self.y2p(y2)
                xa, xb = min(x1, x2), max(x1, x2)
                for r, ln, tx in levels:
                    price = p2 - (p2 - p1) * r
                    yv = self.p2y(price)
                    ln.setData(np.array([xa, xb]), np.array([yv, yv]))
                    tx.setText(f"{r * 100:g}%  ({_fmt(price)})")
                    tx.setPos(xb, yv)

            roi.sigRegionChanged.connect(lambda *_: update_fib())
            roi.sigRegionChangeFinished.connect(
                lambda r: self._commit(d, [self.to_market(x, y) for x, y in self._roi_points(r)]))
            update_fib()

    @staticmethod
    def _roi_points(roi):
        out = []
        for h in roi.handles:
            p = roi.mapToParent(h["item"].pos())
            out.append((p.x(), p.y()))
        return out
