"""Mapa de calor de una lista de seguimiento: un cuadro por símbolo, coloreado por su cambio del día."""
from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .chart import fmt_price

GAIN, LOSS, FLAT = (8, 153, 129), (242, 54, 69), (74, 78, 89)
SCALES = [1, 2, 3, 5, 10]  # % de cambio con el que un cuadro llega al color más intenso
HEAD = 18  # alto del título de cada sección


def tile_color(chg: float, scale: float) -> QtGui.QColor:
    """Gris en 0 %, verde o rojo cada vez más intenso hasta ``scale`` %."""
    k = min(abs(chg) / scale, 1.0) if np.isfinite(chg) else 0.0
    k = k ** 0.7  # los cambios chicos también se distinguen
    target = GAIN if chg >= 0 else LOSS
    return QtGui.QColor(*(round(f + (t - f) * k) for f, t in zip(FLAT, target)))


def split_rects(weights: list[float], rect: QtCore.QRectF) -> list[QtCore.QRectF]:
    """Reparte el rectángulo en partes proporcionales a los pesos, cortando siempre el lado más largo."""
    if not weights:
        return []
    if len(weights) == 1:
        return [rect]
    total, acc, cut = sum(weights), 0.0, 1
    for i, w in enumerate(weights[:-1], start=1):  # el corte que deja las dos mitades más parejas
        acc += w
        cut = i
        if acc >= total / 2:
            break
    share = sum(weights[:cut]) / total if total else 0.5
    if rect.width() >= rect.height():
        a = QtCore.QRectF(rect.x(), rect.y(), rect.width() * share, rect.height())
        b = QtCore.QRectF(a.right(), rect.y(), rect.width() - a.width(), rect.height())
    else:
        a = QtCore.QRectF(rect.x(), rect.y(), rect.width(), rect.height() * share)
        b = QtCore.QRectF(rect.x(), a.bottom(), rect.width(), rect.height() - a.height())
    return split_rects(weights[:cut], a) + split_rects(weights[cut:], b)


