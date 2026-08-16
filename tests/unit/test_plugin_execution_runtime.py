import asyncio
from types import SimpleNamespace

import pytest

from astrbot.core.interaction.conversation_history import (
    CONVERSATION_COMMITTED_TURN_ID_EXTRA,
    commit_interaction_conversation_turn,
)
from astrbot.core.interaction.delayed_plugin_delivery import (
    DelayedPluginDeliveryCoordinator,
)
from astrbot.core.interaction.plugin_artifact_delivery import (
    PluginArtifactDeliveryCoordinator,
)
from astrbot.core.interaction.plugin_execution_runtime import (
    PluginExecutionRuntime,
    PluginModuleDrainingError,
    PluginModuleDrainTimeoutError,
    get_active_plugin_branch_event,
)
from astrbot.core.interaction.plugin_execution_types import (
    PluginArtifactKind,
    PluginBranchResult,
    PluginDeliveryDisposition,
    PluginDeliveryKey,
    PluginGateResolution,
    PluginJobState,
    PluginOutputArtifact,
)
from astrbot.core.interaction.turn_coordinator import (
    InteractionTurnCoordinator,
    PluginJobLaunch,
)
from astrbot.core.interaction.turn_state import (
    record_interaction_turn_visible_message_fingerprint,
)
from astrbot.core.interaction.types import InteractionRouteMode
from astrbot.core.interaction.visible_message_fingerprint import (
    fingerprint_visible_message,
)
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest


class _CoordinatorEvent:
    session_id = "session-1"

    def __init__(self) -> None:
        self._extras = {"_turn_id": "turn-1"}
        self.message_obj = SimpleNamespace(group=None)
        self.platform_meta = SimpleNamespace(
            support_proactive_message=True,
            support_personal_runtime=True,
        )

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self._extras[key] = value

    def get_platform_id(self) -> str:
        return "test"

    def get_platform_name(self) -> str:
        return "test"

    def get_message_type(self) -> MessageType:
        return MessageType.FRIEND_MESSAGE

    def get_session_id(self) -> str:
        return self.session_id

    def get_group_id(self):
        return None


