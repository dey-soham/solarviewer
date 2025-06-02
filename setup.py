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

# Read the content of README.md
with open("README.md") as f:
    long_description = f.read()

setup(
    name="Solar-Image-Viewer",
    version="1.0",
    packages=find_packages(),
    include_package_data=True,  # Enables inclusion of non-code files as specified in MANIFEST.in
    package_data={
        # This tells setuptools to include all .png files in the assets folder inside the package
        "solar_radio_image_viewer": ["assets/*.png"],
        "solar_radio_image_viewer": ["assets/*.fits"],
    },
    install_requires=[
        "PyQt5>=5.15.0",
        "matplotlib>=3.5.0",
        "numpy>=1.20.0",
        "astropy>=5.0.0",
        "scipy>=1.7.0",
        "casatools>=6.4.0",
        "casatasks>=6.4.0",
        "napari>=0.4.16",
        "napari-console>=0.0.8",
        "napari-svg>=0.1.6",
        "vispy>=0.11.0",
        "sunpy>=5.0.0",
    ],
    extras_require={
        "full": [
            "dask>=2022.1.0",
            "zarr>=2.11.0",
            "pyqt5-sip>=12.9.0",
            "qtpy>=2.0.0",
            "imageio>=2.16.0",
            "tifffile>=2022.2.2",
        ]
    },
    entry_points={
        "console_scripts": [
            "solarviewer=solar_radio_image_viewer.main:main",
            "sv=solar_radio_image_viewer.main:main",
            "heliosv=solar_radio_image_viewer.helioprojective_viewer:main",
        ],
    },
    python_requires=">=3.7",
    description="A comprehensive tool for visualizing and analyzing solar radio images",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Soham Dey",
    author_email="sohamd943@gmail.com",
    url="https://github.com/dey-soham/solarviewer/",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Astronomy",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
    project_urls={
        "Documentation": "https://github.com/dey-soham/solarviewer/wiki",
        "Source": "https://github.com/dey-soham/solarviewer/",
        "Tracker": "https://github.com/dey-soham/solarviewer/issues",
    },
)
