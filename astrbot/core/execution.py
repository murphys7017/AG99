from __future__ import annotations

import asyncio
import json
import math
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from time import time
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from astrbot.core import logger
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
CORE_EXECUTION_HEAD_EXTRA_KEY = "_core_execution_head"


class CoreExecutionEventKind(str, Enum):
    """One lifecycle fact emitted by a Core executor."""

    SUBMITTED = "submitted"
    WORKING = "working"
    PROGRESS = "progress"
    ARTIFACT_READY = "artifact_ready"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CoreExecutionArtifact:
    """Executor-neutral description of one result produced by an execution.

    The artifact records identity and bounded diagnostics only. Visible content
    remains owned by the existing output bridge and is not transported through
    execution event metadata.
    """

    artifact_id: str
    artifact_kind: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        artifact_id = str(self.artifact_id or "").strip()
        artifact_kind = str(self.artifact_kind or "").strip()
        if not artifact_id or not artifact_kind:
            raise ValueError(
                "CoreExecutionArtifact requires artifact_id and artifact_kind"
            )
        reserved_attributes = {"artifact_id", "artifact_kind"}.intersection(
            self.attributes
        )
        if reserved_attributes:
            names = ", ".join(sorted(reserved_attributes))
            raise ValueError(
                f"CoreExecutionArtifact attributes contain reserved keys: {names}"
            )
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "artifact_kind", artifact_kind)
        object.__setattr__(
            self,
            "attributes",
            _freeze_execution_event_metadata(self.attributes),
        )

    def event_metadata(self) -> dict[str, Any]:
        """Return detached metadata for one ``artifact_ready`` event."""

        return {
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind,
            **_copy_execution_event_metadata(self.attributes),
        }

    @classmethod
    def from_event_metadata(
        cls,
        metadata: Mapping[str, Any],
    ) -> CoreExecutionArtifact:
        """Restore the typed artifact represented by an execution event."""

        attributes = {
            key: _copy_execution_event_metadata(value)
            for key, value in metadata.items()
            if key not in {"artifact_id", "artifact_kind"}
        }
        return cls(
            artifact_id=str(metadata.get("artifact_id", "") or ""),
            artifact_kind=str(metadata.get("artifact_kind", "") or ""),
            attributes=attributes,
        )


@dataclass(frozen=True, slots=True)
class CoreExecutionProgress:
    """Bounded, non-visible description of one execution progress fact."""

    source: str
    phase: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source = str(self.source or "").strip()
        phase = str(self.phase or "").strip()
        if not source or not phase:
            raise ValueError("CoreExecutionProgress requires source and phase")
        reserved = {"source", "phase"}.intersection(self.attributes)
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(
                f"CoreExecutionProgress attributes contain reserved keys: {names}"
            )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(
            self,
            "attributes",
            _freeze_execution_event_metadata(self.attributes),
        )

    def event_metadata(self) -> dict[str, Any]:
        return {
            "source": self.source,
            **_copy_execution_event_metadata(self.attributes),
        }


class CoreCommandKind(str, Enum):
    """Commands accepted by the in-process Core Head boundary."""

    SUBMIT = "submit"
    PROVIDE_INPUT = "provide_input"
    CANCEL = "cancel"


class CoreCommandOrigin(str, Enum):
    """Logical sender of an in-process Core command."""

    PERSONAL = "personal"
    CORE_HEAD = "core_head"


