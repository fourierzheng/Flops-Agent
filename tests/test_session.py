import json
import os

from flops.schemas import Message, TextBlock, ToolResult, ToolUse
from flops.session import Session


def test_session_basic():
    """Test basic session creation and message adding."""
    s = Session()
    s._add_message(Message(role="user", content=[TextBlock(text="hello")]))
    s._add_message(Message(role="assistant", content=[TextBlock(text="hi there")]))

    assert len(s.messages) == 2
    assert s.messages[0].role == "user"
    assert s.messages[1].role == "assistant"

    path = s._session_file.name
    s.close()

    # Verify file content
    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["role"] == "user"
    assert json.loads(lines[1])["role"] == "assistant"

    # Cleanup
    os.unlink(path)


def test_session_tool_messages():
    """Test session with tool_use and tool_result messages."""
    s = Session()
    s._add_message(Message(role="user", content=[TextBlock(text="run ls")]))
    s._add_message(
        Message(role="assistant", content=[ToolUse(id="1", name="shell", input={"command": "ls"})])
    )
    s._add_message(Message(role="user", content=[ToolResult(content="file.txt", tool_use_id="1")]))

    assert len(s.messages) == 3
    assert isinstance(s.messages[1].content[0], ToolUse)
    assert isinstance(s.messages[2].content[0], ToolResult)

    path = s._session_file.name
    s.close()
    os.unlink(path)


def test_session_restore():
    """Test restore from session file."""
    s = Session()
    s._add_message(Message(role="user", content=[TextBlock(text="hello")]))
    s._add_message(Message(role="assistant", content=[TextBlock(text="hi there")]))

    session_id = s.session_id
    path = s._session_file.name
    s.close()

    # Restore - instance method that hard-switches current session
    s2 = Session()
    s2.restore(session_id)
    assert len(s2.messages) == 2
    assert s2.messages[0].role == "user"
    assert isinstance(s2.messages[0].content[0], TextBlock)
    assert s2.messages[0].content[0].text == "hello"
    assert s2.messages[1].role == "assistant"
    assert s2.messages[1].content[0].text == "hi there"

    s2.close()
    os.unlink(path)


def test_session_restore_with_tools():
    """Test restore session containing tool messages."""
    s = Session()
    s._add_message(Message(role="user", content=[TextBlock(text="run ls")]))
    s._add_message(
        Message(role="assistant", content=[ToolUse(id="1", name="shell", input={"command": "ls"})])
    )
    s._add_message(Message(role="user", content=[ToolResult(content="file.txt", tool_use_id="1")]))

    session_id = s.session_id
    path = s._session_file.name
    s.close()

    s2 = Session()
    s2.restore(session_id)
    assert len(s2.messages) == 3
    assert isinstance(s2.messages[1].content[0], ToolUse)
    assert s2.messages[1].content[0].name == "shell"
    assert isinstance(s2.messages[2].content[0], ToolResult)
    assert s2.messages[2].content[0].content == "file.txt"

    s2.close()
    os.unlink(path)


def test_session_restore_with_compression():
    """Test restore with compressed marker - only keep messages after compression."""
    s = Session()
    s._add_message(Message(role="user", content=[TextBlock(text="msg1")]))
    s._add_message(Message(role="assistant", content=[TextBlock(text="reply1")]))

    # Write compressed marker
    s._session_file.write(json.dumps({"time": "2026-04-27 15:00:00", "compressed": True}) + "\n")
    s._session_file.flush()

    # Messages after compression
    s.messages = [Message(role="system", content=[TextBlock(text="[Summary]")])]
    s._add_message(Message(role="user", content=[TextBlock(text="msg2")]))

    session_id = s.session_id
    path = s._session_file.name
    s.close()

    s2 = Session()
    s2.restore(session_id)
    # Only messages after compressed marker should remain
    assert len(s2.messages) == 1
    assert s2.messages[0].role == "user"
    assert s2.messages[0].content[0].text == "msg2"

    s2.close()
    os.unlink(path)


def test_session_reset_messages():
    """Test reset_messages (compression simulation)."""
    s = Session()
    s._add_message(Message(role="user", content=[TextBlock(text="old msg")]))
    s._add_message(Message(role="assistant", content=[TextBlock(text="old reply")]))

    # Reset with new messages
    new_messages = [Message(role="system", content=[TextBlock(text="[Summary] compressed")])]
    s.reset_messages(new_messages)

    assert len(s.messages) == 1
    assert s.messages[0].role == "system"
    assert "compressed" in s.messages[0].content[0].text

    path = s._session_file.name
    s.close()
    os.unlink(path)


def test_undo_last_turn_with_compressed_marker():
    """undo_last_turn correctly skips compressed marker lines when truncating file."""
    s = Session()
    # Add 2 turns
    s._add_message(Message(role="user", content=[TextBlock(text="turn1")]))
    s._add_message(Message(role="assistant", content=[TextBlock(text="reply1")]))
    # Simulate compression
    s._add_message(Message(role="user", content=[TextBlock(text="turn2")]))
    s._add_message(Message(role="assistant", content=[TextBlock(text="reply2")]))

    # Now compress: write compressed marker, reset messages in memory
    s._session_file.write(json.dumps({"time": "2026-01-01 00:00:00", "compressed": True}) + "\n")
    s._session_file.flush()
    s.messages = s.messages[:2]  # Keep only first turn in memory

    # Add a new turn (post-compression)
    s._add_message(Message(role="user", content=[TextBlock(text="turn3")]))
    s._add_message(Message(role="assistant", content=[TextBlock(text="reply3")]))

    assert len(s.messages) == 4

    # Undo the last turn — should remove turn3 + reply3 lines, keep compressed marker
    removed = s.undo_last_turn()
    assert removed == 2

    # Verify file content
    path = s._session_file.name
    with open(path) as f:
        lines = f.readlines()
    s.close()

    # Should have: turn1, reply1, turn2, reply2, compressed_marker (5 lines)
    # The undo only removes the last turn (turn3/reply3), not the pre-compression turn2/reply2
    assert len(lines) == 5
    assert json.loads(lines[0])["role"] == "user"
    assert json.loads(lines[1])["role"] == "assistant"
    assert json.loads(lines[2])["role"] == "user"
    assert json.loads(lines[3])["role"] == "assistant"
    assert json.loads(lines[4]).get("compressed") is True

    os.unlink(path)


if __name__ == "__main__":
    test_session_basic()
    test_session_tool_messages()
    test_session_restore()
    test_session_restore_with_tools()
    test_session_restore_with_compression()
    test_session_reset_messages()
    test_undo_last_turn_with_compressed_marker()
    print("All session tests passed!")
