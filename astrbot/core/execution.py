from __future__ import annotations

import json
import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from time import time
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from astrbot.core.agent.tool import TOOL_TARGET_CORE, ToolSet
from astrbot.core.capabilities import CapabilityResolver, CapabilitySnapshot
from astrbot.core.prompt.context_types import ContextPack, ContextSlot
from astrbot.core.prompt.render.interfaces import RenderResult
from astrbot.core.prompt.render.request_adapter import (
    PromptApplyResult,
    ProviderRequestAdapter,
)
from astrbot.core.provider.entities import ProviderRequest

if TYPE_CHECKING:
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

CORE_EXECUTION_SPEC_EXTRA_KEY = "_core_execution_spec"
CORE_EXECUTION_SESSION_EXTRA_KEY = "_core_execution_session"
CORE_EXECUTION_LIFECYCLE_EXTRA_KEY = "_core_execution_lifecycle"


class CoreExecutionEventKind(str, Enum):
    """One lifecycle fact emitted by a Core executor."""

    SUBMITTED = "submitted"
    WORKING = "working"
    PROGRESS = "progress"
    ARTIFACT_READY = "artifact_ready"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CoreCommandKind(str, Enum):
    """Commands accepted by the in-process Core Head boundary."""

    SUBMIT = "submit"
    PROVIDE_INPUT = "provide_input"
    CANCEL = "cancel"


_TERMINAL_CORE_EXECUTION_EVENT_KINDS = frozenset(
    {
        CoreExecutionEventKind.COMPLETED,
        CoreExecutionEventKind.FAILED,
        CoreExecutionEventKind.CANCELLED,
    }
)


