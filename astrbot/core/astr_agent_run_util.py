import asyncio
import re
import time
import traceback
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from astrbot.core import logger
from astrbot.core.agent.message import Message
from astrbot.core.agent.response import AgentResponse, AgentStats
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.deadline import TurnDeadlineExceeded
from astrbot.core.execution import (
    CoreExecutionArtifact,
    CoreExecutionEventKind,
    CoreExecutionProgress,
    get_core_execution_head,
)
from astrbot.core.interaction.output_modes import (
    CoreOutputDelivery,
    OutputOrigin,
    temporary_core_output_delivery,
    temporary_output_origin,
)
from astrbot.core.interaction.turn_state import (
    get_interaction_turn_deadline,
    is_interaction_turn_core_delegated,
    record_interaction_turn_core_execution_event,
)
from astrbot.core.message.components import BaseMessageComponent, Json, Plain
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.persona_error_reply import (
    extract_persona_custom_error_message_from_event,
)
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.provider import Provider, TTSProvider

AgentRunner = ToolLoopAgentRunner[AstrAgentContext]


@dataclass(frozen=True, slots=True)
class ExecutorStreamItem:
    """One executor-neutral item consumed by the existing output bridge.

    ``MessageChain`` remains an AstrBot output material type. The item hides the
    Native runner's ``AgentResponse`` container without creating a second
    visible-output contract.
    """

    kind: str
    chain: MessageChain | None = None


@dataclass(frozen=True, slots=True)
class NativeExecutionEvidence:
    """Native evidence bundle; response, messages, and stats remain live references."""

    final_response: LLMResponse | None
    messages: list[Message]
    stats: AgentStats
    was_aborted: bool


