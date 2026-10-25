"""Punto de entrada de ChartView.

Se puede ejecutar de dos formas desde la carpeta del proyecto:
    python src/main.py
    python -m src.main
"""
import os
import sys

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

if __package__:  # python -m src.main
    from .app import main
else:  # python src/main.py: se agrega la carpeta del proyecto para importar el paquete src
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.app import main

if __name__ == "__main__":
    main()
