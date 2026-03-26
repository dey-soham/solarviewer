"""
Shape annotation system for SolarViewer.

Provides customizable shape annotations (circle, ellipse, square, rectangle)
and solar radii preset circles.
"""

import numpy as np
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QDialogButtonBox,
    QAction,
    QMenu,
    QMessageBox,
)
from PyQt5.QtCore import Qt
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Ellipse, Rectangle

from .styles import set_hand_cursor, theme_manager


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _get_pixel_scale_deg(tab):
    """Return the pixel scale in degrees from the tab's WCS, or None."""
    if not tab.current_wcs:
        return None
    try:
        cdelt = tab.current_wcs.increment()["numeric"][0:2]
        if isinstance(cdelt, list):
            cdelt = [float(c) for c in cdelt]
        _hdr = getattr(tab, "_cached_fits_header", None) or {}
        _is_solar = "SOLAR-X" in str(_hdr.get("CTYPE1", "")).upper()
        if _is_solar:
            cdelt = np.array(cdelt) / 3600.0  # arcsec -> degrees
        else:
            cdelt = np.array(cdelt) * 180 / np.pi  # radians -> degrees
        return abs(float(cdelt[0]))
    except Exception:
        return None


def _arcsec_to_pixels(arcsec, tab):
    """Convert an arcsec value to pixels using the tab's WCS."""
    dx_deg = _get_pixel_scale_deg(tab)
    if dx_deg is None or dx_deg == 0:
        return arcsec  # fallback: treat as pixels
    return (arcsec / 3600.0) / dx_deg


def draw_shape_annotations(tab, ax):
    """Draw all shape annotations stored in *tab* onto matplotlib *ax*.

    Call this during every plot refresh. Old patches are removed first.
    """
    # Remove previous shape annotation artists
    for artist in list(ax.patches) + list(ax.texts):
        if getattr(artist, "_shape_annotation", False):
            artist.remove()

    for shape in getattr(tab, "shape_annotations", []):
        try:
            _draw_one_shape(tab, ax, shape)
        except Exception as e:
            print(f"[WARN] Failed to draw shape annotation: {e}")


def draw_text_annotations(tab, ax):
    """Draw all tracked text annotations onto *ax*."""
    for artist in list(ax.texts):
        if getattr(artist, "_text_annotation", False):
            artist.remove()

    for a in getattr(tab, "text_annotations", []):
        try:
            bbox_props = None
            if a.get("background"):
                bbox_props = dict(boxstyle="round,pad=0.3", facecolor=a["background"], alpha=0.7)
            t = ax.text(
                a["x"], a["y"], a["text"],
                color=a.get("color", "yellow"),
                fontsize=a.get("fontsize", 12),
                fontweight=a.get("fontweight", "normal"),
                fontstyle=a.get("fontstyle", "normal"),
                bbox=bbox_props,
                alpha=a.get("alpha", 1.0),
            )
            t._text_annotation = True
        except Exception as e:
            print(f"[WARN] Failed to draw text annotation: {e}")


def draw_arrow_annotations(tab, ax):
    """Draw all tracked arrow annotations onto *ax*."""
    for artist in list(ax.texts):
        if getattr(artist, "_arrow_annotation", False):
            artist.remove()

    for a in getattr(tab, "arrow_annotations", []):
        try:
            ann = ax.annotate(
                "",
                xy=(a["x2"], a["y2"]),
                xytext=(a["x1"], a["y1"]),
                arrowprops=dict(
                    arrowstyle="-|>",
                    color=a.get("color", "red"),
                    lw=a.get("linewidth", 2.0),
                    mutation_scale=a.get("head_width", 8),
                ),
                alpha=a.get("alpha", 1.0),
            )
            ann._arrow_annotation = True
        except Exception as e:
            print(f"[WARN] Failed to draw arrow annotation: {e}")


