"""
Utility functions for the LOFAR Solar Imaging Pipeline.

This module provides various utility functions for the pipeline, including
a centralized logging system that can be used across all pipeline components.
"""

import os
import sys
import logging
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler
import threading
from queue import Queue


class LogRecord:
    """A simple container for log records to be passed to GUI listeners."""

    def __init__(self, level, name, message, timestamp=None):
        self.level = level
        self.name = name
        self.message = message
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            "level": self.level,
            "name": self.name,
            "message": self.message,
            "timestamp": self.timestamp,
        }


class PipelineLoggerManager:
    """
    Centralized logger manager for the LOFAR pipeline.

    This class manages logger instances, file handlers, and GUI listeners,
    ensuring consistent logging behavior across the pipeline.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(PipelineLoggerManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, log_dir=None, log_level=logging.INFO):
        if self._initialized and log_dir is None:
            return

        # If we're already initialized but a new log_dir is provided, update it
        if self._initialized and log_dir is not None and log_dir != self.log_dir:
            self.update_log_dir(log_dir)
            return

        # Set log directory
        self.log_dir = log_dir or os.path.join(os.getcwd(), "logs")
        os.makedirs(self.log_dir, exist_ok=True)

        # Default log level
        self.log_level = log_level

        # Generate log file name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(self.log_dir, f"pipeline_{timestamp}.log")

        # Set up the root logger
        self.root_logger = logging.getLogger()
        self.root_logger.setLevel(self.log_level)

        # Create handlers
        self._create_handlers()

        # Store registered loggers
        self.loggers = {}

        # Queue and listeners for GUI updates
        self.log_queue = Queue()
        self.gui_listeners = []

        self._initialized = True

    def update_log_dir(self, new_log_dir):
        """Update the log directory and recreate file handlers."""
        # Store old handlers for removal
        old_file_handler = self.file_handler if hasattr(self, "file_handler") else None

        # Update log directory
        self.log_dir = new_log_dir
        os.makedirs(self.log_dir, exist_ok=True)

        # Generate a new log file name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(self.log_dir, f"pipeline_{timestamp}.log")

        # Create a new file handler
        file_handler = RotatingFileHandler(
            self.log_file, maxBytes=10 * 1024 * 1024, backupCount=5  # 10MB
        )
        file_handler.setLevel(self.log_level)
        file_format = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
        )
        file_handler.setFormatter(file_format)

        # Remove old file handler and add new one
        if old_file_handler:
            self.root_logger.removeHandler(old_file_handler)
        self.root_logger.addHandler(file_handler)

        # Update reference to the file handler
        self.file_handler = file_handler

        # Log the change to both handlers
        self.root_logger.info(f"Log directory updated to: {self.log_dir}")
        self.root_logger.info(f"New log file: {self.log_file}")

    def _create_handlers(self):
        """Create and configure log handlers."""
        # Clear existing handlers
        for handler in self.root_logger.handlers[:]:
            self.root_logger.removeHandler(handler)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.log_level)
        console_format = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
        )
        console_handler.setFormatter(console_format)

        # File handler (rotating)
        file_handler = RotatingFileHandler(
            self.log_file, maxBytes=10 * 1024 * 1024, backupCount=5  # 10MB
        )
        file_handler.setLevel(self.log_level)
        file_format = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
        )
        file_handler.setFormatter(file_format)

        # Add handlers to root logger
        self.root_logger.addHandler(console_handler)
        self.root_logger.addHandler(file_handler)

        # Store handlers for later access
        self.console_handler = console_handler
        self.file_handler = file_handler

    def set_log_level(self, level):
        """Set the logging level for all handlers."""
        self.log_level = level
        self.root_logger.setLevel(level)
        self.console_handler.setLevel(level)
        self.file_handler.setLevel(level)

    def get_logger(self, name):
        """
        Get or create a logger with the specified name.

        Parameters
        ----------
        name : str
            Name of the logger.

        Returns
        -------
        PipelineLogger
            Logger instance with the specified name.
        """
        if name in self.loggers:
            return self.loggers[name]

        # Create a new logger
        logger = logging.getLogger(name)
        logger.setLevel(self.log_level)

        # Wrap with our custom logger
        pipeline_logger = PipelineLogger(logger, self)
        self.loggers[name] = pipeline_logger

        return pipeline_logger

    def register_gui_listener(self, callback):
        """
        Register a GUI callback to receive log updates.

        Parameters
        ----------
        callback : callable
            Function to call with new log records.
        """
        self.gui_listeners.append(callback)

    def unregister_gui_listener(self, callback):
        """
        Unregister a GUI callback.

        Parameters
        ----------
        callback : callable
            The callback to unregister.
        """
        if callback in self.gui_listeners:
            self.gui_listeners.remove(callback)

    def notify_listeners(self, log_record):
        """
        Notify all GUI listeners of a new log record.

        Parameters
        ----------
        log_record : LogRecord
            The log record to notify listeners about.
        """
        # Add to queue
        self.log_queue.put(log_record)

        # Notify all listeners
        for listener in self.gui_listeners:
            try:
                listener(log_record)
            except Exception as e:
                # Don't let errors in GUI callbacks break the logging
                sys.stderr.write(f"Error in log listener: {e}\n")

    def get_log_queue(self):
        """Get the queue containing log records for GUI consumption."""
        return self.log_queue


class PipelineLogger:
    """
    Wrapper around a standard logger that notifies GUI listeners.
    """

    def __init__(self, logger, manager):
        self._logger = logger
        self._manager = manager

    def _log_and_notify(self, level, message, *args, **kwargs):
        """Log a message and notify GUI listeners."""
        # Format the message with args and kwargs
        if args or kwargs:
            formatted_message = message % args if args else message
        else:
            formatted_message = message

        # Create a log record
        log_record = LogRecord(
            level=logging.getLevelName(level),
            name=self._logger.name,
            message=formatted_message,
        )

        # Notify listeners
        self._manager.notify_listeners(log_record)

        # Log the message
        self._logger.log(level, message, *args, **kwargs)

    def debug(self, message, *args, **kwargs):
        """Log a debug message."""
        self._log_and_notify(logging.DEBUG, message, *args, **kwargs)

    def info(self, message, *args, **kwargs):
        """Log an info message."""
        self._log_and_notify(logging.INFO, message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        """Log a warning message."""
        self._log_and_notify(logging.WARNING, message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        """Log an error message."""
        self._log_and_notify(logging.ERROR, message, *args, **kwargs)

    def critical(self, message, *args, **kwargs):
        """Log a critical message."""
        self._log_and_notify(logging.CRITICAL, message, *args, **kwargs)

    def exception(self, message, *args, **kwargs):
        """Log an exception message."""
        self._log_and_notify(logging.ERROR, message, *args, **kwargs)


# Global instance for easy access
def get_logger_manager(log_dir=None, log_level=None):
    """
    Get the singleton logger manager instance.

    Parameters
    ----------
    log_dir : str, optional
        Directory to store log files. Only used on first call.
    log_level : int, optional
        Logging level. Only used on first call.

    Returns
    -------
    PipelineLoggerManager
        Singleton logger manager instance.
    """
    if log_level is None:
        log_level = logging.INFO

    manager = PipelineLoggerManager(log_dir, log_level)
    return manager


def get_logger(name, log_dir=None, log_level=None):
    """
    Get a logger with the specified name.

    Parameters
    ----------
    name : str
        Name of the logger.
    log_dir : str, optional
        Directory to store log files. Only used on first call.
    log_level : int, optional
        Logging level. Only used on first call.

    Returns
    -------
    PipelineLogger
        Logger instance with the specified name.
    """
    manager = get_logger_manager(log_dir, log_level)
    return manager.get_logger(name)


"""
LOFAR Solar Imaging Pipeline Checkpoint Manager

