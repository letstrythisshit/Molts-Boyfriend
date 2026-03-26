"""Agent role data model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentRole(BaseModel):
    """Defines the capabilities, permissions, and configuration for an agent role."""

    name: str
    display_name: str = ""
    system_prompt_template: str = ""  # path to .md file or inline prompt
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    model: str | None = None
    fallback_models: list[str] = Field(default_factory=list)
    max_iterations: int = 40
    token_budget: int | None = None
    memory_scope: str = "task"  # "task" | "workflow" | "project"
    can_spawn_subtasks: bool = False
    can_access_browser: bool = False
    can_access_sandbox: bool = False
    requires_approval: list[str] = Field(default_factory=list)

    def effective_display_name(self) -> str:
        return self.display_name or self.name.replace("_", " ").title()
