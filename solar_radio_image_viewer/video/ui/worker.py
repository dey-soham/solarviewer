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

    def __init__(self, files, output_file, options):
        super().__init__()
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

        try:
            result = create_video(
                self.files,
                self.output_file,
                self.options,
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

    def cancel(self):
        self.is_cancelled = True

