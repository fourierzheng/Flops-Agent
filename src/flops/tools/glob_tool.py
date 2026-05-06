from pathlib import Path

from pydantic import BaseModel, Field

from flops.const import CONFIG_DIR
from flops.logger import logger
from flops.error import ToolError
from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class GlobParams(BaseModel):
    path: str = Field(default=".", description="Base directory to search.")
    pattern: str = Field(default="*", description="Glob pattern to match files.")
    show_hidden: bool = Field(default=False, description="Whether to include hidden files.")


@tool
class GlobTool(Tool):
    """List files matching a glob pattern."""

    params_model = GlobParams

    def render(self, tool_input: dict) -> str:
        return (
            f"🧶 Glob({tool_input.get('pattern', '<no pattern>')} in {tool_input.get('path', '.')})"
        )

    async def execute(self, ctx: ToolContext, params: GlobParams) -> ToolResult:
        path = params.path
        pattern = params.pattern
        show_hidden = params.show_hidden

        logger.info(f"Glob searching: path={path}, pattern={pattern}, show_hidden={show_hidden}")
        root = Path(path).expanduser().resolve()
        if not root.exists():
            logger.error(f"Path does not exist: {root}")
            raise ToolError(f"Error: path does not exist: {root}")
        if root.is_file():
            logger.error(f"Path is a file, expected directory: {root}")
            raise ToolError(f"Error: path must be a directory, not a file: {root}")

        # Block globbing of system paths
        if str(root).startswith(str(CONFIG_DIR)):
            raise ToolError(f"Error: access to this path is restricted")

        matches = []
        for candidate in sorted(root.glob(pattern)):
            if not show_hidden and any(
                part.startswith(".") for part in candidate.relative_to(root).parts
            ):
                continue
            # Filter out blocked system paths
            if str(candidate.resolve()).startswith(str(CONFIG_DIR)):
                continue
            matches.append(candidate.as_posix())

        if not matches:
            logger.info("Glob search found no matches")
            return ToolResult(content="No matches found.")

        logger.info(f"Glob search found {len(matches)} matches")
        return ToolResult(content="\n".join(matches))
