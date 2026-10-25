"""
Batch coordinate transformation: images between RA/Dec and helioprojective
(HPC) coordinates.
"""

import glob
import os
import traceback
import uuid

from ..processing.fits_io import is_fits
from ..ui.batch import BatchWindow, _escape, _short_home, output_name

SYSTEMS = (("Helioprojective", "HPC", "hpc"), ("RA/Dec", "RADEC", "radec"))
STOKES = ("I", "Q", "U", "V", "All four")


def process_single_file_coordsys(args):
    """Process a single file for coordinate transformation - standalone function for multiprocessing

    Parameters:
    -----------
    args : tuple
        Tuple containing (input_file, output_path, stokes, process_id, target_mode)
        target_mode: "HPC" or "RADEC"

    Returns:
    --------
    dict
        Result dictionary with processing outcome
    """
    input_file, output_path, stokes, process_id, target_mode = args

    try:
        result = {
            "input_file": input_file,
            "output_path": output_path,
            "stokes": stokes,
            "success": False,
            "error": None,
            "skipped": False,  # True if already in target format and just copied
        }

        # Check coordinate system
        already_hpc = is_already_hpc(input_file)
        
        # Decide if we need to convert or just copy
        should_copy = False
        if target_mode == "HPC" and already_hpc:
            should_copy = True
        elif target_mode == "RADEC" and not already_hpc:
            should_copy = True
            
        if should_copy:
            # Just copy the file instead of converting
            import shutil

            try:
                if os.path.isdir(input_file):
                    # CASA image - copy directory
                    if os.path.exists(output_path):
                        shutil.rmtree(output_path)
                    shutil.copytree(input_file, output_path)
                else:
                    # FITS file - copy file
                    shutil.copy2(input_file, output_path)
                result["success"] = True
                result["skipped"] = True
                return result
            except Exception as e:
                result["error"] = f"Copy failed: {str(e)}"
                return result

        # Import transformation functions
        from ..coordinates.helioprojective import convert_and_save_hpc, convert_hpc_to_radec

        # The conversions write FITS, which needs the extension
        if not os.path.splitext(output_path)[1]:
            output_path += ".fits"
            result["output_path"] = output_path

        # Generate a unique file suffix for this process to avoid conflicts
        temp_suffix = f"_proc_{process_id}_{uuid.uuid4().hex[:8]}"

        if target_mode == "HPC":
            # Convert RA/DEC to HPC
            success = convert_and_save_hpc(
                input_file,
                output_path,
                Stokes=stokes,
                overwrite=True,
                temp_suffix=temp_suffix,
            )
        else:
            # Convert HPC to RA/DEC
            # convert_hpc_to_radec handles saving to output_path
            success = convert_hpc_to_radec(
                input_file,
                output_path,
                overwrite=True
            )

        result["success"] = success
        if not success and result["error"] is None:
            result["error"] = f"Transformation to {target_mode} failed (check file headers for valid WCS info)"
            
        return result
    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        return result


def is_already_hpc(imagepath):
    """Check if image is already in helioprojective coordinates (Solar-X/Y or HPLN/HPLT).

    This is a standalone function for use in multiprocessing.

    Parameters:
    -----------
    imagepath : str
        Path to the image file (FITS) or directory (CASA image)

    Returns:
    --------
    bool
        True if already in HPC coordinates, False otherwise
    """
    if not imagepath or not os.path.exists(imagepath):
        return False

    try:
        # For FITS files, check the header
        if is_fits(imagepath):
            from astropy.io import fits

            header = fits.getheader(imagepath)
            ctype1 = header.get("CTYPE1", "").upper()
            ctype2 = header.get("CTYPE2", "").upper()

            # Check for HPC (Helioprojective)
            if (
                "HPLN" in ctype1
                or "HPLT" in ctype2
                or "SOLAR" in ctype1
                or "SOLAR" in ctype2
            ):
                return True
        
        # For CASA images, check coordinate system
        if os.path.isdir(imagepath):
            try:
                from casatools import image as IA

                ia_tool = IA()
                ia_tool.open(imagepath)
                csys = ia_tool.coordsys()
                dimension_names = [n.upper() for n in csys.names()]
                ia_tool.close()

                if "SOLAR-X" in dimension_names or "SOLAR-Y" in dimension_names:
                    return True
                if "HPLN-TAN" in dimension_names or "HPLT-TAN" in dimension_names:
                    return True
            except Exception:
                pass

        return False
    except Exception:
        return False



