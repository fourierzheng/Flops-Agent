

# Flops Agent - Developer Guide

## Overview

Flops Agent is an LLM-powered intelligent CLI agent tool supporting multi-turn conversations, file operations, shell command execution, and web access. It uses an event-driven, async-first architecture with a tool system that allows the LLM to call various functions.

## Tech Stack

- **Language**: Python 3.13+
- **LLM APIs**: Anthropic (Claude format), OpenAI compatible
- **Key Dependencies**: anthropic, openai, prompt_toolkit, pydantic, rich, httpx, pyyaml, bashlex
- **Build System**: hatchling
- **Package Manager**: uv
- **Testing**: pytest, pytest-asyncio
- **Code Formatting**: Black (line-length: 100, target-version: py313)

## Directory Structure

```
src/flops/                 # Main package
├── __init__.py           # Package exports (main, Engine, Config)
├── __main__.py           # Entry point for `python -m flops`
├── agent.py              # Agent - core chat loop with tool execution
├── cli.py                # CLI UI (rich display, prompts, event handling)
├── command.py            # Slash commands (/init, /session, /model, /help, etc.)
├── compact.py            # Safe-split message compression via LLM summarization
├── config.py             # Config dataclasses and JSON loading
├── const.py              # Path constants (CONFIG_DIR, SESSIONS_DIR, etc.)
├── engine.py             # Engine - orchestrates all components, system prompt
├── error.py              # Error types
├── event.py              # ChatEvent dataclass hierarchy (TextDeltaEvent, ToolUseEvent, etc.)
├── llm.py                # AnthropicLLM and OpenAILLM implementations
├── logger.py             # Logging configuration
├── memory.py             # Memory system (SQLite storage, auto-distillation, promotion)
├── registry.py           # Generic registry for models, skills, commands
├── schemas.py            # Core dataclasses (TextBlock, ToolUse, ToolResult, StopReason, etc.)
├── session.py            # Session management with file logging, undo, compression
├── skill.py              # Skill loading and parsing from SKILL.md files
├── snapshot.py           # File backup/restore for undo (/undo command)
├── state.py              # Runtime state (model, session_id, workspace)
└── tools/                # Tool implementations
    ├── tool.py           # Base Tool class and @tool decorator
    ├── agent_tool.py     # agent tool (sub-agent delegation)
    ├── file_edit_tool.py # fileedit tool
    ├── file_read_tool.py # fileread tool
    ├── file_write_tool.py# filewrite tool
    ├── glob_tool.py      # glob tool
    ├── grep_tool.py      # grep tool
    ├── list_tool.py      # list tool
    ├── mem_tool.py       # mem tool (long-term memory)
    ├── python_tool.py    # python tool
    ├── rm_tool.py        # rm tool (delete files/directories)
    ├── shell_tool.py     # shell tool (with bashlex safety checks)
    ├── skill_tool.py     # skill tool
    ├── web_tool.py       # web tool
    └── weather_tool.py   # weather tool
skills/                   # Skill definitions (YAML frontmatter + markdown)
├── test_skill/
└── project_understanding/
tests/                    # Test files (pytest)
├── test_compact.py
├── test_config.py
├── test_session.py
├── test_shell_safety.py
└── test_tool.py
sessions/                  # Session log files
config.json               # Default config example
pyproject.toml            # Project metadata
```

## Getting Started

### Installation

```bash
# Using uv (recommended)
uv sync

# Or install globally
pip install -e .
```

### Configuration

Create `~/.config/flops/config.json` or use project-level `config.json`:

```json
{
    "name": "flops",
    "providers": {
        "MiniMax": {
            "api_format": "anthropic",
            "api_key": "YOUR-API-KEY",
            "base_url": "https://api.minimaxi.com/anthropic",
            "models": {
                "MiniMax-M2.7": {
                    "max_tokens": 8192,
                    "context_size": 200000,
                    "thinking": true,
                    "request_timeout": 600
                }
            }
        }
    },
    "agent": {
        "model": "MiniMax:MiniMax-M2.7",
        "max_turns": 200,
        "workspace": "/path/to/workspace"
    },
    "memory": {
        "distill_interval": 10,
        "enabled": true
    },
    "log": { "level": "INFO" },
    "skills": { "paths": ["skills"] }
}
```

