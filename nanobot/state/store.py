"""SQLite-backed durable state store with WAL mode for crash safety."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import aiosqlite

_DEFAULT_DB_DIR = os.path.expanduser("~/.nanobot")
_DEFAULT_DB_NAME = "state.db"


def _default_db_path() -> str:
    return os.path.join(_DEFAULT_DB_DIR, _DEFAULT_DB_NAME)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflows (
    id            TEXT PRIMARY KEY,
    goal          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    originator_channel TEXT NOT NULL DEFAULT '',
    originator_chat_id TEXT NOT NULL DEFAULT '',
    pause_requested INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    parent_id       TEXT,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'executor',
    status          TEXT NOT NULL DEFAULT 'pending',
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    assigned_agent_id TEXT,
    model_override  TEXT,
    max_iterations  INTEGER NOT NULL DEFAULT 40,
    token_budget    INTEGER,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 2,
    result          TEXT,
    error           TEXT,
    artifacts_json  TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_workflow ON tasks(workflow_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS memory_entries (
    id        TEXT PRIMARY KEY,
    layer     TEXT NOT NULL,
    scope_id  TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    source    TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_layer_scope ON memory_entries(layer, scope_id);

CREATE TABLE IF NOT EXISTS scratchpad (
    agent_id  TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, key)
);

CREATE TABLE IF NOT EXISTS decision_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    agent_role TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    reasoning  TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_task ON decision_log(task_id);
CREATE INDEX IF NOT EXISTS idx_decisions_workflow ON decision_log(workflow_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    workflow_id TEXT NOT NULL DEFAULT '',
    type       TEXT NOT NULL DEFAULT 'file',
    path       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT,
    tool_name  TEXT NOT NULL,
    input_summary TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'ok',
    created_at TEXT NOT NULL
);
"""


