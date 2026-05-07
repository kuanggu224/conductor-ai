"""To-do domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class TodoItem:
    """A single to-do entry."""

    id: str
    title: str
    content: str = ""
    completed: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        payload = asdict(self)
        created_at = payload["created_at"]
        if isinstance(created_at, datetime):
            payload["created_at"] = created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return payload


__all__ = ["TodoItem"]
