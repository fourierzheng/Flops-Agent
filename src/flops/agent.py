from dataclasses import dataclass

from flops.config import AgentConfig
from flops.event import (
    StopEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolOutputEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from flops.llm import LLM
from flops.logger import logger
from flops.registry import Registry
from flops.schemas import (
    Permission,
    Skill,
    StopReason,
    TextBlock,
    ToolUse,
)
from flops.session import Conversation
from flops.snapshot import Snapshot
from flops.memory import Memory
from flops.tools import dispatch_tool, get_tool_schemas, ToolContext


@dataclass
class AgentContext:
    """Context for a chat session."""

    system_prompt: str
    llm: LLM
    skills: Registry[Skill]
    snapshot: Snapshot
    memory: Memory
    tools: list[str] | None = None
    permission: Permission = Permission.FULL


class Agent:
    def __init__(self, config: AgentConfig):
        self._model = config.model
        self._workspace = config.workspace
        self._max_turns = config.max_turns

    @property
    def model(self) -> str:
        return self._model

    @property
    def workspace(self) -> str:
        return self._workspace

    async def _execute_tools(
        self,
        tool_uses: list[ToolUse],
        ctx: AgentContext,
        conversation: Conversation,
    ):
        """Execute tools, streaming any intermediate events."""

        def stream_chat(system_prompt: str, task: str, tools: list[str] | None = None):
            agent_ctx = AgentContext(
                system_prompt,
                ctx.llm,
                ctx.skills,
                ctx.snapshot,
                ctx.memory,
                tools,
                permission=ctx.permission,
            )
            conv = Conversation()
            return self.chat(agent_ctx, conv, task)

        tctx = ToolContext(
            self._workspace,
            ctx.skills,
            ctx.snapshot,
            ctx.memory,
            ctx.llm,
            stream_chat,
            permission=ctx.permission,
        )
        for tool_use in tool_uses:
            logger.info(f"Executing tool: {tool_use.name} with input: {tool_use.input}")
            result = await dispatch_tool(tctx, tool_use)
            if stream := getattr(result, "stream", None):
                parts: list[str] = []
                async for event in stream:
                    if isinstance(event, TextDeltaEvent):
                        yield ToolOutputEvent(text=event.text.text)
                        parts.append(event.text.text)
                result.content = "".join(parts)
            logger.debug(
                f"Tool result: {result.content[:200]}..."
                if len(result.content) > 200
                else f"Tool result: {result.content}"
            )
            conversation.add_tool_result(result)
            yield ToolResultEvent(result)

    async def chat(self, ctx: AgentContext, conversation: Conversation, requirements: str):
        """Main chat loop: stream LLM responses and handle tool calls."""
        user_prompt = requirements
        llm = ctx.llm

        conversation.add_user_message(TextBlock(requirements))
        logger.info(
            f"User input: {user_prompt[:100]}..."
            if len(user_prompt) > 100
            else f"User input: {user_prompt}"
        )
        tool_schemas = get_tool_schemas(ctx.tools)

        turn = 0
        while turn < self._max_turns:
            turn += 1
            logger.info(f"Turn {turn}/{self._max_turns} started")

            stop_event = None
            tool_uses: list[ToolUse] = []
            async for event in llm.stream(
                ctx.system_prompt, tool_schemas, messages=conversation.get_messages()
            ):
                if isinstance(event, UsageEvent):
                    conversation.update_usage(event.usage)
                    logger.debug(f"Token usage: {event.usage}")
                    yield event
                elif isinstance(event, TextDeltaEvent):
                    conversation.add_llm_message(event.text)
                    yield event
                elif isinstance(event, ThinkingEvent):
                    conversation.add_llm_thinking(event.thinking)
                    yield event
                elif isinstance(event, ToolUseEvent):
                    tool_use = event.tool_use
                    logger.info(f"LLM requested tool: {tool_use.name}")
                    conversation.add_tool_use(tool_use)
                    tool_uses.append(tool_use)
                    yield event
                elif isinstance(event, StopEvent):
                    logger.info(f"LLM stopped with reason: {event.reason}")
                    stop_event = event
                    break

            if tool_uses:
                async for event in self._execute_tools(tool_uses, ctx, conversation):
                    yield event

            assert stop_event is not None, "LLM did not return a StopEvent"
            logger.info(f"LLM finished turn with reason: {stop_event.reason}")
            if stop_event.reason not in (
                StopReason.TOOL_CALL,
                StopReason.CONTINUE,
                StopReason.MAX_TOKENS,
            ):
                yield stop_event
                return

        if turn == self._max_turns:
            logger.warning(f"Max turns ({self._max_turns}) reached, stopping")
            yield StopEvent(reason=StopReason.MAX_TURNS)
            return
