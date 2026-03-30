# src/core.py
# =========================
# Core v0: 最小可运行的 Core（串联 Adapter → Bus → Router → SessionWorkers）
# Core v0: Minimal runnable core (Adapter → Bus → Router → SessionWorkers)
# =========================

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING
from loguru import logger

from .input_bus import AsyncInputBus
from .session_router import SessionRouter, SessionInbox, SessionState
from .schemas.observation import (
    Observation,
    ObservationType,
    Actor,
    MessagePayload,
    AlertPayload,
)
from .adapters.interface.base import BaseAdapter
from .gate import DefaultGate
from .gate.types import GateAction, GateContext, GateHint
from .config_provider import GateConfigProvider
from .system_reflex import SystemReflexController, ReflexConfig
from .agent import AgentQueen
from .agent.types import AgentRequest
from .adapters.output import EgressHub

if TYPE_CHECKING:
    from .memory.service import MemoryService


@dataclass
class CoreMetrics:
    """Core 的简单 metrics 结构"""
    processed_total: int = 0
    processed_by_session: Dict[str, int] = field(default_factory=dict)
    errors_total: int = 0
    errors_by_session: Dict[str, int] = field(default_factory=dict)
    sessions_gc_total: int = 0
    sessions_gc_by_reason: Dict[str, int] = field(default_factory=dict)
    
    # Nociception metrics
    pain_total: int = 0
    pain_by_source: Dict[str, int] = field(default_factory=dict)
    pain_by_severity: Dict[str, int] = field(default_factory=dict)
    pain_by_session: Dict[str, int] = field(default_factory=dict)
    drops_overload_total: int = 0
    adapters_cooldown_total: int = 0
    fanout_skipped_total: int = 0
    bus_publish_fail_total: int = 0
    bus_publish_fail_by_reason: Dict[str, int] = field(default_factory=dict)
    bus_publish_fail_by_context: Dict[str, int] = field(default_factory=dict)

    def inc_processed(self, session_key: str) -> None:
        """增加该 session 的处理计数"""
        self.processed_total += 1
        self.processed_by_session[session_key] = self.processed_by_session.get(session_key, 0) + 1

    def inc_error(self, session_key: str) -> None:
        """增加该 session 的错误计数"""
        self.errors_total += 1
        self.errors_by_session[session_key] = self.errors_by_session.get(session_key, 0) + 1

    def inc_gc(self, reason: str) -> None:
        """增加 GC 计数"""
        self.sessions_gc_total += 1
        self.sessions_gc_by_reason[reason] = self.sessions_gc_by_reason.get(reason, 0) + 1

    def inc_bus_publish_fail(self, reason: str, context: str) -> None:
        """增加 bus publish 失败计数。"""
        self.bus_publish_fail_total += 1
        self.bus_publish_fail_by_reason[reason] = self.bus_publish_fail_by_reason.get(reason, 0) + 1
        self.bus_publish_fail_by_context[context] = self.bus_publish_fail_by_context.get(context, 0) + 1


