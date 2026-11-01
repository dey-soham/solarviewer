"""
Text and arrow annotation tool for SolarViewer.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QLineEdit,
    QMessageBox,
    QSpinBox,
)

from ..dialogs.form import color_combo, field_label, form_block
from ..ui.styles import set_hand_cursor
from ..ui.widgets import button_bar, dialog_layout


class AnnotationsMixin:
    """Store text/arrow annotations on a tab (drawn by ``plot_image``).

    Mixed into :class:`SolarRadioImageTab`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``arrow_annotations``, ``schedule_plot``, ``text_annotations``
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

    Host attributes/methods used:

    ``_open_dialogs``, ``tab_widget``
    """

    def show_add_text_annotation_dialog(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No data", "Load data first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add text annotation")
        set_hand_cursor(dialog)
        layout = dialog_layout(dialog)

        # Text
        text_layout = form_block(layout)
        text_layout.addWidget(field_label("Text"), 0, 0)
        text_input = QLineEdit("Annotation")
        text_layout.addWidget(text_input, 0, 1)

        # Position group
        pos_layout = form_block(layout, "Position (pixels)", pairs=2)
        pos_layout.addWidget(field_label("X"), 0, 0)
        x_pos = QLineEdit("100")
        pos_layout.addWidget(x_pos, 0, 1)
        pos_layout.addWidget(field_label("Y"), 0, 2)
        y_pos = QLineEdit("100")
        pos_layout.addWidget(y_pos, 0, 3)

        # Style group
        style_layout = form_block(layout, "Style", pairs=2)

        # Color
        style_layout.addWidget(field_label("Colour"), 0, 0)
        color_choice = color_combo(
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
        style_layout.addWidget(color_choice, 0, 1)

        # Font size
        style_layout.addWidget(field_label("Font size"), 0, 2)
        fontsize_spin = QSpinBox()
        fontsize_spin.setRange(6, 48)
        fontsize_spin.setValue(12)
        style_layout.addWidget(fontsize_spin, 0, 3)

        # Font weight
        style_layout.addWidget(field_label("Weight"), 1, 0)
        weight_combo = QComboBox()
        weight_combo.addItems(["Normal", "Bold"])
        style_layout.addWidget(weight_combo, 1, 1)

        # Font style
        style_layout.addWidget(field_label("Style"), 1, 2)
        fontstyle_combo = QComboBox()
        fontstyle_combo.addItems(["Normal", "Italic"])
        style_layout.addWidget(fontstyle_combo, 1, 3)

        # Background
        style_layout.addWidget(field_label("Background"), 2, 0)
        bg_combo = color_combo(["black", "white", "gray", "yellow", "red", "blue"])
        bg_combo.insertItem(0, "None")
        bg_combo.setCurrentIndex(0)
        style_layout.addWidget(bg_combo, 2, 1)

        # Store tab reference for callback
        tab_ref = current_tab

        def on_add():
            try:
                # Check if tab still exists
                if tab_ref not in self.all_panes():
                    QMessageBox.warning(
                        self, "Error", "The target tab has been closed."
                    )
                    dialog.close()
                    return
                x = int(x_pos.text())
                y = int(y_pos.text())
                text = text_input.text()
                color = color_choice.currentData()
                fontsize = fontsize_spin.value()
                fontweight = weight_combo.currentText().lower()
                fontstyle = fontstyle_combo.currentText().lower()
                background = bg_combo.currentData()
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
                return True
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid input", "Please enter valid numeric coordinates"
                )
            except RuntimeError:
                QMessageBox.warning(
                    self, "Error", "The target tab is no longer available."
                )
                dialog.close()

        def on_ok():
            if on_add():  # (on bad input it stays open, to be corrected)
                dialog.close()

        box, buttons = button_bar(
            primary=("Add", on_ok),
            secondary=("Cancel", dialog.close),
            extras=[("Add another", on_add)],
        )
        buttons["Add another"].setToolTip("Add the annotation and keep this window open")
        layout.addWidget(box)

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

    def show_add_arrow_annotation_dialog(self):
        current_tab = self.tab_widget.currentWidget()
        if not current_tab or current_tab.current_image_data is None:
            QMessageBox.warning(self, "No data", "Load data first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add arrow annotation")
        set_hand_cursor(dialog)
        layout = dialog_layout(dialog)

        # Position group
        pos_layout = form_block(layout, "Position (pixels)", pairs=2)
        pos_layout.addWidget(field_label("Start X"), 0, 0)
        x1_pos = QLineEdit("100")
        pos_layout.addWidget(x1_pos, 0, 1)
        pos_layout.addWidget(field_label("Start Y"), 0, 2)
        y1_pos = QLineEdit("100")
        pos_layout.addWidget(y1_pos, 0, 3)
        pos_layout.addWidget(field_label("End X"), 1, 0)
        x2_pos = QLineEdit("150")
        pos_layout.addWidget(x2_pos, 1, 1)
        pos_layout.addWidget(field_label("End Y"), 1, 2)
        y2_pos = QLineEdit("150")
        pos_layout.addWidget(y2_pos, 1, 3)

        # Style group
        style_layout = form_block(layout, "Style", pairs=2)

        # Color
        style_layout.addWidget(field_label("Colour"), 0, 0)
        color_choice = color_combo(
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
        style_layout.addWidget(color_choice, 0, 1)

        # Line width
        style_layout.addWidget(field_label("Line width"), 0, 2)
        linewidth_spin = QDoubleSpinBox()
        linewidth_spin.setRange(0.5, 10.0)
        linewidth_spin.setValue(2.0)
        linewidth_spin.setSingleStep(0.5)
        style_layout.addWidget(linewidth_spin, 0, 3)

        # Head size
        style_layout.addWidget(field_label("Head size"), 1, 0)
        headsize_spin = QSpinBox()
        headsize_spin.setRange(4, 30)
        headsize_spin.setValue(10)
        style_layout.addWidget(headsize_spin, 1, 1)

        # Store tab reference for callback
        tab_ref = current_tab

        def on_add():
            try:
                # Check if tab still exists
                if tab_ref not in self.all_panes():
                    QMessageBox.warning(
                        self, "Error", "The target tab has been closed."
                    )
                    dialog.close()
                    return
                x1 = int(x1_pos.text())
                y1 = int(y1_pos.text())
                x2 = int(x2_pos.text())
                y2 = int(y2_pos.text())
                color = color_choice.currentData()
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
                return True
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid input", "Please enter valid numeric coordinates"
                )
            except RuntimeError:
                QMessageBox.warning(
                    self, "Error", "The target tab is no longer available."
                )
                dialog.close()

        def on_ok():
            if on_add():  # (on bad input it stays open, to be corrected)
                dialog.close()

        box, buttons = button_bar(
            primary=("Add", on_ok),
            secondary=("Cancel", dialog.close),
            extras=[("Add another", on_add)],
        )
        buttons["Add another"].setToolTip("Add the annotation and keep this window open")
        layout.addWidget(box)

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
