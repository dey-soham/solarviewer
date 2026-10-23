"""
One dialog to open either kind of image: a FITS file, or a CASA image (a
folder). A dynamic spectrum is a FITS file too; the window sends it to the
Dynamic Spectrum Viewer.
"""

from PyQt5.QtWidgets import QDialog, QFileDialog, QListView, QTreeView

from ..processing.fits_io import is_casa_image

FITS_PATTERNS = "*.fits *.fts *.fit *.FITS *.FTS *.FIT"
IMAGE_FILTER = f"Images and dynamic spectra ({FITS_PATTERNS});;All files (*)"
SPECTRUM_FILTER = f"Dynamic spectra ({FITS_PATTERNS});;All files (*)"


class _ImageDialog(QFileDialog):
    """A file dialog that takes a CASA image folder as the image, where a
    plain file dialog would go into it."""

    def __init__(self, parent, caption, directory):
        super().__init__(parent, caption, directory, IMAGE_FILTER)
        self.setFileMode(QFileDialog.ExistingFile)
        # Qt's own dialog: a native one cannot be told what to do with folders
        self.setOption(QFileDialog.DontUseNativeDialog, True)
        self.casa_image = ""
        for view in (
            self.findChild(QListView, "listView"),
            self.findChild(QTreeView, "treeView"),
        ):
            if view is not None:
                view.activated.connect(self._on_activated)

    def _on_activated(self, index):
        # A double-click; Qt's own handler, run first, has gone into the folder
        self._take_casa(self.directory().absolutePath())

    def _take_casa(self, path):
        """Close the dialog with ``path`` if it is a CASA image."""
        if not is_casa_image(path):
            return False
        self.casa_image = path
        QDialog.accept(self)
        return True

    def accept(self):
        # Open pressed, or Enter in the file name box, with a folder chosen
        files = self.selectedFiles()
        if files and self._take_casa(files[0]):
            return
        super().accept()


def choose_image(parent, caption="Open Image or Dynamic Spectrum", directory=""):
    """A FITS file or CASA image chosen by the user, or "" if cancelled.

    A FITS file is chosen as in any file dialog; a CASA image by opening its
    folder (double-click it, or select it and press Open).
    """
    dialog = _ImageDialog(parent, caption, directory)
    if dialog.exec_() != QDialog.Accepted:
        return ""
    if dialog.casa_image:
        return dialog.casa_image
    files = dialog.selectedFiles()
    return files[0] if files else ""