def _freeze_execution_event_metadata(value: Any) -> Any:
    """Create an immutable snapshot without retaining caller-owned containers."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                deepcopy(key): _freeze_execution_event_metadata(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_execution_event_metadata(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_execution_event_metadata(item) for item in value)
    return deepcopy(value)


def _copy_execution_event_metadata(value: Any) -> Any:
    """Restore immutable metadata to ordinary containers for diagnostics."""

    if isinstance(value, Mapping):
        return {key: _copy_execution_event_metadata(item) for key, item in value.items()}
    if isinstance(value, tuple | frozenset):
        return [_copy_execution_event_metadata(item) for item in value]
    return deepcopy(value)


@dataclass(frozen=True, slots=True)
class CoreExecutionEvent:
    """Provider-neutral, non-visible lifecycle fact for one Core execution."""

    execution_id: str
    core_task_id: str
    turn_id: str
    executor_id: str
    kind: CoreExecutionEventKind
    metadata: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: float = field(default_factory=time)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            _freeze_execution_event_metadata(self.metadata),
        )

    def metadata_for_trace(self) -> dict[str, Any]:
        """Return a detached, diagnostics-safe copy of the event metadata."""

        return _copy_execution_event_metadata(self.metadata)

    @property
    def is_terminal(self) -> bool:
        return self.kind in _TERMINAL_CORE_EXECUTION_EVENT_KINDS

    @classmethod
    def from_spec(
        cls,
        spec: CoreExecutionSpec,
        *,
        kind: CoreExecutionEventKind,
        executor_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> CoreExecutionEvent:
        return cls(
            execution_id=spec.execution_id,
            core_task_id=spec.core_task_id,
            turn_id=spec.turn_id,
            executor_id=str(executor_id or "unknown"),
            kind=kind,
            metadata=metadata or {},
        )


@dataclass(frozen=True, slots=True)
class CoreCommand:
    """A directed command sent to one Core execution session."""

    execution_id: str
    turn_id: str
    kind: CoreCommandKind
    command_id: str = field(default_factory=lambda: uuid4().hex)
    execution_spec: CoreExecutionSpec | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None
    issued_at: float = field(default_factory=time)

    def __post_init__(self) -> None:
        execution_id = str(self.execution_id or "").strip()
        turn_id = str(self.turn_id or "").strip()
        command_id = str(self.command_id or "").strip()
        if not execution_id or not turn_id or not command_id:
            raise ValueError("CoreCommand requires execution_id, turn_id, and command_id")
        object.__setattr__(self, "execution_id", execution_id)
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(
            self,
            "payload",
            _freeze_execution_event_metadata(self.payload),
        )
        if self.execution_spec is not None and (
            self.execution_spec.execution_id != execution_id
            or self.execution_spec.turn_id != turn_id
        ):
            raise ValueError("CoreCommand execution identity does not match execution_spec")


@dataclass(frozen=True, slots=True)
class CoreEvent:
    """Sequenced event envelope exchanged inside the Core Head."""

    sequence: int
    execution: CoreExecutionEvent

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("CoreEvent sequence must be positive")

    @property
    def execution_id(self) -> str:
        return self.execution.execution_id

    @property
    def turn_id(self) -> str:
        return self.execution.turn_id

    @property
    def kind(self) -> CoreExecutionEventKind:
        return self.execution.kind

    def metadata_for_trace(self) -> dict[str, Any]:
        return self.execution.metadata_for_trace()


class CoreExecutionSessionStatus(str, Enum):
    """Lifecycle state owned by one Core Head execution session."""

    CREATED = "created"
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            CoreExecutionSessionStatus.COMPLETED,
            CoreExecutionSessionStatus.FAILED,
            CoreExecutionSessionStatus.CANCELLED,
        }


@dataclass(slots=True)
class CoreExecutionSession:
    """Own execution identity, command idempotence, state, and event ordering.

    This is deliberately an in-process coordination boundary. It does not run an
    executor, send platform output, or provide a transport queue.
    """

    spec: CoreExecutionSpec
    status: CoreExecutionSessionStatus = CoreExecutionSessionStatus.CREATED
    _events: list[CoreEvent] = field(default_factory=list, init=False, repr=False)
    _accepted_command_ids: set[str] = field(default_factory=set, init=False, repr=False)

    def accept_command(self, command: CoreCommand) -> bool:
        """Accept a command once, rejecting identity or lifecycle violations."""

        if (
            command.execution_id != self.spec.execution_id
            or command.turn_id != self.spec.turn_id
        ):
            raise ValueError("CoreCommand execution identity does not match session")
        if command.command_id in self._accepted_command_ids:
            return False
        if self.status.is_terminal:
            raise ValueError("CoreExecutionSession is already terminal")
        if command.kind is CoreCommandKind.SUBMIT:
            if self.status is not CoreExecutionSessionStatus.CREATED:
                raise ValueError("submit is only valid for a new CoreExecutionSession")
            if command.execution_spec is None:
                raise ValueError("submit requires execution_spec")
        elif command.kind is CoreCommandKind.PROVIDE_INPUT:
            if self.status is CoreExecutionSessionStatus.CREATED:
                raise ValueError("provide_input requires a submitted CoreExecutionSession")
        elif command.kind is CoreCommandKind.CANCEL:
            pass
        else:
            raise ValueError(f"unsupported CoreCommand kind: {command.kind!r}")
        self._accepted_command_ids.add(command.command_id)
        return True

    def record_event(self, execution_event: CoreExecutionEvent) -> CoreEvent:
        """Append a validated event and advance the session state.

        Repeated non-progress facts and repeated terminal facts are idempotent;
        conflicting terminal facts are rejected.
        """

        if (
            execution_event.execution_id != self.spec.execution_id
            or execution_event.turn_id != self.spec.turn_id
        ):
            raise ValueError("CoreExecutionEvent execution identity does not match session")

        for existing in self._events:
            if (
                existing.kind is execution_event.kind
                and execution_event.kind is not CoreExecutionEventKind.PROGRESS
            ):
                return existing
        if self.status.is_terminal:
            raise ValueError("cannot append an event after terminal state")
        self._advance_status(execution_event.kind)
        envelope = CoreEvent(sequence=len(self._events) + 1, execution=execution_event)
        self._events.append(envelope)
        return envelope

    @property
    def events(self) -> tuple[CoreEvent, ...]:
        return tuple(self._events)

    @property
    def terminal_event(self) -> CoreEvent | None:
        return next((event for event in reversed(self._events) if event.kind in _TERMINAL_CORE_EXECUTION_EVENT_KINDS), None)

    def _advance_status(self, kind: CoreExecutionEventKind) -> None:
        if kind is CoreExecutionEventKind.SUBMITTED:
            if self.status is not CoreExecutionSessionStatus.CREATED:
                raise ValueError("submitted event is only valid for a new session")
            self.status = CoreExecutionSessionStatus.SUBMITTED
            return
        if kind in {
            CoreExecutionEventKind.WORKING,
            CoreExecutionEventKind.PROGRESS,
            CoreExecutionEventKind.ARTIFACT_READY,
        }:
            if self.status not in {
                CoreExecutionSessionStatus.SUBMITTED,
                CoreExecutionSessionStatus.WORKING,
            }:
                raise ValueError(f"{kind.value} event is invalid in {self.status.value} state")
            self.status = CoreExecutionSessionStatus.WORKING
            return
        if kind is CoreExecutionEventKind.COMPLETED:
            self._require_active_state(kind)
            self.status = CoreExecutionSessionStatus.COMPLETED
            return
        if kind is CoreExecutionEventKind.FAILED:
            self._require_terminal_source_state(kind)
            self.status = CoreExecutionSessionStatus.FAILED
            return
        if kind is CoreExecutionEventKind.CANCELLED:
            self._require_terminal_source_state(kind)
            self.status = CoreExecutionSessionStatus.CANCELLED
            return
        raise ValueError(f"unsupported CoreExecutionEvent kind: {kind!r}")

    def _require_active_state(self, kind: CoreExecutionEventKind) -> None:
        if self.status not in {
            CoreExecutionSessionStatus.SUBMITTED,
            CoreExecutionSessionStatus.WORKING,
        }:
            raise ValueError(f"{kind.value} event is invalid in {self.status.value} state")

    def _require_terminal_source_state(self, kind: CoreExecutionEventKind) -> None:
        if self.status not in {
            CoreExecutionSessionStatus.CREATED,
            CoreExecutionSessionStatus.SUBMITTED,
            CoreExecutionSessionStatus.WORKING,
        }:
            raise ValueError(f"{kind.value} event is invalid in {self.status.value} state")


@dataclass(slots=True)
class CoreExecutionLifecycle:
    """Own one in-process Core execution session's command and event boundary.

    This lifecycle owner deliberately does not run an executor, persist records,
    or send platform output. It provides the stable Core-side coordination point
    that Native lifecycle adaptation can use while those responsibilities remain
    in their existing owners.
    """

    session: CoreExecutionSession
    _submit_command_id: str | None = field(default=None, init=False, repr=False)
    _executor_stop_callback: Callable[[], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    @property
    def spec(self) -> CoreExecutionSpec:
        return self.session.spec

    def start(self) -> bool:
        """Accept the one initial submit command for this execution."""

        if self._submit_command_id is not None:
            return False
        if self.session.status is not CoreExecutionSessionStatus.CREATED:
            return False
        command = CoreCommand(
            execution_id=self.spec.execution_id,
            turn_id=self.spec.turn_id,
            kind=CoreCommandKind.SUBMIT,
            execution_spec=self.spec,
        )
        accepted = self.accept_command(command)
        if accepted:
            self._submit_command_id = command.command_id
        return accepted

    def accept_command(self, command: CoreCommand) -> bool:
        """Accept an external command through the owning Core session."""

        return self.session.accept_command(command)

    def record_event(self, execution_event: CoreExecutionEvent) -> CoreEvent:
        """Sequence an executor fact through the owning Core session."""

        return self.session.record_event(execution_event)

    def bind_executor_stop_callback(self, callback: Callable[[], None]) -> None:
        """Bind the active executor's idempotent stop request for this session."""

        if self.session.status.is_terminal:
            raise ValueError("cannot bind an executor callback after terminal state")
        if self._executor_stop_callback is not None:
            if self._executor_stop_callback == callback:
                return
            raise ValueError("CoreExecutionLifecycle already has an executor callback")
        self._executor_stop_callback = callback

    def cancel(
        self,
        *,
        executor_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> CoreEvent:
        """Accept cancellation and record its terminal fact once."""

        terminal = self.session.terminal_event
        if terminal is not None:
            if terminal.kind is CoreExecutionEventKind.CANCELLED:
                return terminal
            raise ValueError("cannot cancel a completed CoreExecutionSession")

        details = metadata or {}
        self.accept_command(
            CoreCommand(
                execution_id=self.spec.execution_id,
                turn_id=self.spec.turn_id,
                kind=CoreCommandKind.CANCEL,
                payload=details,
                reason=str(details.get("reason", "") or "cancelled"),
            )
        )
        cancelled = self.record_event(
            CoreExecutionEvent.from_spec(
                self.spec,
                kind=CoreExecutionEventKind.CANCELLED,
                executor_id=executor_id,
                metadata=details,
            )
        )
        if self._executor_stop_callback is not None:
            self._executor_stop_callback()
        return cancelled

    def ledger_status(self, *, user_aborted: bool = False) -> str | None:
        """Project the terminal Core fact to the existing Ledger status value."""

        terminal = self.session.terminal_event
        if terminal is None:
            return None
        if terminal.kind is CoreExecutionEventKind.COMPLETED:
            return "completed"
        if terminal.kind is CoreExecutionEventKind.FAILED:
            return "failed"
        if terminal.kind is CoreExecutionEventKind.CANCELLED:
            return "aborted" if user_aborted else "cancelled"
        return None


def bind_core_execution_lifecycle(
    event: AstrMessageEvent,
    spec: CoreExecutionSpec,
) -> CoreExecutionLifecycle:
    """Bind the event bridge to the Core-owned lifecycle for one execution."""

    existing = event.get_extra(CORE_EXECUTION_LIFECYCLE_EXTRA_KEY)
    if isinstance(existing, CoreExecutionLifecycle):
        if (
            existing.spec.execution_id == spec.execution_id
            and existing.spec.turn_id == spec.turn_id
        ):
            return existing
        raise ValueError("CoreExecutionLifecycle is already bound to another execution")

    existing_session = event.get_extra(CORE_EXECUTION_SESSION_EXTRA_KEY)
    if isinstance(existing_session, CoreExecutionSession):
        if (
            existing_session.spec.execution_id != spec.execution_id
            or existing_session.spec.turn_id != spec.turn_id
        ):
            raise ValueError("CoreExecutionSession is already bound to another execution")
        lifecycle = CoreExecutionLifecycle(session=existing_session)
    else:
        lifecycle = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))
        event.set_extra(CORE_EXECUTION_SESSION_EXTRA_KEY, lifecycle.session)

    event.set_extra(CORE_EXECUTION_LIFECYCLE_EXTRA_KEY, lifecycle)
    return lifecycle


