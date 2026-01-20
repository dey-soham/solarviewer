"""
SSH Connection Manager for SolarViewer Remote Mode.

Provides SSH/SFTP connectivity using paramiko, with support for:
- Key-based and password authentication
- Reading ~/.ssh/config for host aliases
- Connection pooling and automatic reconnection
"""

import os
import stat
from pathlib import Path
from typing import Optional, List, Tuple, Callable
from dataclasses import dataclass

try:
    import paramiko
    from paramiko import SSHClient, SFTPClient, RSAKey, Ed25519Key, ECDSAKey
    from paramiko.config import SSHConfig

    HAS_PARAMIKO = True

    # Suppress verbose paramiko logging (SFTP open/close messages)
    import logging

    logging.getLogger("paramiko.transport.sftp").setLevel(logging.WARNING)
    logging.getLogger("paramiko.transport").setLevel(logging.WARNING)
except ImportError:
    HAS_PARAMIKO = False
    SSHClient = None
    SFTPClient = None


class SSHConnectionError(Exception):
    """Exception raised for SSH connection errors."""

    pass


@dataclass
class ConnectionProfile:
    """Stores SSH connection settings."""

    name: str
    host: str
    port: int = 22
    username: str = ""
    auth_type: str = "key"  # "key" or "password"
    key_path: str = ""
    password: str = ""  # Note: stored in memory only, not persisted


@dataclass
class RemoteFileInfo:
    """Information about a remote file or directory."""

    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float

    @property
    def is_fits(self) -> bool:
        """Check if this is a FITS file."""
        lower = self.name.lower()
        return lower.endswith((".fits", ".fts", ".fit"))

    @property
    def is_casa_image(self) -> bool:
        """Check if this might be a CASA image directory."""
        # CASA images are directories containing specific files
        return self.is_dir


