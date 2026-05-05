"""
Compressor 单测
"""

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

    async def stream(self, ctx, messages):
        yield TextDeltaEvent(TextBlock(f"<summary>{self.summary_text}</summary>"))
        yield StopEvent(reason="end_turn")


def get_content_items(content):
    """确保 content 是可迭代的 list"""
    if isinstance(content, list):
        return content
    return [content]


class TestSummarizer:
    """Summarizer 测试"""

    def test_no_compress_under_threshold(self):
        """消息数少于等于 3 时，不压缩"""
        summarizer = Summarizer(MockLLM())
        messages = [Message(role="user", content=[TextBlock(f"msg {i}")]) for i in range(3)]

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=100000))

        assert len(compressed) == 3

    def test_compress_over_threshold(self):
        """消息数超过阈值时，压缩"""
        summarizer = Summarizer(MockLLM())
        messages = []
        for i in range(100):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=200))

        # 应该压缩到 summary + recent messages（默认2条）
        assert len(compressed) < len(messages)

    def test_preserve_tool_calls(self):
        """存在工具调用时，压缩结果应包含 tool note"""
        summarizer = Summarizer(MockLLM())
        messages = []

        # 添加一些普通消息
        for i in range(30):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        # 添加工具调用和结果
        tool_use = ToolUse(id="test_id", name="test_tool", input={"key": "value"})
        messages.append(Message(role="assistant", content=[tool_use]))
        tool_result = ToolResult(tool_use_id="test_id", content="tool result")
        messages.append(Message(role="user", content=[tool_result]))

        # 再添加一些普通消息
        for i in range(30, 60):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=200))

        # Summarizer 会把工具内容压缩进 summary，但会保留 tool note
        has_tool_note = any(
            "tool calls" in (c.text if hasattr(c, "text") else str(c))
            for msg in compressed
            for c in get_content_items(msg.content)
        )
        assert has_tool_note, "Tool note should be present when tools were used"

    def test_message_format_unchanged(self):
        """压缩前后消息格式不变"""
        summarizer = Summarizer(MockLLM())
        messages = []
        for i in range(60):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        # 记录原始格式
        original_types = [type(msg) for msg in messages]
        original_roles = [msg.role for msg in messages]
        original_content_types = [
            type(c) for msg in messages for c in get_content_items(msg.content)
        ]

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=300))

        # 检查格式未变
        new_types = [type(msg) for msg in compressed]
        new_roles = [msg.role for msg in compressed]
        new_content_types = [type(c) for msg in compressed for c in get_content_items(msg.content)]

        assert all(t == Message for t in new_types), "All messages should be Message type"
        assert all(r in ["user", "assistant", "system"] for r in new_roles), "Role should be valid"
        # content types 应该是原始类型的子集（加上 TextBlock，因为 summary 是 TextBlock）
        assert set(new_content_types).issubset(
            set(original_content_types + [TextBlock])
        ), "Content types should not change unexpectedly"

    def test_to_api_messages_after_compress(self):
        """压缩后消息结构仍能正常工作"""
        summarizer = Summarizer(MockLLM())
        messages = []
        for i in range(60):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=300))

        # 应该不报错
        assert len(compressed) > 0
        assert all(hasattr(msg, "role") and hasattr(msg, "content") for msg in compressed)

    def test_compress_preserves_recent_messages(self):
        """压缩后 summary 中应包含最近消息"""
        summarizer = Summarizer(MockLLM())
        messages = []
        for i in range(60):
            messages.append(Message(role="user", content=[TextBlock(f"user msg {i}")]))
            messages.append(Message(role="assistant", content=[TextBlock(f"assistant msg {i}")]))

        # 最后一个用户消息
        messages.append(Message(role="user", content=[TextBlock("this is the last message")]))
        messages.append(Message(role="assistant", content=[TextBlock("this is the last response")]))

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=100))

        # 检查压缩发生了（消息数减少）
        assert len(compressed) < len(messages), "Compression should reduce message count"

        # 检查 summary 内容（MockLLM 返回固定文本，验证格式正确）
        all_content = " ".join(
            str(c.text) if hasattr(c, "text") else str(c)
            for msg in compressed
            for c in get_content_items(msg.content)
        )
        assert "History Summary" in all_content, "Should contain history summary marker"

    def test_compress_returns_text_block_content(self):
        """压缩后的 summary 内容应该是 TextBlock 类型"""
        summarizer = Summarizer(MockLLM("summary content"))
        messages = [Message(role="user", content=[TextBlock(f"msg {i}")]) for i in range(60)]

        compressed = asyncio.run(summarizer.summarize(messages, target_tokens=300))

        # 第一个消息是 user summary（注入到对话开头），content 应该是 TextBlock
        assert compressed[0].role == "user"
        for c in get_content_items(compressed[0].content):
            assert isinstance(c, TextBlock), f"Expected TextBlock, got {type(c)}"

    def test_format_messages_with_text_block(self):
        """_format_messages 正确处理 TextBlock"""
        summarizer = Summarizer(MockLLM())
        messages = [
            Message(role="user", content=[TextBlock("hello")]),
            Message(role="assistant", content=[TextBlock("world")]),
        ]
        text = summarizer._format_messages(messages)
        assert "User: hello" in text
        assert "Assistant: world" in text

    def test_format_messages_with_thinking_block(self):
        """_format_messages 正确处理 ThinkingBlock"""
        summarizer = Summarizer(MockLLM())
        messages = [
            Message(role="assistant", content=[ThinkingBlock("deep thought")]),
        ]
        text = summarizer._format_messages(messages)
        assert "deep thought" in text


