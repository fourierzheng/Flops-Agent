from pathlib import Path

from pydantic import BaseModel, Field

from flops.logger import logger
from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class RmParams(BaseModel):
    file_path: str = Field(description="The path of the file or directory to delete.")
    recursive: bool = Field(default=False, description="If True, delete directories recursively.")


# Paths that are protected and cannot be deleted
PROTECTED_PATHS = {
    "/",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
    "/home",
}


def is_protected_path(path: str) -> bool:
    """Check if a path is protected from deletion."""
    path = str(Path(path).resolve())
    for protected in PROTECTED_PATHS:
        if path == protected or path.startswith(protected + "/"):
            return True
    return False


def contains_dangerous_pattern(path: str) -> bool:
    """Check if a path contains dangerous patterns like '..' for directory traversal."""
    # Check original path string for .. before resolving
    if ".." in path:
        return True
    return False


def is_trying_to_escape_workspace(original_path: str, cwd: str) -> bool:
    """Check if a path is trying to escape the workspace."""
    cwd_resolved = Path(cwd).resolve()

    if not Path(original_path).is_absolute():
        # For relative paths, resolve and check if it's outside cwd
        try:
            resolved = (cwd_resolved / original_path).resolve()
            # Check if the resolved path is still under cwd
            try:
                resolved.relative_to(cwd_resolved)
                return False
            except ValueError:
                return True
        except Exception:
            return True
    else:
        # For absolute paths, check if it's within cwd
        try:
            resolved = Path(original_path).resolve()
            resolved.relative_to(cwd_resolved)
            return False
        except ValueError:
            return True
        except Exception:
            return True


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

        # Security check: dangerous patterns on original path BEFORE resolution
        if contains_dangerous_pattern(file_path):
            logger.warning(f"Attempted to delete path with dangerous pattern: {file_path}")
            return ToolResult(
                content=f"Cannot delete path with directory traversal: {file_path}", is_error=True
            )

        # Resolve to absolute path relative to cwd
        if not Path(file_path).is_absolute():
            file_path = str(Path(ctx.cwd) / file_path)

        # Security checks on resolved path
        if is_protected_path(file_path):
            logger.warning(f"Attempted to delete protected path: {file_path}")
            return ToolResult(content=f"Cannot delete protected path: {file_path}", is_error=True)

        if is_trying_to_escape_workspace(file_path, ctx.cwd):
            logger.warning(f"Attempted to delete path outside workspace: {file_path}")
            return ToolResult(
                content=f"Cannot delete path outside workspace: {file_path}", is_error=True
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
