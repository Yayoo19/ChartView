"""Ventana principal de ChartView."""
from __future__ import annotations

import datetime as dt
from copy import deepcopy

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import alerts, config, data, macro
from .alerts_ui import AlertsWindow
from .chart import CHART_TYPES, DOWN, UP, ChartWidget, fmt_change, fmt_price
from .dialogs import IndicatorDialog, SearchDialog
from .drawings import TOOLS
from .indicators import REGISTRY, by_category, compute
from .workers import run_task

REFRESH_OPTIONS = [("15 s", 15), ("30 s", 30), ("1 min", 60), ("5 min", 300), ("15 min", 900)]


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

        self._build_toolbar()
        self._build_center()
        self._build_watchlist()
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
    def _build_toolbar(self):
        tb = QtWidgets.QToolBar("Principal")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(16, 16))
        self.addToolBar(tb)
        self.main_tb = tb

        self.market = QtWidgets.QComboBox()
        self.market.addItems(data.MARKETS)
        self.market.setCurrentText(self.cfg.get("market", "EE.UU."))
        self.market.setToolTip("México agrega .MX · Cripto agrega -USD · Directo usa el símbolo tal cual")
        self.market.currentTextChanged.connect(self._update_completer)

        self.sym_edit = QtWidgets.QLineEdit()
        self.sym_edit.setPlaceholderText("Símbolo (AAPL, WALMEX, BTC…)")
        self.sym_edit.setFixedWidth(200)
        self.sym_edit.returnPressed.connect(lambda: self.load(self.sym_edit.text()))
        self._update_completer()

        go = QtWidgets.QPushButton("Ver")
        go.clicked.connect(lambda: self.load(self.sym_edit.text()))
        find = QtWidgets.QPushButton("🔍 Buscar")
        find.setToolTip("Buscar por nombre (Ctrl+F)")
        find.clicked.connect(self.open_search)

        self.interval = QtWidgets.QComboBox()
        self.interval.addItems(list(data.INTERVALS))
        self.interval.setCurrentText(self.cfg.get("interval", "1 día"))
        self.interval.currentTextChanged.connect(self._on_interval)

        self.period = QtWidgets.QComboBox()
        for key, label, _ in data.PERIODS:
            self.period.addItem(label, key)
        self._set_period(self.cfg.get("period", "2y"))
        self.period.currentIndexChanged.connect(lambda _i: self.load(self.symbol, raw=True))

        self.ctype = QtWidgets.QComboBox()
        self.ctype.addItems(CHART_TYPES)
        self.ctype.setCurrentText(self.cfg.get("chart_type", "Velas"))
        self.ctype.currentTextChanged.connect(lambda _t: self.redraw(keep_view=True))

        self.log_scale = QtWidgets.QCheckBox("Log")
        self.log_scale.setToolTip("Escala logarítmica en la gráfica de precio (Alt+L)")
        self.log_scale.setChecked(bool(self.cfg.get("log_scale")))
        self.log_scale.toggled.connect(lambda _c: (self.redraw(keep_view=True), self._save()))

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

        refresh = QtWidgets.QPushButton("⟳")
        refresh.setToolTip("Actualizar (F5)")
        refresh.setFixedWidth(32)
        refresh.clicked.connect(lambda: self.load(self.symbol, raw=True, keep_view=True))

        self.auto = QtWidgets.QCheckBox("Auto")
        self.auto.setToolTip("Actualizar automáticamente")
        self.auto.setChecked(bool(self.cfg.get("auto_refresh")))
        self.auto.toggled.connect(self._apply_auto_refresh)
        self.auto_every = QtWidgets.QComboBox()
        for label, secs in REFRESH_OPTIONS:
            self.auto_every.addItem(label, secs)
        idx = max(0, self.auto_every.findData(self.cfg.get("refresh_secs", 60)))
        self.auto_every.setCurrentIndex(idx)
        self.auto_every.currentIndexChanged.connect(self._apply_auto_refresh)

        fit = QtWidgets.QPushButton("Ver todo")
        fit.clicked.connect(lambda: self.chart.fit_all())
        last = QtWidgets.QPushButton("Recientes")
        last.setToolTip("Mostrar las últimas velas (Inicio)")
        last.clicked.connect(lambda: self.chart.show_last(self.chart.default_bars))
        shot = QtWidgets.QPushButton("📷")
        shot.setToolTip("Guardar imagen de la gráfica")
        shot.setFixedWidth(32)
        shot.clicked.connect(self.save_image)
        tools = QtWidgets.QToolButton()
        tools.setText("⚙")
        tools.setToolTip("Datos macro y configuración")
        tools.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        tmenu = QtWidgets.QMenu(tools)
        tmenu.addAction("Importar serie desde CSV… (p. ej. ISM PMI)", self.import_series)
        tmenu.addAction("Clave API de FRED…", self.set_fred_key)
        tools.setMenu(tmenu)
        self.alert_btn = QtWidgets.QPushButton("🔔 Alertas")
        self.alert_btn.setToolTip("Alertas de precio e indicadores (Ctrl+Alt+A)")
        self.alert_btn.clicked.connect(self.open_alerts)

        def lbl(t):
            w = QtWidgets.QLabel(t)
            w.setStyleSheet("color:#9598a1; padding:0 4px 0 10px;")
            return w

        for w in (lbl("Mercado"), self.market, self.sym_edit, go, find, lbl("Intervalo"), self.interval,
                  lbl("Periodo"), self.period, lbl("Tipo"), self.ctype, self.log_scale, ind_btn, self.alert_btn, refresh, self.auto,
                  self.auto_every, fit, last, shot, tools):
            tb.addWidget(w)

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
        self.chart.hovered.connect(self.info.setText)
        layer = self.chart.layer
        layer.changed.connect(self._save_drawings)
        layer.toolReset.connect(lambda: self._tool_actions["cursor"].setChecked(True) or self._on_tool("cursor"))
        layer.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        layer.alertRequested.connect(self.alert_at_price)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self._build_drawbar())
        row.addWidget(self.chart, 1)
        lay.addWidget(self.header)
        lay.addWidget(self.info)
        lay.addLayout(row, 1)
        self.setCentralWidget(w)

    def _build_drawbar(self):
        bar = QtWidgets.QToolBar("Dibujo")
        bar.setOrientation(QtCore.Qt.Vertical)
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        bar.setStyleSheet("QToolBar{background:#1e222d;border:1px solid #2a2e39;border-radius:4px;padding:2px;}"
                          "QToolButton{min-width:30px;min-height:26px;font-size:15px;border:none;padding:2px;}"
                          "QToolButton:checked{background:#2962ff;border-radius:4px;}"
                          "QToolButton:hover{background:#2a2e39;}")
        group = QtGui.QActionGroup(bar)
        group.setExclusive(True)
        self._tool_actions = {}
        for key, text, tip in TOOLS:
            act = bar.addAction(text)
            act.setToolTip(tip)
            act.setCheckable(True)
            act.triggered.connect(lambda _c, k=key: self._on_tool(k))
            group.addAction(act)
            self._tool_actions[key] = act
            if key == "cursor":
                act.setChecked(True)
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
        toggle.setText("☰ Listas")
        toggle.setToolTip("Mostrar u ocultar las listas de seguimiento (Ctrl+B)")
        toggle.setShortcut(QtGui.QKeySequence("Ctrl+B"))
        self.addAction(toggle)  # el atajo funciona aunque el panel esté oculto
        first = self.main_tb.actions()[0]
        self.main_tb.insertAction(first, toggle)
        self.main_tb.insertSeparator(first)
        self._fill_watchlist_combo(self.cfg.get("active_watchlist"))

    def _build_shortcuts(self):
        for seq, fn in [
            ("F5", lambda: self.load(self.symbol, raw=True, keep_view=True)),
            ("Ctrl+I", self.open_indicators),
            ("Ctrl+F", self.open_search),
            ("Ctrl+L", lambda: (self.sym_edit.setFocus(), self.sym_edit.selectAll())),
            ("Home", lambda: self.chart.show_last(self.chart.default_bars)),
            ("Ctrl+S", self.save_image),
            ("Alt+L", self.log_scale.toggle),
            ("Esc", lambda: self._tool_actions["cursor"].trigger()),
            ("Ctrl+Z", self.chart.layer.undo),
            ("Ctrl+Alt+A", self.open_alerts),
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
                it.setTextAlignment(1, QtCore.Qt.AlignRight)
                it.setTextAlignment(2, QtCore.Qt.AlignRight)
                top.addChild(it)
                self._watch_items.setdefault(s, []).append(it)
            self.watch.addTopLevelItem(top)
        self.watch.expandAll()

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
        syms = sorted({s for lst in self._wl().values() for s in lst})
        run_task(data.quotes, syms, on_done=self._show_quotes)

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

    def _update_completer(self, *_):
        market = self.market.currentText()
        words = data.POPULAR.get(market) or [s for v in data.POPULAR.values() for s in v]
        if market == macro.MARKET:
            words = words + macro.imported()
        comp = QtWidgets.QCompleter(words, self)
        comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        comp.setFilterMode(QtCore.Qt.MatchContains)
        comp.activated.connect(lambda s: self.load(s, raw=True))
        self.sym_edit.setCompleter(comp)

    # ======================================================== carga
    def _set_period(self, key):
        i = self.period.findData(key)
        self.period.blockSignals(True)
        self.period.setCurrentIndex(i if i >= 0 else 0)
        self.period.blockSignals(False)

    def _on_interval(self, label):
        self._set_period(data.INTERVALS[label][3])
        self.load(self.symbol, raw=True)

    def load(self, text, raw=False, keep_view=False, silent=False):
        sym = text.strip().upper() if raw else data.resolve_symbol(text, self.market.currentText())
        if not sym:
            return
        same = sym == self.symbol
        self.sym_edit.setText(sym)
        interval = self.interval.currentText()
        period = self.period.currentData()
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
            if used != period:
                self._set_period(used)
                self.statusBar().showMessage(
                    f"Yahoo limita la historia para {interval}; se usó el periodo «{used}».", 8000)
            self.redraw(keep_view=keep)
            self._save()

        def fail(err, req=req, sym=sym, silent=silent):
            if not silent:
                QtWidgets.QApplication.restoreOverrideCursor()
            if req != self._req:
                return
            self.statusBar().showMessage(f"Error al cargar {sym}: {err}", 15000)
            if not silent:
                QtWidgets.QMessageBox.warning(self, "No se pudo cargar", f"{sym}\n\n{err}")
            self.sym_edit.setText(self.symbol)

        run_task(data.load, sym, interval, period, on_done=done, on_fail=fail)

    def redraw(self, keep_view=False):
        if self.df is None:
            return
        computed, errors = [], []
        for a in self.indicators:
            try:
                computed.append(compute(a["key"], a.get("params"), self.df, a.get("color")))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{REGISTRY[a['key']].abbr}: {e}")
        interval = self.interval.currentText()
        intraday = data.INTERVALS[interval][2]
        self.chart.layer.drawings = list(self.cfg.setdefault("drawings", {}).get(self.symbol, []))
        chart_type = self.ctype.currentText()
        if chart_type in ("Velas", "Heikin Ashi", "Barras OHLC") and (self.df["High"] == self.df["Low"]).all():
            chart_type = "Línea"  # series de un solo valor por fecha (M2, tasas sin agrupar…)
        self.chart.set_data(self.df, computed, chart_type, intraday=intraday,
                            keep_view=keep_view, title=self.symbol, log=self.log_scale.isChecked())
        self._update_alert_lines()
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
            f"&nbsp;&nbsp;<span style='color:#787b86'>{exch} · {cur}</span>&nbsp;&nbsp;&nbsp;"
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
        self.market.setCurrentText(macro.MARKET)
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
        if self.auto.isChecked():
            self.timer.start(int(self.auto_every.currentData()) * 1000)
        else:
            self.timer.stop()
        self._save()

    def _save(self):
        self.cfg.update({
            "symbol": self.symbol or self.cfg.get("symbol"),
            "market": self.market.currentText(),
            "interval": self.interval.currentText(),
            "period": self.period.currentData(),
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
