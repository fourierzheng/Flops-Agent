from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from flops.logger import logger
from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class GrepParams(BaseModel):
    pattern: str = Field(description="Regex pattern to search for.")
    path: str | None = Field(default=None, description="File or directory to search.")
    glob: str = Field(
        default="*", description="Glob pattern to match files when searching directories."
    )
    files_only: bool = Field(default=False, description="If true, return only matching file paths.")


_DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".eggs",
    "*.egg-info",
    "build",
    "dist",
    ".idea",
    ".vscode",
}


def _should_exclude(dir_name: str) -> bool:
    return dir_name in _DEFAULT_EXCLUDE_DIRS or dir_name.endswith(".egg-info")


@tool
class GrepTool(Tool):
    """Search for a pattern in files.

    Supports both single file search and recursive directory search.
    Returns matching file paths, line numbers, and line content.
    """

    params_model = GrepParams

    def render(self, tool_input: dict) -> str:
        return f"🔍 Grep({tool_input.get('pattern', '<no pattern>')})"

    async def execute(self, ctx: ToolContext, params: GrepParams) -> ToolResult:
        """Search files for a regex pattern.

        Args:
            pattern: Regex pattern to search for.
            path: File or directory to search in. Defaults to ctx.cwd.
                  If path is a file, searches only that file.
                  If path is a directory, recursively searches with glob pattern.
            glob: File glob pattern to filter files (e.g., "*.py"). Defaults to "*".
                  Only used when path is a directory.
            files_only: If True, returns only the list of matching file paths.
                        If False (default), returns matching lines with file path and line number.
        """
        pattern = params.pattern
        path = params.path
        glob = params.glob
        files_only = params.files_only
        logger.info(f"Grep search: pattern='{pattern}', path={path or ctx.cwd}, glob={glob}")
        search_path = Path(path or ctx.cwd).resolve()

        # Security: prevent escaping outside the workspace
        workspace = Path(ctx.cwd).resolve()
        try:
            search_path.relative_to(workspace)
        except ValueError:
            logger.warning(f"Search path '{search_path}' is outside workspace '{workspace}'")
            return ToolResult(
                content=f"Error: search path '{search_path}' is outside the workspace '{workspace}'",
                is_error=True,
            )

        if not search_path.exists():
            logger.error(f"Search path does not exist: {search_path}")
            return ToolResult(
                content=f"Error: path does not exist: {search_path}",
                is_error=True,
            )

        try:
            compiled = re.compile(pattern)
        except re.error as e:
            logger.warning(f"Invalid regex pattern: {e}")
            return ToolResult(
                content=f"Error: invalid regex pattern: {e}",
                is_error=True,
            )

        # Single file mode: search only that file
        if search_path.is_file():
            return self._grep_file(search_path, compiled, files_only, workspace)

        # Directory mode: recursive search
        matches = []
        files_with_matches = set()
        files_scanned = 0

        for file_path in search_path.rglob(glob):
            if not file_path.is_file():
                continue
            if file_path.is_symlink():
                continue
            if any(_should_exclude(part) for part in file_path.relative_to(search_path).parts[:-1]):
                continue

            files_scanned += 1
            result = self._grep_file(file_path, compiled, files_only, workspace)
            if result.is_error:
                continue

            if result.content and result.content != "No matches found.":
                files_with_matches.add(file_path.relative_to(workspace).as_posix())
                if not files_only:
                    matches.extend(result.content.split("\n"))

        logger.debug(
            f"Scanned {files_scanned} files, found {len(files_with_matches)} files with matches"
        )
        if not matches and not files_with_matches:
            logger.info("No matches found")
            return ToolResult(content="No matches found.")

        if files_only:
            content = "\n".join(sorted(files_with_matches))
        else:
            content = "\n".join(matches)

        # Truncate if extremely large
        if len(content) > 20000:
            content = content[:20000] + (
                f"\n\n... [{len(matches)} total matches, output truncated]"
            )
            logger.debug(f"Output truncated: {len(content)} characters")

        logger.info(f"Grep completed: {len(files_with_matches)} files, {len(matches)} matches")
        return ToolResult(content=content)

    def _grep_file(
        self,
        file_path: Path,
        compiled: re.Pattern,
        files_only: bool,
        workspace: Path,
    ) -> ToolResult:
        """Search a single file for pattern matches."""
        matches = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if compiled.search(line):
                        rel_path = file_path.relative_to(workspace)
                        if files_only:
                            return ToolResult(content=rel_path.as_posix())
                        line_content = line.rstrip("\r\n")
                        matches.append(f"{rel_path.as_posix()}:{line_no}: {line_content}")
        except (UnicodeDecodeError, OSError):
            return ToolResult(content="", is_error=False)

        if not matches:
            return ToolResult(content="No matches found.")

        return ToolResult(content="\n".join(matches))
