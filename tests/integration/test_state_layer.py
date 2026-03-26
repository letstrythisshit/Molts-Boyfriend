"""Integration tests for the durable state layer (SQLite WAL)."""

import asyncio
import pytest
from pathlib import Path

from nanobot.state.store import StateStore
from nanobot.state.memory_layers import LayeredMemory, MemoryEntry, MemoryLayer
from nanobot.state.scratchpad import Scratchpad
from nanobot.state.decision_log import DecisionEntry, DecisionLog
from nanobot.state.artifacts import Artifact, ArtifactTracker


# ---------------------------------------------------------------------------
# StateStore basics
# ---------------------------------------------------------------------------

class TestStateStore:

    @pytest.fixture()
    async def store(self, tmp_path):
        db_path = tmp_path / "test.db"
        s = StateStore(str(db_path))
        await s.open()
        yield s
        await s.close()

    async def test_open_creates_tables(self, store):
        """Verify that opening the store creates the expected tables."""
        async with store.db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            rows = await cur.fetchall()
        names = {r["name"] for r in rows}
        assert "workflows" in names
        assert "tasks" in names
        assert "memory_entries" in names

    async def test_workflow_roundtrip(self, store):
        """Save and load a workflow."""
        await store.save_workflow({
            "id": "wf-1",
            "goal": "test goal",
            "status": "planning",
            "originator_channel": "",
            "originator_chat_id": "",
            "pause_requested": 0,
            "cancel_requested": 0,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "metadata_json": "{}",
        })
        row = await store.load_workflow("wf-1")
        assert row is not None
        assert row["goal"] == "test goal"
        assert row["status"] == "planning"

    async def test_task_roundtrip(self, store):
        """Save and load a task."""
        # Create parent workflow first (FK constraint)
        await store.save_workflow({
            "id": "wf-1", "goal": "test", "status": "planning",
            "originator_channel": "", "originator_chat_id": "",
            "pause_requested": 0, "cancel_requested": 0,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "metadata_json": "{}",
        })
        await store.save_task({
            "id": "t-1",
            "workflow_id": "wf-1",
            "parent_id": None,
            "title": "do stuff",
            "description": "test",
            "role": "executor",
            "status": "pending",
            "depends_on_json": "[]",
            "assigned_agent_id": None,
            "model_override": None,
            "max_iterations": 40,
            "token_budget": None,
            "retry_count": 0,
            "max_retries": 2,
            "result": None,
            "error": None,
            "artifacts_json": "[]",
            "created_at": "2025-01-01T00:00:00Z",
            "started_at": None,
            "completed_at": None,
            "metadata_json": "{}",
        })
        tasks = await store.load_tasks_for_workflow("wf-1")
        assert len(tasks) == 1
        assert tasks[0]["title"] == "do stuff"

    async def test_wal_mode_enabled(self, store):
        """Verify SQLite WAL mode is active for crash safety."""
        async with store.db.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        mode = row["journal_mode"] if row else ""
        assert mode == "wal"


# ---------------------------------------------------------------------------
# Scratchpad
# ---------------------------------------------------------------------------

class TestScratchpad:

    @pytest.fixture()
    async def scratchpad(self, tmp_path):
        db_path = tmp_path / "test.db"
        s = StateStore(str(db_path))
        await s.open()
        sp = Scratchpad(s, "agent-1")
        yield sp
        await s.close()

    async def test_set_and_get(self, scratchpad):
        await scratchpad.set("key1", "value1")
        assert await scratchpad.get("key1") == "value1"

    async def test_get_missing_returns_none(self, scratchpad):
        assert await scratchpad.get("nope") is None

    async def test_clear(self, scratchpad):
        await scratchpad.set("k", "v")
        await scratchpad.clear()
        assert await scratchpad.get("k") is None


# ---------------------------------------------------------------------------
# DecisionLog
# ---------------------------------------------------------------------------

class TestDecisionLog:

    @pytest.fixture()
    async def log(self, tmp_path):
        db_path = tmp_path / "test.db"
        s = StateStore(str(db_path))
        await s.open()
        yield DecisionLog(s)
        await s.close()

    async def test_log_and_retrieve(self, log):
        entry = DecisionEntry(
            task_id="t-1",
            workflow_id="wf-1",
            agent_role="coder",
            action="wrote_code",
            reasoning="needed a function",
        )
        await log.log(entry)
        entries = await log.get_for_task("t-1")
        assert len(entries) >= 1
        assert entries[0].action == "wrote_code"


# ---------------------------------------------------------------------------
# ArtifactTracker
# ---------------------------------------------------------------------------

class TestArtifactTracker:

    @pytest.fixture()
    async def tracker(self, tmp_path):
        db_path = tmp_path / "test.db"
        s = StateStore(str(db_path))
        await s.open()
        yield ArtifactTracker(s)
        await s.close()

    async def test_register_and_list(self, tracker):
        artifact = Artifact(
            task_id="t-1",
            artifact_type="file",
            path="/tmp/out.txt",
        )
        await tracker.register(artifact)
        items = await tracker.list_for_task("t-1")
        assert len(items) >= 1
        assert items[0].path == "/tmp/out.txt"


# ---------------------------------------------------------------------------
# Sensitive file blocking
# ---------------------------------------------------------------------------

class TestSensitiveFileBlocking:

    @pytest.fixture()
    def tool(self, tmp_path):
        from nanobot.agent.tools.filesystem import ReadFileTool
        return ReadFileTool(workspace=tmp_path)

    async def test_blocks_config_json(self, tool, tmp_path):
        f = tmp_path / "config.json"
        f.write_text('{"api_key": "secret"}')
        result = await tool.execute(path=str(f))
        assert "Access denied" in result

    async def test_blocks_env_file(self, tool, tmp_path):
        f = tmp_path / ".env"
        f.write_text("API_KEY=secret")
        result = await tool.execute(path=str(f))
        assert "Access denied" in result

    async def test_blocks_id_rsa(self, tool, tmp_path):
        f = tmp_path / "id_rsa"
        f.write_text("-----BEGIN RSA PRIVATE KEY-----")
        result = await tool.execute(path=str(f))
        assert "Access denied" in result

    async def test_allows_normal_files(self, tool, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("hello world")
        result = await tool.execute(path=str(f))
        assert "hello world" in result
