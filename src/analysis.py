"""Panel de Análisis: reportes trimestrales, estimaciones y opinión de analistas."""
from __future__ import annotations

import pandas as pd
from PySide6 import QtCore, QtGui, QtWidgets

from .chart import DOWN, UP, fmt_big, fmt_price

MUTED = "#9598a1"
REC_ROWS = [("strongBuy", "Compra fuerte", "#089981"), ("buy", "Compra", "#26a69a"), ("hold", "Mantener", "#787b86"),
            ("sell", "Venta", "#ef5350"), ("strongSell", "Venta fuerte", "#b71c1c")]
ACTIONS = {"up": "Sube", "down": "Baja", "main": "Mantiene", "init": "Inicia", "reit": "Reitera"}
PERIOD_NAMES = {"0q": "Trimestre actual", "+1q": "Trimestre siguiente", "0y": "Año actual", "+1y": "Año siguiente"}


def _pct(v, digits=1):
    return "—" if v is None else f"{v:+.{digits}f}%"


def _money(v):
    return "—" if v is None else (fmt_big(v) if abs(v) >= 1e6 else fmt_price(v))


def _when(d: pd.Timestamp) -> str:
    days = (d.normalize() - pd.Timestamp.now().normalize()).days
    return {0: "hoy", 1: "mañana"}.get(days, f"en {days} días" if days > 0 else f"hace {-days} días")


class RecsBars(QtWidgets.QWidget):
    """Barras horizontales con cuántos analistas recomiendan comprar, mantener o vender."""

    def __init__(self, counts: dict, parent=None):
        super().__init__(parent)
        self.counts = counts
        self.setMinimumHeight(len(REC_ROWS) * 20 + 4)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        total_max = max(self.counts.values() or [1]) or 1
        label_w, count_w = 92, 26
        bar_w = max(10, self.width() - label_w - count_w - 8)
        for k, (key, label, color) in enumerate(REC_ROWS):
            y = 2 + k * 20
            n = self.counts.get(key, 0)
            p.setPen(QtGui.QColor(MUTED))
            p.drawText(QtCore.QRectF(0, y, label_w, 16), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, label)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#2a2e39"))
            p.drawRoundedRect(QtCore.QRectF(label_w, y + 3, bar_w, 10), 3, 3)
            if n:
                p.setBrush(QtGui.QColor(color))
                p.drawRoundedRect(QtCore.QRectF(label_w, y + 3, bar_w * n / total_max, 10), 3, 3)
            p.setPen(QtGui.QColor("#d1d4dc"))
            p.drawText(QtCore.QRectF(label_w + bar_w + 6, y, count_w, 16), QtCore.Qt.AlignVCenter, str(n))


class TargetRange(QtWidgets.QWidget):
    """Rango de precio objetivo (bajo–alto) con el promedio y el precio actual marcados."""

    def __init__(self, low, high, mean, current, parent=None):
        super().__init__(parent)
        self.low, self.high, self.mean, self.current = low, high, mean, current
        self.setMinimumHeight(46)

    def paintEvent(self, _ev):
        if self.low is None or self.high is None or self.high <= self.low:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        lo = min(self.low, self.current or self.low)
        hi = max(self.high, self.current or self.high)
        x0, x1, y = 8, self.width() - 8, 20

        def px(v):
            return x0 + (x1 - x0) * (v - lo) / (hi - lo)

        def pen(color):
            pn = QtGui.QPen(QtGui.QColor(color), 4)
            pn.setCapStyle(QtCore.Qt.RoundCap)
            return pn

        p.setPen(pen("#2a2e39"))
        p.drawLine(QtCore.QPointF(x0, y), QtCore.QPointF(x1, y))
        p.setPen(pen("#2962ff"))
        p.drawLine(QtCore.QPointF(px(self.low), y), QtCore.QPointF(px(self.high), y))
        f = p.font()
        f.setPointSizeF(f.pointSizeF() - 1)
        p.setFont(f)
        for v, color, label, above in ((self.mean, "#ff9800", "promedio", True), (self.current, "#ffffff", "actual", False)):
            if v is None:
                continue
            x = px(v)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(color))
            p.drawEllipse(QtCore.QPointF(x, y), 5, 5)
            p.setPen(QtGui.QColor(color))
            rect = QtCore.QRectF(x - 50, 0 if above else y + 8, 100, 12)
            p.drawText(rect, QtCore.Qt.AlignCenter, label)


