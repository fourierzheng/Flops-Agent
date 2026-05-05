from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from flops.const import MEMORY_DIR
from flops.event import TextDeltaEvent
from flops.llm import LLM
from flops.logger import logger
from flops.schemas import Message, TextBlock

CHARTER_FILENAME = "FLOPS.md"
DB_FILENAME = "STORE.db"

DEFAULT_CHARTER = """# Flops Charter

## User Preferences
- Language: Chinese

## Durable Facts
*(Facts will be promoted here from STORE.db as they gain confidence.)*

## Decisions & Rules
- Do exactly what's asked. No extras, no cleanup, no refactoring unless requested.
"""

DISTILL_SYSTEM_PROMPT = """You are a fact extraction assistant. Extract factual information from the conversation.

Return a JSON array of objects with these keys:
- domain: one of "user", "project", "habit", "decision", "context"
- key: short fact name (snake_case, e.g. "preferred_language")
- value: fact value (concise)

Rules:
- Only extract clear, specific, useful facts
- Skip vague, trivial, or one-time information
- Skip greetings, farewells, and small talk
- If no facts found, return an empty array []
- Output ONLY the JSON array, no other text

Example:
[{"domain": "project", "key": "language", "value": "Python"},
 {"domain": "habit", "key": "code_style", "value": "Black formatter"}]"""


