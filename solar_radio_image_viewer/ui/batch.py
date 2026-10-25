"""
Running a batch of files without freezing the window.

A dialog hands :class:`BatchRunner` a worker function (defined at module
level, so it can be sent to another process) and one task per file. The
tasks run in worker processes: the window stays responsive, and Abort stops
them at once, even in the middle of a file. Results come back through Qt
signals, one per task, in task order.

`BatchWindow` is the batch tools' window, laid out as the downloaders are:
the form at the left, at the right a live summary of what a run would
make that turns into the run's progress and log, and the buttons below.
"""

import glob
import multiprocessing
import os
import re
import time
from datetime import datetime

from PyQt5.QtCore import QCoreApplication, QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..processing.workers import process_pool


class BatchRunner(QThread):
    """Runs ``worker(task)`` for every task, in up to ``processes`` worker
    processes.

    ``result(index, value)`` is emitted as each task finishes, in task order,
    with what the worker returned; ``error(index, message)`` instead if the
    worker raised. QThread's ``finished`` follows the last one, and
    ``aborted`` then says whether abort() stopped the batch early.
    """

    result = pyqtSignal(int, object)
    error = pyqtSignal(int, str)

    def __init__(self, worker, tasks, processes=1, preload=(), parent=None):
        super().__init__(parent)
        self.worker = worker
        self.tasks = list(tasks)
        self.processes = max(1, min(int(processes), len(self.tasks) or 1))
        self.preload = preload
        self.aborted = False
        # Quitting the app stops the batch (a running QThread must not be destroyed)
        QCoreApplication.instance().aboutToQuit.connect(self.stop)

    def abort(self):
        """Stop the batch: tasks still running are killed, and no further
        results are emitted."""
        self.aborted = True

    def stop(self):
        """Abort and wait until the workers are gone."""
        self.abort()
        self.wait()

    def run(self):
        if not self.tasks:
            return
        pool = process_pool(self.processes, self.preload)
        try:
            results = pool.imap(self.worker, self.tasks)
            for index in range(len(self.tasks)):
                while not self.aborted:
                    try:
                        value = results.next(timeout=0.1)
                    except multiprocessing.TimeoutError:
                        continue  # still working: check for abort again
                    except Exception as e:
                        self.error.emit(index, f"{type(e).__name__}: {e}")
                    else:
                        self.result.emit(index, value)
                    break
                if self.aborted:
                    break
        finally:
            if self.aborted:
                pool.terminate()
            else:
                pool.close()
            pool.join()


SCAN_DELAY_MS = 300  # after the last keystroke in a folder or pattern
FILES_LISTED = 8  # names shown in the summary


def _escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _short_home(path):
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def output_name(pattern, path):
    """The output name ``pattern`` gives the input ``path``: ``*`` stands for
    its name without the extension (a CASA image's .image or .im too)."""
    name = os.path.basename(path.rstrip("/\\"))
    stem, ext = os.path.splitext(name)
    if ext.lower() in (".fits", ".fit", ".fts", ".image", ".im", ".gz") or os.path.isdir(path):
        name = stem if ext else name
    return pattern.replace("*", name) if "*" in pattern else f"{pattern}_{name}"


