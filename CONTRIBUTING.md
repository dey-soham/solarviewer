# Contributing to SolarViewer

Thank you for helping. This guide covers how the code is organised and the
conventions it follows; the README describes what the application does.

## Setting up

```bash
git clone https://github.com/dey-soham/solarviewer.git
cd solarviewer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
solarviewer            # or: python -m solar_radio_image_viewer.main
```

Before committing, run `ruff check solar_radio_image_viewer`. The rules
(`[tool.ruff.lint]` in `pyproject.toml`) catch undefined names, unused
imports and variables, bare `except:` and mutable argument defaults.

## Where things are

The package is `solar_radio_image_viewer/`; the README's *Project Structure*
lists every folder. In short:

| Folder | Holds |
| --- | --- |
| `viewer.py` | The main window and the image tab, which combines the mixins below |
| `core/` | The tab's building blocks: rendering, canvas (pan and zoom), contours, file navigation, exports, remote mode |
| `tools/` | Interactive tools: ROI statistics, ruler, profiles, fitting, TB and HPC views, solar disk |
| `processing/` | Qt-free image work: reading pixels, metadata, Stokes products, RMS, CASA tasks |
| `dialogs/`, `ui/` | Dialogs, one module each; shared widgets, themes and helpers |
| `coordinates/`, `video/`, `remote/` | Helioprojective conversion and phase shift; Create Video; SSH/SFTP access |

The image tab is `SolarRadioImageTab` plus fourteen mixins (one per
feature). Each mixin's docstring lists, under *Host attributes used*, what
it expects the tab to provide; keep that list up to date when a mixin starts
using something new.

## Conventions

**Axes.** Image planes are indexed `[x, y]` (CASA's order) and drawn
transposed (`imshow(data.T)`). A region of interest and the RMS box are
`(x1, x2, y1, y2)` in those pixels. FITS data read with astropy is `[y, x]`:
transpose it when it joins the viewer's planes.

**Stokes planes** are found by their labels (`processing/stokes.py`), never
by position: an image may hold only I and V. Derived products (L, Lfrac,
Vfrac, ...) are computed once, in `stokes_product`.

**FITS or CASA** is decided by `is_fits()` and `is_casa_image()` in
`processing/fits_io.py`; don't test file extensions by hand.

**CASA tasks** (exportfits, importfits, ...) run in a separate process
through `processing/casa_tasks.run_casa_task`, with their arguments passed
as JSON. Never build Python source or shell commands from file names.

**Long work.** Everything that says the application is busy goes through
`ui/activity.py`; add no spinners, wait cursors or polling loops of your own.
Work that does not need the GUI thread leaves it. CPU and CASA work (reading
an image, converting one) runs in a worker process through
`ui/background.run_in_background()`, and work that only waits (a server, a
copy, a program) on a thread through `run_in_thread()`: the window keeps
drawing, and work that takes more than a second shows a dialog whose Cancel
stops it. Handle `Cancelled`. Process jobs are module-level functions without
Qt (from `processing/` or `coordinates/`). Work that must stay on the GUI
thread (drawing, building windows) goes in `ui/busy.busy("Drawing…", self)`,
which gives the wait cursor and a pill saying what is happening; a small
process of its own (`ui/indicator.py`) keeps the pill moving while the GUI
thread is held up. For work in a QThread use `begin_activity(blocking=False)`
or `track_thread()`, and inside a window `Spinner` or `ActivityLabel`.
Batch work over many files runs in worker processes with
`ui/batch.BatchRunner` (`BatchDialogMixin` for dialogs), so the window stays
responsive and Abort works at once.

**Messages.** Log with `logging.getLogger(__name__)` rather than `print`;
records reach the log window and `~/.solarviewer/logs/solarviewer.log`.
Tell the user about failures with `ui/errors.show_error()`: what went wrong
and what to try in the message, the exception and traceback as details.
When a fallback changes a result (a guessed position, a skipped step), say
so in the status bar instead of failing silently.

**Exit.** At exit PyQt destroys the windows Python still owns, and Python
code that runs during that teardown can crash it (`ui/qt_exit.py` says why).
An event filter installed on the whole application must come off first:
pass its removal to `ui/qt_exit.before_qt_exit()`.

**Look.** Colours, type sizes and spacing come from `ui/styles.py`: style a
widget by its class or a role (`setObjectName`), not with a stylesheet of its
own or a literal colour. What cannot be a role (a painter, a plot, rich text)
reads the palette through `themed(widget, style)`, so it follows a theme
change; plots in a window use `ui/plot_theme.py`. Windows of their own
process start with `create_app(theme)` like the main one. Dialogs end in
`ui/widgets.button_bar`, the main action last.

**Settings and files.** Recently opened images live in `QSettings`
(`ui/recent_files.py`); temporary files go in the system temporary folder
and are removed when their tab closes (`SolarRadioImageTab.cleanup`).

## Pull requests

Keep each pull request to one change, describe what it fixes or adds and how
you checked it, and include before/after screenshots for visible changes.
