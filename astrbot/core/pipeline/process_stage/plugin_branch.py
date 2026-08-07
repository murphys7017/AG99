from __future__ import annotations

import copy
from collections.abc import AsyncGenerator, Callable
from types import MethodType
from typing import Any

from astrbot.core.interaction.plugin_execution_types import (
    PluginArtifactKind,
    PluginBranchResult,
    PluginGateResolution,
    PluginOutputArtifact,
)
from astrbot.core.interaction.plugin_media_lease import PluginJobMediaLease
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, Group, MessageMember


def _snapshot_component(component: BaseMessageComponent) -> BaseMessageComponent:
    model_copy = getattr(component, "model_copy", None)
    if callable(model_copy):
        return model_copy(deep=True)
    component_copy = getattr(component, "copy", None)
    if callable(component_copy):
        return component_copy(deep=True)
    return copy.copy(component)


def snapshot_message_chain(message: MessageChain) -> MessageChain:
    return message.derive(
        [_snapshot_component(component) for component in message.chain]
    )


def _snapshot_member(member: MessageMember | None) -> MessageMember | None:
    return copy.copy(member) if member is not None else None


def _snapshot_group(group: Group | None) -> Group | None:
    if group is None:
        return None
    snapshot = copy.copy(group)
    snapshot.group_admins = (
        list(group.group_admins) if group.group_admins is not None else None
    )
    snapshot.members = (
        [_snapshot_member(member) for member in group.members]
        if group.members is not None
        else None
    )
    return snapshot


def snapshot_astrbot_message(message: AstrBotMessage) -> AstrBotMessage:
    """Copy prompt-visible message data without cloning raw platform handles."""
    snapshot = copy.copy(message)
    snapshot.message = [
        _snapshot_component(component) for component in getattr(message, "message", [])
    ]
    snapshot.message_str = str(getattr(message, "message_str", "") or "")
    snapshot.sender = _snapshot_member(getattr(message, "sender", None))
    snapshot.group = _snapshot_group(getattr(message, "group", None))
    return snapshot


def _snapshot_branch_extra(value: Any, memo: dict[int, Any]) -> Any:
    """Detach plain mutable containers while retaining opaque runtime handles."""
    value_id = id(value)
    if value_id in memo:
        return memo[value_id]
    if isinstance(value, MessageChain):
        snapshot = snapshot_message_chain(value)
        memo[value_id] = snapshot
        return snapshot
    if isinstance(value, dict):
        snapshot: dict[Any, Any] = {}
        memo[value_id] = snapshot
        snapshot.update(
            {key: _snapshot_branch_extra(item, memo) for key, item in value.items()}
        )
        return snapshot
    if isinstance(value, list):
        snapshot_list: list[Any] = []
        memo[value_id] = snapshot_list
        snapshot_list.extend(_snapshot_branch_extra(item, memo) for item in value)
        return snapshot_list
    if isinstance(value, tuple):
        snapshot_tuple = tuple(_snapshot_branch_extra(item, memo) for item in value)
        memo[value_id] = snapshot_tuple
        return snapshot_tuple
    if isinstance(value, set):
        snapshot_set = set(value)
        memo[value_id] = snapshot_set
        return snapshot_set
    if isinstance(value, frozenset):
        snapshot_frozenset = frozenset(value)
        memo[value_id] = snapshot_frozenset
        return snapshot_frozenset
    if isinstance(value, bytearray):
        snapshot_bytearray = bytearray(value)
        memo[value_id] = snapshot_bytearray
        return snapshot_bytearray
    return value


def snapshot_branch_extras(extras: dict[str, Any]) -> dict[str, Any]:
    memo: dict[int, Any] = {}
    return {
        key: _snapshot_branch_extra(value, memo)
        for key, value in extras.items()
        if key not in _BRANCH_REMOVED_EXTRA_KEYS
    }


