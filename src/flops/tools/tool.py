from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, AsyncGenerator

from pydantic import BaseModel

from flops.llm import LLM
from flops.logger import logger
from flops.memory import Memory
from flops.registry import Registry
from flops.schemas import Permission, Skill, ToolResult, ToolUse
from flops.snapshot import Snapshot


def is_outside_workspace(file_path: str, cwd: str) -> bool:
    """Check if a resolved path is outside the workspace directory."""
    cwd_resolved = Path(cwd).resolve()
    resolved = Path(file_path).resolve() if Path(file_path).is_absolute() else (cwd_resolved / file_path).resolve()
    try:
        resolved.relative_to(cwd_resolved)
        return False
    except ValueError:
        return True


@dataclass
class ToolContext:
    """Execution context for tools."""

    cwd: str
    skills: Registry[Skill]
    snapshot: Snapshot
    memory: Memory
    llm: LLM
    stream_chat: Callable[..., AsyncGenerator]
    permission: Permission = Permission.FULL


class Tool:
    """Base class for all tools. Each tool should implement the execute method."""

    params_model: type[BaseModel]

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """
        Define schema for the tool input, following the Claude platform's format:
        https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
        """
        assert cls.__name__.endswith("Tool"), "Tool class name must end with 'Tool'"
        assert cls.__doc__ is not None, "Tool class must have a docstring."

        return {
            "name": re.sub(r"Tool$", "", cls.__name__),
            "description": cls.__doc__,
            "input_schema": cls.params_model.model_json_schema(),
        }

    def render(self, tool_input: dict) -> str:
        """Optional method to return a human-friendly representation of the tool call."""
        return f"🔧 Tool(name={self.__class__.__name__}, input={tool_input})"

    # Comment out the execute method to avoid type checker complaint about params being BaseModel
    #
    # async def execute(self, ctx: ToolContext, params: BaseModel) -> ToolResult:
    #     """Tool execution logic."""
    #     raise NotImplementedError("Subclasses must implement execute method.")

    async def __call__(self, ctx: ToolContext, input: dict) -> ToolResult:
        params = self.params_model.model_validate(input)
        return await self.execute(ctx, params)  # type: ignore


class _ToolManager:
    _tools: dict[str, type[Tool]] = {}

    @classmethod
    def register(cls, klass: type[Tool]):
        schema = klass.schema()
        cls._tools[schema["name"]] = klass

    @classmethod
    def get_schemas(cls):
        return [s.schema() for s in cls._tools.values()]

    @classmethod
    def render(cls, tool_use: ToolUse) -> str:
        tool_class = cls._tools.get(tool_use.name)
        if not tool_class:
            return f"🔧 Tool(name={tool_use.name}, input={tool_use.input})"
        return tool_class().render(tool_use.input)

    @classmethod
    async def dispatch(cls, ctx: ToolContext, tool_use: ToolUse) -> ToolResult:
        tool_class = cls._tools.get(tool_use.name)
        if not tool_class:
            return ToolResult(content=f"Tool {tool_use.name} not found", is_error=True)
        t: Tool = tool_class()
        return await t(ctx, tool_use.input)


def tool(cls):
    """A class decorator to mark a class as a tool.
    It automatically extracts the tool's name and description, and wraps the execute method to handle input validation and context.
    """
    _ToolManager.register(cls)
    return cls


def render_tool(tool_use: ToolUse) -> str:
    return _ToolManager.render(tool_use)


async def dispatch_tool(ctx: ToolContext, tool_use: ToolUse) -> ToolResult:
    try:
        result = await _ToolManager.dispatch(ctx, tool_use)
    except Exception as e:
        logger.exception(f"Tool execution failed: {tool_use.name}")
        result = ToolResult(content=str(e), is_error=True)
    result.tool_use_id = tool_use.id  # Must set tool_use_id for LLM to track the tool call
    return result


def get_tool_schemas(names: list[str] | None = None):
    schemas = _ToolManager.get_schemas()
    if names is not None:
        return [s for s in schemas if s["name"] in names]
    return schemas
