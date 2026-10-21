"""
Plot appearance shared by the main window and videos: the default styles of
the solar disk, beam and grid overlays, and the plot customization settings
(labels, fonts, colours, ticks, borders) with the code applying them.

No Qt here, so video frames rendered in worker processes look like the
main window's plot.
"""

import copy

DEFAULT_SOLAR_DISK_STYLE = {
    "color": "white",
    "linestyle": "--",
    "linewidth": 1.8,
    "alpha": 0.6,
    "show_center": False,
}

DEFAULT_BEAM_STYLE = {
    "edgecolor": "black",
    "facecolor": "white",
    "linewidth": 1.5,
    "alpha": 0.4,
}

DEFAULT_GRID_STYLE = {
    "color": "white",
    "linestyle": "--",
    "alpha": 0.5,
}

DEFAULT_PLOT_SETTINGS = {
    "xlabel": "",
    "ylabel": "",
    "title": "",
    "colorbar_label": "",
    "axis_label_fontsize": 12,
    "axis_tick_fontsize": 10,
    "title_fontsize": 12,
    "colorbar_label_fontsize": 10,
    "colorbar_tick_fontsize": 10,
    "plot_bg_color": "auto",
    "figure_bg_color": "auto",
    "text_color": "auto",
    "tick_color": "auto",
    "border_color": "auto",
    "border_width": 1.0,
    "tick_direction": "out",
    "tick_length": 4,
    "tick_width": 1.0,
    # Padding settings
    "pad_left": 0.135,
    "pad_right": 1.0,
    "pad_top": 0.95,
    "pad_bottom": 0.08,
    "pad_wspace": 0.2,
    "pad_hspace": 0.2,
    "use_tight_layout": False,
}


def default_style(defaults):
    """A fresh copy of one of the DEFAULT_* dicts."""
    return copy.deepcopy(defaults)


