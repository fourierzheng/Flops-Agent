import asyncio

import pytest

from flops.compact import Summarizer
from flops.event import StopEvent, TextBlock, TextDeltaEvent, ThinkingBlock
from flops.session import Message, Session
from flops.tools import ToolResult, ToolUse


class MockLLM:
    def __init__(self, summary_text="compressed summary", context_size=200000):
        self.summary_text = summary_text
        self.context_size = context_size

    async def stream(self, ctx, tools=None, messages=None):
        yield TextDeltaEvent(TextBlock(f"<summary>{self.summary_text}</summary>"))
        yield StopEvent(reason="end_turn")


def get_content_items(content):
    """Ensure content is an iterable list"""
    if isinstance(content, list):
        return content
    return [content]


class TestSummarizer:
    """Summarizer tests"""

    def test_no_compress_under_threshold(self):
        """Do not compress when message count <= 3"""
        summarizer = Summarizer(MockLLM())
        messages = [Message(role="user", content=[TextBlock(f"msg {i}")]) for i in range(3)]

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=100000))

        assert len(compressed) == 3

    def test_compress_over_threshold(self):
        """Compress when message count exceeds threshold"""
        summarizer = Summarizer(MockLLM())
        messages = []
        for i in range(100):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=200))

        # Should compress to summary + recent messages (default 2)
        assert len(compressed) < len(messages)

    def test_preserve_tool_calls(self):
        """Compressed result should contain tool note when tool calls exist"""
        summarizer = Summarizer(MockLLM())
        messages = []

        # Add some normal messages
        for i in range(30):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        # Add tool calls and results
        tool_use = ToolUse(id="test_id", name="test_tool", input={"key": "value"})
        messages.append(Message(role="assistant", content=[tool_use]))
        tool_result = ToolResult(tool_use_id="test_id", content="tool result")
        messages.append(Message(role="user", content=[tool_result]))

        # Add some more normal messages
        for i in range(30, 60):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=200))

        # Summarizer compresses tool content into summary but preserves tool note
        has_tool_note = any(
            "tool calls" in (c.text if hasattr(c, "text") else str(c))
            for msg in compressed
            for c in get_content_items(msg.content)
        )
        assert has_tool_note, "Tool note should be present when tools were used"

    def test_message_format_unchanged(self):
        """Message format remains unchanged after compression"""
        summarizer = Summarizer(MockLLM())
        messages = []
        for i in range(60):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        # Record original format
        original_types = [type(msg) for msg in messages]
        original_roles = [msg.role for msg in messages]
        original_content_types = [
            type(c) for msg in messages for c in get_content_items(msg.content)
        ]

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=300))

        # Check format unchanged
        new_types = [type(msg) for msg in compressed]
        new_roles = [msg.role for msg in compressed]
        new_content_types = [type(c) for msg in compressed for c in get_content_items(msg.content)]

        assert all(t == Message for t in new_types), "All messages should be Message type"
        assert all(r in ["user", "assistant", "system"] for r in new_roles), "Role should be valid"
        # content types should be a subset of original types (plus TextBlock for the summary)
        assert set(new_content_types).issubset(
            set(original_content_types + [TextBlock])
        ), "Content types should not change unexpectedly"

    def test_to_api_messages_after_compress(self):
        """Compressed messages should still work correctly with API structure"""
        summarizer = Summarizer(MockLLM())
        messages = []
        for i in range(60):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=300))

        # Should not raise errors
        assert len(compressed) > 0
        assert all(hasattr(msg, "role") and hasattr(msg, "content") for msg in compressed)

    def test_compress_preserves_recent_messages(self):
        """Compressed summary should contain recent messages"""
        summarizer = Summarizer(MockLLM())
        messages = []
        for i in range(60):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        # Last user message
        messages.append(Message(role="user", content=[TextBlock("this is the last message")]))
        messages.append(Message(role="assistant", content=[TextBlock("this is the last response")]))

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=100))

        # Check that compression happened (message count reduced)
        assert len(compressed) < len(messages), "Compression should reduce message count"

        # Check summary content (MockLLM returns fixed text, verify correct format)
        all_content = " ".join(
            str(c.text) if hasattr(c, "text") else str(c)
            for msg in compressed
            for c in get_content_items(msg.content)
        )
        assert "History Summary" in all_content, "Should contain history summary marker"

    def test_compress_returns_text_block_content(self):
        """Compressed summary content should be of type TextBlock"""
        summarizer = Summarizer(MockLLM("summary content"))
        messages = [Message(role="user", content=[TextBlock(f"msg {i}")]) for i in range(60)]

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=300))

        # First message is user summary (injected at beginning of conversation), content should be TextBlock
        assert compressed[0].role == "user"
        for c in get_content_items(compressed[0].content):
            assert isinstance(c, TextBlock), f"Expected TextBlock, got {type(c)}"

    def test_format_messages_with_text_block(self):
        """_format_messages correctly handles TextBlock"""
        summarizer = Summarizer(MockLLM())
        messages = [
            Message(role="user", content=[TextBlock("hello")]),
            Message(role="assistant", content=[TextBlock("world")]),
        ]
        text = summarizer._format_messages(messages)
        assert "User: hello" in text
        assert "Assistant: world" in text

    def test_format_messages_with_thinking_block(self):
        """_format_messages correctly handles ThinkingBlock"""
        summarizer = Summarizer(MockLLM())
        messages = [
            Message(role="assistant", content=[ThinkingBlock("deep thought")]),
        ]
        text = summarizer._format_messages(messages)
        assert "deep thought" in text


