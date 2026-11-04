"""Panel inferior plegable: movimientos de la lista, alertas recientes y notas por símbolo."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .chart import DOWN, UP, fmt_price

TABS = ["Movimientos", "Alertas", "Notas"]


class BottomPanel(QtWidgets.QWidget):
    """Solo la fila de pestañas se queda a la vista: un clic abre el panel y otro en la misma lo pliega."""

    toggled = QtCore.Signal(bool)
    openSymbol = QtCore.Signal(str)
    notesChanged = QtCore.Signal()

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.expanded = False
        self._symbol = ""
        self.bar = QtWidgets.QTabBar()
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.bar.setStyleSheet(
            "QTabBar::tab{background:transparent;color:#787b86;padding:5px 14px;border:none;}"
            "QTabBar::tab:hover{color:#d1d4dc;}")
        for name in TABS:
            self.bar.addTab(name)
        self.bar.tabBarClicked.connect(self._on_tab)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_movers())
        self.stack.addWidget(self._build_alerts())
        self.stack.addWidget(self._build_notes())
        self.stack.hide()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.bar)
        lay.addWidget(self.stack, 1)
        self._save_notes = QtCore.QTimer(self, singleShot=True, interval=800)
        self._save_notes.timeout.connect(self.notesChanged)
        self._mark_tab()

    # ------------------------------------------------------------- pestañas
    def set_state(self, tab: int, expanded: bool):
        tab = tab if 0 <= tab < len(TABS) else 0
        self.bar.setCurrentIndex(tab)
        self.stack.setCurrentIndex(tab)
        self._set_expanded(expanded)

    def toggle(self):
        self._set_expanded(not self.expanded)

    def _on_tab(self, i: int):
        if i < 0:
            return
        if self.expanded and i == self.stack.currentIndex():
            self._set_expanded(False)
            return
        self.stack.setCurrentIndex(i)
        self._set_expanded(True)

    def _set_expanded(self, on: bool):
        self.expanded = on
        self.stack.setVisible(on)
        self._mark_tab()
        self.toggled.emit(on)

    def _mark_tab(self):
        selected = "#ffffff" if self.expanded else "#787b86"
        line = "border-bottom:2px solid #2962ff;" if self.expanded else ""
        self.bar.setStyleSheet(
            "QTabBar::tab{background:transparent;color:#787b86;padding:5px 14px;border:none;}"
            "QTabBar::tab:hover{color:#d1d4dc;}"
            f"QTabBar::tab:selected{{color:{selected};{line}}}")

    # ---------------------------------------------------------- movimientos
    def _build_movers(self):
        t = QtWidgets.QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["Símbolo", "Nombre", "Último", "Cambio"])
        t.verticalHeader().hide()
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setShowGrid(False)
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for c in (0, 2, 3):
            hdr.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeToContents)
        t.cellClicked.connect(lambda r, _c: self.openSymbol.emit(t.item(r, 0).text()))
        self.movers = t
        return t

    def set_movers(self, rows: list[tuple]):
        """rows: (símbolo, nombre, último, cambio %), ya sin datos faltantes. De mayor a menor cambio."""
        t = self.movers
        rows = sorted(rows, key=lambda r: r[3], reverse=True)
        t.setRowCount(len(rows))
        for r, (sym, name, last, chg) in enumerate(rows):
            cells = [sym, name, fmt_price(last), f"{chg:+.2f}%"]
            for c, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)
                if c >= 2:
                    it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if c == 3:
                    it.setForeground(QtGui.QColor(UP if chg >= 0 else DOWN))
                t.setItem(r, c, it)

    # -------------------------------------------------------------- alertas
    def _build_alerts(self):
        self.alerts = QtWidgets.QListWidget()
        self.alerts.itemClicked.connect(
            lambda it: self.openSymbol.emit(it.data(QtCore.Qt.UserRole)) if it.data(QtCore.Qt.UserRole) else None)
        return self.alerts

    def set_alerts(self, history: list[dict]):
        self.alerts.clear()
        for h in reversed(history):
            it = QtWidgets.QListWidgetItem(f"{h['time'][:16].replace('T', ' ')}   {h['text']}")
            it.setData(QtCore.Qt.UserRole, h.get("symbol"))
            self.alerts.addItem(it)

    # ---------------------------------------------------------------- notas
    def _build_notes(self):
        self.notes = QtWidgets.QPlainTextEdit()
        self.notes.setPlaceholderText("Notas de este símbolo. Se guardan solas.")
        self.notes.textChanged.connect(self._on_notes)
        return self.notes

    def set_symbol(self, symbol: str):
        if symbol == self._symbol:
            return
        self._symbol = ""  # poner el texto del otro símbolo no es una edición
        self.notes.setPlainText(self.cfg.get("notes", {}).get(symbol, ""))
        self._symbol = symbol

    def _on_notes(self):
        if not self._symbol:
            return
        notes = self.cfg.setdefault("notes", {})
        text = self.notes.toPlainText()
        if text.strip():
            notes[self._symbol] = text
        else:
            notes.pop(self._symbol, None)
        self._save_notes.start()
