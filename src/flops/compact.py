import re
from typing import TYPE_CHECKING

from flops.const import MAX_TOKENS_RESERVE, COMPRESSION_THRESHOLD
from flops.event import StopEvent, TextDeltaEvent

from flops.logger import logger
from flops.schemas import Message, TextBlock, ThinkingBlock, ToolResult, ToolUse
from flops.session import Session

if TYPE_CHECKING:
    from flops.llm import LLM


# Role name mapping for display
_ROLE_NAMES = {"user": "User", "assistant": "Assistant"}

# Reserve at least this many recent messages when compressing
_MIN_RESERVE = 8


def _find_safe_split(messages: list[Message], min_reserve: int = _MIN_RESERVE) -> int:
    """From the end, find a split point where no tool_use/tool_result pairs are broken.

    Two checks on the candidate suffix:
    1. Forward: every tool_use in candidates must have a matching tool_result in candidates
    2. Backward: no tool_result in candidates may reference a tool_use that exists in history
       (such a tool_use would be compressed away, leaving an orphaned tool_result)

    Returns the index to split at (kept = messages[split:]).
    Returns 0 if no safe split was found (keep everything).
    """
    if len(messages) <= min_reserve:
        return 0

    for reserve in range(min_reserve, len(messages) + 1):
        history = messages[:-reserve]
        candidates = messages[-reserve:]

        # Collect tool_use IDs from history (will be compressed away)
        history_use_ids: set[str] = {
            c.id for msg in history for c in msg.content if isinstance(c, ToolUse)
        }

        # Collect tool_use IDs and tool_result references in candidates
        use_ids: set[str] = set()
        result_refs: set[str] = set()
        for msg in candidates:
            for c in msg.content:
                if isinstance(c, ToolUse):
                    use_ids.add(c.id)
                elif isinstance(c, ToolResult) and c.tool_use_id:
                    result_refs.add(c.tool_use_id)

        # Forward: every tool_use in candidates has a matching tool_result
        forward_ok = use_ids.issubset(result_refs)

        # Backward: no tool_result in candidates references a tool_use in history
        backward_ok = not (result_refs & history_use_ids)

        if forward_ok and backward_ok:
            return len(messages) - reserve

    return 0


