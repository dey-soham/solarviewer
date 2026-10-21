"""
Videos of images on an SSH server, made on this computer.

The server needs only SSH: its files are fetched into the local cache
(remote.remote_files.RemoteFiles) and the video is made from the copies.
These helpers find which server files frames need and put the local
copies in their place; the rest of the video code sees local files only.
"""

import os


def frame_paths(files, options, indices):
    """The files frames ``indices`` need: image, difference image, and each
    enabled contour layer's file (fixed files: those not on this computer)."""
    needed = []
    difference = options.get("difference_files") or []
    for i in indices:
        if i < len(files):
            needed.append(files[i])
        if i < len(difference):
            needed.append(difference[i])
        for layer in options.get("contour_layers") or []:
            if not layer.get("enabled", True):
                continue
            frame_files = layer.get("frame_files") or []
            if layer.get("source") == "series" and i < len(frame_files):
                needed.append(frame_files[i])
            elif layer.get("source") == "external" and _on_server(layer.get("external_image")):
                needed.append(layer["external_image"])
    return list(dict.fromkeys(p for p in needed if p))


def _on_server(path):
    """A path that is not on this computer (a server path)."""
    return bool(path) and not os.path.exists(path)


def localize(files, options, local_of):
    """(files, options) with every server path replaced by
    ``local_of(path)``, its local copy (or where it will be)."""
    options = dict(options)
    if options.get("difference_files"):
        options["difference_files"] = [local_of(p) for p in options["difference_files"]]
    layers = []
    for layer in options.get("contour_layers") or []:
        layer = dict(layer)
        if layer.get("frame_files"):
            layer["frame_files"] = [local_of(p) for p in layer["frame_files"]]
        if layer.get("source") == "external" and _on_server(layer.get("external_image")):
            layer["external_image"] = local_of(layer["external_image"])
        layers.append(layer)
    if "contour_layers" in options:
        options["contour_layers"] = layers
    return [local_of(p) for p in files], options


def sample_indices(count):
    """Frames previewed before all are fetched: the first, middle and last."""
    if count <= 0:
        return []
    return sorted({0, count // 2, count - 1})


def remote_times(remote, paths):
    """Observation times (datetime or None) of server ``paths``, from their
    headers (read without fetching the images)."""
    from .creator import observation_time

    headers = remote.headers(paths)
    return [observation_time(headers.get(p), p) for p in paths]
