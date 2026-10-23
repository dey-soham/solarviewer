"""
Shared UI building blocks for SolarViewer.

- ``styles``: themes, palettes, design tokens, the stylesheet (``theme_manager``)
- ``app``: the one QApplication every window runs in (``create_app``)
- ``geometry``: window sizes that fit the screen (``fit_to_screen``)
- ``widgets``: a dialog's button bar and titled sections
- ``icons``: package resource paths and theme-aware icons
- ``segmented_control``: pill-style segmented toggle
- ``custom_tabs``: main-window tab bar / tab widget
- ``item_delegates``: combo box item delegates
- ``searchable_combobox``: colormap selector with search
- ``free_dialogs``: modal dialogs that move apart from their window
- ``background``: reads and conversions in a worker process, with Cancel
- ``qt_exit``: takes the windows down at exit before PyQt does
"""
