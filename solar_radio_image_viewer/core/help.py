"""
The Help menu's windows: About, the tutorial, the keyboard shortcuts and
checking for updates.
"""

import logging
from PyQt5.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QFrame,
    # QInputDialog,
    # QMenuBar,
    QDialog,
)
from PyQt5.QtCore import Qt
from ..dialogs import UpdateDialog
from ..ui.icons import get_resource_path
from ..ui.styles import SPACE_L, SPACE_M, SPACE_S, SPACE_XS, themed
from ..ui.widgets import button_bar, dialog_layout, empty_note, rule

logger = logging.getLogger(__name__)


from ..ui.geometry import fit_to_screen


def _round_logo(path, size, widget):
    """The logo at ``size``, cut to its disc as on the splash screen (the
    picture has a white square round the sun)."""
    from PyQt5.QtGui import QPainter, QPainterPath, QPixmap

    ratio = widget.devicePixelRatioF()
    pixels = round(size * ratio)
    round_logo = QPixmap(pixels, pixels)
    round_logo.fill(Qt.transparent)
    painter = QPainter(round_logo)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    disc = QPainterPath()
    disc.addEllipse(0, 0, pixels, pixels)
    painter.setClipPath(disc)
    painter.drawPixmap(0, 0, QPixmap(path).scaled(pixels, pixels, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    painter.end()
    round_logo.setDevicePixelRatio(ratio)
    return round_logo


class HelpMixin:
    """Shows the Help menu's windows.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``_open_dialogs``
    """

    def show_update_dialog(self):
        """Show the update check dialog (non-modal)."""
        # Check if already open
        for dialog in self._open_dialogs:
            if isinstance(dialog, UpdateDialog):
                dialog.raise_()
                dialog.activateWindow()
                return

        dialog = UpdateDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        # Installed, or not wanted: the note in the status bar goes
        for signal in (dialog.updated, dialog.skipped):
            signal.connect(lambda *_: self.update_btn.setVisible(False) if hasattr(self, "update_btn") else None)
        dialog.destroyed.connect(
            lambda: (
                self._open_dialogs.remove(dialog)
                if dialog in self._open_dialogs
                else None
            )
        )
        self._open_dialogs.append(dialog)
        dialog.show()

    def show_about_dialog(self):
        """Show a modern, professional about dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("About SolarViewer")
        dialog.setMinimumWidth(440)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header: a low banner in the accent colour, the logo beside the name
        header = QFrame()
        header.setObjectName("Banner")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(SPACE_L, SPACE_M, SPACE_L, SPACE_M)
        header_layout.setSpacing(SPACE_M)

        icon_label = QLabel()
        try:
            icon_label.setPixmap(_round_logo(get_resource_path("assets/icon.png"), 56, dialog))
        except Exception:
            pass
        header_layout.addWidget(icon_label)

        # App title
        from .. import __version__

        title_layout = QVBoxLayout()
        title_layout.setSpacing(SPACE_XS)
        title = QLabel("SolarViewer")
        title.setObjectName("DialogTitle")
        title_layout.addWidget(title)
        title_layout.addWidget(QLabel(f"Version {__version__}"))
        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        layout.addWidget(header)

        # Content section
        content = QFrame()
        content_layout = dialog_layout(content)

        # Description
        desc = QLabel("A visualization tool for CASA and FITS solar radio images")
        desc.setWordWrap(True)
        content_layout.addWidget(desc)

        # Feature pills
        features_layout = QHBoxLayout()
        features_layout.setSpacing(SPACE_S)

        for feature in ["Multi-tab", "Stokes", "Fitting", "Contours", "Remote"]:
            pill = QLabel(feature)
            pill.setObjectName("Badge")
            features_layout.addWidget(pill)

        features_layout.addStretch()
        content_layout.addLayout(features_layout)

        content_layout.addWidget(rule())

        # Author section
        author_label = QLabel("Developed by")
        author_label.setObjectName("Hint")
        content_layout.addWidget(author_label)

        author_name = QLabel("Soham Dey")
        author_name.setObjectName("SectionTitle")
        content_layout.addWidget(author_name)

        # Contact links
        email_link = QLabel()
        email_link.setOpenExternalLinks(True)
        # (a label keeps the link colour it first laid out with)
        themed(
            email_link,
            lambda palette: email_link.setText(
                f'<a href="mailto:sohamd943@gmail.com" style="color: {palette["link"]}">'
                "sohamd943@gmail.com</a>"
            ),
        )
        email_link.setCursor(Qt.PointingHandCursor)
        content_layout.addWidget(email_link)

        # Keyboard shortcut hint
        hint = QLabel("Press F1 for keyboard shortcuts")
        hint.setObjectName("Hint")
        content_layout.addWidget(hint)

        # Close button
        box, _ = button_bar(secondary=("Close", dialog.accept))
        content_layout.addWidget(box)

        layout.addWidget(content, 1)

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

    def show_tutorial(self):
        """Show the comprehensive tutorial dialog."""
        from ..tutorial import TutorialDialog

        dialog = TutorialDialog(self)
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

    # Keys and mouse gestures of the image, which no menu shows. Handled by
    # handle_view_key (R, 1, +, -, arrows, the tools' keys), the regions'
    # keys (roi_stats.py _on_region_key), keyPressEvent below (Space,
    # Enter, Esc), the file navigation shortcuts ([ ] { }) and the tab bar
    # (Ctrl+Tab); keep this list in step with them.
    IMAGE_KEYS = [
        ("R", "Reset the view"),
        ("1", "Zoom to 1° × 1°"),
        ("+  or  =", "Zoom in"),
        ("-", "Zoom out"),
        ("Arrow keys", "Pan"),
        ("H", "Pan tool"),
        ("B", "Rectangle tool"),
        ("E", "Ellipse tool"),
        ("L", "Ruler tool (measure a distance)"),
        ("P", "Profile tool (the flux along a line cut)"),
        ("Delete  or  Backspace", "Delete the selected region (Rectangle or Ellipse tool)"),
        ("Space  or  Enter", "Redraw the image"),
        ("Esc", "Leave a text field (the keys above then work again); deselect the region"),
        ("[  or  ]", "Previous / next file in the folder"),
        ("{  or  }", "First / last file in the folder"),
        ("Ctrl+Tab  or  Ctrl+Shift+Tab", "Next / previous tab"),
    ]

    MOUSE_GESTURES = [
        ("Scroll  or  Pinch", "Zoom about the pointer"),
        ("Middle-drag", "Pan (left-drag too, with the Pan tool)"),
        ("Right-drag", "Zoom to the box drawn"),
        ("Double-click", "Zoom in (with the Pan tool); Shift: zoom out"),
        ("Shift-drag", "A square or circle (Rectangle, Ellipse); 15° steps when rotating a region"),
    ]

    def _shortcut_sections(self):
        """(title, [(keys, what it does)]) for the Keyboard Shortcuts dialog:
        each menu's actions that have a shortcut, then the image's keys and
        mouse gestures."""
        from PyQt5.QtGui import QKeySequence

        def rows_of(menu, prefix=""):
            rows = []
            for action in menu.actions():
                text = prefix + action.text().replace("&", "")
                if action.menu() is not None:
                    # e.g. "AIA Presets: 171 Å"
                    rows += rows_of(action.menu(), f"{text}: ")
                elif action.shortcuts():
                    keys = "  or  ".join(
                        k.toString(QKeySequence.NativeText) for k in action.shortcuts()
                    )
                    rows.append((keys, text))
            return rows

        sections = []
        for top in self.menuBar().actions():
            if top.menu() is not None:
                rows = rows_of(top.menu())
                if rows:
                    sections.append((top.text().replace("&", ""), rows))
        sections.append(("On the image", self.IMAGE_KEYS))
        sections.append(("Mouse", self.MOUSE_GESTURES))
        return sections

    def show_keyboard_shortcuts(self):
        """Show the keyboard shortcuts: those of the menus, read from the
        menus themselves, and the keys and mouse gestures of the image."""
        from PyQt5.QtWidgets import QGridLayout, QGroupBox, QScrollArea, QFrame, QLineEdit

        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard shortcuts")
        fit_to_screen(dialog, 850, 600)

        main_layout = dialog_layout(dialog)

        # Search filter
        search_box = QLineEdit()
        search_box.setPlaceholderText("Filter shortcuts…")
        search_box.setClearButtonEnabled(True)
        main_layout.addWidget(search_box)

        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(SPACE_M)
        scroll_layout.setContentsMargins(0, 0, SPACE_S, 0)

        def create_key_badge(key_text):
            """Create a styled keyboard key badge."""
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)

            # Handle "or" alternatives first (like "+ or =")
            if "  or  " in key_text:
                alternatives = key_text.split("  or  ")
                for j, alt in enumerate(alternatives):
                    if j > 0:
                        or_label = QLabel("or")
                        or_label.setObjectName("Hint")
                        layout.addWidget(or_label)
                    badge = QLabel(alt.strip())
                    badge.setObjectName("Key")
                    layout.addWidget(badge)
            # Handle compound keys like "Ctrl+O"
            elif "+" in key_text and len(key_text) > 1:
                parts = key_text.split("+")
                for i, part in enumerate(parts):
                    part = part.strip()
                    if not part:
                        continue
                    if i > 0:
                        plus = QLabel("+")
                        plus.setObjectName("Hint")
                        layout.addWidget(plus)
                    badge = QLabel(part)
                    badge.setObjectName("Key")
                    layout.addWidget(badge)
            else:
                # Single key
                badge = QLabel(key_text.strip())
                badge.setObjectName("Key")
                layout.addWidget(badge)

            layout.addStretch()
            return container

        # Store all shortcut widgets for filtering
        all_shortcut_widgets = []

        def create_category_section(name, shortcuts):
            """Create a styled category section."""
            section = QGroupBox(name)

            # Shortcuts grid
            grid = QGridLayout(section)
            grid.setContentsMargins(SPACE_M, SPACE_XS, SPACE_M, SPACE_XS)
            grid.setSpacing(SPACE_S)
            # Wide enough for the widest keys, so all sections line up
            grid.setColumnMinimumWidth(0, 290)
            grid.setColumnStretch(1, 1)  # descriptions next to their keys

            shortcut_rows = []
            for row, (key, action) in enumerate(shortcuts):
                key_widget = create_key_badge(key)
                action_label = QLabel(action)
                action_label.setWordWrap(True)  # (the long gestures, in a narrow window)

                grid.addWidget(key_widget, row, 0)
                grid.addWidget(action_label, row, 1)

                # Store for filtering
                shortcut_rows.append(
                    (key_widget, action_label, key.lower(), action.lower())
                )

            return section, shortcut_rows

        # Create all category sections
        for name, shortcuts in self._shortcut_sections():
            section, rows = create_category_section(name, shortcuts)
            scroll_layout.addWidget(section)
            all_shortcut_widgets.extend([(section, rows)])

        no_match = empty_note("No shortcut matches. Clear the filter to see them all.")
        no_match.hide()
        scroll_layout.addWidget(no_match)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, 1)

        # Filter function
        def filter_shortcuts(text):
            text = text.lower()
            matches = 0
            for section, rows in all_shortcut_widgets:
                visible_count = 0
                for key_widget, action_label, key_text, action_text in rows:
                    visible = not text or text in key_text or text in action_text
                    key_widget.setVisible(visible)
                    action_label.setVisible(visible)
                    if visible:
                        visible_count += 1
                section.setVisible(visible_count > 0)
                matches += visible_count
            no_match.setVisible(matches == 0)

        search_box.textChanged.connect(filter_shortcuts)

        box, _ = button_bar(secondary=("Close", dialog.accept))
        main_layout.addWidget(box)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
