from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from astrbot.core.message.message_event_result import MessageChain


class PluginGateResolution(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    HANDLED = "handled"
    STOPPED = "stopped"
    DELEGATED = "delegated"
    FAILED = "failed"
    EXPIRED = "expired"


class PluginJobState(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PluginArtifactKind(str, Enum):
    PROGRESS = "progress"
    SEMANTIC = "semantic"
    DIRECT = "direct"


class PluginDeliveryDisposition(str, Enum):
    PRODUCED = "produced"
    DELIVERY_RESERVED = "delivery_reserved"
    DELIVERED_INLINE = "delivered_inline"
    DELIVERED_DELAYED = "delivered_delayed"
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"
    SUPPRESSED_DUPLICATE_VISIBLE_OUTPUT = "suppressed_duplicate_visible_output"
    DELAYED_TARGET_UNSUPPORTED = "delayed_delivery_target_unsupported"
    DELIVERY_FAILED = "delivery_failed"


@dataclass(frozen=True, slots=True)
class PluginDeliveryKey:
    plugin_job_id: str
    handler_invocation_id: str
    artifact_sequence: int


@dataclass(slots=True)
class PluginOutputArtifact:
    sequence: int
    kind: PluginArtifactKind
    message: MessageChain
    mode: str
    finalize: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    plugin_job_id: str = ""
    origin_plugin_id: str = ""
    origin_handler_name: str = ""
    handler_invocation_id: str = ""
    delivery_group_id: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def delivery_key(self) -> PluginDeliveryKey:
        return PluginDeliveryKey(
            plugin_job_id=self.plugin_job_id,
            handler_invocation_id=self.handler_invocation_id,
            artifact_sequence=self.sequence,
        )


@dataclass(slots=True)
class PluginBranchResult:
    plugin_job_id: str = ""
    gate_resolution: PluginGateResolution = PluginGateResolution.PENDING
    job_state: PluginJobState = PluginJobState.RUNNING
    output_artifacts: list[PluginOutputArtifact] = field(default_factory=list)
    t1_artifact_count: int | None = None
    provider_executions: int = 0
    ignored_provider_requests_after_detach: int = 0
    stopped: bool = False
    failure: BaseException | None = None
    delegated_to_core: bool = False
    started_at: float | None = None
    gate_resolved_at: float | None = None
    gate_resolved_monotonic: float | None = None
    detached_at: float | None = None
    completed_at: float | None = None
    duration_ms: float | None = None
    media_lease: Any | None = field(default=None, repr=False)

    def freeze_t1_artifact_boundary(self) -> None:
        if self.t1_artifact_count is None:
            self.t1_artifact_count = len(self.output_artifacts)

    def cleanup_media(self) -> None:
        cleanup = getattr(self.media_lease, "cleanup", None)
        if callable(cleanup):
            cleanup()