class NativeExecutorAdapter:
    """Expose the Native runner through the Core execution boundary.

    The adapter exposes Native step responses and stop/state operations. The
    caller owns iteration and closing the step stream; output conversion stays
    outside this boundary. AgentResponse is still a Native-specific contract.
    """

    executor_id = "native"

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner

    @property
    def run_context(self) -> AstrAgentContext:
        """Expose the execution context needed by follow-up admission."""
        return self._runner.run_context

    @property
    def streaming(self) -> bool:
        """Expose the runner's response mode at the executor boundary."""
        return self._runner.streaming

    @property
    def req(self):
        """Expose the mutable request used for the max-step fallback."""
        return self._runner.req

    @property
    def agent_hooks(self):
        return self._runner.agent_hooks

    def follow_up(self, *, message_text: str):
        """Capture a follow-up through Core when this execution has a Head."""

        context = getattr(self._runner.run_context, "context", None)
        event = getattr(context, "event", None)
        execution_head = get_core_execution_head(event) if event is not None else None
        if execution_head is not None:
            return execution_head.provide_input(
                executor_id=self.executor_id,
                message_text=message_text,
            )
        return self._runner.follow_up(message_text=message_text)

    def request_follow_up(self, message_text: str):
        """Accept one already-authorized follow-up at the Native boundary."""

        return self._runner.follow_up(message_text=message_text)

    def cancel_follow_up(self, ticket) -> bool:
        """Withdraw a pending follow-up through the executor boundary."""
        return self._runner.cancel_follow_up(ticket)

    def emit_event(
        self,
        *,
        kind: CoreExecutionEventKind,
        metadata: dict | None = None,
    ):
        """Publish one Native fact through the owning Core execution boundary."""
        event = self._runner.run_context.context.event
        execution_head = get_core_execution_head(event)
        if execution_head is not None and execution_head.terminal_event is not None:
            return execution_head.terminal_event.execution
        return record_interaction_turn_core_execution_event(
            event,
            kind=kind,
            executor_id=self.executor_id,
            metadata=metadata,
        )

    def complete(
        self,
        *,
        artifact: CoreExecutionArtifact | None = None,
        metadata: dict | None = None,
    ):
        """Report one successful terminal outcome through the Core boundary.

        The interaction bridge preserves terminal first-write semantics. An
        earlier cancellation or failure therefore remains authoritative when a
        late Native completion arrives.
        """
        if artifact is not None:
            self.emit_event(
                kind=CoreExecutionEventKind.ARTIFACT_READY,
                metadata=artifact.event_metadata(),
            )
        return self.emit_event(
            kind=CoreExecutionEventKind.COMPLETED,
            metadata=metadata,
        )

    def fail(self, *, metadata: dict | None = None):
        """Report one Native failure through the Core lifecycle boundary."""
        return self.emit_event(
            kind=CoreExecutionEventKind.FAILED,
            metadata=metadata,
        )

    def finalize(self):
        """Project the Native terminal state into one Core terminal outcome."""

        if self.completed_successfully():
            return self.complete(artifact=self.final_response_artifact())
        return self.fail(metadata=self.failure_metadata())

    def cancel(self, *, metadata: dict | None = None):
        """Report cancellation through the Core lifecycle and stop callback."""
        return self.emit_event(
            kind=CoreExecutionEventKind.CANCELLED,
            metadata=metadata,
        )

    def observe_response(self, response: ExecutorStreamItem):
        """Project non-visible Native execution progress into Core facts.

        Only tool boundaries are execution progress. Text and reasoning remain
        owned by the existing output path, so this method cannot create another
        user-visible response stream.
        """
        if response.kind not in {"tool_call", "tool_call_result"}:
            return None
        chain = response.chain
        if chain is None:
            return None
        attributes: dict[str, object] = {
            "response_type": response.kind,
            "message_type": str(chain.type or ""),
            "component_count": len(chain.chain),
        }
        details = _extract_chain_json_data(chain)
        if isinstance(details, dict):
            tool_name = str(details.get("name", "") or "").strip()
            if tool_name:
                attributes["tool_name"] = tool_name
            tool_call_id = str(details.get("id", "") or "").strip()
            if tool_call_id:
                attributes["tool_call_id"] = tool_call_id
            if response.kind == "tool_call_result":
                result = details.get("result")
                if result is not None:
                    attributes["result_length"] = len(str(result))
        if response.kind == "tool_call_result" and "result_length" not in attributes:
            attributes["result_length"] = len(
                chain.get_plain_text(with_other_comps_mark=True)
            )
        progress = CoreExecutionProgress(
            source="native_response",
            phase=response.kind,
            attributes=attributes,
        )
        return self.emit_event(
            kind=CoreExecutionEventKind.PROGRESS,
            metadata=progress.event_metadata(),
        )

    def final_response_artifact(self) -> CoreExecutionArtifact | None:
        """Describe the Native final response without exposing it to Core."""
        response = self.final_response()
        if response is None:
            return None
        completion_text = str(response.completion_text or "")
        result_chain = response.result_chain
        return CoreExecutionArtifact(
            artifact_id="final_response",
            artifact_kind=(
                "text"
                if completion_text
                else "message_chain"
                if result_chain is not None
                else "empty"
            ),
            attributes={
                "text_length": len(completion_text),
                "component_count": (
                    len(result_chain.chain) if result_chain is not None else 0
                ),
            },
        )

    def completed_successfully(self) -> bool:
        """Return the Native terminal classification needed by the Core bridge."""
        response = self.final_response()
        return self.done() and (response is None or response.role != "err")

    def failure_metadata(self) -> dict[str, str]:
        """Project the Native failure state into bounded Core diagnostics."""
        response = self.final_response()
        metadata = {
            "reason": "runner_error" if self.done() else "runner_not_completed"
        }
        if response is not None and response.completion_text:
            metadata["error"] = str(response.completion_text)[:2000]
        return metadata

    def request_stop(self) -> None:
        self._runner.request_stop()

    def request_cancellation(self, *, metadata: dict | None = None):
        """Route a stop signal through Core before falling back to Native stop."""

        event = self._runner.run_context.context.event
        if get_core_execution_head(event) is None:
            self.request_stop()
        return self.cancel(metadata=metadata)

    def release_from_core_head(self) -> bool:
        """Release this executor only after the Core session becomes terminal."""

        execution_head = get_core_execution_head(
            self._runner.run_context.context.event
        )
        if execution_head is None:
            return False
        return execution_head.release_executor(executor_id=self.executor_id)

    def step(self) -> AsyncGenerator[AgentResponse, None]:
        """Open one Native step stream; the consumer must close it on exit."""
        return self._runner.step()

    async def stream(self) -> AsyncGenerator[ExecutorStreamItem, None]:
        """Normalize Native response containers for the Core output bridge."""

        native_stream = self.step()
        try:
            async for response in native_stream:
                yield ExecutorStreamItem(
                    kind=response.type,
                    chain=response.data.get("chain"),
                )
        finally:
            await native_stream.aclose()

    def force_final_response(self, *, instruction: str) -> None:
        """Disable further tools and append the bounded final-response prompt."""
        if self._runner.req:
            self._runner.req.func_tool = None
        self._runner.run_context.messages.append(
            Message(role="user", content=instruction)
        )

    async def notify_agent_done(self, response: LLMResponse) -> None:
        """Notify Native lifecycle hooks without exposing the runner object."""
        await self._runner.agent_hooks.on_agent_done(
            self._runner.run_context,
            response,
        )

    @property
    def provider(self) -> Provider:
        """Return the provider used by the transitional Native runner."""

        return self._runner.provider

    def done(self) -> bool:
        return self._runner.done()

    def was_aborted(self) -> bool:
        return self._runner.was_aborted()

    def final_response(self) -> LLMResponse | None:
        return self._runner.get_final_llm_resp()

    def evidence(self) -> NativeExecutionEvidence:
        """Return post-execution evidence without exposing the runner."""

        return NativeExecutionEvidence(
            final_response=self.final_response(),
            messages=self.messages,
            stats=self.stats,
            was_aborted=self.was_aborted(),
        )

    def final_response_chain(self) -> MessageChain | None:
        """Build the final visible chain without exposing response internals."""

        response = self.final_response()
        if response is None:
            return None
        if response is not None and response.completion_text:
            return MessageChain().message(response.completion_text)
        if response.result_chain is not None:
            return response.result_chain
        return MessageChain()

    @property
    def messages(self) -> list[Message]:
        return self._runner.run_context.messages

    @property
    def stats(self) -> AgentStats:
        return self._runner.stats


