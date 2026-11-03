"""
The busy pill, and the small process that keeps it moving.

A Qt window paints on its GUI thread, so when that thread is busy (a
Matplotlib draw, building a window) every spinner in it stops, and no other
thread may paint a widget. This file also runs as a process of its own: its
one window sits over the application's, drawn exactly like the pill the
application draws for itself, and takes over while the application cannot
paint. ``ui.activity`` starts it and tells it what is going on down a pipe;
the application's heartbeat stopping is what tells it to step in, so a stall
nobody announced gets a pill too.

The painting here is shared by both sides, so the hand-over does not show.
This file imports nothing but PyQt5: the package's own imports are far too
heavy for a process that has to be up in a moment.
"""

import json
import math
import os
import select
import signal
import sys
import time

from PyQt5.QtCore import QRect, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QWindow

TURN_S = 1.4  # one turn of a spinner's arc

PILL_HEIGHT = 38
PILL_MARGIN = 14  # inside the pill, left and right
PILL_RING = 18  # its spinner's diameter
PILL_GAP = 10  # between the spinner and the text
PILL_ABOVE_BOTTOM = 56  # clear of a status bar
PILL_MIN_WIDTH = 120
PILL_MAX_WIDTH = 460
PILL_BESIDE = 24  # least room kept at each side of the window

SHOW_AFTER_S = 0.2  # work shorter than this shows nothing
HEARTBEAT_S = 0.1  # how often the application says it is alive
STALL_S = 0.3  # no heartbeat for this long: it is not painting
WATCHDOG_S = 0.8  # how long an unannounced stall goes before a pill shows
STEADY_S = 0.3  # heartbeats this regular for this long: it paints again
HOLD_S = 0.25  # a pill stays up at least this long, so that it never flashes
FADE_S = 0.1

DEFAULT_COLOURS = {
    "fill": "#22242c",
    "line": "#3a3d48",
    "text": "#e8e8ec",
    "accent": "#4c8dff",
    "track": "#8a8d98",
}


def spin_now():
    """How far through its turn a spinner is, from the clock: so that every
    spinner agrees, and one that was held up carries on from the right place."""
    return (time.monotonic() / TURN_S) % 1.0


def paint_ring(painter, rect, spin, accent, track, width):
    """A slim ring in ``rect``, an arc of the accent running round it, its
    length breathing as it goes."""
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(track, width, Qt.SolidLine, Qt.RoundCap))
    painter.drawEllipse(rect)
    # Qt arcs: degrees from 3 o'clock, anticlockwise, in sixteenths
    head = -spin * 360
    span = 50 + 130 * (0.5 - 0.5 * math.cos(spin * 2 * math.pi * 2))
    painter.setPen(QPen(accent, width, Qt.SolidLine, Qt.RoundCap))
    painter.drawArc(rect, int(head * 16), int(-span * 16))


def pill_text(label, done=None, total=None):
    """What a pill says."""
    if total:
        return f"{label}  {min(done or 0, total)}/{total}"
    return label


def pill_rect(window, text, font):
    """Where the pill saying ``text`` goes in a window of size ``window``
    (width, height): low and in the middle."""
    room = max(PILL_MIN_WIDTH, min(PILL_MAX_WIDTH, window[0] - 2 * PILL_BESIDE))
    wanted = (
        QFontMetrics(font).horizontalAdvance(text)
        + 2 * PILL_MARGIN
        + PILL_RING
        + PILL_GAP
    )
    width = max(PILL_MIN_WIDTH, min(room, wanted))
    return QRect(
        (window[0] - width) // 2,
        max(0, window[1] - PILL_HEIGHT - PILL_ABOVE_BOTTOM),
        width,
        PILL_HEIGHT,
    )


def paint_pill(painter, width, text, font, spin, colours, fraction=None, opacity=1.0):
    """A pill of ``width`` at (0, 0): spinner, ``text``, and along its foot a
    line ``fraction`` of the way across (if how far along is known)."""
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setOpacity(opacity)
    body = QRectF(0.5, 0.5, width - 1, PILL_HEIGHT - 1)
    radius = body.height() / 2
    painter.setPen(QPen(QColor(colours["line"]), 1))
    fill = QColor(colours["fill"])
    fill.setAlphaF(0.96)
    painter.setBrush(fill)
    painter.drawRoundedRect(body, radius, radius)

    track = QColor(colours["track"])
    track.setAlphaF(0.25)
    top = (PILL_HEIGHT - PILL_RING) / 2
    ring = QRectF(PILL_MARGIN, top, PILL_RING, PILL_RING)
    paint_ring(painter, ring, spin, QColor(colours["accent"]), track, 2.5)

    left = PILL_MARGIN + PILL_RING + PILL_GAP
    room = width - left - PILL_MARGIN
    painter.setFont(font)
    painter.setPen(QColor(colours["text"]))
    shown = QFontMetrics(font).elidedText(text, Qt.ElideMiddle, room)
    painter.drawText(QRect(left, 0, room, PILL_HEIGHT), Qt.AlignVCenter | Qt.AlignLeft, shown)

    if fraction is not None:
        span = (width - 2 * radius) * min(max(fraction, 0.0), 1.0)
        painter.setPen(QPen(QColor(colours["accent"]), 2, Qt.SolidLine, Qt.RoundCap))
        y = PILL_HEIGHT - 2.5
        painter.drawLine(int(radius), int(y), int(radius + span), int(y))


