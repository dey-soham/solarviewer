"""
Batch format conversion: images between CASA image folders and FITS files.
"""

import os

from ..ui.batch import BatchWindow, _escape, _short_home, output_name

FORMATS = (("FITS", "fits", "converted_fits", "*.fits"), ("CASA image", "image", "converted_casa", "*.image"))


class FormatConversionDialog(BatchWindow):
    """Converts a folder of images to FITS or to CASA images."""

    VERB = "Convert"
    RUNNING = "Converting"
    NOUN = ("image", "images")
    SUMMARY_ROWS = ("From", "To", "Output", "Speed")

    def __init__(self, parent=None, current_file=None):
        super().__init__(parent, "Convert Image Format")
        self.current_file = current_file
        folder = os.path.dirname(current_file) if current_file else ""
        self.input_block(folder, "*.image")
        layout, _ = self.block("Convert to")
        row, self.format_buttons, _ = self.choice_row(
            "Format",
            [f[0] for f in FORMATS],
            ("Standard FITS files (casatasks exportfits)", "CASA image folders (casatasks importfits)"),
            then=self._format_changed,
        )
        layout.addLayout(row)
        self.output_block()
        self.parallel_block()
        self.footer_note.setText("Images already in the format are copied")
        self._format_changed()
        self.finish_form()

    def to_fits(self):
        return self.chosen(self.format_buttons) == 0

    def _format_changed(self):
        """Suggest the input pattern, the output folder and names that go
        with the format, leaving ones the user gave alone."""
        _, _, sub, pattern = FORMATS[self.chosen(self.format_buttons)]
        source_pattern = "*.image" if self.to_fits() else "*.fits"
        if self.pattern_edit.text() in ("", "*.image", "*.fits"):
            self.pattern_edit.setText(source_pattern)
        folder = self.out_dir_edit.text().strip().rstrip("/\\")
        source = self.dir_edit.text().strip()
        if source and (not folder or os.path.basename(folder) in ("converted_fits", "converted_casa")):
            self.out_dir_edit.setText(os.path.join(source, sub))
        if self.output_pattern_edit.text() in ("", "*.image", "*.fits"):
            self.output_pattern_edit.setText(pattern)

    def input_folder_chosen(self, folder):
        self._format_changed()

    def summary(self):
        files = self.matching_files()
        pattern = self.output_pattern_edit.text().strip()
        name = FORMATS[self.chosen(self.format_buttons)][0]
        n = len(files)
        info = {
            "count": (n, "image" if n == 1 else "images"),
            "sub": f"as {'FITS files' if self.to_fits() else 'CASA images'}",
            "files": files,
            "rows": {"To": _escape(name)},
            "verb": f"Convert {n} image{'s' if n != 1 else ''}" if files else "Convert",
        }
        self.files_count_label.setText(f"{n} found" if self.dir_edit.text() else "")
        if self.dir_edit.text():
            info["rows"]["From"] = _escape(_short_home(self.dir_edit.text())) + " · " + _escape(self.pattern_edit.text())
        if files and pattern:
            out = os.path.join(self.output_dir_for(files[0]), output_name(pattern, files[0]))
            info["rows"]["Output"] = _escape(_short_home(out))
        processes = self.processes()
        info["rows"]["Speed"] = "one at a time" if processes == 1 else f"{processes} at once"
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
        to_fits = self.to_fits()
        pattern = self.output_pattern_edit.text().strip()
        self.start_log()
        tasks = []
        for f in files:
            folder = self.output_dir_for(f)
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                self.log(f"Cannot create {folder}: {e}", "failed")
                return
            tasks.append((f, os.path.join(folder, output_name(pattern, f)), to_fits))
        self.log(f"{len(files)} images to {'FITS' if to_fits else 'CASA images'}")
        self.start_batch(
            _format_conversion_worker,
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


def _format_conversion_worker(args):
    """Worker function for format conversion batch processing via isolated casatasks subprocesses."""
    infile, outfile, is_target_fits = args
    import os
    import shutil

    from ..processing.casa_tasks import CasaTaskError, run_casa_task

    try:
        infile_abs = os.path.abspath(infile)
        outfile_abs = os.path.abspath(outfile)
        
        # Check source format simply by looking at whether it's a file or directory
        # CASA images are directories (with table structure inside)
        # FITS files are always individual files.
        is_source_fits = os.path.isfile(infile_abs)
        is_source_casa = os.path.isdir(infile_abs)
        
        if is_target_fits and is_source_fits:
            shutil.copy2(infile_abs, outfile_abs)
            return True, infile, f"Skip (already FITS) -> Copied {os.path.basename(infile)}"
            
        if not is_target_fits and is_source_casa:
            if os.path.exists(outfile_abs):
                shutil.rmtree(outfile_abs)
            shutil.copytree(infile_abs, outfile_abs)
            return True, infile, f"Skip (already CASA) -> Copied {os.path.basename(infile)}"

        # In a separate process
        if is_target_fits:
            # We are exporting CASA -> FITS
            if os.path.exists(outfile_abs):
                os.remove(outfile_abs)
            task = dict(task="exportfits", imagename=infile_abs, fitsimage=outfile_abs)
        else:
            # We are importing FITS -> CASA
            if os.path.exists(outfile_abs):
                shutil.rmtree(outfile_abs)
            task = dict(task="importfits", fitsimage=infile_abs, imagename=outfile_abs)
        try:
            run_casa_task(overwrite=True, **task)
        except CasaTaskError as e:
            return False, infile, f"Error converting {os.path.basename(infile)}: {e}"

        return True, infile, f"Converted {os.path.basename(infile)} -> {'FITS' if is_target_fits else 'CASA'}"
        
    except Exception as e:
        return False, infile, f"Failed {os.path.basename(infile)}: {str(e)}"


