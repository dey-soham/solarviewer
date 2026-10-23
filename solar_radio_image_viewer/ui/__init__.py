"""
Shared UI building blocks for SolarViewer.

- ``styles``: themes, palettes, design tokens, the stylesheet (``theme_manager``)
- ``app``: the one QApplication every window runs in (``create_app``)
- ``geometry``: window sizes that fit the screen (``fit_to_screen``)
- ``widgets``: a dialog's button bar and titled sections
- ``icons``: package resource paths and theme-aware icons
- ``segmented_control``: pill-style segmented toggle
- ``document_tabs``: the main window's image tabs, in the menu bar
- ``histogram``: the intensity histogram with the range draggable on it
- ``stokes_picker``: Stokes I/Q/U/V as buttons, the rest in a dropdown
- ``cursor_readout``: the readout pinned at the inspector's foot
- ``empty_canvas``: the canvas before an image, with the recent files
- ``item_delegates``: combo box item delegates
- ``searchable_combobox``: colormap selector with search
- ``free_dialogs``: modal dialogs that move apart from their window
- ``background``: reads and conversions in a worker process, with Cancel
- ``qt_exit``: takes the windows down at exit before PyQt does
"""
