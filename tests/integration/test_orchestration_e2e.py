"""End-to-end integration tests for the orchestration engine.

Tests marked with ``integration`` require a working OpenRouter API key
in the ``OPENROUTER_API_KEY`` env-var.  They are skipped otherwise.
"""

import asyncio
import os
import pytest
from pathlib import Path

from nanobot.orchestration.models import Task, TaskStatus, Workflow, WorkflowStatus
from nanobot.orchestration.task_graph import TaskGraph
from nanobot.orchestration.executor import AgentExecutor, TaskResult
from nanobot.orchestration.scheduler import Scheduler
from nanobot.orchestration.retry import DEFAULT_RETRY_POLICY
from nanobot.orchestration.state import OrchestrationStore
from nanobot.orchestration.routing import ModelRouter
from nanobot.roles.registry import RoleRegistry
from nanobot.roles.models import AgentRole
from nanobot.state.store import StateStore

_OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
_FREE_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

requires_openrouter = pytest.mark.skipif(
    not _OPENROUTER_KEY,
    reason="OPENROUTER_API_KEY not set",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider():
    """Create an OpenRouter provider instance."""
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider
    from nanobot.providers.registry import find_by_name

    spec = find_by_name("openrouter")
    return OpenAICompatProvider(
        api_key=_OPENROUTER_KEY,
        default_model=_FREE_MODEL,
        spec=spec,
    )


async def _make_store(tmp_path) -> StateStore:
    s = StateStore(str(tmp_path / "test.db"))
    await s.open()
    return s


# ---------------------------------------------------------------------------
# Unit-level orchestration tests (no LLM needed)
# ---------------------------------------------------------------------------

class TestTaskGraph:

    def test_get_runnable_respects_deps(self):
        g = TaskGraph()
        t1 = Task(workflow_id="w", title="A", description="a", role="executor")
        t2 = Task(workflow_id="w", title="B", description="b", role="executor", depends_on=[t1.id])
        g.add_task(t1)
        g.add_task(t2)

        runnable = g.get_runnable()
        assert len(runnable) == 1
        assert runnable[0].id == t1.id

    def test_mark_complete_unlocks_dependents(self):
        g = TaskGraph()
        t1 = Task(workflow_id="w", title="A", description="a", role="executor")
        t2 = Task(workflow_id="w", title="B", description="b", role="executor", depends_on=[t1.id])
        g.add_task(t1)
        g.add_task(t2)

        g.mark_running(t1.id)
        g.mark_complete(t1.id, "done")
        runnable = g.get_runnable()
        assert any(t.id == t2.id for t in runnable)

    def test_is_complete(self):
        g = TaskGraph()
        t1 = Task(workflow_id="w", title="A", description="a", role="executor")
        g.add_task(t1)
        assert not g.is_complete()
        g.mark_running(t1.id)
        g.mark_complete(t1.id, "ok")
        assert g.is_complete()

    def test_stats(self):
        g = TaskGraph()
        t1 = Task(workflow_id="w", title="A", description="a", role="executor")
        t2 = Task(workflow_id="w", title="B", description="b", role="executor")
        g.add_task(t1)
        g.add_task(t2)
        g.mark_running(t1.id)
        g.mark_complete(t1.id, "ok")
        stats = g.stats()
        assert stats["total"] == 2
        assert stats["completed"] == 1


class TestScheduler:

    def test_get_next_tasks_respects_concurrency(self):
        s = Scheduler(max_concurrent=1)
        g = TaskGraph()
        t1 = Task(workflow_id="w", title="A", description="a", role="executor")
        t2 = Task(workflow_id="w", title="B", description="b", role="executor")
        g.add_task(t1)
        g.add_task(t2)

        # With 0 running, should get up to max_concurrent tasks
        tasks = s.get_next_tasks(g, running_count=0)
        assert len(tasks) == 1

    def test_get_next_returns_empty_at_capacity(self):
        s = Scheduler(max_concurrent=1)
        g = TaskGraph()
        t1 = Task(workflow_id="w", title="A", description="a", role="executor")
        g.add_task(t1)

        tasks = s.get_next_tasks(g, running_count=1)
        assert len(tasks) == 0


class TestModelRouter:

    def test_select_model_default(self):
        reg = RoleRegistry()
        router = ModelRouter(reg, "default-model")
        assert router.select_model("unknown_role") == "default-model"

    def test_select_model_from_role(self):
        reg = RoleRegistry()
        # The default coder role doesn't have a model set, so it falls back
        router = ModelRouter(reg, "default-model")
        model = router.select_model("coder")
        assert model == "default-model"

    def test_usage_tracking(self):
        reg = RoleRegistry()
        router = ModelRouter(reg, "default-model")
        router.record_usage("wf-1", "t-1", "coder", "gpt-4", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        usage = router.get_workflow_usage("wf-1")
        assert usage["total_tokens"] == 150
        assert usage["by_role"]["coder"] == 150


class TestExecutorFallback:
    """Test executor without a real LLM provider (fallback path)."""

    async def test_execute_without_provider(self, tmp_path):
        store = await _make_store(tmp_path)
        try:
            executor = AgentExecutor(store, default_timeout=10.0)
            task = Task(workflow_id="w", title="test", description="hello", role="executor")
            result = await executor.execute_task(task)
            assert result.success
            assert "Executed" in result.output
        finally:
            await store.close()


class TestRoleFiltering:
    """Test that tool registry filters by role."""

    def test_denied_tools_filtered(self):
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool
        from nanobot.agent.tools.shell import ExecTool

        reg = ToolRegistry()
        reg.register(ReadFileTool())
        reg.register(WriteFileTool())
        reg.register(EditFileTool())
        reg.register(ExecTool())

        role = AgentRole(
            name="reviewer",
            denied_tools=["exec", "write_file", "edit_file"],
        )
        defs = reg.get_definitions(role=role)
        names = [d["function"]["name"] for d in defs]
        assert "exec" not in names
        assert "write_file" not in names
        assert "read_file" in names


# ---------------------------------------------------------------------------
# Integration tests (require OpenRouter)
# ---------------------------------------------------------------------------

class TestOpenRouterIntegration:

    @requires_openrouter
    async def test_single_agent_execution(self, tmp_path):
        """Test that a single AgentLoop can process a message via OpenRouter."""
        from nanobot.agent.loop import AgentLoop
        from nanobot.bus.queue import MessageBus

        provider = _make_provider()
        bus = MessageBus()
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            model=_FREE_MODEL,
            max_iterations=5,
            restrict_to_workspace=True,
        )

        response = await asyncio.wait_for(
            loop.process_direct(
                content="What is 2+2? Reply with just the number, nothing else.",
                session_key="test:direct",
            ),
            timeout=60.0,
        )

        assert response is not None
        assert "4" in response.content

    @requires_openrouter
    async def test_executor_with_real_llm(self, tmp_path):
        """Test AgentExecutor with a real LLM provider."""
        store = await _make_store(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        try:
            provider = _make_provider()
            executor = AgentExecutor(store, default_timeout=60.0)
            task = Task(
                workflow_id="wf-test",
                title="Simple math",
                description="What is 2+2? Reply with just the number.",
                role="executor",
            )

            result = await asyncio.wait_for(
                executor.execute_task(
                    task,
                    provider=provider,
                    workspace=workspace,
                ),
                timeout=90.0,
            )

            assert result.success, f"Task failed: {result.error}"
            assert result.output  # should have some content
        finally:
            await store.close()

    @requires_openrouter
    async def test_crash_recovery_roundtrip(self, tmp_path):
        """Test that workflow state persists and can be loaded after 'crash'."""
        store = await _make_store(tmp_path)
        try:
            orch = OrchestrationStore(store)
            workflow = Workflow(goal="test recovery")
            workflow.tasks = [
                Task(workflow_id=workflow.id, title="step1", description="do thing", role="executor"),
            ]
            workflow.status = WorkflowStatus.EXECUTING
            await orch.save_workflow(workflow)

            # "Crash" — close and reopen
            await store.close()
            store2 = StateStore(str(tmp_path / "test.db"))
            await store2.open()
            orch2 = OrchestrationStore(store2)

            incomplete = await orch2.load_incomplete_workflows()
            assert len(incomplete) >= 1
            assert incomplete[0].id == workflow.id
            assert incomplete[0].status == WorkflowStatus.EXECUTING
            await store2.close()
        except Exception:
            await store.close()
            raise
