from PyQt5.QtWidgets import (
    QComboBox,
    QPushButton,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QFrame,
    QListWidgetItem,
    QStyle,
    QApplication,
    QDialog,
    QDialogButtonBox,
)
from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QIcon
import os
from .styles import get_icon_path, theme_manager, set_hand_cursor

# Package directory for fast resource loading
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_resource_path(relative_path):
    """Get absolute path to a package resource file."""
    return os.path.join(_PACKAGE_DIR, relative_path)


class SearchDialog(QDialog):
    """A dialog for searching and selecting colormaps"""

    def __init__(self, parent=None, all_items=None):
        super().__init__(parent)
        self.setWindowTitle("Search Colormaps")
        self.setMinimumWidth(350)
        self.setMinimumHeight(400)

        self.all_items = all_items or []
        self.selected_item = None

        layout = QVBoxLayout(self)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Type to search colormaps...")
        self.search_edit.textChanged.connect(self.filter_items)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)
        layout.addWidget(self.list_widget)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.populate_list_widget(self.all_items)
        set_hand_cursor(self)
        self.search_edit.setFocus()
        self.search_edit.installEventFilter(self)
        self.list_widget.installEventFilter(self)

    def filter_items(self, text):
        if not text:
            self.populate_list_widget(self.all_items)
            return

        filtered_items = []
        for item in self.all_items:
            if text.lower() in item.lower():
                filtered_items.append(item)

        self.populate_list_widget(filtered_items)

        if filtered_items and self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def populate_list_widget(self, items):
        self.list_widget.clear()
        for item in items:
            list_item = QListWidgetItem(item)
            list_item.setSizeHint(QSize(300, 25))
            self.list_widget.addItem(list_item)

    def on_item_clicked(self, item):
        self.selected_item = item.text()

    def on_item_double_clicked(self, item):
        self.selected_item = item.text()
        self.accept()

    def accept(self):
        current_item = self.list_widget.currentItem()
        if current_item:
            self.selected_item = current_item.text()
        super().accept()

    def eventFilter(self, obj, event):
        if event.type() == event.KeyPress:
            if obj == self.search_edit:
                if event.key() == Qt.Key_Up or event.key() == Qt.Key_Down:
                    self.list_widget.setFocus()
                    if (
                        self.list_widget.count() > 0
                        and not self.list_widget.currentItem()
                    ):
                        self.list_widget.setCurrentRow(0)
                    return True
                elif event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                    if self.list_widget.count() > 0:
                        if not self.list_widget.currentItem():
                            self.list_widget.setCurrentRow(0)
                        current_item = self.list_widget.currentItem()
                        if current_item:
                            self.selected_item = current_item.text()
                            self.accept()
                    return True
            elif obj == self.list_widget:
                if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                    current_item = self.list_widget.currentItem()
                    if current_item:
                        self.selected_item = current_item.text()
                        self.accept()
                    return True
        return super().eventFilter(obj, event)


class ColormapSelector(QWidget):
    """
    A widget that combines a simple dropdown for preferred colormaps
    and a search button that expands to show a search area for all colormaps.
    """

    colormapSelected = pyqtSignal(str)

    def __init__(self, parent=None, preferred_items=None, all_items=None):
        super().__init__(parent)

        self.preferred_items = preferred_items or []
        self.all_items = all_items or []
        self.current_colormap = "viridis"

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(5)

        self.combo = QComboBox()
        self.combo.addItems(self.preferred_items)
        self.combo.currentTextChanged.connect(self.on_combo_changed)
        self.main_layout.addWidget(self.combo, 1)

        self.search_button = QPushButton()
        self.search_button.setObjectName("IconOnlyNBGButton")
        self.search_button.setToolTip("Search all colormaps")
        self.search_button.setMaximumWidth(32)
        self.search_button.setFixedSize(32, 32)
        self._update_search_icon()  # Use theme-aware icon
        self.search_button.setIconSize(QSize(24, 24))
        self.search_button.clicked.connect(self.show_search_dialog)
        self.main_layout.addWidget(self.search_button)

        # Register callback for theme changes
        theme_manager.register_callback(self._on_theme_changed)

        if self.preferred_items:
            self.combo.setCurrentText(self.preferred_items[0])
            self.current_colormap = self.preferred_items[0]

    def _update_search_icon(self):
        """Update the search icon based on the current theme."""
        icon_filename = get_icon_path("search.png")
        self.search_button.setIcon(QIcon(_get_resource_path(f"assets/{icon_filename}")))

    def _on_theme_changed(self, theme):
        """Handle theme change by updating the search icon."""
        self._update_search_icon()

    def on_combo_changed(self, text):
        if text in self.all_items:
            self.current_colormap = text
            self.colormapSelected.emit(text)

    def show_search_dialog(self):
        dialog = SearchDialog(self, self.all_items)
        if dialog.exec_() == QDialog.Accepted and dialog.selected_item:
            self.current_colormap = dialog.selected_item
            if dialog.selected_item in self.preferred_items:
                self.combo.blockSignals(True)
                self.combo.setCurrentText(dialog.selected_item)
                self.combo.blockSignals(False)
            else:
                self.combo.blockSignals(True)
                found = False
                for i in range(self.combo.count()):
                    if self.combo.itemText(i) == dialog.selected_item:
                        self.combo.setCurrentIndex(i)
                        found = True
                        break
                if not found:
                    self.combo.addItem(dialog.selected_item)
                    self.combo.setCurrentText(dialog.selected_item)
                self.combo.blockSignals(False)
            self.colormapSelected.emit(dialog.selected_item)

    def currentText(self):
        return self.current_colormap

    def setCurrentText(self, text):
        self.current_colormap = text
        if text in self.preferred_items:
            self.combo.setCurrentText(text)
        else:
            found = False
            for i in range(self.combo.count()):
                if self.combo.itemText(i) == text:
                    self.combo.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                self.combo.addItem(text)
                self.combo.setCurrentText(text)