class NativeExecutionLoop:
    """Drive one Native Core execution without owning visible output.

    This owner centralizes step limits, stop observation, progress projection,
    and stream cleanup. Callers still decide how a normalized response becomes
    text, tool status, TTS input, history, or platform output.
    """

    def __init__(self, executor: NativeExecutorAdapter, *, max_step: int) -> None:
        self._executor = executor
        self._max_step = max_step
        self._event = executor.run_context.context.event
        self.aborted = False

    async def stream(self) -> AsyncGenerator[ExecutorStreamItem, None]:
        """Yield normalized responses while preserving Native loop controls."""

        self._executor.emit_event(
            kind=CoreExecutionEventKind.WORKING,
            metadata={"streaming": bool(self._executor.streaming)},
        )
        if self._executor.done():
            return

        step_idx = 0
        while step_idx < self._max_step + 1:
            step_idx += 1
            if step_idx == self._max_step + 1:
                logger.warning(
                    "Agent reached max steps (%s), forcing a final response.",
                    self._max_step,
                )
                if not self._executor.done():
                    self._executor.force_final_response(
                        instruction=(
                            "工具调用次数已达到上限，请停止使用工具，并根据已经收集到的信息，"
                            "对你的任务和发现进行总结，然后直接回复用户。"
                        )
                    )

            stop_watcher = asyncio.create_task(
                self._watch_stop_signal(),
            )
            step_stream = self._executor.stream()
            try:
                async for response in step_stream:
                    if _should_stop_agent(self._event):
                        self._executor.request_cancellation(
                            metadata={"reason": "agent_aborted"}
                        )

                    if response.kind == "aborted":
                        self.aborted = True
                        return

                    if _should_stop_agent(self._event):
                        continue

                    self._executor.observe_response(response)
                    yield response

                if self._executor.done():
                    return
            finally:
                if not stop_watcher.done():
                    stop_watcher.cancel()
                try:
                    await stop_watcher
                except asyncio.CancelledError:
                    pass
                finally:
                    await step_stream.aclose()

    async def _watch_stop_signal(self) -> None:
        """Request cancellation when the active turn asks the loop to stop."""

        while not self._executor.done():
            if _should_stop_agent(self._event):
                self._executor.request_cancellation(
                    metadata={"reason": "agent_aborted"}
                )
                return
            await asyncio.sleep(0.5)