class CoordinateTransformationDialog(BatchWindow):
    """Converts a folder of images to helioprojective or RA/Dec coordinates."""

    VERB = "Convert"
    RUNNING = "Converting"
    NOUN = ("image", "images")
    SUMMARY_ROWS = ("From", "To", "Stokes", "Output", "Speed")

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent, "Transform Coordinates")
        self.current_file = current_file
        folder = os.path.dirname(current_file) if current_file else ""
        pattern = f"*{os.path.splitext(current_file)[1]}" if current_file and os.path.splitext(current_file)[1] else "*.fits"
        self.input_block(folder, pattern)
        layout, _ = self.block("Convert to")
        row, self.system_buttons, _ = self.choice_row(
            "System",
            [f"{name} · {short}" for name, short, _ in SYSTEMS[:1]] + [SYSTEMS[1][0]],
            ("Solar-X / Solar-Y, as seen from the observatory", "Celestial right ascension and declination"),
            then=self._system_changed,
        )
        layout.addLayout(row)
        row, self.stokes_buttons, _ = self.choice_row(
            "Stokes",
            STOKES,
            ("", "", "", "", "Each image's I, Q, U and V, in four files"),
        )
        layout.addLayout(row)
        self.output_block(os.path.join(folder, "hpc") if folder else "", "hpc_*.fits")
        self.output_dir_edit = self.out_dir_edit  # its old name
        self.parallel_block()
        self.footer_note.setText("Images already in the system are copied")
        self.finish_form()

    def target_mode(self):
        return SYSTEMS[self.chosen(self.system_buttons)][1]

    def stokes_list(self):
        index = self.chosen(self.stokes_buttons)
        return ["I", "Q", "U", "V"] if index == 4 else [STOKES[index]]

    def _system_changed(self):
        sub = SYSTEMS[self.chosen(self.system_buttons)][2]
        folder = self.out_dir_edit.text().strip().rstrip("/\\")
        source = self.dir_edit.text().strip()
        if source and (not folder or os.path.basename(folder) in ("hpc", "radec")):
            self.out_dir_edit.setText(os.path.join(source, sub))
        if self.output_pattern_edit.text() in ("", "hpc_*.fits", "radec_*.fits"):
            self.output_pattern_edit.setText(f"{sub}_*.fits")

    def input_folder_chosen(self, folder):
        self._system_changed()

    def _get_matching_files(self):
        return self.matching_files()

    def _outputs(self, input_file):
        """(stokes, output path) for ``input_file``: one file per Stokes."""
        name = output_name(self.output_pattern_edit.text().strip(), input_file)
        path = os.path.join(self.output_dir_for(input_file), name)
        stokes = self.stokes_list()
        if len(stokes) == 1:
            return [(stokes[0], path)]
        root, ext = os.path.splitext(path)
        return [(s, f"{root}_{s}{ext}") for s in stokes]

    def summary(self):
        files = self._get_matching_files()
        pattern = self.output_pattern_edit.text().strip()
        name, short, _ = SYSTEMS[self.chosen(self.system_buttons)]
        n = len(files)
        stokes = self.stokes_list()
        info = {
            "count": (n, "image" if n == 1 else "images"),
            "sub": f"in {name.lower() if short == 'HPC' else name} coordinates"
            + (f", {n * 4} files (I, Q, U, V)" if len(stokes) > 1 and n else ""),
            "files": files,
            "rows": {
                "To": f"{_escape(name)}" + (" · HPC" if short == "HPC" else ""),
                "Stokes": "I, Q, U and V, a file each" if len(stokes) > 1 else stokes[0],
            },
            "verb": f"Convert {n} image{'s' if n != 1 else ''}" if files else "Convert",
        }
        self.files_count_label.setText(f"{n} found" if self.dir_edit.text() else "")
        if self.dir_edit.text():
            info["rows"]["From"] = _escape(_short_home(self.dir_edit.text())) + " · " + _escape(self.pattern_edit.text())
        if files and pattern:
            info["rows"]["Output"] = _escape(_short_home(_fits(self._outputs(files[0])[0][1])))
        processes = self.processes()
        info["rows"]["Speed"] = "one at a time" if processes == 1 else f"{processes} at once"
        if not files and not self.dir_edit.text().strip():
            info["problem"] = "Choose the folder the images are in"
        elif not files:
            info["problem"] = "No image in the folder matches"
        elif not self.out_dir_edit.text().strip():
            info["problem"] = "Choose a folder to save to"
        elif not pattern:
            info["problem"] = "Give the output names a pattern"
        return info

    def convert_files(self):
        self.run()

    def run(self):
        if not self.ready():
            return
        files = self._get_matching_files()
        target = self.target_mode()
        self.start_log()
        tasks = []
        for i, input_file in enumerate(files):
            for stokes, path in self._outputs(input_file):
                folder = os.path.dirname(path)
                try:
                    os.makedirs(folder, exist_ok=True)
                except OSError as e:
                    self.log(f"Cannot create {folder}: {e}", "failed")
                    return
                tasks.append((input_file, path, stokes, f"{i}_{stokes}", target))
        self._stokes_per_file = len(self.stokes_list())
        self.log(f"{len(files)} images to {SYSTEMS[self.chosen(self.system_buttons)][0]}")
        self.start_batch(
            process_single_file_coordsys,
            tasks,
            processes=self.processes(),
            output_folder=os.path.dirname(tasks[0][1]),
        )

    def _task_name(self, input_file, stokes):
        name = os.path.basename(input_file)
        return f"{name}, Stokes {stokes}" if self._stokes_per_file > 1 else name

    def batch_result(self, task, result):
        name = self._task_name(task[0], task[2])
        if result["success"]:
            if result.get("skipped"):
                self.log(f"{name}: already in these coordinates, copied", "copied")
            else:
                self.log(f"{name} → {os.path.basename(result['output_path'])}", "✓")
            return True
        self.log(f"{name}: {result['error'] or 'unknown error'}", "failed")
        return False

    def batch_error(self, task, message):
        self.log(f"{self._task_name(task[0], task[2])}: {message}", "failed")

    def batch_finished(self, aborted):
        if not aborted:
            self.log(f"Converted {self._done} of {self._done + self._failed}")


def _fits(path):
    return path if os.path.splitext(path)[1] else path + ".fits"
