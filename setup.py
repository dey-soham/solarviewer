from setuptools import setup, find_packages
from setuptools.command.install import install
from setuptools.command.develop import develop
import subprocess
import sys


# class PostInstallCommand(install):
#     """Post-installation: fix opencv-python conflict."""
#     def run(self):
#         install.run(self)
#         self._fix_opencv()
#
#     def _fix_opencv(self):
#         """Uninstall opencv-python and ensure opencv-python-headless is installed."""
#         print("\n=== Fixing OpenCV installation ===")
#         # Uninstall opencv-python (may not exist, that's OK)
#         subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "opencv-python"],
#                       capture_output=True)
#         # Uninstall opencv-python-headless (to ensure clean install)
#         subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "opencv-python-headless"],
#                       capture_output=True)
#         # Install opencv-python-headless
#         subprocess.run([sys.executable, "-m", "pip", "install", "opencv-python-headless"],
#                       check=True)
#         print("=== OpenCV headless installed successfully ===\n")
#
#
# class PostDevelopCommand(develop):
#     """Post-develop: fix opencv-python conflict for editable installs."""
#     def run(self):
#         develop.run(self)
#         PostInstallCommand._fix_opencv(self)


# Read the content of README.md
with open("README.md") as f:
    long_description = f.read()

setup(
    name="solarviewer",
    version="1.1.0",
    packages=find_packages(),
    # cmdclass={
    #     'install': PostInstallCommand,
    #     'develop': PostDevelopCommand,
    # },
    include_package_data=True,
    package_data={
        "solar_radio_image_viewer": ["assets/*.png", "assets/*.fits"],
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
        "napari>=0.4.16",
        "napari-console>=0.0.8",
        "napari-svg>=0.1.6",
        "vispy>=0.11.0",
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
    ],
    entry_points={
        "console_scripts": [
            "solarviewer=solar_radio_image_viewer.main:main",
            "sv=solar_radio_image_viewer.main:main",
            # "heliosv=solar_radio_image_viewer.helioprojective_viewer:main",
            # LOFAR/SIMPL tools
            "viewcaltable=solar_radio_image_viewer.from_simpl.caltable_visualizer:main",
            "viewds=solar_radio_image_viewer.from_simpl.view_dynamic_spectra_GUI:main",
            "viewlogs=solar_radio_image_viewer.from_simpl.pipeline_logger_gui:main",
            "viewsolaractivity=solar_radio_image_viewer.noaa_events.noaa_events_gui:main",
            "heliobrowser=solar_radio_image_viewer.helioviewer_browser:main",
        ],
    },
    python_requires=">=3.7",
    description="SolarViewer - A comprehensive tool for visualizing and analyzing solar radio images",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Soham Dey",
    author_email="sohamd943@gmail.com",
    url="https://github.com/dey-soham/solarviewer/",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Astronomy",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    project_urls={
        "Documentation": "https://github.com/dey-soham/solarviewer/wiki",
        "Source": "https://github.com/dey-soham/solarviewer/",
        "Tracker": "https://github.com/dey-soham/solarviewer/issues",
    },
)