def _should_stop_agent(astr_event) -> bool:
    return astr_event.is_stopped() or bool(astr_event.get_extra("agent_stop_requested"))


def _cancellation_reason(astr_event, *, fallback: str) -> str:
    deadline = get_interaction_turn_deadline(astr_event)
    if deadline is not None and deadline.expired():
        return "deadline_exceeded"
    return fallback


async def _send_core_event_message(
    astr_event,
    message: MessageChain,
    *,
    delivery: CoreOutputDelivery,
) -> None:
    if (
        delivery is CoreOutputDelivery.PROGRESS
        and is_interaction_turn_core_delegated(astr_event)
    ):
        # Personal owns the user-visible Interaction surface. Non-visible
        # execution progress is emitted by NativeExecutorAdapter.
        return

    with temporary_output_origin(astr_event, OutputOrigin.CORE.value):
        with temporary_core_output_delivery(astr_event, delivery.value):
            await astr_event.send(message)


def _truncate_tool_result(text: str, limit: int = 70) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[: limit - 3]}..."


def _extract_chain_json_data(msg_chain: MessageChain) -> dict | None:
    if not msg_chain.chain:
        return None
    first_comp = msg_chain.chain[0]
    if isinstance(first_comp, Json) and isinstance(first_comp.data, dict):
        return first_comp.data
    return None


def _record_tool_call_name(
    tool_info: dict | None, tool_name_by_call_id: dict[str, str]
) -> None:
    if not isinstance(tool_info, dict):
        return
    tool_call_id = tool_info.get("id")
    tool_name = tool_info.get("name")
    if tool_call_id is None or tool_name is None:
        return
    tool_name_by_call_id[str(tool_call_id)] = str(tool_name)


def _build_tool_call_status_message(tool_info: dict | None) -> str:
    if tool_info:
        return f"🔨 调用工具: {tool_info.get('name', 'unknown')}"
    return "🔨 调用工具..."


def _build_tool_result_status_message(
    msg_chain: MessageChain, tool_name_by_call_id: dict[str, str]
) -> str:
    tool_name = "unknown"
    tool_result = ""

    result_data = _extract_chain_json_data(msg_chain)
    if result_data:
        tool_call_id = result_data.get("id")
        if tool_call_id is not None:
            tool_name = tool_name_by_call_id.pop(str(tool_call_id), "unknown")
        tool_result = str(result_data.get("result", ""))

    if not tool_result:
        tool_result = msg_chain.get_plain_text(with_other_comps_mark=True)
    tool_result = _truncate_tool_result(tool_result, 70)

    status_msg = f"🔨 调用工具: {tool_name}"
    if tool_result:
        status_msg = f"{status_msg}\n📎 返回结果: {tool_result}"
    return status_msg


def _should_buffer_llm_result(
    buffer_intermediate_messages: bool,
    stream_to_general: bool,
    executor: NativeExecutorAdapter,
) -> bool:
    return (
        buffer_intermediate_messages
        and not stream_to_general
        and not executor.streaming
    )


def _merge_buffered_llm_chains(
    buffered_llm_chains: list[MessageChain],
) -> MessageChain | None:
    if not buffered_llm_chains:
        return None

    merged_chain = MessageChain()
    for chain in buffered_llm_chains:
        merged_chain.chain.extend(chain.chain)
    buffered_llm_chains.clear()
    return merged_chain


def _require_executor_stream_chain(item: ExecutorStreamItem) -> MessageChain:
    if item.chain is None:
        raise RuntimeError(f"executor stream item {item.kind!r} has no message chain")
    return item.chain


