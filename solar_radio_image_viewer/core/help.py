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
        from ..ui.styles import theme_manager
        from PyQt5.QtGui import QPixmap

        is_dark = theme_manager.is_dark

        dialog = QDialog(self)
        dialog.setWindowTitle("About SolarViewer")
        dialog.resize(510, 580)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header section with gradient background
        header = QFrame()
        if is_dark:
            header.setStyleSheet(
                """
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #4c1d95, stop:1 #7c3aed);
                    border-radius: 0px;
                }
            """
            )
        else:
            header.setStyleSheet(
                """
                QFrame {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #7c3aed, stop:1 #a78bfa);
                    border-radius: 0px;
                }
            """
            )
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
            icon_label.setText("☀️")
            icon_label.setStyleSheet("font-size: 48pt;")
        icon_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(icon_label)

        # App title
        title = QLabel("SolarViewer")
        title.setStyleSheet("font-size: 26pt; font-weight: bold; color: white;")
        title.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(title)

        # Version badge
        from .. import __version__

        version_container = QHBoxLayout()
        version_container.addStretch()
        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet(
            """
            QLabel {
                background-color: rgba(255, 255, 255, 0.2);
                color: white;
                padding: 5px 14px;
                border-radius: 12px;
                font-size: 11pt;
                font-weight: bold;
            }
        """
        )
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
        desc.setStyleSheet("font-size: 12pt;")
        content_layout.addWidget(desc)

        # Feature pills
        features_layout = QHBoxLayout()
        features_layout.setSpacing(10)
        features_layout.addStretch()

        feature_style = f"""
            QLabel {{
                background-color: {'rgba(124, 58, 237, 0.15)' if is_dark else 'rgba(124, 58, 237, 0.1)'};
                color: {'#a78bfa' if is_dark else '#7c3aed'};
                padding: 6px 14px;
                border-radius: 14px;
                font-size: 10pt;
            }}
        """

        for feature in ["Multi-tab", "Stokes", "Fitting", "Contours", "Remote"]:
            pill = QLabel(feature)
            pill.setStyleSheet(feature_style)
            features_layout.addWidget(pill)

        features_layout.addStretch()
        content_layout.addLayout(features_layout)

        content_layout.addSpacing(12)

        # Divider
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(
            f"background-color: {'#3a3a4a' if is_dark else '#e0e0e0'};"
        )
        content_layout.addWidget(divider)

        content_layout.addSpacing(8)

        # Author section
        author_label = QLabel("Developed by")
        author_label.setAlignment(Qt.AlignCenter)
        author_label.setStyleSheet(
            f"font-size: 11pt; color: {'#888' if is_dark else '#666'};"
        )
        content_layout.addWidget(author_label)

        author_name = QLabel("Soham Dey")
        author_name.setAlignment(Qt.AlignCenter)
        author_name.setStyleSheet("font-size: 15pt; font-weight: bold;")
        content_layout.addWidget(author_name)

        # Contact links
        links_layout = QHBoxLayout()
        links_layout.setSpacing(20)
        links_layout.addStretch()

        link_style = f"""
            QLabel {{
                color: {'#a78bfa' if is_dark else '#7c3aed'};
                font-size: 11pt;
            }}
            QLabel:hover {{
                text-decoration: underline;
            }}
        """

        email_link = QLabel("📧 sohamd943@gmail.com")
        email_link.setStyleSheet(link_style)
        email_link.setCursor(Qt.PointingHandCursor)
        links_layout.addWidget(email_link)

        links_layout.addStretch()
        content_layout.addLayout(links_layout)

        content_layout.addSpacing(12)

        # Keyboard shortcut hint
        hint = QLabel("Press F1 for keyboard shortcuts")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            f"font-size: 10pt; font-style: italic; color: {'#666' if is_dark else '#888'};"
        )
        content_layout.addWidget(hint)

        content_layout.addStretch()

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet(
            """
            QPushButton {
                padding: 8px 20px;
                font-size: 11pt;
            }
        """
        )
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
    # handle_view_key (R, 1, +, -, arrows), keyPressEvent below (Space,
    # Enter, Esc), the file navigation shortcuts ([ ] { }) and the tab bar
    # (Ctrl+Tab); keep this list in step with them.
    IMAGE_KEYS = [
        ("R", "Reset the view"),
        ("1", "Zoom to 1° × 1°"),
        ("+  or  =", "Zoom in"),
        ("-", "Zoom out"),
        ("Arrow keys", "Pan"),
        ("Space  or  Enter", "Redraw the image"),
        ("Esc", "Leave a text field (the keys above then work again)"),
        ("[  or  ]", "Previous / next file in the folder"),
        ("{  or  }", "First / last file in the folder"),
        ("Ctrl+Tab  or  Ctrl+Shift+Tab", "Next / previous tab"),
    ]

    MOUSE_GESTURES = [
        ("Scroll  or  Pinch", "Zoom about the pointer"),
        ("Middle-drag", "Pan (left-drag too, with the Pan tool)"),
        ("Right-drag", "Zoom to the box drawn"),
        ("Double-click", "Zoom in (with the Pan tool); Shift: zoom out"),
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
        sections.append(("Image", self.IMAGE_KEYS))
        sections.append(("Mouse", self.MOUSE_GESTURES))
        return sections

    def show_keyboard_shortcuts(self):
        """Show the keyboard shortcuts: those of the menus, read from the
        menus themselves, and the keys and mouse gestures of the image."""
        from PyQt5.QtWidgets import QGridLayout, QScrollArea, QFrame, QLineEdit

        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(850, 600)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(24, 20, 24, 20)

        # Header with search
        header_layout = QHBoxLayout()

        title = QLabel("⌨️ Keyboard Shortcuts")
        title.setStyleSheet("font-size: 16pt; font-weight: bold;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Search filter
        search_box = QLineEdit()
        search_box.setPlaceholderText("🔍 Filter shortcuts...")
        search_box.setFixedWidth(200)
        search_box.setStyleSheet(
            """
            QLineEdit {
                padding: 6px 12px;
                border-radius: 15px;
            }
        """
        )
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

        # Get theme for styling
        from ..ui.styles import theme_manager

        is_dark = theme_manager.is_dark

        # Define theme-aware badge styles
        if is_dark:
            badge_style = """
                QLabel {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #4a4a5a, stop:1 #3a3a4a);
                    color: #f0f0f5;
                    padding: 4px 10px;
                    border-radius: 5px;
                    font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
                    font-weight: bold;
                    font-size: 10pt;
                    border: 1px solid #5a5a6a;
                    border-bottom: 2px solid #2a2a3a;
                }
            """
            separator_color = "#888"
        else:
            badge_style = """
                QLabel {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #ffffff, stop:1 #e8e8ec);
                    color: #1f2937;
                    padding: 4px 10px;
                    border-radius: 5px;
                    font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
                    font-weight: bold;
                    font-size: 10pt;
                    border: 1px solid #c0c0c8;
                    border-bottom: 2px solid #a0a0a8;
                }
            """
            separator_color = "#666"

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
                        or_label.setStyleSheet(
                            f"color: {separator_color}; font-size: 10pt; margin: 0 6px;"
                        )
                        layout.addWidget(or_label)
                    badge = QLabel(alt.strip())
                    badge.setStyleSheet(badge_style)
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
                        plus.setStyleSheet(
                            f"font-weight: bold; color: {separator_color}; font-size: 11pt;"
                        )
                        layout.addWidget(plus)
                    badge = QLabel(part)
                    badge.setStyleSheet(badge_style)
                    layout.addWidget(badge)
            else:
                # Single key
                badge = QLabel(key_text.strip())
                badge.setStyleSheet(badge_style)
                layout.addWidget(badge)

            layout.addStretch()
            return container

        # Store all shortcut widgets for filtering
        all_shortcut_widgets = []

        def create_category_section(icon, name, shortcuts):
            """Create a styled category section."""
            section = QFrame()
            if is_dark:
                section.setStyleSheet(
                    """
                    QFrame {
                        background-color: rgba(255, 255, 255, 0.03);
                        border-radius: 10px;
                        padding: 8px;
                    }
                """
                )
                header_style = """
                    font-size: 12pt;
                    font-weight: bold;
                    color: #a78bfa;
                    padding-bottom: 6px;
                    border-bottom: 1px solid rgba(167, 139, 250, 0.3);
                """
            else:
                section.setStyleSheet(
                    """
                    QFrame {
                        background-color: rgba(0, 0, 0, 0.03);
                        border-radius: 10px;
                        padding: 8px;
                    }
                """
                )
                header_style = """
                    font-size: 12pt;
                    font-weight: bold;
                    color: #7c3aed;
                    padding-bottom: 6px;
                    border-bottom: 1px solid rgba(124, 58, 237, 0.3);
                """

            layout = QVBoxLayout(section)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(10)

            # Category header
            header = QLabel(f"{icon}  {name}" if icon else name)
            header.setStyleSheet(header_style)
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
                # Use palette text color for theme compatibility
                action_label.setStyleSheet("font-size: 10.5pt;")

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
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet(
            """
            QPushButton {
                padding: 8px 20px;
                font-size: 11pt;
            }
        """
        )
        close_btn.clicked.connect(dialog.accept)
        close_btn.setDefault(True)
        footer.addWidget(close_btn)

        main_layout.addLayout(footer)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
