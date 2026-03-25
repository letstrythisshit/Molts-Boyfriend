"""Agent-local ephemeral key-value storage.

Each agent gets an isolated scratchpad for working notes during task execution.
Data is persisted to SQLite so it survives brief interruptions but is
explicitly cleared when a task completes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from nanobot.state.store import StateStore


class Scratchpad:
    """Per-agent ephemeral scratch space backed by SQLite."""

    def __init__(self, store: StateStore, agent_id: str) -> None:
        self._store = store
        self._agent_id = agent_id

    async def set(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._store.scratchpad_set(self._agent_id, key, value, now)

    async def get(self, key: str) -> str | None:
        return await self._store.scratchpad_get(self._agent_id, key)

    async def clear(self) -> None:
        await self._store.scratchpad_clear(self._agent_id)
