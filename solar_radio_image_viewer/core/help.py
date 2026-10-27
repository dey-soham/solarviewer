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
    QPushButton,
    QFrame,
    # QInputDialog,
    # QMenuBar,
    QDialog,
)
from PyQt5.QtCore import Qt
from ..dialogs import UpdateDialog
from ..ui.icons import get_resource_path

logger = logging.getLogger(__name__)


from ..ui.geometry import fit_to_screen, fit_width_to_text


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
        from PyQt5.QtGui import QPixmap

        dialog = QDialog(self)
        dialog.setWindowTitle("About SolarViewer")
        fit_to_screen(dialog, 510, 580)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header: a banner in the accent colour
        header = QFrame()
        header.setObjectName("Banner")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(30, 25, 30, 20)
        header_layout.setSpacing(10)

        icon_label = QLabel()
        try:
            icon_path = get_resource_path("assets/icon.png")
            pixmap = QPixmap(icon_path)
            icon_label.setPixmap(
                pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        except Exception:
            pass
        icon_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(icon_label)

        # App title
        title = QLabel("SolarViewer")
        title.setObjectName("DialogTitle")
        title.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(title)

        # Version badge
        from .. import __version__

        version_container = QHBoxLayout()
        version_container.addStretch()
        version_label = QLabel(f"Version {__version__}")
        version_container.addWidget(version_label)
        version_container.addStretch()
        header_layout.addLayout(version_container)

        layout.addWidget(header)

        # Content section
        content = QFrame()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 25, 30, 20)
        content_layout.setSpacing(16)

        # Description
        desc = QLabel("A visualization tool for CASA and FITS solar radio images")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        content_layout.addWidget(desc)

        # Feature pills
        features_layout = QHBoxLayout()
        features_layout.setSpacing(10)
        features_layout.addStretch()

        for feature in ["Multi-tab", "Stokes", "Fitting", "Contours", "Remote"]:
            pill = QLabel(feature)
            pill.setObjectName("Badge")
            features_layout.addWidget(pill)

        features_layout.addStretch()
        content_layout.addLayout(features_layout)

        content_layout.addSpacing(12)

        # Divider
        divider = QFrame()
        divider.setObjectName("Rule")
        content_layout.addWidget(divider)

        content_layout.addSpacing(8)

        # Author section
        author_label = QLabel("Developed by")
        author_label.setAlignment(Qt.AlignCenter)
        author_label.setObjectName("Hint")
        content_layout.addWidget(author_label)

        author_name = QLabel("Soham Dey")
        author_name.setAlignment(Qt.AlignCenter)
        author_name.setObjectName("SectionTitle")
        content_layout.addWidget(author_name)

        # Contact links
        links_layout = QHBoxLayout()
        links_layout.setSpacing(20)
        links_layout.addStretch()

        email_link = QLabel('<a href="mailto:sohamd943@gmail.com">sohamd943@gmail.com</a>')
        email_link.setOpenExternalLinks(True)
        email_link.setCursor(Qt.PointingHandCursor)
        links_layout.addWidget(email_link)

        links_layout.addStretch()
        content_layout.addLayout(links_layout)

        content_layout.addSpacing(12)

        # Keyboard shortcut hint
        hint = QLabel("Press F1 for keyboard shortcuts")
        hint.setAlignment(Qt.AlignCenter)
        hint.setObjectName("Hint")
        content_layout.addWidget(hint)

        content_layout.addStretch()

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setDefault(True)
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        content_layout.addLayout(btn_layout)

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
        sections.append(("On the Image", self.IMAGE_KEYS))
        sections.append(("Mouse", self.MOUSE_GESTURES))
        return sections

    def show_keyboard_shortcuts(self):
        """Show the keyboard shortcuts: those of the menus, read from the
        menus themselves, and the keys and mouse gestures of the image."""
        from PyQt5.QtWidgets import QGridLayout, QScrollArea, QFrame, QLineEdit

        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(850, 600)
        fit_to_screen(dialog, 850, 600)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(24, 20, 24, 20)

        # Header with search
        header_layout = QHBoxLayout()

        title = QLabel("Keyboard Shortcuts")
        title.setObjectName("DialogTitle")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Search filter
        search_box = QLineEdit()
        search_box.setPlaceholderText("Filter shortcuts...")
        search_box.setClearButtonEnabled(True)
        fit_width_to_text(search_box, "Filter shortcuts......................")
        header_layout.addWidget(search_box)

        main_layout.addLayout(header_layout)

        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(20)
        scroll_layout.setContentsMargins(0, 0, 10, 0)

        categories = [("", title, rows) for title, rows in self._shortcut_sections()]

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

        def create_category_section(icon, name, shortcuts):
            """Create a styled category section."""
            section = QFrame()
            section.setObjectName("Card")

            layout = QVBoxLayout(section)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(10)

            # Category header
            header = QLabel(f"{icon}  {name}" if icon else name)
            header.setObjectName("SectionTitle")
            layout.addWidget(header)

            # Shortcuts grid
            grid = QGridLayout()
            grid.setSpacing(8)
            # Wide enough for the widest keys, so all sections line up
            grid.setColumnMinimumWidth(0, 290)
            grid.setColumnStretch(1, 1)  # descriptions next to their keys

            shortcut_rows = []
            for row, (key, action) in enumerate(shortcuts):
                key_widget = create_key_badge(key)
                action_label = QLabel(action)

                grid.addWidget(key_widget, row, 0)
                grid.addWidget(action_label, row, 1)

                # Store for filtering
                shortcut_rows.append(
                    (key_widget, action_label, key.lower(), action.lower())
                )

            layout.addLayout(grid)
            return section, shortcut_rows

        # Create all category sections
        for icon, name, shortcuts in categories:
            section, rows = create_category_section(icon, name, shortcuts)
            scroll_layout.addWidget(section)
            all_shortcut_widgets.extend([(section, rows)])

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, 1)

        # Filter function
        def filter_shortcuts(text):
            text = text.lower()
            for section, rows in all_shortcut_widgets:
                visible_count = 0
                for key_widget, action_label, key_text, action_text in rows:
                    visible = not text or text in key_text or text in action_text
                    key_widget.setVisible(visible)
                    action_label.setVisible(visible)
                    if visible:
                        visible_count += 1
                section.setVisible(visible_count > 0)

        search_box.textChanged.connect(filter_shortcuts)

        # Footer with close button
        footer = QHBoxLayout()
        footer.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setDefault(True)
        footer.addWidget(close_btn)

        main_layout.addLayout(footer)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
