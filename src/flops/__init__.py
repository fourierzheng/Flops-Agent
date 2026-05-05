"""
Flops Agent - An intelligent AI assistant powered by LLM
"""

__version__ = "0.1.0"

from flops.cli import main
from flops.config import Config
from flops.engine import Engine

__all__ = ["main", "Engine", "Config", "__version__"]
