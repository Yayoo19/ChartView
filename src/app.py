"""Ventana principal de ChartView."""
from __future__ import annotations

import datetime as dt
from copy import deepcopy

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from pyqtgraph.util.garbage_collector import GarbageCollector

from . import alerts, config, data, macro, theme
from .alerts_ui import AlertsWindow
from .analysis import AnalysisPanel
from .bottom_panel import BottomPanel
from .chart import CANDLE_TYPES, CHART_TYPES, DOWN, UP, fmt_change, fmt_price
from .chart_types import PARAM_SPECS, TRANSFORMED, transform
from .dialogs import (CandleColorsDialog, ChartTypeDialog, CompareDialog, IndicatorDialog, IndicatorEditDialog,
                      SearchDialog)
from .drawings import TOOL_GROUPS, TOOLS, DrawingHistory
from .indicators import REGISTRY, by_category, compute
from .multichart import LAYOUTS, SYNC_OPTIONS, ChartPane, build_grid, chart_count, layout_icon, translate_time
from .searchbox import SymbolSearch
from .workers import run_task

REFRESH_OPTIONS = [("15 s", 15), ("30 s", 30), ("1 min", 60), ("5 min", 300), ("15 min", 900)]

# botones rápidos de intervalo (texto corto, intervalo); el resto está en la lista desplegable
QUICK_INTERVALS = [("5m", "5 minutos"), ("15m", "15 minutos"), ("1h", "1 hora"), ("4h", "4 horas"),
                   ("D", "1 día"), ("S", "1 semana"), ("M", "1 mes")]
# colores de las líneas de comparación (distintos de las velas y de los primeros indicadores)
COMPARE_COLORS = ["#ff9800", "#e040fb", "#00bcd4", "#ffd600", "#76ff03", "#ec407a", "#90caf9"]
# botones de rango bajo la gráfica
RANGE_ORDER = ["1d", "5d", "1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y", "10y", "max"]
RANGE_LABELS = {"1d": "1D", "5d": "5D", "1mo": "1M", "3mo": "3M", "6mo": "6M", "ytd": "YTD", "1y": "1A",
                "2y": "2A", "5y": "5A", "10y": "10A", "max": "Todo"}
# imán al dibujar
MAGNET_MODES = {"off": "Desactivado", "weak": "Débil (se pega si estás cerca de la vela)",
                "strong": "Fuerte (siempre se pega a apertura, máximo, mínimo o cierre)"}
# cuadrícula del fondo: (nombre, opacidad de las líneas)
GRID_LEVELS = [("Sin cuadrícula", 0.0), ("Suave", 0.12), ("Media", 0.22), ("Fuerte", 0.4)]
WINDOW_STATE_VERSION = 2  # el acomodo guardado antes de la columna de la derecha ya no aplica
SHORTCUTS = [
    ("Ctrl+L", "Ir al buscador de símbolos"), ("Ctrl+F", "Búsqueda avanzada"), ("F5", "Actualizar datos"),
    ("Inicio", "Ver las últimas velas"), ("Alt+L", "Escala logarítmica"), ("Ctrl+B", "Mostrar u ocultar listas"), ("Ctrl+D", "Mostrar u ocultar herramientas de dibujo"),
    ("Ctrl+E", "Mostrar u ocultar el panel de análisis"), ("Ctrl+J", "Abrir o plegar el panel inferior"),
    ("Ctrl+I", "Indicadores"), ("Ctrl+Alt+A", "Alertas"), ("Ctrl+S", "Guardar imagen de la gráfica"),
    ("Alt+P", "Escala porcentual"), ("Alt+I", "Invertir la escala (gráfica de cabeza)"), ("Ctrl+Z", "Deshacer en los dibujos"), ("Ctrl+Y", "Rehacer en los dibujos"),
    ("Shift+clic", "Medir precio, % y velas"), ("Esc", "Volver al cursor (dibujo)"), ("F11", "Pantalla completa"),
    ("Ctrl+G", "Encender o apagar la cuadrícula"), ("Ctrl+M", "Mapa de calor de la lista activa"),
    ("Ctrl+T", "Cambiar entre tema oscuro y claro"), ("F1", "Esta ayuda"), ("Ctrl+Q", "Salir"),
]


NAME_ROLE = QtCore.Qt.UserRole + 1


class WatchDelegate(QtWidgets.QStyledItemDelegate):
    """Columna de símbolo de la lista: ticker arriba y nombre de la empresa abajo, en gris."""

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.data(NAME_ROLE):
            size.setHeight(max(size.height(), 34))
        return size

    def paint(self, painter, option, index):
        name = index.data(NAME_ROLE)
        if index.column() != 0 or not name:
            return super().paint(painter, option, index)
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        style = opt.widget.style() if opt.widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, opt.widget)  # fondo y selección
        r = QtCore.QRectF(style.subElementRect(QtWidgets.QStyle.SE_ItemViewItemText, opt, opt.widget))
        half = r.height() / 2
        painter.save()
        selected = bool(opt.state & QtWidgets.QStyle.State_Selected)
        painter.setPen(opt.palette.color(QtGui.QPalette.HighlightedText if selected else QtGui.QPalette.Text))
        painter.setFont(opt.font)
        painter.drawText(QtCore.QRectF(r.x() + 2, r.y() + 1, r.width(), half),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)
        small = QtGui.QFont(opt.font)
        small.setPointSizeF(max(7.0, opt.font.pointSizeF() - 1.5))
        painter.setFont(small)
        painter.setPen(QtGui.QColor("#d1d4dc" if selected else "#787b86"))
        elided = QtGui.QFontMetrics(small).elidedText(name, QtCore.Qt.ElideRight, int(r.width()) - 2)
        painter.drawText(QtCore.QRectF(r.x() + 2, r.y() + half - 2, r.width(), half),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, elided)
        painter.restore()


