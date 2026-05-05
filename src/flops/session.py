import atexit
import json
import os
import time
import uuid
from dataclasses import asdict

from flops.const import SESSIONS_DIR
from flops.logger import logger
from flops.schemas import Message, TextBlock, ThinkingBlock, ToolResult, ToolUse, Usage

CONTENT_TYPE = {
    "text": TextBlock,
    "thinking": ThinkingBlock,
    "tool_use": ToolUse,
    "tool_result": ToolResult,
}
_TYPE = {v: k for k, v in CONTENT_TYPE.items()}


def now(format: str = "%Y%m%d-%H%M%S"):
    return time.strftime(format, time.localtime())


class Conversation:
    def __init__(self, immutable_history: list[Message] | None = None):
        self._history = immutable_history or []
        self.messages: list[Message] = []
        self._last_usage = Usage(input_tokens=0, output_tokens=0)

    def _append_content(self, role: str, content: TextBlock | ThinkingBlock | ToolUse | ToolResult):
        logger.debug(f"append {role} message: {content}")
        if self.messages and self.messages[-1].role == role:
            last_content = self.messages[-1].content[-1]
            if isinstance(last_content, TextBlock) and isinstance(content, TextBlock):
                last_content.text += content.text
            elif isinstance(last_content, ThinkingBlock) and isinstance(content, ThinkingBlock):
                last_content.thinking += content.thinking
            else:
                self.messages[-1].content.append(content)
        else:
            self.messages.append(Message(role=role, content=[content]))

    def add_user_message(self, content: TextBlock):
        self._append_content("user", content)

    def add_llm_message(self, content: TextBlock):
        self._append_content("assistant", content)

    def add_llm_thinking(self, thinking: ThinkingBlock):
        self._append_content("assistant", thinking)

    def add_tool_use(self, tool_use: ToolUse):
        self._append_content("assistant", tool_use)

    def add_tool_result(self, tool_result: ToolResult):
        self._append_content("user", tool_result)

    def update_usage(self, usage: Usage):
        self._last_usage = usage

    @property
    def last_usage(self) -> Usage:
        return self._last_usage

    @property
    def last_total_tokens(self) -> int:
        return self._last_usage.input_tokens + self._last_usage.output_tokens

    def get_messages(self) -> list[Message]:
        return self._history + self.messages


