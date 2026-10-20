"""
Scientific computation for SolarViewer that does not depend on Qt.

Modules here must not import PyQt5 or matplotlib's Qt backend, so they can be
run in subprocesses, scripts and tests without a GUI.
"""
