"""Buscador único de símbolos con sugerencias mientras escribes.

Combina sugerencias locales (listas de seguimiento, populares, catálogo macro,
series importadas), que aparecen al instante, con la búsqueda de Yahoo Finance,
que llega unos milisegundos después. Elegir una sugerencia emite ``symbolChosen``.
"""
from __future__ import annotations

import time
import unicodedata
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from . import data
from .workers import run_task

SYMBOL_ROLE = QtCore.Qt.UserRole + 1


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


class SymbolSearch(QtWidgets.QLineEdit):
    symbolChosen = QtCore.Signal(str, bool)  # símbolo, ¿buscar por nombre si no existe?

    def __init__(self, local_symbols: Callable[[], dict[str, str]], parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Buscar: WALMEX, Apple, bitcoin… · ratio AAPL/^GSPC · diferencial A - B")
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(260)
        self.setToolTip("Escribe un símbolo o un nombre y elige una sugerencia (Ctrl+L)")
        self._local = local_symbols  # {símbolo: descripción}
        self.model = QtGui.QStandardItemModel(self)
        self.comp = QtWidgets.QCompleter(self.model, self)
        self.comp.setCompletionMode(QtWidgets.QCompleter.UnfilteredPopupCompletion)
        self.comp.setCompletionRole(SYMBOL_ROLE)
        self.comp.setMaxVisibleItems(12)
        self.comp.activated[str].connect(self._on_activated)
        self.setCompleter(self.comp)
        self._timer = QtCore.QTimer(self, singleShot=True, interval=300)
        self._timer.timeout.connect(self._remote_search)
        self._req = 0
        self._shown: list[str] = []
        self._chosen = ("", 0.0)
        self.textEdited.connect(self._on_edit)
        self.returnPressed.connect(self._on_enter)

    # ------------------------------------------------------------ sugerencias
    def _local_matches(self, q: str) -> list[tuple[str, str]]:
        fq = fold(q)
        hits = []
        for sym, desc in self._local().items():
            fs, fd = fold(sym), fold(desc)
            if fs.startswith(fq) or fs.split(":")[-1].startswith(fq):
                hits.append((0, sym, desc))
            elif fq in fs or fq in fd:
                hits.append((1, sym, desc))
        hits.sort(key=lambda h: (h[0], len(h[1])))
        return [(s, d) for _, s, d in hits[:8]]

    def _show(self, rows: list[tuple[str, str]]):
        self.model.clear()
        self._shown = []
        for sym, desc in rows:
            if sym in self._shown:
                continue
            it = QtGui.QStandardItem(f"{sym}    {desc}" if desc else sym)
            it.setData(sym, SYMBOL_ROLE)
            it.setToolTip(desc)
            self.model.appendRow(it)
            self._shown.append(sym)
        if self._shown and self.hasFocus():
            self.comp.complete()
        elif not self._shown:
            self.comp.popup().hide()

    def _on_edit(self, text: str):
        q = text.strip()
        self._req += 1
        if len(q) < 1:
            self._show([])
            return
        if data.parse_expr(q):  # «AAPL/^GSPC», «GC=F/SI=F»: no hay nada que sugerir, se abre con Enter
            self._timer.stop()
            self._show([])
            return
        self._local_rows = self._local_matches(q)
        self._show(self._local_rows)
        self._timer.start()

    def _remote_search(self):
        q = self.text().strip()
        if len(q) < 2:
            return
        req = self._req
        run_task(data.search, q, on_done=lambda rows, req=req: self._on_remote(req, rows),
                 on_fail=lambda _e: None)

    def _on_remote(self, req: int, rows: list[dict]):
        if req != self._req:  # el usuario siguió escribiendo
            return
        remote = []
        for r in rows:
            desc = " · ".join(x for x in (r.get("name"), r.get("exchange"), r.get("type")) if x)
            remote.append((r["symbol"], desc))
        self._show(getattr(self, "_local_rows", []) + remote)

    # --------------------------------------------------------------- elegir
    def _on_activated(self, sym: str):
        self._chosen = (sym, time.monotonic())
        self.setText(sym)
        self.symbolChosen.emit(sym, False)
        self.clearFocus()

    def _on_enter(self):
        text = self.text().strip()
        sym, t = self._chosen
        if not text or (text == sym and time.monotonic() - t < 0.5):
            return  # ya se eligió desde la lista
        up = " ".join(text.upper().split())  # normaliza espacios: «a  -  b» -> «A - B»
        if up in self._shown:
            choice, by_name = up, False
        elif data.parse_expr(up) or any(ch in up for ch in ".-^=:"):  # ratio/diferencial o símbolo completo
            choice, by_name = up, False
        elif self._shown and not self._timer.isActive():
            choice, by_name = self._shown[0], False  # la mejor sugerencia
        else:
            choice, by_name = up, True  # si no existe tal cual, se busca por nombre
        self.comp.popup().hide()
        self.setText(choice)
        self.symbolChosen.emit(choice, by_name)
        self.clearFocus()

    def keyPressEvent(self, ev):
        if ev.key() == QtCore.Qt.Key_Escape:
            self.comp.popup().hide()
            self.clearFocus()
            return
        super().keyPressEvent(ev)