class AnalysisPanel(QtWidgets.QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setMinimumWidth(330)
        self._body = QtWidgets.QWidget()
        self._lay = QtWidgets.QVBoxLayout(self._body)
        self._lay.setContentsMargins(8, 6, 8, 8)
        self._lay.setSpacing(4)
        self.setWidget(self._body)
        self.symbol = None
        self.set_message("Abre una acción para ver sus reportes y la opinión de los analistas.")

    # ------------------------------------------------------------------ util
    def _clear(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _label(self, html: str, size=None, color=None, wrap=True):
        lab = QtWidgets.QLabel(html)
        lab.setTextFormat(QtCore.Qt.RichText)
        lab.setWordWrap(wrap)
        style = []
        if size:
            style.append(f"font-size:{size}px")
        if color:
            style.append(f"color:{color}")
        lab.setStyleSheet(";".join(style))
        self._lay.addWidget(lab)
        return lab

    def _section(self, title: str):
        self._lay.addSpacing(8)
        self._label(f"<b>{title}</b>", size=13, color="#d1d4dc")
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color:#2a2e39;")
        self._lay.addWidget(line)

    def _table(self, headers, rows, colors=None, align_right_from=1):
        t = QtWidgets.QTableWidget(len(rows), len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().hide()
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        t.setFocusPolicy(QtCore.Qt.NoFocus)
        t.setShowGrid(False)
        t.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        t.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                it = QtWidgets.QTableWidgetItem(str(text))
                if c >= align_right_from:
                    it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                if colors and colors[r] and colors[r][c]:
                    it.setForeground(QtGui.QColor(colors[r][c]))
                t.setItem(r, c, it)
        t.resizeRowsToContents()
        height = t.horizontalHeader().height() + sum(t.rowHeight(r) for r in range(len(rows))) + 4
        t.setFixedHeight(height)
        self._lay.addWidget(t)
        return t

    # --------------------------------------------------------------- estados
    def set_message(self, text: str, symbol: str | None = None):
        self.symbol = symbol
        self._clear()
        if symbol:
            self._label(f"<b style='font-size:15px'>{symbol}</b>")
        self._label(text, color=MUTED)
        self._lay.addStretch(1)

    def set_loading(self, symbol: str):
        self.set_message("Cargando reportes y opinión de analistas…", symbol)

    def set_data(self, symbol: str, d: dict, name: str = "", price: float | None = None):
        if not d:
            self.set_message("Este símbolo no tiene reportes trimestrales ni cobertura de analistas "
                             "(cripto, índices, materias primas y series macro no los tienen).", symbol)
            return
        self.symbol = symbol
        self._clear()
        cur = d.get("currency", "")
        self._label(f"<b style='font-size:15px'>{symbol}</b>&nbsp; <span style='color:{MUTED}'>{name}</span>")

        # ---------------- próximo reporte
        nxt = d.get("next")
        eps_q, rev_q = d.get("eps_est", {}).get("0q", {}), d.get("rev_est", {}).get("0q", {})
        if nxt:
            self._section("Próximo reporte")
            when = nxt["date"]
            self._label(f"<b>{when:%d/%m/%Y}</b> &nbsp;<span style='color:#ff9800'>({_when(when)})</span>")
            if eps_q.get("avg") is not None:
                self._label(f"Utilidad por acción estimada: <b>{fmt_price(eps_q['avg'])}</b> {cur}"
                            f"<br><span style='color:{MUTED}'>rango {fmt_price(eps_q.get('low'))} – {fmt_price(eps_q.get('high'))}"
                            f" · {int(eps_q.get('numberOfAnalysts') or 0)} analistas · hace un año {fmt_price(eps_q.get('yearAgoEps'))}"
                            f" ({_pct((eps_q.get('growth') or 0) * 100)})</span>")
            if rev_q.get("avg") is not None:
                self._label(f"Ingresos estimados: <b>{_money(rev_q['avg'])}</b> {cur}"
                            f"<br><span style='color:{MUTED}'>rango {_money(rev_q.get('low'))} – {_money(rev_q.get('high'))}"
                            f" · crecimiento {_pct((rev_q.get('growth') or 0) * 100)}</span>")

        # ---------------- historial
        hist = d.get("history", [])[:8]
        if hist:
            self._section("Reportes: estimado contra real")
            beats = sum(1 for h in hist if (h["surprise"] or 0) > 0)
            self._label(f"Superó lo estimado en <b>{beats} de {len(hist)}</b> reportes recientes.", color=MUTED)
            rows, colors = [], []
            for h in hist:
                s = h["surprise"]
                col = UP if (s or 0) > 0 else DOWN if (s or 0) < 0 else None
                rows.append([f"{h['date']:%d/%m/%Y}", fmt_price(h["estimate"]) if h["estimate"] is not None else "—",
                             fmt_price(h["reported"]), _pct(s)])
                colors.append([None, None, col, col])
            self._table(["Fecha", "Estimado", "Real", "Sorpresa"], rows, colors)

        # ---------------- estimaciones
        est_rows = []
        for key, label in PERIOD_NAMES.items():
            e, r = d.get("eps_est", {}).get(key), d.get("rev_est", {}).get(key)
            if not e and not r:
                continue
            est_rows.append([label, fmt_price(e.get("avg")) if e and e.get("avg") is not None else "—",
                             _pct((e.get("growth") or 0) * 100) if e else "—",
                             _money(r.get("avg")) if r else "—", _pct((r.get("growth") or 0) * 100) if r else "—"])
        if est_rows:
            self._section("Estimaciones")
            self._table(["Periodo", "UPA", "Crec.", "Ingr.", "Crec."], est_rows)
            self._label("UPA: utilidad por acción · Ingr.: ingresos · Crec.: contra el mismo periodo del año anterior.",
                        size=11, color="#5d606b")

        # ---------------- analistas
        recs = d.get("recs", {})
        now = recs.get("0m")
        if now and sum(now.values()):
            self._section("Recomendaciones de analistas")
            total = sum(now.values())
            score = sum(n * w for n, w in zip((now[k] for k, *_ in REC_ROWS), (1, 2, 3, 4, 5))) / total
            consensus = ("Compra fuerte" if score <= 1.5 else "Compra" if score <= 2.5 else "Mantener" if score <= 3.5
                         else "Venta" if score <= 4.5 else "Venta fuerte")
            color = UP if score <= 2.5 else DOWN if score > 3.5 else "#d1d4dc"
            text = f"Consenso: <b style='color:{color}'>{consensus}</b> &nbsp;<span style='color:{MUTED}'>({total} analistas)</span>"
            old = recs.get("-3m")
            if old and sum(old.values()):
                buy_now = (now["strongBuy"] + now["buy"]) / total * 100
                buy_old = (old["strongBuy"] + old["buy"]) / sum(old.values()) * 100
                text += (f"<br><span style='color:{MUTED}'>Recomiendan comprar: {buy_now:.0f}% "
                         f"(hace 3 meses {buy_old:.0f}%)</span>")
            self._label(text)
            self._lay.addWidget(RecsBars(now))

        # ---------------- precio objetivo
        tg = d.get("targets") or {}
        if tg.get("mean") is not None:
            self._section("Precio objetivo a 12 meses")
            current = price if price is not None else tg.get("current")
            rows, colors = [], []
            for key, label in (("low", "Bajo"), ("mean", "Promedio"), ("median", "Mediana"), ("high", "Alto")):
                v = tg.get(key)
                if v is None:
                    continue
                pot = (v / current - 1) * 100 if current else None
                rows.append([label, fmt_price(v), _pct(pot)])
                colors.append([None, "#ff9800" if key == "mean" else None, UP if (pot or 0) >= 0 else DOWN])
            if current:
                rows.insert(0, ["Actual", fmt_price(current), ""])
                colors.insert(0, [None, "#ffffff", None])
            self._table(["", f"Precio ({cur})" if cur else "Precio", "Potencial"], rows, colors)
            self._lay.addWidget(TargetRange(tg.get("low"), tg.get("high"), tg.get("mean"), current))

        # ---------------- cambios de calificación
        changes = d.get("changes", [])[:10]
        if changes:
            self._section("Últimos cambios de calificación")
            rows, colors = [], []
            for c in changes:
                act = ACTIONS.get(c["action"], c["action"])
                grade = c["to"] if not c["from"] or c["from"] == c["to"] else f"{c['from']} → {c['to']}"
                # Yahoo pone 0 cuando la firma no publicó precio objetivo
                tgt = "" if not c["target"] else (fmt_price(c["target"]) if not c["prior"]
                                                  else f"{fmt_price(c['prior'])} → {fmt_price(c['target'])}")
                rows.append([f"{c['date']:%d/%m/%y}", f"{c['firm']}\n{act}: {grade}", tgt])
                col = UP if c["action"] == "up" or (c["target"] or 0) > (c["prior"] or 1e18) else \
                    DOWN if c["action"] == "down" or (c["prior"] or 0) > (c["target"] or 1e18) else None
                colors.append([None, None, col])
            self._table(["Fecha", "Firma y calificación", "Objetivo"], rows, colors, align_right_from=2)

        div = d.get("dividend") or {}
        if div.get("ex"):
            self._section("Dividendos")
            self._label(f"Fecha ex-dividendo: <b>{div['ex']:%d/%m/%Y}</b>"
                        + (f" · pago {div['pay']:%d/%m/%Y}" if div.get("pay") else ""))

        self._lay.addSpacing(6)
        self._label("Fuente: Yahoo Finance. Los datos pueden tardar en actualizarse tras un reporte.",
                    size=11, color="#5d606b")
        self._lay.addStretch(1)
