"""
Process pools for batch work started from the GUI.

On Linux, multiprocessing forks by default. Forking a process that is
running Qt (which has its own threads) can deadlock the workers, so on Linux
the pools use 'forkserver' instead: workers are forked from a small helper
process with no Qt in it. That is also Python 3.14's default on Linux.
Other platforms keep their default ('spawn' on macOS and Windows).
"""

import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor


def process_context(preload=()):
    """Multiprocessing context to use for worker pools.

    ``preload`` names modules the forkserver should import once up front, so
    each worker starts with them already loaded.
    """
    if sys.platform.startswith("linux"):
        context = multiprocessing.get_context("forkserver")
        if preload:
            context.set_forkserver_preload(list(preload))
        return context
    return multiprocessing.get_context()


def process_pool(processes, preload=()):
    """A ``multiprocessing.Pool`` using :func:`process_context`."""
    return process_context(preload).Pool(processes=processes)


def process_pool_executor(max_workers, preload=()):
    """A ``ProcessPoolExecutor`` using :func:`process_context`."""
    return ProcessPoolExecutor(
        max_workers=max_workers, mp_context=process_context(preload)
    )


def import_modules(names):
    """Import the modules ``names``: run in a new worker, so that the jobs
    sent to it later start without waiting for their imports."""
    import importlib

    for name in names:
        importlib.import_module(name)


def call_function(module, name, *args, **kwargs):
    """``module.name(*args, **kwargs)``. Sends a worker a job from a module
    the window has not imported (it imports it), sparing the window the wait."""
    import importlib

    return getattr(importlib.import_module(module), name)(*args, **kwargs)