def start_core_execution_lifecycle(
    event: AstrMessageEvent,
    spec: CoreExecutionSpec,
) -> CoreExecutionLifecycle:
    """Bind and submit one execution through the in-process Core lifecycle."""

    lifecycle = bind_core_execution_lifecycle(event, spec)
    lifecycle.start()
    return lifecycle


def bind_core_execution_session(
    event: AstrMessageEvent,
    spec: CoreExecutionSpec,
) -> CoreExecutionSession:
    """Bind the current Native lifecycle adapter to one Core execution session.

    The event-scoped attachment is transitional: it lets the existing Native
    runner report facts through the Core session without granting the
    Interaction turn ownership of that session.
    """

    return bind_core_execution_lifecycle(event, spec).session


def get_core_execution_lifecycle(
    event: AstrMessageEvent,
) -> CoreExecutionLifecycle | None:
    """Return the Core lifecycle owner bound to the current event bridge."""

    lifecycle = event.get_extra(CORE_EXECUTION_LIFECYCLE_EXTRA_KEY)
    return lifecycle if isinstance(lifecycle, CoreExecutionLifecycle) else None


def get_core_execution_session(event: AstrMessageEvent) -> CoreExecutionSession | None:
    """Return the execution session owned by the current Core lifecycle adapter."""

    session = event.get_extra(CORE_EXECUTION_SESSION_EXTRA_KEY)
    return session if isinstance(session, CoreExecutionSession) else None


