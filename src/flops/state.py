from dataclasses import dataclass, field


@dataclass
class State:
    model: str
    session_id: str
    workspace: str
    history: list[str] = field(default_factory=list)
