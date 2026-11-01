"""
Plot chrome in the theme's colours, for Matplotlib figures embedded in a
window: backgrounds, frames, ticks, labels, grids and legends. What a plot
shows (colormaps, data lines) is the plot's own business and stays as it is.

Matplotlib's rcParams only reach figures made after they are set, so a figure
that lives through a theme change calls ``follow_theme``.
"""

from .styles import theme_manager, themed

FONT_SIZE = 9  # of the text of a plot in a tool window


def style_axes(ax, palette=None, font_size=FONT_SIZE):
    """``ax``'s frame, ticks, labels, title, grid and legend in the theme."""
    p = palette or theme_manager.palette
    ax.set_facecolor(p["plot_bg"])
    ax.title.set_color(p["plot_text"])
    ax.title.set_fontsize(font_size + 1)
    for axis in (ax.xaxis, ax.yaxis):
        axis.label.set_color(p["text_secondary"])
        axis.label.set_fontsize(font_size)
    ax.tick_params(which="both", colors=p["text_dim"], labelsize=font_size - 1, width=0.6)
    for spine in ax.spines.values():
        spine.set_color(p["plot_grid"])
        spine.set_linewidth(0.8)
    for line in ax.get_xgridlines() + ax.get_ygridlines():
        line.set_color(p["plot_grid"])
    legend = ax.get_legend()
    if legend is not None:
        legend.get_frame().set_facecolor(p["plot_bg"])
        legend.get_frame().set_edgecolor(p["plot_grid"])
        for text in legend.get_texts():
            text.set_color(p["plot_text"])


def style_figure(figure, palette=None, font_size=FONT_SIZE):
    """``figure`` and all its axes (colorbars too) in the theme."""
    p = palette or theme_manager.palette
    figure.patch.set_facecolor(p["window"])
    for ax in figure.axes:
        style_axes(ax, p, font_size)
    if figure._suptitle is not None:
        figure._suptitle.set_color(p["plot_text"])
        figure._suptitle.set_fontsize(font_size + 1)


def follow_theme(widget, figure, canvas, font_size=FONT_SIZE):
    """Style ``figure`` now and on every theme change while ``widget`` exists.
    Call it again after drawing into new axes, or call ``style_figure``."""

    def restyle(palette):
        style_figure(figure, palette, font_size)
        canvas.draw_idle()

    themed(widget, restyle)


def figure_toolbar(canvas, parent=None):
    """Matplotlib's navigation toolbar at the application's icon size, its
    icons tinted for the theme and following it."""
    from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
    from PyQt5.QtCore import QSize

    toolbar = NavigationToolbar2QT(canvas, parent)
    toolbar.setIconSize(QSize(16, 16))

    def retint(_palette):
        # (Matplotlib tints its icons once, for the window's colours at the time)
        for _text, _tip, image, callback in toolbar.toolitems:
            action = toolbar._actions.get(callback)
            if action is not None:
                action.setIcon(toolbar._icon(image + ".png"))

    themed(toolbar, retint)
    return toolbar
