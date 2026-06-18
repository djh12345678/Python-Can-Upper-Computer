# -*- coding: utf-8 -*-
"""Entry point — runs the CAN Bus Debugger application."""
import sys
import signal
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from app.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Let Ctrl+C in the terminal kill the app cleanly.
    # Qt's C++ event loop never yields to Python, so SIGINT would be silently
    # ignored without this. The QTimer forces a Python-bytecode tick every
    # 200 ms so the signal handler actually runs.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    _sigint_timer = QTimer()
    _sigint_timer.start(200)
    _sigint_timer.timeout.connect(lambda: None)

    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