@pytest.mark.asyncio
async def test_expired_plugin_job_detaches_without_cancelling_runtime_task():
    runtime = PluginExecutionRuntime()
    result = PluginBranchResult()
    started = asyncio.Event()
    release_job = asyncio.Event()
    released_leases: list[bool] = []

    async def run_job(_publish_gate):
        started.set()
        await release_job.wait()

    async def release_leases():
        released_leases.append(True)

    job = runtime.start(
        branch_event=SimpleNamespace(name="branch"),
        result=result,
        run_job=run_job,
        release_leases=release_leases,
    )
    await started.wait()

    gate = await job.wait_for_gate(asyncio.get_running_loop().time() + 0.01)

    assert gate is PluginGateResolution.EXPIRED
    assert job.task is not None and not job.task.done()
    diagnostics = runtime.snapshot_diagnostics()
    assert diagnostics.active_plugin_job_count == 1
    assert diagnostics.detached_plugin_job_count == 1

    release_job.set()
    completed = await job.wait_completed()

    assert completed.job_state is PluginJobState.COMPLETED
    assert released_leases == [True]
    diagnostics = runtime.snapshot_diagnostics()
    assert diagnostics.active_plugin_job_count == 0
    assert diagnostics.background_job_completed_count == 1
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_plugin_module_draining_waits_for_active_lease():
    runtime = PluginExecutionRuntime()
    lease = runtime.acquire_module_lease(["data.plugins.demo.main"])
    draining = asyncio.create_task(
        runtime.begin_module_draining("data.plugins.demo.main")
    )
    await asyncio.sleep(0)

    assert not draining.done()
    with pytest.raises(PluginModuleDrainingError):
        runtime.acquire_module_lease(["data.plugins.demo.main"])

    await lease.release()
    await draining
    runtime.end_module_draining("data.plugins.demo.main")

    replacement_lease = runtime.acquire_module_lease(["data.plugins.demo.main"])
    await replacement_lease.release()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_plugin_module_drain_timeout_aborts_management_without_releasing_job():
    runtime = PluginExecutionRuntime()
    module_path = "data.plugins.demo.main"
    lease = runtime.acquire_module_lease([module_path])
    started = asyncio.Event()
    release_job = asyncio.Event()

    async def run_job(_publish_gate):
        started.set()
        await release_job.wait()

    job = runtime.start(
        branch_event=SimpleNamespace(name="branch"),
        result=PluginBranchResult(),
        run_job=run_job,
        release_leases=lease.release,
    )
    lease.bind_job(job.job_id)
    await started.wait()

    with pytest.raises(PluginModuleDrainTimeoutError) as caught:
        await runtime.begin_module_draining(module_path, timeout_seconds=0.01)

    error = caught.value
    assert error.module_path == module_path
    assert error.active_lease_count == 1
    assert error.active_job_ids == (job.job_id,)
    assert error.oldest_job_age_seconds >= 0.0
    assert job.task is not None and not job.task.done()

    replacement_lease = runtime.acquire_module_lease([module_path])
    await replacement_lease.release()

    release_job.set()
    await job.wait_completed()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_turn_coordinator_skips_draining_plugin_without_failing_turn():
    runtime = PluginExecutionRuntime()
    coordinator = InteractionTurnCoordinator(runtime)
    event = _CoordinatorEvent()
    route = SimpleNamespace(route_mode=InteractionRouteMode.PERSONA)
    plugin_called = False

    await runtime.begin_module_draining("data.plugins.demo.main")

    async def run_personal():
        return "personal reply"

    async def run_router():
        return route

    async def run_plugin(_publish_gate, _submit_provider_request):
        nonlocal plugin_called
        plugin_called = True

    turn = await coordinator.start(
        event,
        personal_factory=run_personal,
        router_factory=run_router,
        plugin_window_seconds=1.0,
        plugin_launch=PluginJobLaunch(
            branch_event=event,
            result=PluginBranchResult(),
            run_job=run_plugin,
            module_paths=("data.plugins.demo.main",),
        ),
    )
    control = await coordinator.resolve_control(turn)

    assert control.plugin_gate is PluginGateResolution.PASSED
    assert control.route is route
    assert turn.plugin_job is None
    assert not plugin_called
    assert event.get_extra("_interaction_plugin_launch_skipped_reason") == (
        "module_draining"
    )

    runtime.end_module_draining("data.plugins.demo.main")
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_delivery_ledger_reserves_each_artifact_once():
    runtime = PluginExecutionRuntime()
    key = PluginDeliveryKey("job-1", "handler-1", 0)
    await runtime.register_delivery(key)

    first, second = await asyncio.gather(
        runtime.reserve_delivery(key),
        runtime.reserve_delivery(key),
    )

    assert sorted((first, second)) == [False, True]
    assert await runtime.finish_delivery(
        key,
        PluginDeliveryDisposition.DELIVERED_INLINE,
    )
    assert not await runtime.finish_delivery(
        key,
        PluginDeliveryDisposition.DELIVERED_DELAYED,
    )
    assert (
        await runtime.get_delivery_disposition(key)
        is PluginDeliveryDisposition.DELIVERED_INLINE
    )
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_handled_gate_freezes_the_t1_artifact_snapshot():
    runtime = PluginExecutionRuntime()
    result = PluginBranchResult()
    first = PluginOutputArtifact(
        sequence=0,
        kind=PluginArtifactKind.DIRECT,
        message=MessageChain([Plain("first")]),
        mode="direct",
        finalize=True,
        handler_invocation_id="handler-1",
    )
    second = PluginOutputArtifact(
        sequence=1,
        kind=PluginArtifactKind.DIRECT,
        message=MessageChain([Plain("second")]),
        mode="direct",
        finalize=True,
        handler_invocation_id="handler-2",
    )

    async def run_job(publish_gate):
        result.output_artifacts.append(first)
        publish_gate(PluginGateResolution.HANDLED)
        result.output_artifacts.append(second)

    job = runtime.start(
        branch_event=SimpleNamespace(name="branch"),
        result=result,
        run_job=run_job,
    )
    await job.wait_completed()

    delivered: list[str] = []

    class OutputSink:
        async def capture_plugin_output(
            self,
            message,
            _event,
            *,
            mode,
            finalize,
            platform_extras=None,
        ):
            del mode, finalize, platform_extras
            delivered.append(message.get_plain_text())

    delivery = PluginArtifactDeliveryCoordinator(runtime, OutputSink())
    summary = await delivery.deliver_inline(
        _CoordinatorEvent(),
        result,
        claim_final_output=True,
    )

    assert result.t1_artifact_count == 1
    assert summary.delivered_artifact_count == 1
    assert delivered == ["first"]
    assert await runtime.get_delivery_disposition(second.delivery_key) is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_stopped_gate_keeps_later_same_handler_output_deliverable():
    runtime = PluginExecutionRuntime()
    result = PluginBranchResult()
    artifact = PluginOutputArtifact(
        sequence=0,
        kind=PluginArtifactKind.DIRECT,
        message=MessageChain([Plain("stopped result")]),
        mode="direct",
        finalize=True,
        handler_invocation_id="handler-1",
    )

    async def run_job(publish_gate):
        publish_gate(PluginGateResolution.STOPPED)
        result.output_artifacts.append(artifact)

    job = runtime.start(
        branch_event=SimpleNamespace(name="branch"),
        result=result,
        run_job=run_job,
    )
    await job.wait_completed()

    delivered: list[str] = []

    class OutputSink:
        async def capture_plugin_output(
            self,
            message,
            _event,
            *,
            mode,
            finalize,
            platform_extras=None,
        ):
            del mode, finalize, platform_extras
            delivered.append(message.get_plain_text())

    delivery = PluginArtifactDeliveryCoordinator(runtime, OutputSink())
    summary = await delivery.deliver_inline(
        _CoordinatorEvent(),
        result,
        claim_final_output=True,
    )

    assert result.t1_artifact_count is None
    assert summary.delivered_artifact_count == 1
    assert delivered == ["stopped result"]
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_turn_coordinator_starts_three_lines_and_bridges_provider_request():
    runtime = PluginExecutionRuntime()
    coordinator = InteractionTurnCoordinator(runtime)
    event = _CoordinatorEvent()
    personal_started = asyncio.Event()
    router_started = asyncio.Event()
    release_turn_tasks = asyncio.Event()
    plugin_result = PluginBranchResult()

    async def run_personal():
        personal_started.set()
        await release_turn_tasks.wait()

    async def run_router():
        router_started.set()
        await release_turn_tasks.wait()

    async def run_plugin(publish_gate, submit_provider_request):
        assert get_active_plugin_branch_event() is event
        publish_gate(PluginGateResolution.DELEGATED)
        await submit_provider_request(ProviderRequest(prompt="plugin request"))

    turn = await coordinator.start(
        event,
        personal_factory=run_personal,
        router_factory=run_router,
        plugin_window_seconds=1.0,
        plugin_launch=PluginJobLaunch(
            branch_event=event,
            result=plugin_result,
            run_job=run_plugin,
        ),
    )

    await asyncio.gather(personal_started.wait(), router_started.wait())
    control = await coordinator.resolve_control(turn)
    assert control.plugin_gate is PluginGateResolution.DELEGATED
    assert control.route is None
    command = await turn.receive_provider_request()
    assert command.request.prompt == "plugin request"
    command.complete()
    assert turn.plugin_job is not None
    await turn.plugin_job.wait_completed()
    assert get_active_plugin_branch_event() is None
    await asyncio.sleep(0)
    assert turn.personal_task.cancelled()

    release_turn_tasks.set()
    await asyncio.gather(
        turn.personal_task,
        turn.router_task,
        return_exceptions=True,
    )
    assert turn.router_task.cancelled()
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_plugin_runtime_failure_keeps_personal_and_router_running():
    runtime = PluginExecutionRuntime()
    coordinator = InteractionTurnCoordinator(runtime)
    event = _CoordinatorEvent()
    personal_started = asyncio.Event()
    router_started = asyncio.Event()
    release_personal = asyncio.Event()
    release_router = asyncio.Event()
    route = SimpleNamespace(route_mode=InteractionRouteMode.PERSONA)

    async def run_personal():
        personal_started.set()
        await release_personal.wait()

    async def run_router():
        router_started.set()
        await release_router.wait()
        return route

    async def run_plugin(_publish_gate, _submit_provider_request):
        raise RuntimeError("plugin runtime failed")

    turn = await coordinator.start(
        event,
        personal_factory=run_personal,
        router_factory=run_router,
        plugin_window_seconds=1.0,
        plugin_launch=PluginJobLaunch(
            branch_event=event,
            result=PluginBranchResult(),
            run_job=run_plugin,
        ),
    )

    await asyncio.gather(personal_started.wait(), router_started.wait())
    assert turn.plugin_job is not None
    await turn.plugin_job.wait_completed()
    control_task = asyncio.create_task(coordinator.resolve_control(turn))
    await asyncio.sleep(0)

    assert not control_task.done()
    assert not turn.personal_task.done()

    release_router.set()
    control = await control_task

    assert control.plugin_gate is PluginGateResolution.FAILED
    assert control.route is route
    assert not turn.personal_task.cancelled()

    release_personal.set()
    await turn.personal_task
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_delayed_media_duplicate_is_suppressed_before_t2_admission(
    monkeypatch,
    tmp_path,
):
    runtime = PluginExecutionRuntime()
    event = _CoordinatorEvent()
    message = MessageChain([Image.fromURL("https://example.com/result.png")])
    message_fingerprint = fingerprint_visible_message(message)
    record_interaction_turn_visible_message_fingerprint(
        event,
        message_fingerprint,
    )
    monkeypatch.chdir(tmp_path)
    assert fingerprint_visible_message(message) == message_fingerprint
    assert fingerprint_visible_message(
        MessageChain([Image.fromURL("https://example.com/other.png")])
    ) != message_fingerprint
    artifact = PluginOutputArtifact(
        sequence=0,
        kind=PluginArtifactKind.DIRECT,
        message=message,
        mode="direct",
        finalize=True,
        plugin_job_id="job-1",
        handler_invocation_id="handler-1",
    )
    result = PluginBranchResult(
        plugin_job_id="job-1",
        gate_resolution=PluginGateResolution.EXPIRED,
        output_artifacts=[artifact],
    )
    coordinator = DelayedPluginDeliveryCoordinator(runtime)
    context = SimpleNamespace(parent_event=event, parent_turn_id="turn-1")

    summary = await coordinator.deliver(context, result)

    assert summary.delivered_group_count == 0
    assert summary.suppressed_group_count == 1
    assert (
        await runtime.get_delivery_disposition(artifact.delivery_key)
        is PluginDeliveryDisposition.SUPPRESSED_DUPLICATE_VISIBLE_OUTPUT
    )
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_delegated_failure_delivers_semantic_media_with_flags():
    runtime = PluginExecutionRuntime()
    event = _CoordinatorEvent()
    message = MessageChain([Image.fromURL("https://example.com/result.png")])
    message.type = "plugin_result"
    message.use_markdown_ = True
    message.use_t2i_ = False
    artifact = PluginOutputArtifact(
        sequence=0,
        kind=PluginArtifactKind.SEMANTIC,
        message=message,
        mode="persona",
        finalize=True,
        plugin_job_id="job-1",
        handler_invocation_id="handler-1",
    )
    result = PluginBranchResult(
        plugin_job_id="job-1",
        gate_resolution=PluginGateResolution.DELEGATED,
        output_artifacts=[artifact],
    )
    assert not result.delayed_delivery_eligible
    result.record_delegated_t1_failure(RuntimeError("delegated core failed"))
    assert result.delayed_delivery_eligible
    delivered: list[MessageChain] = []
    profiles: list[str] = []

    class Middleware:
        async def handle_runtime_output(
            self,
            runtime_event,
            _turn,
            output,
            *,
            platform_extras=None,
        ):
            del platform_extras
            delivered.append(output)
            runtime_event.set_extra(
                CONVERSATION_COMMITTED_TURN_ID_EXTRA,
                "turn-1",
            )

    class Manager:
        async def submit_delayed_plugin_event(
            self,
            runtime_event,
            _config_id,
            _plugin_context,
            _runtime_config,
            handler,
            *,
            profile,
        ):
            profiles.append(profile)
            await handler(runtime_event, SimpleNamespace())
            return True

    context = SimpleNamespace(
        parent_event=event,
        parent_turn_id="turn-1",
        parent_conversation_id="conversation-1",
        resolve_parent_conversation_id=lambda: "conversation-1",
        config_id="default",
        plugin_context=SimpleNamespace(),
        runtime_config={},
        personal_runtime_manager=Manager(),
        middleware=Middleware(),
    )

    summary = await DelayedPluginDeliveryCoordinator(runtime).deliver(
        context,
        result,
    )

    assert summary.delivered_group_count == 1
    assert profiles == ["delayed_plugin_direct"]
    assert len(delivered) == 1
    assert delivered[0].type == "plugin_result"
    assert delivered[0].use_markdown_ is True
    assert delivered[0].use_t2i_ is False
    assert isinstance(delivered[0].chain[0], Image)
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_delayed_output_without_parent_conversation_skips_history():
    new_conversation_calls = 0

    class ConversationManager:
        async def get_curr_conversation_id(self, _umo):
            return None

        async def new_conversation(self, _umo, _platform_id):
            nonlocal new_conversation_calls
            new_conversation_calls += 1
            return "new-conversation"

    class Event(_CoordinatorEvent):
        unified_msg_origin = "test:FriendMessage:session-1"

        def get_extra(self, key=None, default=None):
            if key is None:
                return self._extras
            return self._extras.get(key, default)

    event = Event()
    event.set_extra("_interaction_fixed_conversation_id", None)
    committed = await commit_interaction_conversation_turn(
        event=event,
        plugin_context=SimpleNamespace(
            conversation_manager=ConversationManager(),
        ),
        turn_id="delayed-turn",
        turn_material={
            "turn_id": "delayed-turn",
            "source": "observation",
            "assistant_text": "late plugin result",
        },
    )

    assert committed is True
    assert new_conversation_calls == 0
    assert event.get_extra("_interaction_delayed_history_skipped_reason") == (
        "parent_conversation_unavailable"
    )
