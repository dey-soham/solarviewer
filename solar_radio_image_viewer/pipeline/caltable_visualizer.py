import sys
import numpy as np
from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QTabWidget,
    QComboBox,
    QGridLayout,
    QMessageBox,
    QShortcut,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# NOTE: casacore is loaded via subprocess to completely isolate it from Qt
from pathlib import Path
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
import io
import subprocess
import os
from matplotlib import rcParams

from ..ui.background import Cancelled, run_in_background, run_in_thread
from ..ui.busy import busy
from ..ui.styles import set_hand_cursor, theme_manager


_READ_CALTABLE_SCRIPT = """
import io, sys
import numpy as np
from casacore.tables import table

path, with_spectral_window = sys.argv[1], sys.argv[2] == "1"
arrays = {}
tb = table(path, readonly=True)
arrays["solutions"] = tb.getcol("CPARAM")
arrays["flag"] = tb.getcol("FLAG")
tb.close()
if with_spectral_window:
    tb = table(path + "/SPECTRAL_WINDOW", readonly=True)
    arrays["chan_freq"] = tb.getcol("CHAN_FREQ")
    tb.close()
buffer = io.BytesIO()
np.savez(buffer, **arrays)
sys.stdout.buffer.write(buffer.getvalue())
"""


from ..ui.geometry import fit_to_screen

READ_TIMEOUT_S = 300
SNR_WORKER_CHANNELS = 2000  # below this the SNR is under a second or so: not worth a worker


def read_caltable_safe(table_path, read_spectral_window=False, timeout=READ_TIMEOUT_S, on_start=None):
    """
    Read a caltable using a completely separate subprocess to avoid casacore/Qt conflicts.

    The path goes to the subprocess as an argument and the arrays come back
    as NumPy .npz bytes, so no file name can break the script and nothing
    is unpickled. ``on_start(process)`` is given the process, so that the
    read can be stopped by killing it.
    """
    table_path = str(Path(table_path).absolute())

    # Run in completely separate process
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _READ_CALTABLE_SCRIPT,
            table_path,
            "1" if read_spectral_window else "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.getcwd() if os.access(os.getcwd(), os.W_OK) else os.path.expanduser("~"),
    )
    if on_start is not None:
        on_start(process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError(f"Reading the table took over {timeout} s; is the disk reachable?")
    if process.returncode != 0:
        lines = stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"The reader ended with code {process.returncode}")

    with np.load(io.BytesIO(stdout)) as arrays:
        return {key: arrays[key] for key in arrays.files}


def split_solutions(solutions, flag):
    """The X and Y solutions of a table, the flagged ones blank."""
    if solutions.ndim != 3 or solutions.shape[2] < 2:
        raise ValueError(f"X and Y solutions are needed, but CPARAM has shape {solutions.shape}")
    solutions[flag] = np.nan
    return solutions[:, :, 0], solutions[:, :, 1]


def read_crossphase(path):
    """What the Crossphase tab shows of the file ``path``, with its fit."""
    loaded_caltable = np.load(path, allow_pickle=True)
    cross_freq = np.array(loaded_caltable[0, :] / 1e6, dtype=float)
    crossphase = np.array(loaded_caltable[1, :], dtype=float)
    cross_flags = loaded_caltable[2, :]

    median_crossphase = np.median(crossphase)
    absolute_deviation = np.abs(crossphase - median_crossphase)
    mad = np.median(absolute_deviation)

    threshold = 10 * mad
    mask = absolute_deviation <= threshold

    cross_freq_filtered = cross_freq[mask]
    crossphase_filtered = crossphase[mask]

    cross_fit_coeffs = np.polyfit(cross_freq_filtered, crossphase_filtered, 2)
    cross_fit_func = np.poly1d(cross_fit_coeffs)
    crossphase_fit = cross_fit_func(cross_freq_filtered)

    residuals = crossphase_filtered - crossphase_fit
    sse = np.sum(residuals**2)
    sst = np.sum((crossphase_filtered - np.mean(crossphase)) ** 2)

    return {
        "cross_freq": cross_freq,
        "crossphase": crossphase,
        "cross_flags": cross_flags,
        "cross_freq_filtered": cross_freq_filtered,
        "crossphase_filtered": crossphase_filtered,
        "cross_fit_coeffs": cross_fit_coeffs,
        "cross_fit_func": cross_fit_func,
        "crossphase_fit": crossphase_fit,
        "cross_r_squared": 1 - (sse / sst),
        "cross_std_residuals": np.std(residuals),
    }


