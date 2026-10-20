"""
Run CASA's ``immath`` in a separate process (avoids casatasks memory issues).

Callers use :func:`run_immath_subprocess`; the subprocess runs this module:

    python -m solar_radio_image_viewer.processing.immath IMAGENAME OUTFILE MODE
"""

import os
import subprocess
import sys
import tempfile


def run_immath_subprocess(imagename, outfile, mode="lpoli"):
    """Run immath in a subprocess to avoid memory issues with casatasks."""
    imagename = os.path.abspath(imagename)
    outfile = os.path.abspath(outfile)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "solar_radio_image_viewer.processing.immath",
            imagename,
            outfile,
            mode,
        ],
        capture_output=True,
        text=True,
        cwd=os.getcwd() if os.access(os.getcwd(), os.W_OK) else tempfile.gettempdir(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"immath failed: {result.stderr}")
    return True


def main(argv=None):
    imagename, outfile, mode = sys.argv[1:] if argv is None else argv
    from casatasks import immath

    try:
        immath(imagename=imagename, outfile=outfile, mode=mode)
        print("SUCCESS")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
