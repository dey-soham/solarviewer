"""from setuptools import setup, find_packages

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
)"""

from setuptools import setup, find_packages

setup(
    name="Solar-Image-Viewer",
    version="1.0",
    packages=find_packages(),
    include_package_data=True,  # Enables inclusion of non-code files as specified in MANIFEST.in
    package_data={
        # This tells setuptools to include all .png files in the assets folder inside the package
        "solar_radio_image_viewer": ["assets/*.png"],
    },
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