def _draw_one_shape(tab, ax, s):
    """Draw a single shape dict *s* onto *ax*."""
    cx = s["center_x"]
    cy = s["center_y"]
    unit = s.get("unit", "pixels")
    color = s.get("color", "white")
    linestyle = s.get("linestyle", "-")
    linewidth = s.get("linewidth", 1.5)
    alpha = s.get("alpha", 0.8)
    fill = s.get("fill", False)
    facecolor = s.get("facecolor", "none")
    angle = s.get("angle", 0.0)
    label = s.get("label", "")
    shape_type = s["type"]

    w = s["width"]
    h = s.get("height", w)

    if unit == "arcsec":
        w = _arcsec_to_pixels(w, tab)
        h = _arcsec_to_pixels(h, tab)

    if shape_type == "circle":
        patch = Circle(
            (cx, cy),
            radius=w / 2.0,
            fill=fill,
            facecolor=facecolor if fill else "none",
            edgecolor=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=alpha,
        )
    elif shape_type == "ellipse":
        patch = Ellipse(
            (cx, cy),
            width=w,
            height=h,
            angle=angle,
            fill=fill,
            facecolor=facecolor if fill else "none",
            edgecolor=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=alpha,
        )
    elif shape_type == "square":
        patch = Rectangle(
            (cx - w / 2.0, cy - w / 2.0),
            w,
            w,
            angle=0,
            fill=fill,
            facecolor=facecolor if fill else "none",
            edgecolor=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=alpha,
        )
    elif shape_type == "rectangle":
        patch = Rectangle(
            (cx - w / 2.0, cy - h / 2.0),
            w,
            h,
            angle=angle,
            fill=fill,
            facecolor=facecolor if fill else "none",
            edgecolor=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=alpha,
        )
    else:
        return

    patch._shape_annotation = True
    ax.add_patch(patch)

    # Optional label
    if label:
        txt = ax.text(
            cx,
            cy,
            f"  {label}",
            color=color,
            fontsize=9,
            alpha=alpha,
            verticalalignment="center",
        )
        txt._shape_annotation = True


# ---------------------------------------------------------------------------
# Solar radii presets
# ---------------------------------------------------------------------------