This module manages checkpoints to allow the pipeline to resume from where it left off
if interrupted. It provides functions to save, load, and check checkpoint status.

Checkpoints are stored as JSON files in a dedicated directory structure.
"""

import os
import json
import shutil
import logging
import glob
import time
from typing import Dict, List, Any, Union, Optional, Tuple
import sys


class CheckpointManager:
    """
    Manages checkpoints for the LOFAR Solar Imaging Pipeline to enable resuming
    from interruptions.

    Attributes:
        working_dir (str): Pipeline working directory
        checkpoint_dir (str): Directory to store checkpoint files
        logger (logging.Logger): Logger for checkpoint operations
        checkpoint_data (dict): Current checkpoint data
        resume_mode (bool): Whether pipeline is running in resume mode
    """

    def __init__(
        self,
        working_dir: str,
        resume: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the CheckpointManager.

        Args:
            working_dir (str): Pipeline working directory
            resume (bool): Whether to resume from checkpoints
            logger (logging.Logger, optional): Logger instance
        """
        self.working_dir = working_dir
        self.checkpoint_dir = os.path.join(working_dir, ".checkpoints")
        self.resume_mode = resume
        self.logger = logger or logging.getLogger("checkpoint_manager")

        # Create checkpoint directory if it doesn't exist
        os.makedirs(self.checkpoint_dir, exist_ok=True)

        # Main checkpoint file location
        self.checkpoint_file = os.path.join(
            self.checkpoint_dir, "pipeline_checkpoints.json"
        )

        # Initialize or load checkpoint data
        if resume and os.path.exists(self.checkpoint_file):
            self.checkpoint_data = self._load_checkpoints()
            self.logger.info(f"Resumed checkpoint data from {self.checkpoint_file}")
        else:
            # Initialize fresh checkpoint data
            self.checkpoint_data = {
                "pipeline_info": {
                    "start_time": datetime.now().isoformat(),
                    "last_update": datetime.now().isoformat(),
                    "working_dir": working_dir,
                    "resume_count": 0,
                },
                "stages": {
                    "dynamic_spectra": {"status": "pending", "timestamp": None},
                    "calibrator_processing": {"status": "pending", "timestamp": None},
                    "calibrator_calibration": {"status": "pending", "timestamp": None},
                    "solar_preparation": {"status": "pending", "timestamp": None},
                    "selfcal": {"status": "pending", "timestamp": None, "chunks": {}},
                    "imaging": {"status": "pending", "timestamp": None, "chunks": {}},
                },
                "results": {
                    "calib_table": None,
                    "for_imaging_ms": [],
                    "selfcal_chunks": {},
                    "selfcal_results": [],
                    "imaging_results": [],
                    "imaging_chunks": {},
                    "dynamic_spectra_files": {},
                },
            }

            if resume:
                self.logger.warning(
                    f"Resume mode requested but no checkpoint file found at {self.checkpoint_file}. Starting fresh."
                )
                # Increment resume counter to indicate a fresh start in resume mode
                self.checkpoint_data["pipeline_info"]["resume_count"] = 1
            else:
                self.logger.info("Starting fresh pipeline run with new checkpoints.")

            # Save initial checkpoint data
            self._save_checkpoints()

    def _save_checkpoints(self) -> None:
        """Save current checkpoint data to disk."""
        # Update timestamp
        self.checkpoint_data["pipeline_info"][
            "last_update"
        ] = datetime.now().isoformat()

        # Write to temporary file first, then rename to avoid corruption if interrupted
        temp_file = f"{self.checkpoint_file}.tmp"
        try:
            with open(temp_file, "w") as f:
                json.dump(self.checkpoint_data, f, indent=2, cls=CustomJSONEncoder)

            # Atomic replacement
            shutil.move(temp_file, self.checkpoint_file)

            # Log at debug level to avoid excessive messages for frequent saves
            self.logger.debug(f"Checkpoint data saved to {self.checkpoint_file}")
        except Exception as e:
            self.logger.error(f"Error saving checkpoint data: {str(e)}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
            # Don't re-raise this error as it would disrupt the pipeline

    def _load_checkpoints(self) -> Dict[str, Any]:
        """Load checkpoint data from disk."""
        try:
            with open(self.checkpoint_file, "r") as f:
                data = json.load(f)

            # Increment resume counter
            if "pipeline_info" in data and "resume_count" in data["pipeline_info"]:
                data["pipeline_info"]["resume_count"] += 1
            else:
                # Initialize if missing
                data.setdefault("pipeline_info", {})
                data["pipeline_info"]["resume_count"] = 1

            # Updated for this resume
            data["pipeline_info"]["last_update"] = datetime.now().isoformat()

            # Auto-repair any list-format chunk data
            # self._fix_chunk_data_format(data)

            return data
        except (json.JSONDecodeError, IOError, ValueError) as e:
            self.logger.error(f"Error loading checkpoint file: {str(e)}")
            self.logger.warning("Starting with fresh checkpoints due to loading error.")
            return {
                "pipeline_info": {
                    "start_time": datetime.now().isoformat(),
                    "last_update": datetime.now().isoformat(),
                    "working_dir": self.working_dir,
                    "resume_count": 1,
                },
                "stages": {},
                "results": {},
            }

    def _fix_chunk_data_format(self, data: Dict[str, Any]) -> None:
        """
        Convert any list-format chunk data to dictionary format.
        This ensures backward compatibility with older checkpoint formats.

        Args:
            data (Dict[str, Any]): Checkpoint data to fix
        """
        try:
            # Fix results section
            if "results" in data:
                for stage_name in ["selfcal", "imaging"]:
                    chunk_key = f"{stage_name}_chunks"
                    if chunk_key in data["results"] and isinstance(
                        data["results"][chunk_key], list
                    ):
                        self.logger.info(
                            f"Converting {chunk_key} from list to dictionary format for compatibility"
                        )

                        # Create new dictionary from list items
                        new_chunks = {}
                        for idx, item in enumerate(data["results"][chunk_key]):
                            if item is not None:
                                new_chunks[str(idx)] = item

                        # Replace list with dictionary
                        data["results"][chunk_key] = new_chunks

                        # If stage data exists, ensure chunks are marked as completed
                        if (
                            "stages" in data
                            and stage_name in data["stages"]
                            and isinstance(data["stages"][stage_name], dict)
                        ):
                            if "chunks" not in data["stages"][stage_name]:
                                data["stages"][stage_name]["chunks"] = {}

                            for chunk_idx in new_chunks.keys():
                                if (
                                    chunk_idx
                                    not in data["stages"][stage_name]["chunks"]
                                ):
                                    data["stages"][stage_name]["chunks"][chunk_idx] = {
                                        "status": "completed",
                                        "timestamp": datetime.now().isoformat(),
                                    }
        except Exception as e:
            # Log but don't raise - we don't want to prevent loading checkpoint just because repair failed
            self.logger.warning(
                f"Error fixing chunk data format: {str(e)}. Some features may not work correctly."
            )

    def mark_stage_complete(self, stage_name: str, result: Any = None) -> None:
        """
        Mark a pipeline stage as complete and save the result if provided.

        Args:
            stage_name (str): Name of the completed stage
            result (Any, optional): Result data to store for this stage
        """
        try:
            # Update stage status
            self.checkpoint_data["stages"].setdefault(stage_name, {})
            self.checkpoint_data["stages"][stage_name]["status"] = "completed"
            self.checkpoint_data["stages"][stage_name][
                "timestamp"
            ] = datetime.now().isoformat()

            # Store result if provided
            if result is not None:
                self.checkpoint_data["results"][stage_name] = result

            self.logger.info(f"Stage '{stage_name}' marked as complete.")
            self._save_checkpoints()
        except Exception as e:
            self.logger.error(f"Error marking stage {stage_name} as complete: {str(e)}")
            # Re-raise the error to allow caller to handle it
            raise

    def mark_stage_in_progress(self, stage_name: str) -> None:
        """
        Mark a pipeline stage as in progress.

        Args:
            stage_name (str): Name of the stage
        """
        try:
            self.checkpoint_data["stages"].setdefault(stage_name, {})
            self.checkpoint_data["stages"][stage_name]["status"] = "in_progress"
            self.checkpoint_data["stages"][stage_name][
                "timestamp"
            ] = datetime.now().isoformat()

            self.logger.debug(f"Stage '{stage_name}' marked as in-progress.")
            self._save_checkpoints()
        except Exception as e:
            self.logger.error(
                f"Error marking stage {stage_name} as in progress: {str(e)}"
            )
            # Re-raise the error to allow caller to handle it
            raise

    def mark_chunk_complete(
        self, stage_name: str, chunk_index: int, chunk_result: Any = None
    ) -> None:
        """
        Mark a chunk within a stage (like self-calibration or imaging) as complete.

        Args:
            stage_name (str): Name of the parent stage
            chunk_index (int): Index of the chunk
            chunk_result (Any, optional): Result data for this chunk
        """
        try:
            # Initialize if needed
            self.checkpoint_data["stages"].setdefault(stage_name, {})
            self.checkpoint_data["stages"][stage_name].setdefault("chunks", {})

            # Make sure to use string key for storing in JSON
            chunk_key = str(chunk_index)
            self.checkpoint_data["stages"][stage_name]["chunks"][chunk_key] = {
                "status": "completed",
                "timestamp": datetime.now().isoformat(),
            }

            # Store result
            if chunk_result is not None:
                # Make sure results section is initialized
                self.checkpoint_data["results"].setdefault(f"{stage_name}_chunks", {})

                # Handle different types of chunk_result
                if isinstance(chunk_result, dict):
                    # Dictionary is the expected format - use as is
                    self.checkpoint_data["results"][f"{stage_name}_chunks"][
                        chunk_key
                    ] = chunk_result
                elif isinstance(chunk_result, str):
                    # String is often a path to the output file - wrap in a dictionary
                    self.logger.warning(
                        f"Converting string chunk result to dictionary for chunk {chunk_index}"
                    )
                    self.checkpoint_data["results"][f"{stage_name}_chunks"][
                        chunk_key
                    ] = {
                        "output_ms": chunk_result,
                        "original_string_value": chunk_result,
                    }
                elif isinstance(chunk_result, list):
                    # Convert list to dictionary
                    self.logger.warning(
                        f"Converting list chunk result to dictionary for chunk {chunk_index}"
                    )
                    result_dict = {}
                    for i, item in enumerate(chunk_result):
                        result_dict[f"item_{i}"] = item
                    self.checkpoint_data["results"][f"{stage_name}_chunks"][
                        chunk_key
                    ] = result_dict
                else:
                    # For any other type, store in a wrapper dictionary
                    self.logger.warning(
                        f"Wrapping chunk result of type {type(chunk_result)} in dictionary for chunk {chunk_index}"
                    )
                    self.checkpoint_data["results"][f"{stage_name}_chunks"][
                        chunk_key
                    ] = {
                        "value": str(chunk_result),
                        "original_type": str(type(chunk_result)),
                    }

            self.logger.info(
                f"Chunk {chunk_index} of stage '{stage_name}' marked as complete."
            )
            self._save_checkpoints()
        except Exception as e:
            self.logger.error(
                f"Error marking chunk {chunk_index} of stage {stage_name} as complete: {str(e)}"
            )
            # Re-raise the error to allow caller to handle it
            raise

    def mark_chunk_in_progress(self, stage_name: str, chunk_index: int) -> None:
        """
        Mark a chunk within a stage as in progress.

        Args:
            stage_name (str): Name of the parent stage
            chunk_index (int): Index of the chunk
        """
        try:
            # Initialize if needed
            self.checkpoint_data["stages"].setdefault(stage_name, {})
            self.checkpoint_data["stages"][stage_name].setdefault("chunks", {})

            # Make sure to use string key for storing in JSON
            chunk_key = str(chunk_index)
            self.checkpoint_data["stages"][stage_name]["chunks"][chunk_key] = {
                "status": "in_progress",
                "timestamp": datetime.now().isoformat(),
            }

            self.logger.debug(
                f"Chunk {chunk_index} of stage '{stage_name}' marked as in-progress."
            )
            self._save_checkpoints()
        except Exception as e:
            self.logger.error(
                f"Error marking chunk {chunk_index} of stage {stage_name} as in progress: {str(e)}"
            )
            # Re-raise the error to allow caller to handle it
            raise

    def is_stage_completed(self, stage_name: str) -> bool:
        """
        Check if a pipeline stage has been completed.

        Args:
            stage_name (str): Name of the stage to check

        Returns:
            bool: True if stage is completed, False otherwise
        """
        if not self.resume_mode:
            return False

        try:
            return (
                stage_name in self.checkpoint_data["stages"]
                and self.checkpoint_data["stages"][stage_name]["status"] == "completed"
            )
        except (KeyError, TypeError):
            return False

    def is_chunk_completed(self, stage_name: str, chunk_index: int) -> bool:
        """
        Check if a chunk within a stage has been completed.

        Args:
            stage_name (str): Name of the parent stage
            chunk_index (int): Index of the chunk

        Returns:
            bool: True if chunk is completed, False otherwise
        """
        if not self.resume_mode:
            return False

        try:
            # Make sure we're using a string key for lookup
            chunk_key = str(chunk_index)
            return (
                stage_name in self.checkpoint_data["stages"]
                and "chunks" in self.checkpoint_data["stages"][stage_name]
                and chunk_key in self.checkpoint_data["stages"][stage_name]["chunks"]
                and self.checkpoint_data["stages"][stage_name]["chunks"][chunk_key][
                    "status"
                ]
                == "completed"
            )
        except (KeyError, TypeError, ValueError) as e:
            self.logger.error(
                f"Error checking if chunk {chunk_index} is completed for stage {stage_name}: {str(e)}"
            )
            return False

    def get_completed_chunks(self, stage_name: str) -> List[int]:
        """
        Get list of completed chunk indices for a stage.

        Args:
            stage_name (str): Name of the stage

        Returns:
            List[int]: List of completed chunk indices
        """
        if not self.resume_mode:
            return []

        try:
            completed = []
            if stage_name in self.checkpoint_data["stages"]:
                stage_data = self.checkpoint_data["stages"][stage_name]

                # Handle the standard case where chunks are stored in a dictionary
                if (
                    isinstance(stage_data, dict)
                    and "chunks" in stage_data
                    and isinstance(stage_data["chunks"], dict)
                ):
                    chunks = stage_data["chunks"]
                    # Ensure we're returning integers, not strings
                    completed = [
                        int(k)
                        for k, v in chunks.items()
                        if isinstance(v, dict) and v.get("status") == "completed"
                    ]

                # Also check for chunks in the results section for backward compatibility
                result_chunks = self.checkpoint_data["results"].get(
                    f"{stage_name}_chunks"
                )
                if isinstance(result_chunks, dict):
                    # Add any indices from results not already in completed list
                    for k in result_chunks.keys():
                        try:
                            chunk_idx = int(k)
                            if chunk_idx not in completed:
                                # Verify it's actually completed by checking the stage data
                                if self.is_chunk_completed(stage_name, chunk_idx):
                                    completed.append(chunk_idx)
                        except (ValueError, TypeError):
                            pass
                elif isinstance(result_chunks, list):
                    # If stored as a list, indices with data are considered completed
                    for i, item in enumerate(result_chunks):
                        if item is not None and i not in completed:
                            # Verify it's actually completed by checking the stage data
                            if self.is_chunk_completed(stage_name, i):
                                completed.append(i)

            return sorted(completed)
        except (KeyError, TypeError, ValueError) as e:
            self.logger.error(
                f"Error getting completed chunks for stage {stage_name}: {str(e)}"
            )
            return []

    def get_stage_result(self, stage_name: str) -> Any:
        """
        Get the stored result for a completed stage.

        Args:
            stage_name (str): Name of the stage

        Returns:
            Any: The stored result, or None if not found
        """
        try:
            return self.checkpoint_data["results"].get(stage_name)
        except (KeyError, TypeError):
            return None

    def get_chunk_result(self, stage_name: str, chunk_index: int) -> Any:
        """
        Get the stored result for a completed chunk.

        Args:
            stage_name (str): Name of the parent stage
            chunk_index (int): Index of the chunk

        Returns:
            Any: The stored result, or None if not found
        """
        try:
            chunk_key = str(chunk_index)
            chunk_results = self.checkpoint_data["results"].get(f"{stage_name}_chunks")

            # Check if chunk_results is a dictionary (expected format)
            if isinstance(chunk_results, dict):
                result = chunk_results.get(chunk_key)

                # if result is a dictionary, return it
                if isinstance(result, dict):
                    print(f"Chunk result is a dictionary: {result}")
                    return result

                # If result is a string, convert to dictionary for compatibility with pipeline
                elif isinstance(result, str):
                    self.logger.debug(
                        f"Converting string chunk result to dictionary for chunk {chunk_index}"
                    )
                    return {"output_ms": result, "original_string_value": result}
                    return result

            # Check if it's a list (unexpected but possible in some cases)
            elif isinstance(chunk_results, list) and 0 <= chunk_index < len(
                chunk_results
            ):
                result = chunk_results[chunk_index]

                # If result is a string, convert to dictionary for compatibility with pipeline
                if isinstance(result, str):
                    self.logger.debug(
                        f"Converting string chunk result to dictionary for chunk {chunk_index}"
                    )
                    return {"output_ms": result, "original_string_value": result}
                return result
            else:
                return None
        except (KeyError, TypeError, IndexError) as e:
            self.logger.error(
                f"Error getting chunk result for stage {stage_name}, chunk {chunk_index}: {str(e)}"
            )
            return None

    def store_data(self, key: str, data: Any) -> None:
        """
        Store arbitrary data in the checkpoint.

        Args:
            key (str): Key to store the data under
            data (Any): Data to store
        """
        try:
            # Special handling for chunk-related data to ensure consistency
            if key.endswith("_chunks") and not isinstance(data, dict):
                # If trying to store a list for a chunks key, store it as a dictionary instead
                if isinstance(data, list):
                    self.logger.debug(
                        f"Converting {key} from list to dictionary for storage"
                    )
                    new_data = {}
                    for idx, item in enumerate(data):
                        if item is not None:
                            new_data[str(idx)] = item
                    data = new_data
                # If it's neither a list nor dictionary, wrap it in a dictionary with key "0"
                elif data is not None:
                    self.logger.debug(
                        f"Wrapping {key} data in a dictionary for storage"
                    )
                    data = {"0": data}

            self.checkpoint_data["results"][key] = data
            self._save_checkpoints()
        except Exception as e:
            self.logger.error(f"Error storing data under key '{key}': {str(e)}")
            # Re-raise to allow caller to handle
            raise

    def get_data(self, key: str, default: Any = None) -> Any:
        """
        Retrieve stored data from the checkpoint.

        Args:
            key (str): Key to retrieve
            default (Any, optional): Default value if key not found

        Returns:
            Any: The stored data, or default if not found
        """
        try:
            return self.checkpoint_data["results"].get(key, default)
        except Exception as e:
            self.logger.error(f"Error retrieving data for key '{key}': {str(e)}")
            return default

    def verify_file_exists(self, filepath: str) -> bool:
        """
        Verify that a file exists as part of checkpoint validation.

        Args:
            filepath (str): Path to the file to check

        Returns:
            bool: True if file exists, False otherwise
        """
        return os.path.exists(filepath)

    def get_resume_info(self) -> Dict[str, Any]:
        """
        Get information about the current pipeline resumption.

        Returns:
            Dict[str, Any]: Resume information dictionary
        """
        return {
            "resume_mode": self.resume_mode,
            "resume_count": self.checkpoint_data["pipeline_info"].get(
                "resume_count", 0
            ),
            "original_start_time": self.checkpoint_data["pipeline_info"].get(
                "start_time"
            ),
            "last_update": self.checkpoint_data["pipeline_info"].get("last_update"),
        }

    def clear_checkpoints(self) -> None:
        """Clear all checkpoints and reset to initial state."""
        if os.path.exists(self.checkpoint_file):
            # Backup the checkpoint file before deleting
            backup_file = f"{self.checkpoint_file}.bak.{int(time.time())}"
            shutil.copy2(self.checkpoint_file, backup_file)
            self.logger.info(f"Backed up checkpoint file to {backup_file}")

            # Delete the checkpoint file
            os.remove(self.checkpoint_file)

        # Reset the checkpoint data
        self.checkpoint_data = {
            "pipeline_info": {
                "start_time": datetime.now().isoformat(),
                "last_update": datetime.now().isoformat(),
                "working_dir": self.working_dir,
                "resume_count": 0,
            },
            "stages": {},
            "results": {},
        }

        self.logger.info("All checkpoints cleared.")
        self._save_checkpoints()


class CustomJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that can handle numpy arrays and other special types.

    This makes the checkpoint system more robust when saving complex results.
    """

    def default(self, obj):
        try:
            # Handle NumPy arrays
            if "numpy" in sys.modules and hasattr(sys.modules["numpy"], "ndarray"):
                if isinstance(obj, sys.modules["numpy"].ndarray):
                    return obj.tolist()

            # Handle NumPy data types
            if "numpy" in sys.modules and hasattr(sys.modules["numpy"], "integer"):
                if isinstance(obj, sys.modules["numpy"].integer):
                    return int(obj)

            if "numpy" in sys.modules and hasattr(sys.modules["numpy"], "floating"):
                if isinstance(obj, sys.modules["numpy"].floating):
                    return float(obj)

            # Handle sets
            if isinstance(obj, set):
                return list(obj)

            # Handle datetimes
            if isinstance(obj, datetime.datetime):
                return obj.isoformat()

            # Handle custom objects with __dict__
            if hasattr(obj, "__dict__"):
                return {
                    "__class__": obj.__class__.__name__,
                    "__module__": obj.__module__,
                    "attributes": obj.__dict__,
                }

            # For any other types, use string representation
            return str(obj)
        except:
            return str(obj)

        # Let the base class handle it otherwise
        return json.JSONEncoder.default(self, obj)


def create_checkpoint_manager(
    working_dir: str, resume: bool = False, logger: Optional[logging.Logger] = None
) -> CheckpointManager:
    """
    Factory function to create a CheckpointManager instance.

    Args:
        working_dir (str): Pipeline working directory
        resume (bool): Whether to resume from checkpoints
        logger (logging.Logger, optional): Logger instance

    Returns:
        CheckpointManager: Initialized checkpoint manager
    """
    return CheckpointManager(working_dir, resume, logger)
