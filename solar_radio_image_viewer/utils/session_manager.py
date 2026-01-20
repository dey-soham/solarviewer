import json
import os
from PyQt5.QtCore import QDateTime, Qt


class SessionManager:
    """
    Manages saving and loading of application sessions.
    """

    @staticmethod
    def save_session(tabs, file_path):
        """
        Save the current session state to a JSON file.

        Args:
            tabs (list): List of SolarRadioImageTab objects.
            file_path (str): Path to the output JSON file.
        """
        session_data = {
            "version": "1.2.0",
            "timestamp": QDateTime.currentDateTime().toString(Qt.ISODate),
            "tabs": [],
        }

        for tab in tabs:
            if hasattr(tab, "get_state"):
                try:
                    state = tab.get_state()
                    session_data["tabs"].append(state)
                except Exception as e:
                    print(f"[ERROR] Failed to get state for tab: {e}")

        try:
            with open(file_path, "w") as f:
                json.dump(session_data, f, indent=4)
            return True, f"Session saved to {file_path}"
        except Exception as e:
            return False, f"Failed to save session: {str(e)}"

    @staticmethod
    def load_session(file_path):
        """
        Load a session from a JSON file.

        Args:
            file_path (str): Path to the JSON file.

        Returns:
            tuple: (success (bool), result (dict or error message))
        """
        if not os.path.exists(file_path):
            return False, f"File not found: {file_path}"

        try:
            with open(file_path, "r") as f:
                session_data = json.load(f)

            if "tabs" not in session_data:
                return False, "Invalid session file format: 'tabs' key missing"

            return True, session_data
        except Exception as e:
            return False, f"Failed to load session: {str(e)}"
