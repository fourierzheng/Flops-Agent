from pathlib import Path

from pydantic import BaseModel, Field

from flops.logger import logger
from flops.error import ToolError
from flops.tools.tool import ToolContext, Tool, ToolResult, tool, resolve_path_in_workspace


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

        file_path = resolve_path_in_workspace(ctx.cwd, file_path, ctx.permission, "delete")

        path = Path(file_path)

        ctx.snapshot.backup(file_path)

        # Check existence
        if not path.exists():
            raise ToolError(f"Path does not exist: {file_path}")

        if path.is_file() or path.is_symlink():
            path.unlink()
            logger.info(f"Deleted file: {file_path}")
            return ToolResult(content=f"Deleted file: {file_path}")
        elif path.is_dir():
            if not recursive:
                # Check if directory is empty
                if any(path.iterdir()):
                    raise ToolError(
                        f"Directory not empty: {file_path}. Use recursive=True to delete."
                    )
            path.rmdir() if not recursive else __import__("shutil").rmtree(path)
            logger.info(f"Deleted directory: {file_path}")
            return ToolResult(content=f"Deleted directory: {file_path}")
        else:
            raise ToolError(f"Unknown path type: {file_path}")
