from dataclasses import dataclass

from flops.schemas import (
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolResult,
    ToolUse,
    Usage,
)


class ChatEvent:
    """Base class for chat events"""


@dataclass
class TextDeltaEvent(ChatEvent):
    text: TextBlock


@dataclass
class ThinkingEvent(ChatEvent):
    thinking: ThinkingBlock


@dataclass
class ToolUseEvent(ChatEvent):
    tool_use: ToolUse


@dataclass
class StopEvent(ChatEvent):
    reason: StopReason


@dataclass
class ToolResultEvent(ChatEvent):
    result: ToolResult


@dataclass
class UsageEvent(ChatEvent):
    usage: Usage


@dataclass
class ErrorEvent(ChatEvent):
    error: Exception


@dataclass
class LineEvent(ChatEvent):
    line: str


@dataclass
class NoticeEvent(ChatEvent):
    line: str


@dataclass
class ExitEvent(ChatEvent):
    pass


@dataclass
class ToolOutputEvent(ChatEvent):
    """Text output streamed from a tool execution (e.g. sub-agent)."""

    text: str