def _resolve_solar_center(tab):
    """Return (center_x, center_y) in pixel coords, same logic as solar disk."""
    if tab.solar_disk_center is not None:
        return tab.solar_disk_center

    if tab.current_image_data is None:
        return None

    height, width = tab.current_image_data.shape

    _is_hpc = tab._is_already_hpc()
    if _is_hpc and tab.current_wcs:
        try:
            ref_world = list(tab.current_wcs.referencevalue()["numeric"])
            ref_world[0] = 0.0
            ref_world[1] = 0.0
            pix_origin = tab.current_wcs.topixel(ref_world)["numeric"]
            cx, cy = float(pix_origin[0]), float(pix_origin[1])
            cx = max(0.0, min(float(width - 1), cx))
            cy = max(0.0, min(float(height - 1), cy))
            return (cx, cy)
        except Exception:
            pass
    if _is_hpc and hasattr(tab, "_cached_wcs_obj") and tab._cached_wcs_obj is not None:
        try:
            px, py = tab._cached_wcs_obj.wcs_world2pix(0.0, 0.0, 0)
            cx, cy = float(px), float(py)
            cx = max(0.0, min(float(width - 1), cx))
            cy = max(0.0, min(float(height - 1), cy))
            return (cx, cy)
        except Exception:
            pass

    return (width // 2, height // 2)


def add_solar_radius_circle(tab, multiplier):
    """Add a circle at *multiplier* × 1 R☉ centred on the solar disk."""
    center = _resolve_solar_center(tab)
    if center is None:
        return

    # 1 R☉ = 960 arcsec (angular solar radius)
    radius_arcsec = 960.0 * multiplier
    diameter_arcsec = radius_arcsec * 2.0

    label_text = f"{multiplier} R☉" if multiplier == int(multiplier) else f"{multiplier} R☉"

    shape = {
        "type": "circle",
        "center_x": center[0],
        "center_y": center[1],
        "width": diameter_arcsec,
        "unit": "arcsec",
        "color": "cyan",
        "linestyle": "--",
        "linewidth": 1.2,
        "alpha": 0.7,
        "fill": False,
        # "label": label_text,
    }
    tab.shape_annotations.append(shape)
    tab.schedule_plot()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

_COLORS = [
    "white", "yellow", "cyan", "lime", "red",
    "magenta", "orange", "blue", "black",
]

_LINESTYLES = [
    ("Solid", "-"),
    ("Dashed", "--"),
    ("Dotted", ":"),
    ("Dash-dot", "-."),
]


class ShapeAnnotationDialog(QDialog):
    """Dialog for adding a customizable shape annotation."""

    def __init__(self, parent, tab):
        super().__init__(parent)
        self.tab = tab
        self.setWindowTitle("Add Shape Annotation")
        set_hand_cursor(self)
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Shape type ---
        type_group = QGroupBox("Shape")
        type_layout = QHBoxLayout(type_group)
        type_layout.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Circle", "Ellipse", "Square", "Rectangle"])
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        type_layout.addWidget(self.type_combo)
        layout.addWidget(type_group)

        # --- Solar Radii Preset (Circle only) ---
        self.preset_group = QGroupBox("Solar Radii Preset")
        preset_layout = QHBoxLayout(self.preset_group)
        preset_layout.addWidget(QLabel("Radius:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("None (custom)", None)
        for r in [1, 1.5, 2, 2.5, 3, 5, 10]:
            label = f"{int(r)} R☉" if r == int(r) else f"{r} R☉"
            self.preset_combo.addItem(label, r)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_layout.addWidget(self.preset_combo)
        layout.addWidget(self.preset_group)

        # --- Position ---
        pos_group = QGroupBox("Position (pixels)")
        pos_layout = QGridLayout(pos_group)
        pos_layout.addWidget(QLabel("Center X:"), 0, 0)
        cx_default = "0"
        cy_default = "0"
        if self.tab.current_image_data is not None:
            h, w = self.tab.current_image_data.shape
            cx_default = str(w // 2)
            cy_default = str(h // 2)
        self.cx_edit = QLineEdit(cx_default)
        pos_layout.addWidget(self.cx_edit, 0, 1)
        pos_layout.addWidget(QLabel("Center Y:"), 0, 2)
        self.cy_edit = QLineEdit(cy_default)
        pos_layout.addWidget(self.cy_edit, 0, 3)
        layout.addWidget(pos_group)

        # --- Size ---
        size_group = QGroupBox("Size")
        size_layout = QGridLayout(size_group)

        size_layout.addWidget(QLabel("Width:"), 0, 0)
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.1, 999999)
        self.width_spin.setValue(100)
        self.width_spin.setDecimals(1)
        size_layout.addWidget(self.width_spin, 0, 1)

        self.height_label = QLabel("Height:")
        size_layout.addWidget(self.height_label, 0, 2)
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(0.1, 999999)
        self.height_spin.setValue(60)
        self.height_spin.setDecimals(1)
        size_layout.addWidget(self.height_spin, 0, 3)

        size_layout.addWidget(QLabel("Unit:"), 1, 0)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["Pixels", "Arcsec"])
        size_layout.addWidget(self.unit_combo, 1, 1)

        self.angle_label = QLabel("Angle (°):")
        size_layout.addWidget(self.angle_label, 1, 2)
        self.angle_spin = QDoubleSpinBox()
        self.angle_spin.setRange(-360, 360)
        self.angle_spin.setValue(0)
        self.angle_spin.setDecimals(1)
        size_layout.addWidget(self.angle_spin, 1, 3)

        layout.addWidget(size_group)

        # --- Style ---
        style_group = QGroupBox("Style")
        style_layout = QGridLayout(style_group)

        style_layout.addWidget(QLabel("Color:"), 0, 0)
        self.color_combo = QComboBox()
        self.color_combo.addItems(_COLORS)
        style_layout.addWidget(self.color_combo, 0, 1)

        style_layout.addWidget(QLabel("Line Style:"), 0, 2)
        self.linestyle_combo = QComboBox()
        for name, val in _LINESTYLES:
            self.linestyle_combo.addItem(name, val)
        style_layout.addWidget(self.linestyle_combo, 0, 3)

        style_layout.addWidget(QLabel("Line Width:"), 1, 0)
        self.linewidth_spin = QDoubleSpinBox()
        self.linewidth_spin.setRange(0.5, 10.0)
        self.linewidth_spin.setValue(1.5)
        self.linewidth_spin.setSingleStep(0.5)
        style_layout.addWidget(self.linewidth_spin, 1, 1)

        style_layout.addWidget(QLabel("Alpha:"), 1, 2)
        self.alpha_spin = QDoubleSpinBox()
        self.alpha_spin.setRange(0.0, 1.0)
        self.alpha_spin.setValue(0.8)
        self.alpha_spin.setSingleStep(0.1)
        style_layout.addWidget(self.alpha_spin, 1, 3)

        self.fill_check = QCheckBox("Fill")
        style_layout.addWidget(self.fill_check, 2, 0)
        self.fill_check.stateChanged.connect(self._on_fill_changed)

        self.fillcolor_label = QLabel("Fill Color:")
        style_layout.addWidget(self.fillcolor_label, 2, 2)
        self.fillcolor_combo = QComboBox()
        self.fillcolor_combo.addItems(_COLORS)
        self.fillcolor_combo.setCurrentText("cyan")
        style_layout.addWidget(self.fillcolor_combo, 2, 3)
        self.fillcolor_label.setVisible(False)
        self.fillcolor_combo.setVisible(False)

        layout.addWidget(style_group)

        # --- Label ---
        label_group = QGroupBox("Label (optional)")
        label_layout = QHBoxLayout(label_group)
        self.label_edit = QLineEdit("")
        label_layout.addWidget(self.label_edit)
        layout.addWidget(label_group)

        from PyQt5.QtWidgets import QPushButton
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.setToolTip("Add shape and keep dialog open")
        add_btn.clicked.connect(self._on_add)
        btn_layout.addWidget(add_btn)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        btn_layout.addWidget(buttons)
        layout.addLayout(btn_layout)

        # Initial visibility
        self._on_type_changed(self.type_combo.currentText())

    # -- Dynamic field visibility --

    def _on_type_changed(self, shape_type):
        is_circle = shape_type == "Circle"
        is_symmetric = shape_type in ("Circle", "Square")
        self.height_label.setVisible(not is_symmetric)
        self.height_spin.setVisible(not is_symmetric)
        has_angle = shape_type in ("Ellipse", "Rectangle")
        self.angle_label.setVisible(has_angle)
        self.angle_spin.setVisible(has_angle)
        self.preset_group.setVisible(is_circle)
        # Reset preset when switching away from circle
        if not is_circle:
            self.preset_combo.setCurrentIndex(0)

    def _on_preset_changed(self, index):
        """Auto-fill position, size, unit, and style from a solar radii preset."""
        multiplier = self.preset_combo.currentData()
        if multiplier is None:
            return

        # Set center to solar disk center
        center = _resolve_solar_center(self.tab)
        if center is not None:
            self.cx_edit.setText(f"{center[0]:.1f}")
            self.cy_edit.setText(f"{center[1]:.1f}")

        # Set diameter in arcsec (1 R☉ = 960 arcsec)
        diameter = 960.0 * multiplier * 2.0
        self.width_spin.setValue(diameter)
        self.unit_combo.setCurrentText("Arcsec")

        # Set suggested style
        '''self.color_combo.setCurrentText("cyan")
        for i in range(self.linestyle_combo.count()):
            if self.linestyle_combo.itemData(i) == "--":
                self.linestyle_combo.setCurrentIndex(i)
                break'''

    def _on_fill_changed(self, checked):
        self.fillcolor_label.setVisible(bool(checked))
        self.fillcolor_combo.setVisible(bool(checked))

    def _on_add(self):
        """Add shape without closing the dialog."""
        try:
            cx = float(self.cx_edit.text())
            cy = float(self.cy_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Enter valid numeric coordinates.")
            return

        shape_type = self.type_combo.currentText().lower()
        w = self.width_spin.value()
        h = self.height_spin.value() if shape_type in ("ellipse", "rectangle") else w
        unit = "arcsec" if self.unit_combo.currentText() == "Arcsec" else "pixels"

        shape = {
            "type": shape_type,
            "center_x": cx,
            "center_y": cy,
            "width": w,
            "height": h,
            "unit": unit,
            "color": self.color_combo.currentText(),
            "linestyle": self.linestyle_combo.currentData(),
            "linewidth": self.linewidth_spin.value(),
            "alpha": self.alpha_spin.value(),
            "fill": self.fill_check.isChecked(),
            "facecolor": self.fillcolor_combo.currentText() if self.fill_check.isChecked() else "none",
            "angle": self.angle_spin.value(),
            "label": self.label_edit.text().strip(),
        }

        self.tab.shape_annotations.append(shape)
        self.tab.schedule_plot()

    def _on_ok(self):
        """Add shape and close the dialog."""
        self._on_add()
        self.accept()


# ---------------------------------------------------------------------------
# Menu wiring
# ---------------------------------------------------------------------------

def setup_shape_menu(app, annot_menu):
    """Add shape/text/arrow annotation management actions to the Annotations menu.

    *app* is the SolarRadioImageViewerApp instance (QMainWindow).
    """
    annot_menu.addSeparator()

    # Add Shape dialog
    shape_act = QAction("Add Shape…", app)
    shape_act.setStatusTip("Add a customizable shape annotation to the image")
    shape_act.triggered.connect(lambda: _open_shape_dialog(app))
    annot_menu.addAction(shape_act)

    # Solar Radii Presets submenu
    radii_menu = QMenu("Solar Radii Presets", app)
    for r in [1, 1.5, 2, 2.5, 3, 5, 10]:
        label = f"{r} R☉" if r == int(r) else f"{r} R☉"
        act = QAction(label, app)
        act.setStatusTip(f"Draw a circle at {r} solar radii")
        act.triggered.connect(lambda checked, mult=r: _add_preset(app, mult))
        radii_menu.addAction(act)
    annot_menu.addMenu(radii_menu)

    annot_menu.addSeparator()

    # --- Remove / Clear submenu ---
    manage_menu = QMenu("Remove / Clear", app)

    # Text
    remove_text_act = QAction("Remove Last Text", app)
    remove_text_act.setStatusTip("Remove the most recently added text annotation")
    remove_text_act.triggered.connect(lambda: _remove_last_of(app, "text_annotations"))
    manage_menu.addAction(remove_text_act)

    clear_text_act = QAction("Clear All Text", app)
    clear_text_act.setStatusTip("Remove all text annotations")
    clear_text_act.triggered.connect(lambda: _clear_all_of(app, "text_annotations"))
    manage_menu.addAction(clear_text_act)

    manage_menu.addSeparator()

    # Arrow
    remove_arrow_act = QAction("Remove Last Arrow", app)
    remove_arrow_act.setStatusTip("Remove the most recently added arrow annotation")
    remove_arrow_act.triggered.connect(lambda: _remove_last_of(app, "arrow_annotations"))
    manage_menu.addAction(remove_arrow_act)

    clear_arrow_act = QAction("Clear All Arrows", app)
    clear_arrow_act.setStatusTip("Remove all arrow annotations")
    clear_arrow_act.triggered.connect(lambda: _clear_all_of(app, "arrow_annotations"))
    manage_menu.addAction(clear_arrow_act)

    manage_menu.addSeparator()

    # Shape
    remove_shape_act = QAction("Remove Last Shape", app)
    remove_shape_act.setStatusTip("Remove the most recently added shape annotation")
    remove_shape_act.triggered.connect(lambda: _remove_last_of(app, "shape_annotations"))
    manage_menu.addAction(remove_shape_act)

    clear_shape_act = QAction("Clear All Shapes", app)
    clear_shape_act.setStatusTip("Remove all shape annotations")
    clear_shape_act.triggered.connect(lambda: _clear_all_of(app, "shape_annotations"))
    manage_menu.addAction(clear_shape_act)

    manage_menu.addSeparator()

    # Clear everything
    clear_all_act = QAction("Clear All Annotations", app)
    clear_all_act.setStatusTip("Remove all text, arrow, and shape annotations")
    clear_all_act.triggered.connect(lambda: _clear_everything(app))
    manage_menu.addAction(clear_all_act)

    annot_menu.addMenu(manage_menu)


# -- helpers for menu actions --

def _open_shape_dialog(app):
    tab = app.tab_widget.currentWidget()
    if not tab or tab.current_image_data is None:
        QMessageBox.warning(app, "No Data", "Load an image first.")
        return
    dlg = ShapeAnnotationDialog(app, tab)
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    dlg.destroyed.connect(
        lambda: (
            app._open_dialogs.remove(dlg) if dlg in app._open_dialogs else None
        )
    )
    app._open_dialogs.append(dlg)
    dlg.show()


def _add_preset(app, multiplier):
    tab = app.tab_widget.currentWidget()
    if not tab or tab.current_image_data is None:
        QMessageBox.warning(app, "No Data", "Load an image first.")
        return
    add_solar_radius_circle(tab, multiplier)


def _remove_last_of(app, attr):
    tab = app.tab_widget.currentWidget()
    if tab and getattr(tab, attr, []):
        getattr(tab, attr).pop()
        tab.schedule_plot()


def _clear_all_of(app, attr):
    tab = app.tab_widget.currentWidget()
    if tab and getattr(tab, attr, []):
        getattr(tab, attr).clear()
        tab.schedule_plot()


def _clear_everything(app):
    tab = app.tab_widget.currentWidget()
    if tab:
        changed = False
        for attr in ("text_annotations", "arrow_annotations", "shape_annotations"):
            lst = getattr(tab, attr, [])
            if lst:
                lst.clear()
                changed = True
        if changed:
            tab.schedule_plot()