@dataclass(frozen=True, slots=True)
class CoreCapabilitySnapshot:
    """Framework-owned capabilities exposed to an executor."""

    tools: Any = None
    tool_schema: Any = None
    skills: Any = None
    knowledge: Any = None

    def snapshot(self) -> CoreCapabilitySnapshot:
        """Copy serializable capability facts while retaining the live ToolSet handle."""
        return type(self)(
            tools=self.tools,
            tool_schema=deepcopy(self.tool_schema),
            skills=deepcopy(self.skills),
            knowledge=deepcopy(self.knowledge),
        )

    @classmethod
    def from_context_pack(
        cls,
        context_pack: ContextPack,
        *,
        tools: Any = None,
    ) -> CoreCapabilitySnapshot:
        return cls(
            tools=tools,
            tool_schema=deepcopy(
                _slot_value(context_pack, "capability.tools_schema")
            ),
            skills=deepcopy(_slot_value(context_pack, "capability.skills_prompt")),
            knowledge=deepcopy(_slot_value(context_pack, "knowledge.snippets")),
        )


@dataclass(frozen=True, slots=True)
class CoreExecutionSpec:
    """Provider-neutral Core facts prepared before backend-specific rendering."""

    execution_id: str
    core_task_id: str
    turn_id: str
    context_pack: ContextPack
    task_spec: dict[str, Any] | None = None
    execution_history: tuple[dict[str, Any], ...] = ()
    capabilities: CoreCapabilitySnapshot = field(default_factory=CoreCapabilitySnapshot)
    parent_execution_id: str | None = None
    attempt: int = 1

    @classmethod
    def from_context_pack(
        cls,
        *,
        context_pack: ContextPack,
        turn_id: str,
        task_spec: dict[str, Any] | None = None,
        parent_execution_id: str | None = None,
        capabilities: CoreCapabilitySnapshot | None = None,
    ) -> CoreExecutionSpec:
        execution_id = uuid4().hex
        resolved_turn_id = turn_id.strip() or execution_id
        task_metadata = task_spec.get("metadata") if isinstance(task_spec, dict) else None
        configured_task_id = (
            task_metadata.get("core_task_id")
            if isinstance(task_metadata, dict)
            else None
        )
        core_task_id = str(configured_task_id or f"core:{resolved_turn_id}")
        history_slot = context_pack.get_slot("conversation.core_execution_history")
        history_value = history_slot.value if history_slot is not None else None
        records = history_value.get("records", []) if isinstance(history_value, dict) else []
        neutral_pack = ContextPack(
            slots=deepcopy(context_pack.slots),
            provider_request_ref=None,
            meta=deepcopy(context_pack.meta),
        )
        return cls(
            execution_id=execution_id,
            core_task_id=core_task_id,
            turn_id=resolved_turn_id,
            context_pack=neutral_pack,
            task_spec=deepcopy(task_spec) if isinstance(task_spec, dict) else None,
            execution_history=tuple(
                deepcopy(item) for item in records if isinstance(item, dict)
            ),
            capabilities=(
                capabilities.snapshot()
                if capabilities is not None
                else CoreCapabilitySnapshot()
            ),
            parent_execution_id=parent_execution_id,
        )


