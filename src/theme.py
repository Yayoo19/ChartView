"""Temas de color (oscuro y claro) y colores de las velas.

El código está escrito con los colores del tema oscuro. El tema claro no repite cada estilo:
traduce esos colores con una tabla. Por eso todo estilo pasa por aquí:

- ``style(widget, css)`` en lugar de ``widget.setStyleSheet(css)``: recuerda el estilo original
  para poder volver a traducirlo al cambiar de tema sin reiniciar.
- ``css(texto)`` para hojas de estilo y HTML (encabezados, leyendas).
- ``c(color)`` para un color suelto que se dibuja sobre el fondo (líneas, textos de la gráfica).
"""
from __future__ import annotations

import re

from PySide6 import QtGui, QtWidgets

THEMES = ["Oscuro", "Claro"]
# Texto que debe resaltar sobre el fondo: casi blanco en el tema oscuro y casi negro en el claro.
# El blanco puro (#ffffff) no se traduce: se usa sobre fondos de color (etiquetas, botones activos).
STRONG = "#fefefe"
UP_DEFAULT, DOWN_DEFAULT = "#26a69a", "#ef5350"

_LIGHT = {
    "#131722": "#ffffff",  # fondo de la gráfica
    "#1e222d": "#f0f3fa",  # barras y paneles
    "#2a2e39": "#e0e3eb",  # bordes, botones
    "#363a45": "#c9cdd6",
    "#d1d4dc": "#131722",  # texto
    "#b2b5be": "#434651",
    "#9598a1": "#5d606b",
    "#5d606b": "#a3a6af",  # texto apagado
    "#4a4e59": "#c1c4cd",
    STRONG: "#0c0e15",
}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")

name = "Oscuro"
_candles = {"up": UP_DEFAULT, "down": DOWN_DEFAULT}


def configure(cfg: dict):
    """Lee el tema y los colores de las velas de la configuración."""
    global name
    name = cfg.get("theme") if cfg.get("theme") in THEMES else "Oscuro"
    saved = cfg.get("candle_colors") or {}
    _candles["up"] = saved.get("up") or UP_DEFAULT
    _candles["down"] = saved.get("down") or DOWN_DEFAULT


def is_light() -> bool:
    return name == "Claro"


def up() -> str:
    return _candles["up"]


def down() -> str:
    return _candles["down"]


def css(text: str) -> str:
    """Traduce al tema actual los colores de una hoja de estilo o de un HTML."""
    if not is_light() or not text:
        return text
    return _HEX.sub(lambda m: _LIGHT.get(m.group().lower(), m.group()), text)


def c(color: str | None) -> str | None:
    """Un color que se dibuja sobre el fondo de la gráfica. En el tema claro, una línea blanca
    (p. ej. la SMA 200 inicial) pasa a negra para que se siga viendo."""
    if not color or not is_light():
        return color
    if color[:7].lower() == "#ffffff":
        return _LIGHT[STRONG] + color[7:]
    return css(color)


def style(widget, text: str):
    """``setStyleSheet`` que sobrevive a un cambio de tema."""
    widget._cv_css = text
    widget.setStyleSheet(css(text))


def palette() -> QtGui.QPalette:
    p = QtGui.QPalette()
    q = lambda h: QtGui.QColor(css(h))  # noqa: E731
    P = QtGui.QPalette
    roles = {
        P.Window: "#1e222d", P.WindowText: "#d1d4dc", P.Base: "#131722", P.AlternateBase: "#1e222d",
        P.ToolTipBase: "#2a2e39", P.ToolTipText: "#d1d4dc", P.Text: "#d1d4dc", P.Button: "#2a2e39",
        P.ButtonText: "#d1d4dc", P.BrightText: "#ff5252", P.Highlight: "#2962ff",
        P.HighlightedText: "#ffffff", P.PlaceholderText: "#787b86",
    }
    for role, color in roles.items():
        p.setColor(role, q(color))
    p.setColor(P.Disabled, P.ButtonText, q("#5d606b"))
    p.setColor(P.Disabled, P.Text, q("#5d606b"))
    return p


def apply(app_style: str):
    """Pone la paleta y los estilos del tema actual en toda la aplicación."""
    app = QtWidgets.QApplication.instance()
    app.setPalette(palette())
    app.setStyleSheet(css(app_style))
    for w in app.allWidgets():
        original = getattr(w, "_cv_css", None)
        if original is not None:
            w.setStyleSheet(css(original))
