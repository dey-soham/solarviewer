# Remote module for SolarViewer
# Provides SSH/SFTP functionality for remote file access

from .ssh_manager import SSHConnection, SSHConnectionError
from .file_cache import RemoteFileCache
from .connection_dialog import ConnectionDialog
from .remote_file_browser import RemoteFileBrowser

__all__ = [
    "SSHConnection",
    "SSHConnectionError",
    "RemoteFileCache",
    "ConnectionDialog",
    "RemoteFileBrowser",
]
