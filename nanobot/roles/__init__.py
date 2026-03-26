"""Role-based agent system.

Provides first-class agent roles (Planner, Researcher, Coder, Executor,
Reviewer, Manager) with per-role prompts, tool permissions, model assignment,
and budget controls.
"""

from nanobot.roles.models import AgentRole
from nanobot.roles.registry import RoleRegistry
from nanobot.roles.policies import get_allowed_tools, DEFAULT_ROLE_POLICIES

__all__ = [
    "AgentRole",
    "RoleRegistry",
    "get_allowed_tools",
    "DEFAULT_ROLE_POLICIES",
]