class Monitor:
    """What the application has said, and from that what to show: no Qt, so
    that the decision can be tested on its own."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.activities = {}  # id -> {"label", "done", "total", "begun"}
        self.anchor = None  # {"wid", "geometry", "active"}: its current window
        self.hint = None  # what the application last said it was doing
        self.last_pulse = None
        self.steady_since = None
        self.shown_at = None

    def handle(self, message):
        """Take in one message from the application."""
        kind = message.get("t")
        now = self.clock()
        if kind == "pulse":
            if self.last_pulse is None or now - self.last_pulse > STALL_S:
                self.steady_since = now
            self.last_pulse = now
            self.anchor = message.get("anchor")
        elif kind == "begin":
            self.activities[message["id"]] = {
                "label": message["label"],
                "done": None,
                "total": None,
                "begun": now,
            }
            if message.get("anchor"):
                self.anchor = message["anchor"]
        elif kind == "update":
            activity = self.activities.get(message["id"])
            if activity is not None:
                for key in ("label", "done", "total"):
                    if message.get(key) is not None:
                        activity[key] = message[key]
        elif kind == "end":
            self.activities.pop(message["id"], None)
        elif kind == "hint":
            self.hint = message.get("text")

    def wanted(self):
        """The activity to show (a dict with a ``label``), or None."""
        if self.last_pulse is None or self.anchor is None:
            return None
        now = self.clock()
        gap = now - self.last_pulse
        if gap <= STALL_S:
            return None
        if self.activities:
            running = list(self.activities.values())
            latest = running[-1]
            if now - latest["begun"] < SHOW_AFTER_S:
                return None
            if not latest["total"]:
                # A step of a batch: the count of the batch stays in view
                for outer in reversed(running):
                    if outer["total"]:
                        return dict(latest, done=outer["done"], total=outer["total"])
            return latest
        if gap >= WATCHDOG_S and self.anchor.get("active"):
            return {"label": self.hint or "Working…"}
        return None

    def may_hide(self):
        """Whether a pill now showing can go: the application paints again
        (and has been painting steadily, if it is still at something, so that
        work that only sometimes lets it paint does not make the pill blink)."""
        now = self.clock()
        if self.shown_at is not None and now - self.shown_at < HOLD_S:
            return False
        if self.last_pulse is None or now - self.last_pulse > STALL_S:
            return False
        if not self.activities:
            return True
        return self.steady_since is not None and now - self.steady_since >= STEADY_S


def _main():
    from PyQt5.QtWidgets import QApplication, QWidget

    # Same scaling as the application (its QT_SCALE_FACTOR is passed in the environment)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    policy = getattr(Qt, "HighDpiScaleFactorRoundingPolicy", None)
    if policy is not None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(policy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    class Hud(QWidget):
        def __init__(self):
            super().__init__(
                None,
                Qt.Tool
                | Qt.FramelessWindowHint
                | Qt.WindowDoesNotAcceptFocus
                | Qt.WindowTransparentForInput,
            )
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.colours = dict(DEFAULT_COLOURS)
            self.pill_font = QFont()
            self.text = ""
            self.fraction = None
            self.width_now = PILL_MIN_WIDTH
            self.foreign = None

        def present(self, monitor, activity):
            done, total = activity.get("done"), activity.get("total")
            self.text = pill_text(activity["label"], done, total)
            self.fraction = (done or 0) / total if total else None
            x, y, width, height = monitor.anchor["geometry"]
            place = pill_rect((width, height), self.text, self.pill_font)
            self.width_now = place.width()
            self.setGeometry(x + place.x(), y + place.y(), place.width(), place.height())
            if not self.isVisible():
                self._follow(monitor.anchor.get("wid"))
                monitor.shown_at = monitor.clock()
                self.show()
                self.raise_()
            self.update()

        def _follow(self, wid):
            """Stay with the application's window, not over everything."""
            if not wid:
                return
            self.winId()
            self.foreign = QWindow.fromWinId(wid)
            self.windowHandle().setTransientParent(self.foreign)

        def paintEvent(self, _event):
            shown = time.monotonic() - (monitor.shown_at or 0)
            painter = QPainter(self)
            paint_pill(
                painter,
                self.width_now,
                self.text,
                self.pill_font,
                spin_now(),
                self.colours,
                self.fraction,
                min(shown / FADE_S, 1.0),
            )
            painter.end()

    hud = Hud()
    monitor = Monitor()
    pending = bytearray()

    def read_messages():
        """Everything waiting in the pipe. False once the application has
        gone: this process has no reason to outlive it."""
        while select.select([0], [], [], 0)[0]:
            chunk = os.read(0, 65536)
            if not chunk:
                return False
            pending.extend(chunk)
        while True:
            end = pending.find(b"\n")
            if end < 0:
                return True
            line = bytes(pending[:end])
            del pending[: end + 1]
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("t") == "style":
                hud.colours.update(message.get("colours") or {})
                if message.get("font"):
                    hud.pill_font.fromString(message["font"])
            else:
                monitor.handle(message)

    application = os.getppid()

    def tick():
        # A child of the application can hold the pipe open after it has gone
        if not read_messages() or os.getppid() != application:
            app.quit()
            return
        activity = monitor.wanted()
        if activity is not None:
            hud.present(monitor, activity)
        elif hud.isVisible() and monitor.may_hide():
            hud.hide()
        ticker.setInterval(16 if hud.isVisible() else 50)

    ticker = QTimer()
    ticker.timeout.connect(tick)
    ticker.start(50)
    sys.exit(app.exec_())


if __name__ == "__main__":
    # Run as a script, this folder heads the import path: its modules are the
    # package's, and must not stand in for anything PyQt5 imports
    sys.path.pop(0)
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is for the application
    _main()
