"""Executor Body contracts and runtime implementations.

This package intentionally contains only executor-facing contracts. Core
lifecycle ownership remains in :mod:`astrbot.core.execution`.
"""

from .assembly import (
    CodexExecutorAssembly,
    NativeExecutorAssembly,
    build_codex_executor_assembly,
)
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
from .coordinator import ExternalCoreExecutionResult, execute_external_core_turn
from .external import (
    ExternalExecutorConfigurationError,
    ExternalExecutorRequest,
    ExternalExecutorSessionKey,
    prepare_external_executor_request,
)
from .registry import (
    register_executor_factory,
    resolve_executor_config,
    resolve_executor_factory,
    resolve_executor_id,
)
from .runtime import drive_executor_run
from .session_registry import ExternalExecutorSessionRegistry

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
    "ExternalExecutorSessionRegistry",
    "register_executor_factory",
    "resolve_executor_config",
    "resolve_executor_factory",
    "resolve_executor_id",
    "prepare_external_executor_request",
    "CodexSessionError",
    "CodexSessionManager",
    "ExternalCoreExecutionResult",
    "execute_external_core_turn",
    "CodexExecutorRun",
    "build_codex_executor_run",
    "CodexExecutorAssembly",
    "build_codex_executor_assembly",
    "NativeExecutorAssembly",
]
