"""Alertas de precio e indicadores con aviso en el escritorio.

Cada alerta es un diccionario guardado en la configuración:
  {"id", "symbol", "type", "value", "interval", "length", "ma", "repeat", "note",
   "enabled", "created", "last_triggered", "count", "last_value", "last_error"}

La revisión corre en segundo plano cada cierto tiempo. Una alerta se dispara al
pasar de "no se cumple" a "se cumple":
  * las de cruce solo cuando ocurre el cruce (no si ya estaba del otro lado al crearla);
  * las de nivel ("por encima de", RSI mayor que…) también si ya se cumple al revisarla
    por primera vez.
Con "Cada vez", se vuelve a armar cuando la condición deja de cumplirse.
"""
from __future__ import annotations

import datetime as dt
import uuid

import numpy as np

from . import data
from .indicators import ema, rsi, sma

# clave: (texto, familia, operador, ¿es cruce?)
TYPES = {
    "price_cross_up": ("El precio cruza hacia arriba de", "price", ">", True),
    "price_cross_down": ("El precio cruza hacia abajo de", "price", "<", True),
    "price_above": ("El precio está por encima de", "price", ">", False),
    "price_below": ("El precio está por debajo de", "price", "<", False),
    "change_up": ("El cambio del día sube más de (%)", "change", ">", False),
    "change_down": ("El cambio del día cae más de (%)", "change", "<", False),
    "rsi_above": ("El RSI sube por encima de", "rsi", ">", True),
    "rsi_below": ("El RSI baja por debajo de", "rsi", "<", True),
    "ma_cross_up": ("El precio cruza hacia arriba de su media", "ma", ">", True),
    "ma_cross_down": ("El precio cruza hacia abajo de su media", "ma", "<", True),
}
PRICE_LEVEL_TYPES = {k for k, t in TYPES.items() if t[1] == "price"}
REPEAT = {"once": "Una vez (luego se desactiva)", "every": "Cada vez que se cumpla"}
CHECK_EVERY = [("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300), ("15 min", 900)]


def new_alert(symbol: str, type_: str = "price_cross_up", value: float = 0.0, **kw) -> dict:
    a = {
        "id": uuid.uuid4().hex, "symbol": symbol, "type": type_, "value": float(value),
        "interval": "1 día", "length": 14, "ma": "SMA", "repeat": "once", "note": "",
        "enabled": True, "created": dt.datetime.now().isoformat(timespec="seconds"),
        "last_triggered": None, "count": 0, "last_value": None, "last_error": None,
    }
    a.update(kw)
    return a


def describe(a: dict) -> str:
    """Texto corto de la condición, p. ej. «cruza ↑ 250.00» o «RSI 14 (1 día) > 70»."""
    from .chart import fmt_price

    fam, op = TYPES[a["type"]][1], TYPES[a["type"]][2]
    arrow = "↑" if op == ">" else "↓"
    v = a["value"]
    if fam == "price":
        verb = "cruza" if TYPES[a["type"]][3] else ("arriba de" if op == ">" else "debajo de")
        return f"{verb} {arrow} {fmt_price(v)}" if TYPES[a["type"]][3] else f"{verb} {fmt_price(v)}"
    if fam == "change":
        return f"cambio del día {'≥ +' if op == '>' else '≤ −'}{abs(v):g}%"
    if fam == "rsi":
        return f"RSI {a['length']} ({a['interval']}) {op} {v:g}"
    return f"cruza {arrow} {a['ma']} {a['length']} ({a['interval']})"


# --------------------------------------------------------------------------- #
# Evaluación (corre en un hilo; no toca la interfaz)
# --------------------------------------------------------------------------- #
def _cond(op: str, x: float, ref: float) -> bool:
    return x > ref if op == ">" else x < ref


def evaluate(alerts: list[dict]) -> dict[str, tuple]:
    """Devuelve {id: (se_cumple, valor_actual, error)} para las alertas activas."""
    out: dict[str, tuple] = {}
    active = [a for a in alerts if a.get("enabled")]
    quick = [a for a in active if TYPES[a["type"]][1] in ("price", "change")]
    quotes = {}
    if quick:
        try:
            quotes = data.quotes(sorted({a["symbol"] for a in quick}))
        except Exception as e:  # noqa: BLE001
            for a in quick:
                out[a["id"]] = (None, None, f"Sin conexión: {e}")
    for a in quick:
        if a["id"] in out:
            continue
        q = quotes.get(a["symbol"])
        if q is None:
            out[a["id"]] = (None, None, "No se obtuvo el precio")
            continue
        fam, op = TYPES[a["type"]][1], TYPES[a["type"]][2]
        x = q[0] if fam == "price" else q[1]
        if x is None or not np.isfinite(x):
            out[a["id"]] = (None, None, "Sin dato")
            continue
        if fam == "price":
            ref = a["value"]
        else:  # «sube más de 3%» -> > +3 · «cae más de 3%» -> < −3
            ref = abs(a["value"]) if op == ">" else -abs(a["value"])
        out[a["id"]] = (_cond(op, x, ref), float(x), None)

    # alertas de indicadores: una descarga por (símbolo, intervalo)
    groups: dict[tuple, list] = {}
    for a in active:
        if TYPES[a["type"]][1] in ("rsi", "ma"):
            groups.setdefault((a["symbol"], a["interval"]), []).append(a)
    for (sym, interval), items in groups.items():
        try:
            df, _, _ = data.load(sym, interval, data.INTERVALS[interval][3])
        except Exception as e:  # noqa: BLE001
            for a in items:
                out[a["id"]] = (None, None, str(e)[:120])
            continue
        close = df["Close"]
        for a in items:
            fam, op = TYPES[a["type"]][1], TYPES[a["type"]][2]
            n = int(a.get("length", 14))
            if fam == "rsi":
                x = float(rsi(close, n).iloc[-1])
                ok = np.isfinite(x)
                out[a["id"]] = (_cond(op, x, a["value"]) if ok else None, x if ok else None,
                                None if ok else "Pocos datos para el RSI")
            else:
                m = (ema if a.get("ma") == "EMA" else sma)(close, n).iloc[-1]
                c = float(close.iloc[-1])
                ok = np.isfinite(m)
                out[a["id"]] = (_cond(op, c, float(m)) if ok else None, c if ok else None,
                                None if ok else "Pocos datos para la media")
    return out


def transitions(alerts: list[dict], results: dict, prev_state: dict) -> list[dict]:
    """Aplica los resultados y devuelve las alertas que se dispararon (modifica ``alerts`` y ``prev_state``)."""
    fired = []
    now = dt.datetime.now().isoformat(timespec="seconds")
    for a in alerts:
        if not a.get("enabled") or a["id"] not in results:
            continue
        state, value, error = results[a["id"]]
        a["last_error"] = error
        if state is None:
            continue
        a["last_value"] = value
        before = prev_state.get(a["id"])
        prev_state[a["id"]] = state
        is_cross = TYPES[a["type"]][3]
        if state and (before is False or (before is None and not is_cross)):
            a["last_triggered"] = now
            a["count"] = int(a.get("count", 0)) + 1
            if a.get("repeat", "once") == "once":
                a["enabled"] = False
            fired.append(a)
    return fired


def message(a: dict) -> str:
    from .chart import fmt_price

    fam = TYPES[a["type"]][1]
    v = a.get("last_value")
    now = "—" if v is None else (f"{v:+.2f}%" if fam == "change" else (f"{v:.1f}" if fam == "rsi" else fmt_price(v)))
    what = {"price": "precio", "change": "cambio", "rsi": "RSI", "ma": "precio"}[fam]
    text = f"{a['symbol']}: {describe(a)}  ·  {what} actual {now}"
    if a.get("note"):
        text += f"\n{a['note']}"
    return text
