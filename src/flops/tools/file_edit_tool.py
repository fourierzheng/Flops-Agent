from pydantic import BaseModel, Field

from flops.logger import logger
from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class FileEditParams(BaseModel):
    file_path: str = Field(description="The path of the file to edit.")
    old_str: str = Field(description="The exact string to replace.")
    new_str: str = Field(description="The exact replacement string.")
    replace_all: bool = Field(default=False, description="Whether to replace all occurrences.")


@tool
class FileEditTool(Tool):
    """Edit an existing file by replacing a string."""

    params_model = FileEditParams

    def render(self, tool_input: dict) -> str:
        return f"📝 EditFile({tool_input.get('file_path', '<no path>')})"

    async def execute(self, ctx: ToolContext, params: FileEditParams) -> ToolResult:
        file_path = params.file_path
        old_str = params.old_str
        new_str = params.new_str
        replace_all = params.replace_all
        logger.info(f"Editing file: {file_path} (replace_all={replace_all})")

        ctx.snapshot.backup(file_path)

        if old_str == "":
            logger.warning("Edit failed: old_str is empty")
            return ToolResult(content="Error: old_str cannot be empty", is_error=True)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            logger.debug(f"File read successfully, size: {len(content)} bytes")
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            return ToolResult(content=f"Error: file not found: {file_path}", is_error=True)
        except Exception as e:
            logger.exception(f"Error reading file {file_path}: {e}")
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        if old_str not in content:
            logger.warning(f"old_str not found in {file_path}")
            return ToolResult(content=f"Error: old_str not found in {file_path}", is_error=True)

        if replace_all:
            count = content.count(old_str)
            new_content = content.replace(old_str, new_str)
            logger.info(f"Replaced {count} occurrence(s) in {file_path}")
        else:
            count = content.count(old_str)
            if count > 1:
                logger.warning(f"Multiple matches ({count}) found, use replace_all=True")
                return ToolResult(
                    content=f"Error: old_str appears {count} times in {file_path}, use replace_all=True to replace all occurrences",
                    is_error=True,
                )
            new_content = content.replace(old_str, new_str, 1)
            logger.info(f"Replaced 1 occurrence in {file_path}")

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            logger.info(f"Successfully edited {file_path}")
            return ToolResult(content=f"Content edited in {file_path}")
        except Exception as e:
            logger.exception(f"Error writing file {file_path}: {e}")
            return ToolResult(content=f"Error writing file: {e}", is_error=True)
