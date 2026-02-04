from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from pabble_ocr.ui.main_window import MainWindow


def run_app() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()

