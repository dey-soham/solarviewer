"""
Pill-style segmented toggle (e.g. CASA Image / FITS File).
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QWidget


class SegmentedControl(QWidget):
    """A modern segmented control widget."""

    selectionChanged = pyqtSignal(int)  # Emits index of selected segment

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options
        self._selected_index = 0
        self._buttons = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create container for the pill shape
        self._container = QWidget()
        self._container.setObjectName("Segmented")
        container_layout = QHBoxLayout(self._container)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(1)

        for i, option in enumerate(options):
            btn = QPushButton(option)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, idx=i: self._on_button_clicked(idx))
            self._buttons.append(btn)
            container_layout.addWidget(btn)

        # Select first by default
        if self._buttons:
            self._buttons[0].setChecked(True)

        layout.addWidget(self._container)

    def _on_button_clicked(self, index):
        """Handle button click - ensure only one is selected."""
        if index == self._selected_index:
            # Re-check if clicking already selected (don't allow deselect)
            self._buttons[index].setChecked(True)
            return

        # Uncheck all others
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)

        self._selected_index = index
        self.selectionChanged.emit(index)

    def selectedIndex(self):
        """Return the currently selected index."""
        return self._selected_index

    def setSelectedIndex(self, index):
        """Set the selected index programmatically."""
        if 0 <= index < len(self._buttons):
            self._on_button_clicked(index)

    def selectedText(self):
        """Return the text of the selected option."""
        return self.options[self._selected_index]
