"""
A tool's end of its link to the SolarViewer window that launched it (the
other end is core/tool_link.py): one local socket, kept open, that the tool
sends messages over (e.g. "show the image at this time") and takes
instructions from: open a file, come to the front, change theme, close.

The socket's name comes from SOLARVIEWER_LINK in the environment; without it
(the tool run on its own) nothing is connected and sends fail quietly.
"""

import json
import os

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtNetwork import QLocalSocket

ENV = "SOLARVIEWER_LINK"


class ViewerLink(QObject):
    """Connected to SolarViewer when launched from it; ``message`` carries
    each instruction it sends, as a dict."""

    message = pyqtSignal(dict)

    def __init__(self, tool, parent=None):
        super().__init__(parent)
        self._socket = None
        name = os.environ.get(ENV, "")
        if not name:
            return
        socket = QLocalSocket(self)
        socket.connectToServer(name)
        if not socket.waitForConnected(1000):
            return
        self._socket = socket
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self._lost)
        self.send({"hello": tool, "pid": os.getpid()})

    @property
    def connected(self):
        return (
            self._socket is not None
            and self._socket.state() == QLocalSocket.ConnectedState
        )

    def send(self, message):
        """Send ``message`` (a dict); False if SolarViewer is not there."""
        if not self.connected:
            return False
        self._socket.write((json.dumps(message) + "\n").encode())
        return self._socket.waitForBytesWritten(1000)

    def _read(self):
        while self._socket is not None and self._socket.canReadLine():
            try:
                message = json.loads(bytes(self._socket.readLine()).decode())
            except ValueError:
                continue
            if isinstance(message, dict):
                self.message.emit(message)

    def _lost(self):
        self._socket = None