class Summarizer:
    """
    LLM-based message compressor

    Analyzes conversation history via LLM to generate a coherent summary,
    preserving key information and context while significantly reducing tokens.
    """

    PROMPT = """You are a conversation history compression expert. Please compress the following conversation into a summary.

## Requirements
1. **Preserve key information**: Decisions, conclusions, important context
2. **Preserve tool calls**: Keep all tool_use and tool_result (critical)
3. **Compress code**: For code blocks, keep function signatures and 1-2 key lines only, remove full implementations
4. **Maintain coherence**: Ensure the summary is logically clear
5. **Concise expression**: Use minimum words to express maximum information

## Output Format
Output the summary between <summary> and </summary> tags only.

Conversation to compress:"""

    def __init__(self, llm: LLM, ratio: float = 0.3):
        self._llm = llm
        self._ratio = ratio

    async def summarize(self, messages: list[Message], target_tokens: int) -> list[Message]:
        """Compress messages via LLM

        Args:
            messages: List of messages to compress
            target_tokens: Target token count after compression
        """
        if len(messages) <= 3:
            return messages

        # Find safe split — only compress history, keep recent messages intact
        split = _find_safe_split(messages)
        history = messages[:split]
        recent = messages[split:]

        if not history or len(history) <= 3:
            # History too short to compress meaningfully, keep everything
            return messages

        has_tools = any(
            isinstance(c, (ToolUse, ToolResult)) for msg in history for c in msg.content
        )

        text = self._format_messages(history)
        original_tokens = max(1, self._estimate_tokens(text))

        # Calculate compression ratio
        if target_tokens:
            self._ratio = max(0.2, min(0.5, target_tokens / original_tokens))

        detail_level = "high" if self._ratio > 0.4 else "medium" if self._ratio > 0.3 else "low"
        prompt = (
            f"{self.PROMPT}\n\n"
            f"[Target] Compress to ~{int(self._ratio * 100)}% (detail level: {detail_level})\n\n"
            f"{'='*50}\n\n{text}\n\n{'='*50}"
        )

        # Call LLM; on failure, keep original messages to avoid losing context
        try:
            summary = await self._call_llm(prompt)
            compressed_tokens = self._estimate_tokens(summary)
            logger.info(
                f"Compression: {len(history)} msgs/{original_tokens}t -> "
                f"1 msgs/{compressed_tokens}t "
                f"(ratio: {compressed_tokens/original_tokens:.0%}), "
                f"keeping {len(recent)} recent msgs"
            )
        except Exception as e:
            logger.warning(f"LLM compression failed: {e}, keeping original messages")
            return messages

        # Build result: summary note + recent messages (unaltered)
        result = [
            Message(
                role="user",
                content=[
                    TextBlock(text=f"[History Summary] Previous conversation summary:\n\n{summary}")
                ],
            )
        ]

        if has_tools:
            result.append(
                Message(
                    role="user",
                    content=[TextBlock(text="[Note] Previous conversation used tool calls.")],
                )
            )

        result.extend(recent)
        return result

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM to generate summary"""

        messages = [Message(role="user", content=[TextBlock(text=prompt)])]
        summary_text = ""

        async for event in self._llm.stream("", [], messages=messages):
            if isinstance(event, TextDeltaEvent):
                summary_text += event.text.text
            elif isinstance(event, StopEvent):
                break
        return self._extract_summary(summary_text)

    def _extract_summary(self, text: str) -> str:
        """Extract summary from LLM output"""
        match = re.search(r"<summary>\s*(.*?)\s*</summary>", text, re.DOTALL)
        return match.group(1).strip() if match else text.strip()

    def _format_messages(self, messages: list[Message]) -> str:
        """Format messages for compression"""
        parts = []

        for msg in messages:
            text_parts = []
            for c in msg.content:
                if isinstance(c, TextBlock):
                    text_parts.append(c.text)
                elif isinstance(c, ThinkingBlock):
                    text_parts.append(c.thinking)
                elif isinstance(c, ToolUse):
                    text_parts.append(f"[Call: {c.name}, params: {c.input}]")
                elif isinstance(c, ToolResult):
                    text_parts.append(f"[Result: {c.content}]")
                else:
                    logger.warning(f"Unknown content type in compression: {type(c)}")
                    continue

            text = " ".join(text_parts)
            role_name = _ROLE_NAMES.get(msg.role, msg.role)
            parts.append(f"{role_name}: {text}")

        return "\n\n".join(parts)

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate"""
        chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
        english = len(re.findall(r"[a-zA-Z]", text))
        other = len(text) - chinese - english

        return chinese // 2 + english // 4 + other // 3


class Compactor:

    def need_compact(self, llm: LLM, session: Session) -> bool:
        """Check if compression is needed"""
        input_tokens = session._last_usage.input_tokens
        needed = input_tokens >= llm.context_size - MAX_TOKENS_RESERVE
        ratio = input_tokens / llm.context_size >= COMPRESSION_THRESHOLD
        return needed or ratio

    async def compact(self, llm: LLM, session: Session) -> int:
        """Compress messages using LLM. Returns the new message count, or 0 if unchanged."""
        available_window = llm.context_size - MAX_TOKENS_RESERVE
        target_tokens = int(available_window * 0.3)  # Target compress to 30%
        logger.info(
            f"Compacting session: target ~{target_tokens} msg tokens, "
            f"prev API call used {session.last_total_tokens} tokens total"
        )
        summarizer = Summarizer(llm)
        original_count = len(session.messages)
        compressed = await summarizer.summarize(session.messages, target_tokens)
        session.reset_messages(compressed)
        if len(compressed) < original_count:
            return len(compressed)
        return 0
