"""Executor Body contracts and runtime implementations.

This package intentionally contains only executor-facing contracts. Core
lifecycle ownership remains in :mod:`astrbot.core.execution`.
"""

from .codex_cli import CodexExecutorRun, build_codex_executor_run
from .codex_session import CodexSessionError, CodexSessionManager
from .contracts import (
    ExecutionFinalUpdate,
    ExecutionOutputMaterial,
    ExecutionOutputUpdate,
    ExecutionProgressUpdate,
    ExecutionResult,
    ExecutionUpdate,
    ExecutorRun,
)
from .external import (
    ExternalExecutorConfigurationError,
    ExternalExecutorRequest,
    ExternalExecutorSessionKey,
    prepare_external_executor_request,
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
    "ExternalExecutorConfigurationError",
    "ExternalExecutorRequest",
    "ExternalExecutorSessionKey",
    "ExecutorRun",
    "drive_executor_run",
    "register_executor_factory",
    "resolve_executor_factory",
    "resolve_executor_id",
    "prepare_external_executor_request",
    "CodexSessionError",
    "CodexSessionManager",
    "CodexExecutorRun",
    "build_codex_executor_run",
]
