from PyQt5.QtWidgets import QSplashScreen, QWidget
from PyQt5.QtGui import (
    QPixmap,
    QColor,
    QPainter,
    QBrush,
    QPen,
    QFont,
    QRadialGradient,
    QLinearGradient,
    QPainterPath,
)
from PyQt5.QtCore import Qt, QRectF, QTimer, QPointF
import os
import math
import time
from .ui.styles import theme_manager


FRAME_S = 0.03  # between frames of the animation


class ModernSplashScreen(QSplashScreen):
    """
    A premium branded splash screen for SolarViewer.
    Adapts to dark/light theme with golden sun accents and subtle animations.
    """

    # Brand colors - golden sun theme (consistent across themes)
    SOLAR_GOLD = "#d4a535"
    SOLAR_AMBER = "#e8b84a"
    SOLAR_ORANGE = "#f5a623"
    CORONA_GLOW = "#ffd700"

    def __init__(self, pixmap=None, version="1.0.0"):
        if not pixmap:
            pixmap = QPixmap(460, 320)
            pixmap.fill(Qt.transparent)

        super().__init__(pixmap)

        self.version = version
        self.is_dark = theme_manager.is_dark

        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.SplashScreen
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Setup theme-specific colors
        self._setup_colors()

        # UI State
        self.loading_text = "Starting…"
        self._target_progress = 0  # Target progress value
        self._display_progress = 0.0  # Smoothly animated display value

        # Animation
        self._glow_phase = 0.0
        self._last_frame = time.monotonic()
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._animate)
        self._pulse_timer.start(int(FRAME_S * 1000))

        # Load icon
        self.icon_pixmap = None
        self._load_icon()

    def _setup_colors(self):
        """Configure colors based on theme."""
        if self.is_dark:
            # Dark theme - space black with warm glow
            self.bg_primary = QColor("#080810")
            self.bg_secondary = QColor("#0c0c18")
            self.text_primary = QColor("#f8f8fc")
            self.text_secondary = QColor("#7a7a8c")
            self.border_alpha = 96
        else:
            # Light theme - clean white with warm accents
            self.bg_primary = QColor("#ffffff")
            self.bg_secondary = QColor("#faf8f5")
            self.text_primary = QColor("#1a1a1a")
            self.text_secondary = QColor("#6b6b7a")
            self.border_alpha = 80

    def _load_icon(self):
        """Load the app icon."""
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(base_dir, "assets", "icon.png")
            if os.path.exists(icon_path):
                self.icon_pixmap = QPixmap(icon_path).scaled(
                    80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
        except Exception:
            pass

    def _advance(self):
        """Move the glow and the bar on by the time gone since the last frame."""
        now = time.monotonic()
        frames = max(1, round((now - self._last_frame) / FRAME_S))
        self._last_frame = now
        self._glow_phase = (self._glow_phase + 0.06 * frames) % (2 * math.pi)

        # Smoothly interpolate display progress toward target
        for _ in range(min(frames, 100)):
            if self._display_progress >= self._target_progress:
                break
            diff = self._target_progress - self._display_progress
            step = max(0.2, diff * 0.1)
            self._display_progress = min(
                self._target_progress, self._display_progress + step
            )

    def _animate(self):
        """Update animation phase and smooth progress."""
        self._advance()
        self.update()

    @property
    def progress(self):
        """Get the display progress value."""
        return self._display_progress

    def show_message(self, message):
        """Update loading message."""
        self.loading_text = message
        self.update()

    def set_progress(self, value):
        """Set target progress (0-100). Display animates smoothly to this
        value; it only goes forward, whatever order the steps report in."""
        self._target_progress = max(self._target_progress, min(100, value))
        self.update()

    def step(self, message, value):
        """Say what the GUI thread does next, on screen before it does: no
        timer runs meanwhile, so these steps are all that moves the splash
        while the window is built. Not processEvents, as QSplashScreen's own
        repaint() does: that would run what is queued for a window still being
        built."""
        self.show_message(message)
        self.set_progress(value)
        self._advance()
        QWidget.repaint(self)

    def finish(self, widget):
        """The window is up: the bar is full, and the splash goes as soon as
        the window is on screen (QSplashScreen waits for that, up to a second,
        so ``widget`` must be shown first)."""
        self._pulse_timer.stop()
        self._target_progress = 100
        self._display_progress = 100
        QWidget.repaint(self)
        super().finish(widget)

    def paintEvent(self, event):
        """Paint the splash screen."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        margin = 10
        rect = self.rect().adjusted(margin, margin, -margin, -margin)

        # === Background with depth ===
        self._draw_background(painter, rect)

        # === Corona glow effect ===
        self._draw_corona_glow(painter, rect)

        # === Icon (vertically centered in content area above progress) ===
        content_height = rect.height() - 70
        icon_center_y = rect.top() + (content_height / 2) - 20
        self._draw_icon(painter, rect, icon_center_y)

        # === Text content ===
        self._draw_text(painter, rect, icon_center_y)

        # === Progress ===
        self._draw_progress(painter, rect)

    def _draw_background(self, painter, rect):
        """Draw the themed background."""
        # Outer shadow
        for i in range(3, 0, -1):
            shadow_rect = rect.adjusted(-i * 2, -i * 2, i * 2, i * 2)
            if self.is_dark:
                shadow_color = QColor(0, 0, 0, 40 * (4 - i))
            else:
                shadow_color = QColor(0, 0, 0, 20 * (4 - i))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(shadow_color))
            painter.drawRoundedRect(shadow_rect, 16 + i, 16 + i)

        # Main background gradient
        bg_gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg_gradient.setColorAt(0, self.bg_secondary)
        bg_gradient.setColorAt(0.5, self.bg_primary)
        bg_gradient.setColorAt(1, self.bg_secondary)

        painter.setBrush(QBrush(bg_gradient))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 14, 14)

        # Subtle star field effect (dark theme only)
        if self.is_dark:
            painter.setPen(QPen(QColor(255, 255, 255, 15), 1))
            star_positions = [
                (0.1, 0.2),
                (0.85, 0.15),
                (0.75, 0.8),
                (0.15, 0.75),
                (0.5, 0.1),
                (0.9, 0.5),
                (0.05, 0.45),
                (0.6, 0.85),
            ]
            for sx, sy in star_positions:
                x = rect.left() + rect.width() * sx
                y = rect.top() + rect.height() * sy
                painter.drawPoint(int(x), int(y))

        # Golden border accent
        border_color = QColor(self.SOLAR_GOLD)
        border_color.setAlpha(self.border_alpha)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border_color, 1.5))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 14, 14)

    def _draw_corona_glow(self, painter, rect):
        """Draw animated solar corona glow behind icon area."""
        center_x = rect.center().x()
        content_height = rect.height() - 70
        center_y = rect.top() + (content_height / 2) - 20

        # Pulsing effect
        pulse = 0.7 + 0.3 * math.sin(self._glow_phase)
        corona_size = 90 + 15 * math.sin(self._glow_phase * 0.7)

        if self.is_dark:
            # Dark theme: vibrant orange/gold corona like the original
            corona = QRadialGradient(center_x, center_y, corona_size)
            corona.setColorAt(
                0, QColor(self.CORONA_GLOW + hex(int(40 * pulse))[2:].zfill(2))
            )
            corona.setColorAt(
                0.3, QColor(self.SOLAR_AMBER + hex(int(25 * pulse))[2:].zfill(2))
            )
            corona.setColorAt(
                0.6, QColor(self.SOLAR_GOLD + hex(int(12 * pulse))[2:].zfill(2))
            )
            corona.setColorAt(1, Qt.transparent)
        else:
            # Light theme: darker golden/bronze glow for contrast on white
            corona = QRadialGradient(center_x, center_y, corona_size)
            # Darker, more saturated colors for visibility
            glow1 = QColor("#c47f17")  # Darker bronze center
            glow1.setAlpha(int(100 * pulse))
            glow2 = QColor("#b8860b")  # Dark goldenrod
            glow2.setAlpha(int(65 * pulse))
            glow3 = QColor("#a67c00")  # Deep gold
            glow3.setAlpha(int(35 * pulse))
            corona.setColorAt(0, glow1)
            corona.setColorAt(0.35, glow2)
            corona.setColorAt(0.65, glow3)
            corona.setColorAt(1, Qt.transparent)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(corona))
        painter.drawEllipse(QPointF(center_x, center_y), corona_size, corona_size)

    def _draw_icon(self, painter, rect, center_y):
        """Draw the app icon with circular clip to remove background."""
        center_x = int(rect.center().x())

        if self.icon_pixmap:
            icon_x = center_x - self.icon_pixmap.width() // 2
            icon_y = int(center_y - self.icon_pixmap.height() // 2)

            # Save painter state
            painter.save()

            # Create circular clip path to hide white background
            clip_path = QPainterPath()
            icon_radius = self.icon_pixmap.width() // 2
            clip_path.addEllipse(QPointF(center_x, center_y), icon_radius, icon_radius)
            painter.setClipPath(clip_path)

            # Draw the icon (clipped to circle)
            painter.drawPixmap(icon_x, icon_y, self.icon_pixmap)

            # Restore painter state
            painter.restore()
        else:
            # Fallback
            painter.setPen(QColor(self.SOLAR_GOLD))
            painter.setFont(QFont("Segoe UI Emoji", 44))
            painter.drawText(
                QRectF(rect.left(), center_y - 30, rect.width(), 60),
                Qt.AlignCenter,
                "☀",
            )

    def _draw_text(self, painter, rect, icon_y):
        """Draw title and version."""
        # Title
        title_y = icon_y + 52

        painter.setPen(self.text_primary)
        title_font = QFont("Inter", 22)
        title_font.setWeight(QFont.DemiBold)
        title_font.setLetterSpacing(QFont.AbsoluteSpacing, 0.5)
        painter.setFont(title_font)

        painter.drawText(
            QRectF(rect.left(), title_y, rect.width(), 32),
            Qt.AlignCenter,
            "SolarViewer",
        )

        # Version
        version_y = title_y + 32
        version_text = f"v{self.version}"

        painter.setPen(QColor(self.SOLAR_GOLD))
        version_font = QFont("Inter", 10)
        version_font.setWeight(QFont.Medium)
        painter.setFont(version_font)

        painter.drawText(
            QRectF(rect.left(), version_y, rect.width(), 18),
            Qt.AlignCenter,
            version_text,
        )

    def _draw_progress(self, painter, rect):
        """Draw progress bar and status."""
        pad_x = 24
        bar_height = 3
        bottom_pad = 22

        # Status text
        status_y = rect.bottom() - bottom_pad - bar_height - 22

        painter.setPen(self.text_secondary)
        status_font = QFont("Inter", 9)
        painter.setFont(status_font)

        text_rect = QRectF(rect.left() + pad_x, status_y, rect.width() - 2 * pad_x, 18)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self.loading_text)

        # Percentage
        if self.progress > 0:
            painter.setPen(QColor(self.SOLAR_GOLD))
            painter.drawText(
                text_rect, Qt.AlignRight | Qt.AlignVCenter, f"{int(self.progress)}%"
            )

        # Progress track
        bar_y = rect.bottom() - bottom_pad - bar_height
        bar_rect = QRectF(
            rect.left() + pad_x, bar_y, rect.width() - 2 * pad_x, bar_height
        )

        track_color = QColor(self.text_secondary)
        track_color.setAlpha(40 if self.is_dark else 60)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(bar_rect, 1.5, 1.5)

        # Progress fill
        if self.progress > 0:
            fill_width = bar_rect.width() * (self.progress / 100.0)
            fill_rect = QRectF(
                bar_rect.left(), bar_rect.top(), max(bar_height, fill_width), bar_height
            )

            # Gradient from gold to amber
            fill_grad = QLinearGradient(fill_rect.left(), 0, fill_rect.right(), 0)
            fill_grad.setColorAt(0, QColor(self.SOLAR_GOLD))
            fill_grad.setColorAt(1, QColor(self.SOLAR_ORANGE))

            painter.setBrush(QBrush(fill_grad))
            painter.drawRoundedRect(fill_rect, 1.5, 1.5)
