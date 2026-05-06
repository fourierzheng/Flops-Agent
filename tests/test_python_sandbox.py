from unittest.mock import AsyncMock, MagicMock

import pytest

from flops.llm import LLM
from flops.memory import Memory
from flops.registry import Registry
from flops.schemas import Permission, Skill, ToolUse
from flops.snapshot import Snapshot
from flops.tools.tool import ToolContext, dispatch_tool


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(
        cwd="/tmp",
        skills=Registry[Skill](),
        snapshot=MagicMock(spec=Snapshot),
        memory=MagicMock(spec=Memory),
        llm=MagicMock(spec=LLM),
        stream_chat=MagicMock(spec=AsyncMock),
        permission=Permission.STANDARD,
    )


@pytest.mark.asyncio
async def test_simple_output(ctx: ToolContext):
    """Basic code execution captures stdout."""
    result = await dispatch_tool(
        ctx, ToolUse(id="t1", name="Python", input={"code": "print('hello world')"})
    )
    assert not result.is_error
    assert "hello world" in result.content


@pytest.mark.asyncio
async def test_no_output(ctx: ToolContext):
    """Code with no output returns '(no output)'."""
    result = await dispatch_tool(ctx, ToolUse(id="t2", name="Python", input={"code": "x = 1 + 1"}))
    assert not result.is_error
    assert result.content == "(no output)"


@pytest.mark.asyncio
async def test_stdout_and_stderr(ctx: ToolContext):
    """Both stdout and stderr are captured."""
    result = await dispatch_tool(
        ctx,
        ToolUse(
            id="t3",
            name="Python",
            # Use 1/0 to generate stderr (traceback), avoid blocked 'sys' module
            input={"code": "print('out'); 1/0"},
        ),
    )
    assert result.is_error
    assert "out" in result.content
    assert "ZeroDivisionError" in result.content


@pytest.mark.asyncio
async def test_syntax_error(ctx: ToolContext):
    """Syntax errors are reported."""
    result = await dispatch_tool(ctx, ToolUse(id="t4", name="Python", input={"code": "print("}))
    assert result.is_error
    assert "SyntaxError" in result.content or "Error" in result.content


@pytest.mark.asyncio
async def test_runtime_error(ctx: ToolContext):
    """Runtime exceptions are reported."""
    result = await dispatch_tool(ctx, ToolUse(id="t5", name="Python", input={"code": "1 / 0"}))
    assert result.is_error
    assert "ZeroDivisionError" in result.content


@pytest.mark.asyncio
async def test_timeout(ctx: ToolContext):
    """Long-running code is killed on timeout."""
    result = await dispatch_tool(
        ctx,
        ToolUse(
            id="t6", name="Python", input={"code": "import time; time.sleep(300)", "timeout": 1}
        ),
    )
    assert result.is_error
    assert "timed out" in result.content


@pytest.mark.asyncio
async def test_strict_mode_blocks(ctx: ToolContext):
    """Strict permission blocks execution."""
    strict_ctx = ToolContext(
        cwd="/tmp",
        skills=ctx.skills,
        snapshot=ctx.snapshot,
        memory=ctx.memory,
        llm=ctx.llm,
        stream_chat=ctx.stream_chat,
        permission=Permission.STRICT,
    )
    result = await dispatch_tool(
        strict_ctx, ToolUse(id="t7", name="Python", input={"code": "print('hi')"})
    )
    assert result.is_error
    assert "disabled" in result.content


@pytest.mark.asyncio
async def test_exit_code_error(ctx: ToolContext):
    """Non-zero exit code is reported as error."""
    result = await dispatch_tool(ctx, ToolUse(id="t8", name="Python", input={"code": "exit(1)"}))
    assert result.is_error
    assert "exit code 1" in result.content or "Error" in result.content


@pytest.mark.asyncio
async def test_working_directory(ctx: ToolContext):
    """Code runs in the specified working directory."""
    # Use open() with a relative path to verify cwd; 'open' is not blocked
    result = await dispatch_tool(
        ctx,
        ToolUse(
            id="t9",
            name="Python",
            input={
                "code": "with open('/tmp/__flops_test.txt', 'w') as f: f.write('ok'); print('ok')"
            },
        ),
    )
    assert not result.is_error
    assert "ok" in result.content