class NativeExecutionOutputBridge:
    """Project Native loop items into the existing AstrBot output boundary."""

    def __init__(
        self,
        executor: NativeExecutorAdapter,
        *,
        max_step: int = 30,
        show_tool_use: bool = True,
        show_tool_call_result: bool = False,
        stream_to_general: bool = False,
        show_reasoning: bool = False,
        buffer_intermediate_messages: bool = False,
    ) -> None:
        self._executor = executor
        self._max_step = max_step
        self._show_tool_use = show_tool_use
        self._show_tool_call_result = show_tool_call_result
        self._stream_to_general = stream_to_general
        self._show_reasoning = show_reasoning
        self._buffer_intermediate_messages = buffer_intermediate_messages

    @property
    def executor(self) -> NativeExecutorAdapter:
        """Return the executor whose visible output this bridge projects."""

        return self._executor

    def stream(self) -> AsyncGenerator[MessageChain | None, None]:
        """Open the existing visible-output stream for one Native execution."""

        return _stream_native_agent_output(
            self._executor,
            max_step=self._max_step,
            show_tool_use=self._show_tool_use,
            show_tool_call_result=self._show_tool_call_result,
            stream_to_general=self._stream_to_general,
            show_reasoning=self._show_reasoning,
            buffer_intermediate_messages=self._buffer_intermediate_messages,
        )

    async def stream_live(
        self,
        tts_provider: TTSProvider | None = None,
    ) -> AsyncGenerator[MessageChain | None, None]:
        """Project this execution through the existing Live TTS output path."""

        async for chain in _stream_live_native_agent_output(self, tts_provider):
            yield chain


