"""Lightweight model routing — maps roles to models and tracks usage.

No heavy dependencies.  Just a dict + simple logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nanobot.roles.registry import RoleRegistry


@dataclass
class UsageRecord:
    """Token usage for a single task."""

    task_id: str
    role: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelRouter:
    """Select models per role and track token usage per workflow."""

    def __init__(
        self,
        role_registry: RoleRegistry,
        default_model: str,
        fallback_models: list[str] | None = None,
    ) -> None:
        self._roles = role_registry
        self._default_model = default_model
        self._fallback_models = fallback_models or []
        # workflow_id -> list of UsageRecord
        self._usage: dict[str, list[UsageRecord]] = {}

    def select_model(self, role_name: str) -> str:
        """Return the model string for a given role, falling back to default."""
        role = self._roles.get_role(role_name)
        if role and role.model:
            return role.model
        return self._default_model

    def get_fallback_chain(self, role_name: str) -> list[str]:
        """Return ordered list of models to try for a role."""
        primary = self.select_model(role_name)
        chain = [primary]

        role = self._roles.get_role(role_name)
        if role and role.fallback_models:
            chain.extend(m for m in role.fallback_models if m not in chain)

        chain.extend(m for m in self._fallback_models if m not in chain)

        if self._default_model not in chain:
            chain.append(self._default_model)

        return chain

    def record_usage(
        self,
        workflow_id: str,
        task_id: str,
        role: str,
        model: str,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Record token usage from an LLM response."""
        if usage is None:
            return
        record = UsageRecord(
            task_id=task_id,
            role=role,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )
        self._usage.setdefault(workflow_id, []).append(record)

    def get_workflow_usage(self, workflow_id: str) -> dict[str, Any]:
        """Return aggregated token usage for a workflow."""
        records = self._usage.get(workflow_id, [])
        total_prompt = sum(r.prompt_tokens for r in records)
        total_completion = sum(r.completion_tokens for r in records)
        total = sum(r.total_tokens for r in records)

        by_role: dict[str, int] = {}
        by_model: dict[str, int] = {}
        for r in records:
            by_role[r.role] = by_role.get(r.role, 0) + r.total_tokens
            by_model[r.model] = by_model.get(r.model, 0) + r.total_tokens

        return {
            "workflow_id": workflow_id,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total,
            "tasks": len(records),
            "by_role": by_role,
            "by_model": by_model,
        }

    def clear_workflow(self, workflow_id: str) -> None:
        """Drop usage records for a completed workflow."""
        self._usage.pop(workflow_id, None)
