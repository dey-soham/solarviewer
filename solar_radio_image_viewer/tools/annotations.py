"""
Text and arrow annotation tool for SolarViewer.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..styles import set_hand_cursor


class AnnotationsMixin:
    """Store text/arrow annotations on a tab (drawn by ``plot_image``).

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.
    """

    def add_text_annotation(
        self,
        x,
        y,
        text,
        color="yellow",
        fontsize=12,
        fontweight="normal",
        fontstyle="normal",
        background=None,
        alpha=1.0,
    ):
        """Add text annotation to the plot with customizable styling."""
        annot = {
            "x": x,
            "y": y,
            "text": text,
            "color": color,
            "fontsize": fontsize,
            "fontweight": fontweight,
            "fontstyle": fontstyle,
            "background": background,
            "alpha": alpha,
        }
        self.text_annotations.append(annot)
        self.schedule_plot()

    def add_arrow_annotation(
        self,
        x1,
        y1,
        x2,
        y2,
        color="red",
        linewidth=2.0,
        head_width=8,
        head_length=10,
        alpha=1.0,
    ):
        """Add arrow annotation to the plot with customizable styling."""
        annot = {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "color": color,
            "linewidth": linewidth,
            "head_width": head_width,
            "alpha": alpha,
        }
        self.arrow_annotations.append(annot)
        self.schedule_plot()


class AnnotationDialogsMixin:
    """Main-window dialogs that create text/arrow annotations on the current tab.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.
    """

    def add_text_annotation(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Text Annotation")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(350)
        layout = QVBoxLayout(dialog)

        # Position group
        pos_group = QGroupBox("Position")
        pos_layout = QGridLayout(pos_group)
        pos_layout.addWidget(QLabel("X:"), 0, 0)
        x_pos = QLineEdit("100")
        pos_layout.addWidget(x_pos, 0, 1)
        pos_layout.addWidget(QLabel("Y:"), 0, 2)
        y_pos = QLineEdit("100")
        pos_layout.addWidget(y_pos, 0, 3)
        layout.addWidget(pos_group)

        # Text group
        text_group = QGroupBox("Text")
        text_layout = QVBoxLayout(text_group)
        text_input = QLineEdit("Annotation")
        text_layout.addWidget(text_input)
        layout.addWidget(text_group)

        # Style group
        style_group = QGroupBox("Style")
        style_layout = QGridLayout(style_group)

        # Color
        style_layout.addWidget(QLabel("Color:"), 0, 0)
        color_combo = QComboBox()
        color_combo.addItems(
            [
                "yellow",
                "white",
                "red",
                "cyan",
                "lime",
                "magenta",
                "orange",
                "blue",
                "black",
            ]
        )
        style_layout.addWidget(color_combo, 0, 1)

        # Font size
        style_layout.addWidget(QLabel("Font Size:"), 0, 2)
        fontsize_spin = QSpinBox()
        fontsize_spin.setRange(6, 48)
        fontsize_spin.setValue(12)
        style_layout.addWidget(fontsize_spin, 0, 3)

        # Font weight
        style_layout.addWidget(QLabel("Weight:"), 1, 0)
        weight_combo = QComboBox()
        weight_combo.addItems(["normal", "bold"])
        style_layout.addWidget(weight_combo, 1, 1)

        # Font style
        style_layout.addWidget(QLabel("Style:"), 1, 2)
        fontstyle_combo = QComboBox()
        fontstyle_combo.addItems(["normal", "italic"])
        style_layout.addWidget(fontstyle_combo, 1, 3)

        # Background
        style_layout.addWidget(QLabel("Background:"), 2, 0)
        bg_combo = QComboBox()
        bg_combo.addItems(["None", "black", "white", "gray", "yellow", "red", "blue"])
        style_layout.addWidget(bg_combo, 2, 1)

        layout.addWidget(style_group)

        # Store tab reference for callback
        tab_ref = current_tab

        def on_add():
            try:
                # Check if tab still exists
                if tab_ref not in [
                    self.tab_widget.widget(i) for i in range(self.tab_widget.count())
                ]:
                    QMessageBox.warning(
                        self, "Error", "The target tab has been closed."
                    )
                    dialog.close()
                    return
                x = int(x_pos.text())
                y = int(y_pos.text())
                text = text_input.text()
                color = color_combo.currentText()
                fontsize = fontsize_spin.value()
                fontweight = weight_combo.currentText()
                fontstyle = fontstyle_combo.currentText()
                background = (
                    bg_combo.currentText() if bg_combo.currentText() != "None" else None
                )
                tab_ref.add_text_annotation(
                    x,
                    y,
                    text,
                    color=color,
                    fontsize=fontsize,
                    fontweight=fontweight,
                    fontstyle=fontstyle,
                    background=background,
                )
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid Input", "Please enter valid numeric coordinates"
                )
            except RuntimeError:
                QMessageBox.warning(
                    self, "Error", "The target tab is no longer available."
                )
                dialog.close()

        def on_ok():
            on_add()
            dialog.close()

        from PyQt5.QtWidgets import QPushButton

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.setToolTip("Add annotation and keep dialog open")
        add_btn.clicked.connect(on_add)
        btn_layout.addWidget(add_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(on_ok)
        buttons.rejected.connect(dialog.close)
        btn_layout.addWidget(buttons)
        layout.addLayout(btn_layout)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def add_arrow_annotation(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No Data", "Load data first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Arrow Annotation")
        set_hand_cursor(dialog)
        dialog.setMinimumWidth(350)
        layout = QVBoxLayout(dialog)

        # Position group
        pos_group = QGroupBox("Position")
        pos_layout = QGridLayout(pos_group)
        pos_layout.addWidget(QLabel("Start X:"), 0, 0)
        x1_pos = QLineEdit("100")
        pos_layout.addWidget(x1_pos, 0, 1)
        pos_layout.addWidget(QLabel("Start Y:"), 0, 2)
        y1_pos = QLineEdit("100")
        pos_layout.addWidget(y1_pos, 0, 3)
        pos_layout.addWidget(QLabel("End X:"), 1, 0)
        x2_pos = QLineEdit("150")
        pos_layout.addWidget(x2_pos, 1, 1)
        pos_layout.addWidget(QLabel("End Y:"), 1, 2)
        y2_pos = QLineEdit("150")
        pos_layout.addWidget(y2_pos, 1, 3)
        layout.addWidget(pos_group)

        # Style group
        style_group = QGroupBox("Style")
        style_layout = QGridLayout(style_group)

        # Color
        style_layout.addWidget(QLabel("Color:"), 0, 0)
        color_combo = QComboBox()
        color_combo.addItems(
            [
                "red",
                "yellow",
                "white",
                "cyan",
                "lime",
                "magenta",
                "orange",
                "blue",
                "black",
            ]
        )
        style_layout.addWidget(color_combo, 0, 1)

        # Line width
        style_layout.addWidget(QLabel("Line Width:"), 0, 2)
        linewidth_spin = QDoubleSpinBox()
        linewidth_spin.setRange(0.5, 10.0)
        linewidth_spin.setValue(2.0)
        linewidth_spin.setSingleStep(0.5)
        style_layout.addWidget(linewidth_spin, 0, 3)

        # Head size
        style_layout.addWidget(QLabel("Head Size:"), 1, 0)
        headsize_spin = QSpinBox()
        headsize_spin.setRange(4, 30)
        headsize_spin.setValue(10)
        style_layout.addWidget(headsize_spin, 1, 1)

        layout.addWidget(style_group)

        # Store tab reference for callback
        tab_ref = current_tab

        def on_add():
            try:
                # Check if tab still exists
                if tab_ref not in [
                    self.tab_widget.widget(i) for i in range(self.tab_widget.count())
                ]:
                    QMessageBox.warning(
                        self, "Error", "The target tab has been closed."
                    )
                    dialog.close()
                    return
                x1 = int(x1_pos.text())
                y1 = int(y1_pos.text())
                x2 = int(x2_pos.text())
                y2 = int(y2_pos.text())
                color = color_combo.currentText()
                linewidth = linewidth_spin.value()
                head_width = headsize_spin.value()
                tab_ref.add_arrow_annotation(
                    x1,
                    y1,
                    x2,
                    y2,
                    color=color,
                    linewidth=linewidth,
                    head_width=head_width,
                )
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid Input", "Please enter valid numeric coordinates"
                )
            except RuntimeError:
                QMessageBox.warning(
                    self, "Error", "The target tab is no longer available."
                )
                dialog.close()

        def on_ok():
            on_add()
            dialog.close()

        from PyQt5.QtWidgets import QPushButton

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.setToolTip("Add annotation and keep dialog open")
        add_btn.clicked.connect(on_add)
        btn_layout.addWidget(add_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(on_ok)
        buttons.rejected.connect(dialog.close)
        btn_layout.addWidget(buttons)
        layout.addLayout(btn_layout)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()
