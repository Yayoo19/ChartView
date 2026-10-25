import os

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

from chartview.app import main  # noqa: E402

if __name__ == "__main__":
    main()
