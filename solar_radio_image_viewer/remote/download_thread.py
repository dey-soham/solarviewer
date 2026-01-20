"""
Download Thread for Remote Files.

This module provides a shared QThread implementation for downloading files
from a remote server without blocking the main UI thread.
"""

from PyQt5.QtCore import QThread, pyqtSignal
from .ssh_manager import SSHConnection


class DownloadThread(QThread):
    """
    Thread for downloading files without blocking UI.

    Signals:
        progress(int, int): Emitted with (bytes_transferred, total_bytes)
        finished(str): Emitted with local_path when download completes
        error(str): Emitted with error message if download fails
    """

    progress = pyqtSignal(int, int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        connection: SSHConnection,
        remote_path: str,
        local_path: str,
        is_directory: bool = False,
    ):
        super().__init__()
        self.connection = connection
        self.remote_path = remote_path
        self.local_path = local_path
        self.is_directory = is_directory
        self._is_cancelled = False

    def cancel(self):
        """Request download cancellation."""
        self._is_cancelled = True
        # Note: True interruption of blocking SFTP calls is hard without
        # closing the channel/transport, but we can stop the loop in
        # recursive directory downloads or check flags in callbacks.

    def run(self):
        try:
            # Create a progress callback that also checks for cancellation
            def progress_callback(transferred, total):
                if self._is_cancelled:
                    raise InterruptedError("Download cancelled")
                self.progress.emit(transferred, total)

            if self.is_directory:
                result = self.connection.download_directory(
                    self.remote_path,
                    self.local_path,
                    progress_callback=progress_callback,
                )
            else:
                result = self.connection.download_file(
                    self.remote_path,
                    self.local_path,
                    progress_callback=progress_callback,
                )

            if not self._is_cancelled:
                self.finished.emit(result)

        except InterruptedError:
            # Just stop, don't emit error
            pass
        except Exception as e:
            if not self._is_cancelled:
                self.error.emit(str(e))
