"""
Contours tab: the contour layers.
"""

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QListWidget,
    QAbstractItemView,
    QDialogButtonBox,
)
from ...processing.contour_layers import (
    MAX_CONTOUR_LAYERS,
    contour_layer_name,
    contour_layer_settings,
    default_contour_options,
    new_contour_layer,
)


class ContoursTabMixin:
    """Contours tab: the contour layers. Mixed into VideoCreationDialog."""

    def _create_contours_tab(self):
        """Contours tab: the video's contour layers. Returns its scroll area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        scroll.setWidget(content)

        info = QLabel(
            f"Up to {MAX_CONTOUR_LAYERS} contour layers, as in the main window. "
            "Each can contour the video image itself (in any Stokes), a fixed "
            "file, or a series of files: each frame then takes the file "
            "nearest to it in observation time."
        )
        info.setWordWrap(True)
        info.setObjectName("SecondaryText")
        layout.addWidget(info)

        self.contour_layers = []
        self.contour_options = default_contour_options()
        self.contour_layer_list = QListWidget()
        self.contour_layer_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.contour_layer_list.setMinimumHeight(140)
        layout.addWidget(self.contour_layer_list)

        buttons = QHBoxLayout()
        edit_btn = QPushButton("Edit Layers...")
        edit_btn.setToolTip("Add, change or remove contour layers, and set the legend")
        edit_btn.clicked.connect(self._edit_contour_layers)
        buttons.addWidget(edit_btn)
        copy_btn = QPushButton("Copy from Main Window")
        copy_btn.setToolTip(
            "Use the contour layers of the image open in the main window"
        )
        copy_btn.clicked.connect(self._copy_viewer_contours)
        buttons.addWidget(copy_btn)
        clear_btn = QPushButton("Remove All")
        clear_btn.clicked.connect(lambda: self._set_contour_layers([]))
        buttons.addWidget(clear_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        levels_row = QHBoxLayout()
        levels_row.addWidget(QLabel("Levels from:"))
        self.contour_level_combo = QComboBox()
        for text, mode in (
            ("Each frame", "frame"),
            ("The first frame", "first"),
            ("All frames", "all"),
        ):
            self.contour_level_combo.addItem(text, mode)
        self.contour_level_combo.setToolTip(
            "What fraction (of the peak) and sigma (× RMS) levels are relative to.\n"
            "Each frame: its own peak / RMS, so levels change as the peak does.\n"
            "The first frame: that frame's, the same in every frame.\n"
            "All frames: the highest peak / the median RMS of all frames,\n"
            "the same in every frame. Absolute levels never change."
        )
        levels_row.addWidget(self.contour_level_combo)
        levels_row.addStretch()
        layout.addLayout(levels_row)

        layout.addStretch()
        self._show_contour_layers()
        return scroll

    def _set_contour_layers(self, layers, options=None):
        self.contour_layers = [contour_layer_settings(layer) for layer in layers]
        if options is not None:
            self.contour_options = dict(default_contour_options(), **options)
        self._show_contour_layers()
        if hasattr(self, "line_plot_curves"):
            self._show_curves()  # their sources include the layers
        self.schedule_preview()

    def _show_contour_layers(self):
        self.contour_layer_list.clear()
        kinds = {"same": "video image", "external": "fixed file", "series": "series"}
        for layer in self.contour_layers:
            state = "" if layer.get("enabled", True) else " (hidden)"
            self.contour_layer_list.addItem(
                f"{contour_layer_name(layer, 'Image')}  —  {kinds.get(layer.get('source'), '')}{state}"
            )
        if not self.contour_layers:
            self.contour_layer_list.addItem("No contours")

    def _edit_contour_layers(self):
        from ...dialogs.contour_settings import ContourSettingsDialog

        layers = self.contour_layers or [new_contour_layer()]
        dialog = ContourSettingsDialog(
            self,
            layers,
            self.contour_options,
            for_video=True,
            # Images on a server: a contour series there too (a fixed file
            # is fetched when chosen, as in the main window)
            series_folder_picker=(
                (lambda start: self._pick_remote("folder", start)) if self.remote else None
            ),
        )
        box = dialog.findChild(QDialogButtonBox)
        if box is not None:
            box.button(QDialogButtonBox.Ok).setText("OK")
            box.button(QDialogButtonBox.Cancel).setText("Cancel")
        if dialog.exec_() == QDialog.Accepted:
            self._set_contour_layers(dialog.get_layers(), dialog.get_options())

    def _copy_viewer_contours(self):
        tab = (
            self._main_window.tab_widget.currentWidget()
            if getattr(self._main_window, "tab_widget", None) is not None
            else None
        )
        layers = getattr(tab, "contour_layers", None)
        if not layers:
            QMessageBox.information(
                self, "No Contours", "The main window has no contour layers."
            )
            return
        enabled = tab.show_contours_checkbox.isChecked()
        self._set_contour_layers(
            [
                dict(layer, enabled=layer.get("enabled", True) and enabled)
                for layer in layers
            ],
            tab.contour_options,
        )

    def _parse_levels(self, text):
        """Parse comma-separated level values from text"""
        try:
            levels = []
            for part in text.split(","):
                part = part.strip()
                if part:
                    levels.append(float(part))
            return levels
        except ValueError:
            return [0.1, 0.3, 0.5, 0.7, 0.9]  # Default levels
