import asyncio
import os
import sys
import tempfile

from pydantic import BaseModel, Field

from flops.error import ToolError, PermissionDenied
from flops.logger import logger
from flops.schemas import Permission
from flops.tools.tool import ToolContext, Tool, ToolResult, tool

# Builtins that enable arbitrary code execution or system escape — explicitly blocked
_BLOCKED_BUILTINS = {"eval", "exec", "compile", "breakpoint", "exit", "quit", "help"}

# Modules that are safe to import in the sandbox (no I/O, no system access, no networking)
_SAFE_MODULES = {
    "array",
    "base64",
    "binascii",
    "bisect",
    "collections",
    "copy",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "heapq",
    "itertools",
    "json",
    "math",
    "numbers",
    "operator",
    "pprint",
    "random",
    "re",
    "reprlib",
    "statistics",
    "string",
    "struct",
    "textwrap",
    "time",
    "typing",
    "uuid",
}


def _build_sandbox_code(user_code: str) -> str:
    """Wrap user code in a sandbox that restricts builtins and module imports."""
    blocked_repr = repr(sorted(_BLOCKED_BUILTINS))
    safe_repr = repr(sorted(_SAFE_MODULES))
    code_repr = repr(user_code)
    return f"""\
import builtins as _b
_BLOCKED = set({blocked_repr})
_SAFE = set({safe_repr})

def _safe_import(name, *args, **kwargs):
    top = name.split(".", 1)[0]
    if top not in _SAFE:
        raise ImportError(
            f"Module '{{name}}' is not allowed for security reasons. "
            f"Allowed modules: {{', '.join(sorted(_SAFE))}}"
        )
    return _b.__import__(name, *args, **kwargs)

_safe_builtins = {{k: v for k, v in _b.__dict__.items() if k not in _BLOCKED}}
_safe_builtins["__import__"] = _safe_import

_exec_globals = {{"__builtins__": _safe_builtins, "__name__": "__main__"}}
exec({code_repr}, _exec_globals)
"""


class PythonParams(BaseModel):
    code: str = Field(description="Python code to execute.")
    timeout: int = Field(default=30, description="Maximum execution time in seconds.")


@tool
class PythonTool(Tool):
    """Execute Python code and return the output"""

    params_model = PythonParams

    def render(self, tool_input: dict) -> str:
        code = tool_input.get("code", "")
        first_line = code.splitlines()[0] if code else "<no code>"
        return f"🐍 Python({first_line})"

    async def execute(self, ctx: ToolContext, params: PythonParams) -> ToolResult:
        code = params.code
        timeout = params.timeout

        # Strict mode: Python is disabled
        if ctx.permission == Permission.STRICT:
            logger.warning("Python blocked in strict mode")
            raise PermissionDenied(
                "Python is disabled in 'strict' permission mode. "
                'Set `tool.permission` to `"standard"` or `"full"` in config.json to enable Python execution.'
            )

        logger.info(f"Executing Python code (timeout={timeout}s)")
        logger.debug(f"Python code:\n{code}")

        # Wrap user code in sandbox that restricts builtins and imports
        safe_code = _build_sandbox_code(code)

        # Write sandbox-wrapped code to a temp file
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            tmp.write(safe_code)
            tmp.close()

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                tmp.name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=ctx.cwd,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning(f"Python execution timed out after {timeout}s")
                proc.kill()
                await proc.wait()
                raise ToolError(f"Error: Execution timed out after {timeout} seconds")

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            if proc.returncode != 0:
                logger.warning(f"Python exited with code {proc.returncode}")
                # stderr usually contains the traceback
                raise ToolError(stderr or f"Error: exit code {proc.returncode}")

            logger.info("Python code executed successfully")
            result_parts = []
            if stdout:
                result_parts.append(stdout)
            if stderr:
                result_parts.append(f"Stderr: {stderr}")
            return ToolResult(content="".join(result_parts) if result_parts else "(no output)")

        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
