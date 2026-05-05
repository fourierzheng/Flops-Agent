from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator

from flops.agent import Agent, AgentContext
from flops.compact import Compactor
from flops.event import ChatEvent, ExitEvent, LineEvent, TextDeltaEvent
from flops.llm import LLM
from flops.logger import logger
from flops.registry import Registry
from flops.schemas import Skill
from flops.session import Conversation, Session
from flops.snapshot import Snapshot
from flops.state import State

if TYPE_CHECKING:
    from flops.memory import Memory


@dataclass
class CommandContext:
    """Context for command handling."""

    state: State
    models: list[str]
    session: Session
    llm: LLM
    agent: Agent
    compactor: Compactor
    snapshot: Snapshot
    memory: "Memory"
    skills: Registry["Skill"] = field(default_factory=Registry)


_INIT_SYS_PROMPT = """You are a project initialization specialist. Your task is to analyze the current project structure and generate a comprehensive **AGENTS.md** developer guide.

## Workflow
1. Use `Glob` to scan the top-level directory structure and identify key files (e.g., README, package.json, pyproject.toml, Makefile, Cargo.toml, src/, etc.)
2. Use `FileRead` to inspect important configuration files, entry points, and representative source files
3. Synthesize all gathered information into a structured project guide

## Output Requirements
- Generate the **raw Markdown content** for the AGENTS.md file
- Do NOT wrap the output in a Markdown code block (do NOT start with ```markdown)
- Content should be concise yet thorough, enabling an AI assistant to quickly understand the project
- If a section is not applicable, skip it entirely; do not fabricate content

## AGENTS.md Structure
Organize the content according to the following sections, as applicable to the project:

- **Overview**: What the project does, its purpose, and architecture summary
- **Tech Stack**: Languages, frameworks, and major dependencies
- **Directory Structure**: Key directories and their responsibilities
- **Getting Started**: How to install, build, and run the project (extract from README or config files)
- **Development Guide**: Common commands (test/lint/build), testing strategy
- **Code Style**: Naming conventions, indentation style, import order, and other conventions
- **Key Files**: Important files an AI assistant should be aware of (entry points, configs, core modules)
- **Notes for AI**: Project-specific quirks, gotchas, or conventions that help an AI write correct code
"""

_INIT_USER_PROMPT = """Analyze the project structure in the current working directory and generate the AGENTS.md file content.

Task:
- Use `Glob` to scan the project structure
- Use `FileRead` to inspect key files such as README, package manifests, and build/config files
- Infer the project purpose, architecture, and development conventions
- Generate the complete AGENTS.md content

Output:
- Output the complete AGENTS.md content in Markdown format
- Do NOT wrap in code blocks
- Do NOT add any preface or explanation
- Start directly with the first heading
"""


class Command:
    name: str = ""
    description: str = ""

    def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        raise NotImplementedError


class InitCommand(Command):
    name: str = "/init"
    description: str = "Initialize this project and create 'AGENTS.md' file"

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        logger.info("Init command: creating AGENTS.md")
        chat_ctx = AgentContext(
            _INIT_SYS_PROMPT,
            ctx.llm,
            Registry[Skill](),
            ctx.snapshot,
            ctx.memory,
            tools=["Grep", "FileRead", "Glob", "List"],
        )
        conv = Conversation()

        content_parts = []
        async for event in ctx.agent.chat(chat_ctx, conv, _INIT_USER_PROMPT):
            yield event
            if isinstance(event, TextDeltaEvent):
                content_parts.append(event.text.text)

        # Write to file
        content = "\n".join(content_parts)
        Path("AGENTS.md").write_text(content)
        logger.info("AGENTS.md written successfully")
        yield LineEvent("AGENTS.md file created successfully")


class SessionCommand(Command):
    name: str = "/session"
    description: str = "List sessions or restore one: /session [session_id]"

    def usage(self) -> list[str]:
        return ["/session", "/session <session_id>"]

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        if not args:
            # No args → list sessions
            sessions = ctx.session.list_sessions()
            lines = ["Listing session IDs:"]
            for session in sessions:
                if session == ctx.session.session_id:
                    lines.append(f"  {session} (current)")
                else:
                    lines.append(f"  {session}")
            for line in lines:
                yield LineEvent(line)
        else:
            # One arg → restore session
            session_id = args[0]
            if session_id not in ctx.session.list_sessions():
                yield LineEvent(f"Error: Invalid session ID '{session_id}'")
            else:
                ctx.session.restore(session_id)
                yield LineEvent(f"Restored session: {session_id}")


