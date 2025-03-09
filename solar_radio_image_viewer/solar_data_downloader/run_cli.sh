#!/bin/bash
source "/home/soham/solarviewerapp/solarviewer_env/bin/activate"
cd "/home/soham/solarviewer/solar_radio_image_viewer/solar_data_downloader"
python3 "/home/soham/solarviewer/solar_radio_image_viewer/solar_data_downloader/solar_data_downloader_cli.py"
read -p "Press Enter to close..."
