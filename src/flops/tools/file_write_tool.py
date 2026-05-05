from pathlib import Path

from pydantic import BaseModel, Field

from flops.logger import logger
from flops.schemas import Permission
from flops.tools.tool import ToolContext, Tool, ToolResult, tool, is_outside_workspace


class FileWriteParams(BaseModel):
    file_path: str = Field(description="The path of the file to write.")
    content: str = Field(description="The content to write into the file.")


@tool
class FileWriteTool(Tool):
    """Write content to a specified file"""

    params_model = FileWriteParams

    def render(self, tool_input: dict) -> str:
        return f"✍️ WriteFile({tool_input.get('file_path', '<no path>')})"

    async def execute(self, ctx: ToolContext, params: FileWriteParams) -> ToolResult:
        """Write content to the specified file. Input example: {"file_path": "/path/to/file.txt", "content": "Hello, World!"}"""
        file_path = params.file_path
        content = params.content
        logger.info(f"Writing file: {file_path}")
        logger.debug(f"Content length: {len(content)} characters")

        # Resolve to absolute path
        if not Path(file_path).is_absolute():
            file_path = str(Path(ctx.cwd) / file_path)

        # Workspace check for standard and strict
        if ctx.permission != Permission.FULL:
            if is_outside_workspace(file_path, ctx.cwd):
                logger.warning(f"Write blocked outside workspace: {file_path}")
                return ToolResult(
                    content=(
                        f"Cannot write outside workspace: {file_path}\n"
                        f"Current permission level is '{ctx.permission.value}'. "
                        f"Set `tool.permission` to `\"full\"` in config.json to allow this."
                    ),
                    is_error=True,
                )

        ctx.snapshot.backup(file_path)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"Successfully wrote to {file_path}")
            return ToolResult(content=f"Content written to {file_path}")
        except Exception as e:
            logger.exception(f"Error writing file {file_path}: {e}")
            return ToolResult(content=str(e), is_error=True)
