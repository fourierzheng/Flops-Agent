import argparse
import asyncio
import os
import random
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown as RichMarkdown
from rich.panel import Panel
from rich.text import Text

from flops.config import Config
from flops.const import HISTORY_PATH
from flops.engine import Engine
from flops.event import (
    ErrorEvent,
    ExitEvent,
    LineEvent,
    NoticeEvent,
    StopEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolOutputEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from flops.logger import logger
from flops.schemas import StopReason, Usage
from flops.tools import render_tool

_SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Sentinel used as spinner placeholder in buffer — U+F000 is in Unicode Private Use Area,
# never produced by LLM output or user input, so no accidental collision.
_SPINNER_SENTINEL = "\uf000"
# Sentinels for tool-call boundaries (rendered in green).
_TOOL_START = "\uf001"
_TOOL_END = "\uf002"
# Sentinels for thinking/tool-output boundaries (rendered in bright_black).
_THINK_START = "\uf003"
_THINK_END = "\uf004"


class LiveRenderer:
    """Rich Live-based streaming renderer for terminal-safe output.

    Uses alternate screen (screen=True) to avoid polluting scrollback.
    Implements __rich_console__ so Live's auto-refresh drives spinner animation.
    """

    def __init__(self, console):
        self.console = console
        self.buffer = ""
        self._dim_text = ""  # real-time preview of pending thinking/tool-output
        self._live: Live | None = None
        self._spinner_active = False
        self._spinner_idx = 0
        self._waiting_active = False

    # ── Shared rendering logic ──────────────────────────────────────────

    @staticmethod
    def _render_buffer(content: str) -> list:
        """Split buffer by tool and thinking sentinels, return styled renderables."""
        renderables: list = []
        # First pass: split by tool call sentinels
        tool_segments = content.split(_TOOL_START)
        for i, seg in enumerate(tool_segments):
            md_text: str
            tool_text: str | None = None
            if i == 0:
                # First segment has no tool prefix
                md_text = seg
            elif _TOOL_END in seg:
                tool_text, md_text = seg.split(_TOOL_END, 1)
            else:
                tool_text = seg
                md_text = ""

            if tool_text:
                renderables.append(Text(tool_text.strip(), style="green"))

            # Second pass: split markdown text by thinking sentinels
            if md_text.strip():
                think_segments = md_text.split(_THINK_START)
                for j, ts in enumerate(think_segments):
                    if _THINK_END in ts:
                        think_content, rest = ts.split(_THINK_END, 1)
                        if think_content.strip():
                            renderables.append(
                                Panel(
                                    Text(think_content.strip()),
                                    box=ROUNDED,
                                    style="bright_black",
                                )
                            )
                        if rest.strip():
                            renderables.append(
                                RichMarkdown(
                                    rest.strip(),
                                    code_theme="ansi_dark",
                                    inline_code_theme="ansi_dark",
                                )
                            )
                    else:
                        if ts.strip():
                            renderables.append(
                                RichMarkdown(
                                    ts.strip(),
                                    code_theme="ansi_dark",
                                    inline_code_theme="ansi_dark",
                                )
                            )
        return renderables

    # ── Rich renderable protocol (called by Live on each refresh) ──────────

    def __rich_console__(self, console, options):
        """Render buffer as markdown, tool calls in green, dim_text preview at bottom."""
        renderables: list = []
        h = console.height or 40

        if self.buffer.strip():
            self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_CHARS)
            tail_lines = self.buffer.split("\n")[-(h - 6) :]
            tail = "\n".join(tail_lines)
            rendered = tail.replace(_SPINNER_SENTINEL, self._spinner_char)
            renderables = self._render_buffer(rendered)
        elif self._dim_text:  # no buffer yet, but dim_text preview active
            preview_lines = self._dim_text.split("\n")[-15:]
            renderables.append(Text("\n".join(preview_lines), style="bright_black"))

        if self._dim_text and self.buffer.strip():
            preview_lines = self._dim_text.split("\n")[-15:]
            renderables.append(Text("\n".join(preview_lines), style="bright_black"))

        if not renderables:
            from rich.text import Text as RichText

            tw = console.width or 60
            t = RichText()
            t.append("🤖 AI\n", style="magenta bold")
            t.append(f"{'─' * tw}\n", style="magenta dim")
            if self._waiting_active:
                self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_CHARS)
                t.append(f"\n{_SPINNER_CHARS[self._spinner_idx]} Processing...")
            yield t
            return

        yield Group(*renderables)

    @property
    def _spinner_char(self) -> str:
        if not self._spinner_active:
            return ""
        return _SPINNER_CHARS[self._spinner_idx]

    # ── Public API ────────────────────────────────────────────────────────

    def add_text(self, text: str) -> None:
        self._waiting_active = False
        self.buffer += text
        if self._live is not None:
            self._live.update(self, refresh=True)

    def start_spinner(self) -> None:
        self._spinner_active = True

    def start_waiting(self) -> None:
        """Show waiting spinner on the initial blank screen."""
        self._waiting_active = True

    def replace_spinner(self, replacement: str) -> None:
        """Replace the first sentinel in buffer with the result emoji."""
        self.buffer = self.buffer.replace(_SPINNER_SENTINEL, replacement, 1)
        self._spinner_active = False
        if self._live is not None:
            self._live.update(self, refresh=True)

    def set_dim_text(self, text: str) -> None:
        """Show real-time preview of pending thinking/tool-output at bottom."""
        self._dim_text = text
        if self._live is not None:
            self._live.update(self, refresh=True)

    def clear_dim_text(self) -> None:
        """Clear the real-time preview area."""
        if self._dim_text:
            self._dim_text = ""
            if self._live is not None:
                self._live.update(self, refresh=True)

    def finalize(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        # Print final content to main screen — clean, scrollback-safe
        if not self.buffer.strip():
            return
        content = self.buffer.replace(_SPINNER_SENTINEL, "")
        renderables = self._render_buffer(content)
        self.console.print(Group(*renderables))

    def __enter__(self):
        self._live = Live(
            self,  # uses __rich_console__ for rendering
            console=self.console,
            auto_refresh=True,
            refresh_per_second=10,
            screen=True,
        )
        self._live.start(refresh=True)
        return self

    def __exit__(self, *args):
        self.finalize()
        return False


def get_default_config_path() -> str:
    """Return the path to the default config.json in user's config directory."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(config_home, "flops", "config.json")


USER_EMOJIS = [
    # Faces
    "😀",
    "😃",
    "😄",
    "😁",
    "😊",
    "🙂",
    "😎",
    "🤩",
    "🥳",
    "😏",
    "🤔",
    "🤗",
    "😜",
    "😌",
    "😺",
    "😸",
    # People
    "👤",
    "🧑",
    "👨",
    "👩",
    "🧑‍💻",
    "👋",
    "🙋",
    "💁",
    "🙌",
    # Symbols
    "💬",
    "🗣️",
    "💭",
    "💡",
    "🎯",
    "✨",
    "🚀",
    "🎉",
    "🔥",
    "⭐",
    "💪",
    "🧠",
    "🎵",
    "🎶",
]


def truncate(text: str, max_len: int) -> str:
    """Truncate text, preserving tail with ellipsis."""
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    return "…" + text[-(max_len - 1) :]


FONT = {
    "O": [
        " █████ ",
        "██   ██",
        "██   ██",
        "██   ██",
        "██   ██",
        "██   ██",
        " █████ ",
    ],
    "D": [
        "█████  ",
        "██  ██ ",
        "██   ██",
        "██   ██",
        "██   ██",
        "██  ██ ",
        "█████  ",
    ],
    "F": [
        "███████",
        "██     ",
        "█████  ",
        "██     ",
        "██     ",
        "██     ",
        "██     ",
    ],
    "L": [
        "██     ",
        "██     ",
        "██     ",
        "██     ",
        "██     ",
        "██     ",
        "███████",
    ],
    "A": [
        " █████ ",
        "██   ██",
        "██   ██",
        "███████",
        "██   ██",
        "██   ██",
        "██   ██",
    ],
    "S": [
        " █████ ",
        "██   ██",
        "██     ",
        " █████ ",
        "     ██",
        "██   ██",
        " █████ ",
    ],
    "P": [
        "██████ ",
        "██   ██",
        "██   ██",
        "██████ ",
        "██     ",
        "██     ",
        "██     ",
    ],
    "G": [
        " █████ ",
        "██   ██",
        "██     ",
        "██  ███",
        "██   ██",
        "██   ██",
        " █████ ",
    ],
    "E": [
        "███████",
        "██     ",
        "█████  ",
        "██     ",
        "██     ",
        "██     ",
        "███████",
    ],
    "N": [
        "██   ██",
        "███  ██",
        "████ ██",
        "██ ████",
        "██  ███",
        "██   ██",
        "██   ██",
    ],
    "T": [
        "███████",
        "   ██  ",
        "   ██  ",
        "   ██  ",
        "   ██  ",
        "   ██  ",
        "   ██  ",
    ],
    "-": [
        "       ",
        "       ",
        "███████",
        "       ",
        "       ",
        "       ",
        "       ",
    ],
    " ": ["       "] * 7,
}


_GRADIENT = [
    "#004D40",
    "#005448",
    "#005C50",
    "#006458",
    "#006C60",
    "#007468",
    "#007C70",
    "#008478",
    "#008C80",
    "#009488",
    "#009C90",
    "#00A498",
    "#00ACA0",
    "#00B4A8",
    "#00BCB0",
]


def render(text: str) -> Text:
    rows = [""] * 7
    text = text.upper()

    for ch in text:
        pattern = FONT.get(ch, FONT[" "])
        for i in range(7):
            rows[i] += pattern[i] + "  "

    result = Text()

    for row in rows:
        stripped = row.rstrip("\n")
        for ci, ch in enumerate(stripped):
            idx = min(ci * len(_GRADIENT) // max(len(stripped), 1), len(_GRADIENT) - 1)
            result.append(ch, style=_GRADIENT[idx])
        result.append("\n")

    return result


# =============================================================================
# Global console
# =============================================================================

console = Console(force_terminal=True)


# =============================================================================
# UI Components
# =============================================================================


class Header:
    """ASCII art header display."""

    MAX_WIDTH = 205
    MIN_WIDTH = 115  # minimum to fit "Flops Agent" ASCII art + borders

    @classmethod
    def show(cls, engine: Optional[Engine] = None):
        """Print welcome header with ASCII art and session info."""
        tw = console.width
        panel_width = max(cls.MIN_WIDTH, min(cls.MAX_WIDTH, tw - 2)) if tw else cls.MAX_WIDTH
        content_w = panel_width - 3

        lines = []
        if engine:
            max_val_width = content_w - 16
            for label, value in [
                ("Session", truncate(str(engine.session_id), max_val_width)),
                ("Model", truncate(str(engine.model), max_val_width)),
                ("Workspace", truncate(str(engine.workspace), max_val_width)),
            ]:
                lines.append(Text(f"    {label:>10}: ", style="dim") + Text(value, style="white"))

        panel = Panel(
            Group(render("Flops Agent"), *lines),
            width=panel_width,
            border_style="dim",
        )
        console.print(panel)
        console.print()


class Prompt:
    """User input prompt."""

    _emoji = "💬"

    @classmethod
    def pick(cls):
        """Pick a random emoji for the next prompt."""
        cls._emoji = random.choice(USER_EMOJIS)

    @classmethod
    def get(cls) -> str:
        """Get user input prompt with fixed emoji."""
        return f"\033[36m{cls._emoji} You:\033[0m "


class Divider:
    """Divider lines."""

    @staticmethod
    def response_footer(usage: str = "") -> str:
        """Return response footer with usage."""
        tw = console.width or 60
        usage_len = len(Text.from_markup(usage).plain) if usage else 0
        divider_len = max(tw - usage_len, 10)
        divider = "─" * divider_len
        return f"[cyan dim]{divider}[/cyan dim]{usage}"


class Message:
    """Colored message display."""

    @staticmethod
    def _print(style: str, text: str, newline: bool = True):
        sep = "\n" if newline else ""
        console.print(f"{sep}[{style}]{text}[/{style}]")

    @staticmethod
    def error(text: str, newline: bool = True):
        Message._print("red bold", f"❌ {text}", newline)

    @staticmethod
    def success(text: str, newline: bool = True):
        Message._print("green", f"✓ {text}", newline)

    @staticmethod
    def info(text: str, newline: bool = True):
        Message._print("cyan", text, newline)

    @staticmethod
    def warning(text: str, newline: bool = True):
        Message._print("yellow", text, newline)

    @staticmethod
    def dim(text: str, newline: bool = True):
        Message._print("dim", text, newline)

    @staticmethod
    def newline():
        console.print()


# =============================================================================
# Chat display (replaces EventFormatter + manual stdout writes)
# =============================================================================


class ChatDisplay:
    """Streaming display: uses Rich Live for scrollback-safe rendering."""

    def __init__(self):
        self._renderer = LiveRenderer(console)
        self._thinking: str = ""
        self._tool_output: str = ""

    def __enter__(self):
        self._renderer.__enter__()
        self._renderer.start_waiting()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._renderer.__exit__(exc_type, exc_val, exc_tb)
        return False

    # -- Public API ----------------------------------------------------------

    def _ensure_separator(self):
        """Ensure buffer ends with a markdown line break (two spaces + newline)."""
        if self._renderer.buffer.endswith("  \n"):
            return
        if self._renderer.buffer.endswith("\n"):
            self._renderer.add_text("\n")
        else:
            self._renderer.add_text("\n\n")

    @staticmethod
    def _format_thinking(text: str) -> str:
        """Format thinking/tool-output for buffer (plain text, gray preview handles coloring)."""
        return text.rstrip("\n")

    def _flush_thinking(self):
        if self._thinking:
            text = self._format_thinking(self._thinking)
            self._renderer.add_text(f"\n{_THINK_START}{text}{_THINK_END}\n")
            self._thinking = ""
            self._renderer.clear_dim_text()

    def _flush_tool_output(self):
        if self._tool_output:
            text = self._format_thinking(self._tool_output)
            self._renderer.add_text(f"\n{_THINK_START}{text}{_THINK_END}\n")
            self._tool_output = ""
            self._renderer.clear_dim_text()

    def add_thinking(self, text: str):
        self._thinking += text
        self._renderer.set_dim_text(self._thinking)

    def add_tool_output(self, text: str):
        self._flush_thinking()
        self._tool_output += text
        self._renderer.set_dim_text(self._tool_output)

    def add_text(self, text: str):
        self._flush_thinking()
        self._flush_tool_output()
        self._renderer.add_text(text)

    def add_line(self, line: str):
        self._flush_thinking()
        self._flush_tool_output()
        self._renderer.add_text(f"{line}  \n")

    def add_notice(self, line: str):
        self._flush_thinking()
        self._flush_tool_output()
        self._ensure_separator()
        self._renderer.add_text(f"> ⚠️ {line}  \n")

    def add_tool(self, tool_use: str):
        self._flush_thinking()
        self._flush_tool_output()
        self._ensure_separator()
        self._renderer.add_text(f"{_TOOL_START}\n🔧 {tool_use} {_SPINNER_SENTINEL}  \n{_TOOL_END}")
        self._renderer.start_spinner()

    def add_interrupt(self):
        self._flush_thinking()
        self._flush_tool_output()
        self._ensure_separator()
        self._renderer.add_text("> ⚡ Interrupted by user  \n")

    def add_error(self, content: str):
        self._flush_thinking()
        self._flush_tool_output()
        self._ensure_separator()
        self._renderer.add_text(f"> ❌ {content}  \n")

    def add_tool_success(self, success: bool = True):
        self._flush_tool_output()
        self._renderer.replace_spinner("✅" if success else "❌")


# =============================================================================
# Event handling
# =============================================================================


def format_session_usage(usage: Usage, model: dict) -> str:
    """Format session usage summary string."""
    total_tokens = usage.input_tokens + usage.output_tokens
    available = model["context_size"]
    pct = total_tokens / available * 100
    model_str = f" [{model['model_name']}]" if model else ""
    return f"[dim]· {pct:.0f}% ({total_tokens:,} tok){model_str}[/dim]"


async def handle_chat(engine: Engine, user_input: str):
    """Handle a single conversation turn. Returns 'exit' if ExitEvent was received."""
    from flops.logger import logger

    # Log first word only (command or brief description) to avoid logging huge inputs
    first_word = user_input.split()[0] if user_input else ""
    logger.info(f"Chat turn: {first_word}...")
    usage: Usage | None = None
    last_tool_name: str | None = None
    with ChatDisplay() as display:
        try:
            stream = engine.run(user_input)
            async for event in stream:
                if isinstance(event, LineEvent):
                    display.add_line(event.line)
                    continue
                if isinstance(event, NoticeEvent):
                    display.add_notice(event.line)
                    continue
                if isinstance(event, ThinkingEvent):
                    display.add_thinking(event.thinking.thinking)
                    continue

                if isinstance(event, TextDeltaEvent):
                    display.add_text(event.text.text)
                    continue

                if isinstance(event, ToolUseEvent):
                    last_tool_name = event.tool_use.name
                    display.add_tool(render_tool(event.tool_use))
                    continue

                if isinstance(event, ToolResultEvent):
                    if event.result.is_error:
                        display.add_error(event.result.content)
                        if last_tool_name:
                            display.add_tool_success(success=False)
                    elif last_tool_name:
                        display.add_tool_success()
                    continue

                if isinstance(event, ToolOutputEvent):
                    display.add_tool_output(event.text)
                    continue
                if isinstance(event, UsageEvent):
                    usage = event.usage
                if isinstance(event, StopEvent) and event.reason == StopReason.INTERRUPT:
                    display.add_interrupt()
                    break

                if isinstance(event, ErrorEvent):
                    display.add_error(f"{type(event.error).__name__}: {event.error}")
                    break
                if isinstance(event, ExitEvent):
                    return "exit"

        except Exception as e:
            Message.error(f"Error: {e}")
            logger.exception("Command error")
            return None

    if usage is not None:
        usage_str = format_session_usage(usage, engine.get_model_info())
        console.print(Divider.response_footer(usage_str))
    console.print()
    return None


# =============================================================================
# Main
# =============================================================================


async def async_main(config_path: Optional[str] = None):
    """Main application logic."""
    from flops.logger import config_log

    if config_path is None:
        config_path = get_default_config_path()

    config = Config.from_json(config_path)
    config_log(config.log)
    engine = Engine(config)

    Header.show(engine)
    Message.dim("Tip: Type /help for all commands")
    Message.newline()

    prompt_session: PromptSession[str] = PromptSession(
        message=lambda: ANSI(Prompt.get()),
        history=FileHistory(HISTORY_PATH),
    )

    while True:
        try:
            Prompt.pick()
            user_input = await prompt_session.prompt_async()

            if not user_input:
                continue
            result = await handle_chat(engine, user_input)
            if result == "exit":
                Message.info("👋 Goodbye!")
                break

        except KeyboardInterrupt:
            print()
        except EOFError:
            Message.info("👋 Goodbye!")
            break
        except Exception as e:
            Message.error(f"An error occurred: {e}")
            logger.exception("Chat error")


def main():
    """CLI entry point."""
    default_config = get_default_config_path()

    parser = argparse.ArgumentParser(description="Flops Agent - An intelligent AI assistant")
    parser.add_argument(
        "-c",
        "--config",
        default=default_config,
        help=f"Path to config file (default: {default_config})",
    )
    args = parser.parse_args()

    try:
        asyncio.run(async_main(args.config))
    except (SystemExit, KeyboardInterrupt):
        pass
    except Exception as e:
        Message.error(f"Error: {e}")
        logger.exception("Main error")


if __name__ == "__main__":
    main()
