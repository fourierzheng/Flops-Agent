"""Constants and paths for flops."""

import os
from pathlib import Path

# Config directory
_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
CONFIG_DIR = Path(_CONFIG_HOME) / "flops"

# Paths
LOGS_DIR = CONFIG_DIR / "logs"
SESSIONS_DIR = CONFIG_DIR / "sessions"
HISTORY_PATH = CONFIG_DIR / "history"
SKILLS_DIR = CONFIG_DIR / "skills"

# Trash directory for snapshot backups
TRASH_DIR = CONFIG_DIR / "trash"

# Memory
MEMORY_DIR = CONFIG_DIR / "memory"

# Log settings
MAX_LOG_FILES = 10

# LLM request timeout (seconds)
REQUEST_TIMEOUT = 600

# Fixed configuration
MAX_TOKENS_RESERVE = 8192  # Reserved max_tokens size
COMPRESSION_THRESHOLD = 0.7  # Trigger compression at 70%