### Running

```bash
# Development (uses uv)
uv run python -m flops

# Or with config
uv run python -m flops --config /path/to/config.json

# Installed globally
flops
```

## Development Guide

### Running Tests

```bash
# All tests
pytest tests/

# Single test file
pytest tests/test_config.py -v

# With output
pytest -v tests/
```

### Key Commands

- `uv sync` - Install dependencies
- `uv python pin 3.13` - Set Python version
- `uv run python -m flops` - Run without installing

### Code Style

- **Formatter**: Black with line-length 100
- **Target Python**: 3.13
- **Import order**: stdlib → third-party → local (enforced by Black)
- **Type hints**: Use throughout, especially for async generators

## Architecture

### Event-Driven Flow

```
User Input → Engine.run()
              ├── if /command: Engine.handle() → Command.handle() → ChatEvent[]
              └── else:       Engine.chat()
                  → Compactor.need_compact()? → asyncio.create_task(compact)
                  → Memory.need_distill()? → asyncio.create_task(distill)
                  → Conversation(session.messages)
                  → Agent.chat(context, conversation, user_input)
                    → loop:
                      → LLM.stream() → TextDeltaEvent / ThinkingEvent / ToolUseEvent / StopEvent
                      → if ToolUseEvent: dispatch_tool() → ToolResultEvent | ToolOutputEvent
                      → until StopEvent.reason ∉ {TOOL_CALL, CONTINUE, MAX_TOKENS}
                  → await compact (if started) + await distill (if started)
                  → yield NoticeEvent (if compressed or distilled)
                  → session.add_conversation(conversation)
```

### Core Classes

| Class | File | Purpose |
|-------|------|---------|
| `Engine` | engine.py | Orchestrates all components, manages state |
| `Agent` | agent.py | Main chat loop with tool execution |
| `AnthropicLLM` / `OpenAILLM` | llm.py | LLM API adapters with retry logic |
| `Session` | session.py | Conversation history with file persistence, undo, restore |
| `Conversation` | session.py | Per-turn message accumulation |
| `Compactor` | compact.py | Automatic conversation compression (safe-split + LLM summarization) |
| `Memory` | memory.py | Long-term memory with SQLite storage, auto-distillation, FLOPS.md promotion |
| `Snapshot` | snapshot.py | File backup/restore for undo operations |

### Context Objects

- **AgentContext**: system_prompt, llm, skills, snapshot, memory, [tools] - passed to agent.chat()
- **ToolContext**: cwd, skills, snapshot, memory, llm, stream_chat - passed to tool.execute()
- **CommandContext**: state, models, session, llm, agent, compactor, snapshot, skills, memory - for slash commands
- **Compactor**: need_compact(llm, session) / compact(llm, session) - for compression

### Event Types

| Event | Description |
|-------|-------------|
| `TextDeltaEvent` | Text content delta from LLM stream |
| `ThinkingEvent` | Extended thinking content (Claude) |
| `ToolUseEvent` | LLM requests tool execution |
| `ToolResultEvent` | Result of a tool execution |
| `ToolOutputEvent` | Streaming output from sub-agent execution |
| `StopEvent` | LLM stopped (COMPLETED / TOOL_CALL / MAX_TOKENS / MAX_TURNS / CONTINUE / INTERRUPT) |
| `UsageEvent` | Token usage data |
| `LineEvent` | Formatted text line (commands) |
| `NoticeEvent` | Notification message (e.g., compression done) |
| `ErrorEvent` | Error occurred |
| `ExitEvent` | Exit program request |

### Tool System

Tools are implemented by:
1. Subclassing `Tool` from `flops.tools.tool`
2. Defining a `params_model` using Pydantic `BaseModel`
3. Adding docstring (used as tool description)
4. Decorating with `@tool`

Example:
```python
from pydantic import BaseModel, Field
from flops.tools.tool import Tool, tool, ToolContext, ToolResult

class MyParams(BaseModel):
    arg1: str = Field(description="Description for LLM")

@tool
class MyTool(Tool):
    """Short docstring - used as tool description."""
    params_model = MyParams

    def execute(self, ctx: ToolContext, params: MyParams) -> ToolResult:
        return ToolResult(content="result")
```