class Memory:
    def __init__(self, memory_dir: str | Path | None = None):
        self._dir = Path(memory_dir or MEMORY_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)

        self._charter_path = self._dir / CHARTER_FILENAME
        self._db_path = self._dir / DB_FILENAME

        # Init FLOPS.md if not exists
        if not self._charter_path.exists():
            self._charter_path.write_text(DEFAULT_CHARTER, encoding="utf-8")
            logger.info(f"Created {self._charter_path}")

        # Init DB
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()
        logger.info(f"Memory initialized at {self._dir}")

        # Distill cursor: tracks how many messages have been processed
        self._last_distill_idx = 0

    def _init_db(self):
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                domain      TEXT NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                confidence  INTEGER DEFAULT 1,
                session_id  TEXT,
                promoted    INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(domain, key)
            )
        """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_domain ON facts(domain)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key)")
        self._conn.commit()

    # ── public api ──

    def read_charter(self) -> str:
        """Read FLOPS.md content."""
        return self._charter_path.read_text(encoding="utf-8")

    def query(
        self,
        domain: str | None = None,
        key: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query facts. All params optional — returns all when empty."""
        conditions: list[str] = []
        params: list[str] = []

        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if key:
            conditions.append("key = ?")
            params.append(key)
        if search:
            conditions.append("(key LIKE ? OR value LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT domain, key, value, confidence, session_id, promoted, created_at, updated_at "
            f"FROM facts {where} ORDER BY confidence DESC, updated_at DESC",
            params,
        ).fetchall()

        return [
            {
                "domain": r[0],
                "key": r[1],
                "value": r[2],
                "confidence": r[3],
                "session_id": r[4],
                "promoted": bool(r[5]),
                "created_at": r[6],
                "updated_at": r[7],
            }
            for r in rows
        ]

    def upsert(self, domain: str, key: str, value: str, mode: str = "auto") -> None:
        """Insert or update a fact.

        - mode="auto": increment confidence if exists (for auto-distill).
        - mode="confirm": set confidence to 5 (for user confirmation).
        """
        existing = self._conn.execute(
            "SELECT id, confidence FROM facts WHERE domain = ? AND key = ?",
            (domain, key),
        ).fetchone()

        if existing:
            new_confidence = 5 if mode == "confirm" else min(existing[1] + 1, 5)
            self._conn.execute(
                "UPDATE facts SET value = ?, confidence = ?, updated_at = datetime('now') WHERE id = ?",
                (value, new_confidence, existing[0]),
            )
        else:
            confidence = 5 if mode == "confirm" else 1
            self._conn.execute(
                "INSERT INTO facts (domain, key, value, confidence) VALUES (?, ?, ?, ?)",
                (domain, key, value, confidence),
            )
        self._conn.commit()

    async def distill(self, messages: list[Message], llm: LLM) -> int:
        """Extract facts from new messages since last distillation, then promote to FLOPS.md.

        Uses a cursor (_last_distill_idx) to only process incremental messages.
        If messages were compacted (len < cursor), resets cursor to 0.

        Returns number of facts extracted.
        """
        # Reset cursor if compaction happened
        if self._last_distill_idx >= len(messages):
            self._last_distill_idx = 0

        new_messages = messages[self._last_distill_idx :]
        if len(new_messages) < 2:
            return 0

        # Build conversation text for LLM
        conv_lines = []
        for msg in new_messages:
            role = "User" if msg.role == "user" else "Assistant"
            for block in msg.content:
                if isinstance(block, TextBlock):
                    conv_lines.append(f"{role}: {block.text}")

        if not conv_lines:
            self._last_distill_idx = len(messages)
            return 0

        conv_text = "\n".join(conv_lines)

        # Call LLM
        user_msg = Message(role="user", content=[TextBlock(f"Extract facts from:\n\n{conv_text}")])
        text_parts: list[str] = []
        async for event in llm.stream(DISTILL_SYSTEM_PROMPT, [], [user_msg]):
            if isinstance(event, TextDeltaEvent):
                text_parts.append(event.text.text)

        response = "".join(text_parts)

        # Parse JSON from response
        facts = self._parse_facts(response)
        if not facts:
            return 0

        # Upsert each fact with mode="auto" (confidence increments)
        count = 0
        for fact in facts:
            domain = fact.get("domain")
            key = fact.get("key")
            value = fact.get("value")
            if domain and key and value:
                self.upsert(domain=domain, key=key, value=value, mode="auto")
                count += 1

        if count:
            promoted = self._promote()
            logger.info(f"Distilled {count} facts, promoted {promoted} to FLOPS.md")

        # Advance cursor regardless of whether facts were found
        self._last_distill_idx = len(messages)
        return count

    def _parse_facts(self, text: str) -> list[dict[str, str]]:
        """Extract JSON array from LLM response text."""
        # Try to find JSON array in code block (handle trailing whitespace)
        match = re.search(r"```(?:json)?\s*\n(.+?)\n\s*```", text, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
        else:
            # Try bare JSON array — greedy match from first [ to last ]
            match = re.search(r"(\[.*\])", text, re.DOTALL)
            if not match:
                return []
            candidate = match.group(1).strip()

        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            # Some LLMs nest the JSON in extra text; try to extract the first valid array
            logger.warning(
                f"Failed to parse distill JSON directly, trying fallback: {candidate[:200]}"
            )
            # Fallback: find first [ and last ], try again
            start = candidate.find("[")
            end = candidate.rfind("]")
            if start != -1 and end > start:
                try:
                    data = json.loads(candidate[start : end + 1])
                    if isinstance(data, list):
                        return data
                except json.JSONDecodeError:
                    pass
        return []

    def _promote(self) -> int:
        """Promote high-confidence (>=3) unpromoted facts to FLOPS.md.

        Returns number of facts promoted.
        """
        rows = self._conn.execute(
            "SELECT id, domain, key, value FROM facts WHERE confidence >= 3 AND promoted = 0"
        ).fetchall()
        if not rows:
            return 0

        # Mark as promoted
        ids = [r[0] for r in rows]
        for fid in ids:
            self._conn.execute("UPDATE facts SET promoted = 1 WHERE id = ?", (fid,))
        self._conn.commit()

        # Build complete Durable Facts section from all promoted facts
        all_promoted = self._conn.execute(
            "SELECT domain, key, value FROM facts WHERE promoted = 1 ORDER BY domain, key"
        ).fetchall()

        by_domain = defaultdict(list)
        for domain, key, value in all_promoted:
            by_domain[domain].append((key, value))

        section_lines = []
        for domain in sorted(by_domain.keys()):
            for key, value in by_domain[domain]:
                section_lines.append(f"- {key}: {value}")

        section_content = "\n".join(section_lines)

        # Update FLOPS.md
        charter = self._charter_path.read_text(encoding="utf-8")

        placeholder = "*(Facts will be promoted here from STORE.db as they gain confidence.)*"
        if placeholder in charter:
            charter = charter.replace(placeholder, section_content)
        else:
            # Replace existing Durable Facts section content
            pattern = r"(## Durable Facts\n).*?(?=\n## |\Z)"
            replacement = f"## Durable Facts\n{section_content}\n\n"
            if re.search(pattern, charter, re.DOTALL):
                charter = re.sub(pattern, replacement, charter, count=1, flags=re.DOTALL)
            else:
                charter += f"\n## Durable Facts\n{section_content}\n"

        self._charter_path.write_text(charter, encoding="utf-8")
        logger.info(f"Promoted {len(rows)} facts to FLOPS.md")

        return len(rows)

    def close(self):
        self._conn.close()