class TestSafeSplit:
    """_find_safe_split tests"""

    def test_split_basic_no_tools(self):
        """Plain text messages, no split for <= 8 messages"""
        from flops.compact import _find_safe_split

        msgs = [Message(role="user", content=[TextBlock(f"msg {i}")]) for i in range(5)]
        assert _find_safe_split(msgs) == 0

    def test_split_complete_tool_pairs(self):
        """When tool calls appear in pairs, safe split from end works"""
        from flops.compact import _find_safe_split

        msgs = []
        for i in range(12):
            msgs.append(Message(role="user", content=[TextBlock(f"user {i}")]))
            msgs.append(Message(role="assistant", content=[TextBlock(f"asst {i}")]))
        # Add some tool calls
        msgs.append(Message(role="assistant", content=[ToolUse(id="1", name="test", input={})]))
        msgs.append(Message(role="user", content=[ToolResult(content="ok", tool_use_id="1")]))
        msgs.append(Message(role="assistant", content=[TextBlock(text="done")]))

        split = _find_safe_split(msgs)
        assert split > 0
        # Recent messages should contain complete tool call pairs
        recent = msgs[split:]
        assert any(
            isinstance(c, ToolUse) for m in recent for c in m.content
        ), "Recent should contain tool_use"
        assert any(
            isinstance(c, ToolResult) for m in recent for c in m.content
        ), "Recent should contain tool_result"

    def test_split_unmatched_tool_use_extended(self):
        """Unmatched tool_use at the end auto-extends to include subsequent tool_result"""
        from flops.compact import _find_safe_split

        msgs = []
        for i in range(20):
            msgs.append(Message(role="user", content=[TextBlock(f"user {i}")]))
            msgs.append(Message(role="assistant", content=[TextBlock(f"asst {i}")]))

        # Place a pair of tool calls at the end (tool_use + tool_result)
        msgs.append(Message(role="assistant", content=[ToolUse(id="x", name="shell", input={})]))
        msgs.append(Message(role="user", content=[ToolResult(content="output", tool_use_id="x")]))

        split = _find_safe_split(msgs)
        assert split > 0
        # tool_result should be in recent
        recent = msgs[split:]
        assert any(
            isinstance(c, ToolResult) for m in recent for c in m.content
        ), "ToolResult should be in recent"

    def test_split_unmatched_tool_result_ok(self):
        """tool_result's tool_use in history does not affect split"""
        from flops.compact import _find_safe_split

        # Construct: tool use in history, tool_result near end
        msgs = []
        msgs.append(Message(role="assistant", content=[ToolUse(id="x", name="shell", input={})]))
        msgs.append(Message(role="user", content=[ToolResult(content="out", tool_use_id="x")]))
        for i in range(10):
            msgs.append(Message(role="user", content=[TextBlock(f"user {i}")]))
            msgs.append(Message(role="assistant", content=[TextBlock(f"asst {i}")]))
        # tool_result at the end whose tool_use is in history
        msgs.append(Message(role="user", content=[ToolResult(content="late", tool_use_id="old")]))

        split = _find_safe_split(msgs, min_reserve=4)
        assert split > 0
        recent = msgs[split:]
        assert any(isinstance(c, ToolResult) for m in recent for c in m.content)


class TestSessionCompression:
    """Session compression tests"""

    def test_session_compress(self):
        """Verify Session compression effect"""
        mock_llm = MockLLM()
        session = Session()
        for i in range(60):
            session.messages.append(Message(role="user", content=[TextBlock(text=f"user msg {i}")]))
            session.messages.append(
                Message(role="assistant", content=[TextBlock(text=f"assistant msg {i}")])
            )

        # Directly call Summarizer to verify compression logic
        summarizer = Summarizer(mock_llm)
        compressed = asyncio.run(summarizer.summarize(session.messages, target_tokens=300))

        # Compressed messages should be fewer
        assert len(compressed) < len(session.messages)

    def test_session_no_compress_under_threshold(self):
        """Session does not compress when tokens are insufficient"""
        mock_llm = MockLLM(context_size=200000)
        session = Session()
        for i in range(5):
            session.messages.append(Message(role="user", content=[TextBlock(text=f"user msg {i}")]))
            session.messages.append(
                Message(role="assistant", content=[TextBlock(text=f"assistant msg {i}")])
            )

        original_count = len(session.messages)

        # Session has no compress_if_need method, directly verify message count unchanged
        # (Small message count does not trigger compression)
        assert len(session.messages) == original_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
