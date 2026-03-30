# AG99 项目模块深潜文档（原 MK2）

> 说明：历史名称为 MK2，本文档标题保留以对齐旧资料。

面向对象：需要维护、排障、扩展本仓库的工程同学。  
基线时间：2026-02-23。

## 1. 全局视角

系统当前主链路：

`Adapter -> Observation -> InputBus -> SessionRouter -> SessionWorker -> Gate -> AgentQueen -> emit 回流 Bus -> Egress`

分层职责：

1. Gate：快速规则决策（DROP/SINK/DELIVER + GateHint 预算）
2. Agent：对 DELIVER 的用户消息做编排处理
3. SystemReflex：处理 system CONTROL，执行受控 overrides
4. Memory：事件/轮次持久化（fail-open）

## 2. 运行时主链路（逐跳）

### 2.1 Adapter 输入层

目录：`src/adapters/`

1. `TextInputAdapter`：文本输入注入
2. `TimerTickAdapter`：周期性 system tick
3. `CliInputAdapter`：实验输入适配器（当前不作为主线基线）

所有输入最终形成 `Observation` 并调用 `bus.publish_nowait()`。

### 2.2 InputBus

文件：`src/input_bus.py`

1. 非阻塞发布：`publish_nowait(obs)`
2. Router 异步消费：`async for obs in bus`
3. 满队列策略：drop（记录 `dropped_total`）

### 2.3 SessionRouter

文件：`src/session_router.py`

1. 按 `session_key` 分流到 `SessionInbox`
2. 每个 session 一个 FIFO inbox
3. inbox 满时 drop newest
4. 提供 `list_active_sessions()` 供 Core watcher 拉起 worker

### 2.4 Core Worker

文件：`src/core.py`（`_session_loop`）

每条 observation 的处理顺序：

1. `await inbox.get()`
2. `state.record(obs)` + metrics
3. `_enqueue_egress(obs)`（异步输出队列）
4. `gate_config_provider.reload_if_changed()`
5. `gate.handle(obs, gate_ctx)`
6. 处理 `outcome.emit`（回流 bus）
7. 处理 `outcome.ingest`（入 gate pool）
8. 若 action 是 `DROP/SINK`，结束本条
9. 若 action 是 `DELIVER`，进入 `_handle_observation()`

### 2.5 Gate

目录：`src/gate/`

固定 pipeline：

1. `SceneInferencer`
2. `HardBypass`
3. `FeatureExtractor`
4. `ScoringStage`
5. `Deduplicator`
6. `PolicyMapper`
7. `FinalizeStage`

输出契约：

1. `GateDecision`：action/scene/score/reasons/hint
2. `GateOutcome`：decision + emit + ingest

### 2.6 AgentQueen

文件：`src/agent/queen.py`

当前编排固定为：

1. PoolSelector（rule / llm / hybrid）
2. ContextBuilder（默认 recent_obs）
3. PoolRouter（默认回退 chat pool）
4. Aggregator（默认 `DraftAggregator`）
5. Speaker（输出 `agent:speaker` MESSAGE）

异常策略：每阶段都有 fallback，不让整条链路沉默。

### 2.7 回流与防循环

文件：`src/core.py::_handle_user_observation`

遇到以下 observation 直接跳过 agent 调用，避免自激活：

1. `source_name.startswith("agent:")`
2. `actor_id == "agent"`

### 2.8 SystemReflex

目录：`src/system_reflex/`

当前已实现：

1. 接收 `CONTROL(kind=tuning_suggestion)`
2. 白名单过滤 overrides（默认仅 `force_low_model`）
3. cooldown 防抖
4. TTL 到期自动回滚
5. 通过 CONTROL 广播 `tuning_applied` / `system_mode_changed`

### 2.9 Memory

目录：`src/memory/`

Core 集成点：