class Core:
    """
    Core v0

    职责 / Responsibilities:
    - 创建并管理 AsyncInputBus 和 SessionRouter
    - 管理 Adapter 生命周期（start/stop）
    - 为每个活跃 session 启动一个 worker（串行消费 inbox）
    - 处理 system session（可选扇出 tick 给所有活跃 session）
    - 支持优雅 shutdown

    非职责 / Non-responsibilities (v0):
    - LLM/intent/memory/tools
    - Session idle 回收
    - 复杂决策逻辑
    """

    def __init__(
        self,
        *,
        bus_maxsize: int = 1000,
        inbox_maxsize: int = 256,
        system_session_key: str = "system",
        default_session_key: str = "default",
        message_routing: str = "user",
        enable_system_fanout: bool = False,
        enable_session_gc: bool = True,
        idle_ttl_seconds: float = 600,
        gc_sweep_interval_seconds: float = 30,
        min_sessions_to_gc: int = 1,
        gate: Optional[DefaultGate] = None,
        gate_config_provider: Optional[GateConfigProvider] = None,
        agent_queen: Optional[AgentQueen] = None,
        enable_memory: bool = True,
        memory_config_path: str = "config/memory.yaml",
        memory_service: Optional[MemoryService] = None,
        egress: Optional[EgressHub] = None,
        egress_timeout_seconds: float = 1.0,
        egress_queue_maxsize: int = 256,
    ) -> None:
        """
        参数：
        - bus_maxsize: 输入总线队列大小
        - inbox_maxsize: 每个 session inbox 队列大小
        - system_session_key: system session 的 key
        - default_session_key: 默认 session key
        - message_routing: "user" 或 "default"
        - enable_system_fanout: system session 是否扇出 tick 给所有活跃 session
        """
        # 创建 bus 和 router
        self.bus = AsyncInputBus(maxsize=bus_maxsize, drop_when_full=True)
        self.router = SessionRouter(
            self.bus,
            inbox_maxsize=inbox_maxsize,
            system_session_key=system_session_key,
            default_session_key=default_session_key,
            message_routing=message_routing,
        )

        self.system_session_key = system_session_key
        self.enable_system_fanout = enable_system_fanout

        # Gate（可注入）
        self.gate: DefaultGate = gate or DefaultGate()
        self.gate_config_provider: GateConfigProvider = (
            gate_config_provider or GateConfigProvider("config/gate.yaml")
        )
        self.system_reflex = SystemReflexController(
            self.gate_config_provider,
            config=ReflexConfig(),
            system_session_key=self.system_session_key,
        )

        # Agent orchestrator
        self.agent_queen = agent_queen or AgentQueen()
        self.memory_service = memory_service
        if self.memory_service is None and enable_memory:
            self.memory_service = self._init_default_memory_service(memory_config_path)
        self.egress = egress
        self.egress_timeout_seconds = egress_timeout_seconds
        if egress_queue_maxsize <= 0:
            raise ValueError("egress_queue_maxsize must be > 0")
        self.egress_queue_maxsize = egress_queue_maxsize
        self._egress_queue: Optional[asyncio.Queue[Observation]] = None
        self._egress_task: Optional[asyncio.Task] = None

        # Session GC 配置
        self.enable_session_gc = enable_session_gc
        self.idle_ttl_seconds = idle_ttl_seconds
        self.gc_sweep_interval_seconds = gc_sweep_interval_seconds
        self.min_sessions_to_gc = min_sessions_to_gc

        # Nociception 保护策略状态
        self.adapters_disabled_until: Dict[str, float] = {}
        self.fanout_disabled_until: float = 0.0
        self.drops_last: int = 0
        self.pain_timestamps_by_source: Dict[str, list] = {}  # source_key -> [ts1, ts2, ...]

        # Adapters
        self.adapters: List[BaseAdapter] = []

        # Session workers
        self._workers: Dict[str, asyncio.Task] = {}
        self._worker_stats: Dict[str, int] = {}  # session_key -> 处理计数

        # SessionState 管理
        self._states: Dict[str, SessionState] = {}

        # Metrics
        self.metrics = CoreMetrics()

        # Debug 记录（用于测试）
        self.processed_payloads: Dict[str, list] = {}  # session_key -> [i1, i2, ...]

        # 控制标志
        self._closing = False
        self._router_task: Optional[asyncio.Task] = None
        self._watcher_task: Optional[asyncio.Task] = None
        self._gc_task: Optional[asyncio.Task] = None

    # -------------------------
    # Adapter 管理
    # -------------------------

    def add_adapter(self, adapter: BaseAdapter) -> None:
        """添加 adapter（必须在 start 前调用）"""
        if self._router_task is not None:
            raise RuntimeError("Cannot add adapter after core started")
        self.adapters.append(adapter)

    # -------------------------
    # SessionState 管理
    # -------------------------

    def get_state(self, session_key: str) -> SessionState:
        """
        获取或创建 session 的 state。
        若不存在则新建，放入 _states 字典，并返回。
        """
        if session_key not in self._states:
            self._states[session_key] = SessionState(session_key=session_key)
        return self._states[session_key]

    # -------------------------
    # 主运行循环
    # -------------------------

    async def run_forever(self) -> None:
        """
        主入口：启动所有组件并运行直到收到取消信号。

        支持 Ctrl+C / task cancel 优雅退出。
        """
        try:
            await self._startup()
            # 等待 router task 结束（正常情况下不会结束，除非 bus close）
            if self._router_task:
                await self._router_task
        except asyncio.CancelledError:
            logger.info("Core received cancellation, shutting down gracefully...")
            raise
        except KeyboardInterrupt:
            logger.info("Core received KeyboardInterrupt, shutting down gracefully...")
        finally:
            await self._shutdown()

    async def _startup(self) -> None:
        """启动所有组件"""
        # logger.info("Core starting up...")

        # 1. 启动 adapters（同步）
        for adapter in self.adapters:
            try:
                adapter.start(self.bus)
                logger.info(f"Started adapter: {adapter.name}")
            except Exception as e:
                logger.error(f"Failed to start adapter {adapter.name}: {e}")

        # 2. 启动 router task
        self._router_task = asyncio.create_task(
            self.router.run(),
            name="router_task",
        )
        logger.info("Router task started")

        # 3. 启动 session watcher（监听新 session 并启动 worker）
        self._watcher_task = asyncio.create_task(
            self._watch_new_sessions(),
            name="session_watcher",
        )
        logger.info("Session watcher started")

        # 4. 启动 session GC loop（可选）
        if self.enable_session_gc:
            self._gc_task = asyncio.create_task(
                self._session_gc_loop(),
                name="session_gc_loop",
            )
            logger.info("Session GC loop started")

        # 5. 启动 egress loop（可选）
        if self.egress is not None:
            self._egress_queue = asyncio.Queue(maxsize=self.egress_queue_maxsize)
            self._egress_task = asyncio.create_task(
                self._egress_loop(),
                name="egress_loop",
            )
            logger.info("Egress loop started")

        logger.info("Core startup complete")

    async def _shutdown(self) -> None:
        """优雅关闭所有组件"""
        if self._closing:
            return
        self._closing = True

        logger.info("Core shutting down...")

        # 1. 停止 adapters（同步）
        for adapter in self.adapters:
            try:
                adapter.stop()
                logger.info(f"Stopped adapter: {adapter.name}")
            except Exception as e:
                logger.error(f"Error stopping adapter {adapter.name}: {e}")

        # 2. 关闭 bus（让 router 迭代自然结束）
        self.bus.close()
        logger.info("Bus closed")

        # 3. 取消所有 tasks
        tasks_to_cancel = []
        if self._router_task and not self._router_task.done():
            tasks_to_cancel.append(self._router_task)
        if self._watcher_task and not self._watcher_task.done():
            tasks_to_cancel.append(self._watcher_task)
        if self._gc_task and not self._gc_task.done():
            tasks_to_cancel.append(self._gc_task)
        if self._egress_task and not self._egress_task.done():
            tasks_to_cancel.append(self._egress_task)
        tasks_to_cancel.extend(
            task for task in self._workers.values() if not task.done()
        )

        for task in tasks_to_cancel:
            task.cancel()

        # 4. 等待所有 tasks 结束（允许异常）
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
            logger.info(f"Cancelled {len(tasks_to_cancel)} tasks")

        # 5. 关闭 memory（可选）
        if self.memory_service is not None:
            try:
                self.memory_service.close()
                logger.info("Memory service closed")
            except Exception as e:
                logger.warning(f"Error closing memory service: {e}")

        logger.info("Core shutdown complete")

    # -------------------------
    # Session worker 管理
    # -------------------------

    async def _watch_new_sessions(self) -> None:
        """
        轮询 router.list_active_sessions()，为新出现的 session 启动 worker。

        方案 A（推荐）：不改 Router，轮询检测。
        """
        while not self._closing:
            try:
                current_sessions = set(self.router.list_active_sessions())

                # 对所有活跃 session 都做 ensure，保证 GC 后可自动恢复 worker
                for session_key in current_sessions:
                    self._ensure_worker(session_key)

                # 轮询间隔（v0 使用 50ms）
                await asyncio.sleep(0.05)

            except asyncio.CancelledError:
                # logger.debug("Session watcher cancelled")
                raise
            except Exception as e:
                # logger.error(f"Error in session watcher: {e}")
                await asyncio.sleep(0.5)

    # -------------------------
    # Session GC
    # -------------------------

    async def _session_gc_loop(self) -> None:
        """周期性扫描并回收 idle session"""
        try:
            while not self._closing:
                await asyncio.sleep(self.gc_sweep_interval_seconds)
                await self._sweep_idle_sessions()
        except asyncio.CancelledError:
            logger.debug("Session GC loop cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in session GC loop: {e}")

    async def _sweep_idle_sessions(self) -> None:
        """扫描并回收 idle session（仅清理 worker/state）"""
        if not self._states:
            return

        if len(self._states) < self.min_sessions_to_gc:
            return

        idle_candidates: List[str] = []
        for session_key, state in list(self._states.items()):
            if session_key == self.system_session_key:
                continue

            idle_sec = state.idle_seconds()
            if idle_sec is None:
                continue

            if idle_sec > self.idle_ttl_seconds:
                idle_candidates.append(session_key)

        for session_key in idle_candidates:
            await self._gc_session(session_key, reason="idle")

    async def _gc_session(self, session_key: str, reason: str = "idle") -> None:
        """回收指定 session 的 worker/state（可选清理 debug）"""
        try:
            task = self._workers.get(session_key)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except asyncio.TimeoutError:
                    logger.warning(f"GC timeout waiting for worker: {session_key}")
                except asyncio.CancelledError:
                    # 允许正常取消
                    pass

            self._workers.pop(session_key, None)
            self._states.pop(session_key, None)
            self.processed_payloads.pop(session_key, None)
            self.router.remove_session(session_key)

            self.metrics.inc_gc(reason)
            logger.info(f"GC session={session_key} reason={reason}")
        except Exception as e:
            logger.error(f"Error GC session {session_key}: {e}")

    def _ensure_worker(self, session_key: str) -> None:
        """确保指定 session 有且仅有一个 worker"""
        if self._closing:
            return

        existing = self._workers.get(session_key)
        if existing is not None:
            if not existing.done():
                return
            # 旧 worker 已结束，允许重建
            self._workers.pop(session_key, None)

        task = asyncio.create_task(
            self._session_loop(session_key),
            name=f"worker_{session_key}",
        )
        self._workers[session_key] = task
        self._worker_stats[session_key] = 0
        # logger.info(f"Started worker for session: {session_key}")

    def _init_default_memory_service(self, memory_config_path: str) -> Optional["MemoryService"]:
        """默认初始化 memory service（失败降级，不影响 Core 启动）。
        
        使用超时控制避免 DB 连接阻塞 Core 启动（最多阻塞 3 秒）。
        """
        import concurrent.futures
        
        def _do_init():
            from .memory.config import MemoryConfigProvider
            from .memory.backends.relational import SQLAlchemyBackend
            from .memory.service import MemoryService

            cfg = MemoryConfigProvider(memory_config_path).snapshot()
            db_backend = SQLAlchemyBackend(
                cfg.database.dsn,
                pool_size=cfg.database.pool_size,
                max_overflow=cfg.database.max_overflow,
            )
            service = MemoryService(
                db_backend=db_backend,
                markdown_vault_path=cfg.vault.root_path,
                failed_events_max_in_memory=cfg.failure_queue.max_in_memory,
                failed_events_spill_batch_size=cfg.failure_queue.spill_batch_size,
                failed_events_dump_max_bytes=cfg.failure_queue.max_dump_file_size_mb * 1024 * 1024,
                failed_events_dump_backups=cfg.failure_queue.max_dump_backups,
                failed_events_max_retries=cfg.failure_queue.max_retries,
            )
            return service
        
        try:
            # 使用线程池 + 超时控制（避免阻塞 Core 启动）
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_init)
                service = future.result(timeout=3.0)  # 最多等待 3 秒
                # logger.info(f"Memory service auto-enabled from {memory_config_path}")
                return service
        except concurrent.futures.TimeoutError:
            # logger.warning(f"Memory auto-init timed out (>3s) from {memory_config_path}, degraded to no-memory mode")
            return None
        except Exception as e:
            # logger.warning(f"Memory auto-init failed from {memory_config_path}: {e}")
            return None

    async def _session_loop(self, session_key: str) -> None:
        """
        单个 session 的 worker 循环：串行消费 inbox。
        每处理一条 obs 都更新 state 和 metrics。

        包含详细的日志输出：
        - [WORKER:IN] obs 信息
        - [GATE:CTX] 上下文信息
        - [GATE:OUT] 决策输出
        - [DELIVER] 最终投递内容

        注意：不要 swallow CancelledError。
        """
        inbox = self.router.get_inbox(session_key)
        state = self.get_state(session_key)

        try:
            while not self._closing:
                obs = await inbox.get()
                
                # [WORKER:IN] 日志
                obs_info = {
                    "obs_id": obs.obs_id if hasattr(obs, 'obs_id') else "unknown",
                    "obs_type": obs.obs_type.value if hasattr(obs.obs_type, 'value') else str(obs.obs_type),
                    "session_key": obs.session_key,
                    "actor_id": obs.actor.actor_id if obs.actor else None,
                    "timestamp": obs.timestamp.isoformat() if obs.timestamp else None,
                }
                # logger.debug(f"[WORKER:IN] {json.dumps(obs_info, ensure_ascii=False)}")
                
                # 记录到 state 并更新 metrics
                state.record(obs)
                self.metrics.inc_processed(session_key)
                self._worker_stats[session_key] += 1

                # 在 worker 侧做异步出站：先入 history，再入输出池，保证一致性
                self._enqueue_egress(obs)

                # Gate 处理（进入业务处理前）
                self.gate_config_provider.reload_if_changed()
                gate_ctx = GateContext(
                    now=datetime.now(timezone.utc),
                    config=self.gate_config_provider.snapshot(),
                    system_session_key=self.system_session_key,
                    metrics=self.gate.metrics,
                    session_state=state,
                    system_health=None,
                )

                # [GATE:CTX] 日志 - 打印上下文信息（至少 overrides）
                ctx_info = {
                    "config_version": gate_ctx.config.version if hasattr(gate_ctx.config, 'version') else None,
                    "session_key": session_key,
                    "state_processed": state.processed_total if state else 0,
                }
                # logger.debug(f"[GATE:CTX] {json.dumps(ctx_info, ensure_ascii=False)}")

                outcome = self.gate.handle(obs, gate_ctx)

                # [GATE:OUT] 日志 - 打印决策信息（增强：包含 hint 信息）
                hint: Optional[GateHint] = outcome.decision.hint
                decision_info = {
                    "action": outcome.decision.action.value if hasattr(outcome.decision.action, 'value') else str(outcome.decision.action),
                    "scene": outcome.decision.scene.value if hasattr(outcome.decision.scene, 'value') else str(outcome.decision.scene),
                    "score": outcome.decision.score,
                    "model_tier": hint.model_tier if hint else None,
                    "response_policy": hint.response_policy if hint else None,
                    "budget": {
                        "level": hint.budget.budget_level if hint and hint.budget else None,
                        "time_ms": hint.budget.time_ms if hint and hint.budget else None,
                        "max_tokens": hint.budget.max_tokens if hint and hint.budget else None,
                        "evidence_allowed": hint.budget.evidence_allowed if hint and hint.budget else None,
                        "max_tool_calls": hint.budget.max_tool_calls if hint and hint.budget else None,
                    },
                    "reason_tags": hint.reason_tags if hint else [],
                    "emit_count": len(outcome.emit),
                    "ingest_count": len(outcome.ingest),
                }
                # logger.debug(f"[GATE:OUT] {json.dumps(decision_info, ensure_ascii=False)}")

                # 0) 将入站 observation 写入 memory（fail-open）
                if self.memory_service is not None and session_key != self.system_session_key:
                    try:
                        gate_result = {
                            "action": outcome.decision.action.value if hasattr(outcome.decision.action, "value") else str(outcome.decision.action),
                            "scene": outcome.decision.scene.value if hasattr(outcome.decision.scene, "value") else str(outcome.decision.scene),
                            "score": outcome.decision.score,
                            "reasons": list(outcome.decision.reasons or []),
                        }
                        event = self.memory_service.append_event(
                            obs=obs,
                            session_key=session_key,
                            gate_result=gate_result,
                            meta=dict(obs.metadata or {}),
                        )
                        obs.metadata["memory_event_id"] = event.event_id
                    except Exception as e:
                        logger.warning(f"[{session_key}] Memory append_event failed: {e}")

                # 1) emit
                for emit_obs in outcome.emit:
                    self._publish_to_bus(emit_obs, context="gate_emit")

                # 2) ingest
                for ingest_obs in outcome.ingest:
                    self.gate.ingest(ingest_obs, outcome.decision)

                # 3) action 分支
                if outcome.decision.action in (GateAction.DROP, GateAction.SINK):
                    continue

                # 4) DELIVER → 进入后续处理，传递 GateDecision
                # [DELIVER] 日志
                deliver_info = {
                    "obs_id": obs.obs_id if hasattr(obs, 'obs_id') else "unknown",
                    "obs_type": obs.obs_type.value if hasattr(obs.obs_type, 'value') else str(obs.obs_type),
                    "session_key": session_key,
                    "action": outcome.decision.action.value if hasattr(outcome.decision.action, 'value') else str(outcome.decision.action),
                }
                # logger.debug(f"[DELIVER] {json.dumps(deliver_info, ensure_ascii=False)}")
                
                # 调用 _handle_observation 来处理（包括调用 Agent）
                await self._handle_observation(session_key, obs, state, outcome.decision)

        except asyncio.CancelledError:
            # logger.debug(f"Worker for session {session_key} cancelled")
            raise
        except Exception as e:
            # logger.error(f"Error in worker for session {session_key}: {e}")
            state.record_error()
            self.metrics.inc_error(session_key)

    def _enqueue_egress(self, obs: Observation) -> None:
        """Enqueue observation for async egress dispatch (non-blocking)."""
        if self.egress is None or self._egress_queue is None:
            return

        try:
            self._egress_queue.put_nowait(obs)
        except asyncio.QueueFull:
            logger.warning(f"[{obs.session_key}] Egress queue full, dropped observation")
        except Exception as e:
            logger.warning(f"[{obs.session_key}] Egress enqueue failed: {e}")

    def _publish_to_bus(self, obs: Observation, *, context: str) -> bool:
        """Publish to bus with failure metrics and warning logs."""
        result = self.bus.publish_nowait(obs)
        if result.ok:
            return True

        reason = str(result.reason or "unknown")
        self.metrics.inc_bus_publish_fail(reason, context)
        logger.warning(
            f"[{obs.session_key}] Bus publish failed context={context} reason={reason}"
        )
        return False

    async def _egress_loop(self) -> None:
        """Background loop that sends queued observations to output adapters."""
        if self.egress is None or self._egress_queue is None:
            return

        try:
            while not self._closing:
                obs = await self._egress_queue.get()
                try:
                    await asyncio.wait_for(
                        self.egress.dispatch(obs),
                        timeout=self.egress_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[{obs.session_key}] Egress dispatch timeout after {self.egress_timeout_seconds}s"
                    )
                except Exception as e:
                    logger.warning(f"[{obs.session_key}] Egress dispatch failed: {e}")
        except asyncio.CancelledError:
            raise

    # -------------------------
    # 处理逻辑（v0：可观测输出）
    # -------------------------

    async def _handle_observation(
        self, session_key: str, obs: Observation, state: SessionState, decision=None
    ) -> None:
        """
        处理单个 observation（v0 版本）。

        - system session：调用 _handle_system_observation
        - 其他 session：调用 _handle_user_observation
        - decision: GateDecision，用于 demo 可观测性

        v0 不引入 LLM/intent/memory。
        """
        if session_key == self.system_session_key:
            await self._handle_system_observation(obs, state)
        else:
            await self._handle_user_observation(session_key, obs, state, decision)

    async def _handle_system_observation(self, obs: Observation, state: SessionState) -> None:
        """
        处理 system session 的 observation。

        v0 行为：分支处理 ALERT / SCHEDULE / SYSTEM 等类型
        """
        if obs.obs_type == ObservationType.ALERT:
            await self._on_system_pain(obs)
        elif obs.obs_type == ObservationType.CONTROL:
            emits = self.system_reflex.handle_observation(obs, datetime.now(timezone.utc))
            for emit_obs in emits:
                self._publish_to_bus(emit_obs, context="system_reflex_emit")
        elif obs.obs_type == ObservationType.SCHEDULE:
            await self._on_system_tick(obs)
        else:
            logger.debug(f"[SYSTEM] obs_type={obs.obs_type.value}, source={obs.source_name}, actor={obs.actor.actor_id}")

    async def _on_system_pain(self, obs: Observation) -> None:
        """处理系统端接收到的痛觉（ALERT）"""
        import time
        from .nociception import extract_pain_key, extract_pain_severity

        source_key = extract_pain_key(obs)
        severity = extract_pain_severity(obs)

        # 更新痛觉 metrics
        self.metrics.pain_total += 1
        self.metrics.pain_by_source[source_key] = self.metrics.pain_by_source.get(source_key, 0) + 1
        self.metrics.pain_by_severity[severity] = self.metrics.pain_by_severity.get(severity, 0) + 1

        # 检查 payload.data 中的 affected_session
        if isinstance(obs.payload, AlertPayload) and "affected_session" in obs.payload.data:
            affected_sk = obs.payload.data["affected_session"]
            self.metrics.pain_by_session[affected_sk] = self.metrics.pain_by_session.get(affected_sk, 0) + 1

        # 滑窗频率判断
        now = time.time()
        if source_key not in self.pain_timestamps_by_source:
            self.pain_timestamps_by_source[source_key] = []

        timestamps = self.pain_timestamps_by_source[source_key]
        timestamps.append(now)

        # 清理窗口外的时间戳
        from .nociception import PAIN_WINDOW_SECONDS, PAIN_BURST_THRESHOLD
        cutoff = now - PAIN_WINDOW_SECONDS
        timestamps[:] = [ts for ts in timestamps if ts > cutoff]

        # 判断是否达到 burst 阈值
        if len(timestamps) >= PAIN_BURST_THRESHOLD:
            source_kind = source_key.split(":")[0]
            source_id = source_key.split(":")[1] if ":" in source_key else "unknown"

            if source_kind == "adapter":
                # 触发 adapter cooldown
                from .nociception import ADAPTER_COOLDOWN_SECONDS
                cooldown_until = now + ADAPTER_COOLDOWN_SECONDS
                self.adapters_disabled_until[source_id] = cooldown_until
                self.metrics.adapters_cooldown_total += 1
                logger.warning(f"Adapter cooldown triggered: {source_id} until {cooldown_until}")

                # 同时抑制 fanout
                from .nociception import FANOUT_SUPPRESS_SECONDS
                self.fanout_disabled_until = now + FANOUT_SUPPRESS_SECONDS

        logger.debug(f"Pain recorded: {source_key} severity={severity} count={len(timestamps)}")

    async def _on_system_tick(self, obs: Observation) -> None:
        """处理系统 tick（SCHEDULE）：drop 采样、fanout 抑制"""
        import time
        from .nociception import DROP_BURST_THRESHOLD, DROP_WINDOW_SECONDS, FANOUT_SUPPRESS_SECONDS, make_pain_alert

        # drop 采样
        drops_now = self.bus.dropped_total if hasattr(self.bus, "dropped_total") else 0
        drops_delta = drops_now - self.drops_last
        self.drops_last = drops_now

        if drops_delta >= DROP_BURST_THRESHOLD:
            now = time.time()
            self.metrics.drops_overload_total += 1
            self.fanout_disabled_until = now + FANOUT_SUPPRESS_SECONDS
            # logger.warning(f"Drop overload detected: {drops_delta} drops in window")

            # 生成 system pain
            pain = make_pain_alert(
                source_kind="system",
                source_id="drop_overload",
                severity="high",
                message=f"Dropped {drops_delta} observations in last window",
                session_key="system",
                data_extra={"drops_delta": drops_delta},
            )
            self._publish_to_bus(pain, context="system_pain_emit")

        # fanout 处理
        await self._fanout_tick(obs)

    async def _fanout_tick(self, obs: Observation) -> None:
        """
        将 tick 扇出给所有活跃的非 system session。

        注意：
        - 避免给 system 自己投递（防止循环）
        - 使用 bus.publish_nowait（同步投递）
        - 受 fanout_disabled_until 控制
        """
        import time
        from .nociception import FANOUT_SUPPRESS_SECONDS

        # 检查 fanout 是否被抑制
        now = time.time()
        if now < self.fanout_disabled_until:
            self.metrics.fanout_skipped_total += 1
            return

        active_sessions = self.router.list_active_sessions()

        for session_key in active_sessions:
            if session_key == self.system_session_key:
                continue

            # 创建一个轻量 tick observation
            from .schemas.observation import SourceKind
            tick_obs = Observation(
                obs_type=ObservationType.SYSTEM,
                source_name="core:fanout",
                source_kind=SourceKind.INTERNAL,
                session_key=session_key,
                actor=Actor(actor_id="system", actor_type="system"),
                payload=MessagePayload(
                    text="[tick]",
                    extra={"fanout_from": obs.obs_id},
                ),
            )

            self._publish_to_bus(tick_obs, context="fanout_tick")

    async def _handle_user_observation(
        self, session_key: str, obs: Observation, state: SessionState, decision=None
    ) -> None:
        """
        处理用户 session 的 observation

        - decision: GateDecision，用于决定是否调用 Agent
        
        流程：
        - 防止死循环：agent 回复的观察不再处理
        - 当 decision.action == DELIVER && obs.obs_type == MESSAGE 时，调用 Agent
        - 把 Agent 的返回值 emit 回流到 bus
        """
        # ============================================================
        # Step 1: 防止死循环
        # ============================================================
        source_name = obs.source_name or ""
        actor_id = obs.actor.actor_id if obs.actor else None
        if source_name.startswith("agent:") or actor_id == "agent":
            # logger.debug(f"[{session_key}] Skip agent observation to prevent loop")
            return
        
        # ============================================================
        # Step 2: 调用 Agent（当 DELIVER 且 MESSAGE）
        # ============================================================
        if (
            decision
            and decision.action == GateAction.DELIVER
            and obs.obs_type == ObservationType.MESSAGE
        ):
            memory_turn_id: Optional[str] = None
            if self.memory_service is not None:
                try:
                    input_event_id = (obs.metadata or {}).get("memory_event_id")
                    if input_event_id:
                        turn = self.memory_service.append_turn(
                            session_key=session_key,
                            input_event_id=input_event_id,
                            plan=None,
                            meta={
                                "gate_action": decision.action.value if hasattr(decision.action, "value") else str(decision.action),
                                "gate_scene": decision.scene.value if hasattr(decision.scene, "value") else str(decision.scene),
                            },
                        )
                        memory_turn_id = turn.turn_id
                    else:
                        logger.warning(f"[{session_key}] Skip memory turn: missing memory_event_id for obs={obs.obs_id}")
                except Exception as e:
                    logger.warning(f"[{session_key}] Memory append_turn failed: {e}")
            try:
                logger.debug(f"[{session_key}] Calling Agent for DELIVER decision")
                
                # 构造 AgentRequest
                agent_req = AgentRequest(
                    obs=obs,
                    gate_decision=decision,
                    session_state=state,
                    now=datetime.now(timezone.utc),
                )
                
                # 调用 agent orchestrator（async）
                agent_outcome = await self.agent_queen.handle(agent_req)
                
                # 将 agent 的 emit 投递回 bus
                for emit_obs in agent_outcome.emit:
                    emit_obs.metadata = dict(emit_obs.metadata or {})
                    if memory_turn_id:
                        emit_obs.metadata["memory_turn_id"] = memory_turn_id
                        emit_obs.metadata["memory_direction"] = "outbound"
                    # Speaker 产出先回灌 bus，后续由 worker 统一进入输出池
                    self._publish_to_bus(emit_obs, context="agent_emit")
                    logger.debug(f"[{session_key}] Agent emit: {emit_obs.source_name}")
                
                if self.memory_service is not None and memory_turn_id is not None:
                    try:
                        final_output_obs_id = (
                            agent_outcome.emit[0].obs_id if agent_outcome.emit else None
                        )
                        turn_status = "error" if agent_outcome.error else "ok"
                        self.memory_service.finish_turn(
                            turn_id=memory_turn_id,
                            final_output_obs_id=final_output_obs_id,
                            status=turn_status,
                            error=agent_outcome.error,
                        )
                    except Exception as e:
                        logger.warning(f"[{session_key}] Memory finish_turn failed: {e}")

                # 记录 trace 信息
                if agent_outcome.error:
                    logger.warning(f"[{session_key}] Agent error: {agent_outcome.error}")
                else:
                    elapsed_ms = agent_outcome.trace.get("elapsed_ms", 0)
                    logger.debug(f"[{session_key}] Agent completed in {elapsed_ms:.1f}ms")
            
            except Exception as e:
                logger.exception(f"[{session_key}] Agent exception: {e}")
                if self.memory_service is not None and memory_turn_id is not None:
                    try:
                        self.memory_service.finish_turn(
                            turn_id=memory_turn_id,
                            status="error",
                            error=str(e),
                        )
                    except Exception as close_err:
                        logger.warning(f"[{session_key}] Memory finish_turn(exception) failed: {close_err}")
        
        # ============================================================
        # Step 3: Debug 记录与日志（兼容旧逻辑）
        # ============================================================
        # Debug 记录：若 payload 是 dict 且含 "i" 字段
        if isinstance(obs.payload, dict) and "i" in obs.payload:
            if session_key not in self.processed_payloads:
                self.processed_payloads[session_key] = []
            self.processed_payloads[session_key].append(obs.payload["i"])

        # 摘要 payload（避免打印超大内容）
        payload_summary = self._shrink_payload(obs.payload)
        idle_sec = state.idle_seconds()
        idle_str = f", idle={idle_sec:.1f}s" if idle_sec is not None else ""

        # logger.debug(
        # f"[{session_key}] obs_type={obs.obs_type.value}, "
        # f"actor={obs.actor.actor_id}, "
        # f"processed={state.processed_total}"
        # f"{idle_str}, "
        # f"payload={payload_summary}"
        # )

    def _shrink_payload(self, payload, max_len: int = 160) -> str:
        """将 payload 截断为最多 max_len 字符，避免日志爆炸"""
        payload_str = self._summarize_payload_simple(payload)
        if len(payload_str) > max_len:
            return payload_str[:max_len - 3] + "..."
        return payload_str

    def _summarize_payload_simple(self, payload) -> str:
        """简单摘要 payload"""
        from .schemas.observation import MessagePayload, SchedulePayload, AlertPayload

        if isinstance(payload, MessagePayload):
            text = payload.text or ""
            return f'text="{text}"'
        elif isinstance(payload, SchedulePayload):
            return f"schedule_id={payload.schedule_id}"
        elif isinstance(payload, AlertPayload):
            return f"alert_type={payload.alert_type}"
        elif isinstance(payload, dict):
            return f"dict{payload}"

        return f"{type(payload).__name__}"

    def _summarize_payload(self, obs: Observation) -> str:
        """生成 payload 摘要（v0 简单版本，兼容旧代码）"""
        return self._summarize_payload_simple(obs.payload)

    # -------------------------
    # 状态查询（可选，用于测试/调试）
    # -------------------------

    def get_worker_stats(self) -> Dict[str, int]:
        """返回每个 session 的处理计数（用于测试验证）"""
        return self._worker_stats.copy()

    @property
    def active_sessions(self) -> List[str]:
        """返回当前活跃的 session 列表"""
        return self.router.list_active_sessions()
    async def shutdown(self) -> None:
        await self._shutdown()

