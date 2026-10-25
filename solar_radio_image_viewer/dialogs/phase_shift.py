"""
Solar phase center shift: moves an image's coordinates so the Sun's disc is
where the Sun really was, for one image or a folder of them (all shifted
by what one reference image shows).
"""

import os
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..processing.fits_io import is_fits
from ..ui.batch import BatchWindow, _escape, _short_home, output_name
from ..ui.busy import busy
from ..ui.errors import show_error
from ..ui.open_image import choose_image
from ..ui.widgets import kicker

STOKES = ("I", "Q", "U", "V", "All four")


def _stem(path):
    """An image's name without .fits, or a CASA image's without .image."""
    name = os.path.basename(path.rstrip("/\\"))
    if os.path.isdir(path):
        for ext in (".image", ".im"):
            if name.endswith(ext):
                return name[: -len(ext)]
        return name
    return os.path.splitext(name)[0]


class PhaseShiftDialog(BatchWindow):
    """Shifts one image, or a folder of images, to the true solar center."""

    VERB = "Apply Shift"
    RUNNING = "Shifting"
    NOUN = ("image", "images")
    SUMMARY_ROWS = ("Image", "Reference", "From", "Method", "Stokes", "Output", "Speed")

    def __init__(self, parent=None, imagename=None, batch_mode=False):
        super().__init__(parent, "Solar Phase Center Shift")
        self.imagename = imagename
        folder = os.path.dirname(imagename) if imagename else ""

        # Images
        layout, self.files_count_label = self.block("Images", "")
        row, self.mode_buttons, _ = self.choice_row(
            "Shift",
            ("One image", "A folder"),
            ("", "Every matching image in a folder, by the offset one reference image shows"),
            then=self.update_mode_ui,
        )
        layout.addLayout(row)
        self.single_file_widget = QWidget()
        single = QVBoxLayout(self.single_file_widget)
        single.setContentsMargins(0, 0, 0, 0)
        self.image_path_edit = QLineEdit(imagename or "")
        self.image_path_edit.setPlaceholderText("A FITS file or CASA image")
        single.addLayout(self.path_row(self.image_path_edit, self.browse_image, "Image"))
        layout.addWidget(self.single_file_widget)
        self.batch_file_widget = QWidget()
        batch = QVBoxLayout(self.batch_file_widget)
        batch.setContentsMargins(0, 0, 0, 0)
        batch.setSpacing(10)
        self.reference_image_edit = QLineEdit("")
        self.reference_image_edit.setPlaceholderText("The image the shift is worked out from")
        batch.addLayout(self.path_row(self.reference_image_edit, self.browse_reference_image, "Reference"))
        self.dir_edit = QLineEdit(folder)
        self.dir_edit.setPlaceholderText("The folder the images are in")
        batch.addLayout(self.path_row(self.dir_edit, self.browse_directory, "Folder"))
        self.dir_edit.editingFinished.connect(lambda: self.input_folder_chosen(self.dir_edit.text()))
        self.pattern_edit = QLineEdit("*.fits")
        self.pattern_edit.setToolTip("The images to shift: * stands for any part of a name")
        self.input_pattern_edit = self.pattern_edit
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.row_label("Matching"))
        self.pattern_edit.setMinimumHeight(30)
        self.pattern_edit.textChanged.connect(self.schedule_summary)
        row.addWidget(self.pattern_edit, 1)
        batch.addLayout(row)
        layout.addWidget(self.batch_file_widget)

        # Method
        layout, _ = self.block("Method")
        row, self.method_buttons, _ = self.choice_row(
            "Find Sun by",
            ("Gaussian fit", "Centre of mass"),
            ("Fit a 2-D Gaussian to the disc", "The centroid of the emission above a threshold"),
            then=self._update_method_options,
        )
        layout.addLayout(row)
        self.sigma_widget = QWidget()
        sigma_row = QHBoxLayout(self.sigma_widget)
        sigma_row.setContentsMargins(0, 0, 0, 0)
        sigma_row.setSpacing(10)
        sigma_row.addWidget(self.row_label("Threshold"))
        self.sigma_spinbox = QDoubleSpinBox()
        self.sigma_spinbox.setRange(1.0, 20.0)
        self.sigma_spinbox.setValue(10.0)
        self.sigma_spinbox.setSingleStep(0.5)
        self.sigma_spinbox.setSuffix(" × RMS")
        self.sigma_spinbox.setToolTip("Only emission above this is taken into the centre of mass")
        self.sigma_spinbox.valueChanged.connect(self.update_summary)
        sigma_row.addWidget(self.sigma_spinbox)
        sigma_row.addStretch()
        layout.addWidget(self.sigma_widget)
        row, self.stokes_buttons, _ = self.choice_row(
            "Stokes", STOKES, ("", "", "", "", "Each image's I, Q, U and V, in four files")
        )
        layout.addLayout(row)
        centre = QFrame()
        centre.setObjectName("Surface")
        centre_row = QHBoxLayout(centre)
        centre_row.setContentsMargins(10, 8, 10, 8)
        centre_row.setSpacing(8)
        self.visual_center_check = QCheckBox()
        self.visual_center_check.setObjectName("Switch")
        self.visual_center_check.setChecked(True)
        self.visual_center_check.toggled.connect(self.update_summary)
        centre_row.addWidget(self.visual_center_check, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(QLabel("Move the Sun to the image centre"))
        desc = QLabel("Shifts the pixels too, so the disc sits in the middle of the image")
        desc.setObjectName("Meta")
        desc.setWordWrap(True)
        text.addWidget(desc)
        centre_row.addLayout(text, 1)
        layout.addWidget(centre)

        # Save to
        layout, _ = self.block("Save to")
        self.single_output_widget = QWidget()
        single = QVBoxLayout(self.single_output_widget)
        single.setContentsMargins(0, 0, 0, 0)
        self.output_path_edit = QLineEdit("")
        self.output_path_edit.setPlaceholderText("centered_<name>.fits next to the image")
        single.addLayout(self.path_row(self.output_path_edit, self.browse_output, "File"))
        layout.addWidget(self.single_output_widget)
        self.batch_output_widget = QWidget()
        batch = QVBoxLayout(self.batch_output_widget)
        batch.setContentsMargins(0, 0, 0, 0)
        batch.setSpacing(10)
        self.out_dir_edit = QLineEdit(os.path.join(folder, "centered") if folder else "")
        self.out_dir_edit.setPlaceholderText("A folder, or a name for one inside the input folder")
        batch.addLayout(self.path_row(self.out_dir_edit, self.browse_output_directory, "Folder"))
        self.output_pattern_edit = QLineEdit("centered_*.fits")
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
        batch.addLayout(row)
        layout.addWidget(self.batch_output_widget)

        # Speed (a folder only)
        self.multiprocessing_widget = QWidget()
        speed = QVBoxLayout(self.multiprocessing_widget)
        speed.setContentsMargins(0, 0, 0, 0)
        speed.setSpacing(10)
        speed.addWidget(kicker("Speed"))
        self.parallel_block(speed)
        self.form_layout.addWidget(self.multiprocessing_widget)

        self.footer_note.setText("Writes FITS")
        if batch_mode:
            self.set_chosen(self.mode_buttons, 1)
        self.update_mode_ui()
        self._update_method_options()
        self.finish_form()

    # ------------------------------------------------------------ the form
    def batch_mode(self):
        return self.chosen(self.mode_buttons) == 1

    def full_stokes(self):
        return self.chosen(self.stokes_buttons) == 4

    def stokes_list(self):
        index = self.chosen(self.stokes_buttons)
        return ["I", "Q", "U", "V"] if index == 4 else [STOKES[index]]

    def use_gaussian(self):
        return self.chosen(self.method_buttons) == 0

    def update_mode_ui(self):
        batch = self.batch_mode()
        self.single_file_widget.setVisible(not batch)
        self.batch_file_widget.setVisible(batch)
        self.single_output_widget.setVisible(not batch)
        self.batch_output_widget.setVisible(batch)
        self.multiprocessing_widget.setVisible(batch)
        self.files_count_label.setVisible(batch)

    def _update_method_options(self):
        self.sigma_widget.setVisible(not self.use_gaussian())

    def browse_image(self):
        path = choose_image(self, "Select Input Image")
        if path:
            self.image_path_edit.setText(path)
            self.imagename = path

    def browse_reference_image(self):
        path = choose_image(self, "Select Reference Image")
        if path:
            self.reference_image_edit.setText(path)
            if not self.dir_edit.text():
                self.dir_edit.setText(os.path.dirname(path))
                self.pattern_edit.setText("*.image" if os.path.isdir(path) else "*.fits")
                self.input_folder_chosen(self.dir_edit.text())

    def input_folder_chosen(self, folder):
        current = self.out_dir_edit.text().strip().rstrip("/\\")
        if folder and (not current or os.path.basename(current) == "centered"):
            self.out_dir_edit.setText(os.path.join(folder, "centered"))

    def browse_output(self):
        source = self.image_path_edit.text()
        suggested = self._single_output(source) if source else ""
        path, _ = QFileDialog.getSaveFileName(self, "Save Output As", suggested, "FITS Files (*.fits)")
        if path:
            self.output_path_edit.setText(path if path.endswith(".fits") else path + ".fits")

    # ------------------------------------------------------------- outputs
    def _single_output(self, source):
        path = self.output_path_edit.text().strip()
        if not path:
            path = os.path.join(os.path.dirname(source), f"centered_{_stem(source)}.fits")
        return path if path.endswith(".fits") else path + ".fits"

    def _output_pattern(self, source):
        """The output pattern a task is given: a path, * for the image's name."""
        if self.batch_mode():
            folder = self.output_dir_for(source)
            pattern = os.path.join(folder, self.output_pattern_edit.text().strip())
        else:
            pattern = self._single_output(source)
        return pattern

    def _outputs(self, source):
        """(stokes, output pattern) for ``source``: one file per Stokes."""
        pattern = self._output_pattern(source)
        stokes = self.stokes_list()
        if len(stokes) == 1:
            return [(stokes[0], pattern)]
        root, ext = os.path.splitext(pattern)
        return [(s, f"{root}_{s}{ext or '.fits'}") for s in stokes]

    def _sources(self):
        if self.batch_mode():
            return self.matching_files()
        path = self.image_path_edit.text().strip()
        return [path] if path else []

    # ------------------------------------------------------------- summary
    def summary(self):
        sources = self._sources()
        n = len(sources)
        stokes = self.stokes_list()
        method = "Gaussian fit" if self.use_gaussian() else f"centre of mass above {self.sigma_spinbox.value():g} × RMS"
        info = {
            "count": (n, "image" if n == 1 else "images"),
            "sub": "with the Sun at its true position"
            + (", moved to the centre" if self.visual_center_check.isChecked() else ""),
            "rows": {
                "Method": _escape(method[0].upper() + method[1:]),
                "Stokes": "I, Q, U and V, a file each" if len(stokes) > 1 else stokes[0],
            },
        }
        if self.batch_mode():
            reference = self.reference_image_edit.text().strip()
            info["files"] = sources
            self.files_count_label.setText(f"{n} found" if self.dir_edit.text() else "")
            if reference:
                info["rows"]["Reference"] = _escape(os.path.basename(reference.rstrip("/")))
            if self.dir_edit.text():
                info["rows"]["From"] = _escape(_short_home(self.dir_edit.text())) + " · " + _escape(self.pattern_edit.text())
            processes = self.processes()
            info["rows"]["Speed"] = "one at a time" if processes == 1 else f"{processes} at once"
            info["verb"] = f"Shift {n} image{'s' if n != 1 else ''}" if n else "Shift"
            if not reference:
                info["problem"] = "Choose the reference image the shift is worked out from"
            elif not os.path.exists(reference):
                info["problem"] = "The reference image does not exist"
            elif not self.dir_edit.text().strip():
                info["problem"] = "Choose the folder the images are in"
            elif not sources:
                info["problem"] = "No image in the folder matches"
            elif not self.out_dir_edit.text().strip() or not self.output_pattern_edit.text().strip():
                info["problem"] = "Choose a folder and names to save to"
        else:
            info["verb"] = "Apply Shift"
            if sources:
                info["rows"]["Image"] = _escape(os.path.basename(sources[0].rstrip("/")))
            if not sources:
                info["problem"] = "Choose the image to shift"
            elif not os.path.exists(sources[0]):
                info["problem"] = "The image does not exist"
        if sources and "problem" not in info:
            out = self._outputs(sources[0])[0][1]
            info["rows"]["Output"] = _escape(_short_home(output_name(out, sources[0]) if "*" in out else out))
        return info

    # ------------------------------------------------------------- running
    def apply_phase_shift(self):
        self.run()

    def run(self):
        """Work out the shift from the reference image, then shift each image
        in worker processes."""
        from ..coordinates.move_phasecenter import SolarPhaseCenter

        if not self.ready():
            return
        batch = self.batch_mode()
        sources = self._sources()
        reference = self.reference_image_edit.text().strip() if batch else sources[0]
        self.start_log()
        try:
            with busy():
                phase_result = SolarPhaseCenter(msname=None).cal_solar_phaseshift(
                    imagename=reference,
                    fit_gaussian=self.use_gaussian(),
                    sigma=self.sigma_spinbox.value(),
                )
        except Exception as e:
            import traceback

            show_error(
                self,
                "Calculation Error",
                f"Could not work out the phase shift from "
                f"{os.path.basename(reference)}: {e}\n\nThe reference "
                "image must show the Sun and have an observation date.",
                traceback.format_exc(),
            )
            return

        if self._is_helioprojective(reference):
            phase_result.update(true_ra=0.0, true_dec=0.0, is_hpc=True)
            self.log("Helioprojective image: the Sun goes to (0, 0)")
        else:
            phase_result["is_hpc"] = False
            self.log(
                f"Sun at RA {phase_result.get('true_ra', 0):.4f}°, Dec {phase_result.get('true_dec', 0):.4f}°"
            )
        if not phase_result.get("needs_shift", False):
            self.log("No significant shift needed")
            if (
                QMessageBox.question(
                    self,
                    "No Shift Needed",
                    "The Sun already looks centred. Shift anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                == QMessageBox.No
            ):
                return

        tasks = []
        for source in sources:
            for stokes, pattern in self._outputs(source):
                tasks.append(
                    (
                        source,
                        phase_result.get("true_ra"),
                        phase_result.get("true_dec"),
                        stokes,
                        pattern,
                        self.visual_center_check.isChecked(),
                        phase_result,
                    )
                )
        if not batch:
            existing = [t[4] for t in tasks if os.path.exists(t[4])]
            if existing and (
                QMessageBox.question(
                    self,
                    "Overwrite?",
                    "Overwrite existing files?\n\n" + "\n".join(existing),
                    QMessageBox.Yes | QMessageBox.No,
                )
                == QMessageBox.No
            ):
                return
        self._stokes_list = self.stokes_list()
        self.start_batch(
            process_single_file_phase_shift,
            tasks,
            processes=self.processes() if batch else 1,
            output_folder=os.path.dirname(tasks[0][4]),
        )

    def batch_result(self, task, value):
        success, path, message = value
        name = os.path.basename(path.rstrip("/"))
        if len(self._stokes_list) > 1:
            name = f"{name}, Stokes {task[3]}"
        if success:
            self.log(f"{name}" + (f": {message}" if message else ""), "shifted")
            return True
        self.log(f"{name}: {message}", "failed")
        return False

    def batch_finished(self, aborted):
        if not aborted:
            self.log(f"Shifted {self._done} of {self._done + self._failed}")

    def _is_helioprojective(self, imagepath):
        """Whether the image is in helioprojective coordinates (Solar-X/Y)."""
        if not imagepath or not os.path.exists(imagepath):
            return False
        try:
            if is_fits(imagepath):
                from astropy.io import fits

                header = fits.getheader(imagepath)
                ctype1 = header.get("CTYPE1", "").upper()
                ctype2 = header.get("CTYPE2", "").upper()
                if "HPLN" in ctype1 or "HPLT" in ctype2 or "SOLAR" in ctype1 or "SOLAR" in ctype2:
                    return True
            if os.path.isdir(imagepath):
                try:
                    from casatools import image as IA

                    ia_tool = IA()
                    ia_tool.open(imagepath)
                    names = [n.upper() for n in ia_tool.coordsys().names()]
                    ia_tool.close()
                    if {"SOLAR-X", "SOLAR-Y", "HPLN-TAN", "HPLT-TAN"} & set(names):
                        return True
                except Exception:
                    pass
            return False
        except Exception:
            return False


def process_single_file_phase_shift(args):
    """
    Wrapper for multiprocessing: instantiates SolarPhaseCenter and processes file.

    Parameters
    ----------
    args : tuple
        Tuple containing (file_path, ra, dec, stokes, output_pattern, visual_center, phase_result)

    Returns
    -------
    tuple
        (success, file_path, error_message)
    """
    try:
        from ..coordinates.move_phasecenter import SolarPhaseCenter

        # Instantiate fresh for this process
        spc = SolarPhaseCenter()
        return spc.process_single_file(args)
    except Exception as e:
        import traceback

        traceback.print_exc()
        # Try to extract filename from args if possible
        filename = args[0] if args and len(args) > 0 else "Unknown"
        return (False, filename, f"Process Error: {str(e)}")
