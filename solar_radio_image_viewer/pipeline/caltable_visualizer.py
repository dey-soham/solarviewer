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
    QStackedWidget,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QKeySequence, QPalette
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# NOTE: casacore is loaded via subprocess to completely isolate it from Qt
from pathlib import Path
import io
import subprocess
import os

from ..ui.background import Cancelled, run_in_background, run_in_thread
from ..ui.busy import busy
from ..ui.plot_theme import figure_toolbar, follow_theme, style_figure
from ..ui.styles import SPACE_M, SPACE_S, set_hand_cursor, theme_manager, themed
from ..ui.widgets import button_bar, dialog_layout, empty_note, kicker, vrule
from .spectra_icons import icon as spectra_icon


_READ_CALTABLE_SCRIPT = """
import io, sys
import numpy as np
from casacore.tables import table

path, with_spectral_window = sys.argv[1], sys.argv[2] == "1"
arrays = {}
tb = table(path, readonly=True, ack=False)
arrays["solutions"] = tb.getcol("CPARAM")
arrays["flag"] = tb.getcol("FLAG")
tb.close()
if with_spectral_window:
    tb = table(path + "/SPECTRAL_WINDOW", readonly=True, ack=False)
    arrays["chan_freq"] = tb.getcol("CHAN_FREQ")
    tb.close()
buffer = io.BytesIO()
np.savez(buffer, **arrays)
sys.stdout.buffer.write(buffer.getvalue())
"""


from ..ui.geometry import fit_to_screen

READ_TIMEOUT_S = 300
SNR_WORKER_CHANNELS = 2000  # below this the SNR is under a second or so: not worth a worker
PLOT_FONT_LEGEND = 8

# (section, rows of (name, what it does), the role of the name)
HELP = (
    ("Tabs", (
        ("Bandpass", "Bandpass calibration solutions: amplitude or phase vs frequency"),
        ("Crossphase", "Crossphase calibration with a polynomial fit"),
        ("Selfcal", "Self-calibration solutions: amplitude or phase vs antenna"),
    ), "Hint"),
    ("Navigation", (
        ("]", "Next page"),
        ("[", "Previous page"),
        ("}", "Last page (Shift+])"),
        ("{", "First page (Shift+[)"),
    ), "Key"),
    ("Mode and file", (
        ("T", "Toggle amplitude or phase"),
        ("Ctrl+O", "Open a table or file for the current tab"),
        ("Ctrl+Q", "Quit"),
    ), "Key"),
    ("Tab switching", (
        ("1", "Bandpass tab"),
        ("2", "Crossphase tab"),
        ("3", "Selfcal tab"),
    ), "Key"),
    ("Help", (("F1", "Show this dialog"),), "Key"),
)


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

    median_crossphase = np.nanmedian(crossphase)
    absolute_deviation = np.abs(crossphase - median_crossphase)
    mad = np.nanmedian(absolute_deviation)

    threshold = 10 * mad
    mask = absolute_deviation <= threshold  # (NaN: left out)

    cross_freq_filtered = cross_freq[mask]
    crossphase_filtered = crossphase[mask]

    cross_fit_coeffs = np.polyfit(cross_freq_filtered, crossphase_filtered, 2)
    cross_fit_func = np.poly1d(cross_fit_coeffs)
    crossphase_fit = cross_fit_func(cross_freq_filtered)

    residuals = crossphase_filtered - crossphase_fit
    sse = np.sum(residuals**2)
    sst = np.sum((crossphase_filtered - np.mean(crossphase_filtered)) ** 2)

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


def plot_stack(canvas, note):
    """``canvas`` with ``note`` in its place until there is something to plot."""
    stack = QStackedWidget()
    stack.addWidget(empty_note(note))
    stack.addWidget(canvas)

    def ground(palette):
        # (Matplotlib paints the canvas white under the figure, which shows
        # in a thin line at the edge at a fractional display scale)
        canvas.setPalette(QPalette(QColor(palette["window"])))

    themed(canvas, ground)
    return stack


def controls_row(canvas_toolbar):
    """The row under a plot, led by its toolbar (which takes the spare width)."""
    row = QHBoxLayout()
    row.setSpacing(SPACE_S)
    row.addWidget(canvas_toolbar, 1)
    return row


class VisualizationApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bandpass and crossphase visualizer")
        fit_to_screen(self, 1000, 810)

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
        self.shown_view = "Per antenna"  # the view on the canvas
        self.selfcal_xx = None  # (its own: the Bandpass tab keeps its table)
        self.selfcal_yy = None
        self.selfcal_mode = "Amplitude"

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
        QShortcut(QKeySequence("F1"), self, self.show_shortcuts_dialog)  # Help

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
            if self.plot_button.isEnabled():  # (not every view has the two modes)
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
        from PyQt5.QtWidgets import QDialog

        dialog = QDialog(self)
        dialog.setWindowTitle("Caltable visualizer help")
        layout = dialog_layout(dialog)

        about = QLabel("A tool for visualizing LOFAR calibration tables.")
        about.setObjectName("Hint")
        layout.addWidget(about)

        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_S)
        grid.setColumnStretch(1, 1)
        row = 0
        for title, rows, role in HELP:
            grid.addWidget(kicker(title), row, 0, 1, 2)
            row += 1
            for key, text in rows:
                name = QLabel(key)
                name.setObjectName(role)
                grid.addWidget(name, row, 0, Qt.AlignLeft)
                grid.addWidget(QLabel(text), row, 1)
                row += 1
        layout.addLayout(grid)

        box, _ = button_bar(secondary=("Close", dialog.accept), parent=dialog)
        layout.addWidget(box)

        dialog.exec_()

    def create_widgets(self):
        # Central widget and layout
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_M)

        # Tabs
        self.tab_control = QTabWidget(self)
        main_layout.addWidget(self.tab_control)

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
        layout = QVBoxLayout(self.bandpass_tab)
        layout.setContentsMargins(0, SPACE_M, 0, 0)
        layout.setSpacing(SPACE_S)

        # Bandpass directory selection
        dir_frame = QHBoxLayout()
        self.dir_label = QLabel("No caltable selected")
        self.dir_label.setObjectName("DetailMono")
        dir_frame.addWidget(self.dir_label)

        dir_frame.addStretch()

        select_button = QPushButton("Load…")
        select_button.clicked.connect(self.select_bandpass_directory)
        dir_frame.addWidget(select_button)
        layout.addLayout(dir_frame)

        # Bandpass canvas
        self.create_bandpass_canvas()
        layout.addWidget(self.bandpass_stack, 1)

        # Navigation toolbar, plot mode and pages in one row
        self.toolbar_bandpass = figure_toolbar(self.bandpass_canvas, self)
        control_frame = controls_row(self.toolbar_bandpass)

        self.plot_button = QPushButton("Show phase")
        self.plot_button.setToolTip("Switch between amplitude and phase (T)")
        self.plot_button.clicked.connect(self.toggle_bandpass_mode)
        control_frame.addWidget(self.plot_button)

        pager = QHBoxLayout()
        pager.setSpacing(0)
        self.first_button = self._page_button("First page ({)", self.first_bandpass_page)
        self.prev_button = self._page_button("Previous page ([)", self.prev_bandpass_page)
        self.next_button = self._page_button("Next page (])", self.next_bandpass_page)
        self.last_button = self._page_button("Last page (})", self.last_bandpass_page)
        buttons = (("first", self.first_button), ("prev", self.prev_button),
                   ("next", self.next_button), ("last", self.last_button))
        for _name, button in buttons:
            pager.addWidget(button)
        control_frame.addLayout(pager)

        def tint(palette):
            for name, button in buttons:
                button.setIcon(spectra_icon(name, palette))

        themed(self, tint)
        control_frame.addWidget(vrule())

        # Plot settings
        control_frame.addWidget(self._hint("Plots per page"))
        self.plot_size_menu = QComboBox()
        self.plot_size_menu.addItems(["2x2", "3x3", "4x4", "5x5"])
        self.plot_size_menu.setCurrentText("3x3")
        self.plot_size_menu.currentIndexChanged.connect(self.update_plot_grid)
        control_frame.addWidget(self.plot_size_menu)

        # Plot type selector
        control_frame.addWidget(self._hint("View"))
        self.plot_type_combo = QComboBox()
        self.plot_type_combo.addItems(
            [
                "Per antenna",  # Current paginated view
                "Waterfall",  # Heatmap (antenna vs freq)
                "Median difference",  # Deviation from median
                "Unwrapped phase",  # Continuous phase
                "RMS per antenna",  # Bar chart
                "SNR heatmap",  # SNR per antenna/channel
                "Closure phases",  # Triangle closure
            ]
        )
        self.plot_type_combo.currentIndexChanged.connect(self._on_plot_type_changed)
        control_frame.addWidget(self.plot_type_combo)

        layout.addLayout(control_frame)

    @staticmethod
    def _hint(text):
        label = QLabel(text)
        label.setObjectName("Hint")
        return label

    @staticmethod
    def _page_button(tip, slot):
        button = QPushButton()
        button.setObjectName("NavButton")
        button.setToolTip(tip)
        button.clicked.connect(slot)
        button.setEnabled(False)
        return button

    def create_bandpass_canvas(self):
        self.bandpass_figure = Figure(figsize=(8, 6), tight_layout=True)
        self.bandpass_axes = self.bandpass_figure.subplots(
            self.plot_rows, self.plot_cols
        )
        self.bandpass_canvas = FigureCanvas(self.bandpass_figure)
        follow_theme(self, self.bandpass_figure, self.bandpass_canvas)
        self.bandpass_stack = plot_stack(
            self.bandpass_canvas, "Load a calibration table to see its solutions"
        )

    def create_crossphase_widgets(self):
        layout = QVBoxLayout(self.crossphase_tab)
        layout.setContentsMargins(0, SPACE_M, 0, 0)
        layout.setSpacing(SPACE_S)

        # Crossphase file selection
        file_frame = QHBoxLayout()
        self.file_label = QLabel("No caltable selected")
        self.file_label.setObjectName("DetailMono")
        file_frame.addWidget(self.file_label)

        file_frame.addStretch()

        select_button = QPushButton("Load…")
        select_button.clicked.connect(self.select_crossphase_file)
        file_frame.addWidget(select_button)
        layout.addLayout(file_frame)

        # Crossphase canvas
        self.crossphase_figure = Figure(figsize=(8, 4), tight_layout=True)
        self.crossphase_ax = self.crossphase_figure.add_subplot(111)
        self.crossphase_canvas = FigureCanvas(self.crossphase_figure)
        follow_theme(self, self.crossphase_figure, self.crossphase_canvas)
        self.crossphase_stack = plot_stack(
            self.crossphase_canvas, "Load a crossphase file to see its fit"
        )
        layout.addWidget(self.crossphase_stack, 1)
        # Add navigation toolbar
        self.toolbar_crossphase = figure_toolbar(self.crossphase_canvas, self)
        layout.addLayout(controls_row(self.toolbar_crossphase))

    def create_selfcal_widgets(self):
        layout = QVBoxLayout(self.selfcal_tab)
        layout.setContentsMargins(0, SPACE_M, 0, 0)
        layout.setSpacing(SPACE_S)

        # Selfcal directory selection
        dir_frame = QHBoxLayout()
        self.selfcal_dir_label = QLabel("No caltable selected")
        self.selfcal_dir_label.setObjectName("DetailMono")
        dir_frame.addWidget(self.selfcal_dir_label)

        dir_frame.addStretch()

        select_button = QPushButton("Load…")
        select_button.clicked.connect(self.select_selfcal_directory)
        dir_frame.addWidget(select_button)
        layout.addLayout(dir_frame)

        # Selfcal canvas
        self.selfcal_figure = Figure(figsize=(8, 6), tight_layout=True)
        self.selfcal_ax = self.selfcal_figure.add_subplot(111)
        self.selfcal_canvas = FigureCanvas(self.selfcal_figure)
        follow_theme(self, self.selfcal_figure, self.selfcal_canvas)
        self.selfcal_stack = plot_stack(
            self.selfcal_canvas, "Load a self-calibration table to see its solutions"
        )
        layout.addWidget(self.selfcal_stack, 1)

        # Navigation toolbar and plot mode in one row
        self.toolbar_selfcal = figure_toolbar(self.selfcal_canvas, self)
        control_frame = controls_row(self.toolbar_selfcal)
        self.selfcal_plot_button = QPushButton("Show phase")  # (amplitude comes first)
        self.selfcal_plot_button.setToolTip("Switch between amplitude and phase (T)")
        self.selfcal_plot_button.clicked.connect(self.toggle_selfcal_mode)
        control_frame.addWidget(self.selfcal_plot_button)
        layout.addLayout(control_frame)

    def select_bandpass_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select bandpass table")
        if directory:
            self.load_bandpass_table(directory)

    def update_plot_grid(self):
        # (the same antenna stays in view, on the page that now holds it)
        first_antenna = self.current_page * self.antennas_per_page
        size_str = self.plot_size_menu.currentText()
        self.plot_rows, self.plot_cols = map(int, size_str.split("x"))
        self.antennas_per_page = self.plot_rows * self.plot_cols

        self.current_page = first_antenna // self.antennas_per_page
        with busy("Drawing the plots…", self):
            self._restore_grid_axes()
            if self.xx_sols is not None:
                self.plot_bandpass_page()
        self.update_navigation_buttons()

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

        # What the canvas shows is the last table's, whichever view it is
        self.shown_view = "Per antenna"
        self._on_plot_type_changed(label="Drawing the plots…")
        return True

    def toggle_bandpass_mode(self):
        if self.plot_mode == "Amplitude":
            self.plot_mode = "Phase"
            self.plot_button.setText("Show amplitude")
        else:
            self.plot_mode = "Amplitude"
            self.plot_button.setText("Show phase")
        self._on_plot_type_changed()  # Re-plot with current view type

    def _on_plot_type_changed(self, _index=None, label=None):
        """Handle plot type ComboBox change - route to appropriate plot method."""
        if self.xx_sols is None:
            return  # No data loaded

        plot_type = self.plot_type_combo.currentText()
        snr = None
        if plot_type == "SNR heatmap":
            snr = self._compute_snr()
            if snr is None:
                self.plot_type_combo.blockSignals(True)
                self.plot_type_combo.setCurrentText(self.shown_view)
                self.plot_type_combo.blockSignals(False)
                if label is None:
                    return
                plot_type = self.shown_view  # (a table just loaded must show)

        self.shown_view = plot_type
        self.bandpass_stack.setCurrentWidget(self.bandpass_canvas)
        with busy(label or f"Drawing {plot_type}…", self):
            # Enable/disable navigation buttons based on plot type
            is_per_antenna = plot_type == "Per antenna"
            self.update_navigation_buttons()
            self.plot_size_menu.setEnabled(is_per_antenna)

            # Enable/disable amplitude/phase toggle based on applicable views
            # Phase Unwrapped, RMS, SNR, and Closure don't use amplitude/phase mode
            toggle_applicable = plot_type in ["Per antenna", "Waterfall", "Median difference"]
            self.plot_button.setEnabled(toggle_applicable)

            # Check if we WERE in single-axis mode before changing the flag
            was_single_axis = getattr(self, "_single_axis_mode", False)

            # Track whether we're in single-axis mode (for next switch)
            self._single_axis_mode = not is_per_antenna

            # If switching TO Per-Antenna from single-axis, need to restore grid
            if plot_type == "Per antenna" and was_single_axis:
                self._restore_grid_axes()

            if plot_type == "Per antenna":
                self.plot_bandpass_page()
            elif plot_type == "Waterfall":
                self.plot_bandpass_waterfall()
            elif plot_type == "Median difference":
                self.plot_bandpass_median_diff()
            elif plot_type == "Unwrapped phase":
                self.plot_bandpass_unwrapped_phase()
            elif plot_type == "RMS per antenna":
                self.plot_rms_per_antenna()
            elif plot_type == "SNR heatmap":
                self.plot_snr_heatmap(snr)
            elif plot_type == "Closure phases":
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
                # The axes names once: on the left column, and under the
                # lowest plot of each column
                if idx % self.plot_cols:
                    ax.set_ylabel("")
                if idx + self.plot_cols >= self.antennas_per_page or ant_idx + self.plot_cols >= self.num_antennas:
                    ax.set_xlabel("Frequency (MHz)")
                ax.grid(linestyle="--", linewidth=0.5)

        self.bandpass_figure.suptitle(
            f"{self.plot_mode} vs frequency, page {self.current_page + 1} of {int(np.ceil(self.num_antennas / self.antennas_per_page))}\nX blue, Y orange"
        )
        self._draw_bandpass()

    def _draw_bandpass(self):
        style_figure(self.bandpass_figure)
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
        self.bandpass_figure.suptitle(f"Waterfall: {label.lower()}")
        self._draw_bandpass()

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
        self.bandpass_figure.suptitle(f"Deviation from median: {label.lower()}")
        self._draw_bandpass()

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
        ax.set_ylabel("Unwrapped phase (°)")
        ax.legend(loc="upper right", fontsize=PLOT_FONT_LEGEND)
        ax.grid(True)
        self.bandpass_figure.suptitle("Unwrapped phase vs frequency")
        self._draw_bandpass()

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
        ax.set_ylabel("RMS amplitude")
        ax.grid(True, axis="y")

        # Highlight outliers (> 2 sigma from median)
        median_rms = np.nanmedian(np.concatenate([rms_xx, rms_yy]))
        std_rms = np.nanstd(np.concatenate([rms_xx, rms_yy]))
        ax.axhline(
            median_rms + 2 * std_rms, color="red", linestyle="--", label="2σ threshold"
        )
        ax.legend(fontsize=PLOT_FONT_LEGEND)

        self.bandpass_figure.suptitle("RMS amplitude per antenna")
        self._draw_bandpass()

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
        self.bandpass_figure.suptitle("SNR heatmap (antenna vs frequency)")
        self._draw_bandpass()

    def plot_closure_phases(self):
        """Plot closure phases for antenna triplets."""
        ax = self._setup_single_axes()

        if self.num_antennas < 3:
            note = self.bandpass_stack.widget(0)
            note.setText("Need at least 3 antennas for closure phases")
            self.bandpass_stack.setCurrentWidget(note)
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
        ax.set_xlabel("Antenna triplet")
        ax.set_ylabel("Closure phase (°)")
        ax.axhline(0, color=theme_manager.palette["text_secondary"], linestyle="--", alpha=0.5)
        ax.grid(True, axis="y")

        self.bandpass_figure.suptitle(
            "Closure phases (should be ~0 for good calibration)"
        )
        self.bandpass_figure.tight_layout()
        self._draw_bandpass()

    def _pages(self):
        """Whether the bandpass is shown a page at a time (the keys work
        whatever is shown; there are no pages without a table, or in the
        other views)."""
        return self.xx_sols is not None and self.shown_view == "Per antenna"

    def _show_page(self, page):
        self.current_page = page
        total_pages = int(np.ceil(self.num_antennas / self.antennas_per_page))
        with busy(f"Drawing page {page + 1} of {total_pages}…", self):
            self.plot_bandpass_page()

    def next_bandpass_page(self):
        if self._pages() and self.current_page + 1 < int(np.ceil(self.num_antennas / self.antennas_per_page)):
            self._show_page(self.current_page + 1)
        self.update_navigation_buttons()

    def prev_bandpass_page(self):
        if self._pages() and self.current_page > 0:
            self._show_page(self.current_page - 1)
        self.update_navigation_buttons()

    def first_bandpass_page(self):
        if self._pages() and self.current_page != 0:
            self._show_page(0)
        self.update_navigation_buttons()

    def last_bandpass_page(self):
        last_page = int(np.ceil(self.num_antennas / self.antennas_per_page)) - 1
        if self._pages() and self.current_page != last_page:
            self._show_page(last_page)
        self.update_navigation_buttons()

    def update_navigation_buttons(self):
        total_pages = int(np.ceil(self.num_antennas / self.antennas_per_page))
        pages = self._pages()
        self.first_button.setEnabled(pages and self.current_page > 0)
        self.prev_button.setEnabled(pages and self.current_page > 0)
        self.next_button.setEnabled(pages and self.current_page + 1 < total_pages)
        self.last_button.setEnabled(pages and self.current_page + 1 < total_pages)

    def select_selfcal_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select self-calibration table"
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
        self.selfcal_xx = xx_sols
        self.selfcal_yy = yy_sols
        with busy("Drawing the plot…", self):
            self.plot_selfcal_page()
        return True

    def toggle_selfcal_mode(self):
        if self.selfcal_mode == "Amplitude":
            self.selfcal_mode = "Phase"
            self.selfcal_plot_button.setText("Show amplitude")
        else:
            self.selfcal_mode = "Amplitude"
            self.selfcal_plot_button.setText("Show phase")
        if self.selfcal_directory:  # Only plot if data is loaded
            with busy("Drawing the plot…", self):
                self.plot_selfcal_page()

    def plot_selfcal_page(self):
        if (
            not self.selfcal_directory or self.selfcal_xx is None
        ):  # Check if data is loaded
            self.selfcal_stack.setCurrentIndex(0)
            return

        self.selfcal_stack.setCurrentWidget(self.selfcal_canvas)
        self.selfcal_ax.clear()
        # Plot Gains vs Antenna
        antennas = np.arange(self.selfcal_xx.shape[0])
        if self.selfcal_mode == "Amplitude":
            self.selfcal_ax.plot(
                antennas,
                np.abs(self.selfcal_xx[:, 0]),
                "+",
                color="#1f77b4",
                label="X",
                markersize=6,
            )
            self.selfcal_ax.plot(
                antennas,
                np.abs(self.selfcal_yy[:, 0]),
                "+",
                color="#ff7f0e",
                label="Y",
                markersize=6,
            )
            self.selfcal_ax.set_ylabel("Amplitude")
        else:
            self.selfcal_ax.plot(
                antennas,
                np.angle(self.selfcal_xx[:, 0], deg=True),
                "+",
                color="#1f77b4",
                label="X",
                markersize=6,
            )
            self.selfcal_ax.plot(
                antennas,
                np.angle(self.selfcal_yy[:, 0], deg=True),
                "+",
                color="#ff7f0e",
                label="Y",
                markersize=6,
            )
            self.selfcal_ax.set_ylabel("Phase (\u00b0)")

        self.selfcal_ax.set_xlabel("Antenna number")
        self.selfcal_ax.grid(linestyle="--", linewidth=0.5)
        self.selfcal_ax.legend(fontsize=PLOT_FONT_LEGEND)

        self.selfcal_figure.suptitle(f"Selfcal gains: {self.selfcal_mode.lower()} vs antenna")
        style_figure(self.selfcal_figure)
        self.selfcal_canvas.draw()

    def select_crossphase_file(self):
        file, _ = QFileDialog.getOpenFileName(
            self,
            "Select crossphase table",
            "",
            "kcross files (*.kcross);;NumPy files (*.npy);;All files (*)",
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
        self.file_label.setText(str(caltable))
        self.crossphase_file = caltable
        for name, value in fit.items():
            setattr(self, name, value)
        with busy("Drawing the plot…", self):
            self.plot_crossphase()
        return True

    def plot_crossphase(self):
        self.crossphase_stack.setCurrentWidget(self.crossphase_canvas)
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
            color=theme_manager.palette["text_secondary"],
            alpha=0.3,
            label=r"Fit error ($3\sigma$)",
        )

        self.crossphase_ax.set_xlabel("Frequency (MHz)")
        self.crossphase_ax.set_ylabel("Crossphase (deg)")
        self.crossphase_ax.grid(linestyle="--", linewidth=0.5)
        self.crossphase_ax.legend(fontsize=PLOT_FONT_LEGEND)

        self.crossphase_figure.suptitle("Crossphase vs frequency")
        style_figure(self.crossphase_figure)
        self.crossphase_canvas.draw()


def main():
    """Entry point for viewcaltable command."""
    import argparse

    from ..ui.app import create_app

    parser = argparse.ArgumentParser(description="Bandpass and crossphase visualizer")
    parser.add_argument("--theme", choices=["dark", "light"], default=None)
    args = parser.parse_args()

    app = create_app(args.theme)

    window = VisualizationApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
