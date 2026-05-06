class FlopsError(Exception):
    """Base class for all Flops-related errors."""


class ToolError(FlopsError):
    """Raised when a tool execution fails. The message becomes the ToolResult content."""


class PermissionDenied(ToolError):
    """Raised when a tool is blocked by permission restrictions (strict mode, workspace, etc.)."""
