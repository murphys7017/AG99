"""Executor Body contracts and runtime implementations.

This package intentionally contains only executor-facing contracts. Core
lifecycle ownership remains in :mod:`astrbot.core.execution`.
"""

from .contracts import (
    ExecutionFinalUpdate,
    ExecutionOutputMaterial,
    ExecutionOutputUpdate,
    ExecutionProgressUpdate,
    ExecutionResult,
    ExecutionUpdate,
    ExecutorRun,
)
from .registry import (
    register_executor_factory,
    resolve_executor_factory,
    resolve_executor_id,
)
from .runtime import drive_executor_run

__all__ = [
    "ExecutionFinalUpdate",
    "ExecutionOutputMaterial",
    "ExecutionOutputUpdate",
    "ExecutionProgressUpdate",
    "ExecutionResult",
    "ExecutionUpdate",
    "ExecutorRun",
    "drive_executor_run",
    "register_executor_factory",
    "resolve_executor_factory",
    "resolve_executor_id",
]