async def _stream_native_agent_output(
    executor: NativeExecutorAdapter,
    max_step: int = 30,
    show_tool_use: bool = True,
    show_tool_call_result: bool = False,
    stream_to_general: bool = False,
    show_reasoning: bool = False,
    buffer_intermediate_messages: bool = False,
) -> AsyncGenerator[MessageChain | None, None]:
    astr_event = executor.run_context.context.event
    tool_name_by_call_id: dict[str, str] = {}
    buffered_llm_chains: list[MessageChain] = []
    can_buffer_llm_result = _should_buffer_llm_result(
        buffer_intermediate_messages,
        stream_to_general,
        executor,
    )
    loop = NativeExecutionLoop(executor, max_step=max_step)
    try:
        async for resp in loop.stream():
            if resp.kind == "tool_call_result":
                msg_chain = _require_executor_stream_chain(resp)

                astr_event.trace.record(
                    "agent_tool_result",
                    tool_result=msg_chain.get_plain_text(
                        with_other_comps_mark=True
                    ),
                )

                if msg_chain.type == "tool_direct_result":
                    # tool_direct_result 用于标记 llm tool 需要直接发送给用户的内容
                    await _send_core_event_message(
                        astr_event,
                        msg_chain,
                        delivery=CoreOutputDelivery.FINAL,
                    )
                    continue
                if astr_event.get_platform_id() == "webchat":
                    await _send_core_event_message(
                        astr_event,
                        msg_chain,
                        delivery=CoreOutputDelivery.PROGRESS,
                    )
                elif show_tool_use and show_tool_call_result:
                    status_msg = _build_tool_result_status_message(
                        msg_chain, tool_name_by_call_id
                    )
                    await _send_core_event_message(
                        astr_event,
                        MessageChain(type="tool_call").message(status_msg),
                        delivery=CoreOutputDelivery.PROGRESS,
                    )
                # 对于其他情况，暂时先不处理
                continue
            elif resp.kind == "tool_call":
                if executor.streaming and show_tool_use:
                    # 向下游平台发送 "break" 分段信号（空 MessageChain，不携带数据）。
                    # 平台适配器收到后会关闭当前流式消息，并在后续文本到来时创建新消息。
                    # 仅在 show_tool_use 为 True 时才发送：此时紧接着会通过
                    # astr_event.send() 独立发送工具状态消息（如"🔨 调用工具: xxx"），
                    # 需要分段才能保证消息顺序正确。
                    # 若 show_tool_use 为 False，不会有独立消息插入，无需分段。
                    yield MessageChain(chain=[], type="break")

                tool_chain = _require_executor_stream_chain(resp)
                tool_info = _extract_chain_json_data(tool_chain)
                astr_event.trace.record(
                    "agent_tool_call",
                    tool_name=tool_info if tool_info else "unknown",
                )
                _record_tool_call_name(tool_info, tool_name_by_call_id)

                if astr_event.get_platform_name() == "webchat":
                    await _send_core_event_message(
                        astr_event,
                        tool_chain,
                        delivery=CoreOutputDelivery.PROGRESS,
                    )
                elif show_tool_use:
                    if show_tool_call_result and isinstance(tool_info, dict):
                        # Delay tool status notification until tool_call_result.
                        continue
                    chain = MessageChain(type="tool_call").message(
                        _build_tool_call_status_message(tool_info)
                    )
                    await _send_core_event_message(
                        astr_event,
                        chain,
                        delivery=CoreOutputDelivery.PROGRESS,
                    )
                continue
            elif resp.kind == "llm_result":
                chain = _require_executor_stream_chain(resp)
                if chain.type == "reasoning":
                    # For non-streaming mode, we handle reasoning in astrbot/core/astr_agent_hooks.py.
                    # For streaming mode, we yield content immediately when received a reasoning chunk but not in here, see below.
                    continue

            if stream_to_general and resp.kind == "streaming_delta":
                continue

            if stream_to_general or not executor.streaming:
                if can_buffer_llm_result and resp.kind == "llm_result":
                    buffered_llm_chains.append(_require_executor_stream_chain(resp))
                    continue

                content_typ = (
                    ResultContentType.LLM_RESULT
                    if resp.kind == "llm_result"
                    else ResultContentType.GENERAL_RESULT
                )
                astr_event.set_result(
                    MessageEventResult(
                        chain=_require_executor_stream_chain(resp).chain,
                        result_content_type=content_typ,
                    ),
                )
                yield _require_executor_stream_chain(resp)
                astr_event.clear_result()
            elif resp.kind == "streaming_delta":
                chain = _require_executor_stream_chain(resp)
                if chain.type == "reasoning" and not show_reasoning:
                    # display the reasoning content only when configured
                    continue
                yield chain

        if loop.aborted:
            if can_buffer_llm_result:
                merged_chain = _merge_buffered_llm_chains(buffered_llm_chains)
                if merged_chain:
                    astr_event.set_result(
                        MessageEventResult(
                            chain=merged_chain.chain,
                            result_content_type=ResultContentType.LLM_RESULT,
                        ),
                    )
                    yield merged_chain
                    astr_event.clear_result()
            astr_event.set_extra("agent_user_aborted", True)
            astr_event.set_extra("agent_stop_requested", False)
            executor.cancel(metadata={"reason": "agent_aborted"})
            return

        if can_buffer_llm_result and executor.done():
            merged_chain = _merge_buffered_llm_chains(buffered_llm_chains)
            if merged_chain:
                astr_event.set_result(
                    MessageEventResult(
                        chain=merged_chain.chain,
                        result_content_type=ResultContentType.LLM_RESULT,
                    ),
                )
                yield merged_chain
                astr_event.clear_result()

        if executor.done():
            # send agent stats to webchat
            if astr_event.get_platform_name() == "webchat":
                await _send_core_event_message(
                    astr_event,
                    MessageChain(
                        type="agent_stats",
                        chain=[Json(data=executor.stats.to_dict())],
                    ),
                    delivery=CoreOutputDelivery.PROGRESS,
                )

    except TurnDeadlineExceeded:
        executor.cancel(metadata={"reason": "deadline_exceeded"})
        raise
    except asyncio.CancelledError:
        executor.cancel(
            metadata={
                "reason": _cancellation_reason(
                    astr_event,
                    fallback="task_cancelled",
                )
            },
        )
        raise
    except Exception as e:
        logger.error(traceback.format_exc())

        custom_error_message = extract_persona_custom_error_message_from_event(
            astr_event
        )
        if custom_error_message:
            err_msg = custom_error_message
        else:
            err_msg = (
                f"Error occurred during AI execution.\n"
                f"Error Type: {type(e).__name__}\n"
                f"Error Message: {str(e)}"
            )

        error_llm_response = LLMResponse(
            role="err",
            completion_text=err_msg,
        )
        try:
            await executor.notify_agent_done(error_llm_response)
        except Exception:
            logger.exception("Error in on_agent_done hook")

        if executor.streaming:
            yield MessageChain().message(err_msg)
        else:
            astr_event.set_result(MessageEventResult().message(err_msg))
        executor.fail(
            metadata={
                "error_type": type(e).__name__,
                "error": str(e)[:2000],
            },
        )
        return


