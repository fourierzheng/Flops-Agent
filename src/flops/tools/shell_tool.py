from __future__ import annotations

import asyncio
import os
import re

import bashlex

from pydantic import BaseModel, Field

from flops.logger import logger
from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class ShellParams(BaseModel):
    command: str = Field(description="Shell command to execute.")
    cwd: str | None = Field(default=None, description="Working directory for the command.")


# Default shell command timeout in seconds
SHELL_TIMEOUT = 180

# Dangerous command names that are always blocked (regardless of arguments)
DANGEROUS_COMMANDS = {
    # File destruction
    "rm",
    "dd",
    "mkfs",
    "shred",
    # System modification
    "chown",
    "chgrp",
    # Privilege escalation
    "passwd",
    # Network attacks
    "nc",
    "netcat",
    "ncat",
    # Security tools (offensive)
    "msfvenom",
    "msfconsole",
    "sqlmap",
    "nikto",
    "hydra",
    "john",
    "hashcat",
    "aircrack",
    # Config destruction
    "sysctl",
    "modprobe",
    "visudo",
}

# Dangerous command prefixes (commands starting with these)
DANGEROUS_COMMAND_PREFIXES = [
    "mkfs.",
]

# Commands with dangerous argument patterns
DANGEROUS_ARG_PATTERNS: dict[str, list[str]] = {
    "chmod": [
        r"-R?\s*0*777\s*[/~]",
        r"-R?\s*[0]?[0]?[0]\s*\.",
        r"^\s*0*777\s+[/~]",
        r"^\s*[0]?[0]?[0]\s+\.",
    ],
    "sudo": [
        r"^\s*su\s*$",
    ],
    "kill": [
        r"-9\s+-1",
    ],
    "killall": [],
    "pkill": [
        r"-9",
    ],
    "crontab": [
        r"-r",
    ],
    "docker": [
        r"system\s+prune",
        r"rm\s+-f\s+",
    ],
    "kubectl": [
        r"delete\s+all\s+--all",
    ],
    "ssh": [
        r"-[LRD]\s+\d+:\w+:\d+",
    ],
}

# Restricted arguments for specific commands (always blocked on those commands)
RESTRICTED_ARGS = {
    "curl": ["-T", "--upload-file", "--data-urlencode"],
    "wget": ["--post-data", "--post-file"],
}

# Dangerous patterns checked at the string level (for things bashlex can't easily catch)
STRING_PATTERNS = [
    # Fork bombs
    r":\(\)\s*\{[^}]*\:\&",
    r":\s*\{\s*:\s*\|\s*:\s*\&",
    # Write to system files via redirect
    r">\s*/etc/(passwd|shadow|group|sudoers)",
    # Download and pipe to shell
    r"(curl|wget)\s+[^|]*\|\s*(sh|bash|python|perl)",
    # Base64 decode piped to shell
    r"base64\s+-d[^|]*\|\s*(sh|bash|python|perl)",
    r"echo\s+['\"][A-Za-z0-9+/=]{20,}['\"]\s*\|\s*base64\s*\|\s*(sh|bash|python|perl)",
    # SQL injection via command
    r";\s*(drop|delete|truncate|alter)\s+",
    r"\|\s*(drop|delete|truncate|alter)\s+",
    # Port forwarding abuse
    r"ssh\s+-[LRD]\s+\d+:\d+:\d+",
]


def _get_command_name(node) -> str | None:
    """Extract the command name from a bashlex AST node."""
    if node.kind == "command":
        for part in node.parts:
            if part.kind == "word":
                word = part.word
                if word and not word.startswith("-"):
                    return word
    elif node.kind == "function":
        name_node = getattr(node, "name", None)
        if name_node is not None:
            return getattr(name_node, "word", None)
    return None


def _walk_ast(nodes) -> list[str]:
    """Walk bashlex AST and collect all command names."""
    commands = []
    for node in nodes:
        cmd = _get_command_name(node)
        if cmd:
            commands.append(cmd)
        # Recurse into sub-nodes
        for child in getattr(node, "parts", []):
            commands.extend(_walk_ast([child]))
    return commands