class PluginBranchOutputSink:
    def __init__(self, result: PluginBranchResult) -> None:
        self.result = result
        self._publish_gate: Callable[[PluginGateResolution], object] | None = None

    def bind_gate_publisher(
        self,
        publish_gate: Callable[[PluginGateResolution], object],
    ) -> None:
        self._publish_gate = publish_gate

    def publish_terminal_gate(self, resolution: PluginGateResolution) -> None:
        if self.result.gate_resolution is not PluginGateResolution.PENDING:
            return
        if self._publish_gate is None:
            self.result.gate_resolution = resolution
            if resolution is PluginGateResolution.HANDLED:
                self.result.freeze_t1_artifact_boundary()
            return
        published = self._publish_gate(resolution)
        if isinstance(published, PluginGateResolution):
            self.result.gate_resolution = published

    def _append(
        self,
        message: MessageChain,
        *,
        event: AstrMessageEvent | None = None,
        mode: str,
        finalize: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        handler_invocation_id = (
            str(
                event.get_extra(
                    "_interaction_plugin_handler_invocation_id",
                    "",
                )
                or ""
            ).strip()
            if event is not None
            else ""
        )
        delivery_group_id = (
            str(
                event.get_extra(
                    "_interaction_plugin_delivery_group_id",
                    "",
                )
                or ""
            ).strip()
            if event is not None
            else ""
        ) or handler_invocation_id
        if finalize:
            kind = (
                PluginArtifactKind.SEMANTIC
                if mode == "persona"
                else PluginArtifactKind.DIRECT
            )
        else:
            kind = PluginArtifactKind.PROGRESS
        artifact = PluginOutputArtifact(
            sequence=len(self.result.output_artifacts),
            kind=kind,
            message=snapshot_message_chain(message),
            mode=mode,
            finalize=finalize,
            metadata=_snapshot_branch_extra(dict(metadata or {}), {}),
            plugin_job_id=self.result.plugin_job_id,
            origin_plugin_id=(
                str(
                    event.get_extra(
                        "_interaction_plugin_handler_module_path",
                        "",
                    )
                    or ""
                )
                if event is not None
                else ""
            ),
            origin_handler_name=(
                str(
                    event.get_extra(
                        "_interaction_plugin_handler_name",
                        "",
                    )
                    or ""
                )
                if event is not None
                else ""
            ),
            handler_invocation_id=handler_invocation_id,
            delivery_group_id=delivery_group_id,
        )
        self.result.output_artifacts.append(artifact)
        if artifact.finalize and artifact.kind is not PluginArtifactKind.PROGRESS:
            self.publish_terminal_gate(
                PluginGateResolution.STOPPED
                if event is not None and event.is_stopped()
                else PluginGateResolution.HANDLED
            )

    async def capture_plugin_output(
        self,
        message: MessageChain,
        _event: AstrMessageEvent,
        *,
        mode: str = "direct",
        finalize: bool = True,
    ) -> None:
        self._append(
            message,
            event=_event,
            mode=mode,
            finalize=finalize,
        )

    async def capture_send(
        self,
        message: MessageChain,
        *,
        event: AstrMessageEvent | None = None,
        platform_extras: dict[str, Any] | None = None,
    ) -> None:
        self._append(
            message,
            event=event,
            mode="direct",
            finalize=True,
            metadata=platform_extras,
        )

    async def capture_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        *,
        event: AstrMessageEvent | None = None,
        mode: str = "direct",
    ) -> None:
        combined = MessageChain()
        async for chunk in generator:
            combined.chain.extend(snapshot_message_chain(chunk).chain)
        if combined.chain:
            self._append(
                combined,
                event=event,
                mode=mode,
                finalize=True,
            )

    async def capture_visible_completion(self, _event: AstrMessageEvent) -> None:
        return None

    async def finalize_plugin_output_transaction(
        self,
        _event: AstrMessageEvent,
        *,
        delegated_to_core: bool,
    ) -> None:
        """Keep branch artifacts in-memory; T1/T2 owns actual delivery."""
        del delegated_to_core
        return None

    def capture_pipeline_result(self, event: AstrMessageEvent) -> bool:
        message = event.get_result()
        if message is None or not message.chain:
            return False
        self._append(
            message,
            event=event,
            mode=str(
                event.get_extra("_interaction_plugin_output_mode", "direct") or "direct"
            ),
            finalize=True,
        )
        event.clear_result()
        return True