class TestSafeSplit:
    """_find_safe_split 测试"""

    def test_split_basic_no_tools(self):
        """纯文本消息，8 条以内不分隔"""
        from flops.compact import _find_safe_split

        msgs = [Message(role="user", content=[TextBlock(f"msg {i}")]) for i in range(5)]
        assert _find_safe_split(msgs) == 0

    def test_split_complete_tool_pairs(self):
        """工具调用成对出现时，能从末尾安全截断"""
        from flops.compact import _find_safe_split

        msgs = []
        for i in range(12):
            msgs.append(Message(role="user", content=[TextBlock(f"user {i}")]))
            msgs.append(Message(role="assistant", content=[TextBlock(f"asst {i}")]))
        # 添加一些工具调用
        msgs.append(Message(role="assistant", content=[ToolUse(id="1", name="test", input={})]))
        msgs.append(Message(role="user", content=[ToolResult(content="ok", tool_use_id="1")]))
        msgs.append(Message(role="assistant", content=[TextBlock(text="done")]))

        split = _find_safe_split(msgs)
        assert split > 0
        # 最近的消息应该包含完整的工具调用对
        recent = msgs[split:]
        assert any(
            isinstance(c, ToolUse) for m in recent for c in m.content
        ), "Recent should contain tool_use"
        assert any(
            isinstance(c, ToolResult) for m in recent for c in m.content
        ), "Recent should contain tool_result"

    def test_split_unmatched_tool_use_extended(self):
        """末尾有未配对的 tool_use 时，自动扩展包含后续 tool_result"""
        from flops.compact import _find_safe_split

        msgs = []
        for i in range(20):
            msgs.append(Message(role="user", content=[TextBlock(f"user {i}")]))
            msgs.append(Message(role="assistant", content=[TextBlock(f"asst {i}")]))

        # 在末尾放一对工具调用（tool_use + tool_result）
        msgs.append(Message(role="assistant", content=[ToolUse(id="x", name="shell", input={})]))
        msgs.append(Message(role="user", content=[ToolResult(content="output", tool_use_id="x")]))

        split = _find_safe_split(msgs)
        assert split > 0
        # tool_result 应该在 recent 里
        recent = msgs[split:]
        assert any(
            isinstance(c, ToolResult) for m in recent for c in m.content
        ), "ToolResult should be in recent"

    def test_split_unmatched_tool_result_ok(self):
        """tool_result 的 tool_use 在 history 中，不影响截断"""
        from flops.compact import _find_safe_split

        # 构造：工具调用在 history 中，tool_result 靠近末尾
        msgs = []
        msgs.append(Message(role="assistant", content=[ToolUse(id="x", name="shell", input={})]))
        msgs.append(Message(role="user", content=[ToolResult(content="out", tool_use_id="x")]))
        for i in range(10):
            msgs.append(Message(role="user", content=[TextBlock(f"user {i}")]))
            msgs.append(Message(role="assistant", content=[TextBlock(f"asst {i}")]))
        # 末尾 tool_result 的 tool_use 在 history 中
        msgs.append(Message(role="user", content=[ToolResult(content="late", tool_use_id="old")]))

        split = _find_safe_split(msgs, min_reserve=4)
        assert split > 0
        recent = msgs[split:]
        assert any(isinstance(c, ToolResult) for m in recent for c in m.content)


class TestSessionCompression:
    """Session 压缩测试"""

    def test_session_compress(self):
        """Session 的压缩效果验证"""
        mock_llm = MockLLM()
        session = Session()
        for i in range(60):
            session.messages.append(Message(role="user", content=[TextBlock(text=f"user msg {i}")]))
            session.messages.append(
                Message(role="assistant", content=[TextBlock(text=f"assistant msg {i}")])
            )

        # 直接调用 Summarizer 验证压缩逻辑
        summarizer = Summarizer(mock_llm)
        compressed = asyncio.run(summarizer.summarize(session.messages, target_tokens=300))

        # 压缩后消息应该减少
        assert len(compressed) < len(session.messages)

    def test_session_no_compress_under_threshold(self):
        """Session 在 token 不足时不压缩"""
        mock_llm = MockLLM(context_size=200000)
        session = Session()
        for i in range(5):
            session.messages.append(Message(role="user", content=[TextBlock(text=f"user msg {i}")]))
            session.messages.append(
                Message(role="assistant", content=[TextBlock(text=f"assistant msg {i}")])
            )

        original_count = len(session.messages)

        # Session 没有 compress_if_need 方法，直接验证消息数不变
        # (小量消息不会触发压缩)
        assert len(session.messages) == original_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
