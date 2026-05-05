from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class StopReason(Enum):
    COMPLETED = "end_turn"
    TOOL_CALL = "tool_use"
    MAX_TOKENS = "max_tokens"
    MAX_TURNS = "max_turns"
    # extended stop reasons
    CONTINUE = "continue"
    INTERRUPT = "interrupt"


class Permission(str, Enum):
    BASIC = "basic"
    STANDARD = "standard"
    STRICT = "strict"


@dataclass
class TextBlock:
    """Represents a text content block in a message."""

    text: str


@dataclass
class ThinkingBlock:
    """Represents a thinking block (extended thinking)."""

    thinking: str


@dataclass
class ToolUse:
    """Represents a tool call request from the LLM."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    """Represents the result of a tool execution."""

    content: str
    is_error: bool = False
    tool_use_id: str = ""  # Tool call ID for associating calls with results


@dataclass
class Usage:
    """Represents the token usage of a session."""

    input_tokens: int
    output_tokens: int


@dataclass
class Message:
    """Represents a conversation message."""

    role: str
    content: list[TextBlock | ThinkingBlock | ToolUse | ToolResult]


@dataclass
class Skill:
    """Represents a skill that the LLM can use."""

    name: str
    description: str
    path: Path