@dataclass(frozen=True, slots=True)
class NativeExecutionInput:
    provider_request: ProviderRequest
    prompt_apply_result: PromptApplyResult


class NativeExecutionAdapter:
    """Apply a Native-rendered prompt and capabilities to AstrBot's request."""

    def __init__(self) -> None:
        self._request_adapter = ProviderRequestAdapter()

    def adapt(
        self,
        spec: CoreExecutionSpec,
        rendered_prompt: RenderResult,
        provider_request: ProviderRequest,
    ) -> NativeExecutionInput:
        apply_result = self._request_adapter.apply_render_result(
            rendered_prompt,
            provider_request,
        )
        provider_request.func_tool = spec.capabilities.tools
        return NativeExecutionInput(
            provider_request=provider_request,
            prompt_apply_result=apply_result,
        )


def bind_effective_core_capabilities(
    spec: CoreExecutionSpec,
    capabilities: CapabilitySnapshot,
) -> CoreExecutionSpec:
    """Replace the pre-Hook tool view with the authorized effective snapshot."""

    context_pack = ContextPack(
        slots=dict(spec.context_pack.slots),
        provider_request_ref=None,
        meta=deepcopy(spec.context_pack.meta),
    )
    inventory = capabilities.serialized_inventory()
    if capabilities.is_empty():
        context_pack.slots.pop("capability.tools_schema", None)
        tool_schema: dict[str, Any] | None = None
    else:
        existing = context_pack.get_slot("capability.tools_schema")
        context_pack.slots["capability.tools_schema"] = (
            replace(
                existing,
                value=inventory,
                source="capability_resolver",
                meta=capabilities.inventory_metadata(),
            )
            if existing is not None
            else ContextSlot(
                name="capability.tools_schema",
                value=inventory,
                category="tools",
                source="capability_resolver",
                render_mode="raw",
                meta=capabilities.inventory_metadata(),
            )
        )
        tool_schema = inventory

    context_budgets = context_pack.meta.get("context_budgets")
    if isinstance(context_budgets, dict):
        serialized_size = (
            len(json.dumps(inventory, ensure_ascii=False, default=str))
            if tool_schema is not None
            else 0
        )
        context_budgets["tool_schema"] = {
            "original_amount": len(capabilities.tools),
            "retained_amount": len(capabilities.tools),
            "original_estimated_tokens": math.ceil(serialized_size / 4),
            "retained_estimated_tokens": math.ceil(serialized_size / 4),
            "limit_amount": None,
            "limit_estimated_tokens": None,
            "truncated": False,
            "truncation_reasons": ["request_hook_effective_capability"],
            "enforced": False,
        }

    effective = CoreCapabilitySnapshot(
        tools=capabilities.to_toolset(),
        tool_schema=tool_schema,
        skills=deepcopy(spec.capabilities.skills),
        knowledge=deepcopy(spec.capabilities.knowledge),
    )
    return replace(
        spec,
        context_pack=context_pack,
        capabilities=effective,
    )


