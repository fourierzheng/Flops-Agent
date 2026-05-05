import asyncio
import os
import platform
import sys
from pathlib import Path
from typing import AsyncGenerator

from flops.agent import Agent, AgentContext
from flops.command import CommandContext, Command, load_commands
from flops.compact import Compactor
from flops.config import Config
from flops.const import MEMORY_DIR, SESSIONS_DIR, SKILLS_DIR, TRASH_DIR
from flops.event import ChatEvent, ErrorEvent, NoticeEvent, StopEvent
from flops.llm import LLM, load_models
from flops.logger import config_log, logger
from flops.memory import Memory
from flops.registry import Registry
from flops.schemas import Skill, StopReason
from flops.session import Conversation, Session
from flops.skill import load_skills
from flops.snapshot import Snapshot
from flops.state import State

SYSTEM_PROMPT_TEMPLATE = """You are a coding assistant. You work with files, shell, and code.

{env_info}

## Code
- Read before you edit. Use `Grep`/`Glob` to find, `FileRead` to read, `FileEdit` to change.
- **Never rewrite entire files** — always use `FileEdit` with targeted old→new strings.
- Use `Shell` for exploration (git status, file listing, build commands) and `Python` for quick scripts.
- Write correct, minimal code. Match existing style, don't reformat.

## Behave
- Do exactly what's asked. No extras, no cleanup, no refactoring unless requested.
- If intent is unclear or there are multiple choices — ask, don't guess.
- Errors happen — try a different approach, don't explain the failure at length.

## Think
- Decide fast. Don't iterate on every option. Pick one and go.
- Long chains of internal back-and-forth waste your token budget. Stop deliberating.

## Output
- Get to the point. Say things once, don't restate.
- Large content (code, logs, diffs) → use `filewrite`/`fileedit`. Never stream raw dumps.
- No blank lines inside a single thought. No separators, no padding.
- Show only what changed — diffs, not whole files.

## Skills
- If a request fits a skill → load it with the `skill` tool, then follow its instructions.
- Skill paths are relative to the skill directory. Set `cwd` accordingly.
- Create skills under `{skill_dir}/<name>/` (lowercase_underscores), with `SKILL.md` + optional `scripts/`.

## Sub-Agent
- **For any multi-step research, analysis, or batch task** → use the `Agent` tool to delegate to a sub-agent. Do NOT do multiple sequential tool calls yourself.
  - `explore` mode: read-only code search and analysis. Use this for project understanding, architecture research, finding patterns.
  - `plan` mode: read-only analysis & implementation planning.
  - `general` mode: full access, can read and write files.
- The sub-agent runs independently and reports back. This is faster and keeps the conversation clean.

{skill_list}

## Memory
- Use the `mem` tool to recall user preferences, project facts, decisions, and habits from past conversations.

## Charter

{charter}
"""


def build_system_prompt(skills: Registry[Skill], workspace: str = "", charter: str = "") -> str:
    env_info = (
        f"- OS: {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"- Python: {sys.version.split()[0]} ({sys.executable})\n"
        f"- Working Directory: {workspace or os.getcwd()}"
    )
    skill_list = "\n".join(
        f"| {s.name} | {s.description} | {s.path.parent} |" for s in skills.values()
    )
    skill_list = (
        "| Name | Description | Base Directory |\n|------|-------------|------|\n" + skill_list
    )
    return SYSTEM_PROMPT_TEMPLATE.format(
        env_info=env_info, skill_dir=SKILLS_DIR, skill_list=skill_list, charter=charter
    )


class Engine:
    def __init__(self, config: Config):
        config_log(config.log)
        logger.info("Initializing Engine")

        self._skills = load_skills([str(SKILLS_DIR), *config.skills.paths])
        logger.info(f"Loaded {len(self._skills.keys())} skills")

        self._memory = Memory(MEMORY_DIR)
        self._commands = load_commands()
        self._session = Session()
        self._compactor = Compactor()

        logger.info("Session initialized with LLM")
        self._models = load_models(config.providers)

        self._agent = Agent(config.agent)
        self._state = State(
            model=self._agent.model,
            session_id=self._session.session_id,
            workspace=self._agent.workspace,
        )
        self._snapshot = Snapshot(TRASH_DIR / self._session.session_id, SESSIONS_DIR)
        self._conv_count = 0
        self._distill_interval = config.memory.distill_interval
        self._permission = config.tool.permission
        self._pending_tasks: list[tuple[asyncio.Task, str]] = []

        logger.info("Engine initialization complete")

    @property
    def model(self) -> str:
        return self._state.model

    @property
    def workspace(self):
        return self._state.workspace

    @property
    def session_id(self):
        return self._state.session_id

    def get_model_info(self):
        llm: LLM = self._models.get(self._state.model)
        return {
            "model_name": llm.name,
            "max_tokens": llm.max_tokens,
            "context_size": llm.context_size,
        }

    async def handle(self, user_input: str):
        cmd_name, *args = user_input.split()
        if cmd_name not in self._commands:
            yield ErrorEvent(error=Exception(f"Command {cmd_name} not found"))
            return
        llm = self._models.get(self._state.model)
        cmd: Command = self._commands.get(cmd_name)
        ctx = CommandContext(
            self._state,
            self._models.keys(),
            self._session,
            llm,
            self._agent,
            self._compactor,
            self._snapshot,
            self._memory,
            self._skills,
            permission=self._permission,
        )
        async for event in cmd.handle(ctx, args):
            yield event

    async def chat(self, user_input: str):
        self._snapshot.clear()
        llm = self._models.get(self._state.model)
        charter = self._memory.read_charter()
        system_prompt = build_system_prompt(self._skills, self._state.workspace, charter)

        # Wait for pending tasks from previous turn before proceeding
        for task, fmt in self._pending_tasks:
            count = await task
            if count:
                yield NoticeEvent(line=fmt.format(count=count))

        self._conv_count += 1
        conv = Conversation(self._session.messages)
        agent_ctx = AgentContext(
            system_prompt,
            llm,
            self._skills,
            snapshot=self._snapshot,
            memory=self._memory,
            permission=self._permission,
        )
        async for event in self._agent.chat(agent_ctx, conv, user_input):
            yield event

        self._session.add_conversation(conv)

        # After adding conversation, start background tasks for NEXT turn
        self._pending_tasks.clear()
        need_compact = self._compactor.need_compact(llm, self._session)
        if need_compact:
            logger.info("Compacting session for next conv")
            compact_task = asyncio.create_task(self._compactor.compact(llm, self._session))
            self._pending_tasks.append((compact_task, "📦 Compressed to {count} messages"))

        if self._distill_interval > 0 and self._conv_count % self._distill_interval == 0:
            logger.info(f"Auto-distilling after {self._conv_count} convs")
            distill_task = asyncio.create_task(self._memory.distill(self._session.messages, llm))
            self._pending_tasks.append((distill_task, "🧠 Remembered {count} new facts"))

    async def run(self, user_input: str) -> AsyncGenerator[ChatEvent]:
        self._state.history.append(user_input)
        try:
            cmd, *_ = user_input.split()
            is_command = cmd.strip() in self._commands
            stream = self.handle(user_input) if is_command else self.chat(user_input)
            async for event in stream:
                yield event
        except asyncio.CancelledError:
            logger.warning("Conversation interrupted by user")
            yield StopEvent(reason=StopReason.INTERRUPT)
        except Exception as e:
            logger.exception("Error during agent execution")
            yield ErrorEvent(error=e)