async def run_agent(
    executor: NativeExecutorAdapter,
    max_step: int = 30,
    show_tool_use: bool = True,
    show_tool_call_result: bool = False,
    stream_to_general: bool = False,
    show_reasoning: bool = False,
    buffer_intermediate_messages: bool = False,
) -> AsyncGenerator[MessageChain | None, None]:
    """Compatibility entry for the Native visible-output bridge."""

    bridge = NativeExecutionOutputBridge(
        executor,
        max_step=max_step,
        show_tool_use=show_tool_use,
        show_tool_call_result=show_tool_call_result,
        stream_to_general=stream_to_general,
        show_reasoning=show_reasoning,
        buffer_intermediate_messages=buffer_intermediate_messages,
    )
    async for chain in bridge.stream():
        yield chain


async def run_live_agent(
    executor: NativeExecutorAdapter,
    tts_provider: TTSProvider | None = None,
    max_step: int = 30,
    show_tool_use: bool = True,
    show_tool_call_result: bool = False,
    show_reasoning: bool = False,
    buffer_intermediate_messages: bool = False,
) -> AsyncGenerator[MessageChain | None, None]:
    """Compatibility entry for the Native Live output bridge."""
    output_bridge = NativeExecutionOutputBridge(
        executor,
        max_step=max_step,
        show_tool_use=show_tool_use,
        show_tool_call_result=show_tool_call_result,
        stream_to_general=False,
        show_reasoning=show_reasoning,
        buffer_intermediate_messages=buffer_intermediate_messages,
    )
    async for chain in output_bridge.stream_live(tts_provider):
        yield chain


async def _stream_live_native_agent_output(
    output_bridge: NativeExecutionOutputBridge,
    tts_provider: TTSProvider | None = None,
) -> AsyncGenerator[MessageChain | None, None]:
    """Keep the existing Live TTS projection behind the output bridge."""

    executor = output_bridge.executor

    # 如果没有 TTS Provider，直接发送文本
    if not tts_provider:
        async for chain in output_bridge.stream():
            yield chain
        return

    support_stream = tts_provider.support_stream()
    if support_stream:
        logger.info("[Live Agent] 使用流式 TTS（原生支持 get_audio_stream）")
    else:
        logger.info(
            f"[Live Agent] 使用 TTS（{tts_provider.meta().type} "
            "使用 get_audio，将按句子分块生成音频）"
        )

    # 统计数据初始化
    tts_start_time = time.time()
    tts_first_frame_time = 0.0
    first_chunk_received = False

    # 创建队列
    text_queue: asyncio.Queue[str | None] = asyncio.Queue()
    # audio_queue stored bytes or (text, bytes)
    audio_queue: asyncio.Queue[bytes | tuple[str, bytes] | None] = asyncio.Queue()

    # 1. 启动 Agent Feeder 任务：负责运行 Agent 并将文本分句喂给 text_queue
    feeder_task = asyncio.create_task(
        _run_agent_feeder(
            output_bridge,
            text_queue,
        )
    )

    # 2. 启动 TTS 任务：负责从 text_queue 读取文本并生成音频到 audio_queue
    if support_stream:
        tts_task = asyncio.create_task(
            _safe_tts_stream_wrapper(tts_provider, text_queue, audio_queue)
        )
    else:
        tts_task = asyncio.create_task(
            _simulated_stream_tts(tts_provider, text_queue, audio_queue)
        )

    # 3. 主循环：从 audio_queue 读取音频并 yield
    try:
        while True:
            queue_item = await audio_queue.get()

            if queue_item is None:
                break

            text = None
            if isinstance(queue_item, tuple):
                text, audio_data = queue_item
            else:
                audio_data = queue_item

            if not first_chunk_received:
                # 记录首帧延迟（从开始处理到收到第一个音频块）
                tts_first_frame_time = time.time() - tts_start_time
                first_chunk_received = True

            # 将音频数据封装为 MessageChain
            import base64

            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            comps: list[BaseMessageComponent] = [Plain(audio_b64)]
            if text:
                comps.append(Json(data={"text": text}))
            chain = MessageChain(chain=comps, type="audio_chunk")
            yield chain

    except Exception as e:
        logger.error(f"[Live Agent] 运行时发生错误: {e}", exc_info=True)
    finally:
        # 清理任务
        if not feeder_task.done():
            feeder_task.cancel()
        if not tts_task.done():
            tts_task.cancel()

        # 确保队列被消费
        pass

    tts_end_time = time.time()

    # 发送 TTS 统计信息
    try:
        astr_event = executor.run_context.context.event
        if astr_event.get_platform_name() == "webchat":
            tts_duration = tts_end_time - tts_start_time
            await _send_core_event_message(
                astr_event,
                MessageChain(
                    type="tts_stats",
                    chain=[
                        Json(
                            data={
                                "tts_total_time": tts_duration,
                                "tts_first_frame_time": tts_first_frame_time,
                                "tts": tts_provider.meta().type,
                                "chat_model": executor.provider.get_model(),
                            }
                        )
                    ],
                ),
                delivery=CoreOutputDelivery.PROGRESS,
            )
    except Exception as e:
        logger.error(f"发送 TTS 统计信息失败: {e}")