def bind_effective_core_request(
    *,
    event: AstrMessageEvent,
    provider_request: ProviderRequest,
    persona_id: str | None = None,
    execution_spec: CoreExecutionSpec | None = None,
    prompt_apply_result: PromptApplyResult | None = None,
) -> tuple[CapabilitySnapshot, CoreExecutionSpec | None]:
    """Authorize the post-Hook Core request and synchronize all Core views.

    ``OnLLMRequest`` is allowed to replace the request-owned tool set.  The
    resulting capability snapshot must be applied to the live request and,
    when present, to the provider-neutral execution spec as one operation.
    """
    candidate_tools = provider_request.func_tool
    effective = CapabilityResolver().resolve_explicit_toolset(
        event=event,
        target=TOOL_TARGET_CORE,
        toolset=candidate_tools if isinstance(candidate_tools, ToolSet) else ToolSet(),
        persona_id=persona_id,
        selection_mode="request_hook",
    )
    provider_request.func_tool = effective.to_toolset()

    if prompt_apply_result is not None:
        prompt_apply_result.tool_schema_count = len(effective.tools)

    if execution_spec is not None:
        execution_spec = bind_effective_core_capabilities(
            execution_spec,
            effective,
        )

    return effective, execution_spec


def _slot_value(pack: ContextPack, name: str) -> Any:
    slot = pack.get_slot(name)
    return slot.value if slot is not None else None


__all__ = [
    "CORE_EXECUTION_SPEC_EXTRA_KEY",
    "CORE_EXECUTION_SESSION_EXTRA_KEY",
    "CORE_EXECUTION_LIFECYCLE_EXTRA_KEY",
    "CoreCapabilitySnapshot",
    "CoreCommand",
    "CoreCommandKind",
    "CoreEvent",
    "CoreExecutionEvent",
    "CoreExecutionEventKind",
    "CoreExecutionLifecycle",
    "CoreExecutionSession",
    "CoreExecutionSessionStatus",
    "CoreExecutionSpec",
    "NativeExecutionAdapter",
    "NativeExecutionInput",
    "bind_effective_core_request",
    "bind_effective_core_capabilities",
    "bind_core_execution_lifecycle",
    "bind_core_execution_session",
    "get_core_execution_lifecycle",
    "get_core_execution_session",
    "start_core_execution_lifecycle",
]
