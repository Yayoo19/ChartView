"""Ejecución de descargas en segundo plano para no congelar la interfaz."""
from __future__ import annotations

from PySide6 import QtCore


class _Signals(QtCore.QObject):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)


class Task(QtCore.QRunnable):
    _alive: set = set()

    def __init__(self, fn, *args, on_done=None, on_fail=None):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = _Signals()
        if on_done:
            self.signals.done.connect(on_done)
        if on_fail:
            self.signals.failed.connect(on_fail)
        self.signals.done.connect(self._release)
        self.signals.failed.connect(self._release)

    def _release(self, *_):
        Task._alive.discard(self)

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as e:  # noqa: BLE001 - se muestra al usuario
            self.signals.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.signals.done.emit(result)


def run_task(fn, *args, on_done=None, on_fail=None) -> Task:
    t = Task(fn, *args, on_done=on_done, on_fail=on_fail)
    t.setAutoDelete(False)
    Task._alive.add(t)
    QtCore.QThreadPool.globalInstance().start(t)
    return t
