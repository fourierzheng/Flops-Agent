import os

from pydantic import BaseModel, Field

from flops.const import CONFIG_DIR
from flops.logger import logger
from flops.error import ToolError
from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class ListParams(BaseModel):
    path: str = Field(default=".", description="Directory path to list.")
    show_hidden: bool = Field(default=False, description="Show hidden files when true.")


@tool
class ListTool(Tool):
    """List directory contents in a formatted output"""

    params_model = ListParams

    def render(self, tool_input: dict) -> str:
        return f"🧾 List({tool_input.get('path', '<no path>')})"

    async def execute(self, ctx: ToolContext, params: ListParams) -> ToolResult:
        """
        List directory contents.

        Args:
            path: Directory path to list. Defaults to current directory.
            show_hidden: If True, show hidden files (starting with .). Defaults to False.
        """
        path = params.path
        show_hidden = params.show_hidden
        logger.info(f"Listing directory: {path} (show_hidden={show_hidden})")
        abs_path = os.path.abspath(os.path.expanduser(path))

        # Block listing of system paths
        if str(abs_path).startswith(str(CONFIG_DIR)):
            raise ToolError(f"Error: access to this path is restricted")

        entries = os.listdir(abs_path)

        if not show_hidden:
            entries = [e for e in entries if not e.startswith(".")]

        entries.sort()
        result = []
        for entry in entries:
            full_path = os.path.abspath(os.path.join(abs_path, entry))
            # Filter out blocked system paths
            if str(full_path).startswith(str(CONFIG_DIR)):
                continue
            if os.path.isdir(full_path):
                result.append(f"{entry}/")
            elif os.path.islink(full_path):
                result.append(f"{entry}@")
            else:
                try:
                    size = os.path.getsize(full_path)
                    result.append(f"{entry} ({size}b)")
                except FileNotFoundError:
                    result.append(f"{entry}!")

        logger.debug(f"Listed {len(result)} entries in {path}")
        return ToolResult(content="\n".join(result) if result else "(empty directory)")
