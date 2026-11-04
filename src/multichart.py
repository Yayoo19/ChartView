"""Varias gráficas en una ventana: el panel de cada gráfica y los diseños disponibles."""
from __future__ import annotations

from copy import deepcopy

import pandas as pd
from PySide6 import QtCore, QtGui, QtWidgets

from .chart import ChartWidget
from .indicators import REGISTRY

# Cada diseño es un árbol: un número es una gráfica; ("h", ...) las pone en columnas y ("v", ...) en filas.
LAYOUTS = {
    "1": ("1 gráfica", 0),
    "2c": ("2 en columnas", ("h", 0, 1)),
    "2f": ("2 en filas", ("v", 0, 1)),
    "3c": ("3 en columnas", ("h", 0, 1, 2)),
    "3i": ("1 grande a la izquierda y 2 a la derecha", ("h", 0, ("v", 1, 2))),
    "3s": ("1 grande arriba y 2 abajo", ("v", 0, ("h", 1, 2))),
    "4": ("4 en cuadrícula", ("v", ("h", 0, 1), ("h", 2, 3))),
    "6": ("6 en cuadrícula", ("v", ("h", 0, 1, 2), ("h", 3, 4, 5))),
    "8": ("8 en cuadrícula", ("v", ("h", 0, 1, 2, 3), ("h", 4, 5, 6, 7))),
}
MAX_CHARTS = 8
SYNC_OPTIONS = [("symbol", "Símbolo"), ("interval", "Intervalo"), ("cursor", "Cursor"), ("time", "Rango de tiempo")]


def translate_time(t: float, source: "ChartPane", target: "ChartPane") -> float:
    """Fecha de ``source`` expresada en el reloj de ``target``. Cada gráfica guarda la hora local de su
    mercado. Solo se convierte entre dos gráficas intradía: una vela diaria representa un día de
    calendario, y convertirla a otra zona la movería al día anterior."""
    if not (source.intraday and target.intraday) or not source.tz or not target.tz or source.tz == target.tz:
        return t
    try:
        stamp = pd.Timestamp(t, unit="s").tz_localize(source.tz, ambiguous=False, nonexistent="shift_forward")
        return stamp.tz_convert(target.tz).tz_localize(None).value / 1e9
    except Exception:  # noqa: BLE001 - zona desconocida o fecha fuera de rango: mejor sin convertir
        return t


def chart_count(kind: str) -> int:
    def walk(node):
        return 1 if isinstance(node, int) else sum(walk(c) for c in node[1:])
    return walk(LAYOUTS[kind][1])


def build_grid(kind: str, panes: list) -> tuple[QtWidgets.QWidget, list[QtWidgets.QSplitter]]:
    """Acomoda las gráficas según el diseño. Devuelve el widget raíz y sus divisores."""
    splitters = []

    def walk(node):
        if isinstance(node, int):
            return panes[node]
        sp = QtWidgets.QSplitter(QtCore.Qt.Horizontal if node[0] == "h" else QtCore.Qt.Vertical)
        sp.setChildrenCollapsible(False)
        sp.setHandleWidth(4)
        splitters.append(sp)
        children = [walk(c) for c in node[1:]]
        for c in children:
            sp.addWidget(c)
        sp.setSizes([1000] * len(children))  # partes iguales; Qt las escala al tamaño real
        return sp

    return walk(LAYOUTS[kind][1]), splitters


