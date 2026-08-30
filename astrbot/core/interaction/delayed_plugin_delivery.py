from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from astrbot import logger
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .conversation_history import CONVERSATION_COMMITTED_TURN_ID_EXTRA
from .observation import RuntimeObservation, RuntimeObservationTarget
from .personal_expression_guard import fingerprint_personal_expression
from .plugin_execution_runtime import PluginExecutionRuntime
from .plugin_execution_types import (
    PluginArtifactKind,
    PluginBranchResult,
    PluginDeliveryDisposition,
    PluginOutputArtifact,
)
from .runtime_event import RuntimeObservationEvent
from .turn_state import (
    get_interaction_turn_visible_message_fingerprints,
    get_interaction_turn_visible_outputs,
)
from .visible_message_fingerprint import (
    fingerprint_visible_message,
    is_plain_text_message,
)


@dataclass(slots=True)
class DelayedPluginDeliveryContext:
    parent_event: AstrMessageEvent
    config_id: str
    runtime_config: Mapping[str, Any]
    plugin_context: Any
    personal_runtime_manager: Any
    middleware: Any
    parent_turn_id: str
    parent_conversation_id: str | None

    @classmethod
    def capture(
        cls,
        *,
        parent_event: AstrMessageEvent,
        config_id: str,
        runtime_config: Mapping[str, Any],
        plugin_context: Any,
        personal_runtime_manager: Any,
        middleware: Any,
    ) -> DelayedPluginDeliveryContext:
        conversation_manager = getattr(plugin_context, "conversation_manager", None)
        conversation_id = (
            conversation_manager.session_conversations.get(
                parent_event.unified_msg_origin
            )
            if conversation_manager is not None
            else None
        )
        return cls(
            parent_event=parent_event,
            config_id=config_id,
            runtime_config=dict(runtime_config),
            plugin_context=plugin_context,
            personal_runtime_manager=personal_runtime_manager,
            middleware=middleware,
            parent_turn_id=str(parent_event.get_extra("_turn_id", "") or ""),
            parent_conversation_id=(
                str(conversation_id).strip() or None
                if conversation_id is not None
                else None
            ),
        )

    def resolve_parent_conversation_id(self) -> str | None:
        committed = str(
            self.parent_event.get_extra(
                "_interaction_committed_conversation_id",
                "",
            )
            or ""
        ).strip()
        return committed or self.parent_conversation_id


@dataclass(slots=True)
class DelayedPluginDeliverySummary:
    delivered_group_count: int = 0
    suppressed_group_count: int = 0
    unsupported_group_count: int = 0
    failed_group_count: int = 0


