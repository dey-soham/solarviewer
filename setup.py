from setuptools import setup, find_packages
import subprocess
import sys

# Read the content of README.md
with open("README.md") as f:
    long_description = f.read()


def get_version():
    with open("solar_radio_image_viewer/version.py") as f:
        for line in f:
            if line.startswith("__version__"):
                return line.split("=")[1].strip().strip('"').strip("'")
    raise RuntimeError("Unable to find version string.")


setup(
    name="solarviewer",
    version=get_version(),
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "solar_radio_image_viewer.assets": ["*.png", "*.fits", "*.ttf"],
    },
    install_requires=[
        "setuptools<81",
        "PyQt5>=5.15.0",
        "matplotlib>=3.5.0",
        "numpy>=1.20.0",
        "astropy>=5.0.0",
        "scipy>=1.7.0",
        "drms",
        "casatools>=6.4.0",
        "casatasks>=6.4.0",
        "sunpy[image,map,net,timeseries,visualization]>=5.0.0",
        "pillow",
        "python-casacore",
        "seaborn",
        "opencv-python-headless",
        "dask>=2022.1.0",
        "zarr>=2.11.0",
        "pyqt5-sip>=12.9.0",
        "qtpy>=2.0.0",
        "imageio>=2.16.0",
        "tifffile>=2022.2.2",
        "aiapy>=0.1.0",
        "imageio[ffmpeg]",
        "paramiko>=3.0.0",
    ],
    entry_points={
        "console_scripts": [
            "solarviewer=solar_radio_image_viewer.main:main",
            "sv=solar_radio_image_viewer.main:main",
            "viewcaltable=solar_radio_image_viewer.from_simpl.caltable_visualizer:main",
            "viewds=solar_radio_image_viewer.from_simpl.view_dynamic_spectra_GUI:main",
            "viewlogs=solar_radio_image_viewer.from_simpl.pipeline_logger_gui:main",
            "viewsolaractivity=solar_radio_image_viewer.noaa_events.noaa_events_gui:main",
            "heliobrowser=solar_radio_image_viewer.helioviewer_browser:main",
        ],
    },
    python_requires=">=3.10",
    description="SolarViewer - A comprehensive tool for visualizing and analyzing solar radio images",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Soham Dey",
    author_email="sohamd943@gmail.com",
    url="https://github.com/dey-soham/solarviewer/",
    license="MIT",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Astronomy",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
    project_urls={
        "Documentation": "https://github.com/dey-soham/solarviewer/wiki",
        "Source": "https://github.com/dey-soham/solarviewer/",
        "Tracker": "https://github.com/dey-soham/solarviewer/issues",
    },
)
