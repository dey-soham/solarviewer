"""
Showing that something is going on: everything that says so goes through here.

:func:`activity` (or :func:`begin_activity`) announces a piece of work. If it
outlasts a moment, a pill low in the window says what it is, with a spinner:

    with activity("Reprojecting sun.fits…", parent=self):
        convert(...)

    task = begin_activity("Fetching images…", parent=self, blocking=False)
    task.update(done=3, total=12)   # any thread
    task.finish()

``blocking=True`` (the default, for work on the GUI thread) also sets the wait
cursor; ``busy()`` in ``ui.busy`` is this without a label. Use
``blocking=False`` for work in a thread or process that leaves the window
usable, and :func:`track_thread` for a QThread.

While the GUI thread is held up nothing in the window can paint, whichever
thread the work is on, so the pill is also drawn by a small process of its own
(see ``ui.indicator``), which takes over for as long as the heartbeat sent
from here stops. It does so for stalls nobody announced too, saying what the
status bar last said was under way (:func:`hint`). Where it cannot run (macOS,
native Wayland, where it could not sit over another process's window, or
without a display) the pill is the application's own and pauses in such a
stall, so slow work belongs off the GUI thread regardless: see
``ui.background``.

Inside a window, :class:`Spinner` and :class:`ActivityLabel` show work that
belongs to one place. All spinners turn from one clock and one timer.
"""

import json
import logging
import os
import select
import subprocess
import sys
import threading
import time
import weakref
from contextlib import contextmanager

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QObject, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSplashScreen,
    QWidget,
)

from . import indicator
from .indicator import paint_pill, paint_ring, pill_rect, pill_text, spin_now
from .qt_exit import before_qt_exit
from .styles import theme_manager

logger = logging.getLogger(__name__)

FRAME_MS = 33
SHOW_AFTER_MS = int(indicator.SHOW_AFTER_S * 1000)
HEARTBEAT_MS = int(indicator.HEARTBEAT_S * 1000)
DEFAULT_LABEL = "Working…"


def _gui_thread():
    return (
        QApplication.instance() is not None
        and threading.current_thread() is threading.main_thread()
    )


def _alive(widget):
    return widget is not None and not sip.isdeleted(widget)