class BatchWindow(QDialog):
    """A batch tool's window. A subclass fills ``form_layout`` (the
    ``block``, ``path_row``, ``choice``, ``input_block``, ``output_block``
    and ``parallel_block`` helpers make the usual pieces), then calls
    ``finish_form``; it implements ``summary()`` and ``run()``, which
    hands ``start_batch`` a worker and its tasks, and
    ``batch_result(task, value)``, returning whether that task worked.

    While a batch runs, the form is dimmed and Cancel reads Stop; closing
    the window stops the batch too, and closes the window once the
    workers are gone.
    """

    VERB = "Run"  # the main button
    RUNNING = "Working"  # the progress page's heading
    NOUN = ("file", "files")  # what one task makes
    SUMMARY_ROWS = ()  # the summary's names, in order

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(720, 520)
        from .geometry import fit_to_screen

        fit_to_screen(self, 820, 600)
        self._batch = None
        self._close_after_batch = False
        self._started = None
        self._done = self._failed = 0
        self._log_lines = []
        self._problem = None
        self._ran = False  # the side shows a finished run until the form changes
        self.files = []
        self.output_folder = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_form(), 5)
        body.addWidget(self._build_side(), 3)
        outer.addLayout(body, 1)
        outer.addWidget(self._build_footer(), 0)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(SCAN_DELAY_MS)
        self._scan_timer.timeout.connect(self.update_summary)

    # ------------------------------------------------------------ the form
    def _build_form(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.form = QWidget()
        self.form_layout = QVBoxLayout(self.form)
        self.form_layout.setContentsMargins(22, 18, 22, 18)
        self.form_layout.setSpacing(20)
        scroll.setWidget(self.form)
        return scroll

    def finish_form(self):
        """Close the form (a subclass calls this once it is filled) and
        show the summary."""
        from .styles import set_hand_cursor

        self.form_layout.addStretch()
        set_hand_cursor(self)
        self.update_summary()

    def block(self, title, aside=None):
        """A titled block of the form; returns (layout, aside label or None)."""
        from .widgets import kicker, kicker_row

        layout = QVBoxLayout()
        layout.setSpacing(10)
        label = None
        if aside is not None:
            head, label = kicker_row(title, aside)
            layout.addWidget(head)
        else:
            layout.addWidget(kicker(title))
        self.form_layout.addLayout(layout)
        return layout, label

    @staticmethod
    def row_label(text):
        """A row's name at its left, as the downloaders' Cadence and Source."""
        label = QLabel(text)
        label.setObjectName("Hint")
        label.setMinimumWidth(label.fontMetrics().horizontalAdvance("Reference") + 8)
        return label

    def path_row(self, edit, browse, name=None, button="Browse…"):
        """``edit`` with a Browse button calling ``browse``, after ``name``."""
        row = QHBoxLayout()
        row.setSpacing(8)
        if name:
            row.addWidget(self.row_label(name))
        edit.setMinimumHeight(30)
        edit.textChanged.connect(self.schedule_summary)
        row.addWidget(edit, 1)
        browse_button = QPushButton(button)
        browse_button.setAutoDefault(False)
        browse_button.clicked.connect(browse)
        row.addWidget(browse_button)
        return row

    def choice(self, labels, tooltips=(), then=None):
        """One of ``labels``, as segmented buttons on a track; the first is
        chosen. Returns (group, buttons); ``chosen(buttons)`` reads it."""
        from .widgets import segmented

        buttons = []
        for i, text in enumerate(labels):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setAutoDefault(False)
            if i < len(tooltips) and tooltips[i]:
                button.setToolTip(tooltips[i])
            buttons.append(button)
        buttons[0].setChecked(True)

        def pick(chosen):
            for button in buttons:
                button.setChecked(button is chosen)
            if then is not None:
                then()
            self.update_summary()

        for button in buttons:
            button.clicked.connect(lambda _=False, b=button: pick(b))
        return segmented(buttons, role="HeaderSegmented"), buttons

    @staticmethod
    def chosen(buttons):
        return next((i for i, b in enumerate(buttons) if b.isChecked()), 0)

    @staticmethod
    def set_chosen(buttons, index):
        for i, button in enumerate(buttons):
            button.setChecked(i == index)

    def choice_row(self, name, labels, tooltips=(), then=None, aside=""):
        """A named row of segmented choices, with a note after them.
        Returns (row layout, buttons, note label)."""
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.row_label(name))
        group, buttons = self.choice(labels, tooltips, then)
        row.addWidget(group)
        note = QLabel(aside)
        note.setObjectName("Meta")
        row.addWidget(note, 1)
        return row, buttons, note

    def input_block(self, folder="", pattern="*.fits", title="Images"):
        """A folder and the pattern its files must match; the count of
        matching files at the block's right."""
        layout, self.files_count_label = self.block(title, "")
        self.dir_edit = QLineEdit(folder)
        self.dir_edit.setPlaceholderText("The folder the images are in")
        layout.addLayout(self.path_row(self.dir_edit, self.browse_directory, "Folder"))
        self.dir_edit.editingFinished.connect(lambda: self.input_folder_chosen(self.dir_edit.text()))
        self.pattern_edit = QLineEdit(pattern)
        self.pattern_edit.setPlaceholderText("*.fits, sun_*.fits, *.image")
        self.pattern_edit.setToolTip("The images to take: * stands for any part of a name")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.row_label("Matching"))
        self.pattern_edit.setMinimumHeight(30)
        self.pattern_edit.textChanged.connect(self.schedule_summary)
        row.addWidget(self.pattern_edit, 1)
        layout.addLayout(row)
        return layout

    def output_block(self, folder="", pattern="", title="Save to"):
        """The output folder and the output names' pattern."""
        layout, _ = self.block(title)
        self.out_dir_edit = QLineEdit(folder)
        self.out_dir_edit.setPlaceholderText("A folder, or a name for one inside the input folder")
        layout.addLayout(self.path_row(self.out_dir_edit, self.browse_output_directory, "Folder"))
        self.output_pattern_edit = QLineEdit(pattern)
        self.output_pattern_edit.setToolTip("* stands for each input image's name")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.row_label("Names"))
        self.output_pattern_edit.setMinimumHeight(30)
        self.output_pattern_edit.textChanged.connect(self.schedule_summary)
        row.addWidget(self.output_pattern_edit, 1)
        hint = QLabel("* is the input's name")
        hint.setObjectName("Meta")
        row.addWidget(hint)
        layout.addLayout(row)
        return layout

    def parallel_block(self, layout=None):
        """A switch for running in worker processes, and how many. Added
        to ``layout``, or as a block of its own."""
        if layout is None:
            layout, _ = self.block("Speed")
        self.parallel_row = QFrame()
        self.parallel_row.setObjectName("Surface")
        row = QHBoxLayout(self.parallel_row)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        self.multiprocessing_check = QCheckBox()
        self.multiprocessing_check.setObjectName("Switch")
        self.multiprocessing_check.setChecked(True)
        row.addWidget(self.multiprocessing_check, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(QLabel("Several images at once"))
        cores = multiprocessing.cpu_count()
        desc = QLabel(f"In separate processes · this computer has {cores} cores")
        desc.setObjectName("Meta")
        text.addWidget(desc)
        row.addLayout(text, 1)
        self.cores_spinbox = QSpinBox()
        self.cores_spinbox.setRange(1, cores)
        self.cores_spinbox.setValue(max(1, cores - 1))
        self.cores_spinbox.setSuffix(" at once")
        self.cores_spinbox.setToolTip("How many images to work on at the same time")
        row.addWidget(self.cores_spinbox, 0, Qt.AlignVCenter)
        self.multiprocessing_check.toggled.connect(self.cores_spinbox.setEnabled)
        self.multiprocessing_check.toggled.connect(self.update_summary)
        self.cores_spinbox.valueChanged.connect(self.update_summary)
        layout.addWidget(self.parallel_row)
        return layout

    def processes(self):
        if not self.multiprocessing_check.isChecked():
            return 1
        return self.cores_spinbox.value()

    # -------------------------------------------- the usual browse buttons
    def browse_directory(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Input Folder", self.dir_edit.text() or os.path.expanduser("~")
        )
        if folder:
            self.dir_edit.setText(folder)
            self.input_folder_chosen(folder)
            self.update_summary()

    def input_folder_chosen(self, folder):
        """The input folder was chosen or typed: a subclass suggests an
        output folder here, leaving one the user gave alone."""

    def browse_output_directory(self):
        start = self.out_dir_edit.text()
        if start and not os.path.isabs(start) and self.dir_edit.text():
            start = os.path.join(self.dir_edit.text(), start)
        folder = QFileDialog.getExistingDirectory(
            self, "Select Output Folder", start or self.dir_edit.text() or os.path.expanduser("~")
        )
        if folder:
            self.out_dir_edit.setText(folder)

    def matching_files(self):
        folder, pattern = self.dir_edit.text().strip(), self.pattern_edit.text().strip()
        if not folder or not pattern:
            return []
        return sorted(glob.glob(os.path.join(os.path.expanduser(folder), pattern)))

    def output_dir_for(self, path):
        """The output folder for the input ``path``: the folder given, or a
        relative name taken inside the input's folder."""
        folder = os.path.expanduser(self.out_dir_edit.text().strip())
        if not os.path.isabs(folder):
            folder = os.path.join(os.path.dirname(path), folder)
        return folder

    # ------------------------------------------------------ the side column
    def _build_side(self):
        side = QFrame()
        side.setObjectName("SidePanel")
        side.setMinimumWidth(280)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(0, 0, 0, 0)
        self.side_pages = QStackedWidget()
        self.side_pages.addWidget(self._build_summary())
        self.side_pages.addWidget(self._build_progress())
        layout.addWidget(self.side_pages)
        return side

    @staticmethod
    def _label(text="", role=None, rich=False):
        label = QLabel(text)
        if role:
            label.setObjectName(role)
        if rich:
            label.setTextFormat(Qt.RichText)
        return label

    def _build_summary(self):
        from .widgets import kicker, rule

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        layout.addWidget(kicker("You'll get"))
        head = QVBoxLayout()
        head.setSpacing(2)
        self.count_label = self._label("", "DialogTitle", rich=True)
        self.count_sub = self._label("", "Hint")
        self.count_sub.setWordWrap(True)
        head.addWidget(self.count_label)
        head.addWidget(self.count_sub)
        layout.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.summary_values = {}
        for i, key in enumerate(self.SUMMARY_ROWS):
            name = self._label(key, "Hint")
            grid.addWidget(name, i, 0, Qt.AlignTop)
            value = self._label("", rich=True)
            value.setWordWrap(True)
            grid.addWidget(value, i, 1)
            self.summary_values[key] = (name, value)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.files_rule = rule()
        layout.addWidget(self.files_rule)
        self.files_head = self._label("", "Meta")
        layout.addWidget(self.files_head)
        from .download_parts import _monospace

        self.files_list = _monospace(self._label("", "Meta"))
        self.files_list.setWordWrap(True)
        self.files_list.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.files_list.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.files_list)
        layout.addStretch()
        self.note_label = self._label("", "Meta")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)
        return page

    def _build_progress(self):
        from .widgets import kicker_row, rule

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        head, self.elapsed_label = kicker_row(self.RUNNING, "0:00 elapsed")
        self.progress_kicker = head.findChild(QLabel, "Kicker")
        layout.addWidget(head)
        block = QVBoxLayout()
        block.setSpacing(6)
        numbers = QHBoxLayout()
        numbers.setSpacing(8)
        self.progress_count = self._label("", "DialogTitle", rich=True)
        self.progress_left = self._label("", "Hint")
        numbers.addWidget(self.progress_count)
        numbers.addWidget(self.progress_left, 0, Qt.AlignBottom)
        numbers.addStretch()
        block.addLayout(numbers)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("ThinProgress")
        self.progress_bar.setTextVisible(False)
        block.addWidget(self.progress_bar)
        layout.addLayout(block)
        tiles = QHBoxLayout()
        tiles.setSpacing(6)
        self.tile_values = {}
        for name in ("Done", "Failed"):
            tile = QFrame()
            tile.setObjectName("Tile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(9, 7, 9, 7)
            tile_layout.setSpacing(1)
            tile_layout.addWidget(self._label(name, "Meta"))
            value = self._label("0", "Mono")
            tile_layout.addWidget(value)
            self.tile_values[name] = value
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)
        layout.addWidget(rule())
        self.log_view = QTextEdit()
        self.log_view.setObjectName("QuietLog")
        self.log_view.setReadOnly(True)
        self.log_view.setFrameShape(QFrame.NoFrame)
        self.log_view.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.log_view, 1)
        return page

    def _build_footer(self):
        footer = QFrame()
        footer.setObjectName("CanvasBar")
        footer.setFixedHeight(52)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(8)
        self.footer_note = self._label("", "Meta")
        layout.addWidget(self.footer_note)
        layout.addStretch()
        self.folder_button = QPushButton("Show folder")
        self.folder_button.setObjectName("GhostButton")
        self.folder_button.setAutoDefault(False)
        self.folder_button.setToolTip("Open the folder the results were saved in")
        self.folder_button.clicked.connect(self.show_folder)
        self.folder_button.hide()
        layout.addWidget(self.folder_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("GhostButton")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.cancel_or_abort)
        layout.addWidget(self.cancel_button)
        self.ok_button = QPushButton(self.VERB)
        self.ok_button.setObjectName("PrimaryButton")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.run)
        layout.addWidget(self.ok_button)
        return footer

    # ------------------------------------------------------------ summary
    def schedule_summary(self, *args):
        self._scan_timer.start()

    def summary(self):
        """What a run would make, as a dict: ``count`` (the headline),
        ``sub`` (the line under it), ``rows`` {name: rich text},
        ``files`` (input paths to list), ``note``, and ``problem`` (why it
        cannot run yet, or None)."""
        return {}

    def update_summary(self, *args):
        if self._batch is not None:
            return
        if self._ran:  # a change after a run: back to what the next would make
            self._ran = False
            self.side_pages.setCurrentIndex(0)
            self.folder_button.hide()
        info = self.summary() or {}
        from .styles import set_tone, theme_manager

        secondary = theme_manager.palette["text_secondary"]
        self._problem = info.get("problem")
        count = info.get("count", "")
        if isinstance(count, tuple):  # (number, noun)
            count = f"{count[0]}" + f"<span style='color:{secondary}'> {_escape(count[1])}</span>"
        self.count_label.setText(count or "—")
        self.count_sub.setText(self._problem or info.get("sub", ""))
        set_tone(self.count_sub, "warning" if self._problem else None)
        rows = info.get("rows", {})
        for key, (name, value) in self.summary_values.items():
            text = rows.get(key)
            name.setVisible(text is not None)
            value.setVisible(text is not None)
            value.setText(text or "")
        files = info.get("files", [])
        self.files_rule.setVisible(bool(files))
        self.files_head.setVisible(bool(files))
        self.files_list.setVisible(bool(files))
        if files:
            shown = files[:FILES_LISTED]
            self.files_head.setText(info.get("files_head", "Images"))
            # long names may break after _ . - only
            names = [
                re.sub(r"([_.-])", "\\1\u200b", _escape(os.path.basename(f.rstrip("/\\"))))
                for f in shown
            ]
            more = len(files) - len(shown)
            if more:
                names.append(f"<span style='color:{secondary}'>and {more} more</span>")
            self.files_list.setText("<br>".join(names))
        self.note_label.setText(info.get("note", ""))
        self.ok_button.setEnabled(self._problem is None)
        verb = info.get("verb")
        if verb:
            self.ok_button.setText(verb)

    def ready(self):
        """Refresh the summary; whether a run can start."""
        self._scan_timer.stop()
        self.update_summary()
        return self._problem is None

    # ------------------------------------------------------------- the log
    def log(self, message, status=""):
        """A line in the run's log, shown newest first."""
        from .download_parts import render_log

        self._log_lines.insert(0, (datetime.now().strftime("%H:%M"), message, status))
        render_log(self.log_view, self._log_lines[:500])

    def log_text(self):
        """The log, oldest line first, as plain text."""
        return "\n".join(
            f"{message}  {status}".rstrip() for _, message, status in reversed(self._log_lines)
        )

    # ------------------------------------------------------------- running
    def run(self):
        """The main button: check the form and start the batch."""

    def start_batch(self, worker, tasks, processes=1, output_folder=""):
        """Run ``worker`` on ``tasks`` in up to ``processes`` processes."""
        self.output_folder = output_folder
        self._done = self._failed = 0
        self._started = time.time()
        self.progress_kicker.setText(self.RUNNING.upper())
        self.side_pages.setCurrentIndex(1)
        self.progress_bar.setRange(0, max(1, len(tasks)))
        self.progress_bar.setValue(0)
        self._set_form_locked(True)
        self.ok_button.setEnabled(False)
        self.folder_button.hide()
        self.cancel_button.setText("Stop")
        self.cancel_button.setEnabled(True)
        self._close_after_batch = False
        runner = BatchRunner(worker, tasks, processes, parent=self)
        runner.result.connect(self._on_batch_result)
        runner.error.connect(self._on_batch_error)
        runner.finished.connect(self._on_batch_finished)
        self._batch = runner
        self._render_progress()
        self._tick()
        self._timer.start()
        runner.start()

    def start_log(self):
        """Clear the log for a new run (before the lines that lead up to
        ``start_batch``)."""
        self._log_lines = []
        self.log_view.clear()

    def batch_running(self):
        return self._batch is not None

    def _set_form_locked(self, locked):
        self.form.setEnabled(not locked)
        if locked:
            effect = QGraphicsOpacityEffect(self.form)
            effect.setOpacity(0.45)
            self.form.setGraphicsEffect(effect)
        else:
            self.form.setGraphicsEffect(None)

    def _tick(self):
        if self._started is None:
            return
        elapsed = int(time.time() - self._started)
        self.elapsed_label.setText(f"{elapsed // 60}:{elapsed % 60:02d} elapsed")

    def _render_progress(self, finished=False):
        from .download_parts import _duration_text
        from .styles import theme_manager

        secondary = theme_manager.palette["text_secondary"]
        total = self.progress_bar.maximum()
        handled = self._done + self._failed
        noun = self.NOUN[0] if total == 1 else self.NOUN[1]
        if finished:
            self.progress_count.setText(
                f"{self._done}" + f"<span style='color:{secondary}'> of {total} {noun}</span>"
            )
            self.progress_left.setText("")
        else:
            self.progress_count.setText(
                f"{handled}" + f"<span style='color:{secondary}'> / {total} {noun}</span>"
            )
            if handled and self._started:
                left = (time.time() - self._started) / handled * (total - handled)
                self.progress_left.setText(f"≈ {_duration_text(left)} left" if left > 5 else "almost done")
            else:
                self.progress_left.setText("")
        self.progress_bar.setValue(handled)
        self.tile_values["Done"].setText(str(self._done))
        self.tile_values["Failed"].setText(str(self._failed))

    def abort_batch(self):
        if self._batch is not None and not self._batch.aborted:
            self._batch.abort()
            self.log("Stopping…")
            self.cancel_button.setEnabled(False)

    def cancel_or_abort(self):
        """Cancel: Stop while a batch runs, else close."""
        if self._batch is not None:
            self.abort_batch()
        else:
            self.reject()

    def reject(self):
        # Esc and the window's close button: stop the batch first
        if self._batch is not None:
            self._close_after_batch = True
            self.abort_batch()
            return
        super().reject()

    def batch_result(self, task, value):
        """Log ``value``, what the worker returned for ``task``; return
        whether the task worked."""
        return True

    def batch_error(self, task, message):
        """The worker raised on ``task``."""
        self.log(f"{os.path.basename(str(task[0]))}: {message}", "failed")

    def batch_finished(self, aborted):
        """After the last task (a subclass may log a summary)."""

    def _on_batch_result(self, index, value):
        if self.batch_result(self._batch.tasks[index], value) is False:
            self._failed += 1
        else:
            self._done += 1
        self._render_progress()

    def _on_batch_error(self, index, message):
        self._failed += 1
        self.batch_error(self._batch.tasks[index], message)
        self._render_progress()

    def _on_batch_finished(self):
        runner, self._batch = self._batch, None
        runner.wait()  # "finished" comes just before the thread ends
        self._timer.stop()
        self._tick()
        self._started = None
        self._set_form_locked(False)
        self.cancel_button.setText("Close")
        self.cancel_button.setEnabled(True)
        self.ok_button.setEnabled(True)
        self._ran = True
        if runner.aborted:
            self.log("Stopped before the end", "stopped")
            heading = "Stopped"
        elif self._failed and not self._done:
            heading = "Failed"
        elif self._failed:
            heading = f"Done · {self._failed} failed"
        else:
            heading = "Done"
        self.progress_kicker.setText(heading.upper())
        self._render_progress(finished=True)
        self.folder_button.setVisible(bool(self._done and self.output_folder))
        if self._close_after_batch:
            super().reject()
        else:
            self.batch_finished(runner.aborted)
        runner.deleteLater()

    def show_folder(self):
        if self.output_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_folder))