def layout_icon(kind: str, size: int = 18) -> QtGui.QIcon:
    """Miniatura del diseño, dibujada al vuelo."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setPen(QtGui.QPen(QtGui.QColor("#d1d4dc"), 1))
    p.setBrush(QtCore.Qt.NoBrush)

    def walk(node, r: QtCore.QRectF):
        if isinstance(node, int):
            p.drawRect(r)
            return
        n = len(node) - 1
        for i, c in enumerate(node[1:]):
            if node[0] == "h":
                walk(c, QtCore.QRectF(r.x() + r.width() * i / n, r.y(), r.width() / n, r.height()))
            else:
                walk(c, QtCore.QRectF(r.x(), r.y() + r.height() * i / n, r.width(), r.height() / n))

    walk(LAYOUTS[kind][1], QtCore.QRectF(1.5, 1.5, size - 3, size - 3))
    p.end()
    return QtGui.QIcon(pm)


class ChartPane(QtWidgets.QFrame):
    """Una gráfica de la ventana, con su encabezado y todo lo que le es propio."""

    activated = QtCore.Signal(object)

    def __init__(self, state: dict, grid_alpha: float):
        super().__init__()
        state = deepcopy(state)
        self.setObjectName("chartpane")
        self.setMinimumSize(160, 120)
        self.df = None
        self.meta: dict = {}
        self.symbol = ""
        self.want = state.get("symbol") or ""  # el último símbolo pedido, aunque todavía no cargue
        self.interval = state.get("interval") or "1 día"
        self.period_key = state.get("period") or "2y"
        self.chart_type = state.get("chart_type") or "Velas"
        self.log = bool(state.get("log"))
        self.pct = bool(state.get("pct")) and not self.log
        self.chart_params = dict(state.get("chart_params") or {})  # tipo de gráfica -> sus ajustes
        self.indicators = [a for a in state.get("indicators") or [] if a.get("key") in REGISTRY]
        self.comparisons = list(state.get("comparisons") or [])
        self._comp_closes: dict = {}  # símbolo -> cierres alineados a self.df
        self._req = 0

        self.chart = ChartWidget()
        self.header = self._label()  # van encima de la gráfica, para que ella tenga toda la altura
        self.info = self._label()
        self.chart.grid_alpha = grid_alpha
        self.chart.pane_heights = dict(state.get("pane_heights") or {})
        self.chart.hovered.connect(self.info.setText)
        self.chart.viewport().installEventFilter(self)
        self.chart.installEventFilter(self)  # para reacomodar el encabezado al cambiar de tamaño
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(self.chart, 1)
        self.compact = False
        self.set_appearance(False, True)

    @property
    def intraday(self) -> bool:
        return self.chart._intraday  # el de los datos que se ven, no el del intervalo que aún carga

    @property
    def tz(self) -> str | None:
        return self.meta.get("exchangeTimezoneName") or self.meta.get("indexTimezone")

    def _label(self) -> QtWidgets.QLabel:
        lab = QtWidgets.QLabel("", self.chart)
        lab.setTextFormat(QtCore.Qt.RichText)
        lab.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)  # el mouse sigue llegando a la gráfica
        return lab

    def _place_labels(self):
        """Encabezado y datos de la vela arriba a la izquierda; la leyenda de indicadores, debajo."""
        width = max(self.chart.width() - 88, 40)  # sin tapar el eje de precios
        y = 2
        for lab in (self.header, self.info):
            height = lab.sizeHint().height()
            lab.setGeometry(6, y, width, height)
            lab.raise_()
            y += height
        self.chart.set_legend_top(y)

    def set_appearance(self, compact: bool, active: bool):
        """``compact``: hay varias gráficas en la ventana. ``active``: es la que recibe las órdenes."""
        self.compact = compact
        self.header.setStyleSheet(f"background:transparent; font-size:{13 if compact else 15}px; padding:1px 2px;")
        self.info.setStyleSheet(f"background:transparent; font-size:{11 if compact else 12}px; padding:0 2px;")
        self.chart.set_compact(compact)
        color = "transparent" if not compact else "#2962ff" if active else "#2a2e39"
        self.setStyleSheet(f"#chartpane{{border:1px solid {color};}}")
        self._place_labels()

    def state(self) -> dict:
        return {"symbol": self.symbol or self.want, "interval": self.interval, "period": self.period_key,
                "chart_type": self.chart_type, "log": self.log, "pct": self.pct,
                "chart_params": self.chart_params, "indicators": self.indicators,
                "comparisons": self.comparisons, "pane_heights": dict(self.chart.pane_heights)}

    def retire(self):
        """Saca la gráfica de la ventana; lo que aún se esté descargando para ella se descarta."""
        self._req += 1
        self.chart._countdown.stop()
        self.setParent(None)
        self.deleteLater()

    def eventFilter(self, obj, ev):
        if obj is self.chart:
            if ev.type() == QtCore.QEvent.Resize:
                self._place_labels()
            return False
        if ev.type() == QtCore.QEvent.MouseButtonPress:
            self.activated.emit(self)  # no se consume: el clic sigue hacia la gráfica
        return False

    def mousePressEvent(self, ev):  # clic en el borde
        self.activated.emit(self)
        super().mousePressEvent(ev)
