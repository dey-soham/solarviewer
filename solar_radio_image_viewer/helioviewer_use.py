"""
A Helioviewer frame in the viewer: the archive's JPEG 2000 image, written
as a helioprojective FITS file (``helioviewer_api.fits_for``), opened as a
new tab, beside the radio image, as a contour layer, or saved.

The functions take the browser's ``parent`` (the pane or the main window
it was opened from) and find the viewer from it; each fetches the FITS
behind the existing work dialog, says its outcome through ``say`` and
returns what it made, or None.
"""

import logging
import os
import shutil
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QFileDialog, QWidget

from . import helioviewer_api as hv
from .ui.errors import show_error
from .video.ui.progress import run_with_dialog

logger = logging.getLogger(__name__)

NOTE = "Helioviewer display values (8-bit, log-scaled), not DN"


def describe(frame, name):
    """``AIA 171 2024-05-14 16:49:57 UTC``: the frame as its tab or layer is named."""
    return f"{name} {frame.observed:%Y-%m-%d %H:%M:%S} UTC"


def source_note(frame, name):
    """What the opened pane is told of its image."""
    return f"{describe(frame, name)} · {NOTE} · Helioviewer.org"


# ---------------------------------------------------------------- finding the viewer


def main_window(widget):
    """The SolarViewer main window ``widget`` belongs to (the one with
    ``open_path`` and ``add_new_tab``), or None."""
    seen = widget
    while seen is not None:
        if hasattr(seen, "open_path") and hasattr(seen, "add_new_tab") and hasattr(seen, "tab_widget"):
            return seen
        seen = seen.parent() if isinstance(seen, QWidget) else None
    for top in QApplication.topLevelWidgets():
        if hasattr(top, "open_path") and hasattr(top, "add_new_tab") and hasattr(top, "tab_widget") and top.isVisible():
            return top
    return None


def pane_of(widget):
    """The image pane ``widget`` is or belongs to, else the main window's
    current one; None without a viewer."""
    seen = widget
    while seen is not None:
        if hasattr(seen, "open_image_path") and hasattr(seen, "contour_layers"):
            return seen
        seen = seen.parent() if isinstance(seen, QWidget) else None
    window = main_window(widget)
    if window is None:
        return None
    pane = window.tab_widget.currentWidget()
    return pane if hasattr(pane, "open_image_path") else None


# ---------------------------------------------------------------- the FITS file


def _widget(*candidates):
    """The first of ``candidates`` a dialog can have as its parent, else None."""
    return next((c for c in candidates if isinstance(c, QWidget)), None)


def fits_file(parent, frame, name, reduce=2, say=None, dialog_parent=None):
    """The FITS of ``frame``'s archive image, fetched and written behind a
    work dialog; its path, or None if that was cancelled or failed (said)."""
    what = f"{name} {frame.observed:%H:%M:%S} UTC"
    dialog_parent = _widget(dialog_parent, parent)
    outcome = run_with_dialog(
        dialog_parent, "Preparing the FITS file",
        f"Fetching the archive image of {what}…" if reduce else f"Fetching the whole archive image of {what}…",
        lambda progress, cancelled: str(hv.fits_for(frame, reduce)),
    )
    if outcome.cancelled and not outcome.result:
        if say:
            say("Cancelled")
        return None
    if outcome.error or not outcome.result:
        logger.warning("No FITS for %s (id %s): %r", what, frame.image_id, outcome.error)
        show_error(
            dialog_parent, "Could not get the archive image",
            f"Helioviewer did not give the image of {what}. Try again in a moment.", outcome.error,
        )
        return None
    return Path(outcome.result)


def _tell(pane, text):
    """Tell the pane's window what it shows: the status line, and the pane's
    note for anything that reads it."""
    try:
        pane.source_note = text
    except Exception:
        pass
    if hasattr(pane, "show_status_message"):
        try:
            pane.show_status_message(text)
        except Exception as e:
            logger.debug("No status message: %s", e)


# ---------------------------------------------------------------- the actions


