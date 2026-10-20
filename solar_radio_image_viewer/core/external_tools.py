"""
Launching downloaders, Helioviewer browser and pipeline tools.
"""

from PyQt5.QtWidgets import QMessageBox

from ..styles import theme_manager


class ExternalToolsMixin:
    """Launch data downloaders, the Helioviewer browser and pipeline GUI tools.

    Mixed into :class:`SolarRadioImageViewerApp`; relies on its attributes via ``self``.

    Host attributes/methods used:

    ``statusBar``, ``tab_widget``
    """

    def launch_data_downloader_gui(self):
        """Launch the Solar Data Downloader GUI."""
        try:
            from ..solar_data_downloader import launch_gui as launch_downloader_gui

            # Try to extract datetime from current tab
            initial_datetime = self._get_current_tab_datetime()
            launch_downloader_gui(self, initial_datetime=initial_datetime)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to launch Solar Data Downloader GUI: {str(e)}\n\n"
                "Please make sure all required dependencies are installed.",
            )

    def launch_radio_data_downloader_gui(self):
        """Launch the Radio Solar Data Downloader GUI."""
        try:
            from ..radio_data_downloader import (
                launch_gui as launch_radio_downloader_gui,
            )

            # Try to extract datetime from current tab
            initial_datetime = self._get_current_tab_datetime()
            launch_radio_downloader_gui(self, initial_datetime=initial_datetime)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to launch Radio Data Downloader GUI: {str(e)}\n\n"
                "Please make sure all required dependencies are installed.",
            )

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
                if not (
                    imagename.lower().endswith(".fits")
                    or imagename.lower().endswith(".fts")
                ):
                    try:
                        from astropy.time import Time
                        from casatools import image as IA

                        ia_tool = IA()
                        ia_tool.open(imagename)
                        csys_record = ia_tool.coordsys().torecord()
                        ia_tool.close()

                        if "obsdate" in csys_record:
                            obsdate = csys_record["obsdate"]
                            m0 = obsdate.get("m0", {})
                            mjd_value = m0.get("value", None)
                            if mjd_value is not None:
                                t = Time(mjd_value, format="mjd")
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
            cli_script = os.path.join(
                os.path.dirname(__file__),
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

            self.statusBar().showMessage("Launching Radio Data Downloader CLI...")

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
python3 "{cli_script}"
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

                    self.statusBar().showMessage(f"Launching in {terminal}...")
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
                        # Process is still running after 1 second, which is good
                        self.statusBar().showMessage(f"Launched CLI in {terminal}")
                        print(f"[INFO] Successfully launched CLI in {terminal}")
                        return

                except Exception as term_error:
                    print(f"[ERROR] Error launching {terminal}: {str(term_error)}")
                    self.statusBar().showMessage(
                        f"Error launching {terminal}: {str(term_error)}"
                    )
                    raise Exception(f"Failed to launch {terminal}: {str(term_error)}")

            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(["open", "-a", "Terminal", temp_script])
            else:
                raise Exception(f"Unsupported platform: {sys.platform}")

        except Exception as e:
            error_msg = (
                f"Failed to launch Solar Data Downloader CLI: {str(e)}\n\n"
                "Please try running the CLI directly from a terminal:\n"
                f"cd {cli_dir} && source {activate_script} && python3 {cli_script}"
            )
            print(f"[ERROR] {error_msg}")  # Print to console for debugging
            QMessageBox.critical(self, "Error", error_msg)

    def open_helioviewer_browser(self):
        """Open Helioviewer browser window."""
        try:
            import requests
            from PyQt5.QtWidgets import QMessageBox

            # Fast connection check
            try:
                requests.get("https://api.helioviewer.org/v2/", timeout=3)
            except requests.RequestException:
                QMessageBox.warning(
                    self,
                    "Connection Error",
                    "Could not connect to the Helioviewer API.\nPlease check your internet connection and try again.",
                )
                return

            from ..helioviewer_browser import HelioviewerBrowser

            browser = HelioviewerBrowser(self)
            browser.show()
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox

            QMessageBox.critical(
                self, "Error", f"Failed to open Helioviewer Browser:\n{str(e)}"
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

    def _get_current_theme(self):
        """Get current theme name for passing to subprocess."""
        return theme_manager.current_theme

    def _launch_pipeline_tool(self, tool, display_name):
        """Launch a SIMPL pipeline GUI tool (see ``pipeline/launch.py``).

        These tools are launched as separate processes to avoid memory
        conflicts between python-casacore and casatasks/casatools.
        """
        import os
        import subprocess
        import sys

        try:
            env = self._get_clean_qt_env()
            launch_cwd = (
                os.getcwd()
                if os.access(os.getcwd(), os.W_OK)
                else os.path.expanduser("~")
            )
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "solar_radio_image_viewer.pipeline.launch",
                    tool,
                    self._get_current_theme(),
                ],
                start_new_session=True,
                env=env,
                cwd=launch_cwd,
            )
            self.statusBar().showMessage(f"{display_name} launched", 3000)
        except Exception as e:
            error_message = f"Error launching {display_name}: {str(e)}"
            print(f"[ERROR] {error_message}")
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "Error", error_message)