_BRANCH_REMOVED_EXTRA_KEYS = frozenset(
    {
        "_interaction_turn_state",
        "_output_controller",
        "_interaction_output_controller",
        "_interaction_original_send",
        "_interaction_original_send_streaming",
        "_interaction_original_complete_visible_turn",
        "_interaction_output_interceptor_installed",
        "provider_request",
    }
)


def create_plugin_branch_event(
    event: AstrMessageEvent,
) -> tuple[AstrMessageEvent, PluginBranchResult, PluginBranchOutputSink]:
    """Create a concrete-type-compatible event with isolated mutable state."""
    branch = copy.copy(event)
    branch.message_str = str(event.message_str)
    branch.message_obj = snapshot_astrbot_message(event.message_obj)
    branch.session = copy.copy(event.session)
    branch._extras = snapshot_branch_extras(event.get_extra(default={}))
    branch._result = None
    branch._force_stopped = False
    branch._has_send_oper = False
    branch._temporary_local_files = []
    branch.plugins_name = (
        list(event.plugins_name) if event.plugins_name is not None else None
    )

    result = PluginBranchResult()
    result.media_lease = PluginJobMediaLease(
        parent_temporary_files=list(event._temporary_local_files),
        branch_message=MessageChain(branch.message_obj.message),
    )
    sink = PluginBranchOutputSink(result)
    branch.set_extra("_interaction_output_controller", sink)
    branch.set_extra("_interaction_plugin_branch", True)
    original_stop_event = branch.stop_event

    def track_temporary_local_file_wrapper(
        wrapped_event: AstrMessageEvent,
        path: str,
    ) -> None:
        if path and path not in wrapped_event._temporary_local_files:
            wrapped_event._temporary_local_files.append(path)
        result.media_lease.track_temporary_file(path)

    def stop_event_wrapper(wrapped_event: AstrMessageEvent) -> None:
        del wrapped_event
        original_stop_event()
        sink.publish_terminal_gate(PluginGateResolution.STOPPED)

    async def send_wrapper(
        wrapped_event: AstrMessageEvent,
        message: MessageChain,
    ) -> None:
        await sink.capture_send(message, event=wrapped_event)
        wrapped_event._has_send_oper = True

    async def send_streaming_wrapper(
        wrapped_event: AstrMessageEvent,
        generator: AsyncGenerator[MessageChain, None],
        use_fallback: bool = False,
    ) -> None:
        del use_fallback
        await sink.capture_streaming(generator, event=wrapped_event)
        wrapped_event._has_send_oper = True

    async def send_message_with_extras_wrapper(
        wrapped_event: AstrMessageEvent,
        message: MessageChain,
        *,
        platform_extras: dict[str, Any] | None = None,
        record_send_operation: bool = True,
    ) -> None:
        previous_has_send_oper = wrapped_event._has_send_oper
        await sink.capture_send(
            message,
            event=wrapped_event,
            platform_extras=platform_extras,
        )
        wrapped_event._has_send_oper = (
            True if record_send_operation else previous_has_send_oper
        )

    async def complete_visible_turn_wrapper(
        wrapped_event: AstrMessageEvent,
    ) -> None:
        await sink.capture_visible_completion(wrapped_event)

    branch.send = MethodType(send_wrapper, branch)
    branch.send_streaming = MethodType(send_streaming_wrapper, branch)
    branch.send_message_with_extras = MethodType(
        send_message_with_extras_wrapper,
        branch,
    )
    branch.complete_visible_turn = MethodType(
        complete_visible_turn_wrapper,
        branch,
    )
    branch.track_temporary_local_file = MethodType(
        track_temporary_local_file_wrapper,
        branch,
    )
    branch.stop_event = MethodType(stop_event_wrapper, branch)
    return branch, result, sink
