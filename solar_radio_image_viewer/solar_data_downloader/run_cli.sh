#!/bin/bash
source "/home/soham/solarviewer/venv/bin/activate"
python3 "/home/soham/solarviewer/solar_radio_image_viewer/solar_data_downloader/solar_data_downloader_cli.py"
read -p "Press Enter to close..."
