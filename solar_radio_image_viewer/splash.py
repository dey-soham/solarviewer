from PyQt5.QtWidgets import QSplashScreen, QWidget
from PyQt5.QtGui import (
    QPixmap,
    QColor,
    QPainter,
    QBrush,
    QPen,
    QFont,
    QFontMetricsF,
    QRadialGradient,
    QLinearGradient,
    QPainterPath,
)
from PyQt5.QtCore import Qt, QRectF, QTimer, QPointF
import os
import math
import time
from .ui.styles import FONT_BODY, FONT_META, FONT_SMALL, theme_manager


FRAME_S = 0.03  # between frames of the animation
WAIT_SHOWN_S = 2.0  # a wait that has lasted this long shows its time and its hint
WAIT_GAP_S = 0.5  # waits that follow each other closer than this count as one
GLINT_S = 1.6  # the glint runs along the bar once in this time, while waiting


class ModernSplashScreen(QSplashScreen):
    """
    A premium branded splash screen for SolarViewer.
    Adapts to dark/light theme with golden sun accents and subtle animations.
    """

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

        # What holds a step up from outside (a download), see set_waiting
        self._wait_reason = ""
        self._wait_detail = ""
        self._wait_hint = ""
        self._wait_since = 0.0
        self._wait_ended = float("-inf")

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
        self.palette_keys = theme_manager.palette
        self.bg_primary = QColor(self.palette_keys["brand_ground"])
        self.bg_secondary = QColor(self.palette_keys["brand_ground_edge"])
        self.text_primary = QColor(self.palette_keys["brand_text"])
        self.text_secondary = QColor(self.palette_keys["brand_text_dim"])
        self.solar_gold = self.palette_keys["brand_gold"]
        self.solar_orange = self.palette_keys["brand_orange"]
        self.border_alpha = 96 if self.is_dark else 80

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

    def set_waiting(self, reason, detail="", hint=""):
        """Something outside holds the step up (CASA downloading its data).
        ``reason`` shows in place of the step's message, with ``detail``
        (how far it is); once it has lasted a while the time it has taken
        shows too, and ``hint`` above it (why, and what can be done about
        it). The bar, which stands still meanwhile, shows a glint running.
        An empty ``reason`` ends it; waits that follow each other at once
        count as one."""
        now = time.monotonic()
        if reason and not self._wait_reason and now - self._wait_ended > WAIT_GAP_S:
            self._wait_since = now
        if self._wait_reason and not reason:
            self._wait_ended = now
        self._wait_reason, self._wait_detail, self._wait_hint = reason, detail, hint
        self.update()

    def waited(self):
        """Seconds the wait shown has lasted (0 with none)."""
        return time.monotonic() - self._wait_since if self._wait_reason else 0.0

    def status_text(self):
        """The line above the bar: the step, or what holds it up, how far
        that is and how long it has taken."""
        if not self._wait_reason:
            return self.loading_text
        parts = [self._wait_detail] if self._wait_detail else []
        waited = int(self.waited())
        if waited >= WAIT_SHOWN_S:
            parts.append(f"{waited // 60}:{waited % 60:02d}")
        if not parts:
            return self._wait_reason
        return " · ".join([self._wait_reason.rstrip("…")] + parts)

    def hint_text(self):
        """The hint of a wait that has lasted a while ("" otherwise)."""
        return self._wait_hint if self._wait_reason and self.waited() >= WAIT_SHOWN_S else ""

    @staticmethod
    def _whole_paths(text):
        """``text`` to draw wrapped, with no line ending inside a path
        (after one of its slashes)."""
        return text.replace("/", "/\u2060")

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
        self.set_waiting("")
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

        # === Why it waits, if it does (under the version) ===
        self._draw_hint(painter, rect, icon_center_y + 52 + 32 + 18 + 2)

        # === Progress ===
        self._draw_progress(painter, rect)

    def _draw_background(self, painter, rect):
        """Draw the themed background."""
        # Outer shadow
        for i in range(3, 0, -1):
            shadow_rect = rect.adjusted(-i * 2, -i * 2, i * 2, i * 2)
            shadow_color = QColor(self.palette_keys["brand_shadow"])
            shadow_color.setAlpha((40 if self.is_dark else 20) * (4 - i))
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
            star_color = QColor(self.palette_keys["brand_star"])
            star_color.setAlpha(15)
            painter.setPen(QPen(star_color, 1))
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
        border_color = QColor(self.solar_gold)
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

        glow1, glow2, glow3 = (
            self.palette_keys[key] for key in ("brand_glow", "brand_glow_mid", "brand_glow_edge")
        )
        if self.is_dark:
            # Dark theme: vibrant orange/gold corona like the original
            corona = QRadialGradient(center_x, center_y, corona_size)
            corona.setColorAt(0, QColor(glow1 + hex(int(40 * pulse))[2:].zfill(2)))
            corona.setColorAt(0.3, QColor(glow2 + hex(int(25 * pulse))[2:].zfill(2)))
            corona.setColorAt(0.6, QColor(glow3 + hex(int(12 * pulse))[2:].zfill(2)))
            corona.setColorAt(1, Qt.transparent)
        else:
            # Light theme: darker golden/bronze glow for contrast on white
            corona = QRadialGradient(center_x, center_y, corona_size)
            # Darker, more saturated colors for visibility
            glow1 = QColor(glow1)  # Darker bronze center
            glow1.setAlpha(int(100 * pulse))
            glow2 = QColor(glow2)  # Dark goldenrod
            glow2.setAlpha(int(65 * pulse))
            glow3 = QColor(glow3)  # Deep gold
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
            # Fallback: the sun's disc
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.solar_gold))
            painter.drawEllipse(QPointF(center_x, center_y), 24, 24)

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

        painter.setPen(QColor(self.solar_gold))
        version_font = QFont("Inter", FONT_BODY)
        version_font.setWeight(QFont.Medium)
        painter.setFont(version_font)

        painter.drawText(
            QRectF(rect.left(), version_y, rect.width(), 18),
            Qt.AlignCenter,
            version_text,
        )

    PAD_X = 24  # left and right of the status line, the bar and the hint
    BAR_HEIGHT = 3
    BOTTOM_PAD = 22

    def _status_y(self, rect):
        """The top of the status line."""
        return rect.bottom() - self.BOTTOM_PAD - self.BAR_HEIGHT - 22

    def hint_layout(self, rect, top):
        """``(font, room, fits)`` to draw the hint with: the room between
        ``top`` (under the version) and the status line, and the largest of
        the small sizes at which the hint, wrapped, fits there."""
        room = QRectF(
            rect.left() + self.PAD_X, top, rect.width() - 2 * self.PAD_X, self._status_y(rect) - 5 - top
        )
        font = QFont("Inter", FONT_META)
        for size in (FONT_META, FONT_META - 1):
            font = QFont("Inter", size)
            needed = QFontMetricsF(font).boundingRect(
                QRectF(0, 0, room.width(), 1000),
                Qt.AlignLeft | Qt.TextWordWrap,
                self._whole_paths(self._wait_hint),
            )
            if needed.height() <= room.height():
                return font, room, True
        return font, room, False

    def _draw_hint(self, painter, rect, top):
        """Why the start waits and what can be done about it: small and
        wrapped, just above the status line."""
        hint = self.hint_text()
        if not hint:
            return
        font, room, fits = self.hint_layout(rect, top)
        painter.save()
        painter.setClipRect(room)
        painter.setFont(font)
        painter.setPen(self.text_secondary)
        # (one too long to fit keeps its beginning)
        align = Qt.AlignBottom if fits else Qt.AlignTop
        painter.drawText(room, Qt.AlignLeft | align | Qt.TextWordWrap, self._whole_paths(hint))
        painter.restore()

    def _draw_progress(self, painter, rect):
        """Draw progress bar and status."""
        pad_x = self.PAD_X
        bar_height = self.BAR_HEIGHT
        bottom_pad = self.BOTTOM_PAD

        # Status text
        status_y = self._status_y(rect)

        painter.setPen(self.text_secondary)
        status_font = QFont("Inter", FONT_SMALL)
        painter.setFont(status_font)

        text_rect = QRectF(rect.left() + pad_x, status_y, rect.width() - 2 * pad_x, 18)
        percent = f"{int(self.progress)}%" if self.progress > 0 else ""
        metrics = painter.fontMetrics()
        room = text_rect.width() - (metrics.horizontalAdvance(percent) + 12 if percent else 0)
        painter.drawText(
            text_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(self.status_text(), Qt.ElideRight, int(room)),
        )

        # Percentage
        if percent:
            painter.setPen(QColor(self.solar_gold))
            painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, percent)

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
            fill_grad.setColorAt(0, QColor(self.solar_gold))
            fill_grad.setColorAt(1, QColor(self.solar_orange))

            painter.setBrush(QBrush(fill_grad))
            painter.drawRoundedRect(fill_rect, 1.5, 1.5)

        # Waiting, the bar stands still: a glint runs along it, to show the
        # start has not hung
        if self._wait_reason:
            at = (time.monotonic() - self._wait_since) % GLINT_S / GLINT_S
            width = bar_rect.width() * 0.22
            left = bar_rect.left() - width + (bar_rect.width() + width) * at
            edge, middle = QColor(self.text_primary), QColor(self.text_primary)
            edge.setAlpha(0)
            middle.setAlpha(220)
            glint = QLinearGradient(left, 0, left + width, 0)
            glint.setColorAt(0, edge)
            glint.setColorAt(0.5, middle)
            glint.setColorAt(1, edge)
            painter.save()
            painter.setClipRect(bar_rect)
            painter.setBrush(QBrush(glint))
            painter.drawRect(QRectF(left, bar_rect.top(), width, bar_height))
            painter.restore()
