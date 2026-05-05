import builtins as _builtins_mod
import contextlib
import io
import threading

from pydantic import BaseModel, Field

from flops.logger import logger
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


def _safe_import(name: str, *args, **kwargs):
    """Custom __import__ that only allows whitelisted modules."""
    top_level = name.split(".", maxsplit=1)[0]
    if top_level not in _SAFE_MODULES:
        raise ImportError(
            f"Module '{name}' is not allowed for security reasons. "
            f"Allowed modules: {', '.join(sorted(_SAFE_MODULES))}"
        )
    return _builtins_mod.__import__(name, *args, **kwargs)


def _build_safe_builtins() -> dict:
    """Build a restricted builtins dict with a whitelisted __import__."""
    builtins = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
    safe = {k: v for k, v in builtins.items() if k not in _BLOCKED_BUILTINS}
    safe["__import__"] = _safe_import
    return safe


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
        logger.info(f"Executing Python code (timeout={timeout}s)")
        logger.debug(f"Python code:\n{code}")
        result = {}
        exception_occurred = {}

        def run_code():
            try:
                output = io.StringIO()
                errors = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    namespace = {
                        "__name__": "__main__",
                        "cwd": ctx.cwd,
                        "__builtins__": _build_safe_builtins(),
                    }
                    exec(code, namespace)
                result["output"] = output.getvalue()
                result["errors"] = errors.getvalue()
            except Exception as e:
                exception_occurred["exc"] = e

        try:
            thread = threading.Thread(target=run_code)
            thread.start()
            thread.join(timeout=timeout)

            if thread.is_alive():
                logger.warning(f"Python execution timed out after {timeout} seconds")
                return ToolResult(
                    content=f"Error: Execution timed out after {timeout} seconds", is_error=True
                )

            if exception_occurred:
                e = exception_occurred["exc"]
                logger.warning(f"Python execution error: {type(e).__name__}: {e}")
                if isinstance(e, SyntaxError):
                    return ToolResult(content=f"Syntax Error: {e}", is_error=True)
                return ToolResult(content=f"Error: {type(e).__name__}: {e}", is_error=True)

            logger.info("Python code executed successfully")
            result_parts = []
            if result.get("output"):
                result_parts.append(result["output"])
            if result.get("errors"):
                result_parts.append(f"Stderr: {result['errors']}")

            return ToolResult(content="".join(result_parts) if result_parts else "(no output)")
        except Exception as e:
            logger.exception(f"Unexpected error executing Python code: {e}")
            return ToolResult(content=f"Error: {type(e).__name__}: {e}", is_error=True)
