import requests
from packaging import version
import sys


def check_for_updates(current_version_str):
    """
    Check PyPI for the latest version of solarviewer.

    Args:
        current_version_str (str): The current version string (e.g. "1.2.0")

    Returns:
        tuple: (is_update_available (bool), latest_version_str (str), error_message (str or None))
    """
    package_name = "solarviewer"
    pypi_url = f"https://pypi.org/pypi/{package_name}/json"

    try:
        response = requests.get(pypi_url, timeout=5)
        response.raise_for_status()
        data = response.json()

        latest_version_str = data["info"]["version"]

        current_ver = version.parse(current_version_str)
        latest_ver = version.parse(latest_version_str)

        is_available = latest_ver > current_ver

        return is_available, latest_version_str, None

    except requests.exceptions.RequestException as e:
        return False, None, f"Network error: {str(e)}"
    except Exception as e:
        return False, None, f"Error checking update: {str(e)}"
