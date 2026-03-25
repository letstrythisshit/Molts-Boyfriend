"""Retry policies with exponential backoff and jitter."""

from __future__ import annotations

import random

from pydantic import BaseModel

from nanobot.orchestration.models import Task


class RetryPolicy(BaseModel):
    max_retries: int = 2
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    jitter: float = 0.5  # random factor 0..jitter added to delay

    def should_retry(self, task: Task) -> bool:
        return task.retry_count < (task.max_retries or self.max_retries)

    def next_delay(self, task: Task) -> float:
        """Compute the next retry delay in seconds with exponential backoff + jitter."""
        exp = min(self.backoff_base ** task.retry_count, self.backoff_max)
        jitter_val = random.uniform(0, self.jitter * exp)
        return exp + jitter_val


DEFAULT_RETRY_POLICY = RetryPolicy()