def apply_plot_settings(ax, cb, ps, figure, dark=False, bunit=None, on_error=None):
    """Apply plot customization settings ``ps`` (see DEFAULT_PLOT_SETTINGS)
    to ``ax`` and its colorbar ``cb`` (or None) in ``figure``.

    "auto" colours follow a dark or light theme (``dark``); ``bunit`` labels
    the colorbar unless ``ps`` gives a label. ``on_error(message)`` hears of
    settings that could not be applied.
    """
    theme_text = "#ffffff" if dark else "#000000"
    theme_tick = "#e0e0e0" if dark else "#333333"
    theme_border = "#ffffff" if dark else "#000000"

    # Get text color for labels and title
    text_color = ps.get("text_color", "auto")
    if text_color == "auto":
        text_color = theme_text  # Explicitly set based on theme

    # Get tick color for tick marks and tick labels (separate from text_color)
    tick_color = ps.get("tick_color", "auto")
    if tick_color == "auto":
        tick_color = theme_tick  # Explicitly set based on theme

    # Build label kwargs with color if specified
    label_kwargs = {"fontsize": ps.get("axis_label_fontsize", 12)}
    title_kwargs = {"fontsize": ps.get("title_fontsize", 12)}
    if text_color:
        label_kwargs["color"] = text_color
        title_kwargs["color"] = text_color

    # Apply custom labels and fontsizes
    # For WCS axes, we need to re-set the label to apply fontsize
    if ps.get("xlabel"):
        ax.set_xlabel(ps["xlabel"], **label_kwargs)
    else:
        # Get existing label and re-set with new fontsize
        ax.set_xlabel(ax.get_xlabel(), **label_kwargs)

    if ps.get("ylabel"):
        ax.set_ylabel(ps["ylabel"], **label_kwargs)
    else:
        ax.set_ylabel(ax.get_ylabel(), **label_kwargs)

    if ps.get("title"):
        ax.set_title(ps["title"], **title_kwargs)
    else:
        ax.set_title(ax.get_title(), **title_kwargs)

    # Apply tick settings with separate tick color (tick marks only)
    tick_params = {
        "axis": "both",
        "which": "major",
        "labelsize": ps.get("axis_tick_fontsize", 10),
        "direction": ps.get("tick_direction", "in"),
        "length": ps.get("tick_length", 4),
        "width": ps.get("tick_width", 1.0),
    }
    if tick_color:
        tick_params["color"] = tick_color  # Only tick marks, not labels
    if text_color:
        tick_params["labelcolor"] = text_color  # Tick labels use text color
    ax.tick_params(**tick_params)

    # For WCSAxes, also apply tick mark colors via coords (not tick labels)
    if hasattr(ax, "coords"):
        try:
            for coord in ax.coords:
                if tick_color:
                    coord.set_ticks(color=tick_color)
                if text_color:
                    coord.set_ticklabel(color=text_color)
        except Exception as e:
            if on_error:
                on_error(f"Error setting WCSAxes tick colors: {e}")

    if cb is not None:
        # Apply colorbar settings with separate tick color
        cb_tick_params = {
            "labelsize": ps.get("colorbar_tick_fontsize", 10),
            "direction": ps.get("tick_direction", "in"),
            "length": ps.get("tick_length", 4),
            "width": ps.get("tick_width", 1.0),
        }
        if tick_color:
            cb_tick_params["color"] = tick_color  # Only tick marks
        if text_color:
            cb_tick_params["labelcolor"] = text_color  # Tick labels use text color
        cb.ax.tick_params(**cb_tick_params)

        # Set colorbar label - use custom label or BUNIT from image
        colorbar_label = ps.get("colorbar_label", "") or bunit
        if colorbar_label:
            cb_label_kwargs = {"fontsize": ps.get("colorbar_label_fontsize", 10)}
            if text_color:
                cb_label_kwargs["color"] = text_color
            cb.set_label(colorbar_label, **cb_label_kwargs)

    # Apply text color to labels and title
    if text_color:
        ax.xaxis.label.set_color(text_color)
        ax.yaxis.label.set_color(text_color)
        ax.title.set_color(text_color)

    # Apply background colors
    plot_bg = ps.get("plot_bg_color", "auto")
    figure_bg = ps.get("figure_bg_color", "auto")

    # Resolve defaults from current theme (stored in rcParams)
    from matplotlib import rcParams

    if plot_bg == "auto":
        plot_bg = rcParams.get("axes.facecolor", "white")
    if figure_bg == "auto":
        figure_bg = rcParams.get("figure.facecolor", "white")

    if plot_bg:
        try:
            ax.set_facecolor(plot_bg)
        except ValueError:
            pass  # Invalid color, ignore

    if figure_bg:
        try:
            figure.patch.set_facecolor(figure_bg)
            figure.set_facecolor(figure_bg)
        except ValueError:
            pass  # Invalid color, ignore

    # Apply border (spine) color and width
    border_color = ps.get("border_color", "auto")
    border_width = ps.get("border_width", 1.0)

    if border_color == "auto":
        border_color = theme_border

    cb_spines = list(cb.ax.spines.values()) if cb is not None else []
    if border_color:
        for spine in ax.spines.values():
            spine.set_color(border_color)
        # For WCSAxes, also set frame color
        if hasattr(ax, "coords") and hasattr(ax.coords, "frame"):
            try:
                ax.coords.frame.set_color(border_color)
            except Exception:
                pass
        # Apply to colorbar borders
        for spine in cb_spines:
            spine.set_color(border_color)

    # Apply border width to main axes
    for spine in ax.spines.values():
        spine.set_linewidth(border_width)
    # For WCSAxes frame width
    if hasattr(ax, "coords") and hasattr(ax.coords, "frame"):
        try:
            ax.coords.frame.set_linewidth(border_width)
        except Exception:
            pass
    # Apply border width to colorbar
    for spine in cb_spines:
        spine.set_linewidth(border_width)