Tool naming convention: class names must end with `Tool` (e.g., `FileEditTool`). The `@tool` decorator strips the suffix via `re.sub(r"Tool$", "", cls.__name__)` to get the tool name (e.g., `fileedit`).

Tools can be filtered at the Agent level via `AgentContext.tools` — passing a list of tool names restricts which tools the LLM can use.

### Skills System

Skills are defined in `SKILL.md` files with YAML frontmatter:
```markdown
---
name: skill_name
description: Brief description
---

# Skill Name

## Instructions
...
```

Loaded via `load_skills()` into a `Registry[Skill]`.

## Key Files

| File | Purpose |
|------|---------|
| `src/flops/__main__.py` | Entry point for `python -m flops` |
| `src/flops/cli.py` | Main CLI loop with async_main() |
| `src/flops/engine.py` | Engine class - system initialization |
| `src/flops/agent.py` | Agent - turn loop and tool dispatch |
| `src/flops/llm.py` | AnthropicLLM and OpenAILLM with streaming and retry |
| `src/flops/memory.py` | Memory system with SQLite storage, auto-distillation, FLOPS.md promotion |
| `src/flops/compact.py` | Safe-split compression with LLM summarization |
| `src/flops/command.py` | Slash commands (init, session, model, undo, remember, etc.) |
| `src/flops/config.py` | Config loading and validation |
| `src/flops/const.py` | Path and timeout constants |
| `src/flops/error.py` | Error types |
| `src/flops/event.py` | ChatEvent dataclass hierarchy |
| `src/flops/schemas.py` | Core dataclasses (TextBlock, ToolUse, etc.) |
| `src/flops/session.py` | Session persistence, undo, restore |
| `src/flops/snapshot.py` | File backup/restore for undo |
| `src/flops/state.py` | Runtime state (model, session_id, workspace) |
| `src/flops/skill.py` | Skill loading and parsing from SKILL.md |
| `src/flops/tools/tool.py` | @tool decorator and Tool base class |

## Notes for AI

- **Async throughout**: Most operations are async; use `async for` when iterating over event streams
- **Tool naming**: Tool class names must end with `Tool`. The `@tool` decorator strips this suffix via regex to get the tool name
- **Tool dispatch**: Use `dispatch_tool(ctx, tool_use)` from `flops.tools` to execute tools
- **Tool filtering**: Pass `tools=["Grep", "FileRead"]` to `AgentContext.tools` to restrict which tools the LLM can call
- **Context passing**: Always pass appropriate context objects to async functions
- **Event types**: All events are now `@dataclass`. Key events: `TextDeltaEvent`, `ThinkingEvent`, `ToolUseEvent`, `ToolResultEvent`, `ToolOutputEvent`, `StopEvent`, `UsageEvent`, `LineEvent`, `NoticeEvent`, `ErrorEvent`, `ExitEvent`
- **Session compression**: `Compactor.need_compact()` checks if token usage exceeds 70% of context window. Compression uses safe-split (`_find_safe_split`) to avoid breaking tool_use/tool_result pairs. On failure, original messages are preserved
- **Memory system**: `Memory` uses SQLite (`STORE.db`) for fact storage and `FLOPS.md` for permanent charter. Facts have confidence scores (1-5). High confidence facts (>=3) are promoted to FLOPS.md. Distillation runs as a background async task every `distill_interval` turns
- **Sub-agent streaming**: `AgentTool` can stream sub-agent output via `ToolOutputEvent`, yielding text deltas, thinking, and tool usage from the sub-agent in real-time
- **Config format**: Models are referenced as `"provider:model"` (e.g., `"MiniMax:MiniMax-M2.7"`). Config uses `providers` dictionary with `api_format` auto-detection. Optional `memory` section controls distillation behavior
- **File editing**: Use `fileedit` tool with `old_str`/`new_str` rather than rewriting entire files
- **Shell timeout**: Default 600 seconds (defined in `const.REQUEST_TIMEOUT`) for shell commands
- **Working directory**: Tools use `ctx.cwd` if `cwd` param not provided
- **Snapshot/Undo**: `Snapshot` backs up files before modification. `/undo` restores files and rolls back conversation
- **Shell safety**: `shell_tool.py` uses bashlex AST parsing for command safety checks (dangerous command blacklist, pattern matching, string matching)