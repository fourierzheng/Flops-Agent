from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from typing import AsyncGenerator

import anthropic
import httpx
import openai

from flops.config import ProviderConfig
from flops.const import REQUEST_TIMEOUT
from flops.event import (
    ChatEvent,
    StopEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolUseEvent,
    UsageEvent,
)
from flops.logger import logger
from flops.registry import Registry
from flops.schemas import (
    Message,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolResult,
    ToolUse,
    Usage,
)

_TYPE = {
    TextBlock: "text",
    ThinkingBlock: "thinking",
    ToolUse: "tool_use",
    ToolResult: "tool_result",
}

_RETRY_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.APIStatusError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.APIStatusError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.TransportError,
    httpx.RequestError,
)


class LLM:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int,
        context_size: int,
        name: str,
        thinking: bool,
        timeout: float = REQUEST_TIMEOUT,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._max_tokens = max_tokens
        self._context_size = context_size
        self._name = name
        self._thinking: bool = thinking
        self._timeout = timeout
        self._client = None

    @property
    def model(self):
        return self._model

    @property
    def name(self):
        return self._name

    @property
    def max_tokens(self):
        return self._max_tokens

    @property
    def context_size(self):
        return self._context_size

    def _to_api_messages(self, messages):
        raise NotImplementedError("Subclasses must implement _to_api_messages")

    def _do_stream(self, messages, tools, system_prompt):
        raise NotImplementedError("Subclasses must implement _do_stream")

    async def stream(
        self, system_prompt: str, tools: list, messages: list[Message]
    ) -> AsyncGenerator[ChatEvent]:
        logger.info(f"Starting stream with {len(messages)} messages, {len(tools)} tools available")
        messages = self._to_api_messages(messages)
        nretries = 3
        for attempt in range(nretries):
            try:
                logger.debug(f"LLM stream attempt {attempt + 1}/3")
                async for event in self._do_stream(messages, tools, system_prompt):
                    yield event
                break  # Chat completed successfully, exit retry loop
            except _RETRY_ERRORS as e:
                logger.exception(f"LLM stream attempt {attempt + 1}/3 failed with error: {e}")
                await asyncio.sleep(1)  # Wait 1 second before retry
                if attempt == nretries - 1:  # Last retry failed, raise exception
                    raise


class AnthropicLLM(LLM):

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            logger.info(f"Connecting to Anthropic with base_url={self._base_url}")
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    def _to_api_messages(self, messages: list):
        api_messages = []
        for msg in messages:
            content = [{"type": _TYPE[type(c)], **asdict(c)} for c in msg.content]
            message = {"role": msg.role, "content": content}
            api_messages.append(message)
        return api_messages

    async def _do_stream(self, messages: list, tools: list, system_prompt: str):
        async with self._get_client().messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            tools=tools,
            messages=messages,
            system=system_prompt,
            thinking={"type": "enabled"} if self._thinking else {"type": "disabled"},  # type: ignore
        ) as stream:
            current_tool = None
            stop_reason = None
            async for event in stream:
                if event.type == "message_stop":
                    # calculate tokens
                    # ref: https://platform.claude.com/docs/en/build-with-claude/prompt-caching#tracking-cache-performance
                    msg_usage = event.message.usage
                    input_tokens = (
                        (msg_usage.input_tokens or 0)
                        + (msg_usage.cache_read_input_tokens or 0)
                        + (msg_usage.cache_creation_input_tokens or 0)
                    )
                    output_tokens = msg_usage.output_tokens or 0
                    usage = Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                    yield UsageEvent(usage=usage)
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool = {
                            "name": block.name,
                            "input_str": "",
                            "id": block.id,
                        }
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield TextDeltaEvent(TextBlock(delta.text))
                    elif delta.type == "input_json_delta":
                        if current_tool:
                            current_tool["input_str"] += delta.partial_json
                    elif delta.type == "thinking_delta":
                        yield ThinkingEvent(ThinkingBlock(delta.thinking))
                elif event.type == "content_block_stop":
                    if current_tool:
                        yield ToolUseEvent(
                            tool_use=ToolUse(
                                id=current_tool["id"],
                                name=current_tool["name"],
                                input=json.loads(current_tool["input_str"]),
                            ),
                        )
                        current_tool = None

                elif event.type == "message_delta":
                    stop_reason = event.delta.stop_reason
            if stop_reason not in ("tool_use", "end_turn", "max_tokens"):
                logger.error(
                    f"Unexpected Anthropic stop reason: {stop_reason}, falling back to COMPLETED"
                )
                stop_reason = "end_turn"
            yield StopEvent(StopReason(stop_reason))