1. 入站 observation 在 gate 后写 Event
2. `DELIVER + MESSAGE` 且存在 event_id 时创建 Turn
3. Agent 返回后调用 `finish_turn`
4. 任一 memory 异常都 fail-open，不阻断回复

### 2.10 Egress

目录：`src/adapters/output/`

1. Worker 只负责入队（不 await 外部输出）
2. `_egress_loop` 后台消费并 `EgressHub.dispatch(obs)`
3. `EgressHub` 支持按 `session_key` 路由 adapter

## 3. 关键数据契约

### 3.1 Observation

文件：`src/schemas/observation.py`

这是跨层唯一事件协议，关键字段：

1. `obs_type`
2. `session_key`
3. `actor`
4. `source_name` / `source_kind`
5. `payload`
6. `metadata`

### 3.2 SessionState

文件：`src/session_router.py`

是运行态轻量状态，不是长期 memory，包含：

1. `processed_total` / `error_total`
2. `last_active_at`
3. `recent_obs`（`deque(maxlen=20)`）

### 3.3 GateDecision + GateHint

文件：`src/gate/types.py`

1. Action：`DROP/SINK/DELIVER`
2. Scene：`DIALOGUE/GROUP/SYSTEM/TOOL_CALL/TOOL_RESULT/ALERT/UNKNOWN`
3. GateHint：模型层级、响应策略、预算约束

### 3.4 AgentRequest + AgentOutcome

文件：`src/agent/types.py`

1. `AgentRequest`：`obs + gate_decision + session_state + now (+gate_hint)`
2. `AgentOutcome`：`emit + trace + error`

## 4. 配置体系

### 4.1 Gate

1. 文件：`config/gate.yaml`
2. 入口：`src/config_provider.py::GateConfigProvider`
3. 支持：快照替换、stamp/hash 变化检测、运行时 override

### 4.2 Agent

1. 文件：`config/agent/agent.yaml`
2. PoolSelector 默认项：`default -> hybrid`
3. pool_selector 细化配置：`config/agent/pool_selector/default.yaml`

### 4.3 LLM

1. 文件：`config/llm.yaml`
2. `LLMProvider.call()` 为同步调用
3. `LLMPoolSelector.select()` 通过 `asyncio.to_thread(...)` 调用 provider，避免阻塞事件循环

### 4.4 Memory

1. 文件：`config/memory.yaml`
2. 控制关系库、vault、失败队列参数

## 5. 并发与阻塞点

高频 await 点：

1. `Core._session_loop -> await inbox.get()`
2. `Core._handle_user_observation -> await agent_queen.handle(req)`
3. `Core._egress_loop -> await egress.dispatch(obs)`
4. `LLMPoolSelector.select -> await asyncio.to_thread(provider.call, ...)`

注意：单个 session 是串行语义，慢请求会阻塞该 session 后续消息。

## 6. 测试映射（当前文件）

1. Core/Router/GC：`tests/test_core_*.py`, `tests/test_session_*.py`
2. Gate：`tests/test_gate_*.py`
3. Agent：`tests/test_agent_phase0.py`, `tests/test_agent_hybrid_planner_phase1.py`
4. LLM：`tests/test_llm_*.py`
5. Memory：`tests/test_memory_*.py`
6. SystemReflex/Nociception：`tests/test_system_reflex_*.py`, `tests/test_nociception_v0.py`

## 7. 当前已知边界

1. 默认 pool 路由实际可用池为 `chat`，`code/plan/creative` 默认回退到 chat（除非注入自定义 pool）。
2. `docs/demo_e2e.md` 对应脚本链路是实验态，不纳入当前稳定基线。

## 8. 扩展建议（低风险顺序）

1. 先扩 Pool，再扩 Planner：避免 Planner 输出无可执行池。
2. 新增 Gate 规则优先通过配置表达，再考虑代码分支。
3. 新增 provider 时同步补 integration 测试和超时策略。
4. 任何跨层新字段优先挂到 `Observation.metadata`，避免平行协议。

