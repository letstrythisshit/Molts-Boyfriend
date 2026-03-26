"""Multi-layer memory abstraction.

Layers:
  SHORT_TERM  - current task context (in-memory only, not persisted)
  WORKFLOW    - shared across tasks in a workflow
  PROJECT     - workspace-scoped, persists across sessions
  LONG_TERM   - user facts/preferences (complements existing MEMORY.md)
  SOURCE      - external source citations, URLs, excerpts
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from nanobot.state.store import StateStore


class MemoryLayer(str, Enum):
    SHORT_TERM = "short_term"
    WORKFLOW = "workflow"
    PROJECT = "project"
    LONG_TERM = "long_term"
    SOURCE = "source"


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    layer: MemoryLayer
    scope_id: str
    key: str
    value: str
    source: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    expires_at: str | None = None

    def to_db_dict(self) -> dict:
        return {
            "id": self.id,
            "layer": self.layer.value,
            "scope_id": self.scope_id,
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_db_dict(cls, d: dict) -> MemoryEntry:
        return cls(
            id=d["id"],
            layer=MemoryLayer(d["layer"]),
            scope_id=d["scope_id"],
            key=d["key"],
            value=d["value"],
            source=d.get("source"),
            created_at=d["created_at"],
            expires_at=d.get("expires_at"),
        )


class LayeredMemory:
    """Read/write across memory layers with scope filtering."""

    def __init__(self, store: StateStore) -> None:
        self._store = store
        self._short_term: dict[str, dict[str, str]] = {}

    async def write(self, entry: MemoryEntry) -> None:
        if entry.layer == MemoryLayer.SHORT_TERM:
            scope = self._short_term.setdefault(entry.scope_id, {})
            scope[entry.key] = entry.value
            return
        await self._store.save_memory_entry(entry.to_db_dict())

    async def read(
        self, layer: MemoryLayer, scope_id: str, key: str | None = None
    ) -> list[MemoryEntry]:
        if layer == MemoryLayer.SHORT_TERM:
            scope = self._short_term.get(scope_id, {})
            if key:
                val = scope.get(key)
                if val is None:
                    return []
                return [
                    MemoryEntry(
                        layer=MemoryLayer.SHORT_TERM,
                        scope_id=scope_id,
                        key=key,
                        value=val,
                    )
                ]
            return [
                MemoryEntry(
                    layer=MemoryLayer.SHORT_TERM,
                    scope_id=scope_id,
                    key=k,
                    value=v,
                )
                for k, v in scope.items()
            ]
        rows = await self._store.query_memory(layer.value, scope_id, key)
        return [MemoryEntry.from_db_dict(r) for r in rows]

    async def read_all_for_scope(self, scope_id: str) -> list[MemoryEntry]:
        """Read all memory entries across all persistent layers for a scope."""
        results: list[MemoryEntry] = []
        for layer in MemoryLayer:
            results.extend(await self.read(layer, scope_id))
        return results

    async def delete(self, entry_id: str) -> None:
        await self._store.delete_memory(entry_id)

    async def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        return await self._store.delete_expired_memory(now)

    def clear_short_term(self, scope_id: str) -> None:
        self._short_term.pop(scope_id, None)
