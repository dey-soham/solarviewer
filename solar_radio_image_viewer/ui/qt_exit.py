"""
Taking the windows down at exit before PyQt does.

At exit PyQt destroys every QObject that Python still owns (windows never
deleted, timers, ...) while walking sip's table of wrappers. If destroying
one runs Python code, the objects that code is handed get wrappers and join
the table. An event filter installed on the whole application does this for
every widget being destroyed, and a slot connected to ``destroyed`` does it
too. When the table fills, sip rebuilds it and frees the old one under
PyQt's walk, which then reads freed memory: now and then a segfault at exit.

The handler registered here runs before PyQt's, because it is registered
after it (PyQt5.QtCore registers its handler on import). It runs the
callbacks given to :func:`before_qt_exit` (taking the application's Python
event filters off), closes matplotlib's pyplot windows, and destroys the
top-level windows Python owns, so PyQt's walk finds them gone.
"""

import atexit
import logging
import sys

from PyQt5 import sip
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

_callbacks = []


def before_qt_exit(callback):
    """Call ``callback()`` at exit, before the windows are destroyed."""
    _callbacks.append(callback)


def _take_down():
    if QApplication.instance() is None:
        return
    for callback in _callbacks:
        try:
            callback()
        except Exception as e:
            logger.warning(f"At exit, {callback!r} failed: {e}")
    # matplotlib's own exit handler closes its windows: leave it none
    pyplot = sys.modules.get("matplotlib.pyplot")
    if pyplot is not None:
        pyplot.close("all")
    for widget in QApplication.topLevelWidgets():
        # Python-owned first: only then is sip told when Qt deletes it, so
        # that isdeleted() is reliable and parent() safe to call
        if (
            sip.ispyowned(widget)
            and not sip.isdeleted(widget)
            and widget.parent() is None
        ):
            sip.delete(widget)


atexit.register(_take_down)
