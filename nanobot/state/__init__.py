"""Durable state layer for Molts-Boyfriend.

Provides SQLite-backed persistent storage with WAL mode for crash safety,
multi-layer memory, agent scratchpads, decision logging, and artifact tracking.
"""

from nanobot.state.store import StateStore
from nanobot.state.memory_layers import LayeredMemory, MemoryEntry, MemoryLayer
from nanobot.state.scratchpad import Scratchpad
from nanobot.state.decision_log import DecisionLog, DecisionEntry
from nanobot.state.artifacts import ArtifactTracker, Artifact

__all__ = [
    "StateStore",
    "LayeredMemory",
    "MemoryEntry",
    "MemoryLayer",
    "Scratchpad",
    "DecisionLog",
    "DecisionEntry",
    "ArtifactTracker",
    "Artifact",
]