class SSHConnection:
    """
    Manages SSH/SFTP connection to a remote server.

    Usage:
        conn = SSHConnection()
        conn.connect("user@hostname", key_path="~/.ssh/id_rsa")
        files = conn.list_directory("/data/fits/")
        local_path = conn.download_file("/data/fits/image.fits", "/tmp/cache/")
        conn.disconnect()
    """

    def __init__(self):
        if not HAS_PARAMIKO:
            raise SSHConnectionError(
                "paramiko is not installed. Install it with: pip install paramiko"
            )

        self._client: Optional[SSHClient] = None
        self._sftp: Optional[SFTPClient] = None
        self._host: str = ""
        self._port: int = 22
        self._username: str = ""
        self._connected: bool = False
        self._ssh_config: Optional[SSHConfig] = None
        self._last_home: str = ""  # Cached home directory for non-blocking access

        # Store connection parameters for auto-reconnect
        self._connect_params: dict = {}

        # Load SSH config if available
        self._load_ssh_config()

    def _load_ssh_config(self):
        """Load ~/.ssh/config if it exists."""
        config_path = Path.home() / ".ssh" / "config"
        if config_path.exists():
            self._ssh_config = SSHConfig()
            with open(config_path) as f:
                self._ssh_config.parse(f)

    def _resolve_host(self, host: str) -> dict:
        """
        Resolve host using SSH config.
        Returns dict with hostname, port, user, identityfile.
        """
        result = {
            "hostname": host,
            "port": 22,
            "user": None,
            "identityfile": None,
        }

        if self._ssh_config:
            config = self._ssh_config.lookup(host)
            if "hostname" in config:
                result["hostname"] = config["hostname"]
            if "port" in config:
                result["port"] = int(config["port"])
            if "user" in config:
                result["user"] = config["user"]
            if "identityfile" in config:
                # Take the first identity file
                result["identityfile"] = os.path.expanduser(config["identityfile"][0])

        return result

    def connect(
        self,
        host: str,
        port: int = None,
        username: str = None,
        password: str = None,
        key_path: str = None,
        timeout: float = 30.0,
    ) -> None:
        """
        Connect to a remote SSH server.

        Args:
            host: Hostname or SSH config alias (can be user@host format)
            port: SSH port (default: 22 or from SSH config)
            username: Username (default: from SSH config or current user)
            password: Password for authentication (optional)
            key_path: Path to private key file (optional, uses SSH agent/default keys if not specified)
            timeout: Connection timeout in seconds

        Raises:
            SSHConnectionError: If connection fails
        """
        # Parse user@host format
        if "@" in host:
            parsed_user, host = host.rsplit("@", 1)
            if username is None:
                username = parsed_user

        # Resolve host through SSH config
        resolved = self._resolve_host(host)
        actual_host = resolved["hostname"]
        actual_port = port if port is not None else resolved["port"]
        actual_user = username if username is not None else resolved["user"]
        actual_key = key_path if key_path is not None else resolved["identityfile"]

        # Default username to current user
        if actual_user is None:
            actual_user = os.getlogin()

        # Expand key path
        if actual_key:
            actual_key = os.path.expanduser(actual_key)

        try:
            self._client = SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Build connection kwargs
            connect_kwargs = {
                "hostname": actual_host,
                "port": actual_port,
                "username": actual_user,
                "timeout": timeout,
                "look_for_keys": True,
            }

            if password:
                # When password is provided, disable agent to avoid hardware token issues
                connect_kwargs["password"] = password
                connect_kwargs["allow_agent"] = False
            elif actual_key and os.path.exists(actual_key):
                # When explicit key is provided, disable agent to avoid hardware token issues
                connect_kwargs["key_filename"] = actual_key
                connect_kwargs["allow_agent"] = False
            else:
                # Only use agent when no explicit credentials provided
                connect_kwargs["allow_agent"] = True

            self._client.connect(**connect_kwargs)

            # Configure keep-alive to prevent connection timeout
            transport = self._client.get_transport()
            if transport:
                transport.set_keepalive(30)  # Send keep-alive every 30 seconds

            # Open SFTP channel
            self._sftp = self._client.open_sftp()

            self._host = actual_host
            self._port = actual_port
            self._username = actual_user
            self._connected = True

            # Store connection parameters for potential reconnect
            self._connect_params = {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "key_path": key_path,
                "timeout": timeout,
            }

        except paramiko.AuthenticationException as e:
            self._cleanup()
            raise SSHConnectionError(f"Authentication failed: {e}")
        except paramiko.SSHException as e:
            self._cleanup()
            raise SSHConnectionError(f"SSH error: {e}")
        except Exception as e:
            self._cleanup()
            raise SSHConnectionError(f"Connection failed: {e}")

    def _cleanup(self):
        """Clean up connection resources."""
        if self._sftp:
            try:
                self._sftp.close()
            except:
                pass
            self._sftp = None

        if self._client:
            try:
                self._client.close()
            except:
                pass
            self._client = None

        self._connected = False

    def disconnect(self, clear_credentials: bool = True) -> None:
        """Disconnect from the remote server.

        Args:
            clear_credentials: If True, clear stored connection parameters
        """
        self._cleanup()
        self._host = ""
        self._port = 22
        self._username = ""
        if clear_credentials:
            self._connect_params = {}

    def reconnect(self) -> bool:
        """
        Attempt to reconnect using stored connection parameters.

        Returns:
            True if reconnection succeeded, False otherwise
        """
        if not self._connect_params:
            return False

        try:
            # Clean up existing connection
            self._cleanup()

            # Reconnect with stored params
            self.connect(**self._connect_params)
            return True
        except SSHConnectionError as e:
            print(f"[SSH] Reconnection failed: {e}")
            return False
        except Exception as e:
            print(f"[SSH] Reconnection error: {e}")
            return False

    def is_connected(self) -> bool:
        """Check if currently connected (passive check only)."""
        if not self._connected or not self._client:
            return False

        # Check if connection is still alive
        try:
            transport = self._client.get_transport()
            if transport is None or not transport.is_active():
                self._connected = False
                return False
            return True
        except:
            self._connected = False
            return False

    def ping_connection(self, timeout: float = 5.0) -> bool:
        """
        Actively test connection by sending traffic.

        Args:
            timeout: Timeout in seconds for the test

        Returns:
            True if connection is responsive, False otherwise
        """
        if not self._connected or not self._client:
            return False

        try:
            transport = self._client.get_transport()
            if transport is None or not transport.is_active():
                self._connected = False
                return False

            # Set a timeout on the socket for this check
            sock = transport.sock
            old_timeout = sock.gettimeout()
            sock.settimeout(timeout)

            try:
                # Use stat('.') which requires an actual server round-trip
                # Unlike getcwd() which might use cached data
                if self._sftp:
                    self._sftp.stat(".")
                else:
                    # Just send keep-alive if no SFTP
                    transport.send_ignore()
            finally:
                sock.settimeout(old_timeout)

            return True
        except Exception as e:
            print(f"[SSH] Ping failed: {e}")
            self._connected = False
            return False

    def ensure_connected(self) -> bool:
        """
        Ensure connection is active, auto-reconnecting if needed.

        Returns:
            True if connected (possibly after reconnect), False if failed
        """
        if self.is_connected():
            return True

        # Try to reconnect
        if self._connect_params:
            print("[SSH] Connection lost, attempting auto-reconnect...")
            if self.reconnect():
                print("[SSH] Auto-reconnect successful")
                return True
            else:
                print("[SSH] Auto-reconnect failed")

        return False

    def check_health(self) -> dict:
        """
        Perform a health check on the connection.

        Returns:
            dict with status info: {
                'connected': bool,
                'transport_active': bool,
                'sftp_active': bool,
                'can_reconnect': bool
            }
        """
        result = {
            "connected": False,
            "transport_active": False,
            "sftp_active": False,
            "can_reconnect": bool(self._connect_params),
        }

        if not self._client:
            return result

        try:
            transport = self._client.get_transport()
            if transport and transport.is_active():
                result["transport_active"] = True
                result["connected"] = True
        except:
            pass

        try:
            if self._sftp:
                # Try a simple operation to test SFTP
                self._sftp.getcwd()
                result["sftp_active"] = True
        except:
            pass

        return result

    def refresh_sftp(self) -> bool:
        """
        Refresh the SFTP channel.
        Useful if the previous SFTP session was interrupted.

        Returns:
            True if refresh succeeded, False otherwise
        """
        if not self._connected or not self._client:
            return False

        try:
            # Close existing SFTP if any
            if self._sftp:
                try:
                    self._sftp.close()
                except:
                    pass

            # Open new SFTP channel
            self._sftp = self._client.open_sftp()
            return True
        except Exception as e:
            print(f"[SSH] Failed to refresh SFTP: {e}")
            return False

    @property
    def connection_info(self) -> str:
        """Get a string describing the current connection."""
        if self.is_connected():
            return f"{self._username}@{self._host}:{self._port}"
        return "Not connected"

    def list_directory(
        self,
        path: str,
        show_hidden: bool = False,
        fits_only: bool = False,
    ) -> List[RemoteFileInfo]:
        """
        List contents of a remote directory.

        Args:
            path: Remote directory path
            show_hidden: Include hidden files (starting with .)
            fits_only: Only show FITS files and directories

        Returns:
            List of RemoteFileInfo objects
        """
        if not self.is_connected():
            raise SSHConnectionError("Not connected to remote server")

        try:
            entries = []
            for attr in self._sftp.listdir_attr(path):
                name = attr.filename

                # Skip hidden files if not requested
                if not show_hidden and name.startswith("."):
                    continue

                is_dir = stat.S_ISDIR(attr.st_mode)
                full_path = os.path.join(path, name)

                info = RemoteFileInfo(
                    name=name,
                    path=full_path,
                    is_dir=is_dir,
                    size=attr.st_size,
                    mtime=attr.st_mtime,
                )

                # Filter for FITS files if requested
                if fits_only and not is_dir and not info.is_fits:
                    continue

                entries.append(info)

            # Sort: directories first, then alphabetically
            entries.sort(key=lambda x: (not x.is_dir, x.name.lower()))
            return entries

        except IOError as e:
            raise SSHConnectionError(f"Failed to list directory {path}: {e}")

    def get_file_info(self, path: str) -> RemoteFileInfo:
        """Get information about a remote file or directory."""
        if not self.is_connected():
            raise SSHConnectionError("Not connected to remote server")

        try:
            attr = self._sftp.stat(path)
            return RemoteFileInfo(
                name=os.path.basename(path),
                path=path,
                is_dir=stat.S_ISDIR(attr.st_mode),
                size=attr.st_size,
                mtime=attr.st_mtime,
            )
        except IOError as e:
            raise SSHConnectionError(f"Failed to get file info for {path}: {e}")

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        """
        Download a file from the remote server.

        Args:
            remote_path: Path to remote file
            local_path: Local directory or file path to save to
            progress_callback: Optional callback(bytes_transferred, total_bytes)

        Returns:
            Local path where file was saved
        """
        if not self.is_connected():
            raise SSHConnectionError("Not connected to remote server")

        # If local_path is a directory, append the filename
        if os.path.isdir(local_path):
            local_path = os.path.join(local_path, os.path.basename(remote_path))

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        try:
            if progress_callback:
                self._sftp.get(remote_path, local_path, callback=progress_callback)
            else:
                self._sftp.get(remote_path, local_path)
            return local_path
        except Exception as e:
            # If download fails (especially interruption), the SFTP channel might be in a bad state.
            # Close it to force a fresh channel on next use.
            if self._sftp:
                try:
                    self._sftp.close()
                except:
                    pass
                self._sftp = None

                # Check if we need to reconnect the SSH client itself?
                # Usually closing SFTP is enough, but if transport is garbage, might need full reconnect.
                # For now, let's try resetting SFTP only.
                # If we are effectively disconnected, next ensure_connection/is_connected check should handle it.
                if self._client:
                    try:
                        self._sftp = self._client.open_sftp()
                    except:
                        # If re-opening fails, just leave it closed. Next status check will catch it.
                        self._sftp = None

            raise SSHConnectionError(f"Failed to download {remote_path}: {e}")

    def download_directory(
        self,
        remote_path: str,
        local_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        """
        Recursively download a directory (useful for CASA images).

        Args:
            remote_path: Path to remote directory
            local_path: Local directory to save to
            progress_callback: Optional callback(bytes_transferred, total_bytes)

        Returns:
            Local path where directory was saved
        """
        if not self.is_connected():
            raise SSHConnectionError("Not connected to remote server")

        try:
            # Create local directory with same name
            local_dir = os.path.join(local_path, os.path.basename(remote_path))
            os.makedirs(local_dir, exist_ok=True)

            # Get all files recursively with sizes
            files_to_download = self._get_files_recursive_with_sizes(remote_path)

            # Calculate total size
            total_bytes = sum(size for _, _, size in files_to_download)
            bytes_transferred = 0

            for remote_file, rel_path, file_size in files_to_download:
                local_file = os.path.join(local_dir, rel_path)
                os.makedirs(os.path.dirname(local_file), exist_ok=True)

                # Create a per-file progress callback that updates cumulative progress
                if progress_callback:

                    def make_file_callback(current_total):
                        def file_progress(transferred, _):
                            progress_callback(current_total + transferred, total_bytes)

                        return file_progress

                    file_callback = make_file_callback(bytes_transferred)
                    self._sftp.get(remote_file, local_file, callback=file_callback)
                else:
                    self._sftp.get(remote_file, local_file)

                bytes_transferred += file_size

                # Final update for this file
                if progress_callback:
                    progress_callback(bytes_transferred, total_bytes)

            return local_dir

        except Exception as e:
            # If download fails, reset SFTP channel to prevent "Garbage packet" errors on next use
            if self._sftp:
                try:
                    self._sftp.close()
                except:
                    pass
                self._sftp = None

                if self._client:
                    try:
                        self._sftp = self._client.open_sftp()
                    except:
                        self._sftp = None

            raise SSHConnectionError(f"Failed to download directory {remote_path}: {e}")

    def _get_files_recursive_with_sizes(
        self, remote_path: str, base_path: str = None
    ) -> List[Tuple[str, str, int]]:
        """Get all files in a directory recursively with their sizes."""
        if base_path is None:
            base_path = remote_path

        files = []
        for attr in self._sftp.listdir_attr(remote_path):
            full_path = os.path.join(remote_path, attr.filename)
            rel_path = os.path.relpath(full_path, base_path)

            if stat.S_ISDIR(attr.st_mode):
                files.extend(self._get_files_recursive_with_sizes(full_path, base_path))
            else:
                files.append((full_path, rel_path, attr.st_size))

        return files

    def _get_files_recursive(
        self, remote_path: str, base_path: str = None
    ) -> List[Tuple[str, str]]:
        """Get all files in a directory recursively."""
        if base_path is None:
            base_path = remote_path

        files = []
        for attr in self._sftp.listdir_attr(remote_path):
            full_path = os.path.join(remote_path, attr.filename)
            rel_path = os.path.relpath(full_path, base_path)

            if stat.S_ISDIR(attr.st_mode):
                files.extend(self._get_files_recursive(full_path, base_path))
            else:
                files.append((full_path, rel_path))

        return files

    def path_exists(self, path: str) -> bool:
        """Check if a remote path exists."""
        if not self.is_connected():
            return False

        try:
            self._sftp.stat(path)
            return True
        except IOError:
            return False

    def get_home_directory(self) -> str:
        """Get the home directory on the remote server."""
        if not self.is_connected():
            raise SSHConnectionError("Not connected to remote server")

        try:
            # Use SFTP normalize to get absolute path of ~
            home = self._sftp.normalize(".")
            # Cache for non-blocking access later
            self._last_home = home
            return home
        except:
            return "/"

    def get_directory_count(self, path: str) -> int:
        """
        Get the number of items in a directory (fast - just counts names).

        Args:
            path: Remote directory path

        Returns:
            Number of items in directory, or -1 if error
        """
        if not self.is_connected():
            return -1

        try:
            # listdir is faster than listdir_attr as it doesn't fetch attributes
            return len(self._sftp.listdir(path))
        except:
            return -1
