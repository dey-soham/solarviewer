"""
Download Thread for Remote Files.

This module provides a shared QThread implementation for downloading files
from a remote server without blocking the main UI thread.

Includes network stall detection via socket-level timeouts to prevent
silent hangs on slow or dropped connections.
"""

import socket
from PyQt5.QtCore import QThread, pyqtSignal
from .ssh_manager import SSHConnection


# Maximum time (seconds) to wait for any data on the socket before
# considering the transfer stalled.  This is a socket-level read timeout,
# NOT a total transfer timeout — large files on slow links will complete
# fine as long as *some* data arrives within each window.
TRANSFER_STALL_TIMEOUT = 120  # 2 minutes


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
        stall_timeout: int = TRANSFER_STALL_TIMEOUT,
    ):
        super().__init__()
        self.connection = connection
        self.remote_path = remote_path
        self.local_path = local_path
        self.is_directory = is_directory
        self._is_cancelled = False
        self._stall_timeout = stall_timeout

    def cancel(self):
        """Request download cancellation."""
        self._is_cancelled = True
        # Note: True interruption of blocking SFTP calls is hard without
        # closing the channel/transport, but we can stop the loop in
        # recursive directory downloads or check flags in callbacks.

    def _set_socket_timeout(self, timeout):
        """Set the socket timeout on the SSH transport.

        Returns the previous timeout value so it can be restored later.
        """
        try:
            transport = self.connection.transport
            if transport and transport.sock:
                old_timeout = transport.sock.gettimeout()
                transport.sock.settimeout(timeout)
                return old_timeout
        except Exception:
            pass
        return None

    def run(self):
        old_timeout = None
        try:
            # Set socket timeout to detect network stalls during transfer.
            # This makes paramiko's sftp.get() raise socket.timeout if no
            # data arrives for _stall_timeout seconds — preventing infinite
            # hangs on dropped connections.
            old_timeout = self._set_socket_timeout(self._stall_timeout)

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
            # User-initiated cancellation — just stop, don't emit error
            pass
        except socket.timeout:
            if not self._is_cancelled:
                self.error.emit(
                    f"Download stalled — no data received for "
                    f"{self._stall_timeout} seconds. "
                    f"Check your network connection and try again."
                )
        except EOFError:
            if not self._is_cancelled:
                self.error.emit(
                    "Connection lost during download (server closed connection). "
                    "Try reconnecting."
                )
        except OSError as e:
            # Covers socket.error, BrokenPipeError, ConnectionResetError, etc.
            if not self._is_cancelled:
                self.error.emit(f"Network error during download: {e}")
        except Exception as e:
            if not self._is_cancelled:
                self.error.emit(str(e))
        finally:
            # Always restore the original socket timeout so we don't break
            # the SSH connection for future operations (keep-alive, listing, etc.)
            if old_timeout is not None:
                self._set_socket_timeout(old_timeout)