class HeatmapWidget(QtWidgets.QWidget):
    openSymbol = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups: list = []   # [(sección, [(símbolo, nombre, último, cambio %)])]
        self.scale = 3
        self._tiles: list = []   # [(rectángulo, símbolo, nombre, último, cambio)]
        self._heads: list = []   # [(rectángulo, título)]
        self.setMouseTracking(True)
        self.setMinimumHeight(60)

    def set_groups(self, groups: list):
        """Secciones en el orden de la lista; dentro de cada una, de mayor a menor cambio."""
        self.groups = [(name, sorted(rows, key=lambda r: r[3], reverse=True)) for name, rows in groups if rows]
        self.update()

    def set_scale(self, pct: float):
        self.scale = pct
        self.update()

    def _layout(self):
        self._tiles, self._heads = [], []
        area = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if area.width() < 20 or area.height() < 20:
            return
        blocks = split_rects([len(rows) for _n, rows in self.groups], area)
        for (name, rows), block in zip(self.groups, blocks):
            block = block.adjusted(1, 1, -1, -1)
            head = HEAD if len(self.groups) > 1 and block.height() > HEAD * 3 else 0
            if head:
                self._heads.append((QtCore.QRectF(block.x(), block.y(), block.width(), head), name))
            body = block.adjusted(0, head, 0, 0)
            n = len(rows)
            # columnas que dejan los cuadros lo más parecido posible a un rectángulo 3:2
            cols = int(min(n, max(1, round(np.sqrt(n * body.width() / max(body.height(), 1) / 1.5)))))
            lines = int(np.ceil(n / cols))
            w, h = body.width() / cols, body.height() / lines
            for i, row in enumerate(rows):
                r = QtCore.QRectF(body.x() + (i % cols) * w, body.y() + (i // cols) * h, w, h)
                self._tiles.append((r.adjusted(1, 1, -1, -1), *row))

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        if not self.groups:
            p.setPen(QtGui.QColor("#787b86"))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "Todavía no hay cotizaciones de esta lista.")
            return
        self._layout()
        base = self.font()
        small = QtGui.QFont(base)
        small.setPointSizeF(max(7.0, base.pointSizeF() - 1))
        p.setFont(small)
        p.setPen(QtGui.QColor(theme.c("#9598a1")))
        for r, title in self._heads:
            text = QtGui.QFontMetrics(small).elidedText(title, QtCore.Qt.ElideRight, int(r.width()) - 4)
            p.drawText(r.adjusted(3, 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)
        for r, sym, _name, last, chg in self._tiles:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(tile_color(chg, self.scale))
            p.drawRoundedRect(r, 3, 3)
            if r.width() < 34 or r.height() < 16:
                continue  # muy chico para texto: queda el color y el detalle al pasar el mouse
            p.setPen(QtGui.QColor("#ffffff"))
            big = QtGui.QFont(base)
            big.setBold(True)
            big.setPointSizeF(float(np.clip(min(r.width() / 9, r.height() / 4.5), 7, 16)))
            fm = QtGui.QFontMetrics(big)
            lines = [(big, fm.elidedText(sym, QtCore.Qt.ElideRight, int(r.width()) - 6))]
            detail = QtGui.QFont(base)
            detail.setPointSizeF(max(7.0, big.pointSizeF() - 2))
            if r.height() >= fm.height() * 2 + 2:
                lines.append((detail, f"{chg:+.2f}%"))
            if r.height() >= fm.height() * 3.4 and r.width() >= 60:
                lines.append((detail, fmt_price(last)))
            heights = [QtGui.QFontMetrics(f).height() for f, _t in lines]
            y = r.center().y() - sum(heights) / 2
            for (font, text), height in zip(lines, heights):
                p.setFont(font)
                p.drawText(QtCore.QRectF(r.x(), y, r.width(), height), QtCore.Qt.AlignCenter, text)
                y += height

    def _tile_at(self, pos):
        point = QtCore.QPointF(pos)
        return next((t for t in self._tiles if t[0].contains(point)), None)

    def mouseMoveEvent(self, ev):
        tile = self._tile_at(ev.position())
        self.setCursor(QtCore.Qt.PointingHandCursor if tile else QtCore.Qt.ArrowCursor)
        if tile:
            _r, sym, name, last, chg = tile
            title = f"<b>{sym}</b>" + (f" — {name}" if name else "")
            QtWidgets.QToolTip.showText(ev.globalPosition().toPoint(),
                                        f"{title}<br>{fmt_price(last)} &nbsp; {chg:+.2f}%", self)
        else:
            QtWidgets.QToolTip.hideText()

    def mouseReleaseEvent(self, ev):
        tile = self._tile_at(ev.position())
        if tile and ev.button() == QtCore.Qt.LeftButton:
            self.openSymbol.emit(tile[1])


class HeatmapPanel(QtWidgets.QWidget):
    """El mapa con su barra: qué lista se ve y con qué % se llega al color más intenso."""
    openSymbol = QtCore.Signal(str)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.map = HeatmapWidget()
        self.map.openSymbol.connect(self.openSymbol)
        self.title = QtWidgets.QLabel("")
        theme.style(self.title, "color:#9598a1; padding:0 4px;")
        self.scale = QtWidgets.QComboBox()
        self.scale.setToolTip("Cambio del día con el que un cuadro llega al color más intenso")
        for s in SCALES:
            self.scale.addItem(f"± {s} %", s)
        self.scale.setCurrentIndex(max(0, self.scale.findData(cfg.get("heatmap_scale", 3))))
        self.scale.currentIndexChanged.connect(self._on_scale)
        self.map.set_scale(self.scale.currentData())
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(4, 2, 4, 2)
        top.addWidget(self.title, 1)
        top.addWidget(self.scale)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.map, 1)

    def _on_scale(self):
        self.cfg["heatmap_scale"] = self.scale.currentData()
        self.map.set_scale(self.scale.currentData())

    def set_groups(self, list_name: str, groups: list):
        count = sum(len(rows) for _n, rows in groups)
        ups = sum(1 for _n, rows in groups for r in rows if r[3] >= 0)
        self.title.setText(f"{list_name} · {count} símbolos · {ups} suben, {count - ups} bajan · cambio del día"
                           if count else list_name)
        self.map.set_groups(groups)
