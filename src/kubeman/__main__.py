import sys

from PySide6.QtWidgets import QApplication

from .k8s.client import KubeService
from .ui import theme
from .ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    theme.apply(app)
    win = MainWindow(KubeService)  # a factory: each open cluster gets its own connection
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