class ModelCommand(Command):
    name: str = "/model"
    description: str = "List models or switch: /model [model_name]"

    def usage(self) -> list[str]:
        return ["/model", "/model <model_name>"]

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        if not args:
            # No args → list models
            lines = ["Listing models:"]
            for model in ctx.models:
                if model == ctx.state.model:
                    lines.append(f"  {model} (current)")
                else:
                    lines.append(f"  {model}")
            for line in lines:
                yield LineEvent(line)
        else:
            # One arg → switch model
            model_name = args[0]
            if model_name not in ctx.models:
                yield LineEvent(f"Error: Invalid model '{model_name}'")
                yield LineEvent("Available models: " + ", ".join(ctx.models))
            else:
                ctx.state.model = model_name
                logger.info(f"Model switched to: {model_name}")
                yield LineEvent(f"Changed model to: {model_name}")


class HistoryCommand(Command):
    name: str = "/history"
    description: str = "Show conversation history"

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        if not ctx.state.history:
            yield LineEvent("No conversation history")
            return
        yield LineEvent(f"Conversation history ({len(ctx.state.history)} turns):")
        for i, entry in enumerate(ctx.state.history, 1):
            yield LineEvent(f"  {i}. {entry}")


class ClearCommand(Command):
    name: str = "/clear"
    description: str = "Clear the conversation history"

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        ctx.state.history.clear()
        ctx.session.clear()
        ctx.snapshot.clear()
        logger.info("Conversation history cleared")
        yield LineEvent("Conversation history cleared successfully")


class CompactCommand(Command):
    name: str = "/compact"
    description: str = "Compress the conversation history"

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        yield LineEvent("Compressing conversation history...")
        await ctx.compactor.compact(ctx.llm, ctx.session)
        logger.info("Conversation history compressed")
        yield LineEvent("Conversation history compressed successfully")


class SkillsCommand(Command):
    name: str = "/skills"
    description: str = "List available skills"

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        skills = ctx.skills.values()
        if not skills:
            yield LineEvent("No skills available")
            return
        yield LineEvent(f"Available skills ({len(skills)}):")
        for skill in skills:
            yield LineEvent(f"  {skill.name:<20} - {skill.description}")


class ExitCommand(Command):
    name: str = "/exit"
    description: str = "Exit the program"

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        yield ExitEvent()


class UndoCommand(Command):
    name: str = "/undo"
    description: str = "Undo the last turn (conversation + file changes)"

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        if not ctx.session.messages:
            yield LineEvent("Nothing to undo")
            return

        # Remove from history
        if ctx.state.history:
            ctx.state.history.pop()

        # Restore files via snapshot
        restored = ctx.snapshot.restore_all()
        ctx.snapshot.clear()

        # Clear conversation
        removed = ctx.session.undo_last_turn()
        logger.info(f"Undo: {removed} messages, {restored} files restored")
        yield LineEvent(f"Undone last conversation ({removed} messages, {restored} files restored)")


class RememberCommand(Command):
    name: str = "/remember"
    description: str = "Manually trigger memory distillation from recent conversation."

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        yield LineEvent("Distilling facts from conversation...")
        count = await ctx.memory.distill(ctx.session.messages, ctx.llm)
        if count:
            yield LineEvent(f"✅ Distilled {count} new/updated facts.")
        else:
            yield LineEvent("No new facts found.")


class HelpCommand(Command):
    name: str = "/help"
    description: str = "Show the help message"

    def __init__(self, commands: list[Command]):
        self.commands = commands

    async def handle(self, ctx: CommandContext, args: list[str]) -> AsyncGenerator[ChatEvent]:
        helps = ["Available Commands:", f"  {self.name:<10} - Show this help message"]
        for cmd in self.commands:
            helps.append(f"  {cmd.name:<10} - {cmd.description}")

        keyboard_help = """
Keyboard Shortcuts:
  Ctrl+C       - Interrupt current response
  Ctrl+D       - Exit program
  Ctrl+A/E     - Move to beginning/end of line
  Ctrl+W       - Delete word
  Ctrl+K       - Delete to end of line
  ↑/↓          - Navigate command history
  Tab          - Complete (if enabled)"""
        helps.extend(keyboard_help.splitlines())
        for help in helps:
            yield LineEvent(help)


def load_commands() -> Registry[Command]:
    registry = Registry[Command]()
    cmds: list[Command] = [
        InitCommand(),
        SessionCommand(),
        ModelCommand(),
        HistoryCommand(),
        ClearCommand(),
        CompactCommand(),
        SkillsCommand(),
        UndoCommand(),
        RememberCommand(),
        ExitCommand(),
    ]
    for cmd in cmds:
        registry.register(cmd.name, cmd)

    registry.register("/help", HelpCommand(cmds))

    return registry
