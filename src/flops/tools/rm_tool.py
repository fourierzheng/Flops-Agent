from pathlib import Path

from pydantic import BaseModel, Field

from flops.logger import logger
from flops.schemas import Permission
from flops.tools.tool import ToolContext, Tool, ToolResult, tool, is_outside_workspace


class RmParams(BaseModel):
    file_path: str = Field(description="The path of the file or directory to delete.")
    recursive: bool = Field(default=False, description="If True, delete directories recursively.")


@tool
class RmTool(Tool):
    """Delete a file or empty directory."""

    params_model = RmParams

    def render(self, tool_input: dict) -> str:
        return f"🗑️ Delete({tool_input.get('file_path', '<no path>')})"

    async def execute(self, ctx: ToolContext, params: RmParams) -> ToolResult:
        """Delete the specified file or directory."""
        file_path = params.file_path
        recursive = params.recursive

        # Resolve to absolute path
        if not Path(file_path).is_absolute():
            file_path = str(Path(ctx.cwd) / file_path)

        # Workspace check for standard and strict
        if ctx.permission != Permission.FULL:
            if is_outside_workspace(file_path, ctx.cwd):
                logger.warning(f"Attempted to delete path outside workspace: {file_path}")
                return ToolResult(
                    content=(
                        f"Cannot delete path outside workspace: {file_path}\n"
                        f"Current permission level is '{ctx.permission.value}'. "
                        f"Set `tool.permission` to `\"full\"` in config.json to allow this."
                    ),
                    is_error=True,
                )

        path = Path(file_path)

        ctx.snapshot.backup(file_path)

        # Check existence
        if not path.exists():
            return ToolResult(content=f"Path does not exist: {file_path}", is_error=True)

        # Try to delete
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                logger.info(f"Deleted file: {file_path}")
                return ToolResult(content=f"Deleted file: {file_path}")
            elif path.is_dir():
                if not recursive:
                    # Check if directory is empty
                    if any(path.iterdir()):
                        return ToolResult(
                            content=f"Directory not empty: {file_path}. Use recursive=True to delete.",
                            is_error=True,
                        )
                path.rmdir() if not recursive else __import__("shutil").rmtree(path)
                logger.info(f"Deleted directory: {file_path}")
                return ToolResult(content=f"Deleted directory: {file_path}")
            else:
                return ToolResult(content=f"Unknown path type: {file_path}", is_error=True)
        except OSError as e:
            logger.exception(f"Error deleting {file_path}: {e}")
            return ToolResult(content=f"Error deleting {file_path}: {e}", is_error=True)
