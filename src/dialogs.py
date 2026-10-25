"""Diálogos: administrador de indicadores y buscador de símbolos."""
from __future__ import annotations

from copy import deepcopy

from PySide6 import QtCore, QtGui, QtWidgets

from . import data
from .indicators import REGISTRY, by_category
from .workers import run_task


class ColorButton(QtWidgets.QPushButton):
    """Botón que muestra un color; vacío = automático."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.color: str | None = None
        self.clicked.connect(self._pick)
        self._refresh()

    def set_color(self, c):
        self.color = c
        self._refresh()

    def _refresh(self):
        if self.color:
            self.setText(self.color)
            self.setStyleSheet(f"background:{self.color}; color:#000; border:1px solid #555; padding:3px 8px;")
        else:
            self.setText("Automático")
            self.setStyleSheet("")

    def _pick(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color or "#2962ff"), self, "Color de la línea")
        if c.isValid():
            self.set_color(c.name())


class IndicatorDialog(QtWidgets.QDialog):
    def __init__(self, active: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indicadores")
        self.resize(1000, 600)
        self.active = deepcopy(active)
        self._key: str | None = None
        self._edit_index: int | None = None
        self._editors: dict = {}

        root = QtWidgets.QVBoxLayout(self)
        split = QtWidgets.QSplitter()
        root.addWidget(split, 1)

        # --- catálogo
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.filter = QtWidgets.QLineEdit(placeholderText="Buscar indicador…")
        self.filter.textChanged.connect(self._filter)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Indicador", "Ubicación"])
        self.tree.setColumnWidth(0, 260)
        for cat, defs in by_category().items():
            if not defs:
                continue
            top = QtWidgets.QTreeWidgetItem([f"{cat} ({len(defs)})"])
            top.setFlags(top.flags() & ~QtCore.Qt.ItemIsSelectable)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            for d in defs:
                it = QtWidgets.QTreeWidgetItem([d.name, "Sobre el precio" if d.overlay else "Panel inferior"])
                it.setData(0, QtCore.Qt.UserRole, d.key)
                top.addChild(it)
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()
        self.tree.currentItemChanged.connect(self._on_tree)
        self.tree.itemDoubleClicked.connect(lambda it, _c: it.data(0, QtCore.Qt.UserRole) and self._add())
        ll.addWidget(self.filter)
        ll.addWidget(self.tree)
        split.addWidget(left)

        # --- parámetros
        mid = QtWidgets.QWidget()
        ml = QtWidgets.QVBoxLayout(mid)
        self.title = QtWidgets.QLabel("Selecciona un indicador")
        self.title.setStyleSheet("font-size:14px; font-weight:bold;")
        self.title.setWordWrap(True)
        self.desc = QtWidgets.QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet("color:#9598a1;")
        self.form_box = QtWidgets.QGroupBox("Parámetros")
        self.form = QtWidgets.QFormLayout(self.form_box)
        self.color_btn = ColorButton()
        reset = QtWidgets.QPushButton("Auto")
        reset.setFixedWidth(50)
        reset.clicked.connect(lambda: self.color_btn.set_color(None))
        crow = QtWidgets.QHBoxLayout()
        crow.addWidget(self.color_btn, 1)
        crow.addWidget(reset)
        self.add_btn = QtWidgets.QPushButton("➕  Agregar")
        self.add_btn.clicked.connect(self._add)
        self.upd_btn = QtWidgets.QPushButton("✔  Actualizar seleccionado")
        self.upd_btn.clicked.connect(self._update)
        ml.addWidget(self.title)
        ml.addWidget(self.desc)
        ml.addWidget(self.form_box)
        ml.addWidget(QtWidgets.QLabel("Color de la línea principal:"))
        ml.addLayout(crow)
        ml.addStretch(1)
        ml.addWidget(self.add_btn)
        ml.addWidget(self.upd_btn)
        split.addWidget(mid)

        # --- activos
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QtWidgets.QLabel("<b>Indicadores activos</b>"))
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._on_list)
        rl.addWidget(self.list, 1)
        g = QtWidgets.QGridLayout()
        for i, (txt, fn) in enumerate([("Quitar", self._remove), ("Subir", lambda: self._move(-1)),
                                       ("Bajar", lambda: self._move(1)), ("Quitar todos", self._clear)]):
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(fn)
            g.addWidget(b, i // 2, i % 2)
        rl.addLayout(g)
        split.addWidget(right)
        split.setSizes([380, 330, 290])

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.Ok).setText("Aplicar")
        bb.button(QtWidgets.QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self._refresh_list()
        self._set_buttons()

    # ----------------------------------------------------------------------
    def _filter(self, text):
        t = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            visible = 0
            for j in range(top.childCount()):
                ch = top.child(j)
                d = REGISTRY[ch.data(0, QtCore.Qt.UserRole)]
                ok = not t or t in d.name.lower() or t in d.abbr.lower()
                ch.setHidden(not ok)
                visible += ok
            top.setHidden(visible == 0)

    def _show_params(self, key, params=None, color=None):
        self._key = key
        d = REGISTRY[key]
        params = {**d.defaults(), **(params or {})}
        self.title.setText(d.name)
        self.desc.setText(d.description or ("Se dibuja sobre el precio." if d.overlay else "Se dibuja en un panel debajo del precio."))
        while self.form.rowCount():
            self.form.removeRow(0)
        self._editors = {}
        for p in d.params:
            v = params[p.name]
            if p.options is not None:
                w = QtWidgets.QComboBox()
                w.addItems([str(o) for o in p.options])
                w.setCurrentText(str(v))
            elif p.is_int:
                w = QtWidgets.QSpinBox()
                w.setRange(int(p.minimum), int(p.maximum) if p.maximum < 2 ** 31 else 2 ** 31 - 1)
                w.setSingleStep(int(p.step or 1))
                w.setValue(int(v))
            else:
                w = QtWidgets.QDoubleSpinBox()
                w.setDecimals(3)
                w.setRange(float(p.minimum), float(p.maximum))
                w.setSingleStep(float(p.step or 0.1))
                w.setValue(float(v))
            self._editors[p.name] = w
            self.form.addRow(p.label + ":", w)
        if not d.params:
            self.form.addRow(QtWidgets.QLabel("Sin parámetros."))
        self.color_btn.set_color(color)
        self._set_buttons()

    def _collect(self) -> dict:
        out = {}
        for name, w in self._editors.items():
            if isinstance(w, QtWidgets.QComboBox):
                out[name] = w.currentText()
            else:
                out[name] = w.value()
        return out

    def _set_buttons(self):
        self.add_btn.setEnabled(self._key is not None)
        self.upd_btn.setEnabled(self._edit_index is not None)

    def _on_tree(self, cur, _prev):
        key = cur.data(0, QtCore.Qt.UserRole) if cur else None
        if key:
            self._edit_index = None
            self.list.blockSignals(True)
            self.list.clearSelection()
            self.list.setCurrentRow(-1)
            self.list.blockSignals(False)
            self._show_params(key)

    def _on_list(self, row):
        if 0 <= row < len(self.active):
            a = self.active[row]
            self._edit_index = row
            self._show_params(a["key"], a.get("params"), a.get("color"))
        else:
            self._edit_index = None
            self._set_buttons()

    def _refresh_list(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for a in self.active:
            d = REGISTRY.get(a["key"])
            if d is None:
                continue
            label = d.label({**d.defaults(), **a.get("params", {})})
            it = QtWidgets.QListWidgetItem(f"{label}   ·   {'precio' if d.overlay else 'panel'}")
            if a.get("color"):
                it.setForeground(QtGui.QColor(a["color"]))
            self.list.addItem(it)
        self.list.blockSignals(False)
        if select is not None and 0 <= select < self.list.count():
            self.list.setCurrentRow(select)

    def _add(self):
        if not self._key:
            return
        self.active.append({"key": self._key, "params": self._collect(), "color": self.color_btn.color})
        self._edit_index = None
        self._refresh_list()
        self._set_buttons()

    def _update(self):
        i = self._edit_index
        if i is None or not self._key:
            return
        self.active[i] = {"key": self._key, "params": self._collect(), "color": self.color_btn.color}
        self._refresh_list(select=i)

    def _remove(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.active):
            del self.active[i]
            self._edit_index = None
            self._refresh_list(select=min(i, len(self.active) - 1))
            self._set_buttons()

    def _move(self, d):
        i = self.list.currentRow()
        j = i + d
        if 0 <= i < len(self.active) and 0 <= j < len(self.active):
            self.active[i], self.active[j] = self.active[j], self.active[i]
            self._refresh_list(select=j)

    def _clear(self):
        self.active.clear()
        self._edit_index = None
        self._refresh_list()
        self._set_buttons()


class SearchDialog(QtWidgets.QDialog):
    """Busca símbolos en Yahoo Finance por nombre o clave."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Buscar símbolo")
        self.resize(720, 460)
        self.symbol: str | None = None
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.edit = QtWidgets.QLineEdit(text, placeholderText="Ej. Walmart, Cemex, Bitcoin, NVDA…")
        self.edit.returnPressed.connect(self._search)
        btn = QtWidgets.QPushButton("Buscar")
        btn.clicked.connect(self._search)
        row.addWidget(self.edit, 1)
        row.addWidget(btn)
        lay.addLayout(row)
        self.status = QtWidgets.QLabel("Escribe un nombre y presiona Enter. Doble clic para abrir.")
        self.status.setStyleSheet("color:#9598a1;")
        lay.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Símbolo", "Nombre", "Bolsa", "Tipo"])
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.cellDoubleClicked.connect(lambda r, _c: self._choose(r))
        lay.addWidget(self.table, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Open | QtWidgets.QDialogButtonBox.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.Open).setText("Abrir")
        bb.button(QtWidgets.QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(lambda: self._choose(self.table.currentRow()))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if text:
            QtCore.QTimer.singleShot(0, self._search)

    def _search(self):
        q = self.edit.text().strip()
        if not q:
            return
        self.status.setText("Buscando…")
        run_task(data.search, q, on_done=self._show, on_fail=lambda e: self.status.setText(f"Error: {e}"))

    def _show(self, rows):
        try:
            self.table.setRowCount(0)
        except RuntimeError:  # el diálogo ya se cerró
            return
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            for j, k in enumerate(("symbol", "name", "exchange", "type")):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(str(r[k])))
        self.status.setText(f"{len(rows)} resultados" if rows else "Sin resultados")
        if rows:
            self.table.selectRow(0)

    def _choose(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        self.symbol = self.table.item(row, 0).text()
        self.accept()
