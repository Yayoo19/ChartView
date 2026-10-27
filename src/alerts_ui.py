"""Ventanas de alertas: editor de una alerta y administrador con historial."""
from __future__ import annotations

import datetime as dt
from copy import deepcopy

from PySide6 import QtCore, QtGui, QtWidgets

from . import data
from .alerts import CHECK_EVERY, REPEAT, TYPES, describe, new_alert


class AlertEditDialog(QtWidgets.QDialog):
    def __init__(self, alert: dict, last_price: float | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alerta")
        self.setMinimumWidth(460)
        self.alert = deepcopy(alert)
        self.last_price = last_price
        form = QtWidgets.QFormLayout(self)

        self.symbol = QtWidgets.QLineEdit(self.alert["symbol"])
        self.symbol.setPlaceholderText("AAPL, WALMEX.MX, BTC-USD, MACRO:UST10Y…")
        self.type = QtWidgets.QComboBox()
        for k, (label, *_rest) in TYPES.items():
            self.type.addItem(label, k)
        self.type.setCurrentIndex(max(0, self.type.findData(self.alert["type"])))
        self.value = QtWidgets.QDoubleSpinBox()
        ref = abs(last_price or self.alert["value"] or 1)
        self.value.setDecimals(2 if ref >= 1 or TYPES[self.alert["type"]][1] != "price" else 6)
        self.value.setRange(-1e15, 1e15)
        self.value.setGroupSeparatorShown(True)
        self.value.setValue(self.alert["value"])
        self.interval = QtWidgets.QComboBox()
        self.interval.addItems([k for k, v in data.INTERVALS.items() if k not in ("1 minuto", "2 minutos")])
        self.interval.setCurrentText(self.alert.get("interval", "1 día"))
        self.length = QtWidgets.QSpinBox()
        self.length.setRange(2, 500)
        self.length.setValue(int(self.alert.get("length", 14)))
        self.ma = QtWidgets.QComboBox()
        self.ma.addItems(["SMA", "EMA"])
        self.ma.setCurrentText(self.alert.get("ma", "SMA"))
        self.repeat = QtWidgets.QComboBox()
        for k, label in REPEAT.items():
            self.repeat.addItem(label, k)
        self.repeat.setCurrentIndex(max(0, self.repeat.findData(self.alert.get("repeat", "once"))))
        self.note = QtWidgets.QLineEdit(self.alert.get("note", ""))
        self.note.setPlaceholderText("Opcional: aparece en la notificación")
        self.preview = QtWidgets.QLabel()
        self.preview.setStyleSheet("color:#9598a1;")

        form.addRow("Símbolo:", self.symbol)
        form.addRow("Condición:", self.type)
        form.addRow("Valor:", self.value)
        form.addRow("Intervalo:", self.interval)
        form.addRow("Periodo:", self.length)
        form.addRow("Media:", self.ma)
        form.addRow("Repetir:", self.repeat)
        form.addRow("Nota:", self.note)
        form.addRow(self.preview)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.Save).setText("Guardar")
        bb.button(QtWidgets.QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._form = form

        self._last_family = None
        self.type.currentIndexChanged.connect(self._on_type)
        for w in (self.value, self.length):
            w.valueChanged.connect(self._update_preview)
        for w in (self.interval, self.ma):
            w.currentTextChanged.connect(self._update_preview)
        self._on_type(initial=True)

    def _on_type(self, *_a, initial=False):
        k = self.type.currentData()
        fam = TYPES[k][1]
        for w, show in ((self.value, fam != "ma"), (self.interval, fam in ("rsi", "ma")),
                        (self.length, fam in ("rsi", "ma")), (self.ma, fam == "ma")):
            self._form.setRowVisible(w, show)
        # valores sugeridos al cambiar de familia (precio actual, ±3 %, RSI 70/30, media de 50)
        if not initial and fam != self._last_family:
            if fam == "price" and self.last_price:
                self.value.setValue(self.last_price)
            elif fam == "change":
                self.value.setValue(3.0)
            elif fam == "rsi":
                self.value.setValue(70 if TYPES[k][2] == ">" else 30)
                self.length.setValue(14)
            elif fam == "ma":
                self.length.setValue(50)
        elif not initial and fam == "rsi":
            self.value.setValue(70 if TYPES[k][2] == ">" else 30)
        self._last_family = fam
        self._update_preview()

    def _collect(self) -> dict:
        a = dict(self.alert)
        a.update({
            "symbol": self.symbol.text().strip().upper(), "type": self.type.currentData(),
            "value": self.value.value(), "interval": self.interval.currentText(),
            "length": self.length.value(), "ma": self.ma.currentText(),
            "repeat": self.repeat.currentData(), "note": self.note.text().strip(),
        })
        return a

    def _update_preview(self, *_):
        a = self._collect()
        self.preview.setText(f"Avisará cuando {a['symbol'] or '…'} {describe(a)}")

    def _accept(self):
        a = self._collect()
        if not a["symbol"]:
            QtWidgets.QMessageBox.warning(self, "Alerta", "Escribe un símbolo.")
            return
        a["enabled"] = True
        a["last_error"] = None
        self.alert = a
        self.accept()


class AlertsWindow(QtWidgets.QDialog):
    """Lista de alertas, historial y opciones. No es modal: se puede dejar abierta."""
    changed = QtCore.Signal()
    openSymbol = QtCore.Signal(str)

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setWindowTitle("Alertas")
        self.resize(900, 560)
        lay = QtWidgets.QVBoxLayout(self)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Símbolo", "Condición", "Repetir", "Último valor", "Estado"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QtWidgets.QHeaderView.Stretch)
        for c in (0, 1, 3, 4):
            hdr.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.cellDoubleClicked.connect(lambda r, c: self.edit() if c else None)
        self.table.cellChanged.connect(self._on_check)
        lay.addWidget(self.table, 3)

        row = QtWidgets.QHBoxLayout()
        for text, fn in (("➕ Nueva", self.add), ("✎ Editar", self.edit), ("🗑 Eliminar", self.remove),
                         ("Abrir gráfica", self._open), ("🔔 Probar aviso", main.test_notification),
                         ("⟳ Revisar ahora", main.check_alerts)):
            b = QtWidgets.QPushButton(text)
            # lambda: «clicked» manda un bool (checked) que no debe llegar como argumento
            b.clicked.connect(lambda _checked=False, fn=fn: fn())
            row.addWidget(b)
        lay.addLayout(row)

        opts = QtWidgets.QHBoxLayout()
        opts.addWidget(QtWidgets.QLabel("Revisar cada:"))
        self.every = QtWidgets.QComboBox()
        for label, secs in CHECK_EVERY:
            self.every.addItem(label, secs)
        self.every.setCurrentIndex(max(0, self.every.findData(main.cfg.get("alerts_every", 60))))
        self.every.currentIndexChanged.connect(self._on_every)
        opts.addWidget(self.every)
        self.tray = QtWidgets.QCheckBox("Al cerrar la ventana, seguir vigilando desde la bandeja del sistema")
        self.tray.setChecked(bool(main.cfg.get("alerts_tray_on_close", False)))
        self.tray.toggled.connect(self._on_tray)
        opts.addWidget(self.tray)
        opts.addStretch(1)
        lay.addLayout(opts)

        lay.addWidget(QtWidgets.QLabel("<b>Historial</b>"))
        self.history = QtWidgets.QListWidget()
        self.history.itemDoubleClicked.connect(
            lambda it: self.openSymbol.emit(it.data(QtCore.Qt.UserRole)) if it.data(QtCore.Qt.UserRole) else None)
        lay.addWidget(self.history, 2)
        clear = QtWidgets.QPushButton("Limpiar historial")
        clear.clicked.connect(self._clear_history)
        lay.addWidget(clear, 0, QtCore.Qt.AlignRight)
        self.refresh()

    @property
    def alerts(self) -> list[dict]:
        return self.main.cfg.setdefault("alerts", [])

    def refresh(self):
        from .chart import fmt_price

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for a in self.alerts:
            r = self.table.rowCount()
            self.table.insertRow(r)
            chk = QtWidgets.QTableWidgetItem()
            chk.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            chk.setCheckState(QtCore.Qt.Checked if a.get("enabled") else QtCore.Qt.Unchecked)
            chk.setToolTip("Activa / pausada")
            chk.setData(QtCore.Qt.UserRole, a["id"])
            self.table.setItem(r, 0, chk)
            v = a.get("last_value")
            fam = TYPES[a["type"]][1]
            vtxt = "—" if v is None else (f"{v:+.2f}%" if fam == "change" else fmt_price(v))
            if a.get("last_error"):
                state, color = f"⚠ {a['last_error']}", "#ff9800"
            elif a.get("enabled"):
                state, color = "Vigilando", "#26a69a"
            else:
                state, color = "Pausada", "#787b86"
            if a.get("last_triggered"):
                t = dt.datetime.fromisoformat(a["last_triggered"])
                state += f"  ·  disparada {a.get('count', 1)}× (última {t:%d/%m %H:%M})"
            cells = [a["symbol"], describe(a), "Una vez" if a.get("repeat") == "once" else "Cada vez", vtxt, state]
            for c, text in enumerate(cells, start=1):
                it = QtWidgets.QTableWidgetItem(text)
                if c == 5:
                    it.setForeground(QtGui.QColor(color))
                if a.get("note") and c == 2:
                    it.setToolTip(a["note"])
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)

        self.history.clear()
        for h in reversed(self.main.cfg.get("alert_history", [])):
            it = QtWidgets.QListWidgetItem(f"{h['time'][:16].replace('T', ' ')}   {h['text']}")
            it.setData(QtCore.Qt.UserRole, h.get("symbol"))
            self.history.addItem(it)

    def _row_alert(self, row=None):
        row = self.table.currentRow() if row is None else row
        if row < 0:
            return None
        did = self.table.item(row, 0).data(QtCore.Qt.UserRole)
        return next((a for a in self.alerts if a["id"] == did), None)

    def _on_check(self, row, col):
        if col != 0:
            return
        a = self._row_alert(row)
        if a:
            a["enabled"] = self.table.item(row, 0).checkState() == QtCore.Qt.Checked
            if a["enabled"]:
                a["last_error"] = None
                self.main.reset_alert_state(a["id"])
            self.changed.emit()
            self.refresh()

    def add(self, preset: dict | None = None):
        if not isinstance(preset, dict):
            preset = None
        price = self.main.last_price()
        a = preset or new_alert(self.main.symbol or "", "price_cross_up", price or 0)
        dlg = AlertEditDialog(a, price if (preset is None or preset["symbol"] == self.main.symbol) else None, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.alerts.append(dlg.alert)
            self.changed.emit()
            self.refresh()

    def edit(self):
        a = self._row_alert()
        if not a:
            return
        dlg = AlertEditDialog(a, self.main.last_price() if a["symbol"] == self.main.symbol else None, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            a.update(dlg.alert)
            self.main.reset_alert_state(a["id"])
            self.changed.emit()
            self.refresh()

    def remove(self):
        a = self._row_alert()
        if a and QtWidgets.QMessageBox.question(
                self, "Eliminar alerta", f"¿Eliminar la alerta de {a['symbol']} ({describe(a)})?") \
                == QtWidgets.QMessageBox.Yes:
            self.alerts.remove(a)
            self.changed.emit()
            self.refresh()

    def _open(self):
        a = self._row_alert()
        if a:
            self.openSymbol.emit(a["symbol"])

    def _on_every(self, *_):
        self.main.cfg["alerts_every"] = self.every.currentData()
        self.changed.emit()

    def _on_tray(self, on):
        self.main.cfg["alerts_tray_on_close"] = on
        self.changed.emit()

    def _clear_history(self):
        self.main.cfg["alert_history"] = []
        self.changed.emit()
        self.refresh()
