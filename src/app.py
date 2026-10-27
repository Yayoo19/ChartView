"""Ventana principal de ChartView."""
from __future__ import annotations

import datetime as dt
from copy import deepcopy

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import alerts, config, data, macro
from .alerts_ui import AlertsWindow
from .chart import CHART_TYPES, DOWN, UP, ChartWidget, fmt_change, fmt_price
from .dialogs import CompareDialog, IndicatorDialog, IndicatorEditDialog, SearchDialog
from .drawings import TOOL_GROUPS, TOOLS
from .indicators import REGISTRY, by_category, compute
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
SHORTCUTS = [
    ("Ctrl+L", "Ir al buscador de símbolos"), ("Ctrl+F", "Búsqueda avanzada"), ("F5", "Actualizar datos"),
    ("Inicio", "Ver las últimas velas"), ("Alt+L", "Escala logarítmica"), ("Ctrl+B", "Mostrar u ocultar listas"), ("Ctrl+D", "Mostrar u ocultar herramientas de dibujo"),
    ("Ctrl+I", "Indicadores"), ("Ctrl+Alt+A", "Alertas"), ("Ctrl+S", "Guardar imagen de la gráfica"),
    ("Ctrl+Z", "Deshacer último dibujo"), ("Esc", "Volver al cursor (dibujo)"), ("F11", "Pantalla completa"),
    ("Ctrl+G", "Encender o apagar la cuadrícula"), ("F1", "Esta ayuda"), ("Ctrl+Q", "Salir"),
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


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChartView")
        self.resize(1500, 920)
        self.cfg = config.load()
        macro.FRED_API_KEY = self.cfg.get("fred_api_key", "")
        self.indicators: list[dict] = [a for a in self.cfg["indicators"] if a.get("key") in REGISTRY]
        self.df = None
        self.meta: dict = {}
        self.symbol: str = ""
        self._req = 0
        self.period_key: str = self.cfg.get("period", "2y")
        self.comparisons: list[str] = list(self.cfg.get("comparisons", []))
        self._comp_closes: dict = {}  # símbolo -> cierres alineados a self.df

        self._build_actions()
        self._build_toolbar()
        self._build_center()
        self._build_watchlist()
        self._build_menu()
        self._build_shortcuts()
        self.statusBar().showMessage("Listo")

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(lambda: self.load(self.symbol, raw=True, keep_view=True, silent=True))
        self.quote_timer = QtCore.QTimer(self)
        self.quote_timer.timeout.connect(self.refresh_quotes)
        self.quote_timer.start(5 * 60 * 1000)

        self._apply_auto_refresh()
        self._setup_alerts()
        if self.cfg.get("window_geometry"):
            self.restoreGeometry(QtCore.QByteArray.fromBase64(self.cfg["window_geometry"].encode()))
        if self.cfg.get("window_state"):
            self.restoreState(QtCore.QByteArray.fromBase64(self.cfg["window_state"].encode()))
        QtCore.QTimer.singleShot(0, lambda: self.load(self.cfg["symbol"], raw=True))
        QtCore.QTimer.singleShot(500, self.refresh_quotes)

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
        self.log_scale.setChecked(bool(self.cfg.get("log_scale")))
        self.log_scale.toggled.connect(lambda _c: (self.redraw(keep_view=True), self._save()))
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
                                  slot=lambda: self.load(self.symbol, raw=True, keep_view=True))
        self.fit_act = action("Ver toda la historia", "Ver toda la historia cargada", short="Ver todo",
                              slot=lambda: self.chart.fit_all())
        self.recent_act = action("Ver últimas velas", "Ver las últimas velas (Inicio)", "Home", short="Recientes",
                                 slot=lambda: self.chart.show_last(self.chart.default_bars))
        self.drawbar_act = action("Herramientas de dibujo", "Mostrar u ocultar las herramientas de dibujo (Ctrl+D)",
                                  "Ctrl+D", True, short="✎ Dibujo")
        self.drawbar_act.setChecked(bool(self.cfg.get("drawbar_visible", True)))
        self.drawbar_act.toggled.connect(self._set_drawbar_visible)

    def _build_toolbar(self):
        tb = QtWidgets.QToolBar("Principal")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(16, 16))
        self.addToolBar(tb)
        self.main_tb = tb

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
        self.interval.setCurrentText(self.cfg.get("interval", "1 día"))
        self.interval.currentTextChanged.connect(self._on_interval)
        tb.addWidget(self.interval)
        tb.addSeparator()

        self.ctype = QtWidgets.QComboBox()
        self.ctype.setToolTip("Tipo de gráfica")
        self.ctype.addItems(CHART_TYPES)
        self.ctype.setCurrentText(self.cfg.get("chart_type", "Velas"))
        self.ctype.currentTextChanged.connect(lambda _t: self.redraw(keep_view=True))
        tb.addWidget(self.ctype)

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
        self.alert_btn = QtWidgets.QPushButton("🔔 Alertas")
        self.alert_btn.setToolTip("Alertas de precio e indicadores (Ctrl+Alt+A)")
        self.alert_btn.clicked.connect(lambda: self.open_alerts())
        tb.addWidget(self.alert_btn)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.refresh_act)
        self._sync_interval_buttons(self.interval.currentText())
        self._update_compare_button()

    def _build_rangebar(self):
        """Barra bajo la gráfica: cuánta historia ver (rango) y opciones de visualización."""
        w = QtWidgets.QWidget()
        w.setObjectName("rangebar")
        w.setStyleSheet(
            "#rangebar QToolButton{background:transparent;border:none;border-radius:4px;padding:3px 9px;color:#b2b5be;}"
            "#rangebar QToolButton:hover{background:#2a2e39;}"
            "#rangebar QToolButton:checked{background:#2a2e39;color:#ffffff;}"
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
        for act in (self.log_scale, self.auto):
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
        mb = self.menuBar()
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
        v.addAction(self.drawbar_act)
        v.addSeparator()
        v.addAction(self.log_scale)
        v.addAction(self.fit_act)
        v.addAction(self.recent_act)
        v.addSeparator()
        v.addAction(self.refresh_act)
        v.addAction(self.auto)
        v.addSeparator()
        full = v.addAction("Pantalla completa", lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen(),
                           QtGui.QKeySequence("F11"))
        full.setToolTip("Pantalla completa (F11)")
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

        t = mb.addMenu("&Herramientas")
        t.addAction("Indicadores…", self.open_indicators, QtGui.QKeySequence("Ctrl+I"))
        t.addAction("Alertas…", self.open_alerts, QtGui.QKeySequence("Ctrl+Alt+A"))
        t.addSeparator()
        t.addAction("Clave API de FRED…", self.set_fred_key)

        a = mb.addMenu("A&yuda")
        a.addAction("Atajos de teclado", self.show_shortcuts, QtGui.QKeySequence("F1"))
        a.addAction("Acerca de ChartView", self.show_about)

    def _set_grid_level(self, name: str):
        self.chart.set_grid(dict(GRID_LEVELS)[name])
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
            "Datos: Yahoo Finance, Reserva Federal (DBnomics) y FRED.<br>"
            "<a href='https://github.com/Yayoo19/ChartView' style='color:#2962ff'>github.com/Yayoo19/ChartView</a>")

    def _build_center(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 0)
        lay.setSpacing(2)
        self.header = QtWidgets.QLabel("")
        self.header.setTextFormat(QtCore.Qt.RichText)
        self.header.setStyleSheet("font-size:15px; padding:4px 2px;")
        self.info = QtWidgets.QLabel("")
        self.info.setTextFormat(QtCore.Qt.RichText)
        self.info.setStyleSheet("font-size:12px; padding:0 2px 4px 2px;")
        self.chart = ChartWidget()
        self.chart.grid_alpha = dict(GRID_LEVELS).get(self.cfg.get("grid", "Media"), 0.22)
        self.chart.hovered.connect(self.info.setText)
        self.chart.legendAction.connect(self._legend_action)
        self.chart.compareRemoved.connect(self.remove_comparison)
        layer = self.chart.layer
        layer.changed.connect(self._save_drawings)
        layer.toolReset.connect(lambda: self._tool_actions["cursor"].setChecked(True) or self._on_tool("cursor"))
        layer.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        layer.alertRequested.connect(self.alert_at_price)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.drawbar = self._build_drawbar()
        row.addWidget(self.drawbar)
        row.addWidget(self.chart, 1)
        lay.addWidget(self.header)
        lay.addWidget(self.info)
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
        bar.setStyleSheet("QToolBar{background:#1e222d;border:1px solid #2a2e39;border-radius:4px;padding:2px;}"
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
        undo.setToolTip("Deshacer último dibujo (Ctrl+Z)")
        undo.triggered.connect(self.chart.layer.undo)
        self.eye_act = bar.addAction("👁")
        self.eye_act.setToolTip("Mostrar u ocultar dibujos")
        self.eye_act.setCheckable(True)
        self.eye_act.setChecked(True)
        self.eye_act.toggled.connect(self.chart.layer.set_visible)
        trash = bar.addAction("🗑")
        trash.setToolTip("Borrar todos los dibujos de este símbolo")
        trash.triggered.connect(self._clear_drawings)
        return bar

    def _set_magnet(self, mode: str, save: bool = True):
        if mode not in MAGNET_MODES:
            mode = "off"
        self.chart.layer.magnet = mode
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
        self.chart.layer.set_tool(key)
        if key == "cursor":
            self.chart.unsetCursor()
        else:
            self.chart.setCursor(QtCore.Qt.CrossCursor)
            tip = dict((k, t) for k, _, t in TOOLS)[key]
            self.statusBar().showMessage(f"{tip}  ·  Esc o clic derecho para cancelar", 6000)

    def _set_draw_color(self, color):
        self.chart.layer.color = color
        self.color_btn.setStyleSheet(f"color:{color}; font-size:20px;")
        self.cfg["draw_color"] = color

    def _pick_draw_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.chart.layer.color), self, "Color de dibujo")
        if c.isValid():
            self._set_draw_color(c.name())
            self._save()

    def _set_draw_width(self, wv, btn):
        self.chart.layer.width = wv
        btn.setText(f"{wv}px")
        self.cfg["draw_width"] = wv

    def _save_drawings(self):
        if self.symbol:
            self.cfg.setdefault("drawings", {})[self.symbol] = self.chart.layer.drawings
            self._save()

    def _clear_drawings(self):
        if not self.chart.layer.drawings:
            return
        if QtWidgets.QMessageBox.question(self, "Borrar dibujos",
                                          f"¿Borrar todos los dibujos de {self.symbol}?") == QtWidgets.QMessageBox.Yes:
            self.chart.layer.clear()

    def _build_watchlist(self):
        dock = QtWidgets.QDockWidget("Listas de seguimiento", self)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetClosable)
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
        wl_menu.addAction("Eliminar lista", self.delete_watchlist)
        wl_btn.setMenu(wl_menu)
        top.addWidget(self.wl_combo, 1)
        top.addWidget(wl_btn)
        lay.addLayout(top)
        self.watch = QtWidgets.QTreeWidget()
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
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
        self.watch_dock = dock
        dock.setObjectName("watchlist_dock")  # necesario para guardar/restaurar su estado
        # botón (y Ctrl+B) para volver a abrir el panel después de cerrarlo con la ✕
        toggle = dock.toggleViewAction()
        toggle.setText("Listas de seguimiento")
        toggle.setIconText("☰ Listas")
        toggle.setToolTip("Mostrar u ocultar las listas de seguimiento (Ctrl+B)")
        toggle.setShortcut(QtGui.QKeySequence("Ctrl+B"))
        self.addAction(toggle)  # el atajo funciona aunque el panel esté oculto
        first = self.main_tb.actions()[0]
        self.main_tb.insertAction(first, toggle)
        self.main_tb.insertAction(first, self.drawbar_act)
        self.main_tb.insertSeparator(first)
        self._fill_watchlist_combo(self.cfg.get("active_watchlist"))

    def _build_shortcuts(self):
        # el resto de los atajos están en las acciones del menú (_build_menu)
        for seq, fn in [
            ("Esc", lambda: self._tool_actions["cursor"].trigger()),
            ("Ctrl+Z", self.chart.layer.undo),
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

    def _ask_name(self, title, text=""):
        name, ok = QtWidgets.QInputDialog.getText(self, title, "Nombre de la lista:", text=text)
        name = name.strip()
        if not ok or not name:
            return None
        if name in self.cfg["watchlists"]:
            QtWidgets.QMessageBox.warning(self, title, f"Ya existe una lista llamada «{name}».")
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

    def _fill_watchlist(self):
        self.watch.clear()
        self._watch_items = {}
        for group, syms in self._wl().items():
            if not syms:
                continue
            top = QtWidgets.QTreeWidgetItem([group])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            for s in syms:
                it = QtWidgets.QTreeWidgetItem([s, "", ""])
                it.setData(0, QtCore.Qt.UserRole, s)
                it.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                it.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                top.addChild(it)
                self._watch_items.setdefault(s, []).append(it)
            self.watch.addTopLevelItem(top)
        self.watch.expandAll()
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
        if not it or not it.data(0, QtCore.Qt.UserRole):
            return
        s = it.data(0, QtCore.Qt.UserRole)
        m = QtWidgets.QMenu(self)
        m.addAction("Abrir", lambda: self._on_watch(it))
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
        groups = self.cfg["watchlists"].setdefault(list_name or self.wl_combo.currentText(), {})
        lst = groups.setdefault(data.guess_market(symbol, meta), [])
        if symbol in lst:
            return
        lst.append(symbol)
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
    def _set_period(self, key):
        self.period_key = key
        if getattr(self, "_range_btns", None):
            self._range_group.setExclusive(False)
            for k, b in self._range_btns.items():
                b.setChecked(k == key)
            self._range_group.setExclusive(True)

    def _on_range(self, key):
        self._set_period(key)
        self.load(self.symbol, raw=True)

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
        self._sync_interval_buttons(label)
        self._sync_range_enabled(label)
        self._set_period(data.INTERVALS[label][3])
        self.load(self.symbol, raw=True)

    def load(self, text, raw=False, keep_view=False, silent=False, by_name=False):
        """Carga un símbolo. ``by_name``: si no existe tal cual, se busca por nombre y se abre el primero."""
        sym = " ".join(text.upper().split())  # «aapl / ^gspc» -> «AAPL / ^GSPC»
        if not sym:
            return
        same = sym == self.symbol
        self.sym_edit.setText(sym)
        interval = self.interval.currentText()
        period = self.period_key
        self._req += 1
        req = self._req
        if not silent:
            self.statusBar().showMessage(f"Descargando {sym} ({interval})…")
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.BusyCursor)
        keep = keep_view and same

        def done(res, req=req, sym=sym, keep=keep, silent=silent):
            if not silent:
                QtWidgets.QApplication.restoreOverrideCursor()
            if req != self._req:
                return
            df, meta, used = res
            self.df, self.meta, self.symbol = df, meta, sym
            self._remember_name(sym, meta)
            if not silent and sym in getattr(self, "_watch_items", {}):
                # el renglón de la lista muestra el mismo precio que la gráfica recién cargada
                run_task(data.quotes, [sym], on_done=self._show_quotes)
            if used != period:
                self._set_period(used)
                self.statusBar().showMessage(
                    f"Yahoo limita la historia para {interval}; se usó el periodo «{used}».", 8000)
            if self.comparisons:
                self._load_comparisons(keep)  # dibuja cuando lleguen, sin parpadeo
            else:
                self._comp_closes = {}
                self.redraw(keep_view=keep)
            self._save()

        def fail(err, req=req, sym=sym, silent=silent):
            if not silent:
                QtWidgets.QApplication.restoreOverrideCursor()
            if req != self._req:
                return
            if by_name:  # «walmex», «bitcoin»…: se busca por nombre y se abre el mejor resultado
                def found(rows, req=req):
                    if req == self._req and rows:
                        self.load(rows[0]["symbol"], raw=True)
                    elif req == self._req:
                        QtWidgets.QMessageBox.warning(self, "No se encontró", f"No encontré nada para «{sym}».")
                        self.sym_edit.setText(self.symbol)
                self.statusBar().showMessage(f"Buscando «{sym}» por nombre…")
                run_task(data.search, sym, on_done=found, on_fail=lambda e: found([]))
                return
            self.statusBar().showMessage(f"Error al cargar {sym}: {err}", 15000)
            if not silent:
                QtWidgets.QMessageBox.warning(self, "No se pudo cargar", f"{sym}\n\n{err}")
            self.sym_edit.setText(self.symbol)

        run_task(data.load, sym, interval, period, on_done=done, on_fail=fail)

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
            self._comp_closes.pop(sym, None)
            self._update_compare_button()
            self._save()
            self.redraw(keep_view=bool(self.comparisons))

    def clear_comparisons(self):
        self.comparisons.clear()
        self._comp_closes = {}
        self._update_compare_button()
        self._save()
        self.redraw()

    def _load_comparisons(self, keep=False):
        """Descarga los símbolos a comparar con el mismo intervalo y periodo, y luego dibuja todo junto."""
        syms = [s for s in self.comparisons if s != self.symbol]
        req, interval, period = self._req, self.interval.currentText(), self.period_key

        def done(res, req=req):
            if req != self._req:
                return
            self._comp_closes = {s: v for s, v in res.items() if not isinstance(v, str)}
            self.redraw(keep_view=keep)
            errs = [f"{s} ({v})" for s, v in res.items() if isinstance(v, str)]
            if errs:
                self.statusBar().showMessage("No se pudo comparar con " + "; ".join(errs), 12000)

        if not syms:
            done({})
            return
        self.statusBar().showMessage(f"Descargando {', '.join(syms)} para comparar…")
        run_task(data.load_closes, syms, self.df.index, interval, period, on_done=done,
                 on_fail=lambda e, req=req: done({}) if req == self._req else None)

    def redraw(self, keep_view=False):
        if self.df is None:
            return
        computed, errors = [], []
        for i, a in enumerate(self.indicators):
            try:
                c = compute(a["key"], a.get("params"), self.df, a.get("color"))
                c.index, c.hidden = i, bool(a.get("hidden"))
                computed.append(c)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{REGISTRY[a['key']].abbr}: {e}")
        interval = self.interval.currentText()
        intraday = data.INTERVALS[interval][2]
        self.chart.layer.drawings = list(self.cfg.setdefault("drawings", {}).get(self.symbol, []))
        chart_type = self.ctype.currentText()
        if chart_type in ("Velas", "Heikin Ashi", "Barras OHLC") and (self.df["High"] == self.df["Low"]).all():
            chart_type = "Línea"  # series de un solo valor por fecha (M2, tasas sin agrupar…)
        comps = [(s, self._comp_closes[s], COMPARE_COLORS[k % len(COMPARE_COLORS)])
                 for k, s in enumerate(self.comparisons)
                 if s in self._comp_closes and len(self._comp_closes[s]) == len(self.df)]
        self.chart.set_data(self.df, computed, chart_type, intraday=intraday,
                            keep_view=keep_view, title=self.symbol, log=self.log_scale.isChecked(),
                            comparisons=comps)
        self._update_alert_lines()
        try:
            self.chart.set_countdown(data.candle_end(self.df, self.meta, interval))
        except Exception:  # noqa: BLE001 - sin cuenta regresiva si Yahoo no da el horario
            self.chart.set_countdown(None)
        self._update_header(intraday)
        msg = f"{self.symbol} · {len(self.df)} velas · {interval} · actualizado {dt.datetime.now():%H:%M:%S}"
        if errors:
            msg += "  ·  Errores: " + "; ".join(errors)
        self.statusBar().showMessage(msg)

    def _update_header(self, intraday):
        m, df = self.meta, self.df
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
        self.header.setText(
            f"<b style='color:#ffffff'>{self.symbol}</b>&nbsp;&nbsp;<span style='color:#d1d4dc'>{name}</span>"
            f"&nbsp;&nbsp;<span style='color:#787b86'>{' · '.join(x for x in (exch, cur) if x)}</span>&nbsp;&nbsp;&nbsp;"
            f"<b style='color:#ffffff; font-size:17px'>{fmt_price(last)}</b>&nbsp;"
            f"<span style='color:{col}'>{fmt_change(chg)} ({pct:+.2f}%)</span>")
        self.setWindowTitle(f"{self.symbol} {fmt_price(last)} — ChartView")

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

    def _save(self):
        self.cfg.update({
            "symbol": self.symbol or self.cfg.get("symbol"),
            "interval": self.interval.currentText(),
            "period": self.period_key,
            "comparisons": self.comparisons,
            "chart_type": self.ctype.currentText(),
            "log_scale": self.log_scale.isChecked(),
            "auto_refresh": self.auto.isChecked(),
            "refresh_secs": self.auto_every.currentData(),
            "indicators": self.indicators,
        })
        config.save(self.cfg)

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
        self.alert_btn.setText(f"🔔 Alertas ({n})" if n else "🔔 Alertas")

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
        self._restart_alert_timer()
        self._update_alert_button()
        self._update_alert_lines()
        self._save()

    def _update_alert_lines(self):
        levels = [(a["value"], alerts.describe(a)) for a in self.cfg.get("alerts", [])
                  if a.get("enabled") and a["symbol"] == self.symbol and a["type"] in alerts.PRICE_LEVEL_TYPES]
        self.chart.set_alert_lines(levels)

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
        self.close()
        QtWidgets.QApplication.quit()

    def closeEvent(self, ev):
        # posición, tamaño y si el panel de listas quedó abierto, cerrado o flotando
        self.cfg["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.cfg["window_state"] = bytes(self.saveState().toBase64()).decode()
        self._save()
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


def dark_palette() -> QtGui.QPalette:
    p = QtGui.QPalette()
    c = QtGui.QColor
    roles = {
        QtGui.QPalette.Window: c("#1e222d"), QtGui.QPalette.WindowText: c("#d1d4dc"),
        QtGui.QPalette.Base: c("#131722"), QtGui.QPalette.AlternateBase: c("#1e222d"),
        QtGui.QPalette.ToolTipBase: c("#2a2e39"), QtGui.QPalette.ToolTipText: c("#d1d4dc"),
        QtGui.QPalette.Text: c("#d1d4dc"), QtGui.QPalette.Button: c("#2a2e39"),
        QtGui.QPalette.ButtonText: c("#d1d4dc"), QtGui.QPalette.BrightText: c("#ff5252"),
        QtGui.QPalette.Highlight: c("#2962ff"), QtGui.QPalette.HighlightedText: c("#ffffff"),
        QtGui.QPalette.PlaceholderText: c("#787b86"),
    }
    for role, color in roles.items():
        p.setColor(role, color)
    p.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, c("#5d606b"))
    p.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, c("#5d606b"))
    return p


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
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