def snr_estimate(xx_sols, yy_sols):
    """SNR per antenna and channel: amplitude / std(amplitude) over a sliding
    window of channels. Run in a worker: a Python loop over every channel."""
    amp = (np.abs(xx_sols) + np.abs(yy_sols)) / 2

    # Simple SNR estimate: mean / std across small windows
    window_size = max(1, amp.shape[1] // 20)
    snr = np.zeros_like(amp)

    for i in range(amp.shape[1]):
        start = max(0, i - window_size)
        end = min(amp.shape[1], i + window_size)
        local_mean = np.nanmean(amp[:, start:end], axis=1)
        local_std = np.nanstd(amp[:, start:end], axis=1)
        snr[:, i] = local_mean / (local_std + 1e-10)
    return snr


class VisualizationApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bandpass and Crossphase Visualizer")
        fit_to_screen(self, 860, 810)

        # Variables for bandpass
        self.bandpass_solutions = None
        self.bandpass_freqs = None
        self.crossphase_freq = None
        self.crossphase_data = None
        self.num_antennas = 0
        self.current_page = 0
        self.plot_rows = 3  # Default rows for plots
        self.plot_cols = 3  # Default columns for plots
        self.antennas_per_page = self.plot_rows * self.plot_cols
        self.bandpass_directory = None
        self.crossphase_file = None
        self.plot_mode = "Amplitude"  # Default mode for bandpass
        self.xx_sols = None
        self.yy_sols = None
        self.shown_view = "Per-Antenna"  # the view on the canvas

        # Variables for crossphase
        self.crossphase_file = None
        self.crossphase_freq = None
        self.crossphase = None
        self.cross_freq_filtered = None
        self.crossphase_filtered = None
        self.cross_fit_func = None
        self.cross_r_squared = None
        self.cross_std_residuals = None

        # Variables for selfcal
        self.selfcal_directory = None
        # self.plot_mode is reused for selfcal as well

        # Create GUI elements
        self.create_widgets()
        self.setup_shortcuts()
        set_hand_cursor(self)

    def setup_shortcuts(self):
        """Setup keyboard shortcuts for navigation and actions."""
        # Navigation shortcuts (similar to solarviewer)
        QShortcut(QKeySequence("]"), self, self.next_bandpass_page)  # Next page
        QShortcut(QKeySequence("["), self, self.prev_bandpass_page)  # Previous page
        QShortcut(
            QKeySequence("}"), self, self.last_bandpass_page
        )  # Last page (Shift+])
        QShortcut(
            QKeySequence("{"), self, self.first_bandpass_page
        )  # First page (Shift+[)

        # Mode toggle
        QShortcut(
            QKeySequence("T"), self, self.toggle_current_mode
        )  # Toggle amplitude/phase

        # File operations
        QShortcut(
            QKeySequence("Ctrl+O"), self, self.open_current_tab
        )  # Open directory/file
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)  # Quit

        # Tab switching
        QShortcut(
            QKeySequence("1"), self, lambda: self.tab_control.setCurrentIndex(0)
        )  # Bandpass tab
        QShortcut(
            QKeySequence("2"), self, lambda: self.tab_control.setCurrentIndex(1)
        )  # Crossphase tab
        QShortcut(
            QKeySequence("3"), self, lambda: self.tab_control.setCurrentIndex(2)
        )  # Selfcal tab

    def toggle_current_mode(self):
        """Toggle mode based on current tab."""
        current_tab = self.tab_control.currentIndex()
        if current_tab == 0:  # Bandpass
            self.toggle_bandpass_mode()
        elif current_tab == 2:  # Selfcal
            self.toggle_selfcal_mode()

    def open_current_tab(self):
        """Open directory/file based on current tab."""
        current_tab = self.tab_control.currentIndex()
        if current_tab == 0:  # Bandpass
            self.select_bandpass_directory()
        elif current_tab == 1:  # Crossphase
            self.select_crossphase_file()
        elif current_tab == 2:  # Selfcal
            self.select_selfcal_directory()

    def show_shortcuts_dialog(self):
        """Show dialog with keyboard shortcuts and usage documentation."""
        from PyQt5.QtWidgets import QDialog, QTextEdit

        dialog = QDialog(self)
        dialog.setWindowTitle("Help - Caltable Visualizer")
        dialog.resize(500, 500)

        layout = QVBoxLayout(dialog)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(
            """
        <h2>Caltable Visualizer</h2>
        <p>A tool for visualizing LOFAR calibration tables.</p>
        
        <h3>Features</h3>
        <ul>
            <li><b>Bandpass:</b> View bandpass calibration solutions (amplitude/phase vs frequency)</li>
            <li><b>Crossphase:</b> View crossphase calibration with polynomial fit</li>
            <li><b>Selfcal:</b> View self-calibration solutions (amplitude/phase vs antenna)</li>
        </ul>
        
        <hr>
        <h2>Keyboard Shortcuts</h2>
        
        <h3>Navigation</h3>
        <table>
            <tr><td width="100"><b>]</b></td><td>Next page</td></tr>
            <tr><td><b>[</b></td><td>Previous page</td></tr>
            <tr><td><b>}</b> (Shift+])</td><td>Last page</td></tr>
            <tr><td><b>{</b> (Shift+[)</td><td>First page</td></tr>
        </table>
        
        <h3>Mode & File</h3>
        <table>
            <tr><td width="100"><b>T</b></td><td>Toggle Amplitude/Phase mode</td></tr>
            <tr><td><b>Ctrl+O</b></td><td>Open directory/file (current tab)</td></tr>
            <tr><td><b>Ctrl+Q</b></td><td>Quit application</td></tr>
        </table>
        
        <h3>Tab Switching</h3>
        <table>
            <tr><td width="100"><b>1</b></td><td>Bandpass tab</td></tr>
            <tr><td><b>2</b></td><td>Crossphase tab</td></tr>
            <tr><td><b>3</b></td><td>Selfcal tab</td></tr>
        </table>
        
        <h3>Help</h3>
        <table>
            <tr><td width="100"><b>F1</b></td><td>Show this dialog</td></tr>
        </table>
        
        <hr>
        <p><i>Part of Solar Radio Image Viewer LOFAR Tools</i></p>
        """
        )
        layout.addWidget(text)

        dialog.exec_()

    def create_widgets(self):
        # Central widget and layout
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Tabs
        self.tab_control = QTabWidget(self)
        main_layout.addWidget(self.tab_control)

        # self.help_button = QPushButton("?")
        # self.help_button.setToolTip("Help & Shortcuts (F1)")
        # self.help_button.setFixedSize(10, 10)
        # self.help_button.clicked.connect(self.show_shortcuts_dialog)
        # self.tab_control.setCornerWidget(self.help_button, Qt.TopRightCorner)

        # Bandpass tab
        self.bandpass_tab = QWidget()
        self.tab_control.addTab(self.bandpass_tab, "Bandpass")
        self.create_bandpass_widgets()

        # Crossphase tab
        self.crossphase_tab = QWidget()
        self.tab_control.addTab(self.crossphase_tab, "Crossphase")
        self.create_crossphase_widgets()

        # Selfcal tab
        self.selfcal_tab = QWidget()
        self.tab_control.addTab(self.selfcal_tab, "Selfcal")
        self.create_selfcal_widgets()

    def create_bandpass_widgets(self):
        # Instead of icons, use text labels
        layout = QVBoxLayout(self.bandpass_tab)

        # Bandpass directory selection
        dir_frame = QHBoxLayout()
        self.dir_label = QLabel("No caltable selected")
        self.dir_label.setAlignment(Qt.AlignCenter)
        dir_frame.addWidget(self.dir_label)

        dir_frame.addStretch()

        select_button = QPushButton("Load")
        # select_button.setFixedSize(100, 30)
        select_button.clicked.connect(self.select_bandpass_directory)
        dir_frame.addWidget(select_button)
        layout.addLayout(dir_frame)

        # Bandpass canvas
        self.bandpass_canvas_frame = QWidget()
        layout.addWidget(self.bandpass_canvas_frame)
        self.create_bandpass_canvas()

        # Add navigation toolbar
        self.toolbar_bandpass = NavigationToolbar(self.bandpass_canvas, self)
        layout.addWidget(self.toolbar_bandpass)

        # Bandpass navigation and plot mode
        control_frame = QHBoxLayout()
        self.plot_button = QPushButton("Phase")
        self.plot_button.clicked.connect(self.toggle_bandpass_mode)
        control_frame.addWidget(self.plot_button)

        # self.first_button = QPushButton(QIcon(str(icon_path) + "/first.svg"), "")  # Replace "icons/first.png" with your icon path
        self.first_button = QPushButton("|◄")
        self.first_button.setToolTip("First page")
        self.first_button.clicked.connect(self.first_bandpass_page)
        self.first_button.setEnabled(False)
        control_frame.addWidget(self.first_button)

        # self.prev_button = QPushButton(QIcon(str(icon_path) + "/prev.svg"), "")  # Replace "icons/prev.png" with your icon path
        self.prev_button = QPushButton("◄")
        self.prev_button.setToolTip("Previous page")
        self.prev_button.clicked.connect(self.prev_bandpass_page)
        self.prev_button.setEnabled(False)
        control_frame.addWidget(self.prev_button)

        # self.next_button = QPushButton(QIcon(str(icon_path) + "/next.svg"), "")  # Replace "icons/next.png" with your icon path
        self.next_button = QPushButton("►")
        self.next_button.setToolTip("Next page")
        self.next_button.clicked.connect(self.next_bandpass_page)
        self.next_button.setEnabled(False)
        control_frame.addWidget(self.next_button)

        # self.last_button = QPushButton(QIcon(str(icon_path) + "/last.svg"), "")  # Replace "icons/last.png" with your icon path
        self.last_button = QPushButton("►|")
        self.last_button.setToolTip("Last page")
        self.last_button.clicked.connect(self.last_bandpass_page)
        self.last_button.setEnabled(False)
        control_frame.addWidget(self.last_button)

        control_frame.addStretch()

        # Plot settings
        control_frame.addWidget(QLabel("Plots per page:"))
        self.plot_size_menu = QComboBox()
        self.plot_size_menu.addItems(["2x2", "3x3", "4x4", "5x5"])
        self.plot_size_menu.setCurrentText("3x3")
        self.plot_size_menu.currentIndexChanged.connect(self.update_plot_grid)
        control_frame.addWidget(self.plot_size_menu)

        # Plot type selector
        control_frame.addWidget(QLabel("View:"))
        self.plot_type_combo = QComboBox()
        self.plot_type_combo.addItems(
            [
                "Per-Antenna",  # Current paginated view
                "Waterfall",  # Heatmap (antenna vs freq)
                "Median Diff",  # Deviation from median
                "Phase Unwrapped",  # Continuous phase
                "RMS per Antenna",  # Bar chart
                "SNR Heatmap",  # SNR per antenna/channel
                "Closure Phases",  # Triangle closure
            ]
        )
        self.plot_type_combo.currentIndexChanged.connect(self._on_plot_type_changed)
        control_frame.addWidget(self.plot_type_combo)

        layout.addLayout(control_frame)

    def create_bandpass_canvas(self):
        self.bandpass_figure = Figure(figsize=(8, 6), tight_layout=True)
        self.bandpass_axes = self.bandpass_figure.subplots(
            self.plot_rows, self.plot_cols
        )
        self.bandpass_canvas = FigureCanvas(self.bandpass_figure)
        layout = QGridLayout(self.bandpass_canvas_frame)
        layout.addWidget(self.bandpass_canvas)

    def create_crossphase_widgets(self):
        layout = QVBoxLayout(self.crossphase_tab)

        # Crossphase file selection
        file_frame = QHBoxLayout()
        self.file_label = QLabel("No caltable selected")
        self.file_label.setAlignment(Qt.AlignCenter)
        file_frame.addWidget(self.file_label)

        file_frame.addStretch()

        select_button = QPushButton("Load")
        select_button.clicked.connect(self.select_crossphase_file)
        file_frame.addWidget(select_button)
        layout.addLayout(file_frame)

        # Crossphase canvas
        self.crossphase_figure = Figure(figsize=(8, 4), tight_layout=True)
        self.crossphase_ax = self.crossphase_figure.add_subplot(111)
        self.crossphase_canvas = FigureCanvas(self.crossphase_figure)
        layout.addWidget(self.crossphase_canvas)
        # Add navigation toolbar
        self.toolbar_crossphase = NavigationToolbar(self.crossphase_canvas, self)
        layout.addWidget(self.toolbar_crossphase)

    def create_selfcal_widgets(self):
        layout = QVBoxLayout(self.selfcal_tab)

        # Selfcal directory selection
        dir_frame = QHBoxLayout()
        self.selfcal_dir_label = QLabel("No caltable selected")
        self.selfcal_dir_label.setAlignment(Qt.AlignCenter)
        dir_frame.addWidget(self.selfcal_dir_label)

        dir_frame.addStretch()

        select_button = QPushButton("Load")
        select_button.clicked.connect(self.select_selfcal_directory)
        dir_frame.addWidget(select_button)
        layout.addLayout(dir_frame)

        # Selfcal canvas
        self.selfcal_canvas_frame = QWidget()
        layout.addWidget(self.selfcal_canvas_frame)
        self.create_selfcal_canvas()

        # Add navigation toolbar for selfcal
        self.toolbar_selfcal = NavigationToolbar(self.selfcal_canvas, self)
        layout.addWidget(self.toolbar_selfcal)

        # Selfcal plot mode
        control_frame = QHBoxLayout()
        self.selfcal_plot_button = QPushButton(
            "Plot Phase vs Antenna"
        )  # Initial text assuming default is Amplitude
        self.selfcal_plot_button.clicked.connect(self.toggle_selfcal_mode)
        control_frame.addWidget(self.selfcal_plot_button)

        # Add other controls if needed, e.g., if pagination was desired for selfcal
        # For now, selfcal plots all antennas at once.

        layout.addLayout(control_frame)

    def create_selfcal_canvas(self):
        self.selfcal_figure = Figure(figsize=(8, 6), tight_layout=True)
        self.selfcal_ax = self.selfcal_figure.add_subplot(111)
        self.selfcal_canvas = FigureCanvas(self.selfcal_figure)
        layout = QGridLayout(
            self.selfcal_canvas_frame
        )  # Use QGridLayout for consistency
        layout.addWidget(self.selfcal_canvas)

    def select_bandpass_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Bandpass Table")
        if directory:
            self.load_bandpass_table(directory)

    def update_plot_grid(self):
        size_str = self.plot_size_menu.currentText()
        self.plot_rows, self.plot_cols = map(int, size_str.split("x"))
        self.antennas_per_page = self.plot_rows * self.plot_cols

        with busy("Drawing the plots…", self):
            # Remove old layout properly
            old_layout = self.bandpass_canvas_frame.layout()
            if old_layout is not None:
                while old_layout.count():
                    item = old_layout.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.deleteLater()
                QWidget().setLayout(old_layout)

            # Re-create bandpass canvas
            self.create_bandpass_canvas()
            self.plot_bandpass_page()

    def _read_table(self, path, read_spectral_window):
        """The arrays of a caltable, or None if it failed (said in a message)
        or was cancelled. A process does the reading, so Cancel can stop it."""
        name = os.path.basename(str(path).rstrip("/"))
        started = []
        try:
            return run_in_thread(
                self,
                f"Reading {name}…",
                read_caltable_safe,
                path,
                read_spectral_window,
                on_start=started.append,
            )
        except Cancelled:
            for process in started:
                process.kill()
            self.statusBar().showMessage("Cancelled", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load caltable:\n{str(e)}")
        return None

    def load_bandpass_table(self, bandpass_table):
        """Whether the table was loaded; if not, what is shown stays."""
        # Load bandpass table using subprocess to avoid Qt/casacore conflicts
        data = self._read_table(bandpass_table, True)
        if data is None:
            return False
        try:
            xx_sols, yy_sols = split_solutions(data["solutions"], data["flag"])
            bandpass_freqs = data["chan_freq"][0, :] / 1e6
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load caltable:\n{str(e)}")
            return False

        self.dir_label.setText(str(bandpass_table))
        self.bandpass_directory = bandpass_table
        self.bandpass_freqs = bandpass_freqs
        self.xx_sols = xx_sols
        self.yy_sols = yy_sols
        self.num_antennas = self.xx_sols.shape[0]
        self.current_page = 0

        # Enable/disable navigation buttons based on data
        self.update_navigation_buttons()
        with busy("Drawing the plots…", self):
            self.plot_bandpass_page()
        return True

    def toggle_bandpass_mode(self):
        if self.plot_mode == "Amplitude":
            self.plot_mode = "Phase"
            self.plot_button.setText("Amplitude")
        else:
            self.plot_mode = "Amplitude"
            self.plot_button.setText("Phase")
        self._on_plot_type_changed()  # Re-plot with current view type

    def _on_plot_type_changed(self):
        """Handle plot type ComboBox change - route to appropriate plot method."""
        if self.xx_sols is None:
            return  # No data loaded

        plot_type = self.plot_type_combo.currentText()
        snr = None
        if plot_type == "SNR Heatmap":
            snr = self._compute_snr()
            if snr is None:
                self.plot_type_combo.blockSignals(True)
                self.plot_type_combo.setCurrentText(self.shown_view)
                self.plot_type_combo.blockSignals(False)
                return

        self.shown_view = plot_type
        with busy(f"Drawing {plot_type}…", self):
            # Enable/disable navigation buttons based on plot type
            is_per_antenna = plot_type == "Per-Antenna"
            self.first_button.setEnabled(is_per_antenna and self.current_page > 0)
            self.prev_button.setEnabled(is_per_antenna and self.current_page > 0)
            self.next_button.setEnabled(
                is_per_antenna
                and self.current_page
                < int(np.ceil(self.num_antennas / self.antennas_per_page)) - 1
            )
            self.last_button.setEnabled(
                is_per_antenna
                and self.current_page
                < int(np.ceil(self.num_antennas / self.antennas_per_page)) - 1
            )
            self.plot_size_menu.setEnabled(is_per_antenna)

            # Enable/disable amplitude/phase toggle based on applicable views
            # Phase Unwrapped, RMS, SNR, and Closure don't use amplitude/phase mode
            toggle_applicable = plot_type in ["Per-Antenna", "Waterfall", "Median Diff"]
            self.plot_button.setEnabled(toggle_applicable)

            # Check if we WERE in single-axis mode before changing the flag
            was_single_axis = getattr(self, "_single_axis_mode", False)

            # Track whether we're in single-axis mode (for next switch)
            self._single_axis_mode = not is_per_antenna

            # If switching TO Per-Antenna from single-axis, need to restore grid
            if plot_type == "Per-Antenna" and was_single_axis:
                self._restore_grid_axes()

            if plot_type == "Per-Antenna":
                self.plot_bandpass_page()
            elif plot_type == "Waterfall":
                self.plot_bandpass_waterfall()
            elif plot_type == "Median Diff":
                self.plot_bandpass_median_diff()
            elif plot_type == "Phase Unwrapped":
                self.plot_bandpass_unwrapped_phase()
            elif plot_type == "RMS per Antenna":
                self.plot_rms_per_antenna()
            elif plot_type == "SNR Heatmap":
                self.plot_snr_heatmap(snr)
            elif plot_type == "Closure Phases":
                self.plot_closure_phases()

    def _compute_snr(self):
        """The SNR map, or None if it failed (said in a message) or was
        cancelled: it comes before anything on screen changes."""
        try:
            if self.xx_sols.shape[1] < SNR_WORKER_CHANNELS:
                with busy("Computing the SNR…", self):
                    return snr_estimate(self.xx_sols, self.yy_sols)
            return run_in_background(
                self, "Computing the SNR…", snr_estimate, self.xx_sols, self.yy_sols
            )
        except Cancelled:
            self.statusBar().showMessage("Cancelled", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not compute the SNR:\n{str(e)}")
        return None

    def plot_bandpass_page(self):
        # Fallback: ensure grid axes exist (normally handled by _on_plot_type_changed)
        if not hasattr(self, "bandpass_axes") or not hasattr(
            self.bandpass_axes, "flat"
        ):
            self._restore_grid_axes()

        start_ant = self.current_page * self.antennas_per_page

        for idx, ax in enumerate(self.bandpass_axes.flat):
            ax.clear()
            ant_idx = start_ant + idx
            if ant_idx >= self.num_antennas:
                ax.axis("off")
            else:
                if self.plot_mode == "Amplitude":
                    ax.plot(
                        self.bandpass_freqs,
                        np.abs(self.xx_sols[ant_idx, :]),
                        "+",
                        color="#1f77b4",
                        label="X",
                        markersize=6,
                    )
                    ax.plot(
                        self.bandpass_freqs,
                        np.abs(self.yy_sols[ant_idx, :]),
                        "+",
                        color="#ff7f0e",
                        label="Y",
                        markersize=6,
                    )
                    ax.set_ylabel("Amplitude")
                else:
                    ax.plot(
                        self.bandpass_freqs,
                        np.angle(self.xx_sols[ant_idx, :], deg=True),
                        "+",
                        color="#1f77b4",
                        label="X",
                        markersize=6,
                    )
                    ax.plot(
                        self.bandpass_freqs,
                        np.angle(self.yy_sols[ant_idx, :], deg=True),
                        "+",
                        color="#ff7f0e",
                        label="Y",
                        markersize=6,
                    )
                    ax.set_ylabel("Phase (\u00b0)")

                ax.set_title(f"Antenna {ant_idx}")
                ax.set_xlim(self.bandpass_freqs[0], self.bandpass_freqs[-1])
                ax.set_xlabel("Frequency (MHz)")
                ax.grid(linestyle="--", linewidth=0.5, color="grey", alpha=0.5)
                # ax.legend() # Add legend to each subplot

        # Add a single legend to the figure if any plots were made, or handle it per subplot as above.
        # If using per-subplot legends, a figure-level legend might be redundant or require careful placement.
        # For now, per-subplot legends are enabled.

        self.bandpass_figure.suptitle(
            f"{self.plot_mode} vs Frequency - Page {self.current_page + 1} of {int(np.ceil(self.num_antennas / self.antennas_per_page))}\n X: Blue, Y: Orange"
        )
        self.bandpass_canvas.draw()

    def _setup_single_axes(self):
        """Reconfigure figure for a single axes plot."""
        self.bandpass_figure.clear()
        self.bandpass_ax_single = self.bandpass_figure.add_subplot(111)
        return self.bandpass_ax_single

    def _restore_grid_axes(self):
        """Restore the grid axes configuration."""
        self.bandpass_figure.clear()
        self.bandpass_axes = self.bandpass_figure.subplots(
            self.plot_rows, self.plot_cols
        )

    def plot_bandpass_waterfall(self):
        """Plot waterfall heatmap of amplitude/phase (antenna vs frequency)."""
        ax = self._setup_single_axes()

        if self.plot_mode == "Amplitude":
            # Average X and Y polarizations
            data = (np.abs(self.xx_sols) + np.abs(self.yy_sols)) / 2
            label = "Amplitude"
        else:
            # Use X polarization phase
            data = np.angle(self.xx_sols, deg=True)
            label = "Phase (°)"

        im = ax.imshow(
            data,
            aspect="auto",
            origin="lower",
            extent=[
                self.bandpass_freqs[0],
                self.bandpass_freqs[-1],
                0,
                self.num_antennas,
            ],
            cmap="viridis" if self.plot_mode == "Amplitude" else "twilight",
        )

        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel("Antenna")
        self.bandpass_figure.colorbar(im, ax=ax, label=label)
        self.bandpass_figure.suptitle(f"Waterfall - {label}")
        self.bandpass_canvas.draw()

    def plot_bandpass_median_diff(self):
        """Plot deviation from median bandpass per antenna."""
        ax = self._setup_single_axes()

        if self.plot_mode == "Amplitude":
            data = np.abs(self.xx_sols)
            label = "Amplitude"
        else:
            data = np.angle(self.xx_sols, deg=True)
            label = "Phase (°)"

        # Compute median across antennas
        median_per_freq = np.nanmedian(data, axis=0)
        diff_from_median = data - median_per_freq

        im = ax.imshow(
            diff_from_median,
            aspect="auto",
            origin="lower",
            extent=[
                self.bandpass_freqs[0],
                self.bandpass_freqs[-1],
                0,
                self.num_antennas,
            ],
            cmap="RdBu_r",
            vmin=-np.nanstd(diff_from_median) * 3,
            vmax=np.nanstd(diff_from_median) * 3,
        )

        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel("Antenna")
        self.bandpass_figure.colorbar(im, ax=ax, label=f"Δ {label} from median")
        self.bandpass_figure.suptitle(f"Deviation from Median - {label}")
        self.bandpass_canvas.draw()

    def plot_bandpass_unwrapped_phase(self):
        """Plot unwrapped phase (continuous, no 360° jumps)."""
        ax = self._setup_single_axes()

        # Unwrap phase for each antenna
        phase_xx = np.angle(self.xx_sols)  # radians

        # Plot a subset of antennas to avoid clutter
        step = max(1, self.num_antennas // 10)
        for ant_idx in range(0, self.num_antennas, step):
            unwrapped = np.unwrap(phase_xx[ant_idx, :])
            ax.plot(
                self.bandpass_freqs,
                np.degrees(unwrapped),
                label=f"Ant {ant_idx}",
                alpha=0.7,
            )

        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel("Unwrapped Phase (°)")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)
        self.bandpass_figure.suptitle("Unwrapped Phase vs Frequency")
        self.bandpass_canvas.draw()

    def plot_rms_per_antenna(self):
        """Bar chart of RMS amplitude variation per antenna."""
        ax = self._setup_single_axes()

        # Compute RMS of amplitude across frequency
        amp_xx = np.abs(self.xx_sols)
        amp_yy = np.abs(self.yy_sols)

        rms_xx = np.nanstd(amp_xx, axis=1)
        rms_yy = np.nanstd(amp_yy, axis=1)

        antennas = np.arange(self.num_antennas)
        width = 0.35

        ax.bar(
            antennas - width / 2, rms_xx, width, label="X", color="#1f77b4", alpha=0.8
        )
        ax.bar(
            antennas + width / 2, rms_yy, width, label="Y", color="#ff7f0e", alpha=0.8
        )

        ax.set_xlabel("Antenna")
        ax.set_ylabel("RMS Amplitude")
        ax.legend()
        ax.grid(True, alpha=0.3, axis="y")

        # Highlight outliers (> 2 sigma from median)
        median_rms = np.nanmedian(np.concatenate([rms_xx, rms_yy]))
        std_rms = np.nanstd(np.concatenate([rms_xx, rms_yy]))
        ax.axhline(
            median_rms + 2 * std_rms, color="red", linestyle="--", label="2σ threshold"
        )

        self.bandpass_figure.suptitle("RMS Amplitude per Antenna")
        self.bandpass_canvas.draw()

    def plot_snr_heatmap(self, snr):
        """Heatmap of SNR estimate per antenna/channel."""
        ax = self._setup_single_axes()

        im = ax.imshow(
            snr,
            aspect="auto",
            origin="lower",
            extent=[
                self.bandpass_freqs[0],
                self.bandpass_freqs[-1],
                0,
                self.num_antennas,
            ],
            cmap="plasma",
            vmin=0,
            vmax=np.nanpercentile(snr, 95),
        )

        ax.set_xlabel("Frequency (MHz)")
        ax.set_ylabel("Antenna")
        self.bandpass_figure.colorbar(im, ax=ax, label="SNR estimate")
        self.bandpass_figure.suptitle("SNR Heatmap (Antenna vs Frequency)")
        self.bandpass_canvas.draw()

    def plot_closure_phases(self):
        """Plot closure phases for antenna triplets."""
        ax = self._setup_single_axes()

        if self.num_antennas < 3:
            ax.text(
                0.5,
                0.5,
                "Need at least 3 antennas for closure phases",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            self.bandpass_canvas.draw()
            return

        # Get phases for X polarization
        phase = np.angle(self.xx_sols)

        # Compute closure phases for triplets (0-1-2, 1-2-3, etc.)
        closure_phases = []
        triplet_labels = []

        num_triplets = min(20, self.num_antennas - 2)  # Limit to 20 triplets
        for i in range(num_triplets):
            # Closure phase = phi_01 + phi_12 - phi_02
            phi_01 = phase[i, :]
            phi_12 = phase[i + 1, :]
            phi_02 = phase[i + 2, :]

            closure = np.degrees(phi_01 + phi_12 - phi_02)
            # Wrap to -180 to 180
            closure = (closure + 180) % 360 - 180

            closure_phases.append(np.nanmean(closure))
            triplet_labels.append(f"{i}-{i+1}-{i+2}")

        ax.bar(range(len(closure_phases)), closure_phases, color="#2ca02c", alpha=0.8)
        ax.set_xticks(range(len(triplet_labels)))
        ax.set_xticklabels(triplet_labels, rotation=45, ha="right", fontsize=8)
        ax.set_xlabel("Antenna Triplet")
        ax.set_ylabel("Closure Phase (°)")
        ax.axhline(0, color="gray", linestyle="--", alpha=0.5)
        ax.grid(True, alpha=0.3, axis="y")

        self.bandpass_figure.suptitle(
            "Closure Phases (should be ~0 for good calibration)"
        )
        self.bandpass_figure.tight_layout()
        self.bandpass_canvas.draw()

    def _show_page(self, page):
        self.current_page = page
        total_pages = int(np.ceil(self.num_antennas / self.antennas_per_page))
        with busy(f"Drawing page {page + 1} of {total_pages}…", self):
            self.plot_bandpass_page()

    def next_bandpass_page(self):
        if self.current_page < int(np.ceil(self.num_antennas / self.antennas_per_page)):
            self._show_page(self.current_page + 1)
        self.update_navigation_buttons()

    def prev_bandpass_page(self):
        if self.current_page > 0:
            self._show_page(self.current_page - 1)
        self.update_navigation_buttons()

    def first_bandpass_page(self):
        if self.current_page != 0:
            self._show_page(0)
        self.update_navigation_buttons()

    def last_bandpass_page(self):
        last_page = int(np.ceil(self.num_antennas / self.antennas_per_page)) - 1
        if self.current_page != last_page:
            self._show_page(last_page)
        self.update_navigation_buttons()

    def update_navigation_buttons(self):
        total_pages = int(np.ceil(self.num_antennas / self.antennas_per_page))
        self.first_button.setEnabled(self.current_page > 0)
        self.prev_button.setEnabled(self.current_page > 0)
        self.next_button.setEnabled(self.current_page + 1 < total_pages)
        self.last_button.setEnabled(self.current_page + 1 < total_pages)

    def select_selfcal_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select Self-calibration Table"
        )
        if directory:
            self.load_selfcal_table(directory)

    def load_selfcal_table(self, caltable):
        """Whether the table was loaded; if not, what is shown stays."""
        # Load selfcal table using subprocess to avoid Qt/casacore conflicts
        data = self._read_table(caltable, False)
        if data is None:
            return False
        try:
            xx_sols, yy_sols = split_solutions(data["solutions"], data["flag"])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load caltable:\n{str(e)}")
            return False

        self.selfcal_dir_label.setText(str(caltable))  # Update selfcal specific label
        self.selfcal_directory = caltable
        self.xx_sols = xx_sols
        self.yy_sols = yy_sols
        self.num_antennas = self.xx_sols.shape[0]
        with busy("Drawing the plot…", self):
            self.plot_selfcal_page()
        return True

    def toggle_selfcal_mode(self):
        if self.plot_mode == "Amplitude":
            self.plot_mode = "Phase"
            self.selfcal_plot_button.setText(
                "Plot Amplitude vs Antenna"
            )  # Update selfcal button
        else:
            self.plot_mode = "Amplitude"
            self.selfcal_plot_button.setText(
                "Plot Phase vs Antenna"
            )  # Update selfcal button
        if self.selfcal_directory:  # Only plot if data is loaded
            with busy("Drawing the plot…", self):
                self.plot_selfcal_page()

    def plot_selfcal_page(self):
        if (
            not self.selfcal_directory or self.xx_sols is None
        ):  # Check if data is loaded
            # Optionally, display a message on the canvas or clear it
            self.selfcal_ax.clear()
            self.selfcal_ax.text(
                0.5,
                0.5,
                "No data loaded. Select a selfcal directory.",
                horizontalalignment="center",
                verticalalignment="center",
                transform=self.selfcal_ax.transAxes,
            )
            self.selfcal_canvas.draw()
            return

        self.selfcal_ax.clear()
        # Plot Gains vs Antenna
        antennas = np.arange(self.num_antennas)
        if self.plot_mode == "Amplitude":
            self.selfcal_ax.plot(
                antennas,
                np.abs(self.xx_sols[:, 0]),
                "+",
                color="#1f77b4",
                label="X",
                markersize=6,
            )
            self.selfcal_ax.plot(
                antennas,
                np.abs(self.yy_sols[:, 0]),
                "+",
                color="#ff7f0e",
                label="Y",
                markersize=6,
            )
            self.selfcal_ax.set_ylabel("Amplitude")
        else:
            self.selfcal_ax.plot(
                antennas,
                np.angle(self.xx_sols[:, 0], deg=True),
                "+",
                color="#1f77b4",
                label="X",
                markersize=6,
            )
            self.selfcal_ax.plot(
                antennas,
                np.angle(self.yy_sols[:, 0], deg=True),
                "+",
                color="#ff7f0e",
                label="Y",
                markersize=6,
            )
            self.selfcal_ax.set_ylabel("Phase (\u00b0)")

        self.selfcal_ax.set_title(f"{self.plot_mode} vs Antenna")
        self.selfcal_ax.set_xlabel("Antenna Number")
        self.selfcal_ax.grid(linestyle="--", linewidth=0.5, color="grey", alpha=0.5)
        self.selfcal_ax.legend()

        self.selfcal_figure.suptitle(
            f"Selfcal Gains - {self.plot_mode} vs Antenna", fontsize=14
        )
        self.selfcal_canvas.draw()

    def select_crossphase_file(self):
        file, _ = QFileDialog.getOpenFileName(
            self,
            "Select Crossphase Caltable",
            "",
            "kcross Files (*.kcross);;Numpy Files (*.npy);;All Files (*)",
        )
        if file:
            self.load_crossphase_file(file)

    def load_crossphase_file(self, caltable):
        """Whether the file was loaded; if not, what is shown stays."""
        try:
            fit = run_in_thread(
                self, f"Reading {os.path.basename(caltable)}…", read_crossphase, caltable
            )
        except Cancelled:
            self.statusBar().showMessage("Cancelled", 3000)
            return False
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load caltable:\n{str(e)}")
            return False
        self.crossphase_file = caltable
        for name, value in fit.items():
            setattr(self, name, value)
        with busy("Drawing the plot…", self):
            self.plot_crossphase()
        return True

    def plot_crossphase(self):
        self.crossphase_ax.clear()

        self.crossphase_ax.plot(self.cross_freq, self.crossphase, "+", label="Data")
        self.crossphase_ax.plot(
            self.cross_freq_filtered,
            self.crossphase_fit,
            "-",
            label=f"Fit ($R^2 = {self.cross_r_squared:.3f}$)",
        )
        self.crossphase_ax.fill_between(
            self.cross_freq_filtered,
            self.crossphase_fit - 3 * self.cross_std_residuals,
            self.crossphase_fit + 3 * self.cross_std_residuals,
            color="gray",
            alpha=0.3,
            label=r"Fit Error ($3\sigma$)",
        )

        self.crossphase_ax.set_xlabel("Frequency (MHz)")
        self.crossphase_ax.set_ylabel("Crossphase (deg)")
        self.crossphase_ax.grid(linestyle="--", linewidth=0.5, color="grey", alpha=0.5)
        self.crossphase_ax.legend()

        self.crossphase_figure.suptitle("Crossphase vs Frequency", fontsize=14)
        self.crossphase_canvas.draw()


def main():
    """Entry point for viewcaltable command."""
    import argparse

    import seaborn as sns

    from ..ui.app import create_app

    parser = argparse.ArgumentParser(description="Bandpass and Crossphase Visualizer")
    parser.add_argument("--theme", choices=["dark", "light"], default=None)
    args = parser.parse_args()

    app = create_app(args.theme)
    sns.set_theme(style="darkgrid")
    rcParams.update(theme_manager.matplotlib_params)

    window = VisualizationApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