class Session:
    def __init__(self):
        self.session_id = f"{uuid.uuid4().hex[:8]}-{now()}"
        self.messages: list[Message] = []
        self._last_usage = Usage(input_tokens=0, output_tokens=0)
        self._cleanup_old_sessions()
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._session_file = open(SESSIONS_DIR / f"{self.session_id}", "w")
        atexit.register(self.close)
        logger.info(f"Session created: {self.session_id}")

    def _cleanup_old_sessions(self):
        """Clean up session files older than 30 days."""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        now_ts = time.time()
        for f in os.listdir(SESSIONS_DIR):
            try:
                mtime = os.path.getmtime(SESSIONS_DIR / f)
                if now_ts - mtime > 30 * 86400:
                    (SESSIONS_DIR / f).unlink()
            except OSError:
                pass

    def _add_message(self, msg: Message):
        logger.debug(f"Adding message: {msg}")
        self.messages.append(msg)

        # Serialize content items to dicts for JSON storage
        record: dict[str, object] = {
            "time": now(format="%Y-%m-%d %H:%M:%S"),
            "role": msg.role,
            "content": [{"type": _TYPE[type(item)], **asdict(item)} for item in msg.content],
        }
        self._session_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._session_file.flush()

    def reset_messages(self, messages: list[Message]):
        # Write compressed marker
        self._session_file.write(
            json.dumps({"time": now(format="%Y-%m-%d %H:%M:%S"), "compressed": True}) + "\n"
        )
        self.messages = []
        for msg in messages:
            self._add_message(msg)

        self._session_file.flush()
        self._last_usage = Usage(input_tokens=0, output_tokens=0)

    @property
    def last_total_tokens(self) -> int:
        """Get the total tokens from most recent API call"""
        return self._last_usage.input_tokens + self._last_usage.output_tokens

    def get_messages(self) -> list[Message]:
        api_messages: list[Message] = []
        for msg in self.messages:
            if api_messages and api_messages[-1].role == msg.role:
                api_messages[-1].content.extend(msg.content)
            else:
                api_messages.append(Message(role=msg.role, content=list(msg.content)))
        return api_messages

    def add_conversation(self, conversation: Conversation):
        for msg in conversation.messages:
            self._add_message(msg)
        self._last_usage = conversation.last_usage

    def close(self):
        """Close session file."""
        if self._session_file and not self._session_file.closed:
            self._session_file.close()

    def restore(self, session_id: str):
        """Restore session from a session log file (JSON Lines format).

        Parses each line: if normal message, adds to messages list.
        If encounters {"compressed": true}, clears messages and only keeps compressed messages after it.
        """

        # Clean up empty session file created by __init__, if any
        if self._session_file and not self._session_file.closed:
            tmp_path = self._session_file.name
            if self._session_file.tell() == 0:
                self._session_file.close()
                os.unlink(tmp_path)
            else:
                self._session_file.close()

        self.session_id = session_id
        self.messages = []
        self._last_usage = Usage(input_tokens=0, output_tokens=0)

        # Parse existing log
        filepath = SESSIONS_DIR / session_id
        logger.info(f"Restoring session from {filepath}")
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # Try legacy format (repr-based)
                    logger.warning(f"Skipping legacy format line: {line[:100]}...")
                    continue

                if record.get("compressed"):
                    # Clear all previous messages, only keep compressed ones after this
                    self.messages = []
                    continue

                role = record.get("role")
                content_dicts = record.get("content", [])

                # Deserialize content items
                content = []
                for d in content_dicts:
                    item_type = d.pop("type")
                    if item_type in CONTENT_TYPE:
                        content.append(CONTENT_TYPE[item_type](**d))

                self.messages.append(Message(role=role, content=content))

        # Reopen in append mode to continue logging
        self._session_file = open(filepath, "a")

        # Estimate token usage from restored messages so compaction can trigger if needed
        if self.messages:
            serialized = json.dumps(
                [
                    {
                        "role": m.role,
                        "content": [{"type": _TYPE[type(c)], **asdict(c)} for c in m.content],
                    }
                    for m in self.messages
                ],
                ensure_ascii=False,
            )
            self._last_usage = Usage(
                input_tokens=len(serialized) // 4,
                output_tokens=0,
            )

        logger.info(f"Session restored from {filepath} ({len(self.messages)} messages)")

    @classmethod
    def list_sessions(cls):
        return os.listdir(SESSIONS_DIR)

    def clear(self):
        self.messages = []  # Clear current messages, will rewrite compressed ones
        self._session_file.write(
            json.dumps({"time": now(format="%Y-%m-%d %H:%M:%S"), "compressed": True}) + "\n"
        )
        self._session_file.flush()

    def undo_last_turn(self) -> int:
        """Remove the last conversation (user input + AI response + tool calls) from session. Returns number of messages removed."""
        if not self.messages:
            return 0

        # Scan backwards: find where the last turn starts (user message with TextBlock)
        remove_count = 0
        for i in range(len(self.messages) - 1, -1, -1):
            msg = self.messages[i]
            is_turn_start = msg.role == "user" and any(
                isinstance(c, TextBlock) for c in msg.content
            )
            remove_count += 1
            if is_turn_start:
                break

        if remove_count == 0:
            return 0

        # Truncate file: remove lines corresponding to the removed messages,
        # accounting for compressed markers that don't map to messages.
        if not self._session_file.closed:
            filepath = self._session_file.name
            self._session_file.close()

            with open(filepath, "r") as f:
                lines = f.readlines()

            # Scan backwards, counting only message lines (those with "role")
            msg_seen = 0
            cut_at = len(lines)
            for j in range(len(lines) - 1, -1, -1):
                try:
                    record = json.loads(lines[j])
                    if record.get("role"):
                        msg_seen += 1
                except json.JSONDecodeError:
                    pass
                if msg_seen == remove_count:
                    cut_at = j
                    break

            with open(filepath, "w") as f:
                f.writelines(lines[:cut_at])

            self._session_file = open(filepath, "a")

        # Remove from memory
        self.messages = self.messages[:-remove_count]
        return remove_count
