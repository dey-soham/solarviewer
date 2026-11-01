"""
Launching downloaders, Helioviewer browser and pipeline tools.
"""

import logging
import time
import traceback

from PyQt5.QtCore import QTimer

from ..ui.activity import begin_activity
from ..ui.background import Cancelled, run_in_thread
from ..ui.busy import busy
from ..ui.errors import show_error
from ..ui.styles import theme_manager
from ..processing.fits_io import is_fits, readable_image_path
from ..ui.recent_files import remember_image
from ..processing.image_meta import casa_obs_time

logger = logging.getLogger(__name__)

MISSING_HINT = "Make sure all required dependencies are installed and try again."

# The tools that tell this window when they are up (pipeline/viewer_link.py);
# how long to wait for that, in seconds (as ToolLink.note_launch)
TOOLS_SAYING_HELLO = ("dynamic_spectrum_viewer",)
TOOL_HELLO_S = 20
# The other tools say nothing: about how long their window takes to appear
TOOL_START_S = 3


def _helioviewer_reachable():
    """Whether the Helioviewer API answers (on a thread: this waits)."""
    import requests

    try:
        requests.get("https://api.helioviewer.org/v2/", timeout=3)
    except requests.RequestException:
        return False
    return True


class ExternalToolsMixin:
    """Launch data downloaders, the Helioviewer browser and pipeline GUI tools.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``statusBar``, ``tab_widget``
    """

    def launch_data_downloader_gui(self):
        """Launch the Solar Data Downloader GUI."""
        try:
            with busy("Opening the Solar Data Downloader…", self):
                from ..solar_data_downloader import launch_gui as launch_downloader_gui

                # Try to extract datetime from current tab
                initial_datetime = self._get_current_tab_datetime()
                launch_downloader_gui(self, initial_datetime=initial_datetime)
        except Exception:
            show_error(self, "Could not open the Solar Data Downloader", MISSING_HINT, traceback.format_exc())

    def launch_radio_data_downloader_gui(self):
        """Launch the Radio Solar Data Downloader GUI."""
        try:
            with busy("Opening the Radio Data Downloader…", self):
                from ..radio_data_downloader import (
                    launch_gui as launch_radio_downloader_gui,
                )

                # Try to extract datetime from current tab
                initial_datetime = self._get_current_tab_datetime()
                launch_radio_downloader_gui(self, initial_datetime=initial_datetime)
        except Exception:
            show_error(self, "Could not open the Radio Data Downloader", MISSING_HINT, traceback.format_exc())

    def _get_current_tab_datetime(self):
        """Extract observation datetime from the current tab's image, if available."""
        from datetime import datetime

        try:
            # Get the current tab
            current_tab = self.tab_widget.currentWidget()
            if not current_tab:
                return None

            obs_datetime = None

            # Method 1: Try FITS header
            if hasattr(current_tab, "current_header") and current_tab.current_header:
                header = current_tab.current_header
                for key in ["DATE-OBS", "DATE_OBS", "DATE", "STARTOBS", "T_OBS"]:
                    if key in header:
                        date_str = str(header[key]).strip()
                        try:
                            if "T" in date_str:
                                # Clean the string: remove Z, timezone, and fractional seconds
                                clean_str = (
                                    date_str.replace("Z", "")
                                    .split("+")[0]
                                    .split(".")[0]
                                )
                                if "-" in clean_str[11:]:
                                    clean_str = clean_str[:19]
                                obs_datetime = datetime.fromisoformat(clean_str)
                                break
                            elif "-" in date_str and len(date_str) >= 10:
                                obs_datetime = datetime.strptime(
                                    date_str[:10], "%Y-%m-%d"
                                )
                                break
                        except (ValueError, TypeError):
                            continue

            # Method 2: Try CASA image metadata (if no FITS datetime found)
            if (
                not obs_datetime
                and hasattr(current_tab, "imagename")
                and current_tab.imagename
            ):
                imagename = current_tab.imagename
                # Check if it's a CASA image (directory or non-FITS file)
                if not is_fits(imagename):
                    try:
                        from ..processing.dependencies import IA

                        ia_tool = IA()
                        ia_tool.open(readable_image_path(imagename))
                        csys_record = ia_tool.coordsys().torecord()
                        ia_tool.close()

                        t = casa_obs_time(csys_record)
                        if t is not None:
                            obs_datetime = t.datetime
                    except Exception:
                        pass  # CASA not available or failed

            return obs_datetime
        except Exception:
            return None

    def launch_data_downloader_cli(self):
        """Launch the Solar Data Downloader CLI in a new terminal window."""
        try:
            import os
            import subprocess
            import sys

            # Get the path to the CLI script and its directory
            package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cli_script = os.path.join(
                package_dir,
                "solar_data_downloader",
                "solar_data_downloader_cli.py",
            )
            cli_dir = os.path.dirname(cli_script)

            # Make sure the script is executable
            os.chmod(cli_script, 0o755)

            # Get the current Python interpreter path and virtual environment
            python_path = sys.executable
            venv_path = os.path.dirname(os.path.dirname(python_path))
            activate_script = os.path.join(venv_path, "bin", "activate")

            self.statusBar().showMessage("Launching Solar Data Downloader CLI…")

            # Check if package directory is writable for the shell script
            if os.access(cli_dir, os.W_OK):
                temp_script = os.path.join(cli_dir, "run_cli.sh")
            else:
                temp_script = os.path.join(
                    os.path.expanduser("~"), f".run_solar_cli_{os.getpid()}.sh"
                )
            with open(temp_script, "w") as f:
                f.write(f"""#!/bin/bash
source "{activate_script}"
"{python_path}" "{cli_script}"
read -p "Press Enter to close..."
""")
            os.chmod(temp_script, 0o755)

            # Determine the terminal command based on the platform
            if sys.platform.startswith("linux"):
                # First, let's check which terminals are available
                available_terminals = []
                for term in ["xfce4-terminal", "gnome-terminal", "konsole", "xterm"]:
                    try:
                        result = subprocess.run(
                            ["which", term], capture_output=True, text=True
                        )
                        if result.returncode == 0:
                            available_terminals.append(term)
                    except Exception:
                        continue

                self.statusBar().showMessage(
                    f"Found terminals: {', '.join(available_terminals)}"
                )

                if not available_terminals:
                    raise Exception(
                        "No terminal emulators found. Please install gnome-terminal, konsole, xfce4-terminal, or xterm."
                    )

                # Try to launch using the first available terminal
                terminal = available_terminals[0]
                try:
                    if terminal == "gnome-terminal":
                        cmd = [terminal, "--", "bash", temp_script]
                    elif terminal == "konsole":
                        cmd = [terminal, "--separate", "--", "bash", temp_script]
                    elif terminal == "xfce4-terminal":
                        cmd = [terminal, "--command", f"bash {temp_script}"]
                    else:  # xterm and others
                        cmd = [terminal, "-e", f"bash {temp_script}"]

                    self.statusBar().showMessage(f"Launching in {terminal}…")
                    launch_cwd = (
                        os.getcwd()
                        if os.access(os.getcwd(), os.W_OK)
                        else os.path.expanduser("~")
                    )
                    process = subprocess.Popen(cmd, cwd=launch_cwd)

                    # Wait a bit to see if the process starts successfully
                    try:
                        process.wait(timeout=1)
                        if process.returncode is not None and process.returncode != 0:
                            raise Exception(f"Terminal {terminal} failed to start")
                    except subprocess.TimeoutExpired:
                        pass  # still running after 1 second, which is good
                    # (gnome-terminal hands over to its server and exits at once)
                    self.statusBar().showMessage(f"Launched CLI in {terminal}")
                    logger.info(f"Successfully launched CLI in {terminal}")
                    return

                except Exception as term_error:
                    logger.error(f"Error launching {terminal}: {str(term_error)}")
                    self.statusBar().showMessage(
                        f"Error launching {terminal}: {str(term_error)}"
                    )
                    raise Exception(f"Failed to launch {terminal}: {str(term_error)}")

            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(["open", "-a", "Terminal", temp_script])
            else:
                raise Exception(f"Unsupported platform: {sys.platform}")

        except Exception as e:
            logger.error(f"Failed to launch Solar Data Downloader CLI: {str(e)}")
            show_error(
                self,
                "Could not open the Solar Data Downloader CLI",
                "Try running it directly from a terminal:\n"
                f"cd {cli_dir} && source {activate_script} && python3 {cli_script}",
                traceback.format_exc(),
            )

    def open_helioviewer_browser(self):
        """Open Helioviewer browser window."""
        try:
            from PyQt5.QtWidgets import QMessageBox

            # Fast connection check
            if not run_in_thread(
                self, "Checking the connection to Helioviewer…", _helioviewer_reachable
            ):
                QMessageBox.warning(
                    self,
                    "Connection error",
                    "Could not connect to the Helioviewer API.\nPlease check your internet connection and try again.",
                )
                return

            with busy("Opening the Helioviewer browser…", self):
                from ..helioviewer_browser import HelioviewerBrowser

                browser = HelioviewerBrowser(self)
                browser.show()
        except Cancelled:
            self.statusBar().showMessage("Cancelled")
        except Exception:
            show_error(
                self,
                "Could not open the Helioviewer browser",
                "The window could not be opened. Try again; the details say what went wrong.",
                traceback.format_exc(),
            )

    def _get_clean_qt_env(self):
        """Get environment with Qt plugin paths cleared to avoid opencv conflicts."""
        import os

        env = os.environ.copy()
        # Remove Qt plugin paths that opencv-python sets, which conflict with PyQt5
        for key in ["QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"]:
            if key in env:
                del env[key]
        return env

    def _tool_link_server(self):
        """The link the tools talk to this window over, made on first use;
        None if it can't be (a warning says why, once)."""
        if not hasattr(self, "_tool_link"):
            from .tool_link import ToolLink

            try:
                self._tool_link = ToolLink(self)
            except OSError as e:
                logger.warning(f"Tools can't message this window: {e}")
                self._tool_link = None
        return self._tool_link

    def _get_current_theme(self):
        """Get current theme name for passing to subprocess."""
        return theme_manager.current_theme

    def open_spectrum(self, path):
        """Show the dynamic spectrum ``path`` in the Dynamic Spectrum Viewer:
        the one window of it, brought to the front, started if there is none.
        The spectrum goes first in Open Recent. True if it was shown."""
        import os

        link = self._tool_link_server()
        started = link is not None and link.deliver(
            "dynamic_spectrum_viewer", {"open": path, "raise": True}
        )
        if not started:
            started = self._launch_pipeline_tool(
                "dynamic_spectrum_viewer", "Dynamic Spectrum Viewer", path
            )
            if started and link is not None:
                link.note_launch("dynamic_spectrum_viewer", path)
        if started:
            remember_image(path)
            self.statusBar().showMessage(
                f"{os.path.basename(path)} is a dynamic spectrum: "
                "opening it in the Dynamic Spectrum Viewer",
                8000,
            )
        return started

    def _launch_pipeline_tool(self, tool, display_name, path=None):
        """Launch a SIMPL pipeline GUI tool (see ``pipeline/launch.py``),
        opening ``path`` in it if given. True if it was started.

        These tools are launched as separate processes to avoid memory
        conflicts between python-casacore and casatasks/casatools.
        """
        import os
        import subprocess
        import sys

        starting = self.__dict__.setdefault("_tools_starting", set())
        if path is None and tool in starting:
            self.statusBar().showMessage(f"{display_name} is still starting", 3000)
            return True

        try:
            env = self._get_clean_qt_env()
            connection = getattr(self, "remote_connection", None)
            if connection is not None and connection.is_connected():
                # The same server, for the tool's "Open from Server" (keys or
                # agent: the password isn't passed on)
                import json

                params = {
                    k: v
                    for k, v in connection.connect_params.items()
                    if k in ("host", "port", "username", "key_path") and v is not None
                }
                env["SOLARVIEWER_SSH"] = json.dumps(params)
            else:
                env.pop("SOLARVIEWER_SSH", None)
            # Messages to and from this window (e.g. "show the image at this time")
            from .tool_link import ENV

            link = self._tool_link_server()
            if link is not None:
                env[ENV] = link.name
            launch_cwd = (
                os.getcwd()
                if os.access(os.getcwd(), os.W_OK)
                else os.path.expanduser("~")
            )
            command = [
                sys.executable,
                "-m",
                "solar_radio_image_viewer.pipeline.launch",
                tool,
                self._get_current_theme(),
            ]
            if path:
                command.append(path)
            process = subprocess.Popen(
                command, start_new_session=True, env=env, cwd=launch_cwd
            )
            self._show_tool_starting(tool, display_name, process)
            return True
        except Exception:
            logger.exception(f"Could not start {display_name}")
            show_error(
                self,
                f"Could not start {display_name}",
                "The program did not start. Check the installation and try again.",
                traceback.format_exc(),
            )
            return False

    def _show_tool_starting(self, tool, display_name, process):
        """Say ``tool`` is starting until it says it is up, or for the time a
        tool takes if it cannot, or until its process ends: its window comes
        seconds after the process does."""
        task = begin_activity(f"Starting {display_name}…", self, blocking=False)
        link = getattr(self, "_tool_link", None)
        says_hello = link is not None and tool in TOOLS_SAYING_HELLO
        until = time.monotonic() + (TOOL_HELLO_S if says_hello else TOOL_START_S)
        self._tools_starting.add(tool)
        timer = QTimer(self)

        def check():
            ended = process.poll() is not None
            up = says_hello and link.has(tool)
            if not (ended or up or time.monotonic() >= until):
                return
            timer.stop()
            timer.deleteLater()
            task.finish()
            self._tools_starting.discard(tool)
            if ended and process.returncode != 0:
                message = f"{display_name} stopped as it started (exit code {process.returncode})"
            else:
                message = f"{display_name} launched"
            self.statusBar().showMessage(message, 3000)

        timer.timeout.connect(check)
        timer.start(100)