class DelayedPluginDeliveryCoordinator:
    """Deliver post-T1 Plugin artifacts through low-priority Personal turns."""

    def __init__(self, runtime: PluginExecutionRuntime) -> None:
        self.runtime = runtime

    async def deliver(
        self,
        context: DelayedPluginDeliveryContext,
        result: PluginBranchResult,
    ) -> DelayedPluginDeliverySummary:
        summary = DelayedPluginDeliverySummary()
        if not result.delayed_delivery_eligible:
            return summary

        target = self._build_target(context.parent_event)
        groups = self._group_final_artifacts(result.output_artifacts)
        parent_fingerprints = self._parent_visible_fingerprints(
            context.parent_event
        )
        parent_message_fingerprints = (
            get_interaction_turn_visible_message_fingerprints(context.parent_event)
        )
        for group_id, profile, artifacts in groups:
            reserved_at = time.time()
            if not target.support_proactive_message:
                await self._finish_without_delivery(
                    artifacts,
                    PluginDeliveryDisposition.DELAYED_TARGET_UNSUPPORTED,
                )
                summary.unsupported_group_count += 1
                self._log_group_result(
                    context,
                    result,
                    group_id,
                    profile,
                    "target_unsupported",
                    artifacts=artifacts,
                    reserved_at=reserved_at,
                    target_proactive_supported=False,
                    duplicate_disposition="",
                    delivery_drop_reason="target_unsupported",
                )
                continue

            reserved = await self._reserve_artifacts(artifacts)
            if not reserved:
                summary.suppressed_group_count += 1
                self._log_group_result(
                    context,
                    result,
                    group_id,
                    profile,
                    "reservation_conflict",
                    artifacts=artifacts,
                    reserved_at=reserved_at,
                    target_proactive_supported=True,
                    duplicate_disposition="delivery_not_produced",
                    delivery_drop_reason="reservation_conflict",
                )
                continue
            message = self._combine_messages(reserved)
            visible_fingerprint = fingerprint_personal_expression(
                message.get_plain_text()
            )
            message_fingerprint = fingerprint_visible_message(message)
            if is_plain_text_message(message):
                duplicate_visible_output = (
                    visible_fingerprint is not None
                    and visible_fingerprint in parent_fingerprints
                )
            else:
                duplicate_visible_output = (
                    message_fingerprint is not None
                    and message_fingerprint in parent_message_fingerprints
                )
            if duplicate_visible_output:
                await self._finish_artifacts(
                    reserved,
                    PluginDeliveryDisposition.SUPPRESSED_DUPLICATE_VISIBLE_OUTPUT,
                )
                summary.suppressed_group_count += 1
                self._log_group_result(
                    context,
                    result,
                    group_id,
                    profile,
                    "duplicate_visible_output",
                    artifacts=reserved,
                    reserved_at=reserved_at,
                    target_proactive_supported=True,
                    duplicate_disposition=(
                        PluginDeliveryDisposition
                        .SUPPRESSED_DUPLICATE_VISIBLE_OUTPUT.value
                    ),
                    delivery_drop_reason="duplicate_visible_output",
                )
                continue

            delivery_details: dict[str, Any] = {}
            try:
                delivered, delivery_details = await self._deliver_group(
                    context,
                    result,
                    target,
                    group_id,
                    profile,
                    reserved,
                    message,
                )
            except Exception:
                await self._finish_artifacts(
                    reserved,
                    PluginDeliveryDisposition.DELIVERY_FAILED,
                )
                summary.failed_group_count += 1
                logger.exception(
                    "Delayed Plugin delivery failed: job_id=%s group_id=%s profile=%s",
                    result.plugin_job_id,
                    group_id,
                    profile,
                )
                self._log_group_result(
                    context,
                    result,
                    group_id,
                    profile,
                    "delivery_failed",
                    artifacts=reserved,
                    reserved_at=reserved_at,
                    target_proactive_supported=True,
                    delivery_drop_reason="delivery_exception",
                )
                continue
            if not delivered:
                await self._finish_artifacts(
                    reserved,
                    PluginDeliveryDisposition.DELIVERY_FAILED,
                )
                summary.failed_group_count += 1
                self._log_group_result(
                    context,
                    result,
                    group_id,
                    profile,
                    "delivery_failed",
                    artifacts=reserved,
                    reserved_at=reserved_at,
                    delayed_turn_id=str(
                        delivery_details.get("delayed_turn_id", "") or ""
                    ),
                    written_to_history=bool(
                        delivery_details.get("written_to_history", False)
                    ),
                    history_status=str(
                        delivery_details.get("history_status", "") or ""
                    ),
                    target_proactive_supported=True,
                    delivery_drop_reason=str(
                        delivery_details.get("delivery_drop_reason", "")
                        or "delivery_rejected"
                    ),
                )
                continue
            await self._finish_artifacts(
                reserved,
                PluginDeliveryDisposition.DELIVERED_DELAYED,
            )
            summary.delivered_group_count += 1
            self._log_group_result(
                context,
                result,
                group_id,
                profile,
                "delivered",
                artifacts=reserved,
                reserved_at=reserved_at,
                delivered_at=time.time(),
                delayed_turn_id=str(
                    delivery_details.get("delayed_turn_id", "") or ""
                ),
                written_to_history=bool(
                    delivery_details.get("written_to_history", False)
                ),
                history_status=str(
                    delivery_details.get("history_status", "") or ""
                ),
                target_proactive_supported=True,
            )
        return summary

    async def _deliver_group(
        self,
        context: DelayedPluginDeliveryContext,
        result: PluginBranchResult,
        target: RuntimeObservationTarget,
        group_id: str,
        profile: str,
        artifacts: list[PluginOutputArtifact],
        message: MessageChain,
    ) -> tuple[bool, dict[str, Any]]:
        metadata = self._build_metadata(
            context,
            result,
            group_id,
            artifacts,
        )
        observation = RuntimeObservation(
            kind=profile,
            source="plugin_delayed_output",
            occurred_at=time.time(),
            target_session=target,
            correlation_id=f"{result.plugin_job_id}:{group_id}",
            payload={"visible_reply_material": message.get_plain_text()},
        )
        event = RuntimeObservationEvent(
            context=context.plugin_context,
            observation=observation,
        )
        event.set_extra("_turn_id", metadata["delayed_turn_id"])
        event.set_extra("_interaction_delivery_metadata", metadata)

        def prepare_parent_context(runtime_event: RuntimeObservationEvent) -> None:
            parent_conversation_id = context.resolve_parent_conversation_id()
            metadata["parent_conversation_id"] = parent_conversation_id
            runtime_event.set_extra(
                "_interaction_delivery_metadata",
                dict(metadata),
            )
            runtime_event.set_extra(
                "_interaction_fixed_conversation_id",
                parent_conversation_id,
            )
        if profile == "delayed_plugin_direct":
            async def deliver_direct(runtime_event, turn):
                prepare_parent_context(runtime_event)
                await context.middleware.handle_runtime_output(
                    runtime_event,
                    turn,
                    message,
                    platform_extras=self._combine_platform_extras(artifacts),
                )
                return True

            handler = deliver_direct
        else:
            if not message.get_plain_text().strip():
                return False, {
                    "delayed_turn_id": metadata["delayed_turn_id"],
                    "written_to_history": False,
                    "history_status": "empty_semantic_text",
                    "delivery_drop_reason": "empty_semantic_text",
                }

            async def deliver_expression(runtime_event, turn):
                prepare_parent_context(runtime_event)
                return await context.middleware.handle_runtime_observation(
                    runtime_event,
                    turn,
                )

            handler = deliver_expression

        delivered = bool(
            await context.personal_runtime_manager.submit_delayed_plugin_event(
                event,
                context.config_id,
                context.plugin_context,
                dict(context.runtime_config),
                handler,
                profile=profile,
            )
        )
        history_status = str(
            event.get_extra("_interaction_delayed_history_skipped_reason", "") or ""
        )
        committed_turn_id = str(
            event.get_extra(CONVERSATION_COMMITTED_TURN_ID_EXTRA, "") or ""
        )
        if delivered and not committed_turn_id and not history_status:
            history_status = str(
                event.get_extra(
                    "_interaction_turn_finalization_failure_reason",
                    "history_not_committed",
                )
                or "history_not_committed"
            )
        return delivered, {
            "delayed_turn_id": metadata["delayed_turn_id"],
            "written_to_history": bool(
                delivered and committed_turn_id and not history_status
            ),
            "history_status": history_status,
            "delivery_drop_reason": "" if delivered else "delivery_rejected",
        }

    async def _reserve_artifacts(
        self,
        artifacts: list[PluginOutputArtifact],
    ) -> list[PluginOutputArtifact]:
        reserved = []
        for artifact in artifacts:
            await self.runtime.register_delivery(artifact.delivery_key)
            if await self.runtime.reserve_delivery(artifact.delivery_key):
                reserved.append(artifact)
        return reserved

    async def _finish_without_delivery(
        self,
        artifacts: list[PluginOutputArtifact],
        disposition: PluginDeliveryDisposition,
    ) -> list[PluginOutputArtifact]:
        reserved = await self._reserve_artifacts(artifacts)
        await self._finish_artifacts(reserved, disposition)
        return reserved

    async def _finish_artifacts(
        self,
        artifacts: list[PluginOutputArtifact],
        disposition: PluginDeliveryDisposition,
    ) -> None:
        for artifact in artifacts:
            await self.runtime.finish_delivery(artifact.delivery_key, disposition)

    @staticmethod
    def _group_final_artifacts(
        artifacts: list[PluginOutputArtifact],
    ) -> list[tuple[str, str, list[PluginOutputArtifact]]]:
        grouped: dict[tuple[str, str], list[PluginOutputArtifact]] = defaultdict(list)
        for artifact in artifacts:
            if not artifact.finalize or artifact.kind is PluginArtifactKind.PROGRESS:
                continue
            group_id = (
                artifact.delivery_group_id.strip()
                or artifact.handler_invocation_id.strip()
                or f"artifact-{artifact.sequence}"
            )
            semantic_output = (
                artifact.kind is PluginArtifactKind.SEMANTIC
                or artifact.mode == "persona"
            )
            profile = (
                "delayed_plugin_expression"
                if semantic_output
                and is_plain_text_message(artifact.message)
                and artifact.message.get_plain_text().strip()
                else "delayed_plugin_direct"
            )
            grouped[(group_id, profile)].append(artifact)
        return [
            (group_id, profile, group)
            for (group_id, profile), group in grouped.items()
        ]

    @staticmethod
    def _combine_messages(artifacts: list[PluginOutputArtifact]) -> MessageChain:
        if not artifacts:
            return MessageChain()
        combined = artifacts[0].message.derive()
        for artifact in artifacts:
            combined.chain.extend(artifact.message.chain)
        return combined

    @staticmethod
    def _combine_platform_extras(
        artifacts: list[PluginOutputArtifact],
    ) -> dict[str, Any]:
        combined: dict[str, Any] = {}
        for artifact in artifacts:
            combined.update(artifact.metadata)
        return combined

    @staticmethod
    def _build_target(event: AstrMessageEvent) -> RuntimeObservationTarget:
        group_id = event.get_group_id() if event.get_message_type().value == "GroupMessage" else None
        group = getattr(event.message_obj, "group", None)
        return RuntimeObservationTarget(
            platform_id=event.get_platform_id(),
            platform_name=event.get_platform_name(),
            message_type=event.get_message_type(),
            session_id=event.get_session_id(),
            support_proactive_message=event.platform_meta.support_proactive_message,
            support_personal_runtime=event.platform_meta.support_personal_runtime,
            group_id=str(group_id).strip() or None if group_id is not None else None,
            group_name=str(getattr(group, "group_name", "") or "").strip() or None,
        )

    @staticmethod
    def _parent_visible_fingerprints(event: AstrMessageEvent) -> set[str]:
        fingerprints = set()
        for output in get_interaction_turn_visible_outputs(event):
            fingerprint = fingerprint_personal_expression(
                str(output.get("text", "") or "")
            )
            if fingerprint is not None:
                fingerprints.add(fingerprint)
        return fingerprints

    @staticmethod
    def _build_metadata(
        context: DelayedPluginDeliveryContext,
        result: PluginBranchResult,
        group_id: str,
        artifacts: list[PluginOutputArtifact],
    ) -> dict[str, Any]:
        first = artifacts[0]
        delivery_keys = [
            {
                "plugin_job_id": artifact.plugin_job_id,
                "handler_invocation_id": artifact.handler_invocation_id,
                "artifact_sequence": artifact.sequence,
            }
            for artifact in artifacts
        ]
        delivery_fingerprint = hashlib.sha256(
            json.dumps(
                delivery_keys,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "plugin_delayed_output": True,
            "plugin_job_id": result.plugin_job_id,
            "origin_plugin_id": first.origin_plugin_id,
            "origin_handler_name": first.origin_handler_name,
            "handler_invocation_id": first.handler_invocation_id,
            "delivery_group_id": group_id,
            "parent_turn_id": context.parent_turn_id,
            "parent_conversation_id": context.resolve_parent_conversation_id(),
            "detached_at": result.detached_at,
            "delivery_keys": delivery_keys,
            "delivery_fingerprint": delivery_fingerprint,
            "delayed_turn_id": uuid.uuid4().hex,
        }

    @staticmethod
    def _log_group_result(
        context: DelayedPluginDeliveryContext,
        result: PluginBranchResult,
        group_id: str,
        profile: str,
        status: str,
        *,
        artifacts: list[PluginOutputArtifact],
        reserved_at: float | None = None,
        delivered_at: float | None = None,
        delayed_turn_id: str = "",
        written_to_history: bool = False,
        history_status: str = "",
        duplicate_disposition: str = "",
        target_proactive_supported: bool,
        delivery_drop_reason: str = "",
    ) -> None:
        delivery_keys = [
            {
                "plugin_job_id": artifact.plugin_job_id,
                "handler_invocation_id": artifact.handler_invocation_id,
                "artifact_sequence": artifact.sequence,
            }
            for artifact in artifacts
        ]
        logger.debug(
            "DIAG plugin.delayed_delivery: platform_id=%s session_id=%s "
            "parent_turn_id=%s delayed_turn_id=%s job_id=%s group_id=%s "
            "delivery_keys=%s profile=%s status=%s reserved_at=%s "
            "delivered_at=%s written_to_history=%s "
            "history_status=%s duplicate_disposition=%s "
            "target_proactive_supported=%s "
            "delivery_drop_reason=%s",
            context.parent_event.get_platform_id(),
            context.parent_event.session_id,
            context.parent_turn_id,
            delayed_turn_id,
            result.plugin_job_id,
            group_id,
            json.dumps(delivery_keys, ensure_ascii=True, separators=(",", ":")),
            profile,
            status,
            f"{reserved_at:.6f}" if reserved_at is not None else "none",
            f"{delivered_at:.6f}" if delivered_at is not None else "none",
            written_to_history,
            history_status,
            duplicate_disposition,
            target_proactive_supported,
            delivery_drop_reason,
        )
