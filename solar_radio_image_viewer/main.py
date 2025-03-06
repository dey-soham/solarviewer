#!/usr/bin/env python3
import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtCore import Qt
from .viewer import SolarRadioImageViewerApp
from .styles import DARK_PALETTE, STYLESHEET

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Apply dark palette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(DARK_PALETTE["window"]))
    palette.setColor(QPalette.WindowText, QColor(DARK_PALETTE["text"]))
    palette.setColor(QPalette.Base, QColor(DARK_PALETTE["base"]))
    palette.setColor(QPalette.AlternateBase, QColor(DARK_PALETTE["button"]))
    palette.setColor(QPalette.Text, QColor(DARK_PALETTE["text"]))
    palette.setColor(QPalette.Button, QColor(DARK_PALETTE["button"]))
    palette.setColor(QPalette.ButtonText, QColor(DARK_PALETTE["text"]))
    palette.setColor(QPalette.Highlight, QColor(DARK_PALETTE["highlight"]))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)

    imagename = sys.argv[1] if len(sys.argv) > 1 else None
    window = SolarRadioImageViewerApp(imagename)
    window.resize(1280, 720)
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