def open_as_new_tab(parent, frame, name, reduce=2, say=None, dialog_parent=None):
    """The frame as a new tab of the viewer; the pane, or None."""
    window = main_window(parent)
    if window is None:
        if say:
            say("Open SolarViewer first: there is no window to open the image in")
        return None
    path = fits_file(parent, frame, name, reduce, say, dialog_parent)
    if path is None:
        return None
    pane = window.add_new_tab(describe(frame, name))
    loaded = window.open_path(str(path), tab=pane)
    if not loaded:
        if say:
            say(f"The viewer could not open {path.name}")
        return None
    _tell(pane, source_note(frame, name))
    if say:
        say(f"Opened {describe(frame, name)} in a new tab")
    return pane


def open_beside(parent, frame, name, reduce=2, say=None, dialog_parent=None):
    """The frame in a new pane of the radio image's page; the pane, or None."""
    pane = pane_of(parent)
    page = getattr(pane, "page", None) if pane is not None else None
    if page is None:
        return open_as_new_tab(parent, frame, name, reduce, say, dialog_parent)
    path = fits_file(parent, frame, name, reduce, say, dialog_parent)
    if path is None:
        return None
    before = list(page.panes)
    loaded = page.open_in_new_pane(str(path))
    if not loaded:
        if say:
            say("The page is full, or the image could not be opened" if len(before) >= getattr(page, "MAX_PANES", 6) else f"The viewer could not open {path.name}")
        return None
    new = next((p for p in page.panes if p not in before), page.active_pane)
    _tell(new, source_note(frame, name))
    if say:
        say(f"Opened {describe(frame, name)} beside the radio image")
    return new


def add_contour_layer(parent, frame, name, reduce=2, say=None, dialog_parent=None):
    """The frame's contours on the radio image, as a new external contour
    layer that is loaded and switched on; the layer, or None."""
    from .processing.contour_layers import MAX_CONTOUR_LAYERS, new_contour_layer

    pane = pane_of(parent)
    if pane is None or not getattr(pane, "imagename", ""):
        if say:
            say("Open a radio image in SolarViewer first: the contours go on it")
        return None
    if len(pane.contour_layers) >= MAX_CONTOUR_LAYERS:
        if say:
            say(f"The image already has {MAX_CONTOUR_LAYERS} contour layers")
        return None
    path = fits_file(parent, frame, name, reduce, say, dialog_parent)
    if path is None:
        return None
    layer = new_contour_layer(
        len(pane.contour_layers), source="external", external_image=str(path), label=describe(frame, name), enabled=True
    )
    pane.contour_layers.append(layer)
    pane.load_contour_data(layer)
    if layer.get("contour_data") is None:
        pane.contour_layers.remove(layer)
        if say:
            say(f"Could not read contours from {path.name}")
        return None
    switch = getattr(pane, "show_contours_checkbox", None)
    if switch is not None and not switch.isChecked():
        switch.setChecked(True)  # (draws the layers it has, this one loaded)
    elif hasattr(pane, "on_checkbox_changed"):
        pane.on_checkbox_changed()
    if say:
        say(f"Added {describe(frame, name)} as a contour layer")
    return layer


def save_fits(parent, frame, name, reduce=2, say=None, dialog_parent=None):
    """The frame's FITS copied where the user says; the path, or None."""
    stem = f"{name.replace(' ', '_')}_{frame.observed:%Y%m%d_%H%M%S}"
    dialog_parent = _widget(dialog_parent, parent)
    file_path, _ = QFileDialog.getSaveFileName(dialog_parent, "Save FITS", f"{stem}.fits", "FITS image (*.fits)")
    if not file_path:
        return None
    if not file_path.lower().endswith((".fits", ".fit", ".fts")):
        file_path += ".fits"
    path = fits_file(parent, frame, name, reduce, say, dialog_parent)
    if path is None:
        return None
    try:
        partial = file_path + ".part"
        shutil.copyfile(path, partial)
        os.replace(partial, file_path)
    except OSError as e:
        show_error(dialog_parent, "Could not save the FITS file", f"The file could not be written:\n{file_path}", e)
        return None
    if say:
        from .helioviewer_export import show_in_folder

        say(f"Saved {os.path.basename(file_path)}", ("Show in folder", lambda: show_in_folder(file_path)))
    return Path(file_path)
