from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication

        from bom_tool.ui.main_window import MainWindow
    except ImportError as exc:
        print(f"Missing runtime dependency: {exc}")
        return 1

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
