"""
Making the video in a background thread.
"""

from PyQt5.QtCore import QThread, pyqtSignal


# Worker thread for video creation
class VideoWorker(QThread):
    """Makes the video in the background; cancel() stops it after the frame
    being written (no file is left behind)."""

    video_created = pyqtSignal(str)  # output file
    error = pyqtSignal(str)
    cancelled = pyqtSignal()
    progress = pyqtSignal(int, int)  # frames written, total frames
    status = pyqtSignal(str)  # what is being prepared

    def __init__(self, files, output_file, options, remote=None):
        super().__init__()
        # Images on a server (RemoteFiles): fetched first, then the video is
        # made here from the local copies
        self.remote = remote
        self.files = files
        self.output_file = output_file
        self.options = options
        self.is_cancelled = False
        # What could not be drawn: message -> number of frames
        self.problems = {}

    def run(self):
        from ..creator import create_video

        def on_progress(done, total):
            self.progress.emit(done, total)
            return not self.is_cancelled

        files, options = self.files, self.options
        if self.remote is not None:
            from ...remote.remote_files import Cancelled

            try:
                files, options = self._fetch(files, options)
            except Cancelled:
                self.cancelled.emit()
                return
            except Exception as e:
                self.error.emit(f"Could not fetch the images from {self.remote.label}: {e}")
                return
        try:
            result = create_video(
                files,
                self.output_file,
                options,
                progress_callback=on_progress,
                problems=self.problems,
                status_callback=self.status.emit,
            )
        except Exception as e:
            if self.is_cancelled:
                self.cancelled.emit()
            else:
                self.error.emit(str(e))
            return
        if result is None or self.is_cancelled:
            self.cancelled.emit()
        else:
            self.video_created.emit(result)

    def _fetch(self, files, options):
        """Fetch every frame's images from the server (unless cached);
        (files, options) with the local copies."""
        from ..remote_video import frame_paths, localize, remote_times

        remote = self.remote

        def report(done, total, fetched, count):
            self.status.emit(
                f"Fetching images from {remote.label}: {fetched} of {count} "
                f"({done / 1e6:.0f} of {total / 1e6:.0f} MB)"
            )
            self.progress.emit(0, len(files))  # still preparing

        self.status.emit(f"Checking the images on {remote.label}…")
        local = remote.fetch(
            frame_paths(files, options, range(len(files))),
            progress=report,
            cancelled=lambda: self.is_cancelled,
        )
        times = remote_times(remote, files)
        files, options = localize(files, options, lambda path: local.get(path, path))
        return files, dict(options, frame_times=times)

    def cancel(self):
        self.is_cancelled = True

