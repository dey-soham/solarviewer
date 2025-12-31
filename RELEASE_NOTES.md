# SolarViewer Release Notes

## Usability Enhancements
*   **Non-Modal Interface**: Converted key dialogs (Profiles, Histograms, Annotations, Settings) to non-modal windows, enabling simultaneous interaction with the main viewer and analysis tools.
*   **Settings Management**: Standardized application configuration storage under "SolarViewer".
*   **Shortcut Resolution**: Resolved conflict for `Ctrl+T`; now dedicated exclusively to Text Annotations.

## Analysis & Measurements
*   **WCS Integration**: Analysis tools (Fitting, Ruler, Profiles) now default to World Coordinate System units (arcsec/degrees) when WCS data is available, with pixel fallback.
*   **Flux Profiling**: Implemented automatic Full Width at Half Maximum (FWHM) calculation with smart validation and visual indicators for suitable peaks.
*   **Enhanced Reporting**: Standardized terminal output for Gaussian/Ring fits and Ruler measurements to provide formatted, professional data presentation including RA/Dec coordinates.

## Visualization Tools
*   **Advanced Annotations**: Added comprehensive styling options for Text (color, font, background) and Arrow (color, width, head size) annotations.