class _Pipe:
    """The pipe to the indicator process. Writing never waits: the GUI thread
    must not hang on the process that is there for when it hangs."""

    def __init__(self):
        self._process = None
        self._lock = threading.Lock()

    def start(self):
        path = os.path.abspath(indicator.__file__)
        env = dict(os.environ, QT_QPA_PLATFORM="xcb")
        # Scaled as the application (which keeps its scale out of os.environ)
        app = QApplication.instance()
        scale = app.property("solarviewerUiScale") if app is not None else None
        if scale is not None:
            env["QT_SCALE_FACTOR"] = str(scale)
        try:
            self._process = subprocess.Popen(
                [sys.executable, path],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except OSError as e:
            logger.info(f"No indicator process: {e}")
            return False
        os.set_blocking(self._process.stdin.fileno(), False)
        return True

    @property
    def running(self):
        return self._process is not None and self._process.poll() is None

    def send(self, message):
        with self._lock:
            if self._process is None:
                return
            data = json.dumps(message, default=str).encode() + b"\n"
            if len(data) > select.PIPE_BUF:
                return  # only this much is written whole or not at all
            try:
                os.write(self._process.stdin.fileno(), data)
            except BlockingIOError:
                pass  # it is not reading: a heartbeat or two can go missing
            except (OSError, ValueError):
                self._process = None

    def stop(self):
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        try:
            process.stdin.close()  # it leaves when the pipe ends
            process.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait()


class Activity:
    """A piece of work announced with :func:`begin_activity`. ``update`` may
    be called from any thread; ``finish`` from the GUI thread, more than once
    if need be."""

    def __init__(self, label, window=None, blocking=False):
        self.label = label or DEFAULT_LABEL
        self.done = None
        self.total = None
        self.blocking = blocking
        self.finished = False
        self.begun = time.monotonic()
        self.window = weakref.ref(window) if window is not None else None

    @property
    def place(self):
        """The window to show on, or None: the one it was begun for, or (it
        has gone, or its wrapper has) the one in front."""
        window = self.window() if self.window is not None else None
        return window if _alive(window) else _front_window()

    @property
    def text(self):
        return pill_text(self.label, self.done, self.total)

    @property
    def fraction(self):
        return (self.done or 0) / self.total if self.total else None

    def update(self, label=None, done=None, total=None):
        """Change what the pill says: ``label``, and how far along (``done``
        of ``total``) if that is known."""
        if label is not None:
            self.label = label
        if done is not None:
            self.done = done
        if total is not None:
            self.total = total
        _hub().updated(self, label, done, total)

    def finish(self):
        if not self.finished:
            self.finished = True
            _hub().finish(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.finish()
        return False


class _NoActivity(Activity):
    """What work off the GUI thread gets when it announces itself: only the
    GUI thread can show anything."""

    def update(self, label=None, done=None, total=None):
        pass

    def finish(self):
        pass


class _Part(Activity):
    """Unlabelled work inside other work: the pill goes on saying what the
    outer work is, and this reports to it."""

    def __init__(self, outer):
        super().__init__(outer.label)
        self.outer = outer

    def update(self, label=None, done=None, total=None):
        self.outer.update(label, done, total)

    def finish(self):
        pass


class _Pill(QWidget):
    """The pill itself, over a window's contents."""

    def __init__(self, window):
        super().__init__(window)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("Working")
        self.text = ""
        self.fraction = None
        self.shown_at = 0.0
        self.hide()
        window.installEventFilter(self)

    def present(self, text, fraction):
        self.text = text
        self.fraction = fraction
        self.setGeometry(pill_rect((self.window().width(), self.window().height()), text, self.font()))
        if not self.isVisible():
            self.shown_at = time.monotonic()
            self.show()
        self.raise_()
        self.update()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize and self.isVisible():
            self.present(self.text, self.fraction)
        return False

    def paintEvent(self, _event):
        painter = QPainter(self)
        opacity = min((time.monotonic() - self.shown_at) / indicator.FADE_S, 1.0)
        paint_pill(
            painter,
            self.width(),
            self.text,
            self.font(),
            spin_now(),
            _colours(),
            self.fraction,
            opacity,
        )
        painter.end()


def _colours():
    palette = theme_manager.palette
    return {
        "fill": palette["surface_elevated"],
        "line": palette["border"],
        "text": palette["text"],
        "accent": palette["highlight"],
        "track": palette["text_secondary"],
    }


class _Hub(QObject):
    _changed = pyqtSignal(object)  # an Activity updated, from any thread

    def __init__(self):
        super().__init__()
        self.activities = []
        self.held = 0  # waits showing a dialog of their own, which say it all
        self.pills = weakref.WeakKeyDictionary()  # window -> its _Pill
        self.spinners = set()
        self.pipe = _Pipe()
        self.next_id = 1
        self.ids = {}  # Activity -> the id the indicator process knows it by
        self._changed.connect(self._refresh, Qt.QueuedConnection)
        self.frames = QTimer(self)
        self.frames.setInterval(FRAME_MS)
        self.frames.timeout.connect(self._frame)
        self.heart = QTimer(self)
        self.heart.setInterval(HEARTBEAT_MS)
        self.heart.timeout.connect(self._pulse)
        self.reveal = QTimer(self)
        self.reveal.setSingleShot(True)
        self.reveal.timeout.connect(self._refresh)

    # The indicator process

    def start_indicator(self):
        if self.pipe.running or not self._indicator_works_here():
            return
        if self.pipe.start():
            self.send_style()
            self._pulse()
            self.heart.start()

    def _indicator_works_here(self):
        # On native Wayland no window can be placed, nor sit over another
        # process's; on macOS it would come up as an application in the Dock
        if os.environ.get("SOLARVIEWER_NO_INDICATOR"):
            return False
        return sys.platform.startswith("linux") and QApplication.platformName() == "xcb"

    @property
    def independent(self):
        """Whether something keeps moving when the GUI thread stops."""
        return self.pipe.running

    def send_style(self):
        self.pipe.send(
            {
                "t": "style",
                "colours": _colours(),
                "font": QApplication.font().toString(),
            }
        )

    def on_theme(self, _theme):
        self.send_style()

    def _anchor(self, window=None):
        """The window to show over, for the indicator process."""
        window = window if _alive(window) else _front_window()
        if window is None or not window.isVisible() or window.isMinimized():
            return None
        geometry = window.geometry()
        return {
            "wid": int(window.winId()),
            "geometry": [geometry.x(), geometry.y(), geometry.width(), geometry.height()],
            "active": QApplication.applicationState() == Qt.ApplicationActive,
        }

    def _pulse(self):
        self.pipe.send({"t": "pulse", "anchor": self._anchor(self._current_window())})

    def _current_window(self):
        for activity in reversed(self.activities):
            if activity.place is not None:
                return activity.place
        return None

    # Activities

    def begin(self, label, parent, blocking):
        if not label and self.activities:
            return _Part(self.activities[-1])
        window = _window_of(parent)
        activity = Activity(label, window, blocking)
        self.activities.append(activity)
        self.ids[activity] = self.next_id
        self.pipe.send(
            {
                "t": "begin",
                "id": self.next_id,
                "label": activity.label,
                "anchor": self._anchor(window),
            }
        )
        self.next_id += 1
        if blocking and label and not self.independent and window is not None:
            # No timer will run to show it, nor another frame be painted:
            # this one has to be complete
            activity.begun -= indicator.SHOW_AFTER_S
            self._show_pill(window, activity)
            pill = self.pills[window]
            pill.shown_at -= indicator.FADE_S
            pill.repaint()
            self._run_frames()
        else:
            self.reveal.start(SHOW_AFTER_MS)
        return activity

    def updated(self, activity, label, done, total):
        number = self.ids.get(activity)
        if number is not None:
            self.pipe.send({"t": "update", "id": number, "label": label, "done": done, "total": total})
        self._changed.emit(activity)

    def finish(self, activity):
        if activity in self.activities:
            self.activities.remove(activity)
        number = self.ids.pop(activity, None)
        if number is not None:
            self.pipe.send({"t": "end", "id": number})
        self._refresh()

    def refresh(self):
        self._refresh()

    def _refresh(self, *_):
        """Each window's pill says its latest activity that has been going
        for more than a moment; the others are hidden."""
        now = time.monotonic()
        latest = {}
        for activity in self.activities if not self.held else ():
            window = activity.place
            if window is None:
                continue
            wait = indicator.SHOW_AFTER_S - (now - activity.begun)
            if wait <= 0.005:
                latest[window] = activity
            else:
                self.reveal.start(int(wait * 1000) + 1)
        for window, pill in list(self.pills.items()):
            if window not in latest and _alive(pill):
                pill.hide()
        for window, activity in latest.items():
            self._show_pill(window, activity)
        self._run_frames()

    def _show_pill(self, window, activity):
        pill = self.pills.get(window)
        if pill is None or not _alive(pill):
            pill = self.pills[window] = _Pill(window)
        count = activity
        if not activity.total:
            # A step of a batch: the count of the batch stays in view
            for outer in reversed(self.activities[: self.activities.index(activity)]):
                if outer.total:
                    count = outer
                    break
        pill.present(pill_text(activity.label, count.done, count.total), count.fraction)

    # Frames

    def add_spinner(self, spinner):
        self.spinners.add(spinner)
        self._run_frames()

    def remove_spinner(self, spinner):
        self.spinners.discard(spinner)
        self._run_frames()

    def _run_frames(self):
        needed = self.spinners or any(_alive(pill) and pill.isVisible() for pill in self.pills.values())
        if needed and not self.frames.isActive():
            self.frames.start()
        elif not needed and self.frames.isActive():
            self.frames.stop()

    def _frame(self):
        for spinner in list(self.spinners):
            if _alive(spinner):
                spinner.update()
            else:
                self.spinners.discard(spinner)
        for pill in self.pills.values():
            if _alive(pill) and pill.isVisible():
                pill.update()
        self._run_frames()

    def shutdown(self):
        self.heart.stop()
        self.frames.stop()
        self.reveal.stop()
        self.pipe.stop()
        for window, pill in list(self.pills.items()):
            if _alive(window) and _alive(pill):
                window.removeEventFilter(pill)
        self.pills.clear()
        self.spinners.clear()
        self.activities.clear()
        self.ids.clear()


_the_hub = None
_closed = False  # taken down at exit, with the application


class _NoHub:
    """Stands in once the hub has gone: work still ending says so to nobody."""

    independent = False
    activities = ()

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _hub():
    global _the_hub
    if _closed:
        return _NoHub()
    if _the_hub is not None and sip.isdeleted(_the_hub.frames):
        # Its timers went with an application that has been replaced
        _the_hub.pipe.stop()
        theme_manager.unregister_owner(_the_hub)
        _the_hub = None
    if _the_hub is None:
        _the_hub = _Hub()
        theme_manager.register_callback(_the_hub.on_theme, owner=_the_hub)
    return _the_hub


def _shutdown():
    global _the_hub, _closed
    _closed = True
    if _the_hub is not None:
        theme_manager.unregister_owner(_the_hub)
        _the_hub.shutdown()
        _the_hub = None


# Timers and event filters must go before the windows do (see qt_exit)
before_qt_exit(_shutdown)


def _front_window():
    """The window the user is looking at, or None."""
    window = QApplication.activeModalWidget() or QApplication.activeWindow()
    if window is not None and not isinstance(window, QSplashScreen):
        return window.window()
    return None


def _window_of(parent):
    if _alive(parent):
        window = parent.window()
        if window.isVisible():
            return window
    return _front_window()


def start_indicator():
    """Start what keeps a pill moving while the GUI thread is held up. Called
    as the application starts, as nothing can be started during a stall."""
    if QApplication.instance() is not None:
        _hub().start_indicator()


def stop_indicator():
    if _the_hub is not None:
        _the_hub.pipe.stop()
        _the_hub.heart.stop()


def begin_activity(label=None, parent=None, blocking=True):
    """Announce work until the returned :class:`Activity` is finished.

    ``parent`` is any widget of the window it belongs to (by default the one
    in front); ``blocking`` is whether it holds up that window, in which case
    :func:`ui.busy.begin_busy` is the usual way in, for the cursor.
    """
    if not _gui_thread() or _closed:
        return _NoActivity(label)
    return _hub().begin(label, parent, blocking)


def activity(label=None, parent=None, blocking=True):
    """As :func:`begin_activity`, for a ``with`` block. With ``blocking``, the
    wait cursor is on for the block too."""
    if blocking:
        from .busy import busy

        return busy(label, parent)
    return begin_activity(label, parent, blocking=False)


@contextmanager
def pills_held():
    """No pill while a dialog that says what is going on is up: it would say
    the same thing twice."""
    hub = _hub()
    hub.held += 1
    hub.refresh()
    try:
        yield
    finally:
        hub.held -= 1
        hub.refresh()


def track_thread(thread, label, parent=None):
    """Show ``label`` until the QThread ``thread`` has finished; returns the
    :class:`Activity`, for the thread's progress."""
    task = begin_activity(label, parent, blocking=False)
    thread.finished.connect(task.finish)
    return task


def hint(text):
    """What the application says it is doing (a status message): a stall that
    was not announced is labelled with it, if it says work is under way."""
    if _the_hub is not None and text:
        text = str(text)
        ongoing = text.rstrip().endswith(("…", "..."))
        _the_hub.pipe.send({"t": "hint", "text": text if ongoing else None})


class Spinner(QWidget):
    """A slim ring with an arc of the accent running round it. Hidden until
    shown, and it turns only while visible."""

    def __init__(self, diameter=16, parent=None):
        super().__init__(parent)
        self.diameter = diameter
        self.setFixedSize(diameter + 4, diameter + 4)
        self.setAccessibleName("Working")
        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        _hub().add_spinner(self)

    def hideEvent(self, event):
        super().hideEvent(event)
        _hub().remove_spinner(self)

    def paintEvent(self, _event):
        palette = theme_manager.palette
        track = QColor(palette["text_secondary"])
        track.setAlphaF(0.25)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(2, 2, self.diameter, self.diameter)
        width = max(2.0, self.diameter / 9.0)
        paint_ring(painter, rect, spin_now(), QColor(palette["highlight"]), track, width)
        painter.end()


class ActivityLabel(QWidget):
    """A spinner and a line of text, for where a label said "Loading…":
    ``start(text)`` while the work goes on, ``stop(text)`` when it is over."""

    def __init__(self, text="", parent=None, diameter=14):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.spinner = Spinner(diameter, self)
        self.label = QLabel(text, self)
        layout.addWidget(self.spinner)
        layout.addWidget(self.label, 1)

    def start(self, text=None):
        if text is not None:
            self.label.setText(text)
        self.spinner.show()

    def stop(self, text=""):
        self.spinner.hide()
        self.label.setText(text)

    def setText(self, text):
        self.label.setText(text)

    def text(self):
        return self.label.text()

    @property
    def running(self):
        return not self.spinner.isHidden()


__all__ = [
    "Activity",
    "ActivityLabel",
    "Spinner",
    "activity",
    "begin_activity",
    "hint",
    "pills_held",
    "start_indicator",
    "stop_indicator",
    "track_thread",
]