class WatchTree(QtWidgets.QTreeWidget):
    """Lista de seguimiento con orden manual: se arrastran símbolos (dentro de su sección o a otra)
    y secciones completas. El árbol no se mueve solo: avisa el cambio y la ventana lo rehace desde la config."""
    symbolMoved = QtCore.Signal(int, int, int, int)  # sección y fila de origen, sección y fila de destino
    sectionMoved = QtCore.Signal(int, int)  # índice de origen, posición de destino (antes de quitarla)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.setDropIndicatorShown(True)

    def dropEvent(self, ev):
        src = self.currentItem()
        target = self.itemAt(ev.position().toPoint())
        pos = self.dropIndicatorPosition()
        # el movimiento lo hace la ventana sobre la config; Qt no debe mover ni borrar nada por su cuenta
        ev.setDropAction(QtCore.Qt.IgnoreAction)
        ev.accept()
        n = self.topLevelItemCount()
        if src is None or n == 0:
            return
        V = QtWidgets.QAbstractItemView
        if src.parent() is None:  # sección completa
            si = self.indexOfTopLevelItem(src)
            if target is None:
                new = n
            else:
                ts = target if target.parent() is None else target.parent()
                ti = self.indexOfTopLevelItem(ts)
                if target is ts and pos == V.AboveItem:
                    new = ti
                elif target is ts and pos == V.BelowItem:
                    new = ti + 1
                else:  # sobre la sección o sobre uno de sus símbolos: toma su lugar
                    new = ti if si > ti else ti + 1
            if new not in (si, si + 1):
                QtCore.QTimer.singleShot(0, lambda: self.sectionMoved.emit(si, new))
            return
        ss, sr = self.indexOfTopLevelItem(src.parent()), src.parent().indexOfChild(src)
        if target is None:  # espacio vacío de abajo: al final de la última sección
            ds = n - 1
            dr = self.topLevelItem(ds).childCount()
        elif target.parent() is None:  # sobre el título de una sección
            ti = self.indexOfTopLevelItem(target)
            if pos == V.AboveItem and ti > 0:  # justo arriba del título = final de la sección anterior
                ds, dr = ti - 1, self.topLevelItem(ti - 1).childCount()
            elif pos == V.BelowItem and target.isExpanded():
                ds, dr = ti, 0
            else:
                ds, dr = ti, (0 if pos == V.AboveItem else target.childCount())
        else:
            ds = self.indexOfTopLevelItem(target.parent())
            dr = target.parent().indexOfChild(target) + (0 if pos == V.AboveItem else 1)
        if (ds, dr) not in ((ss, sr), (ss, sr + 1)):
            QtCore.QTimer.singleShot(0, lambda: self.symbolMoved.emit(ss, sr, ds, dr))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChartView")
        self.resize(1500, 920)
        self.cfg = config.load()
        theme.configure(self.cfg)
        theme.apply(STYLE)
        # los cambios seguidos (cargar varias gráficas, arrastrar un borde…) se guardan una sola vez
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._flush_save)
        self._save_failed = False
        if config.load_warning:
            QtCore.QTimer.singleShot(0, lambda: QtWidgets.QMessageBox.warning(
                self, "Configuración dañada", config.load_warning))
        macro.FRED_API_KEY = self.cfg.get("fred_api_key", "")
        self._syncing_ui = False  # la barra se está poniendo al día con la gráfica activa: no es una orden
        self._inflight: dict = {}  # descargas en curso: (símbolo, intervalo, periodo) -> quienes la esperan
        self.sync = {k: bool(self.cfg.get("sync", {}).get(k, k == "cursor")) for k, _ in SYNC_OPTIONS}
        self._draw_history = DrawingHistory()  # compartido: los dibujos son del símbolo, no de la gráfica
        self._create_panes()

        self._build_actions()
        self._build_toolbar()
        self._build_center()
        self._build_bottom()  # antes de las listas: se llena con sus precios
        self._build_watchlist()
        self._build_analysis_dock()
        self._build_rail()
        self._build_menu()
        self._build_shortcuts()
        self.statusBar().showMessage("Listo")

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(lambda: self._refresh_all(silent=True))
        self.quote_timer = QtCore.QTimer(self)
        self.quote_timer.timeout.connect(self.refresh_quotes)
        self.quote_timer.start(5 * 60 * 1000)

        self._apply_auto_refresh()
        self._setup_alerts()
        if self.cfg.get("window_geometry"):
            self.restoreGeometry(QtCore.QByteArray.fromBase64(self.cfg["window_geometry"].encode()))
        if self.cfg.get("window_state"):
            self.restoreState(QtCore.QByteArray.fromBase64(self.cfg["window_state"].encode()), WINDOW_STATE_VERSION)
        if self.watch_dock.isVisible() and self.analysis_dock.isVisible():
            self.analysis_dock.hide()  # un panel a la vez
        self.bottom.set_state(int(self._bottom_start.get("tab", 0)), bool(self._bottom_start.get("open")))
        QtCore.QTimer.singleShot(0, self._load_all)
        QtCore.QTimer.singleShot(500, self.refresh_quotes)

    # ================================================================ varias gráficas
    # El resto de la ventana (indicadores, comparar, alertas, análisis…) trabaja sobre la gráfica activa.
    chart = property(lambda self: self.active.chart)
    df = property(lambda self: self.active.df)
    meta = property(lambda self: self.active.meta)
    symbol = property(lambda self: self.active.symbol)
    period_key = property(lambda self: self.active.period_key)
    comparisons = property(lambda self: self.active.comparisons)
    indicators = property(lambda self: self.active.indicators,
                          lambda self, value: setattr(self.active, "indicators", value))

    def _create_panes(self):
        cfg = self.cfg
        self.layout_kind = cfg.get("layout") if cfg.get("layout") in LAYOUTS else "1"
        states = list(cfg.get("charts") or [])
        if not states:  # configuración anterior a las varias gráficas
            states = [{"symbol": cfg.get("symbol"), "interval": cfg.get("interval"), "period": cfg.get("period"),
                       "chart_type": cfg.get("chart_type"), "log": cfg.get("log_scale"),
                       "indicators": cfg.get("indicators"), "comparisons": cfg.get("comparisons"),
                       "pane_heights": cfg.get("pane_heights")}]
        n = chart_count(self.layout_kind)
        states += [states[0]] * (n - len(states))
        self.panes = []
        self.active = None
        for st in states[:n]:
            self.panes.append(self._new_pane(st))
        self.active = self.panes[min(int(cfg.get("active_chart", 0)), n - 1)]
        self._parked = states[n:]  # gráficas que no caben en este diseño; regresan si vuelve a haber lugar

    def _new_pane(self, state: dict) -> ChartPane:
        pane = ChartPane(state, dict(GRID_LEVELS).get(self.cfg.get("grid", "Media"), 0.22))
        pane.activated.connect(self._set_active)
        chart, layer = pane.chart, pane.chart.layer
        layer.history = self._draw_history
        chart.panesResized.connect(self._save)
        chart.legendAction.connect(lambda act, i, p=pane: (self._set_active(p), self._legend_action(act, i)))
        chart.compareRemoved.connect(lambda sym, p=pane: (self._set_active(p), self.remove_comparison(sym)))
        chart.cursorTime.connect(lambda t, p=pane: self._on_cursor(p, t))
        chart.rangeChanged.connect(lambda p=pane: self._on_view_changed(p))
        layer.changed.connect(lambda p=pane: self._save_drawings(p))
        layer.toolReset.connect(lambda: self._tool_actions["cursor"].setChecked(True) or self._on_tool("cursor"))
        layer.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        layer.alertRequested.connect(self.alert_at_price)
        if self.active is not None:  # la barra de dibujo es una sola: la gráfica nueva hereda lo elegido
            src = self.active.chart.layer
            layer.color, layer.width, layer.magnet, layer.visible = src.color, src.width, src.magnet, src.visible
            layer.set_tool(src.tool)
            if src.tool != "cursor":
                chart.setCursor(QtCore.Qt.CrossCursor)
        return pane

    def _load_all(self):
        # solo la activa avisa con ventana si falla, para no encimar un aviso por cada gráfica
        for p in self.panes:
            self.load(p.want, raw=True, silent=p is not self.active, pane=p)

    def _refresh_all(self, silent=False):
        for p in self.panes:
            self.load(p.want, raw=True, keep_view=True, silent=silent or p is not self.active, pane=p)

    def _set_active(self, pane: ChartPane):
        if pane is self.active:
            return
        self.active = pane
        self.statusBar().clearMessage()  # el mensaje era de la gráfica anterior
        self._apply_appearance()
        self._sync_ui()
        self._refresh_analysis()
        if pane.df is not None:
            self._update_header(data.INTERVALS[pane.interval][2], pane)

    def _apply_appearance(self):
        for p in self.panes:
            p.set_appearance(len(self.panes) > 1, p is self.active)
            if p.df is not None:  # el encabezado cambia entre compacto y completo
                self._update_header(data.INTERVALS[p.interval][2], p)

    def _sync_ui(self):
        """Pone la barra superior y la de rangos con los datos de la gráfica activa."""
        a = self.active
        self._syncing_ui = True
        try:
            self.sym_edit.setText(a.want)
            self.interval.setCurrentText(a.interval)
            self.ctype.setCurrentText(a.chart_type)
            self.log_scale.setChecked(a.log)
            self.pct_scale.setChecked(a.pct)
            self.invert_act.setChecked(a.invert)
        finally:
            self._syncing_ui = False
        self._update_chart_params_button()
        self._sync_interval_buttons(a.interval)
        self._sync_range_enabled(a.interval)
        self._set_period(a.period_key)
        self._update_compare_button()

    def _build_layout_menu(self):
        menu = QtWidgets.QMenu("Diseño de gráficas", self)
        group = QtGui.QActionGroup(menu)
        self._layout_actions = {}
        for kind, (name, _tree) in LAYOUTS.items():
            act = menu.addAction(layout_icon(kind), name, lambda k=kind: self.set_layout(k))
            act.setCheckable(True)
            act.setChecked(kind == self.layout_kind)
            group.addAction(act)
            self._layout_actions[kind] = act
        menu.addSeparator()
        saved = menu.addMenu("Diseños guardados")
        saved.aboutToShow.connect(lambda: self._fill_saved_layouts(saved))
        menu.addSeparator()
        title = menu.addAction("Sincronizar en todas las gráficas:")
        title.setEnabled(False)
        for key, name in SYNC_OPTIONS:
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(self.sync[key])
            act.toggled.connect(lambda on, k=key: self._set_sync(k, on))
        self._layout_menu = menu
        self.layout_btn = QtWidgets.QToolButton()
        self.layout_btn.setToolTip("Varias gráficas: diseño y sincronización")
        self.layout_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.layout_btn.setIcon(layout_icon(self.layout_kind))
        self.layout_btn.setMenu(menu)
        return self.layout_btn

    def _place_panes(self):
        """(Re)arma la cuadrícula con el diseño actual, sin tocar las gráficas."""
        for p in self.panes:
            p.setParent(None)
        old = self._grid_lay.takeAt(0)
        if old is not None and old.widget() is not None:
            old.widget().deleteLater()
        root, self._splitters = build_grid(self.layout_kind, self.panes)
        saved = self.cfg.get("layout_sizes", {}).get(self.layout_kind) or []
        if len(saved) == len(self._splitters):
            for sp, sizes in zip(self._splitters, saved):
                if len(sizes) == sp.count() and all(sizes):
                    sp.setSizes(sizes)
        self._grid_lay.addWidget(root)
        for p in self.panes:
            p.show()
        self._apply_appearance()

    def set_layout(self, kind: str):
        if kind == self.layout_kind:
            return
        self._remember_sizes()
        self.layout_kind = kind
        n = chart_count(kind)
        while len(self.panes) > n:
            p = self.panes.pop()
            self._parked.insert(0, deepcopy(p.state()))
            p.retire()
        if self.active not in self.panes:
            self.active = self.panes[0]
        added = []
        while len(self.panes) < n:
            # primero las que se habían guardado; si no hay, una copia de la activa
            st = self._parked.pop(0) if self._parked else self.active.state()
            st = {**st, "symbol": self.active.want} if self.sync["symbol"] else st
            if self.sync["interval"]:
                st = {**st, "interval": self.active.interval, "period": self.active.period_key}
            pane = self._new_pane(st)
            self.panes.append(pane)
            added.append(pane)
        self._place_panes()
        self.layout_btn.setIcon(layout_icon(kind))
        self._layout_actions[kind].setChecked(True)
        self._sync_ui()
        self._refresh_analysis()
        for p in added:
            self.load(p.want, raw=True, silent=True, pane=p)
        self._save()

    # ---------------------------------------------------- diseños guardados
    def _fill_saved_layouts(self, menu):
        menu.clear()
        saved = self.cfg.get("saved_layouts", {})
        menu.addAction("Guardar el diseño actual…", self.save_layout_as)
        if saved:
            menu.addSeparator()
            for name, item in saved.items():
                kind = item.get("layout") if item.get("layout") in LAYOUTS else "1"
                symbols = ", ".join(st.get("symbol") or "?" for st in item.get("charts", [])[:chart_count(kind)])
                act = menu.addAction(layout_icon(kind), name, lambda n=name: self.apply_saved_layout(n))
                act.setToolTip(symbols)
            menu.addSeparator()
            sub = menu.addMenu("Eliminar")
            for name in saved:
                sub.addAction(name, lambda n=name: self._delete_saved("saved_layouts", n, "el diseño"))
        menu.setToolTipsVisible(True)

    def _ask_save_name(self, title: str, store: dict, what: str):
        """Nombre para guardar algo; si ya existe, pregunta antes de reemplazarlo."""
        name, ok = QtWidgets.QInputDialog.getText(self, title, f"Nombre de {what}:")
        name = name.strip()
        if not ok or not name:
            return None
        if name in store and QtWidgets.QMessageBox.question(
                self, title, f"Ya existe «{name}». ¿Reemplazarlo?") != QtWidgets.QMessageBox.Yes:
            return None
        return name

    def _delete_saved(self, key: str, name: str, what: str):
        if QtWidgets.QMessageBox.question(self, "Eliminar", f"¿Eliminar {what} «{name}»?") == QtWidgets.QMessageBox.Yes:
            self.cfg.get(key, {}).pop(name, None)
            self._save()

    def save_layout_as(self):
        """Guarda con nombre el acomodo de gráficas y lo que muestra cada una."""
        store = self.cfg.setdefault("saved_layouts", {})
        name = self._ask_save_name("Guardar diseño", store, "el diseño")
        if name:
            store[name] = {"layout": self.layout_kind, "charts": deepcopy([p.state() for p in self.panes])}
            self._save()
            self.statusBar().showMessage(f"Diseño «{name}» guardado.", 5000)

    def apply_saved_layout(self, name: str):
        item = deepcopy(self.cfg.get("saved_layouts", {}).get(name))
        if not item or not item.get("charts"):
            return
        kind = item.get("layout") if item.get("layout") in LAYOUTS else "1"
        n = chart_count(kind)
        states = item["charts"][:n]
        states += [states[0]] * (n - len(states))
        self._remember_sizes()
        old = self.panes
        # las gráficas nuevas heredan de la activa lo elegido en la barra de dibujo, así que se crean antes de quitarla
        self.panes = [self._new_pane(st) for st in states]
        for p in old:
            p.retire()
        self.active = self.panes[0]
        self._parked = []
        self.layout_kind = kind
        self._place_panes()
        self.layout_btn.setIcon(layout_icon(kind))
        self._layout_actions[kind].setChecked(True)
        self._sync_ui()
        self._load_all()
        self._save()
        self.statusBar().showMessage(f"Diseño «{name}» abierto.", 5000)

    def _set_sync(self, key: str, on: bool):
        self.sync[key] = on
        a, others = self.active, [p for p in self.panes if p is not self.active]
        if key == "symbol" and on and a.symbol:
            for p in others:
                if p.symbol != a.symbol:
                    self.load(a.symbol, raw=True, silent=True, pane=p)
        elif key == "interval" and on:
            for p in others:
                if p.interval != a.interval or p.period_key != a.period_key:
                    p.interval, p.period_key = a.interval, a.period_key
                    self.load(p.want, raw=True, silent=True, pane=p)
        elif key == "cursor" and not on:
            for p in others:
                p.chart.show_cursor_at(float("nan"))
        elif key == "time" and on:
            self._align_view(a, others)
        self._save()

    def _on_cursor(self, source: ChartPane, t: float):
        if self.sync["cursor"]:
            for p in self.panes:
                if p is not source:
                    p.chart.show_cursor_at(translate_time(t, source, p))

    def _on_view_changed(self, source: ChartPane):
        if self.sync["time"]:
            self._align_view(source, [p for p in self.panes if p is not source])

    @staticmethod
    def _align_view(source: ChartPane, targets: list[ChartPane]):
        times = source.chart.visible_times()
        if times:
            for p in targets:
                p.chart.show_times(*(translate_time(t, source, p) for t in times))

    def _fetch(self, sym, interval, period, on_done, on_fail):
        """Descarga velas. Las gráficas que piden lo mismo a la vez comparten una sola descarga."""
        key = (sym, interval, period)
        if key in self._inflight:
            self._inflight[key].append((on_done, on_fail))
            return
        self._inflight[key] = [(on_done, on_fail)]

        def finish(which, result, key=key):
            for callbacks in self._inflight.pop(key):
                callbacks[which](result)

        run_task(data.load, sym, interval, period,
                 on_done=lambda r: finish(0, r), on_fail=lambda e: finish(1, e))

    # ================================================================ UI
    def _build_actions(self):
        """Acciones compartidas por el menú y las barras: así siempre están sincronizadas."""
        def action(text, tip="", shortcut=None, checkable=False, slot=None, short=None):
            # text = cómo se lee en el menú · short = texto corto en los botones
            a = QtGui.QAction(text, self)
            if short:
                a.setIconText(short)
            a.setToolTip(tip or text)
            if shortcut:
                a.setShortcut(QtGui.QKeySequence(shortcut))
            a.setCheckable(checkable)
            if slot:
                a.triggered.connect(slot)
            return a

        self.log_scale = action("Escala logarítmica", "Escala logarítmica en la gráfica de precio (Alt+L)",
                                "Alt+L", True, short="Log")
        self.log_scale.setChecked(self.active.log)
        self.log_scale.toggled.connect(self._on_log)
        self.pct_scale = action("Escala porcentual", "Eje en % desde la primera vela visible (Alt+P)",
                                "Alt+P", True, short="%")
        self.pct_scale.setChecked(self.active.pct)
        self.pct_scale.toggled.connect(self._on_pct)
        self.invert_act = action("Invertir escala", "Voltear la gráfica de precio de cabeza: lo que sube se ve "
                                 "bajando (Alt+I)", "Alt+I", True, short="Invertir")
        self.invert_act.setChecked(self.active.invert)
        self.invert_act.toggled.connect(self._on_invert)
        self.auto = action("Actualización automática", "Actualizar la gráfica automáticamente",
                           checkable=True, short="Auto")
        self.auto.setChecked(bool(self.cfg.get("auto_refresh")))
        self.auto.toggled.connect(self._apply_auto_refresh)
        self.auto_every = QtWidgets.QComboBox()
        self.auto_every.setToolTip("Cada cuánto se actualiza la gráfica en modo Auto")
        for label, secs in REFRESH_OPTIONS:
            self.auto_every.addItem(label, secs)
        self.auto_every.setCurrentIndex(max(0, self.auto_every.findData(self.cfg.get("refresh_secs", 60))))
        self.auto_every.currentIndexChanged.connect(self._apply_auto_refresh)
        self.refresh_act = action("Actualizar datos", "Actualizar datos (F5)", "F5", short="⟳",
                                  slot=lambda: self._refresh_all())
        self.fit_act = action("Ver toda la historia", "Ver toda la historia cargada", short="Ver todo",
                              slot=lambda: self.chart.fit_all())
        self.recent_act = action("Ver últimas velas", "Ver las últimas velas (Inicio)", "Home", short="Recientes",
                                 slot=lambda: self.chart.show_last(self.chart.default_bars))
        self.drawbar_act = action("Herramientas de dibujo", "Mostrar u ocultar las herramientas de dibujo (Ctrl+D)",
                                  "Ctrl+D", True, short="✎")
        self.drawbar_act.setChecked(bool(self.cfg.get("drawbar_visible", True)))
        self.drawbar_act.toggled.connect(self._set_drawbar_visible)

    def _build_toolbar(self):
        tb = QtWidgets.QToolBar("Principal")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(16, 16))
        self.addToolBar(tb)
        self.main_tb = tb
        self.menu_btn = QtWidgets.QToolButton()
        self.menu_btn.setText("☰")
        self.menu_btn.setToolTip("Menú")
        self.menu_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        theme.style(self.menu_btn, "QToolButton::menu-indicator{image:none;}")
        tb.addWidget(self.menu_btn)
        tb.addAction(self.drawbar_act)
        tb.addSeparator()

        # un solo buscador para todos los mercados (sustituye al selector «Mercado»)
        self.sym_edit = SymbolSearch(self._known_symbols)
        self.sym_edit.symbolChosen.connect(lambda s, by_name: self.load(s, raw=True, by_name=by_name))
        tb.addWidget(self.sym_edit)
        tb.addSeparator()

        # intervalos más usados como botones; el resto en la lista desplegable
        self._interval_group = QtWidgets.QButtonGroup(self)
        self._interval_btns = {}
        for short, label in QUICK_INTERVALS:
            b = QtWidgets.QToolButton()
            b.setText(short)
            b.setToolTip(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, lab=label: self.interval.setCurrentText(lab))
            self._interval_group.addButton(b)
            self._interval_btns[label] = b
            tb.addWidget(b)
        self.interval = QtWidgets.QComboBox()
        self.interval.setToolTip("Todos los intervalos")
        self.interval.addItems(list(data.INTERVALS))
        self.interval.setCurrentText(self.active.interval)
        self.interval.currentTextChanged.connect(self._on_interval)
        tb.addWidget(self.interval)
        tb.addSeparator()

        self.ctype = QtWidgets.QComboBox()
        self.ctype.setToolTip("Tipo de gráfica")
        self.ctype.addItems(CHART_TYPES)
        self.ctype.setCurrentText(self.active.chart_type)
        self.ctype.currentTextChanged.connect(self._on_chart_type)
        tb.addWidget(self.ctype)
        self.ctype_cfg = QtWidgets.QToolButton()
        self.ctype_cfg.setText("⚙")
        self.ctype_cfg.clicked.connect(self._open_chart_params)
        tb.addWidget(self.ctype_cfg)
        self._update_chart_params_button()

        ind_btn = QtWidgets.QToolButton()
        ind_btn.setText("📈 Indicadores")
        ind_btn.setToolTip("Administrar indicadores (Ctrl+I)")
        ind_btn.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        ind_btn.clicked.connect(self.open_indicators)
        menu = QtWidgets.QMenu(ind_btn)
        menu.addAction("Administrar indicadores…", self.open_indicators)
        menu.addSeparator()
        for cat, defs in by_category().items():
            if not defs:
                continue
            sub = menu.addMenu(cat)
            for d in defs:
                sub.addAction(d.name, lambda k=d.key: self.add_indicator(k))
        menu.addSeparator()
        templates = menu.addMenu("Plantillas")
        templates.aboutToShow.connect(lambda: self._fill_templates(templates))
        menu.addAction("Quitar todos", self._clear_indicators)
        ind_btn.setMenu(menu)
        tb.addWidget(ind_btn)
        self.compare_btn = QtWidgets.QToolButton()
        self.compare_btn.setText("⇄ Comparar")
        self.compare_btn.setToolTip("Comparar con otros símbolos en % de cambio")
        self.compare_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._compare_menu = QtWidgets.QMenu(self.compare_btn)
        self._compare_menu.aboutToShow.connect(self._fill_compare_menu)
        self.compare_btn.setMenu(self._compare_menu)
        tb.addWidget(self.compare_btn)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addWidget(self._build_layout_menu())
        tb.addAction(self.refresh_act)
        self._sync_interval_buttons(self.interval.currentText())
        self._update_compare_button()

    def _build_rangebar(self):
        """Barra bajo la gráfica: cuánta historia ver (rango) y opciones de visualización."""
        w = QtWidgets.QWidget()
        w.setObjectName("rangebar")
        theme.style(w, 
            "#rangebar QToolButton{background:transparent;border:none;border-radius:4px;padding:3px 9px;color:#b2b5be;}"
            "#rangebar QToolButton:hover{background:#2a2e39;}"
            "#rangebar QToolButton:checked{background:#2a2e39;color:#fefefe;}"
            "#rangebar QToolButton:disabled{color:#4a4e59;}"
            "#rangebar QComboBox{padding:2px 6px;}")
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(40, 2, 4, 2)  # el margen izquierdo se ajusta en _set_drawbar_visible
        self._range_layout = h
        h.setSpacing(2)
        self._range_group = QtWidgets.QButtonGroup(self)
        self._range_btns = {}
        labels = {k: lab for k, lab, _ in data.PERIODS}
        for key in RANGE_ORDER:
            b = QtWidgets.QToolButton()
            b.setText(RANGE_LABELS[key])
            b.setToolTip(f"Ver {labels[key].lower()} de historia")
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self._on_range(k))
            self._range_group.addButton(b)
            self._range_btns[key] = b
            h.addWidget(b)
        h.addStretch(1)
        for act in (self.log_scale, self.pct_scale, self.invert_act, self.auto):
            b = QtWidgets.QToolButton()
            b.setDefaultAction(act)
            h.addWidget(b)
        h.addWidget(self.auto_every)
        h.addSpacing(10)
        for act in (self.fit_act, self.recent_act):
            b = QtWidgets.QToolButton()
            b.setDefaultAction(act)
            h.addWidget(b)
        self._set_period(self.period_key)
        self._sync_range_enabled(self.interval.currentText())
        return w

    def _build_menu(self):
        mb = QtWidgets.QMenu(self)  # sin barra de menú: esa altura es para la gráfica
        self.menu_btn.setMenu(mb)
        f = mb.addMenu("&Archivo")
        f.addAction("Buscar símbolo", lambda: (self.sym_edit.setFocus(), self.sym_edit.selectAll()),
                    QtGui.QKeySequence("Ctrl+L"))
        f.addAction("Búsqueda avanzada…", self.open_search, QtGui.QKeySequence("Ctrl+F"))
        f.addAction("Importar serie desde CSV… (p. ej. ISM PMI)", self.import_series)
        f.addSeparator()
        f.addAction("Guardar imagen de la gráfica…", self.save_image, QtGui.QKeySequence("Ctrl+S"))
        f.addSeparator()
        f.addAction("Salir", self.quit_app, QtGui.QKeySequence("Ctrl+Q"))

        v = mb.addMenu("&Ver")
        v.addAction(self.watch_dock.toggleViewAction())
        v.addAction(self.analysis_dock.toggleViewAction())
        v.addAction(self.drawbar_act)
        v.addMenu(self._layout_menu)
        v.addAction("Panel inferior", self.bottom.toggle, QtGui.QKeySequence("Ctrl+J"))
        v.addAction("Mapa de calor de la lista", self.bottom.show_heatmap, QtGui.QKeySequence("Ctrl+M"))
        v.addSeparator()
        v.addAction(self.log_scale)
        v.addAction(self.pct_scale)
        v.addAction(self.invert_act)
        v.addAction(self.fit_act)
        v.addAction(self.recent_act)
        v.addSeparator()
        v.addAction(self.refresh_act)
        v.addAction(self.auto)
        v.addSeparator()
        full = v.addAction("Pantalla completa", lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen(),
                           QtGui.QKeySequence("F11"))
        full.setToolTip("Pantalla completa (F11)")
        v.addAction("Restablecer paneles inferiores", lambda: self.chart.reset_panes())
        grid_menu = v.addMenu("Cuadrícula")
        grid_group = QtGui.QActionGroup(grid_menu)
        self._grid_actions = {}
        current = self.cfg.get("grid", "Media")
        for name, _alpha in GRID_LEVELS:
            act = grid_menu.addAction(name, lambda n=name: self._set_grid_level(n))
            act.setCheckable(True)
            act.setChecked(name == current)
            grid_group.addAction(act)
            self._grid_actions[name] = act
        grid_menu.addSeparator()
        grid_menu.addAction("Encender / apagar", self._toggle_grid, QtGui.QKeySequence("Ctrl+G"))
        theme_menu = v.addMenu("Tema")
        theme_group = QtGui.QActionGroup(theme_menu)
        self._theme_actions = {}
        for name in theme.THEMES:
            act = theme_menu.addAction(name, lambda n=name: self.set_theme(n))
            act.setCheckable(True)
            act.setChecked(name == theme.name)
            theme_group.addAction(act)
            self._theme_actions[name] = act
        theme_menu.addSeparator()
        theme_menu.addAction("Cambiar entre oscuro y claro", self._toggle_theme, QtGui.QKeySequence("Ctrl+T"))
        v.addAction("Colores de las velas…", self.edit_candle_colors)

        t = mb.addMenu("&Herramientas")
        t.addAction("Indicadores…", self.open_indicators, QtGui.QKeySequence("Ctrl+I"))
        t.addAction("Alertas…", self.open_alerts, QtGui.QKeySequence("Ctrl+Alt+A"))
        t.addSeparator()
        t.addAction("Clave API de FRED…", self.set_fred_key)

        a = mb.addMenu("A&yuda")
        a.addAction("Atajos de teclado", self.show_shortcuts, QtGui.QKeySequence("F1"))
        a.addAction("Acerca de ChartView", self.show_about)
        # un menú oculto no atiende atajos por sí solo: la ventana los toma
        for menu in mb.findChildren(QtWidgets.QMenu):
            for act in menu.actions():
                if not act.shortcut().isEmpty():
                    self.addAction(act)

    def _set_grid_level(self, name: str):
        for p in self.panes:
            p.chart.set_grid(dict(GRID_LEVELS)[name])
        self._grid_actions[name].setChecked(True)
        if name != "Sin cuadrícula":
            self.cfg["grid_last_on"] = name  # para que Ctrl+G vuelva al último nivel encendido
        self.cfg["grid"] = name
        self._save()

    def _toggle_grid(self):
        on = self.cfg.get("grid", "Media") != "Sin cuadrícula"
        self._set_grid_level("Sin cuadrícula" if on else self.cfg.get("grid_last_on", "Media"))

    def show_shortcuts(self):
        rows = "".join(f"<tr><td style='padding:3px 16px 3px 0'><b>{k}</b></td><td>{d}</td></tr>" for k, d in SHORTCUTS)
        QtWidgets.QMessageBox.information(self, "Atajos de teclado", f"<table>{rows}</table>")

    def show_about(self):
        QtWidgets.QMessageBox.about(
            self, "Acerca de ChartView",
            "<b>ChartView</b><br>Gráficas de mercado de escritorio en Python.<br><br>"
            "Datos: Yahoo Finance, Binance, Reserva Federal (DBnomics) y FRED.<br>"
            "<a href='https://github.com/Yayoo19/ChartView' style='color:#2962ff'>github.com/Yayoo19/ChartView</a>")

    def _build_center(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 0)
        lay.setSpacing(2)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.drawbar = self._build_drawbar()
        grid_host = QtWidgets.QWidget()
        self._grid_lay = QtWidgets.QVBoxLayout(grid_host)
        self._grid_lay.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.drawbar)
        row.addWidget(grid_host, 1)
        self._place_panes()
        lay.addLayout(row, 1)
        lay.addWidget(self._build_rangebar())
        self.setCentralWidget(w)
        self._set_drawbar_visible(self.drawbar_act.isChecked())

    def _set_drawbar_visible(self, on: bool):
        if not hasattr(self, "drawbar"):
            return
        if not on and self.chart.layer.tool != "cursor":
            self._tool_actions["cursor"].trigger()  # no seguir dibujando con la barra escondida
        self.drawbar.setVisible(on)
        # la barra de rangos se alinea con el borde izquierdo de la gráfica
        left = self.drawbar.sizeHint().width() + 4 if on else 4
        m = self._range_layout.contentsMargins()
        self._range_layout.setContentsMargins(left, m.top(), m.right(), m.bottom())
        self.cfg["drawbar_visible"] = on
        if hasattr(self, "timer"):  # durante el arranque todavía no hay nada que guardar
            self._save()

    def _build_drawbar(self):
        bar = QtWidgets.QToolBar("Dibujo")
        bar.setOrientation(QtCore.Qt.Vertical)
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        theme.style(bar, "QToolBar{background:#1e222d;border:1px solid #2a2e39;border-radius:4px;padding:2px;}"
                          "QToolButton{min-width:30px;min-height:26px;font-size:15px;border:none;padding:2px;}"
                          "QToolButton:checked{background:#2962ff;border-radius:4px;}"
                          "QToolButton:hover{background:#2a2e39;}"
                          "QToolButton[popupMode=\"1\"]{padding-right:9px;}"
                          "QToolButton::menu-button{border:none;width:9px;}"
                          "QToolButton::menu-arrow{width:6px;height:6px;}")
        group = QtGui.QActionGroup(bar)
        group.setExclusive(True)
        self._tool_actions = {}
        info = {k: (glyph, tip) for k, glyph, tip in TOOLS}
        for title, keys in TOOL_GROUPS:
            acts = []
            for key in keys:
                glyph, tip = info[key]
                name = tip.split(" (")[0].split(":")[0]  # «Línea horizontal (clic)» -> «Línea horizontal»
                act = QtGui.QAction(f"{glyph}    {name}", bar)  # así se lee en el menú del grupo
                act.setIconText(glyph)                          # y así en el botón
                act.setToolTip(tip)
                act.setCheckable(True)
                act.triggered.connect(lambda _c, k=key: self._on_tool(k))
                group.addAction(act)
                self._tool_actions[key] = act
                acts.append(act)
            if len(acts) == 1:
                bar.addAction(acts[0])
                continue
            btn = QtWidgets.QToolButton()
            btn.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
            btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            menu = QtWidgets.QMenu(title, btn)
            for act in acts:
                menu.addAction(act)
                # el botón del grupo recuerda la última herramienta elegida
                act.triggered.connect(lambda _c, b=btn, a=act: b.setDefaultAction(a))
            btn.setMenu(menu)
            btn.setDefaultAction(acts[0])
            bar.addWidget(btn)
        self._tool_actions["cursor"].setChecked(True)
        # imán: clic lo enciende/apaga; la flechita elige débil o fuerte
        self.magnet_btn = QtWidgets.QToolButton()
        self.magnet_btn.setText("🧲")
        self.magnet_btn.setCheckable(True)
        self.magnet_btn.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        self.magnet_btn.clicked.connect(self._toggle_magnet)
        mmenu = QtWidgets.QMenu("Imán", self.magnet_btn)
        mgroup = QtGui.QActionGroup(mmenu)
        self._magnet_actions = {}
        for mode, text in MAGNET_MODES.items():
            act = mmenu.addAction(text, lambda m=mode: self._set_magnet(m))
            act.setCheckable(True)
            mgroup.addAction(act)
            self._magnet_actions[mode] = act
        self.magnet_btn.setMenu(mmenu)
        bar.addWidget(self.magnet_btn)
        self._set_magnet(self.cfg.get("magnet", "off"), save=False)
        bar.addSeparator()
        self.color_btn = QtWidgets.QToolButton()
        self.color_btn.setText("■")
        self.color_btn.setToolTip("Color del próximo dibujo")
        self.color_btn.clicked.connect(self._pick_draw_color)
        bar.addWidget(self.color_btn)
        self._set_draw_color(self.cfg.get("draw_color", "#2962ff"))
        width_btn = QtWidgets.QToolButton()
        width_btn.setToolTip("Grosor del próximo dibujo")
        width_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(width_btn)
        for wv in (1, 2, 3, 4, 6):
            menu.addAction(f"{wv} px", lambda wv=wv: self._set_draw_width(wv, width_btn))
        width_btn.setMenu(menu)
        bar.addWidget(width_btn)
        self._set_draw_width(int(self.cfg.get("draw_width", 2)), width_btn)
        bar.addSeparator()
        undo = bar.addAction("↶")
        undo.setToolTip("Deshacer (Ctrl+Z)")
        undo.triggered.connect(lambda: self.chart.layer.undo())
        redo = bar.addAction("↷")
        redo.setToolTip("Rehacer (Ctrl+Y)")
        redo.triggered.connect(lambda: self.chart.layer.redo())
        self.eye_act = bar.addAction("👁")
        self.eye_act.setToolTip("Mostrar u ocultar dibujos")
        self.eye_act.setCheckable(True)
        self.eye_act.setChecked(True)
        self.eye_act.toggled.connect(lambda on: [p.chart.layer.set_visible(on) for p in self.panes])
        trash = bar.addAction("🗑")
        trash.setToolTip("Borrar todos los dibujos de este símbolo")
        trash.triggered.connect(self._clear_drawings)
        return bar

    def _set_magnet(self, mode: str, save: bool = True):
        if mode not in MAGNET_MODES:
            mode = "off"
        for p in self.panes:
            p.chart.layer.magnet = mode
        self.magnet_btn.setChecked(mode != "off")
        self._magnet_actions[mode].setChecked(True)
        self.magnet_btn.setToolTip(f"Imán: {MAGNET_MODES[mode]}\nClic para encender/apagar · flechita para elegir el modo")
        self.cfg["magnet"] = mode
        if mode != "off":
            self.cfg["magnet_last"] = mode
        if save:
            self._save()
            self.statusBar().showMessage(f"Imán: {MAGNET_MODES[mode]}", 4000)

    def _toggle_magnet(self):
        on = self.chart.layer.magnet != "off"
        self._set_magnet("off" if on else self.cfg.get("magnet_last", "weak"))

    def _on_tool(self, key):
        for p in self.panes:  # se dibuja en la gráfica donde se haga clic
            p.chart.layer.set_tool(key)
            if key == "cursor":
                p.chart.unsetCursor()
            else:
                p.chart.setCursor(QtCore.Qt.CrossCursor)
        if key != "cursor":
            tip = dict((k, t) for k, _, t in TOOLS)[key]
            self.statusBar().showMessage(f"{tip}  ·  Esc o clic derecho para cancelar", 6000)

    def _set_draw_color(self, color):
        for p in self.panes:
            p.chart.layer.color = color
        theme.style(self.color_btn, f"color:{color}; font-size:20px;")
        self.cfg["draw_color"] = color

    def _pick_draw_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.chart.layer.color), self, "Color de dibujo")
        if c.isValid():
            self._set_draw_color(c.name())
            self._save()

    def _set_draw_width(self, wv, btn):
        for p in self.panes:
            p.chart.layer.width = wv
        btn.setText(f"{wv}px")
        self.cfg["draw_width"] = wv

    def _save_drawings(self, pane: ChartPane):
        if pane.symbol:
            store = self.cfg.setdefault("drawings", {})
            store[pane.symbol] = pane.chart.layer.drawings
            for p in self.panes:  # los dibujos son del símbolo: se ven en todas sus gráficas
                if p is not pane and p.symbol == pane.symbol:
                    p.chart.layer.set_drawings(list(store[pane.symbol]))
            self._save()

    def _clear_drawings(self):
        if not self.chart.layer.drawings:
            return
        if QtWidgets.QMessageBox.question(self, "Borrar dibujos",
                                          f"¿Borrar todos los dibujos de {self.symbol}?") == QtWidgets.QMessageBox.Yes:
            self.chart.layer.clear()

    def _build_watchlist(self):
        dock = QtWidgets.QDockWidget("Listas de seguimiento", self)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable)
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        top = QtWidgets.QHBoxLayout()
        self.wl_combo = QtWidgets.QComboBox()
        self.wl_combo.setToolTip("Lista activa")
        self.wl_combo.currentTextChanged.connect(self._on_watchlist_changed)
        wl_btn = QtWidgets.QToolButton()
        wl_btn.setText("⋯")
        wl_btn.setToolTip("Administrar listas")
        wl_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        wl_menu = QtWidgets.QMenu(wl_btn)
        wl_menu.addAction("Nueva lista…", self.new_watchlist)
        wl_menu.addAction("Duplicar lista…", lambda: self.new_watchlist(copy=True))
        wl_menu.addAction("Renombrar…", self.rename_watchlist)
        wl_menu.addSeparator()
        wl_menu.addAction("Nueva sección…", self.new_section)
        wl_menu.addSeparator()
        wl_menu.addAction("Eliminar lista", self.delete_watchlist)
        wl_btn.setMenu(wl_menu)
        top.addWidget(self.wl_combo, 1)
        top.addWidget(wl_btn)
        lay.addLayout(top)
        self.watch = WatchTree()
        self.watch.setToolTip("Arrastra los símbolos para ordenarlos o pasarlos a otra sección.\n"
                              "Clic derecho para crear, renombrar o eliminar secciones.")
        self.watch.symbolMoved.connect(self._move_symbol)
        self.watch.sectionMoved.connect(self._move_section)
        self.watch.itemCollapsed.connect(lambda it: self._wl_collapsed.add((self.wl_combo.currentText(), it.text(0))))
        self.watch.itemExpanded.connect(lambda it: self._wl_collapsed.discard((self.wl_combo.currentText(), it.text(0))))
        self._wl_collapsed = set()  # secciones plegadas (se respetan al rehacer el árbol)
        self.watch.setHeaderLabels(["Símbolo", "Último", "Cambio"])
        self.watch.setRootIsDecorated(True)
        self.watch.setIndentation(12)
        hdr = self.watch.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.watch.setItemDelegateForColumn(0, WatchDelegate(self.watch))
        self.watch.itemActivated.connect(self._on_watch)
        self.watch.itemClicked.connect(self._on_watch)
        self.watch.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.watch.customContextMenuRequested.connect(self._watch_menu)
        lay.addWidget(self.watch, 1)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("+ Agregar actual")
        add.clicked.connect(lambda: self.add_to_watchlist())
        rem = QtWidgets.QPushButton("− Quitar")
        rem.clicked.connect(self.remove_from_watchlist)
        upd = QtWidgets.QPushButton("⟳")
        upd.setFixedWidth(30)
        upd.setToolTip("Actualizar precios")
        upd.clicked.connect(self.refresh_quotes)
        row.addWidget(add)
        row.addWidget(rem)
        row.addWidget(upd)
        lay.addLayout(row)
        dock.setWidget(w)
        dock.setMinimumWidth(300)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        dock.hide()  # la gráfica primero; si lo dejaste abierto, se restaura con la ventana
        self.watch_dock = dock
        dock.setObjectName("watchlist_dock")  # necesario para guardar/restaurar su estado
        # botón (y Ctrl+B) para volver a abrir el panel después de cerrarlo con la ✕
        toggle = dock.toggleViewAction()
        toggle.setText("Listas de seguimiento")
        toggle.setIconText("📋")
        toggle.setToolTip("Mostrar u ocultar las listas de seguimiento (Ctrl+B)")
        toggle.setShortcut(QtGui.QKeySequence("Ctrl+B"))
        self.addAction(toggle)  # el atajo funciona aunque el panel esté oculto
        self._fill_watchlist_combo(self.cfg.get("active_watchlist"))

    def _build_analysis_dock(self):
        """Panel derecho con reportes trimestrales, estimaciones y opinión de analistas."""
        dock = QtWidgets.QDockWidget("Análisis", self)
        dock.setObjectName("analysis_dock")  # para guardar/restaurar si quedó abierto
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable)
        self.analysis = AnalysisPanel()
        dock.setWidget(self.analysis)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        dock.hide()
        self.analysis_dock = dock
        self._fund_cache: dict = {}  # símbolo -> (hora, datos)
        toggle = dock.toggleViewAction()
        toggle.setText("Panel de análisis")
        toggle.setIconText("📊")
        toggle.setToolTip("Reportes trimestrales y opinión de analistas (Ctrl+E)")
        toggle.setShortcut(QtGui.QKeySequence("Ctrl+E"))
        self.addAction(toggle)
        dock.visibilityChanged.connect(lambda on: self._refresh_analysis() if on else None)

    def _build_rail(self):
        """Columna de iconos a la derecha: abre un panel a la vez, o ninguno."""
        rail = QtWidgets.QToolBar("Paneles")
        rail.setObjectName("right_rail")
        rail.setMovable(False)
        rail.setFloatable(False)
        rail.setContextMenuPolicy(QtCore.Qt.PreventContextMenu)
        rail.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        theme.style(rail, "QToolBar{background:#131722;border:none;border-left:1px solid #2a2e39;padding:4px 2px;}"
                           "QToolButton{min-width:26px;min-height:26px;font-size:15px;background:transparent;"
                           "border:none;border-radius:4px;padding:2px;}"
                           "QToolButton:hover{background:#2a2e39;}"
                           "QToolButton:checked{background:#2a2e39;border-left:2px solid #2962ff;}")
        self.addToolBar(QtCore.Qt.RightToolBarArea, rail)
        for dock in (self.watch_dock, self.analysis_dock):
            act = dock.toggleViewAction()
            rail.addAction(act)
            act.toggled.connect(lambda on, d=dock: self._on_panel(d, on))
        self.alert_act = rail.addAction("🔔")
        self.alert_act.triggered.connect(lambda: self.open_alerts())

    def _on_panel(self, dock, on: bool):
        if on:
            for other in (self.watch_dock, self.analysis_dock):
                if other is not dock:
                    other.hide()

    def _build_bottom(self):
        self._bottom_start = dict(self.cfg.get("bottom_panel") or {})  # guardar durante el arranque lo pisaría
        self.bottom = BottomPanel(self.cfg)
        self.bottom.set_alerts(self.cfg.get("alert_history", []))
        self.bottom.openSymbol.connect(lambda s: self.load(s, raw=True))
        self.bottom.notesChanged.connect(self._save)
        dock = QtWidgets.QDockWidget("Panel inferior", self)
        dock.setObjectName("bottom_dock")
        dock.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QtWidgets.QWidget())  # sin barra de título: solo las pestañas
        dock.setWidget(self.bottom)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dock)
        self.bottom_dock = dock
        self.bottom.toggled.connect(self._on_bottom_toggled)
        self._on_bottom_toggled(False)

    def _on_bottom_toggled(self, on: bool):
        dock = self.bottom_dock
        bar = self.bottom.bar.sizeHint().height()
        if on:
            dock.setMinimumHeight(bar + 90)
            dock.setMaximumHeight(16777215)
            height = int((self.cfg.get("bottom_panel") or {}).get("height", 220))
            # después de repartir el espacio: si no, Qt ignora la altura pedida
            QtCore.QTimer.singleShot(0, lambda: self.resizeDocks([dock], [height], QtCore.Qt.Vertical))
        else:
            dock.setFixedHeight(bar)
        if hasattr(self, "timer"):  # durante el arranque todavía no hay nada que guardar
            self._save()

    def _refresh_analysis(self):
        """Carga los datos del símbolo abierto (solo si el panel está visible; caché de 1 hora)."""
        sym = self.symbol
        self.bottom.set_symbol(sym)  # pasa cada vez que cambia el símbolo o la gráfica activa
        if not sym or not self.analysis_dock.isVisible():
            return
        name = data.short_name(self.meta.get("longName") or self.meta.get("shortName") or "")
        hit = self._fund_cache.get(sym)
        if hit and dt.datetime.now().timestamp() - hit[0] < 3600:
            self.analysis.set_data(sym, hit[1], name, self.last_price())
            return
        if self.analysis.symbol == sym and getattr(self, "_fund_loading", None) == sym:
            return  # ya se está descargando
        self._fund_loading = sym
        self.analysis.set_loading(sym)

        def done(d, sym=sym):
            self._fund_cache[sym] = (dt.datetime.now().timestamp(), d)
            self._fund_loading = None
            if sym == self.symbol:
                self.analysis.set_data(sym, d, name, self.last_price())

        def fail(err, sym=sym):
            self._fund_loading = None
            if sym == self.symbol:
                self.analysis.set_message(f"No se pudieron cargar los datos: {err}", sym)

        run_task(data.fundamentals, sym, on_done=done, on_fail=fail)

    def _on_escape(self):
        self._tool_actions["cursor"].trigger()
        for p in self.panes:
            p.chart.layer.clear_measure()

    def _build_shortcuts(self):
        # el resto de los atajos están en las acciones del menú (_build_menu)
        for seq, fn in [
            ("Esc", self._on_escape),
            ("Ctrl+Z", lambda: self.chart.layer.undo()),
            ("Ctrl+Y", lambda: self.chart.layer.redo()),
        ]:
            QtGui.QShortcut(QtGui.QKeySequence(seq), self, activated=fn)

    # ======================================================== watchlist
    def _wl(self) -> dict:
        """Grupos (mercado -> símbolos) de la lista activa."""
        return self.cfg["watchlists"].setdefault(self.wl_combo.currentText() or "Principal", {})

    def _fill_watchlist_combo(self, select=None):
        self.wl_combo.blockSignals(True)
        self.wl_combo.clear()
        self.wl_combo.addItems(list(self.cfg["watchlists"]))
        i = self.wl_combo.findText(select or "")
        self.wl_combo.setCurrentIndex(max(i, 0))
        self.wl_combo.blockSignals(False)
        self._on_watchlist_changed(self.wl_combo.currentText())

    def _on_watchlist_changed(self, name):
        if not name:
            return
        self.cfg["active_watchlist"] = name
        self._fill_watchlist()
        self._show_quotes({})
        if hasattr(self, "timer"):  # durante el arranque las cotizaciones se piden después
            self.refresh_quotes()
            self._save()

    def _ask_name(self, title, text="", taken=None, what="lista"):
        name, ok = QtWidgets.QInputDialog.getText(self, title, f"Nombre de la {what}:", text=text)
        name = name.strip()
        if not ok or not name or name == text:
            return None
        if name in (self.cfg["watchlists"] if taken is None else taken):
            QtWidgets.QMessageBox.warning(self, title, f"Ya existe una {what} llamada «{name}».")
            return None
        return name

    def new_watchlist(self, copy=False):
        name = self._ask_name("Duplicar lista" if copy else "Nueva lista")
        if name:
            self.cfg["watchlists"][name] = deepcopy(self._wl()) if copy else {}
            self._fill_watchlist_combo(name)

    def rename_watchlist(self):
        old = self.wl_combo.currentText()
        name = self._ask_name("Renombrar lista", old)
        if name:
            self.cfg["watchlists"] = {(name if k == old else k): v for k, v in self.cfg["watchlists"].items()}
            self._fill_watchlist_combo(name)

    def delete_watchlist(self):
        name = self.wl_combo.currentText()
        if len(self.cfg["watchlists"]) <= 1:
            QtWidgets.QMessageBox.information(self, "Eliminar lista", "Debe quedar al menos una lista.")
            return
        if QtWidgets.QMessageBox.question(self, "Eliminar lista", f"¿Eliminar la lista «{name}»?") \
                == QtWidgets.QMessageBox.Yes:
            del self.cfg["watchlists"][name]
            self._fill_watchlist_combo()

    # ---------------------------------------------------- secciones y orden manual
    def _set_wl(self, groups: dict):
        self.cfg["watchlists"][self.wl_combo.currentText()] = groups
        self._fill_watchlist()
        self._show_quotes({})
        self._save()

    def _selected_section(self):
        it = self.watch.currentItem()
        if it is None:
            return None
        return (it.parent() or it).text(0)

    def new_section(self, move_symbol=None):
        """Crea una sección con el nombre que quieras; si viene de «Mover a sección», pasa el símbolo ahí."""
        groups = self._wl()
        name = self._ask_name("Nueva sección", taken=groups, what="sección")
        if not name:
            return
        groups = dict(groups)
        groups[name] = []
        if move_symbol:
            sym, old = move_symbol
            if sym in groups.get(old, []):
                groups[old] = [s for s in groups[old] if s != sym]
                groups[name].append(sym)
        self._set_wl(groups)

    def rename_section(self, old):
        groups = self._wl()
        name = self._ask_name("Renombrar sección", old, taken=groups, what="sección")
        if name:
            self._set_wl({(name if k == old else k): v for k, v in groups.items()})

    def delete_section(self, name):
        syms = self._wl().get(name, [])
        if syms and QtWidgets.QMessageBox.question(
                self, "Eliminar sección",
                f"¿Eliminar la sección «{name}» y sus {len(syms)} símbolo(s) de esta lista?") != QtWidgets.QMessageBox.Yes:
            return
        self._set_wl({k: v for k, v in self._wl().items() if k != name})

    def _move_to_section(self, sym, old, new):
        groups = {k: list(v) for k, v in self._wl().items()}
        if sym in groups.get(old, []) and new in groups:
            groups[old].remove(sym)
            groups[new].append(sym)
            self._set_wl(groups)

    def _sort_section(self, name):
        groups = {k: list(v) for k, v in self._wl().items()}
        groups[name].sort(key=lambda s: s.lstrip("^").upper())
        self._set_wl(groups)

    def _move_symbol(self, ss, sr, ds, dr):
        """Arrastre de un símbolo: de (sección ss, fila sr) a (sección ds, antes de la fila dr)."""
        groups = {k: list(v) for k, v in self._wl().items()}
        names = list(groups)
        try:
            src, dst = groups[names[ss]], groups[names[ds]]
            sym = src.pop(sr)
        except IndexError:
            return
        if src is dst and dr > sr:
            dr -= 1
        if sym in dst:  # ya estaba en la sección destino: no se duplica
            src.insert(sr, sym)
            return
        dst.insert(dr, sym)
        self._set_wl(groups)
        self._select_symbol(sym, names[ds])

    def _move_section(self, si, new):
        items = list(self._wl().items())
        if not 0 <= si < len(items):
            return
        moved = items.pop(si)
        items.insert(new - 1 if new > si else new, moved)
        self._set_wl(dict(items))

    def _select_symbol(self, sym, section):
        for it in self._watch_items.get(sym, []):
            if it.parent() and it.parent().text(0) == section:
                self.watch.setCurrentItem(it)

    def _fill_watchlist(self):
        self.watch.clear()
        self._watch_items = {}
        lname = self.wl_combo.currentText()
        for group, syms in self._wl().items():
            top = QtWidgets.QTreeWidgetItem([group])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setToolTip(0, "Arrastra la sección para cambiarla de lugar; clic derecho para renombrarla")
            for s in syms:
                it = QtWidgets.QTreeWidgetItem([s, "", ""])
                it.setData(0, QtCore.Qt.UserRole, s)
                it.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                it.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                top.addChild(it)
                self._watch_items.setdefault(s, []).append(it)
            self.watch.addTopLevelItem(top)
            top.setExpanded((lname, group) not in self._wl_collapsed)
        self._apply_names()
        self._fetch_names()

    def _apply_names(self):
        names = self.cfg.setdefault("names", {})
        for s, items in self._watch_items.items():
            name = names.get(s, "")
            for it in items:
                it.setData(0, NAME_ROLE, name)
                it.setToolTip(0, f"{s} — {name}" if name else s)

    def _fetch_names(self):
        """Busca en segundo plano los nombres que faltan; quedan en caché (cfg["names"])."""
        names = self.cfg.setdefault("names", {})
        missing = [s for s in self._watch_items if s not in names]
        if not missing or getattr(self, "_names_busy", False):
            return
        self._names_busy = True

        def done(found, missing=missing):
            self._names_busy = False
            for s in missing:
                names[s] = found.get(s, "")  # vacío = no se encontró; no se vuelve a buscar
            self._apply_names()
            self._show_quotes({})  # la tabla de movimientos también muestra los nombres
            self._save()
            self._fetch_names()  # por si mientras tanto se agregaron símbolos

        run_task(data.lookup_names, missing, on_done=done,
                 on_fail=lambda e: setattr(self, "_names_busy", False))

    def _remember_name(self, sym: str, meta: dict):
        """Al abrir un símbolo, Yahoo da su nombre: se guarda para la lista."""
        name = data.short_name(meta.get("longName") or meta.get("shortName") or "")
        names = self.cfg.setdefault("names", {})
        if name and not names.get(sym):
            names[sym] = name
            self._apply_names()

    def _on_watch(self, item, _col=0):
        s = item.data(0, QtCore.Qt.UserRole)
        if s and s != self.symbol:
            self.load(s, raw=True)

    def _watch_menu(self, pos):
        it = self.watch.itemAt(pos)
        m = QtWidgets.QMenu(self)
        if it is None or it.parent() is None:  # espacio vacío o título de sección
            if it is not None:
                sec = it.text(0)
                m.addAction("Renombrar sección…", lambda: self.rename_section(sec))
                m.addAction("Ordenar A→Z", lambda: self._sort_section(sec))
                m.addAction("Eliminar sección", lambda: self.delete_section(sec))
                m.addSeparator()
            m.addAction("Nueva sección…", self.new_section)
            m.exec(self.watch.viewport().mapToGlobal(pos))
            return
        s = it.data(0, QtCore.Qt.UserRole)
        sec = it.parent().text(0)
        m.addAction("Abrir", lambda: self._on_watch(it))
        mv = m.addMenu("Mover a sección")
        for n in self._wl():
            if n != sec:
                mv.addAction(n, lambda n=n: self._move_to_section(s, sec, n))
        if not mv.isEmpty():
            mv.addSeparator()
        mv.addAction("Nueva sección…", lambda: self.new_section(move_symbol=(s, sec)))
        last = getattr(self, "_quotes", {}).get(s, (0.0,))[0] or 0.0
        m.addAction("🔔 Crear alerta…", lambda: self.open_alerts(alerts.new_alert(s, "price_cross_up", last)))
        others = [n for n in self.cfg["watchlists"] if n != self.wl_combo.currentText()]
        if others:
            sub = m.addMenu("Copiar a lista")
            for n in others:
                sub.addAction(n, lambda n=n: self.add_to_watchlist(s, n))
        m.addAction("Quitar de la lista", lambda: self._remove_item(it))
        m.exec(self.watch.viewport().mapToGlobal(pos))

    def add_to_watchlist(self, symbol=None, list_name=None):
        symbol = symbol or self.symbol
        if not symbol:
            return
        meta = self.meta if symbol == self.symbol else None
        target = list_name or self.wl_combo.currentText()
        groups = self.cfg["watchlists"].setdefault(target, {})
        where = next((g for g, syms in groups.items() if symbol in syms), None)
        if where:
            self.statusBar().showMessage(f"{symbol} ya está en «{target}», sección «{where}»", 5000)
            return
        # en la lista activa va a la sección seleccionada; si no hay ninguna, a la de su mercado
        section = self._selected_section() if list_name in (None, self.wl_combo.currentText()) else None
        groups.setdefault(section or data.guess_market(symbol, meta), []).append(symbol)
        self._save()
        if list_name is None or list_name == self.wl_combo.currentText():
            self._fill_watchlist()
            self.refresh_quotes()
        else:
            self.statusBar().showMessage(f"{symbol} agregado a «{list_name}»", 5000)

    def _remove_item(self, it):
        s = it.data(0, QtCore.Qt.UserRole)
        group = it.parent().text(0) if it.parent() else None
        if group and s in self._wl().get(group, []):
            self._wl()[group].remove(s)
            self._fill_watchlist()
            self._save()
            self._show_quotes({})

    def remove_from_watchlist(self):
        it = self.watch.currentItem()
        if it and it.data(0, QtCore.Qt.UserRole):
            self._remove_item(it)

    def refresh_quotes(self):
        if getattr(self, "_quotes_busy", False):  # con Auto cada 15 s no se encimen las descargas
            return
        syms = sorted({s for lst in self._wl().values() for s in lst})
        if not syms:
            return
        self._quotes_busy = True

        def done(q):
            self._quotes_busy = False
            self._show_quotes(q)

        run_task(data.quotes, syms, on_done=done, on_fail=lambda e: setattr(self, "_quotes_busy", False))

    def _show_quotes(self, q):
        """Pinta precios; se guardan en caché para mostrarlos al cambiar de lista."""
        self._quotes = {**getattr(self, "_quotes", {}), **q}
        q = self._quotes
        for s, items in getattr(self, "_watch_items", {}).items():
            if s not in q:
                continue
            last, chg = q[s]
            for it in items:
                it.setText(1, fmt_price(last))
                if np.isfinite(chg):
                    it.setText(2, f"{chg:+.2f}%")
                    it.setForeground(2, QtGui.QColor(UP if chg >= 0 else DOWN))
        names = self.cfg.get("names", {})
        ok = lambda s: s in q and np.isfinite(q[s][0]) and np.isfinite(q[s][1])  # noqa: E731
        self.bottom.heatmap.set_groups(self.wl_combo.currentText(), [
            (section, [(s, names.get(s, ""), q[s][0], q[s][1]) for s in dict.fromkeys(syms) if ok(s)])
            for section, syms in self._wl().items()])
        self.bottom.set_movers([(s, names.get(s, ""), q[s][0], q[s][1]) for s in getattr(self, "_watch_items", {})
                                if s in q and np.isfinite(q[s][0]) and np.isfinite(q[s][1])])

    def _known_symbols(self) -> dict[str, str]:
        """Sugerencias locales del buscador: {símbolo: descripción}."""
        out = {}
        for lst in self.cfg.get("watchlists", {}).values():
            for group, syms in lst.items():
                for s in syms:
                    out.setdefault(s, group)
        for group, syms in data.POPULAR.items():
            for s in syms:
                out.setdefault(s, group)
        for key, (_path, name, *_rest) in macro.CATALOG.items():
            out[f"MACRO:{key}"] = name
        out.update(data.COMMODITIES)  # «cobre», «oro», «petróleo»… también en español
        for s in macro.imported():
            out[s] = "Serie importada (CSV)"
        return out

    # ======================================================== carga
    def _set_period(self, key, pane=None):
        pane = pane or self.active
        pane.period_key = key
        if pane is self.active and getattr(self, "_range_btns", None):
            self._range_group.setExclusive(False)
            for k, b in self._range_btns.items():
                b.setChecked(k == key)
            self._range_group.setExclusive(True)

    def _on_range(self, key):
        self._set_period(key)
        self.load(self.active.want, raw=True)
        if self.sync["interval"] or self.sync["time"]:  # sin la misma historia no podrían verse iguales
            for p in self.panes:
                if p is not self.active:
                    self._set_period(key, p)
                    self.load(p.want, raw=True, silent=True, pane=p)

    def _sync_interval_buttons(self, label):
        self._interval_group.setExclusive(False)
        for lab, b in self._interval_btns.items():
            b.setChecked(lab == label)
        self._interval_group.setExclusive(True)

    def _sync_range_enabled(self, label):
        """Desactiva los rangos que Yahoo no da para intervalos intradía (p. ej. 1 minuto = 7 días)."""
        yi = data.INTERVALS[label][0]
        for k, b in getattr(self, "_range_btns", {}).items():
            b.setEnabled(data.clamp_period(yi, k) == k)

    def _on_interval(self, label):
        if self._syncing_ui:
            return
        self._sync_interval_buttons(label)
        self._sync_range_enabled(label)
        self.active.interval = label
        self._set_period(data.INTERVALS[label][3])
        self.load(self.active.want, raw=True)
        if self.sync["interval"]:
            for p in self.panes:
                if p is not self.active:
                    p.interval = label
                    self._set_period(data.INTERVALS[label][3], p)
                    self.load(p.want, raw=True, silent=True, pane=p)

    def _on_chart_type(self, chart_type):
        if self._syncing_ui:
            return
        # las barras de Renko, Kagi… no corresponden a las velas: la vista anterior no sirve
        same_bars = chart_type not in TRANSFORMED and self.active.chart_type not in TRANSFORMED
        self.active.chart_type = chart_type
        self._update_chart_params_button()
        self.redraw(keep_view=same_bars)
        self._save()

    def _update_chart_params_button(self):
        specs = PARAM_SPECS.get(self.active.chart_type)
        self.ctype_cfg.setEnabled(bool(specs))
        self.ctype_cfg.setToolTip(f"Ajustes de {self.active.chart_type}" if specs else "Este tipo de gráfica no tiene ajustes")

    def _open_chart_params(self):
        a = self.active
        specs = PARAM_SPECS.get(a.chart_type)
        if not specs:
            return
        dlg = ChartTypeDialog(a.chart_type, specs, a.chart_params.get(a.chart_type, {}), self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            a.chart_params[a.chart_type] = dlg.values()
            self.redraw()
            self._save()

    def _set_scale(self, log: bool, pct: bool):
        """Log y % se excluyen entre sí."""
        a = self.active
        a.log, a.pct = log, pct
        self._syncing_ui = True
        try:
            self.log_scale.setChecked(log)
            self.pct_scale.setChecked(pct)
        finally:
            self._syncing_ui = False
        self.redraw(keep_view=True)
        self._save()

    def _on_log(self, on):
        if not self._syncing_ui:
            self._set_scale(on, False if on else self.active.pct)

    def _on_pct(self, on):
        if not self._syncing_ui:
            self._set_scale(False if on else self.active.log, on)

    def _on_invert(self, on):
        if not self._syncing_ui:
            self.active.invert = on
            self.redraw(keep_view=True)
            self._save()

    def load(self, text, raw=False, keep_view=False, silent=False, by_name=False, pane=None):
        """Carga un símbolo en una gráfica (por omisión, la activa).
        ``by_name``: si no existe tal cual, se busca por nombre y se abre el primero."""
        pane = pane or self.active
        sym = " ".join(text.upper().split())  # «aapl / ^gspc» -> «AAPL / ^GSPC»
        if not sym:
            return
        same = sym == pane.symbol
        pane.want = sym  # cambiar de intervalo o refrescar mientras carga no debe volver al símbolo anterior
        if pane is self.active:
            self.sym_edit.setText(sym)
        interval = pane.interval
        period = pane.period_key
        pane._req += 1
        req = pane._req
        if not silent:
            self.statusBar().showMessage(f"Descargando {sym} ({interval})…")
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.BusyCursor)
        keep = keep_view and same

        def done(res, req=req, sym=sym, keep=keep, silent=silent):
            if not silent:
                QtWidgets.QApplication.restoreOverrideCursor()
            if req != pane._req:
                return
            df, meta, used = res
            changed = sym != pane.symbol
            pane.df, pane.meta, pane.symbol = df, meta, sym
            self._remember_name(sym, meta)
            if pane is self.active and (changed or not silent):
                self._refresh_analysis()
            if not silent and sym in getattr(self, "_watch_items", {}):
                # el renglón de la lista muestra el mismo precio que la gráfica recién cargada
                run_task(data.quotes, [sym], on_done=self._show_quotes)
            if used != period:
                self._set_period(used, pane)
                if pane is self.active:
                    self.statusBar().showMessage(
                        f"Yahoo limita la historia para {interval}; se usó el periodo «{used}».", 8000)
            if pane.comparisons:
                self._load_comparisons(keep, pane)  # dibuja cuando lleguen, sin parpadeo
            else:
                pane._comp_closes = {}
                self.redraw(keep_view=keep, pane=pane)
            if changed and pane is self.active and self.sync["symbol"]:
                # solo cuando el símbolo ya cargó: uno equivocado no se lleva a las demás gráficas
                for p in self.panes:
                    if p is not pane and p.symbol != sym:
                        self.load(sym, raw=True, silent=True, pane=p)
            self._save()

        def fail(err, req=req, sym=sym, silent=silent):
            if not silent:
                QtWidgets.QApplication.restoreOverrideCursor()
            if req != pane._req:
                return
            pane.want = pane.symbol or sym  # se queda en lo último que sí cargó
            if by_name:  # «walmex», «bitcoin»…: se busca por nombre y se abre el mejor resultado
                def found(rows, req=req):
                    if req == pane._req and rows:
                        self.load(rows[0]["symbol"], raw=True, pane=pane)
                    elif req == pane._req:
                        QtWidgets.QMessageBox.warning(self, "No se encontró", f"No encontré nada para «{sym}».")
                        if pane is self.active:
                            self.sym_edit.setText(pane.symbol)
                self.statusBar().showMessage(f"Buscando «{sym}» por nombre…")
                run_task(data.search, sym, on_done=found, on_fail=lambda e: found([]))
                return
            self.statusBar().showMessage(f"Error al cargar {sym}: {err}", 15000)
            if pane.df is None:  # sin esto, la gráfica quedaría vacía sin decir por qué
                pane.header.setText(theme.css(f"<b style='color:#fefefe'>{sym}</b>&nbsp;&nbsp;"
                                                  f"<span style='color:{DOWN}'>no se pudo cargar</span>"))
            if not silent:
                QtWidgets.QMessageBox.warning(self, "No se pudo cargar", f"{sym}\n\n{err}")
            if pane is self.active:
                self.sym_edit.setText(pane.symbol or sym)

        self._fetch(sym, interval, period, done, fail)

    # ======================================================== comparar
    def _fill_compare_menu(self):
        m = self._compare_menu
        m.clear()
        m.addAction("➕  Agregar símbolo para comparar…", self.add_comparison)
        if self.comparisons:
            m.addSeparator()
            for s in self.comparisons:
                m.addAction(f"✕  Quitar {s}", lambda s=s: self.remove_comparison(s))
            m.addSeparator()
            m.addAction("Quitar todas las comparaciones", self.clear_comparisons)
        m.addSeparator()
        hint = m.addAction("Ratio o diferencial: escribe AAPL/^GSPC o A - B en el buscador")
        hint.setEnabled(False)

    def _update_compare_button(self):
        n = len(self.comparisons)
        self.compare_btn.setText(f"⇄ Comparar ({n})" if n else "⇄ Comparar")

    def add_comparison(self, sym: str | None = None):
        if not sym:
            dlg = CompareDialog(self._known_symbols, self)
            if dlg.exec() != QtWidgets.QDialog.Accepted or not dlg.symbol:
                return
            sym = dlg.symbol
        sym = " ".join(sym.upper().split())
        if sym == self.symbol or sym in self.comparisons:
            self.statusBar().showMessage(f"{sym} ya está en la gráfica.", 5000)
            return
        self.comparisons.append(sym)
        self._update_compare_button()
        self._save()
        if self.df is not None:
            self._load_comparisons(keep=False)

    def remove_comparison(self, sym: str):
        if sym in self.comparisons:
            self.comparisons.remove(sym)
            self.active._comp_closes.pop(sym, None)
            self._update_compare_button()
            self._save()
            self.redraw(keep_view=bool(self.comparisons))

    def clear_comparisons(self):
        self.comparisons.clear()
        self.active._comp_closes = {}
        self._update_compare_button()
        self._save()
        self.redraw()

    def _load_comparisons(self, keep=False, pane=None):
        """Descarga los símbolos a comparar con el mismo intervalo y periodo, y luego dibuja todo junto."""
        pane = pane or self.active
        syms = [s for s in pane.comparisons if s != pane.symbol]
        req, interval, period = pane._req, pane.interval, pane.period_key

        def done(res, req=req):
            if req != pane._req:
                return
            pane._comp_closes = {s: v for s, v in res.items() if not isinstance(v, str)}
            self.redraw(keep_view=keep, pane=pane)
            errs = [f"{s} ({v})" for s, v in res.items() if isinstance(v, str)]
            if errs:
                self.statusBar().showMessage("No se pudo comparar con " + "; ".join(errs), 12000)

        if not syms:
            done({})
            return
        self.statusBar().showMessage(f"Descargando {', '.join(syms)} para comparar…")
        run_task(data.load_closes, syms, pane.df.index, interval, period, on_done=done,
                 on_fail=lambda e, req=req: done({}) if req == pane._req else None)

    def redraw(self, keep_view=False, pane=None):
        pane = pane or self.active
        if pane.df is None:
            return
        df = source = pane.df
        chart_type, opts, note = pane.chart_type, {}, ""
        if chart_type in TRANSFORMED:
            try:
                df, opts = transform(source, chart_type, pane.chart_params.get(chart_type))
                note = f"{chart_type}: {opts['text']}"
                if df.empty:
                    raise ValueError("el precio no se movió lo suficiente para formar barras con ese tamaño")
            except ValueError as e:
                df, chart_type, opts, note = source, "Velas", {}, f"{chart_type}: {e}; se muestran velas"
        transformed = df is not source
        computed, errors = [], []
        for i, a in enumerate(pane.indicators):
            try:
                c = compute(a["key"], a.get("params"), df, a.get("color"))
                c.index, c.hidden = i, bool(a.get("hidden"))
                computed.append(c)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{REGISTRY[a['key']].abbr}: {e}")
        interval = pane.interval
        intraday = data.INTERVALS[interval][2]
        chart = pane.chart
        chart.layer.bind(pane.symbol, list(self.cfg.setdefault("drawings", {}).get(pane.symbol, [])))
        if not transformed and chart_type in CANDLE_TYPES and (df["High"] == df["Low"]).all():
            chart_type = "Línea"  # series de un solo valor por fecha (M2, tasas sin agrupar…)
        comps = [(s, pane._comp_closes[s], COMPARE_COLORS[k % len(COMPARE_COLORS)])
                 for k, s in enumerate(pane.comparisons)
                 if s in pane._comp_closes and len(pane._comp_closes[s]) == len(df)]
        if transformed:
            comps = []  # los cierres están alineados a las velas, no a estas barras
            if pane.comparisons:
                note += " · Comparar no aplica en este tipo de gráfica"
        chart.set_data(df, computed, chart_type, intraday=intraday,
                       keep_view=keep_view, title=pane.symbol, log=pane.log, comparisons=comps,
                       pct_scale=pane.pct, last_price=float(source["Close"].iloc[-1]) if transformed else None,
                       opts=opts, inverted=pane.invert)
        self._update_alert_lines(pane)
        try:  # una barra de Renko o Kagi no cierra a una hora: se cierra cuando el precio la completa
            chart.set_countdown(None if transformed else data.candle_end(df, pane.meta, interval))
        except Exception:  # noqa: BLE001 - sin cuenta regresiva si Yahoo no da el horario
            chart.set_countdown(None)
        self._update_header(intraday, pane)
        if self.sync["time"]:  # recargar no debe desalinear las gráficas
            if pane is self.active:
                self._align_view(pane, [p for p in self.panes if p is not pane])
            else:
                self._align_view(self.active, [pane])
        if pane is self.active:
            msg = f"{pane.symbol} · {len(df)} {'barras' if transformed else 'velas'} · {interval}"
            msg += f" · {note}" if note else ""
            msg += f" · actualizado {dt.datetime.now():%H:%M:%S}"
            if errors:
                msg += "  ·  Errores: " + "; ".join(errors)
            self.statusBar().showMessage(msg)

    def _update_header(self, intraday, pane):
        m, df = pane.meta, pane.df
        name = m.get("longName") or m.get("shortName") or ""
        cur = m.get("currency", "")
        exch = m.get("fullExchangeName") or m.get("exchangeName") or ""
        last = float(df["Close"].iloc[-1])
        prev = m.get("previousClose") if intraday else None
        if not prev:
            prev = float(df["Close"].iloc[-2]) if len(df) > 1 else last
        if intraday and m.get("regularMarketPrice"):
            last_q = float(m["regularMarketPrice"])
            if abs(last_q / last - 1) < 0.2:
                last = last_q
        chg = last - prev
        pct = chg / prev * 100 if prev else 0
        col = UP if chg >= 0 else DOWN
        if pane.compact:  # varias gráficas: poco ancho, y cada una puede tener otro intervalo
            middle = f"<span style='color:#787b86'>{pane.interval}</span>&nbsp;&nbsp;"
            price = f"<b style='color:#fefefe'>{fmt_price(last)}</b>&nbsp;"
        else:
            middle = (f"<span style='color:#d1d4dc'>{name}</span>&nbsp;&nbsp;"
                      f"<span style='color:#787b86'>{' · '.join(x for x in (exch, cur) if x)}</span>&nbsp;&nbsp;&nbsp;")
            price = f"<b style='color:#fefefe; font-size:17px'>{fmt_price(last)}</b>&nbsp;"
        pane.header.setText(theme.css(f"<b style='color:#fefefe'>{pane.symbol}</b>&nbsp;&nbsp;{middle}{price}"
                                      f"<span style='color:{col}'>{fmt_change(chg)} ({pct:+.2f}%)</span>"))
        if pane is self.active:
            self.setWindowTitle(f"{pane.symbol} {fmt_price(last)} — ChartView")

    # ======================================================== indicadores
    def open_indicators(self):
        dlg = IndicatorDialog(self.indicators, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.indicators = dlg.active
            self.redraw(keep_view=True)
            self._save()

    def _legend_action(self, action: str, index: int):
        """Clic en ⚙ / 👁 / ✕ (o su menú) en la leyenda de la gráfica."""
        if not 0 <= index < len(self.indicators):
            return
        a = self.indicators[index]
        if action == "cfg":
            dlg = IndicatorEditDialog(a, self)
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            self.indicators[index] = dlg.instance
        elif action == "hide":
            a["hidden"] = not a.get("hidden")
        elif action == "del":
            del self.indicators[index]
        self.redraw(keep_view=True)
        self._save()

    # ---------------------------------------------------- plantillas
    def _fill_templates(self, menu):
        menu.clear()
        store = self.cfg.get("templates", {})
        menu.addAction("Guardar los indicadores como plantilla…", self.save_template)
        if store:
            menu.addSeparator()
            for name, item in store.items():
                act = menu.addAction(name, lambda n=name: self.apply_template(n))
                act.setToolTip(", ".join(REGISTRY[a["key"]].label({**REGISTRY[a["key"]].defaults(), **(a.get("params") or {})})
                                         for a in item.get("indicators", []) if a.get("key") in REGISTRY) or "Sin indicadores")
            menu.addSeparator()
            sub = menu.addMenu("Eliminar")
            for name in store:
                sub.addAction(name, lambda n=name: self._delete_saved("templates", n, "la plantilla"))
        menu.setToolTipsVisible(True)

    def save_template(self):
        """Guarda con nombre los indicadores de la gráfica activa, con sus parámetros y colores."""
        store = self.cfg.setdefault("templates", {})
        name = self._ask_save_name("Guardar plantilla", store, "la plantilla")
        if name:
            store[name] = {"indicators": deepcopy(self.indicators)}
            self._save()
            self.statusBar().showMessage(f"Plantilla «{name}» guardada con {len(self.indicators)} indicador(es).", 5000)

    def apply_template(self, name: str):
        item = self.cfg.get("templates", {}).get(name)
        if item is None:
            return
        self.indicators = [a for a in deepcopy(item.get("indicators", [])) if a.get("key") in REGISTRY]
        self.redraw(keep_view=True)
        self._save()
        self.statusBar().showMessage(f"Plantilla «{name}» aplicada a {self.symbol}.", 5000)

    # ---------------------------------------------------- tema y colores
    def set_theme(self, name: str):
        self.cfg["theme"] = name
        self._retheme()

    def _toggle_theme(self):
        self.set_theme("Oscuro" if theme.is_light() else "Claro")

    def _retheme(self):
        """Vuelve a pintar todo con el tema y los colores de velas de la configuración."""
        theme.configure(self.cfg)
        theme.apply(STYLE)
        self._theme_actions[theme.name].setChecked(True)
        self.layout_btn.setIcon(layout_icon(self.layout_kind))
        for kind, act in self._layout_actions.items():
            act.setIcon(layout_icon(kind))
        for p in self.panes:
            p.chart.apply_theme()
            self.redraw(keep_view=True, pane=p)
        self.bottom.restyle()
        if self.analysis_dock.isVisible():
            self.analysis.symbol = None  # el panel se arma de nuevo con los colores del tema
            self._refresh_analysis()
        self._save()

    def edit_candle_colors(self):
        dlg = CandleColorsDialog(theme.up(), theme.down(), self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.cfg["candle_colors"] = dlg.colors()
            self._retheme()

    def add_indicator(self, key):
        self.indicators.append({"key": key, "params": {}, "color": None})
        self.redraw(keep_view=True)
        self._save()

    def _clear_indicators(self):
        self.indicators = []
        self.redraw(keep_view=True)
        self._save()

    # ======================================================== varios
    def open_search(self):
        dlg = SearchDialog(self.sym_edit.text() if self.sym_edit.text() != self.symbol else "", self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.symbol:
            self.load(dlg.symbol, raw=True)

    def import_series(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Importar serie (CSV con fecha y valor)", "", "CSV (*.csv *.txt);;Todos (*)")
        if not path:
            return
        default = macro.clean_name(QtCore.QFileInfo(path).completeBaseName()) or "MI_SERIE"
        name, ok = QtWidgets.QInputDialog.getText(self, "Importar serie", "Nombre de la serie:", text=default)
        if not ok:
            return
        try:
            sym = macro.import_csv(path, name)
        except Exception as e:  # noqa: BLE001 - se muestra al usuario
            QtWidgets.QMessageBox.warning(self, "No se pudo importar", str(e))
            return
        self.statusBar().showMessage(f"Serie importada como {sym}. Para actualizarla, vuelve a importar con el mismo nombre.", 8000)
        self.load(sym, raw=True)

    def set_fred_key(self):
        key, ok = QtWidgets.QInputDialog.getText(
            self, "Clave API de FRED",
            "Con una clave gratuita de FRED puedes abrir cualquiera de sus series\n"
            "(p. ej. M2SL, WALCL, DGS2, DGS10) en el mercado «Macro EE.UU.».\n"
            "Pídela en https://fredaccount.stlouisfed.org/apikeys\n\n"
            "Clave (vacío para quitarla):",
            QtWidgets.QLineEdit.Password, self.cfg.get("fred_api_key", ""))
        if ok:
            self.cfg["fred_api_key"] = key.strip()
            macro.FRED_API_KEY = key.strip()
            self._save()
            self.statusBar().showMessage("Clave de FRED guardada." if key.strip() else "Clave de FRED eliminada.", 5000)

    def save_image(self):
        name = f"{self.symbol or 'grafica'}_{dt.datetime.now():%Y%m%d_%H%M}.png".replace("^", "").replace("=", "")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Guardar imagen", name, "PNG (*.png)")
        if path:
            self.centralWidget().grab().save(path)
            self.statusBar().showMessage(f"Imagen guardada en {path}", 6000)

    def _apply_auto_refresh(self, *_):
        # la gráfica y los precios de la lista se actualizan al ritmo elegido en Auto;
        # sin Auto, la lista se refresca cada 5 minutos
        if self.auto.isChecked():
            secs = int(self.auto_every.currentData())
            self.timer.start(secs * 1000)
            self.quote_timer.start(secs * 1000)
            self.refresh_quotes()
        else:
            self.timer.stop()
            self.quote_timer.start(5 * 60 * 1000)
        self._save()

    def _remember_sizes(self):
        """Tamaños de los divisores del diseño actual (solo con la ventana a la vista, ya tienen medida)."""
        sizes = [sp.sizes() for sp in self._splitters]
        if self.isVisible() and sizes and all(sum(s) for s in sizes):
            self.cfg.setdefault("layout_sizes", {})[self.layout_kind] = sizes

    def _save(self):
        """Pide guardar la configuración; se escribe poco después, junto con los cambios que sigan."""
        self._save_timer.start()

    def _flush_save(self):
        """Escribe la configuración ahora mismo (al cerrar no se espera al temporizador)."""
        self._save_timer.stop()
        self._remember_sizes()
        a = self.active
        self.cfg.update({
            "symbol": a.symbol or self.cfg.get("symbol"),
            "interval": a.interval,
            "period": a.period_key,
            "comparisons": a.comparisons,
            "chart_type": a.chart_type,
            "log_scale": a.log,
            "auto_refresh": self.auto.isChecked(),
            "refresh_secs": self.auto_every.currentData(),
            "indicators": a.indicators,
            "pane_heights": dict(a.chart.pane_heights),
            "charts": [p.state() for p in self.panes] + self._parked,
            "layout": self.layout_kind,
            "active_chart": self.panes.index(a),
            "sync": self.sync,
        })
        panel = self.cfg.setdefault("bottom_panel", {})
        panel.update(open=self.bottom.expanded, tab=self.bottom.stack.currentIndex())
        if self.bottom.expanded and self.isVisible():
            panel["height"] = self.bottom_dock.height()
        ok = config.save(self.cfg)
        if not ok and not self._save_failed:  # se avisa una vez, no en cada intento
            QtWidgets.QMessageBox.warning(
                self, "No se pudo guardar",
                f"No se pudo guardar la configuración en\n{config.PATH}\n\n{config.last_error}\n\n"
                "Tus cambios siguen en la app y se intentará guardar de nuevo; "
                "el último guardado correcto sigue intacto.")
        self._save_failed = not ok

    # ======================================================== alertas
    def _setup_alerts(self):
        self._alert_state: dict = {}   # último resultado de cada alerta (para detectar cruces)
        self._alert_busy = False
        self._alerts_win = None
        self._last_fired_symbol = None
        self._quitting = False
        icon = app_icon()
        self.setWindowIcon(icon)
        self.tray = None
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QtWidgets.QSystemTrayIcon(icon, self)
            self.tray.setToolTip("ChartView")
            menu = QtWidgets.QMenu()
            menu.addAction("Mostrar ChartView", self._show_from_tray)
            menu.addAction("Alertas…", lambda: (self._show_from_tray(), self.open_alerts()))
            menu.addSeparator()
            menu.addAction("Salir", self.quit_app)
            self.tray.setContextMenu(menu)
            self._tray_menu = menu
            self.tray.activated.connect(
                lambda r: self._show_from_tray() if r in (QtWidgets.QSystemTrayIcon.Trigger,
                                                          QtWidgets.QSystemTrayIcon.DoubleClick) else None)
            self.tray.messageClicked.connect(self._on_notification_clicked)
            self.tray.show()
        self.alert_timer = QtCore.QTimer(self)
        self.alert_timer.timeout.connect(self.check_alerts)
        self._restart_alert_timer()
        self._update_alert_button()
        QtCore.QTimer.singleShot(3000, self.check_alerts)

    def _restart_alert_timer(self):
        self.alert_timer.start(int(self.cfg.get("alerts_every", 60)) * 1000)

    def _update_alert_button(self):
        n = sum(1 for a in self.cfg.get("alerts", []) if a.get("enabled"))
        self.alert_act.setText(f"🔔{n}" if n else "🔔")
        self.alert_act.setToolTip(f"Alertas: {n} activas (Ctrl+Alt+A)" if n else "Alertas (Ctrl+Alt+A)")

    def last_price(self):
        if self.df is None or not len(self.df):
            return None
        return float(self.df["Close"].iloc[-1])

    def reset_alert_state(self, alert_id):
        self._alert_state.pop(alert_id, None)

    def open_alerts(self, preset=None):
        if not isinstance(preset, dict):  # desde un botón o menú llega un bool, no una alerta
            preset = None
        if self._alerts_win is None:
            self._alerts_win = AlertsWindow(self, self)
            self._alerts_win.changed.connect(self._on_alerts_changed)
            self._alerts_win.openSymbol.connect(lambda s: self.load(s, raw=True))
        self._alerts_win.refresh()
        self._alerts_win.show()
        self._alerts_win.raise_()
        self._alerts_win.activateWindow()
        if preset:
            self._alerts_win.add(preset)

    def alert_at_price(self, price: float):
        """Desde el menú de una línea horizontal: alerta de cruce en ese precio."""
        last = self.last_price()
        kind = "price_cross_up" if last is None or price >= last else "price_cross_down"
        self.open_alerts(alerts.new_alert(self.symbol, kind, price))

    def _on_alerts_changed(self):
        self.bottom.set_alerts(self.cfg.get("alert_history", []))
        self._restart_alert_timer()
        self._update_alert_button()
        for p in self.panes:
            self._update_alert_lines(p)
        self._save()

    def _update_alert_lines(self, pane):
        levels = [(a["value"], alerts.describe(a)) for a in self.cfg.get("alerts", [])
                  if a.get("enabled") and a["symbol"] == pane.symbol and a["type"] in alerts.PRICE_LEVEL_TYPES]
        pane.chart.set_alert_lines(levels)

    def check_alerts(self):
        active = [a for a in self.cfg.get("alerts", []) if a.get("enabled")]
        if not active or self._alert_busy:
            return
        self._alert_busy = True
        snapshot = deepcopy(active)  # el hilo trabaja con una copia
        run_task(alerts.evaluate, snapshot, on_done=self._on_alert_results,
                 on_fail=lambda e: setattr(self, "_alert_busy", False))

    def _on_alert_results(self, results):
        self._alert_busy = False
        fired = alerts.transitions(self.cfg.get("alerts", []), results, self._alert_state)
        for a in fired:
            self.notify(a)
        if fired:
            self._on_alerts_changed()
        if self._alerts_win is not None and self._alerts_win.isVisible():
            self._alerts_win.refresh()

    def notify(self, a: dict):
        text = alerts.message(a)
        title = f"🔔 Alerta: {a['symbol']}"
        hist = self.cfg.setdefault("alert_history", [])
        hist.append({"time": dt.datetime.now().isoformat(timespec="seconds"), "symbol": a["symbol"],
                     "text": text.replace("\n", " · ")})
        del hist[:-200]
        self.bottom.set_alerts(hist)
        self._last_fired_symbol = a["symbol"]
        if self.tray is not None:
            self.tray.showMessage(title, text, QtWidgets.QSystemTrayIcon.Information, 15000)
        QtWidgets.QApplication.beep()
        QtWidgets.QApplication.alert(self, 0)  # parpadea en la barra de tareas
        self.statusBar().showMessage(f"{title}  ·  {text.splitlines()[0]}", 30000)

    def test_notification(self):
        if self.tray is None:
            QtWidgets.QMessageBox.information(self, "Alertas", "Este sistema no tiene bandeja de notificaciones; "
                                                               "las alertas se mostrarán en la barra de estado.")
            return
        self._last_fired_symbol = None
        self.tray.showMessage("🔔 ChartView", "Así se verán tus alertas en el escritorio.",
                              QtWidgets.QSystemTrayIcon.Information, 8000)
        QtWidgets.QApplication.beep()

    def _on_notification_clicked(self):
        self._show_from_tray()
        if self._last_fired_symbol and self._last_fired_symbol != self.symbol:
            self.load(self._last_fired_symbol, raw=True)

    def _show_from_tray(self):
        self.showNormal() if self.isMinimized() or not self.isVisible() else None
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._quitting = True
        self._flush_save()  # desde la bandeja la ventana puede estar oculta y no recibir closeEvent
        self.close()
        QtWidgets.QApplication.quit()

    def closeEvent(self, ev):
        # posición, tamaño y si el panel de listas quedó abierto, cerrado o flotando
        self.cfg["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.cfg["window_state"] = bytes(self.saveState(WINDOW_STATE_VERSION).toBase64()).decode()
        self._flush_save()
        has_alerts = any(a.get("enabled") for a in self.cfg.get("alerts", []))
        if (not self._quitting and self.tray is not None and has_alerts
                and self.cfg.get("alerts_tray_on_close")):
            ev.ignore()
            self.hide()
            if self._alerts_win is not None:
                self._alerts_win.hide()
            self.tray.showMessage("ChartView sigue vigilando tus alertas",
                                  "Clic derecho en el icono de la bandeja → Salir para cerrarlo.",
                                  QtWidgets.QSystemTrayIcon.Information, 6000)
            return
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(ev)
        QtWidgets.QApplication.quit()


def app_icon() -> QtGui.QIcon:
    """Icono de la app (ventana y bandeja): velas sobre fondo azul, dibujado al vuelo."""
    icon = QtGui.QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        s = size / 64
        p.setBrush(QtGui.QColor("#2962ff"))
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(QtCore.QRectF(0, 0, size, size), 12 * s, 12 * s)
        for x, top, bottom, color in ((14, 30, 50, "#ef5350"), (28, 18, 42, "#26a69a"), (42, 8, 30, "#26a69a")):
            p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), max(1.0, 2 * s)))
            p.drawLine(QtCore.QPointF((x + 4) * s, (top - 5) * s), QtCore.QPointF((x + 4) * s, (bottom + 5) * s))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(color))
            p.drawRect(QtCore.QRectF(x * s, top * s, 8 * s, (bottom - top) * s))
        p.end()
        icon.addPixmap(pm)
    return icon


STYLE = """
QToolBar { background:#1e222d; border:none; border-bottom:1px solid #2a2e39; padding:4px; spacing:4px; }
QComboBox, QLineEdit, QPushButton, QToolButton, QSpinBox, QDoubleSpinBox {
    background:#2a2e39; border:1px solid #363a45; border-radius:4px; padding:4px 8px; color:#d1d4dc; }
QPushButton:hover, QToolButton:hover, QComboBox:hover { border-color:#2962ff; }
QPushButton:pressed { background:#2962ff; }
QTreeWidget, QListWidget, QTableWidget { background:#131722; border:1px solid #2a2e39; }
QHeaderView::section { background:#1e222d; color:#9598a1; border:none; padding:4px; }
QDockWidget::title { background:#1e222d; padding:6px; }
QStatusBar { background:#1e222d; color:#9598a1; }
QGroupBox { border:1px solid #2a2e39; border-radius:4px; margin-top:12px; padding-top:8px; }
QGroupBox::title { subcontrol-origin: margin; left:8px; color:#9598a1; }
QMenu { background:#1e222d; border:1px solid #363a45; }
QMenu::item:selected { background:#2962ff; }
QToolButton:checked { background:#2962ff; border-color:#2962ff; color:#ffffff; }
QMenuBar { background:#1e222d; color:#d1d4dc; padding:2px; }
QMenuBar::item { padding:4px 10px; background:transparent; }
QMenuBar::item:selected { background:#2a2e39; border-radius:4px; }
QAbstractItemView { selection-background-color:#2962ff; }
"""


def main():
    import sys
    import warnings

    warnings.filterwarnings("ignore")
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("ChartView")
    app.setQuitOnLastWindowClosed(False)  # puede seguir en la bandeja vigilando alertas
    app.setStyle("Fusion")  # la paleta y los estilos los pone el tema, al crear la ventana
    # Python podría recolectar basura en un hilo de descarga y destruir ahí objetos de Qt, lo que
    # tumba la app: así solo se recolecta en el hilo de la interfaz.
    collector = GarbageCollector(interval=1.0)  # noqa: F841 - debe seguir vivo mientras corre la app
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
