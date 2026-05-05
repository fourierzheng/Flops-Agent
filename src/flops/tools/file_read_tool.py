from __future__ import annotations

from pydantic import BaseModel, Field

from flops.logger import logger
from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class FileReadParams(BaseModel):
    file_path: str = Field(description="The path of the file to read.")
    start_line: int = Field(default=0, description="The starting line number (0-indexed).")
    num_lines: int | None = Field(default=None, description="Number of lines to read.")


@tool
class FileReadTool(Tool):
    """Read a UTF-8 text file with line numbers."""

    params_model = FileReadParams

    def render(self, tool_input: dict) -> str:
        return f"📖 ReadFile({tool_input.get('file_path', '<no path>')}:{tool_input.get('start_line', 0)}:{tool_input.get('num_lines', '-')})"

    async def execute(self, ctx: ToolContext, params: FileReadParams) -> ToolResult:
        """Read the content of the specified file. Input example: {"file_path": "/path/to/file.txt"}"""
        file_path = params.file_path
        start_line = params.start_line
        num_lines = params.num_lines
        logger.info(f"Reading file: {file_path} (start_line={start_line}, num_lines={num_lines})")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                end_line = start_line + (num_lines if num_lines is not None else len(lines))
                selected_lines = lines[start_line:end_line]

            # add line numbers with | separator
            content = "".join(
                [f"{i + start_line + 1:>5}| {line}" for i, line in enumerate(selected_lines)]
            )
            logger.debug(f"Successfully read {len(selected_lines)} lines from {file_path}")
            return ToolResult(content=content)
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            return ToolResult(content=f"Error: file not found: {file_path}", is_error=True)
        except Exception as e:
            logger.exception(f"Error reading file {file_path}: {e}")
            return ToolResult(content=str(e), is_error=True)