class StateStore:
    """Async SQLite store with WAL mode.

    All writes happen inside transactions.  WAL mode ensures readers never
    block writers and incomplete transactions are rolled back on crash.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _default_db_path()
        self._db: aiosqlite.Connection | None = None

    # -- lifecycle -----------------------------------------------------------

    async def open(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("StateStore is not open. Call open() first.")
        return self._db

    # -- workflows -----------------------------------------------------------

    async def save_workflow(self, wf: dict) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO workflows
               (id, goal, status, originator_channel, originator_chat_id,
                pause_requested, cancel_requested, created_at, updated_at, metadata_json)
               VALUES (:id, :goal, :status, :originator_channel, :originator_chat_id,
                       :pause_requested, :cancel_requested, :created_at, :updated_at,
                       :metadata_json)""",
            wf,
        )
        await self.db.commit()

    async def load_workflow(self, workflow_id: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def load_incomplete_workflows(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM workflows WHERE status NOT IN ('completed', 'cancelled')"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_workflow_status(self, workflow_id: str, status: str, updated_at: str) -> None:
        await self.db.execute(
            "UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?",
            (status, updated_at, workflow_id),
        )
        await self.db.commit()

    # -- tasks ---------------------------------------------------------------

    async def save_task(self, task: dict) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO tasks
               (id, workflow_id, parent_id, title, description, role, status,
                depends_on_json, assigned_agent_id, model_override, max_iterations,
                token_budget, retry_count, max_retries, result, error,
                artifacts_json, created_at, started_at, completed_at, metadata_json)
               VALUES (:id, :workflow_id, :parent_id, :title, :description, :role, :status,
                       :depends_on_json, :assigned_agent_id, :model_override, :max_iterations,
                       :token_budget, :retry_count, :max_retries, :result, :error,
                       :artifacts_json, :created_at, :started_at, :completed_at,
                       :metadata_json)""",
            task,
        )
        await self.db.commit()

    async def load_tasks_for_workflow(self, workflow_id: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM tasks WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        *,
        result: str | None = None,
        error: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        fields = ["status = ?"]
        values: list = [status]
        if result is not None:
            fields.append("result = ?")
            values.append(result)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        if started_at is not None:
            fields.append("started_at = ?")
            values.append(started_at)
        if completed_at is not None:
            fields.append("completed_at = ?")
            values.append(completed_at)
        values.append(task_id)
        await self.db.execute(
            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values
        )
        await self.db.commit()

    async def increment_task_retry(self, task_id: str) -> None:
        await self.db.execute(
            "UPDATE tasks SET retry_count = retry_count + 1 WHERE id = ?", (task_id,)
        )
        await self.db.commit()

    # -- memory entries ------------------------------------------------------

    async def save_memory_entry(self, entry: dict) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO memory_entries
               (id, layer, scope_id, key, value, source, created_at, expires_at)
               VALUES (:id, :layer, :scope_id, :key, :value, :source, :created_at, :expires_at)""",
            entry,
        )
        await self.db.commit()

    async def query_memory(
        self, layer: str, scope_id: str, key: str | None = None
    ) -> list[dict]:
        if key:
            cursor = await self.db.execute(
                "SELECT * FROM memory_entries WHERE layer = ? AND scope_id = ? AND key = ?",
                (layer, scope_id, key),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM memory_entries WHERE layer = ? AND scope_id = ?",
                (layer, scope_id),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def delete_memory(self, entry_id: str) -> None:
        await self.db.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
        await self.db.commit()

    async def delete_expired_memory(self, now_iso: str) -> int:
        cursor = await self.db.execute(
            "DELETE FROM memory_entries WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now_iso,),
        )
        await self.db.commit()
        return cursor.rowcount

    # -- scratchpad ----------------------------------------------------------

    async def scratchpad_set(self, agent_id: str, key: str, value: str, created_at: str) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO scratchpad (agent_id, key, value, created_at)
               VALUES (?, ?, ?, ?)""",
            (agent_id, key, value, created_at),
        )
        await self.db.commit()

    async def scratchpad_get(self, agent_id: str, key: str) -> str | None:
        cursor = await self.db.execute(
            "SELECT value FROM scratchpad WHERE agent_id = ? AND key = ?",
            (agent_id, key),
        )
        row = await cursor.fetchone()
        return row["value"] if row else None

    async def scratchpad_clear(self, agent_id: str) -> None:
        await self.db.execute("DELETE FROM scratchpad WHERE agent_id = ?", (agent_id,))
        await self.db.commit()

    # -- decision log --------------------------------------------------------

    async def log_decision(self, entry: dict) -> None:
        await self.db.execute(
            """INSERT INTO decision_log
               (task_id, workflow_id, agent_role, action, reasoning, created_at)
               VALUES (:task_id, :workflow_id, :agent_role, :action, :reasoning, :created_at)""",
            entry,
        )
        await self.db.commit()

    async def get_decisions_for_task(self, task_id: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM decision_log WHERE task_id = ? ORDER BY id", (task_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_decisions_for_workflow(self, workflow_id: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM decision_log WHERE workflow_id = ? ORDER BY id", (workflow_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]

    # -- artifacts -----------------------------------------------------------

    async def register_artifact(self, artifact: dict) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO artifacts
               (id, task_id, workflow_id, type, path, description, created_at)
               VALUES (:id, :task_id, :workflow_id, :type, :path, :description, :created_at)""",
            artifact,
        )
        await self.db.commit()

    async def list_artifacts_for_task(self, task_id: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at", (task_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def list_artifacts_for_workflow(self, workflow_id: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM artifacts WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]

    # -- audit log -----------------------------------------------------------

    async def log_audit(self, entry: dict) -> None:
        await self.db.execute(
            """INSERT INTO audit_log
               (agent_id, tool_name, input_summary, output_summary, status, created_at)
               VALUES (:agent_id, :tool_name, :input_summary, :output_summary,
                       :status, :created_at)""",
            entry,
        )
        await self.db.commit()
