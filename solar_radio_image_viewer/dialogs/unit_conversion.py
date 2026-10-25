"""
Batch unit conversion: images between flux density (Jy/beam) and
brightness temperature (K).
"""

import os

from ..ui.batch import BatchWindow, _escape, _short_home, output_name

UNITS = (("Brightness temperature", "K"), ("Flux density", "Jy/beam"))


class UnitConversionDialog(BatchWindow):
    """Converts a folder of FITS or CASA images to K or to Jy/beam."""

    VERB = "Convert"
    RUNNING = "Converting"
    NOUN = ("image", "images")
    SUMMARY_ROWS = ("From", "To", "Output", "Speed")

    def __init__(self, parent=None):
        super().__init__(parent, "Convert Units")
        self.input_block()
        layout, _ = self.block("Convert to")
        row, self.unit_buttons, self.unit_note = self.choice_row(
            "Unit",
            [f"{name} · {unit}" for name, unit in UNITS],
            ("Kelvin, from the beam and frequency", "Jansky per beam"),
            then=self._unit_changed,
        )
        layout.addLayout(row)
        self.output_block(pattern="tb_*.fits")
        self.parallel_block()
        self.footer_note.setText("Images already in the unit are copied")
        self.finish_form()

    def to_kelvin(self):
        return self.chosen(self.unit_buttons) == 0

    def _unit_changed(self):
        """Keep the suggested output folder and names in step with the unit."""
        sub, pattern = ("temperature", "tb_*.fits") if self.to_kelvin() else ("flux", "flux_*.fits")
        folder = self.out_dir_edit.text().strip().rstrip("/\\")
        source = self.dir_edit.text().strip()
        if source and (not folder or os.path.basename(folder) in ("temperature", "flux")):
            self.out_dir_edit.setText(os.path.join(source, sub))
        if self.output_pattern_edit.text() in ("", "tb_*.fits", "flux_*.fits"):
            self.output_pattern_edit.setText(pattern)

    def input_folder_chosen(self, folder):
        self._unit_changed()

    def summary(self):
        files = self.matching_files()
        pattern = self.output_pattern_edit.text().strip()
        name, unit = UNITS[self.chosen(self.unit_buttons)]
        info = {
            "count": (len(files), "image" if len(files) == 1 else "images"),
            "sub": f"in {name.lower()} ({unit}), as FITS",
            "files": files,
            "rows": {"To": f"{_escape(name)} · {unit}"},
            "verb": f"Convert {len(files)} image{'s' if len(files) != 1 else ''}" if files else "Convert",
        }
        self.files_count_label.setText(f"{len(files)} found" if self.dir_edit.text() else "")
        if self.dir_edit.text():
            info["rows"]["From"] = _escape(_short_home(self.dir_edit.text())) + " · " + _escape(self.pattern_edit.text())
        if files and pattern:
            out = os.path.join(self.output_dir_for(files[0]), _fits_name(pattern, files[0]))
            info["rows"]["Output"] = _escape(_short_home(out))
        n = self.processes()
        info["rows"]["Speed"] = "one at a time" if n == 1 else f"{n} at once"
        if not self.dir_edit.text().strip():
            info["problem"] = "Choose the folder the images are in"
        elif not files:
            info["problem"] = "No image in the folder matches"
        elif not self.out_dir_edit.text().strip():
            info["problem"] = "Choose a folder to save to"
        elif not pattern:
            info["problem"] = "Give the output names a pattern"
        return info

    def run(self):
        if not self.ready():
            return
        files = self.matching_files()
        to_kelvin = self.to_kelvin()
        pattern = self.output_pattern_edit.text().strip()
        tasks = []
        self.start_log()
        for f in files:
            folder = self.output_dir_for(f)
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                self.log(f"Cannot create {folder}: {e}", "failed")
                return
            tasks.append((f, os.path.join(folder, _fits_name(pattern, f)), to_kelvin))
        self.log(f"{len(files)} images to {'K' if to_kelvin else 'Jy/beam'}")
        self.start_batch(
            _unit_conversion_worker,
            tasks,
            processes=self.processes(),
            output_folder=self.output_dir_for(files[0]),
        )

    def batch_result(self, task, value):
        success, _, message = value
        self.log(message, "" if success else "failed")
        return success

    def batch_finished(self, aborted):
        if not aborted:
            self.log(f"Converted {self._done} of {self._done + self._failed}")


def _fits_name(pattern, path):
    """The output's name: FITS, so with .fits unless the pattern gives one."""
    name = output_name(pattern, path)
    return name if os.path.splitext(name)[1] else name + ".fits"


def _unit_conversion_worker(args):
    """Worker function for unit conversion batch processing."""
    infile, outfile, is_target_tb = args
    import os
    import shutil
    try:
        from ..processing.metadata import get_image_metadata
        from ..processing.maps import generate_tb_map, generate_flux_map
        
        # Read metadata to check current unit
        meta = get_image_metadata(infile)
        current_unit = ""
        if "image" in meta and "Units" in meta["image"]:
            current_unit = meta["image"]["Units"].strip().lower()
            
        if not current_unit:
            # Fallback if metadata fails
            return False, infile, f"Skip {os.path.basename(infile)}: Could not read units"

        is_current_tb = current_unit == "k"
        is_current_flux = "jy" in current_unit and "beam" in current_unit

        # Check if already in target unit
        if (is_target_tb and is_current_tb) or (not is_target_tb and is_current_flux):
            # Just copy the file
            if os.path.isdir(infile):
                if os.path.exists(outfile):
                    shutil.rmtree(outfile)
                shutil.copytree(infile, outfile)
            else:
                shutil.copy2(infile, outfile)
            return True, infile, f"Copy {os.path.basename(infile)}: Already in desired unit"
            
        elif is_target_tb and is_current_flux:
            # Convert Jy/beam to K
            tb_data, result = generate_tb_map(infile, outfile=outfile)
            if tb_data is None:
                return False, infile, f"Error {os.path.basename(infile)}: {result}"
            return True, infile, f"Convert {os.path.basename(infile)} -> K"
            
        elif not is_target_tb and is_current_tb:
            # Convert K to Jy/beam
            flux_data, result = generate_flux_map(infile, outfile=outfile)
            if flux_data is None:
                return False, infile, f"Error {os.path.basename(infile)}: {result}"
            return True, infile, f"Convert {os.path.basename(infile)} -> Jy/beam"
            
        else:
            return False, infile, f"Skip {os.path.basename(infile)}: Unsupported unit '{current_unit}'"
            
    except Exception as e:
        return False, infile, f"Failed {os.path.basename(infile)}: {str(e)}"

