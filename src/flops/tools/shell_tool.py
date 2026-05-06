import asyncio
import os
import re
from pathlib import Path

import bashlex
from pydantic import BaseModel, Field

from flops.logger import logger
from flops.schemas import Permission
from flops.error import ToolError, PermissionDenied
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

# Interpreters blocked in shell under STANDARD mode (use the dedicated tool instead)
BLOCKED_INTERPRETERS = {
    "python",
    "python3",
}


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


def _check_cd_workspace(command: str, work_dir: str, workspace_root: Path) -> tuple[bool, str]:
    """
    Walk bashlex AST in execution order, tracking `cd` to ensure no workspace escape.

    Returns (is_safe, reason).
    """
    try:
        ast = bashlex.parse(command)
    except Exception:
        return True, ""  # syntax errors caught by analyze_command

    virtual_cwd = Path(work_dir).resolve()

    def _resolve(target: str) -> Path | None:
        """Resolve a cd target to an absolute Path, or None if unresolvable."""
        if target == "-":
            return None  # previous dir, can't track
        if "$" in target or "`" in target or "$(" in target:
            return None  # variable/command substitution
        if target.startswith("~"):
            return Path(os.path.expanduser(target)).resolve()
        if target.startswith("/"):
            return Path(target).resolve()
        return (virtual_cwd / target).resolve()

    def _walk_nodes(nodes: list, subshell: bool = False) -> tuple[bool, str]:
        nonlocal virtual_cwd
        for node in nodes:
            kind = node.kind

            # command node: check for cd
            if kind == "command":
                words = [p for p in node.parts if p.kind == "word"]
                if words and words[0].word == "cd":
                    # first non-flag argument is the target; None means cd with no args (= ~)
                    target = next(
                        (
                            w.word
                            for w in words[1:]
                            if not (w.word.startswith("-") and w.word != "-")
                        ),
                        None,
                    )
                    if target == "-":
                        return False, "'cd -' would escape workspace (unable to track previous dir)"
                    resolved = _resolve(target or "~")
                    if resolved is None:
                        return False, f"Unresolvable cd target: {target or '~'}"
                    if not resolved.is_relative_to(workspace_root):
                        return False, f"cd to '{target or '~'}' would escape workspace"
                    if not subshell:
                        virtual_cwd = resolved

            # recurse into compound structures
            for lst in getattr(node, "list", []):
                r = _walk_nodes([lst], subshell)
                if not r[0]:
                    return r
            for part in getattr(node, "parts", []):
                if hasattr(part, "kind"):
                    r = _walk_nodes([part], subshell or kind == "subshell")
                    if not r[0]:
                        return r

        return True, ""

    return _walk_nodes(ast)


def _has_blocked_interpreter(command: str) -> str | None:
    """Check if command runs a blocked interpreter (python) in a non-FULL context.

    Returns the blocked command name, or None if allowed.
    """
    try:
        ast = bashlex.parse(command)
    except Exception:
        return None
    for name in _walk_ast(ast):
        if name in BLOCKED_INTERPRETERS:
            return name
        # python3.11, python312, etc.
        if name.startswith("python"):
            rest = name[6:]
            if rest and rest.replace(".", "").isdigit():
                return name
    return None


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

        # Strict mode: shell is disabled
        if ctx.permission == Permission.STRICT:
            logger.warning(f"Shell blocked in strict mode: {command[:100]}...")
            raise PermissionDenied(
                "Shell is disabled in 'strict' permission mode. "
                'Set `tool.permission` to `"standard"` or `"full"` in config.json to enable shell access.'
            )

        # Workspace check for standard mode
        if ctx.permission != Permission.FULL:
            cwd_path = Path(ctx.cwd).resolve()
            work_path = Path(work_dir).resolve()
            if not work_path.is_relative_to(cwd_path):
                logger.warning(f"Shell blocked outside workspace: {work_dir}")
                raise PermissionDenied(
                    f"Cannot run shell outside workspace: {work_dir}\n"
                    f"Current permission level is '{ctx.permission.value}'. "
                    f'Set `tool.permission` to `"full"` in config.json to allow this.'
                )

            # Check that cd commands in the command stay within workspace
            cd_safe, cd_reason = _check_cd_workspace(command, work_dir, cwd_path)
            if not cd_safe:
                logger.warning(f"Shell blocked cd escape: {command[:100]}... Reason: {cd_reason}")
                raise PermissionDenied(
                    f"cd blocked for workspace safety: {cd_reason}\n"
                    f"Current permission level is '{ctx.permission.value}'. "
                    f'Set `tool.permission` to `"full"` in config.json to allow this.'
                )

            # Block interpreters that bypass workspace checks (python etc.)
            blocked = _has_blocked_interpreter(command)
            if blocked:
                logger.warning(f"Shell blocked interpreter: {command[:100]}...")
                raise PermissionDenied(
                    f"'{blocked}' is not allowed in shell under '{ctx.permission.value}' permission. "
                    f"Use the Python tool instead, which has proper sandbox restrictions.\n"
                    f'Set `tool.permission` to `"full"` in config.json to allow this.'
                )

        # Analyze command for dangerous patterns
        is_safe, reason = analyze_command(command)
        if not is_safe:
            logger.warning(f"Blocked dangerous command: {command[:100]}... Reason: {reason}")
            raise ToolError(f"Command blocked for safety: {reason}\nCommand: {command}")

        logger.info(f"Shell command: {command}")
        logger.debug(f"Shell working directory: {work_dir}")
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
            raise ToolError("Command terminated")

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        logger.debug(f"Shell exit code: {proc.returncode}")
        return ToolResult(content=stdout, is_error=proc.returncode != 0)