class OpenAILLM(LLM):

    def _get_client(self):
        if self._client is None:
            logger.info(f"Connecting to OpenAI with base_url={self._base_url}")
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    def _to_api_messages(self, messages: list):
        api_messages = []
        for msg in messages:
            openai_msg = {"role": msg.role}
            content = ""
            tool_uses = []
            tool_results = []
            reasoning_content = ""
            for seg in msg.content:
                if isinstance(seg, TextBlock):
                    content += seg.text
                elif isinstance(seg, ThinkingBlock):
                    # OpenAI does not support thinking blocks; store content
                    reasoning_content += seg.thinking
                elif isinstance(seg, ToolUse):
                    tool_uses.append(seg)
                elif isinstance(seg, ToolResult):
                    tool_results.append(seg)
            if content:
                openai_msg["content"] = content
            if reasoning_content:
                openai_msg["reasoning_content"] = reasoning_content
            if tool_uses:
                openai_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.input),
                        },
                    }
                    for tc in tool_uses
                ]
            if content or tool_uses:
                api_messages.append(openai_msg)
            for tool_result in tool_results:
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_result.tool_use_id,
                        "content": tool_result.content,
                    }
                )

        return api_messages

    async def _do_stream(self, messages: list, tools: list, system_prompt: str):
        messages = [{"role": "system", "content": system_prompt}] + messages
        logger.debug(f"OpenAI API call: {len(messages)} messages, {len(tools)} tools")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
        finish_reason = None
        tool_uses: list[ToolUse] = []
        tool_use_args: list[str] = []
        stream = await self._get_client().chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            tools=tools,
            messages=messages,
            stream_options={"include_usage": True},
            stream=True,
            extra_body={"thinking": {"type": "enabled" if self._thinking else "disabled"}},
        )
        async for event in stream:
            if not event.choices:
                continue
            if event.choices[0].finish_reason:
                finish_reason = event.choices[0].finish_reason
            if event.usage:
                prompt_tokens = event.usage.prompt_tokens or 0
                completion_tokens = event.usage.completion_tokens or 0
                usage = Usage(
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                )
                yield UsageEvent(usage=usage)
            delta = event.choices[0].delta
            if getattr(delta, "reasoning_content", None):
                yield ThinkingEvent(ThinkingBlock(delta.reasoning_content))  # type: ignore
            if delta.content:
                yield TextDeltaEvent(TextBlock(delta.content))
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    if tc.index == len(tool_uses):
                        tool_uses.append(ToolUse("", "", {}))
                        tool_use_args.append("")
                    if tc.id:
                        tool_uses[tc.index].id = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_uses[tc.index].name = tc.function.name
                        if tc.function.arguments:
                            tool_use_args[tc.index] += tc.function.arguments

        for i, tool_use in enumerate(tool_uses):
            if tool_use_args[i]:
                try:
                    tool_use.input = json.loads(tool_use_args[i])
                except json.JSONDecodeError:
                    tool_use.input = {}
            yield ToolUseEvent(tool_use=tool_use)
        if finish_reason not in ("stop", "tool_calls", "length", "content_filter", None):
            logger.error(
                f"Unexpected OpenAI finish reason: {finish_reason}, falling back to COMPLETED"
            )
            finish_reason = "stop"
        stop_reason = {
            "stop": StopReason.COMPLETED,
            "tool_calls": StopReason.TOOL_CALL,
            "length": StopReason.MAX_TOKENS,
            "content_filter": StopReason.MAX_TOKENS,
            None: StopReason.CONTINUE,
        }[finish_reason]
        yield StopEvent(stop_reason)


def _create_llm(
    api_format: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
    context_size: int,
    name: str,
    thinking: bool,
    timeout: float = REQUEST_TIMEOUT,
) -> LLM:
    if not api_format or api_format == "auto":
        url_lower = base_url.lower()
        if "/anthropic" in url_lower or "claude" in url_lower:
            api_format = "anthropic"
        else:
            api_format = "openai"
    assert api_format in ("anthropic", "openai"), f"Unknown api_format: {api_format}"
    llm_class = AnthropicLLM if api_format == "anthropic" else OpenAILLM
    return llm_class(api_key, base_url, model, max_tokens, context_size, name, thinking, timeout)


def load_models(providers: dict[str, ProviderConfig]) -> Registry[LLM]:
    registry: Registry[LLM] = Registry()
    for name, provider in providers.items():
        for model, config in provider.models.items():
            model_name = f"{name}:{model}"
            llm = _create_llm(
                provider.api_format,
                provider.api_key,
                provider.base_url,
                model,
                config.max_tokens,
                config.context_size,
                model_name,
                config.thinking,
                config.request_timeout,
            )
            registry.register(model_name, llm)
    return registry