def analyze_command(command: str) -> tuple[bool, str]:
    """
    Analyze a command for dangerous patterns using bashlex AST parsing.

    Returns:
        (is_safe, reason) - if is_safe is False, reason contains the warning message
    """
    if not command or not command.strip():
        return False, "Empty command"

    # Parse with bashlex
    try:
        ast = bashlex.parse(command)
    except Exception as e:
        return False, f"Failed to parse command: {e}"

    # Extract all command names from AST
    cmd_names = _walk_ast(ast)

    # Check each command name against dangerous list
    for cmd_name in cmd_names:
        if cmd_name in DANGEROUS_COMMANDS:
            return False, f"Dangerous command: {cmd_name}"
        # Check dangerous prefixes
        for prefix in DANGEROUS_COMMAND_PREFIXES:
            if cmd_name.startswith(prefix):
                return False, f"Dangerous command prefix: {prefix}"

    # Check dangerous argument patterns via AST
    for node in ast:
        result = _check_node_dangerous_args(node)
        if not result[0]:
            return result

    # String-level checks for patterns bashlex can't easily catch
    for pattern in STRING_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Dangerous pattern detected"

    # Check restricted arguments
    for cmd_name in cmd_names:
        if cmd_name in RESTRICTED_ARGS:
            for restricted in RESTRICTED_ARGS[cmd_name]:
                if restricted in command:
                    return False, f"Restricted argument '{restricted}' in command '{cmd_name}'"

    # Check file:// protocol with sensitive paths (block curl/wget reading system files)
    for cmd_name in cmd_names:
        if cmd_name in ("curl", "wget"):
            if re.search(r"file://[^|]*?(/etc/|/root/|/proc/|/sys/|/var/|/bin/|/sbin/)", command):
                return False, f"Accessing sensitive path via file:// in '{cmd_name}'"

    return True, "Command accepted"


def _check_node_dangerous_args(node) -> tuple[bool, str]:
    """Recursively check AST node for dangerous argument patterns."""
    if node.kind == "command":
        words = [p for p in node.parts if p.kind == "word"]
        if len(words) >= 1:
            cmd_name = words[0].word
            args_str = " ".join(w.word for w in words[1:])

            if cmd_name in DANGEROUS_ARG_PATTERNS:
                for pattern in DANGEROUS_ARG_PATTERNS[cmd_name]:
                    if re.search(pattern, args_str):
                        return False, f"Dangerous argument pattern in '{cmd_name}': {pattern}"

    # Recurse
    for child in getattr(node, "parts", []):
        result = _check_node_dangerous_args(child)
        if not result[0]:
            return result

    return True, ""


@tool
class ShellTool(Tool):
    """Run a shell command and return the content."""

    params_model = ShellParams

    def render(self, tool_input: dict) -> str:
        command = tool_input.get("command")
        first_line = command.splitlines()[0] if command else "<no command>"
        return f"💻 Shell({first_line})"

    async def execute(self, ctx: ToolContext, params: ShellParams) -> ToolResult:
        command = params.command
        cwd = params.cwd
        work_dir = cwd or ctx.cwd

        # Analyze command for dangerous patterns
        is_safe, reason = analyze_command(command)
        if not is_safe:
            logger.warning(f"Blocked dangerous command: {command[:100]}... Reason: {reason}")
            return ToolResult(
                content=f"Command blocked for safety: {reason}\nCommand: {command}",
                is_error=True,
            )

        logger.info(f"Shell command: {command}")
        logger.debug(f"Shell working directory: {work_dir}")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=work_dir,
                preexec_fn=os.setpgrp,
            )

            try:
                stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=SHELL_TIMEOUT)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning(f"Shell command terminated: {command}")
                proc.kill()
                await proc.wait()
                return ToolResult(content="Command terminated", is_error=True)

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            logger.debug(f"Shell exit code: {proc.returncode}")
            return ToolResult(content=stdout, is_error=proc.returncode != 0)
        except Exception as e:
            logger.exception(f"Error executing shell command: {e}")
            return ToolResult(content=f"Error: {type(e).__name__}: {e}", is_error=True)