async def _run_agent_feeder(
    output_bridge: NativeExecutionOutputBridge,
    text_queue: asyncio.Queue,
) -> None:
    """运行 Agent 并将文本输出分句放入队列"""
    buffer = ""
    try:
        async for chain in output_bridge.stream():
            if chain is None:
                continue

            # 提取文本
            text = chain.get_plain_text()
            if text:
                buffer += text

                # 分句逻辑：匹配标点符号
                # r"([.。!！?？\n]+)" 会保留分隔符
                parts = re.split(r"([.。!！?？\n]+)", buffer)

                if len(parts) > 1:
                    # 处理完整的句子
                    # range step 2 因为 split 后是 [text, delim, text, delim, ...]
                    temp_buffer = ""
                    for i in range(0, len(parts) - 1, 2):
                        sentence = parts[i]
                        delim = parts[i + 1]
                        full_sentence = sentence + delim
                        temp_buffer += full_sentence

                        if len(temp_buffer) >= 10:
                            if temp_buffer.strip():
                                logger.info(f"[Live Agent Feeder] 分句: {temp_buffer}")
                                await text_queue.put(temp_buffer)
                            temp_buffer = ""

                    # 更新 buffer 为剩余部分
                    buffer = temp_buffer + parts[-1]

        # 处理剩余 buffer
        if buffer.strip():
            await text_queue.put(buffer)

    except Exception as e:
        logger.error(f"[Live Agent Feeder] Error: {e}", exc_info=True)
    finally:
        # 发送结束信号
        await text_queue.put(None)


async def _safe_tts_stream_wrapper(
    tts_provider: TTSProvider,
    text_queue: asyncio.Queue[str | None],
    audio_queue: "asyncio.Queue[bytes | tuple[str, bytes] | None]",
) -> None:
    """包装原生流式 TTS 确保异常处理和队列关闭"""
    try:
        await tts_provider.get_audio_stream(text_queue, audio_queue)
    except Exception as e:
        logger.error(f"[Live TTS Stream] Error: {e}", exc_info=True)
    finally:
        await audio_queue.put(None)


async def _simulated_stream_tts(
    tts_provider: TTSProvider,
    text_queue: asyncio.Queue[str | None],
    audio_queue: "asyncio.Queue[bytes | tuple[str, bytes] | None]",
) -> None:
    """模拟流式 TTS 分句生成音频"""
    try:
        while True:
            text = await text_queue.get()
            if text is None:
                break

            try:
                audio_path = await tts_provider.get_audio(text)

                if audio_path:
                    with open(audio_path, "rb") as f:
                        audio_data = f.read()
                    await audio_queue.put((text, audio_data))
            except Exception as e:
                logger.error(
                    f"[Live TTS Simulated] Error processing text '{text[:20]}...': {e}"
                )
                # 继续处理下一句

    except Exception as e:
        logger.error(f"[Live TTS Simulated] Critical Error: {e}", exc_info=True)
    finally:
        await audio_queue.put(None)
