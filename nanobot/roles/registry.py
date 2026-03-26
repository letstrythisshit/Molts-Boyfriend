"""Role registry - loads and manages agent role definitions."""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

from nanobot.roles.models import AgentRole
from nanobot.roles.policies import DEFAULT_ROLE_POLICIES

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt_template(role_name: str) -> str:
    """Load the system prompt template for a role from the prompts/ directory."""
    path = _PROMPTS_DIR / f"{role_name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _build_default_role(name: str) -> AgentRole:
    """Build a default AgentRole from built-in policies and prompt templates."""
    policy = DEFAULT_ROLE_POLICIES.get(name, {})
    prompt = _load_prompt_template(name)
    return AgentRole(
        name=name,
        display_name=name.replace("_", " ").title(),
        system_prompt_template=prompt,
        allowed_tools=policy.get("allowed_tools", []),
        denied_tools=policy.get("denied_tools", []),
        can_spawn_subtasks=policy.get("can_spawn_subtasks", False),
        can_access_browser=policy.get("can_access_browser", False),
        can_access_sandbox=policy.get("can_access_sandbox", False),
        memory_scope=policy.get("memory_scope", "task"),
        max_iterations=policy.get("max_iterations", 40),
    )


_DEFAULT_ROLE_NAMES = ["planner", "researcher", "coder", "executor", "reviewer", "manager"]


class RoleRegistry:
    """Manages agent role definitions.

    Loads defaults for the 6 built-in roles.  Custom roles from config
    are merged on top — config values override defaults.
    """

    def __init__(self) -> None:
        self._roles: dict[str, AgentRole] = {}

    def load_defaults(self) -> None:
        for name in _DEFAULT_ROLE_NAMES:
            self._roles[name] = _build_default_role(name)
        logger.debug("Loaded {} default agent roles", len(self._roles))

    def load_from_config(self, roles_config: list[dict] | None) -> None:
        """Merge user-defined role overrides on top of defaults.

        Each entry in *roles_config* is a dict that matches the AgentRole
        schema.  The ``name`` field is required.  All other fields are
        optional and override the built-in default for that role.
        """
        self.load_defaults()
        if not roles_config:
            return
        for entry in roles_config:
            name = entry.get("name")
            if not name:
                logger.warning("Skipping role config entry without a name: {}", entry)
                continue
            base = self._roles.get(name)
            if base:
                merged = base.model_dump()
                merged.update({k: v for k, v in entry.items() if v is not None})
                self._roles[name] = AgentRole(**merged)
            else:
                self._roles[name] = AgentRole(**entry)
            logger.debug("Registered role: {}", name)

    def get_role(self, name: str) -> AgentRole | None:
        return self._roles.get(name)

    def list_roles(self) -> list[AgentRole]:
        return list(self._roles.values())

    def role_names(self) -> list[str]:
        return list(self._roles.keys())
