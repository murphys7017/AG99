# AG99 设计决策（ADR，原 MK2）

> 说明：历史名称为 MK2，ADR 标题保留以对齐旧记录。

最后更新：2026-02-23  
定位：只记录“当前仍有效且已落地”的决策。

## ADR-001：统一事件协议使用 `Observation`

状态：Accepted

决策：

1. 跨模块统一使用 `Observation`。
2. 状态变化通过事件流传播，不走隐藏 side-channel。

实现锚点：`src/schemas/observation.py`

## ADR-002：并发模型采用“会话内串行，会话间并发”

状态：Accepted

决策：

1. Core 为每个 `session_key` 维护一个 worker task。
2. `SessionState` 只在该 worker 上串行修改。

实现锚点：`src/core.py`, `src/session_router.py`

## ADR-003：Gate 保持同步、确定性、无 LLM

状态：Accepted

决策：

1. Gate pipeline 只做快速规则决策。
2. 输出固定为 `GateOutcome(decision, emit, ingest)`。
3. Gate 不直接调用 Agent。

实现锚点：`src/gate/pipeline/`, `src/gate/gate.py`

## ADR-004：Gate 配置热更新采用“快照替换 + stamp/hash 检测”

状态：Accepted

决策：

1. `GateConfigProvider` 维护当前配置快照引用。
2. `reload_if_changed()` 使用 `mtime_ns + size`，必要时补 hash。
3. reload 失败保留旧快照（fail-safe）。

实现锚点：`src/config_provider.py`

## ADR-005：Gate 动作语义固定为 `DROP/SINK/DELIVER`

状态：Accepted

决策：

1. `DROP/SINK`：不进入后续业务处理。
2. `DELIVER`：进入 `_handle_observation`，允许触发 Agent。
3. `emit` 与 `ingest` 分离，先 emit 后 ingest。

实现锚点：`src/core.py::_session_loop`, `src/gate/types.py`

## ADR-006：Agent 回流消息必须防循环

状态：Accepted

决策：

1. `source_name` 以 `agent:` 开头的消息不再触发 Agent。
2. `actor_id == agent` 的消息不再触发 Agent。

实现锚点：`src/core.py::_handle_user_observation`

## ADR-007：LLM 同步调用必须在线程中执行

状态：Accepted

决策：

1. provider 客户端当前是同步 HTTP 调用。
2. 在异步链路中使用 `asyncio.to_thread(...)` 包装调用。

实现锚点：`src/agent/pool_selector/llm_pool_selector.py::LLMPoolSelector.select`

## ADR-008：系统调节入口统一在 SystemReflex（白名单+TTL）

状态：Accepted

决策：

1. Agent 只能发 `CONTROL(kind=tuning_suggestion)` 提建议。
2. SystemReflex 白名单过滤 + cooldown + TTL 回滚。
3. 实际覆盖通过 `GateConfigProvider.update_overrides()` 执行。

实现锚点：`src/system_reflex/controller.py`

## ADR-009：Memory fail-open，不阻断主链路

状态：Accepted

决策：

1. Event/Turn 写入失败只记录 warning。
2. Core 继续执行 Gate/Agent/egress。

实现锚点：`src/core.py`, `src/memory/service.py`

## ADR-010：输出链路采用“worker 入队 + 后台 egress loop”

状态：Accepted

决策：

1. worker 不 await 外部输出。
2. 由 `_egress_loop` 异步发送。
3. 输出超时/失败 fail-open。

实现锚点：`src/core.py::_enqueue_egress`, `src/core.py::_egress_loop`

## ADR-011：Output Adapter 支持按 `session_key` 路由

状态：Accepted

决策：

1. `EgressHub` 可配置 session 专属 adapter。
2. 命中专属路由时优先 session adapter，否则使用默认 adapter。

实现锚点：`src/adapters/output/hub.py`

## ADR-012：MESSAGE 标准路径默认 DELIVER（含用户对话安全阀）

状态：Accepted

决策：

1. `PolicyMapper` 中用户对话消息默认 `DELIVER`（除非上游硬 DROP）。
2. 通过 GateHint 输出预算与策略约束。

实现锚点：`src/gate/pipeline/policy.py`

## 待决议题

1. 是否引入更多可执行 pool（code/plan/creative）替代当前 chat 回退。
2. 是否将 provider 层改为原生 async HTTP 客户端。
3. 是否拆分 `core.py` 进一步降低单文件复杂度。
4. 是否落地统一 trace_id 跨 Adapter/Gate/Agent/Memory。


