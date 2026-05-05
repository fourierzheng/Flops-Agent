import logging
from pathlib import Path
import time

from flops.config import LogConfig
from flops.const import LOGS_DIR, MAX_LOG_FILES

# Create logger
logger = logging.getLogger("flops")

# Default level
_level = logging.INFO

# Disable default console handler to prevent output to stderr
# This must be set early before any logging calls
logging.lastResort = None


def _cleanup_old_logs():
    """Clean up old log files, keeping only the latest MAX_LOG_FILES."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_files = sorted(
        LOGS_DIR.glob("*.log"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old_file in log_files[MAX_LOG_FILES:]:
        try:
            old_file.unlink()
        except OSError:
            pass


def _get_new_log_path() -> Path:
    """Get a new log file path based on current time."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / time.strftime("%Y%m%d-%H%M%S.log")


def config_log(log: LogConfig):
    """Set log level from config"""

    level = log.level
    global _level
    _level = getattr(logging, level.upper(), logging.INFO)
    # Also remove any existing handlers from root logger
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    logger.setLevel(_level)
    if not logger.handlers:
        # Clean up old logs and create new log file
        _cleanup_old_logs()
        handler = logging.FileHandler(_get_new_log_path(), mode="w")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
            )
        )
        logger.addHandler(handler)
