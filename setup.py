from setuptools import setup, find_packages

setup(
    name="Solar-Image-Viewer",
    version="1.0",
    packages=find_packages(),
    install_requires=[
        "PyQt5",
        "matplotlib",
        "numpy",
        "astropy",
        "scipy",
        "casatools",
        "casatasks",
    ],
    entry_points={
        "console_scripts": [
            "solarviewer=solar_radio_image_viewer.main:main",
            "sv=solar_radio_image_viewer.main:main",
        ],
    },
)