class CoreCommandDisposition(str, Enum):
    """Receipt state returned by the Core Head for one command."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


_TERMINAL_CORE_EXECUTION_EVENT_KINDS = frozenset(
    {
        CoreExecutionEventKind.COMPLETED,
        CoreExecutionEventKind.FAILED,
        CoreExecutionEventKind.CANCELLED,
    }
)
_CORE_EXECUTION_TERMINAL_ERROR_MAX_LENGTH = 2000


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

    @property
    def replay_key(self) -> tuple[CoreExecutionEventKind, str] | None:
        """Return the idempotence key for replay-safe execution facts.

        Progress facts intentionally retain every observation. Artifact facts may
        repeat for one execution, so their producer must provide a stable
        ``artifact_id`` before they can be deduplicated.
        """

        if self.kind is CoreExecutionEventKind.PROGRESS:
            return None
        if self.kind is CoreExecutionEventKind.ARTIFACT_READY:
            artifact_id = str(self.metadata.get("artifact_id", "") or "").strip()
            return (
                (self.kind, artifact_id)
                if artifact_id
                else None
            )
        return (self.kind, "")

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
class CoreExecutionOutcome:
    """Read-only terminal summary prepared by the Core lifecycle owner.

    The summary deliberately contains execution facts only. The caller that owns
    persistence still supplies runner-specific evidence such as messages, final
    text, and token usage.
    """

    execution_id: str
    turn_id: str
    status: str
    terminal_event: CoreEvent
    terminal_error: str | None
    artifacts: tuple[CoreExecutionArtifact, ...]


@dataclass(frozen=True, slots=True)
class CoreExecutionDeadlineView:
    """Read-only view of the Personal-owned turn deadline."""

    deadline_at: float
    _remaining_seconds_reader: Callable[[], float] = field(
        repr=False,
        compare=False,
    )

    @classmethod
    def from_budget(cls, budget: Any) -> CoreExecutionDeadlineView:
        """Bind a view without transferring ownership of the mutable budget."""

        reader = getattr(budget, "remaining", None)
        deadline_at = getattr(budget, "deadline_at", None)
        if not callable(reader) or deadline_at is None:
            raise TypeError("CoreExecutionDeadlineView requires a deadline budget")
        return cls(deadline_at=float(deadline_at), _remaining_seconds_reader=reader)

    def remaining_seconds(self) -> float:
        return max(0.0, float(self._remaining_seconds_reader()))

    def expired(self) -> bool:
        return self.remaining_seconds() <= 0.0


@dataclass(frozen=True, slots=True)
class CoreExecutionLedgerPreparation:
    """Terminal material prepared for the existing Ledger boundary.

    This is intentionally not a persistence record. The Core lifecycle owns
    the execution-derived status, result policy, and terminal error; the
    Native Stage still supplies runner evidence and invokes the current
    SQLite-backed Ledger.
    """

    execution_spec: CoreExecutionSpec
    status: str
    result: str | None
    error: str | None
    outcome: CoreExecutionOutcome | None

    @classmethod
    def from_fallback(
        cls,
        *,
        execution_spec: CoreExecutionSpec,
        completion_text: str | None,
        user_aborted: bool = False,
        fallback_status: str | None = None,
        fallback_error: str | None = None,
    ) -> CoreExecutionLedgerPreparation:
        """Preserve the legacy pre-terminal fallback without a lifecycle fact."""

        status = fallback_status or ("aborted" if user_aborted else "completed")
        text = str(completion_text or "")
        error = None
        if status == "failed":
            error = fallback_error or text
        elif status in {"cancelled", "aborted"}:
            error = fallback_error
        return cls(
            execution_spec=execution_spec,
            status=status,
            result=text if status != "failed" else None,
            error=error,
            outcome=None,
        )


@dataclass(frozen=True, slots=True)
class CoreCommand:
    """A directed command sent to one Core execution session."""

    execution_id: str
    turn_id: str
    kind: CoreCommandKind
    origin: CoreCommandOrigin = CoreCommandOrigin.PERSONAL
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
class CoreCommandReceipt:
    """Structured acknowledgement for a Personal-to-Core command.

    This is an in-process message contract only. It does not imply that the
    command has completed execution; completion is reported through CoreEvent.
    """

    command_id: str
    execution_id: str
    turn_id: str
    disposition: CoreCommandDisposition
    origin: CoreCommandOrigin
    session_status: CoreExecutionSessionStatus

    @property
    def accepted(self) -> bool:
        return self.disposition is CoreCommandDisposition.ACCEPTED


class CoreExecutionEventMailbox:
    """Async, in-process delivery boundary for one Core event subscriber.

    The mailbox receives only events published after subscription. It does not
    replay history, run an executor, or grant the subscriber ownership of the
    execution session.
    """

    def __init__(self, *, maxsize: int = 128) -> None:
        if maxsize < 1:
            raise ValueError("CoreExecutionEventMailbox maxsize must be positive")
        self._events: deque[CoreEvent] = deque()
        self._maxsize = maxsize
        self._wake = asyncio.Event()
        self._closed = False
        self._dropped_progress = 0

    def publish(self, event: CoreEvent) -> bool:
        """Publish one event without blocking the Core publisher.

        Progress is the only event kind that may be discarded under pressure.
        Terminal events are always retained, even if that temporarily exceeds
        the configured bound by one item.
        """

        if not self._closed:
            if len(self._events) >= self._maxsize:
                progress_index = next(
                    (
                        index
                        for index, queued in enumerate(self._events)
                        if queued.kind is CoreExecutionEventKind.PROGRESS
                    ),
                    None,
                )
                if progress_index is not None:
                    del self._events[progress_index]
                    self._dropped_progress += 1
                # Keep submitted/working/artifact/terminal facts even when no
                # progress remains to evict. The bound is a pressure guard
                # for progress, not permission to lose execution facts.
            self._events.append(event)
            self._wake.set()
            return True
        return False

    def close(self) -> None:
        """Close the mailbox after already-published events are drained."""

        if self._closed:
            return
        self._closed = True
        self._wake.set()

    async def receive(self) -> CoreEvent:
        """Wait for the next event, raising StopAsyncIteration when closed."""

        while True:
            # Clear before inspecting the queue. Publishing between the
            # inspection and clear would otherwise lose the wake-up signal.
            self._wake.clear()
            if self._events:
                return self._events.popleft()
            if self._closed:
                raise StopAsyncIteration
            await self._wake.wait()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def dropped_progress(self) -> int:
        return self._dropped_progress

    def __aiter__(self) -> CoreExecutionEventMailbox:
        return self

    async def __anext__(self) -> CoreEvent:
        return await self.receive()


class CoreExecutionCommandMailbox:
    """Async, in-process delivery boundary for accepted Core commands.

    The mailbox observes commands accepted after subscription. It does not
    execute commands, replay history, or change command acknowledgement
    semantics. Duplicate command attempts are therefore not published.
    """

    def __init__(self, *, maxsize: int = 128) -> None:
        if maxsize < 1:
            raise ValueError("CoreExecutionCommandMailbox maxsize must be positive")
        self._commands: deque[CoreCommand] = deque()
        self._maxsize = maxsize
        self._wake = asyncio.Event()
        self._closed = False
        self._dropped_commands = 0

    def publish(self, command: CoreCommand) -> bool:
        """Publish one accepted command without blocking the Core caller."""

        if self._closed:
            return False
        if len(self._commands) >= self._maxsize:
            droppable_index = next(
                (
                    index
                    for index, queued in enumerate(self._commands)
                    if queued.kind is CoreCommandKind.PROVIDE_INPUT
                ),
                None,
            )
            if droppable_index is not None:
                del self._commands[droppable_index]
                self._dropped_commands += 1
            else:
                # Submit and cancel are control facts. Keep them even when
                # that temporarily exceeds the nominal bound.
                pass
        self._commands.append(command)
        self._wake.set()
        return True

    def close(self) -> None:
        """Close the mailbox after already-published commands are drained."""

        if self._closed:
            return
        self._closed = True
        self._wake.set()

    async def receive(self) -> CoreCommand:
        """Wait for the next command, raising StopAsyncIteration when closed."""

        while True:
            self._wake.clear()
            if self._commands:
                return self._commands.popleft()
            if self._closed:
                raise StopAsyncIteration
            await self._wake.wait()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def dropped_commands(self) -> int:
        return self._dropped_commands

    def __aiter__(self) -> CoreExecutionCommandMailbox:
        return self

    async def __anext__(self) -> CoreCommand:
        return await self.receive()


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

        State facts are idempotent by kind, artifact facts by ``artifact_id``,
        and progress facts retain every observation. Conflicting terminal facts
        are rejected.
        """

        if (
            execution_event.execution_id != self.spec.execution_id
            or execution_event.turn_id != self.spec.turn_id
        ):
            raise ValueError("CoreExecutionEvent execution identity does not match session")

        replay_key = execution_event.replay_key
        if replay_key is not None:
            for existing in self._events:
                if existing.execution.replay_key == replay_key:
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
    _executor_input_callback: Callable[[str], Any] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _executor_id: str | None = field(default=None, init=False, repr=False)
    _executor_stop_error: str | None = field(default=None, init=False, repr=False)
    _deadline_view: CoreExecutionDeadlineView | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _event_publisher: Callable[[CoreEvent], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _command_publisher: Callable[[CoreCommand], None] | None = field(
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

        accepted = self.session.accept_command(command)
        if accepted and self._command_publisher is not None:
            self._command_publisher(command)
        return accepted

    def record_event(self, execution_event: CoreExecutionEvent) -> CoreEvent:
        """Sequence an executor fact through the owning Core session."""

        self._validate_executor_identity(execution_event.executor_id)
        envelope = self.session.record_event(execution_event)
        if self._event_publisher is not None:
            self._event_publisher(envelope)
        return envelope

    def bind_event_publisher(self, callback: Callable[[CoreEvent], None]) -> None:
        """Bind the Head's one local publication path for this lifecycle."""

        if self._event_publisher is not None:
            raise ValueError("CoreExecutionLifecycle already has an event publisher")
        self._event_publisher = callback

    def bind_command_publisher(self, callback: Callable[[CoreCommand], None]) -> None:
        """Bind the Head's one local command publication path."""

        if self._command_publisher is not None:
            raise ValueError("CoreExecutionLifecycle already has a command publisher")
        self._command_publisher = callback

    def bind_executor_stop_callback(self, callback: Callable[[], None]) -> None:
        """Bind the active executor's idempotent stop request for this session."""

        if self.session.status.is_terminal:
            raise ValueError("cannot bind an executor callback after terminal state")
        if self._executor_stop_callback is not None:
            if self._executor_stop_callback == callback:
                return
            raise ValueError("CoreExecutionLifecycle already has an executor callback")
        self._executor_stop_callback = callback

    def bind_executor_input_callback(self, callback: Callable[[str], Any]) -> None:
        """Bind the active executor's synchronous supplemental-input request."""

        if self.session.status.is_terminal:
            raise ValueError("cannot bind an executor callback after terminal state")
        if self._executor_input_callback is not None:
            if self._executor_input_callback == callback:
                return
            raise ValueError("CoreExecutionLifecycle already has an input callback")
        self._executor_input_callback = callback

    @property
    def executor_id(self) -> str | None:
        """Return the executor currently attached to this lifecycle."""

        return self._executor_id

    def bind_executor(
        self,
        *,
        executor_id: str,
        stop_callback: Callable[[], None],
        input_callback: Callable[[str], Any] | None = None,
    ) -> None:
        """Attach one executor identity and its stop operation to this session."""

        normalized_executor_id = str(executor_id or "").strip()
        if not normalized_executor_id:
            raise ValueError("CoreExecutionLifecycle requires a non-empty executor_id")
        if self._executor_id is not None and self._executor_id != normalized_executor_id:
            raise ValueError("CoreExecutionLifecycle already has another executor")
        self.bind_executor_stop_callback(stop_callback)
        if input_callback is not None:
            self.bind_executor_input_callback(input_callback)
        self._executor_id = normalized_executor_id

    def release_executor(self, *, executor_id: str) -> bool:
        """Release a completed executor without dropping active cancel support."""

        normalized_executor_id = str(executor_id or "").strip()
        if self._executor_id is None:
            return False
        if self._executor_id != normalized_executor_id:
            raise ValueError("CoreExecutionLifecycle executor identity does not match")
        if not self.session.status.is_terminal:
            return False
        self._executor_stop_callback = None
        self._executor_input_callback = None
        self._executor_id = None
        return True

    def provide_input(
        self,
        *,
        executor_id: str,
        message_text: str,
        origin: CoreCommandOrigin = CoreCommandOrigin.PERSONAL,
    ) -> Any | None:
        """Deliver supplemental input and publish it only when accepted."""

        self._validate_executor_identity(executor_id)
        if (
            self.session.status is CoreExecutionSessionStatus.CREATED
            or self.session.status.is_terminal
            or self._executor_input_callback is None
        ):
            return None
        text = str(message_text or "").strip()
        if not text:
            return None
        ticket = self._executor_input_callback(text)
        if ticket is None:
            return None
        self.accept_command(
            CoreCommand(
                execution_id=self.spec.execution_id,
                turn_id=self.spec.turn_id,
                kind=CoreCommandKind.PROVIDE_INPUT,
                origin=origin,
                payload={"message_text": text},
            )
        )
        return ticket

    @property
    def deadline_view(self) -> CoreExecutionDeadlineView | None:
        """Return the Personal-owned deadline as a read-only Core view."""

        return self._deadline_view

    def bind_deadline_view(self, view: CoreExecutionDeadlineView) -> None:
        """Bind the one Personal turn deadline view for this execution."""

        if self._deadline_view is not None:
            if self._deadline_view is view:
                return
            raise ValueError("CoreExecutionLifecycle already has a deadline view")
        self._deadline_view = view

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
        self._request_executor_stop()
        return cancelled

    def cancel_for_deadline(
        self,
        *,
        executor_id: str,
        stage: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> CoreEvent:
        """Route an upstream deadline expiry through the Core cancellation owner."""

        details = dict(metadata or {})
        details.setdefault("reason", "deadline_exceeded")
        details.setdefault("stage", str(stage or "turn"))
        return self.cancel(executor_id=executor_id, metadata=details)

    @property
    def executor_stop_error(self) -> str | None:
        """Return the last non-fatal executor stop callback failure, if any."""

        return self._executor_stop_error

    def terminal_error(self) -> str | None:
        """Project the terminal failure or cancellation evidence for persistence."""

        terminal = self.session.terminal_event
        if terminal is None:
            return None
        metadata = terminal.execution.metadata
        for key in ("error", "reason", "error_type"):
            value = metadata.get(key)
            text = str(value or "").strip()
            if text:
                return text[:_CORE_EXECUTION_TERMINAL_ERROR_MAX_LENGTH]
        return None

    def outcome(self, *, user_aborted: bool = False) -> CoreExecutionOutcome | None:
        """Prepare the terminal facts needed by a later Ledger projection."""

        terminal = self.session.terminal_event
        status = self.ledger_status(user_aborted=user_aborted)
        if terminal is None or status is None:
            return None
        return CoreExecutionOutcome(
            execution_id=self.spec.execution_id,
            turn_id=self.spec.turn_id,
            status=status,
            terminal_event=terminal,
            terminal_error=self.terminal_error(),
            artifacts=tuple(
                CoreExecutionArtifact.from_event_metadata(
                    envelope.execution.metadata
                )
                for envelope in self.session.events
                if envelope.kind is CoreExecutionEventKind.ARTIFACT_READY
            ),
        )

    def prepare_ledger_preparation(
        self,
        *,
        completion_text: str | None,
        user_aborted: bool = False,
        fallback_status: str | None = None,
        fallback_error: str | None = None,
    ) -> CoreExecutionLedgerPreparation:
        """Prepare terminal facts without coupling the lifecycle to persistence.

        A structured terminal event wins whenever it exists. The fallback is
        retained solely for failures that happen before a terminal event can be
        recorded by the legacy Native Stage.
        """

        outcome = self.outcome(user_aborted=user_aborted)
        if outcome is None:
            return CoreExecutionLedgerPreparation.from_fallback(
                execution_spec=self.spec,
                completion_text=completion_text,
                user_aborted=user_aborted,
                fallback_status=fallback_status,
                fallback_error=fallback_error,
            )

        status = outcome.status
        text = str(completion_text or "")
        terminal_error = outcome.terminal_error or fallback_error
        error = None
        if status == "failed":
            error = terminal_error or text
        elif status in {"cancelled", "aborted"}:
            error = terminal_error
        return CoreExecutionLedgerPreparation(
            execution_spec=self.spec,
            status=status,
            result=text if status != "failed" else None,
            error=error,
            outcome=outcome,
        )

    def _request_executor_stop(self) -> None:
        if self._executor_stop_callback is None:
            return
        try:
            self._executor_stop_callback()
        except Exception as exc:  # noqa: BLE001
            self._executor_stop_error = f"{type(exc).__name__}: {exc}"

    def _validate_executor_identity(self, executor_id: str) -> None:
        if self._executor_id is None:
            return
        if self._executor_id != str(executor_id or "").strip():
            raise ValueError("CoreExecutionEvent executor identity does not match")

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


@dataclass(slots=True)
class CoreExecutionHead:
    """Own the in-process command and event entry point for one execution.

    This is the first explicit Core Head boundary. It deliberately remains a
    synchronous coordinator: command queues, background consumption, executor
    selection, and artifact persistence are later responsibilities.
    """

    lifecycle: CoreExecutionLifecycle
    _published_sequences: set[int] = field(default_factory=set, init=False, repr=False)
    _event_subscribers: list[Callable[[CoreEvent], None]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _event_mailboxes: list[CoreExecutionEventMailbox] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _command_mailboxes: list[CoreExecutionCommandMailbox] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)
    _ledger_settled: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # A Head may be attached after a legacy Lifecycle has recorded facts.
        # Those facts are historical, so they must not be re-published on a
        # later duplicate call.
        self._published_sequences.update(
            event.sequence for event in self.lifecycle.session.events
        )
        self.lifecycle.bind_event_publisher(self._publish)
        self.lifecycle.bind_command_publisher(self._publish_command)

    @property
    def session(self) -> CoreExecutionSession:
        return self.lifecycle.session

    @property
    def spec(self) -> CoreExecutionSpec:
        return self.lifecycle.spec

    def start(self) -> bool:
        """Accept the initial submit command through the Head boundary."""

        return self.lifecycle.start()

    def accept_command(self, command: CoreCommand) -> bool:
        """Accept a Personal/Core command for this execution."""

        return self.lifecycle.accept_command(command)

    def dispatch_command(self, command: CoreCommand) -> CoreCommandReceipt:
        """Accept one command and return an explicit in-process receipt.

        A duplicate command is a successful idempotent delivery outcome, not
        a second execution. Lifecycle violations still raise so callers cannot
        mistake a rejected command for an accepted one.
        """

        accepted = self.accept_command(command)
        return CoreCommandReceipt(
            command_id=command.command_id,
            execution_id=command.execution_id,
            turn_id=command.turn_id,
            disposition=(
                CoreCommandDisposition.ACCEPTED
                if accepted
                else CoreCommandDisposition.DUPLICATE
            ),
            origin=command.origin,
            session_status=self.session.status,
        )

    def bind_executor_stop_callback(self, callback: Callable[[], None]) -> None:
        """Bind the active Executor Body stop request."""

        self.lifecycle.bind_executor_stop_callback(callback)

    def bind_executor_input_callback(self, callback: Callable[[str], Any]) -> None:
        """Bind the active Executor Body supplemental-input request."""

        self.lifecycle.bind_executor_input_callback(callback)

    @property
    def executor_id(self) -> str | None:
        """Return the executor currently attached to this Core session."""

        return self.lifecycle.executor_id

    def bind_executor(
        self,
        *,
        executor_id: str,
        stop_callback: Callable[[], None],
        input_callback: Callable[[str], Any] | None = None,
    ) -> None:
        """Attach one active Executor Body to this Core session."""

        self.lifecycle.bind_executor(
            executor_id=executor_id,
            stop_callback=stop_callback,
            input_callback=input_callback,
        )

    def activate_executor(
        self,
        *,
        executor_id: str,
        stop_callback: Callable[[], None],
        input_callback: Callable[[str], Any] | None = None,
        submission_metadata: Mapping[str, Any] | None = None,
    ) -> CoreExecutionEvent:
        """Start the session and attach one Executor Body as one Core action."""

        if self.session.status.is_terminal:
            raise ValueError("cannot activate an executor for a terminal Core session")
        self.start()
        self.bind_executor(
            executor_id=executor_id,
            stop_callback=stop_callback,
            input_callback=input_callback,
        )
        return self.emit_event(
            kind=CoreExecutionEventKind.SUBMITTED,
            executor_id=executor_id,
            metadata=submission_metadata,
        )

    def release_executor(self, *, executor_id: str) -> bool:
        """Release a terminal Executor Body from this Core session."""

        return self.lifecycle.release_executor(executor_id=executor_id)

    def provide_input(
        self,
        *,
        executor_id: str,
        message_text: str,
        origin: CoreCommandOrigin = CoreCommandOrigin.PERSONAL,
    ) -> Any | None:
        """Route one supplemental input through the active Executor Body."""

        return self.lifecycle.provide_input(
            executor_id=executor_id,
            message_text=message_text,
            origin=origin,
        )

    @property
    def deadline_view(self) -> CoreExecutionDeadlineView | None:
        return self.lifecycle.deadline_view

    def bind_deadline_view(self, view: CoreExecutionDeadlineView) -> None:
        """Bind Personal's read-only deadline view to the Core Head."""

        self.lifecycle.bind_deadline_view(view)

    def record_event(self, execution_event: CoreExecutionEvent) -> CoreEvent:
        """Record and publish one sequenced execution fact."""

        return self.lifecycle.record_event(execution_event)

    def emit_event(
        self,
        *,
        kind: CoreExecutionEventKind,
        executor_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> CoreExecutionEvent:
        """Create, sequence, and publish one executor fact through the Head."""

        if kind is CoreExecutionEventKind.CANCELLED:
            envelope = self.cancel(
                executor_id=executor_id,
                metadata=metadata,
            )
        else:
            envelope = self.record_event(
                CoreExecutionEvent.from_spec(
                    self.spec,
                    kind=kind,
                    executor_id=executor_id,
                    metadata=metadata,
                )
            )
        return envelope.execution

    def complete(
        self,
        *,
        executor_id: str,
        artifact: CoreExecutionArtifact | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CoreExecutionEvent:
        """Finalize successfully, preserving artifact-before-terminal order."""

        if artifact is not None:
            self.emit_event(
                kind=CoreExecutionEventKind.ARTIFACT_READY,
                executor_id=executor_id,
                metadata=artifact.event_metadata(),
            )
        return self.emit_event(
            kind=CoreExecutionEventKind.COMPLETED,
            executor_id=executor_id,
            metadata=metadata,
        )

    def fail(
        self,
        *,
        executor_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> CoreExecutionEvent:
        """Finalize with one failed terminal fact through the Head."""

        return self.emit_event(
            kind=CoreExecutionEventKind.FAILED,
            executor_id=executor_id,
            metadata=metadata,
        )

    def cancel(
        self,
        *,
        executor_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> CoreEvent:
        """Accept cancellation and publish the resulting terminal event once."""

        return self.lifecycle.cancel(executor_id=executor_id, metadata=metadata)

    def cancel_for_deadline(
        self,
        *,
        executor_id: str,
        stage: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> CoreEvent:
        """Cancel the Core execution after Personal reports deadline expiry."""

        return self.lifecycle.cancel_for_deadline(
            executor_id=executor_id,
            stage=stage,
            metadata=metadata,
        )

    def subscribe(self, callback: Callable[[CoreEvent], None]) -> None:
        """Register a local observer for newly published Core events."""

        if callback not in self._event_subscribers:
            self._event_subscribers.append(callback)

    def subscribe_mailbox(
        self,
        *,
        maxsize: int = 128,
    ) -> CoreExecutionEventMailbox:
        """Create an async subscriber for future Core events."""

        if self._closed:
            mailbox = CoreExecutionEventMailbox(maxsize=maxsize)
            mailbox.close()
            return mailbox
        mailbox = CoreExecutionEventMailbox(maxsize=maxsize)
        self._event_mailboxes.append(mailbox)
        return mailbox

    def subscribe_command_mailbox(
        self,
        *,
        maxsize: int = 128,
    ) -> CoreExecutionCommandMailbox:
        """Create an async subscriber for accepted commands."""

        if self._closed:
            mailbox = CoreExecutionCommandMailbox(maxsize=maxsize)
            mailbox.close()
            return mailbox
        mailbox = CoreExecutionCommandMailbox(maxsize=maxsize)
        self._command_mailboxes.append(mailbox)
        return mailbox

    def unsubscribe_mailbox(self, mailbox: CoreExecutionEventMailbox) -> None:
        """Stop one async subscriber and release it from the Head."""

        if mailbox in self._event_mailboxes:
            self._event_mailboxes.remove(mailbox)
        mailbox.close()

    def unsubscribe_command_mailbox(
        self,
        mailbox: CoreExecutionCommandMailbox,
    ) -> None:
        """Stop one async command subscriber and release it from the Head."""

        if mailbox in self._command_mailboxes:
            self._command_mailboxes.remove(mailbox)
        mailbox.close()

    def close(self) -> None:
        """Close event delivery without changing execution session state."""

        if self._closed:
            return
        self._closed = True
        for mailbox in tuple(self._event_mailboxes):
            mailbox.close()
        self._event_mailboxes.clear()
        for mailbox in tuple(self._command_mailboxes):
            mailbox.close()
        self._command_mailboxes.clear()

    def _publish(self, envelope: CoreEvent) -> None:
        if self._closed:
            return
        if envelope.sequence in self._published_sequences:
            return
        self._published_sequences.add(envelope.sequence)
        for mailbox in tuple(self._event_mailboxes):
            mailbox.publish(envelope)
        for callback in tuple(self._event_subscribers):
            try:
                callback(envelope)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Core execution event subscriber failed: execution_id=%s "
                    "turn_id=%s sequence=%s kind=%s",
                    envelope.execution_id,
                    envelope.turn_id,
                    envelope.sequence,
                    envelope.kind.value,
                    exc_info=True,
                )
        if envelope.kind in _TERMINAL_CORE_EXECUTION_EVENT_KINDS:
            self.close()

    def _publish_command(self, command: CoreCommand) -> None:
        if self._closed:
            return
        for mailbox in tuple(self._command_mailboxes):
            mailbox.publish(command)

    @property
    def events(self) -> tuple[CoreEvent, ...]:
        return self.session.events

    @property
    def terminal_event(self) -> CoreEvent | None:
        return self.session.terminal_event

    @property
    def executor_stop_error(self) -> str | None:
        return self.lifecycle.executor_stop_error

    def terminal_error(self) -> str | None:
        return self.lifecycle.terminal_error()

    def outcome(self, *, user_aborted: bool = False) -> CoreExecutionOutcome | None:
        """Return the lifecycle-owned terminal summary for this execution."""

        return self.lifecycle.outcome(user_aborted=user_aborted)

    def prepare_ledger_preparation(
        self,
        *,
        completion_text: str | None,
        user_aborted: bool = False,
        fallback_status: str | None = None,
        fallback_error: str | None = None,
    ) -> CoreExecutionLedgerPreparation:
        """Return the Core-owned material for the existing Ledger boundary."""

        return self.lifecycle.prepare_ledger_preparation(
            completion_text=completion_text,
            user_aborted=user_aborted,
            fallback_status=fallback_status,
            fallback_error=fallback_error,
        )

    def ledger_status(self, *, user_aborted: bool = False) -> str | None:
        return self.lifecycle.ledger_status(user_aborted=user_aborted)

    def claim_ledger_settlement(self) -> bool:
        """Claim the one Ledger settlement slot for this execution."""

        if self._ledger_settled:
            return False
        self._ledger_settled = True
        return True

    def release_ledger_settlement(self) -> None:
        """Release a failed Ledger settlement attempt for retry."""

        self._ledger_settled = False

    async def settle_ledger(
        self,
        append: Callable[[], Awaitable[bool]],
    ) -> bool | None:
        """Run the one allowed Ledger append through the Core Head.

        The Head owns settlement idempotence and retry release, while the
        callback keeps the concrete persistence backend outside Core.
        ``None`` means another caller already settled this execution.
        """

        if not self.claim_ledger_settlement():
            return None
        try:
            return await append()
        except BaseException:
            self.release_ledger_settlement()
            raise

    @property
    def ledger_settled(self) -> bool:
        return self._ledger_settled


def bind_core_execution_lifecycle(
    event: AstrMessageEvent,
    spec: CoreExecutionSpec,
) -> CoreExecutionLifecycle:
    """Bind the event bridge to the Core-owned lifecycle for one execution."""

    existing_lifecycle = event.get_extra(CORE_EXECUTION_LIFECYCLE_EXTRA_KEY)
    existing_session = event.get_extra(CORE_EXECUTION_SESSION_EXTRA_KEY)
    existing_head = event.get_extra(CORE_EXECUTION_HEAD_EXTRA_KEY)

    def matches_spec(session: CoreExecutionSession) -> bool:
        return (
            session.spec.execution_id == spec.execution_id
            and session.spec.turn_id == spec.turn_id
        )

    if isinstance(existing_head, CoreExecutionHead):
        if not matches_spec(existing_head.session):
            raise ValueError("CoreExecutionHead is already bound to another execution")
        if isinstance(existing_lifecycle, CoreExecutionLifecycle):
            if not matches_spec(existing_lifecycle.session):
                raise ValueError(
                    "CoreExecutionLifecycle is already bound to another execution"
                )
            if existing_head.lifecycle is not existing_lifecycle:
                raise ValueError(
                    "CoreExecutionHead and CoreExecutionLifecycle are inconsistent"
                )
        if isinstance(existing_session, CoreExecutionSession):
            if not matches_spec(existing_session):
                raise ValueError(
                    "CoreExecutionSession is already bound to another execution"
                )
            if existing_head.session is not existing_session:
                raise ValueError(
                    "CoreExecutionHead and CoreExecutionSession are inconsistent"
                )
        event.set_extra(CORE_EXECUTION_LIFECYCLE_EXTRA_KEY, existing_head.lifecycle)
        event.set_extra(CORE_EXECUTION_SESSION_EXTRA_KEY, existing_head.session)
        return existing_head.lifecycle

    if isinstance(existing_lifecycle, CoreExecutionLifecycle):
        if not matches_spec(existing_lifecycle.session):
            raise ValueError("CoreExecutionLifecycle is already bound to another execution")
        if existing_lifecycle._event_publisher is not None:
            raise ValueError(
                "CoreExecutionLifecycle has an event publisher but CoreExecutionHead is missing"
            )
        if isinstance(existing_session, CoreExecutionSession):
            if not matches_spec(existing_session):
                raise ValueError(
                    "CoreExecutionSession is already bound to another execution"
                )
            if existing_lifecycle.session is not existing_session:
                raise ValueError(
                    "CoreExecutionLifecycle and CoreExecutionSession are inconsistent"
                )
        lifecycle = existing_lifecycle
    elif isinstance(existing_session, CoreExecutionSession):
        if not matches_spec(existing_session):
            raise ValueError("CoreExecutionSession is already bound to another execution")
        lifecycle = CoreExecutionLifecycle(session=existing_session)
    else:
        lifecycle = CoreExecutionLifecycle(session=CoreExecutionSession(spec=spec))

    head = CoreExecutionHead(lifecycle)
    event.set_extra(CORE_EXECUTION_SESSION_EXTRA_KEY, lifecycle.session)
    event.set_extra(CORE_EXECUTION_LIFECYCLE_EXTRA_KEY, lifecycle)
    event.set_extra(CORE_EXECUTION_HEAD_EXTRA_KEY, head)
    return lifecycle


def bind_core_execution_head(
    event: AstrMessageEvent,
    spec: CoreExecutionSpec,
) -> CoreExecutionHead:
    """Bind and return the in-process Core Head for one execution."""

    bind_core_execution_lifecycle(event, spec)
    head = get_core_execution_head(event)
    if head is None:  # pragma: no cover - defensive guard for custom event bridges
        raise RuntimeError("CoreExecutionHead was not bound to the event")
    return head


def start_core_execution_head(
    event: AstrMessageEvent,
    spec: CoreExecutionSpec,
) -> CoreExecutionHead:
    """Bind and submit one execution through the explicit Core Head boundary."""

    head = bind_core_execution_head(event, spec)
    head.start()
    return head


def start_core_execution_lifecycle(
    event: AstrMessageEvent,
    spec: CoreExecutionSpec,
) -> CoreExecutionLifecycle:
    """Bind and submit one execution through the in-process Core lifecycle."""

    return start_core_execution_head(event, spec).lifecycle


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


def get_core_execution_head(event: AstrMessageEvent) -> CoreExecutionHead | None:
    """Return the explicit Core Head bound to the current event bridge."""

    head = event.get_extra(CORE_EXECUTION_HEAD_EXTRA_KEY)
    return head if isinstance(head, CoreExecutionHead) else None


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
    execution_spec: CoreExecutionSpec
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
            execution_spec=spec,
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
    "CORE_EXECUTION_HEAD_EXTRA_KEY",
    "CoreCapabilitySnapshot",
    "CoreCommand",
    "CoreCommandKind",
    "CoreCommandOrigin",
    "CoreCommandDisposition",
    "CoreCommandReceipt",
    "CoreEvent",
    "CoreExecutionArtifact",
    "CoreExecutionProgress",
    "CoreExecutionCommandMailbox",
    "CoreExecutionEvent",
    "CoreExecutionEventKind",
    "CoreExecutionDeadlineView",
    "CoreExecutionHead",
    "CoreExecutionLedgerPreparation",
    "CoreExecutionLifecycle",
    "CoreExecutionOutcome",
    "CoreExecutionSession",
    "CoreExecutionSessionStatus",
    "CoreExecutionSpec",
    "NativeExecutionAdapter",
    "NativeExecutionInput",
    "bind_effective_core_request",
    "bind_effective_core_capabilities",
    "bind_core_execution_lifecycle",
    "bind_core_execution_head",
    "bind_core_execution_session",
    "get_core_execution_lifecycle",
    "get_core_execution_head",
    "get_core_execution_session",
    "start_core_execution_lifecycle",
    "start_core_execution_head",
]
