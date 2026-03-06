# AG99 Agent 系统架构文档

**基线时间**: 2026-03-04  
**版本**: Phase 0 - Phase 1.2 (进行中)

---

## 概述

AG99 的 **Agent 系统**是一套从 Gate 决策后接收用户请求、进行编排处理、生成响应的完整链路。其设计是 **可预期、可演进、失败自降级**。

```
Gate.DELIVER(MESSAGE)
        ↓
   AgentQueen.handle(req: AgentRequest) -> AgentOutcome
        ↓
[PoolSelector → Context → Pool → Aggregator → Speaker]
        ↓
emit Observation(MESSAGE) → Egress
```

Phase 1.26 主链路：`CLI → PoolSelector → ContextBuilder → PoolRouter → ChatPool → PromptEngine → LLM → Speaker`

---

## 1. 数据流与契约

### 1.1 输入：AgentRequest

**来源**: [src/agent/types.py](../src/agent/types.py)

```python
@dataclass
class AgentRequest:
    obs: Observation                    # 原始用户消息
    gate_decision: GateDecision         # Gate 的决策结果
    session_state: SessionState         # 会话轻量状态
    now: datetime                       # 处理时刻
    gate_hint: Optional[GateHint] = None  # Gate 的预算与策略提示
```

**字段说明**:
- `obs`: 用户输入的原始 Observation（含 `session_key`, `payload.text`, 等）
- `gate_decision`: 包含 `action` (DELIVER/DROP/SINK), `scene`, `score`, `reasons`
- `session_state`: 包含 `recent_obs` (deque[Observation], 最近 20 条), `processed_total`, `error_total`, `last_active_at`
- `gate_hint`: 预算等级、模型层级、响应策略等建议

**来源链路**:
```
Core._handle_observation(obs, gate_decision, session_state)
  → AgentQueen.handle(AgentRequest) ✓
```

### 1.2 输出：AgentOutcome

**来源**: [src/agent/types.py](../src/agent/types.py)

```python
@dataclass
class AgentOutcome:
    emit: List[Observation]             # 回流消息列表（通常 1 条 MESSAGE）
    trace: Dict[str, Any]               # 全链路跟踪信息
    error: Optional[str] = None         # 链路错误摘要
```

**emit 中的 Observation**:
- `obs_type`: `ObservationType.MESSAGE`
- `source_name`: `"agent:speaker"`
- `source_kind`: `SourceKind.INTERNAL`
- `payload`: `MessagePayload(text=final_text)` 
- `metadata`: dict（包含 `task_type`, `pool_id`, `fallback` 等）

**trace 结构**:
```python
{
    "pool_selector_input_summary": {...}, # 输入摘要
    "pool_selector_summary": {...},       # 路由选择执行与结果
    "context_build_summary":  {...},      # 上下文构建统计
    "pool":                   {...},      # Pool 执行信息
    "aggregation":            {...},      # 聚合结果
    "speaker":                {...},      # Speaker 执行
    "fallback_triggered":     bool,       # 是否触发了任何 fallback
    "elapsed_ms":             float,      # 端到端耗时
    "error":                  str | None  # 链路级错误
}
```

---

## 2. 完整处理链路（Queen → Pool）

### 2.1 AgentQueen 核心流程

**文件**: [src/agent/queen.py](../src/agent/queen.py)

```python
async def handle(self, req: AgentRequest) -> AgentOutcome:
    # 1) 路由选择
    routing_plan = await self._safe_select(req, trace, errors)
    # 2) 上下文  
    ctx = await self._safe_context(req, routing_plan, trace, errors)
    # 3) 选择 Pool
    pool = self._safe_pick_pool(req, routing_plan, trace, errors)
    # 4) 执行 Pool
    raw = await self._safe_pool_run(req, routing_plan, ctx, pool, trace, errors)
    # 5) 聚合
    final_text = await self._safe_aggregate(req, routing_plan, ctx, raw, trace, errors)
    # 6) 发言
    out_obs = self._safe_speak(req, final_text, routing_plan, pool, trace, errors)
    # 7) 打包返回
    return AgentOutcome(emit=[out_obs], trace=trace, error=...)
```

**特点**:
- 每阶段都有 `_safe_*` 包装，失败时自动 fallback
- 错误不会中断链路，累积到 `errors` 列表并记录 `trace["error"]`
- 所有异常都有 fallback plan（见 4 个 fallback point）

---

## 3. 核心组件详解

### 3.1 PoolSelector：Pool/Strategy/Context/Prompt 路由选择

**文件**: [src/agent/planner/](../src/agent/planner/)  
**输入**: `AgentRequest + PoolSelectorInputView`  
**输出**: `RoutingPlan`

> 语义边界：PoolSelector 只负责 pool 路由、strategy 选择、context selector、prompt policy selector、预算/信心评分；
> 不负责分步任务分解、执行图构建、子agent协作规划。

#### 3.1.1 RoutingPlan 数据结构

```python
@dataclass
class RoutingPlan:
    task_type: str              # "chat", "code", "plan", "creative"
    pool_id: str                # "chat", "code", "plan", "creative"
    required_context: tuple[str, ...]  # 需要的上下文槽位
    meta: Dict[str, Any]        # 路由元数据
```

#### 3.1.2 PoolSelector 实现体系

1. **RulePoolSelector** ([src/agent/planner/rule_pool_selector.py](../src/agent/planner/rule_pool_selector.py))
   - 基于关键词的快速分类
   - 无 LLM 调用，确定性
   - 默认回退到 `task_type="chat"`

2. **LLMPoolSelector** ([src/agent/planner/llm_pool_selector.py](../src/agent/planner/llm_pool_selector.py))
   - 调用 LLM 进行分类与规划
     - 配置: `config/agent/agent.yaml > pool_selector.items.llm`
   - 使用 `LLMProvider.from_config()` + `asyncio.to_thread()`
    - 解析 JSON 输出为 RoutingPlan

3. **HybridPoolSelector** ([src/agent/planner/hybrid_pool_selector.py](../src/agent/planner/hybrid_pool_selector.py))
   - 两阶段：先 Rule，再可选 LLM 精细化
   - 配置可选参数控制何时升级到 LLM

#### 3.1.3 PoolSelector 配置

```yaml
# config/agent/agent.yaml
pool_selector:
    default: default              # 默认使用 "default" pool selector
    items:
        default:
            kind: hybrid
            config_file: config/agent/pool_selector/default.yaml
        rule:
            kind: rule
        llm:
            kind: llm
            llm:
                provider: bailian        # 指定 provider
                model: qwen-max          # 指定 model
                timeout_seconds: 6.0
```

#### 3.1.4 PoolSelector 错误与降级

- **LLM 超时/异常** → 若 HybridPoolSelector，回退 RulePoolSelector 结果；若仅 LLMPoolSelector，输出硬编码 fallback plan
- **JSON 解析失败** → 输出 fallback plan
- 所有降级都记录 `trace["pool_selector_summary"]["fallback"]`

---

### 3.2 Context：上下文供应链

**文件**: [src/agent/context/](../src/agent/context/)  
**输入**: `AgentRequest + RoutingPlan`  
**输出**: `ContextPack`

#### 3.2.1 ContextPack 与 ContextSlot

```python
@dataclass
class ContextPack:
    slots: Dict[str, ContextSlot]       # 槽位容器
    meta: Dict[str, Any]                # 构建元数据
    recent_obs: List[Observation]       # 向后兼容
    slots_hit: Dict[str, bool]          # 各槽位是否命中

@dataclass
class ContextSlot:
    name: str                           # 槽位名
    value: Any                          # 值
    priority: int                       # 优先级
    source: str                         # 提供者
    status: str                         # "ok", "missing", "error"
    meta: Dict[str, Any]                # 额外信息
```

#### 3.2.2 内置槽位与提供者

| 槽位名 | 提供者 | 值形状 | 优先级 | 备注 |
|--------|--------|--------|--------|------|
| `current_input` | CurrentInputProvider | dict: `{text, obs_id, source_name, session_key, actor_id, attachments}` | 100 | 自动注入 |
| `recent_obs` | RecentObsProvider | list[Observation] | 90 | 最近 20 条 |
| `plan_meta` | PlanMetaProvider | dict: `{task_type, pool_id, required_context, meta}` | 80 | 自动注入 |
| `session_state` | SessionStateProvider | dict: `{session_key, processed_total, error_total, ...}` | 70 | - |
| `persona` | PersonaProvider | any | 55 | stub |
| `memory` | MemoryProvider | any | 50 | stub |
| `knowledge` | KnowledgeProvider | any | 40 | stub |
| `tool_results` | ToolResultsProvider | any | 35 | stub |

#### 3.2.3 SlotContextBuilder 流程

[src/agent/context/builder.py](../src/agent/context/builder.py)

```python
async def build(req: AgentRequest, plan: RoutingPlan) -> ContextPack:
    # 1) 读取 plan.required_context
    requested_by_plan = list(plan.required_context or ())
    
    # 2) 自动注入 required_by_default 的槽位
    auto_injected = _resolve_auto_injected_slots(...)
    requested_effective = _merge_requested_slots(requested_by_plan, auto_injected)
    
    # 3) 根据优先级排序
    priorities = _resolve_priorities(requested_effective, ...)
    
    # 4) 逐一调用提供者
    for slot_name in requested_effective:
        provider = self._providers.get(provider_name)
        result = await provider.provide(req, plan)
        slots[slot_name] = _slot_from_result(...)
    
    # 5) 返回 ContextPack
    return ContextPack(slots=slots, meta={...}, recent_obs=..., slots_hit=...)
```

**输出 meta 示例**:
```python
{
    "requested_by_plan": ["recent_obs"],
    "auto_injected": ["current_input", "plan_meta"],
    "requested_effective": ["recent_obs", "current_input", "plan_meta"],
    "provided": ["recent_obs", "current_input", "plan_meta"],
    "missing": [],
    "errors": [],
    "priorities": {"current_input": 100, "recent_obs": 90, "plan_meta": 80},
    "priority_sources": {"current_input": "default", ...}
}
```

---

### 3.3 Pool Router：Pool 选择

**文件**: [src/agent/pools/router.py](../src/agent/pools/router.py)

```python
class AgentPoolRouter:
    def pick(self, req: AgentRequest, plan: RoutingPlan) -> Pool:
        # 1) 优先按 plan.pool_id 
        if plan.pool_id in self._pools:
            return self._pools[plan.pool_id]
        
        # 2) 次优按 plan.task_type（alias 机制）
        if plan.task_type in self._pools:
            return self._pools[plan.task_type]
        
        # 3) 最后回退到 chat_pool
        return self._chat_pool
    
    def fallback_pool(self) -> Pool:
        return self._chat_pool
```

**路由决策链**:
- 请求 pool_id="code" ✓ ("code" pool 存在)
- 请求 pool_id="code" ✗ → 尝试 task_type="code" ✓
- 请求 pool_id="unknown" ✗ → 尝试 task_type="unknown" ✗ → **fallback chat_pool**

**trace 记录**:
```python
trace["pool"] = {
    "requested_pool_id": plan.pool_id,
    "pool_name": type(pool).__name__,
    "pool_id": pool.pool_id,
    "fallback": False,  # 如果回退为 True
}
```

---

### 3.4 Pool：执行引擎

**文件**: [src/agent/pools/](../src/agent/pools/)  
**接口**: [src/agent/pools/base.py](../src/agent/pools/base.py)

#### 3.4.1 Pool 协议

```python
class Pool(Protocol):
    pool_id: str                        # 池唯一标识
    name: str                           # 池友好名
    
    async def run(
        self, 
        req: AgentRequest, 
        plan: RoutingPlan, 
        ctx: ContextPack
    ) -> Dict[str, Any]:                # 原始结果
        ...
```

#### 3.4.2 当前实现

**ChatPool** ([src/agent/pools/chat_pool.py](../src/agent/pools/chat_pool.py))

现状：默认仍是最小可用 draft 逻辑；已接入 PromptEngine 调试分支（`DEBUG_PROMPT_ENGINE=1`）

```python
class ChatPool:
    pool_id = "chat"
    name = "chat_pool"
    
    async def run(self, req, plan, ctx) -> Dict[str, Any]:
        if os.getenv("DEBUG_PROMPT_ENGINE") == "1":
            engine = PromptEngine()
            messages, manifest = engine.render("chat.single_pass", plan, ctx)
            return {"draft": "...", "prompt_engine_manifest": manifest.to_dict()}

        # 默认路径：稳定 fallback 文本
        draft = _build_fallback_draft(plan.task_type, ctx)
        return {
            "draft": draft,
            "task_type": plan.task_type,
            "pool_id": self.pool_id,
        }
```

**下一步升级** (Phase 1.2+):
- 在默认路径接入 LLMProvider（当前仅调试分支可观测 PromptEngine 结果）
- 返回 `{"draft": ..., "meta": {...}}`（补齐模型/时延/token 等 trace）
- 在 `_safe_pool_run` 与 aggregator 汇总 pool 级元数据

#### 3.4.3 Pool 返回值契约

Pool 必须返回 dict，至少包含 `"draft"` 键：
```python
{
    "draft": "<最终文本 或 中间草稿>",
    "meta": {                          # 可选但推荐
        "pool_id": "chat",
        "llm_model": "qwen-max",
        "llm_latency_ms": 123.45,
        "prompt_chars": 256,
        "fallback_used": False,
    },
    "task_type": plan.task_type,      # 可选
}
```

---

### 3.5 Aggregator：结果聚合

**文件**: [src/agent/pools/aggregator.py](../src/agent/pools/aggregator.py)

#### 3.5.1 聚合器接口

```python
class Aggregator(Protocol):
    async def aggregate(
        self,
        req: AgentRequest,
        plan: RoutingPlan,
        ctx: ContextPack,
        raw: Dict[str, Any],
    ) -> str:
        """raw (pool 结果) → final_text (字符串)"""
        ...
```

#### 3.5.2 当前实现

**DraftAggregator**（Phase 0 最小版）

```python
class DraftAggregator:
    async def aggregate(self, req, plan, ctx, raw: Dict) -> str:
        draft = raw.get("draft")
        if isinstance(draft, str) and draft.strip():
            return draft.strip()
        return "我已收到请求，当前在最小模式下先返回默认结果。"
```

**期望升级** (Phase 1+):
- 多 Pool 输出时做评估/选择（蜂群中的 integrate/select）
- 评分与过滤
- 拼接多个结果

---

### 3.6 Speaker：消息发送

**文件**: [src/agent/speaker/speaker.py](../src/agent/speaker/speaker.py)

```python
class Speaker(Protocol):
    def speak(
        self,
        req: AgentRequest,
        final_text: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Observation:
        ...

class AgentSpeaker:
    source_name = "agent:speaker"
    actor_id = "agent"
    
    def speak(self, req, final_text: str, extra=None) -> Observation:
        metadata = dict(extra or {})
        return Observation(
            obs_type=ObservationType.MESSAGE,
            source_name=self.source_name,
            source_kind=SourceKind.INTERNAL,
            session_key=req.obs.session_key,
            actor=Actor(actor_id="agent", actor_type="system", display_name="Agent"),
            payload=MessagePayload(text=final_text),
            metadata=metadata,
        )
```

**输出 Observation**:
- `obs_type`: MESSAGE
- `source_name`: "agent:speaker"（防循环：Core 遇此来源会跳过 Agent 处理）
- `payload.text`: final_text
- `metadata`: 包含 `task_type`, `pool_id`, `fallback`, 等

---

## 4. 错误处理与降级策略

### 4.1 四层 Fallback

| 阶段 | 正常流 | 异常处理 | 输出 |
|------|--------|---------|------|
| PoolSelector | 返回 RoutingPlan | 捕获异常 → 硬编码 fallback plan (task_type="chat", pool_id="chat") | fallback plan |
| Context | 逐槽位不阻塞 | 失败槽位记 status="missing"/"error" | ContextPack (部分槽位可能为空) |
| Pool | pool.run() ✓ | 异常 → 调用 fallback_pool.run() | raw dict (或最终还是异常则硬编码 `{"draft": "出错..."}`) |
| Aggregator | raw["draft"] | 异常 → 回退到 raw["draft"] | final_text (或 fallback 文本) |

### 4.2 错误列表与记录

Agent 内所有异常都会：
1. 记录到 `errors: list[str]` 累积器
2. 打印 logger.exception()
3. 最后汇总到 `trace["error"]` 与 `AgentOutcome.error`

```python
errors = []
try:
    plan = await self.pool_selector.select(req, ...)
except Exception as exc:
    logger.exception(f"Agent pool selector failed: {exc}")
    errors.append(f"pool_selector:{exc}")
    plan = <fallback_plan>

# ...最后
if errors:
    trace["error"] = "; ".join(errors)
```

### 4.3 防循环机制

**在 Core 层** ([src/core.py](../src/core.py)):
```python
# 遇到来自 agent 的消息，直接跳过 Agent 处理
if source_name.startswith("agent:") or actor_id == "agent":
    # 只做 memory 记录，不进 Agent
    ...
```

**效果**：Agent 输出 → Egress 回流 Bus → SessionRouter.route() → SessionWorker 取到 agent: 消息 → 跳过 gate/agent，直接 memory

---

## 5. 配置体系

### 5.1 Agent 总配置

**文件**: `config/agent/agent.yaml`

```yaml
version: "0.1-phase0"

pool_selector:
    default: default                    # 默认 pool selector ID
    items:
        default:
            kind: hybrid                    # 使用 hybrid pool selector
            config_file: config/agent/pool_selector/default.yaml
        rule:
            kind: rule                      # 规则 pool selector (快速)
        llm:
            kind: llm                       # LLM pool selector (精准)

pools:
  default: chat                       # 默认 pool ID
  items:
    chat:
      kind: chat
    code:
      kind: code_stub                 # 当前是 stub
    plan:
      kind: plan_stub
    creative:
      kind: creative_stub

subagents:
  default: chat
  items:
    chat:
      enabled: true

prompts:
    default: planner_default  # legacy prompt profile id
```

### 5.2 PoolSelector 细化配置

例如 `config/agent/pool_selector/default.yaml`:

```yaml
kind: hybrid
rule_stage:
  enabled: true
llm_stage:
  enabled: true
  escalation_threshold: 0.3           # 置信度 < 0.3 时升级
  provider: bailian
  model: qwen-max
  timeout_seconds: 6.0
  params:
    temperature: 0.2
    max_tokens: 256
```

### 5.3 LLM 配置

**文件**: `config/llm.yaml`

```yaml
version: 1

default:
  provider:
    bailian: ["qwen-max", "qwen-long-latest"]

providers:
  bailian:
    api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "<BAILIAN_API_KEY>"      # 支持 <ENV_VAR>
    models:
      qwen-max:
        temperature: 0.2
        max_tokens: 1024
  
  ollama:
    api_base: "http://localhost:11434"
    models:
      qwen3:1.7b:
        temperature: 0.2
        num_ctx: 32768
```

---

## 6. 跟踪信息详解

### 6.1 Trace 完整结构示例

```python
{
    # Pool selector
    "pool_selector_input_summary": {
        "current_input_len": 42,
        "recent_obs_count": 5,
        "recent_obs_preview_count": 5,
        "gate_hint_present": True,
    },
    "pool_selector_summary": {
        "pool_selector_kind": "hybrid",
        "selector_stage": "rule",
        "final_routing_source": "rule",
        "task_type": "chat",
        "pool_id": "chat",
        "confidence": 0.95,
        "reason": "matched_greeting",
    },
    
    # Context
    "context_build_summary": {
        "requested_by_plan": ["recent_obs"],
        "auto_injected": ["current_input", "plan_meta"],
        "provided": ["current_input", "recent_obs", "plan_meta"],
        "missing": [],
        "errors": [],
        "slots": [
            {
                "name": "current_input",
                "status": "ok",
                "priority": 100,
                "text_len": 42,
                "preview": "你好 Agent，告诉我今天天气如何"
            },
            {
                "name": "recent_obs",
                "status": "ok",
                "priority": 90,
                "count": 5
            },
        ],
    },
    
    # Pool
    "pool": {
        "requested_pool_id": "chat",
        "pool_id": "chat",
        "fallback": False,
        "raw_keys": ["draft", "meta"],
    },
    
    # Aggregation
    "aggregation": {
        "length": 128,
    },
    
    # Speaker
    "speaker": {
        "source_name": "agent:speaker",
        "actor_id": "agent",
    },
    
    # 汇总
    "fallback_triggered": False,
    "elapsed_ms": 234.5,
    "error": None,
}
```

### 6.2 日志示例

```
DEBUG: Agent pool selector summary: kind=hybrid source=rule task=chat pool=chat
DEBUG: Agent context summary: requested=2 auto=1 effective=3 missing=0 errors=0
DEBUG: Agent pool trace: requested=chat actual=chat fallback=False
DEBUG: Agent processing completed in 234.5ms, fallback_triggered=False
```

---

## 7. 当前限制与已知问题

1. **Pool 实现不完整**
   - ChatPool 当前是 stub（直接字符串拼接）
   - 需要实现 LLM 调用链路（PromptRenderer → MessageComposer → LLMProvider）
   - code/plan/creative pool 是 stub，实际回退到 chat

2. **Aggregator 太简单**
   - DraftAggregator 只读 raw["draft"]
   - 无评估、选择、整合能力
   - 蜂群架构（多池并发）不支持

3. **Context 提供者不完整**
   - persona, memory, knowledge, tool_results 都是 stub
   - 仅 current_input, recent_obs, plan_meta, session_state 有真实实现

4. **单 session 串行**
   - 设计选择：同一 session 内消息顺序执行
   - 慢请求会阻塞该 session 的后续消息
   - 不是 bug，是预期行为

5. **防循环依赖 Core 层逻辑**
   - Agent 无法自主判断是否来自自己
   - 依赖 Core 在路由前检查 source_name/actor_id

---

## 8. 接口与调用规范

### 8.1 AgentQueen 初始化

```python
# 默认配置
queen = AgentQueen()

# 自定义组件
queen = AgentQueen(
    pool_selector=HybridPoolSelector(config={...}),
    context_builder=SlotContextBuilder(),
    pool_router=AgentPoolRouter(pools={...}),
    aggregator=DraftAggregator(),
    speaker=AgentSpeaker(),
)
```

### 8.2 处理单条请求

```python
req = AgentRequest(
    obs=observation,
    gate_decision=gate_decision,
    session_state=session_state,
    now=datetime.now(),
    gate_hint=gate_hint,
)

outcome: AgentOutcome = await queen.handle(req)

# 使用结果
for obs in outcome.emit:
    await bus.publish(obs)

if outcome.error:
    logger.warning(f"Agent error: {outcome.error}")

print(outcome.trace["elapsed_ms"])  # 端到端耗时
```

### 8.3 池扩展示例（伪代码）

```python
class CustomPool:
    pool_id = "custom"
    name = "custom_pool"
    
    async def run(self, req, plan, ctx):
        # 从 ctx.slots 读取需要的槽位
        current = ctx.slots["current_input"].value
        recent = ctx.slots["recent_obs"].value
        
        # 业务逻辑
        result = await self.process(current, recent)
        
        # 返回规范格式
        return {
            "draft": result,
            "meta": {
                "pool_id": self.pool_id,
                "latency_ms": ...,
            }
        }

# 注册
router = AgentPoolRouter(pools={"custom": CustomPool()})
queen = AgentQueen(pool_router=router)
```

---

## 9. 演进路线图（Phase 规划）

### Phase 1.2: ChatPool 升级 (single_pass LLM)
- 进行中：PromptEngine（view/layout/budget/template/composer）已落地
- 待完成：ChatPool 默认路径接入 LLMProvider
- 待完成：回包 `meta` 与 trace 字段标准化

### Phase 1.3: 多 Pool 与蜂群
- 待完成：code_pool / plan_pool / creative_pool 从 stub 升级为可执行
- 待完成：Aggregator 升级为多池评估选优
- 待完成：按 plan 策略启用并发多池执行

### Phase 2: Context 完善
- 待完成：Persona / Memory / Knowledge / ToolResults 提供者从 stub 转实装
- 待完成：derived/future item 计算链路与缓存策略

### Phase 3: 可观测性与自适应
- 待完成：跨层 trace_id 与 span 级跟踪
- 待完成：基于指标的预算与模型动态策略

---

## 10. 文件导航

| 文件 | 职责 | 关键类/函数 |
|------|------|-----------|
| [src/agent/types.py](../src/agent/types.py) | 数据契约 | AgentRequest, RoutingPlan, AgentOutcome |
| [src/agent/queen.py](../src/agent/queen.py) | 总编排器 | AgentQueen.handle(), _safe_* fallbacks |
| [src/agent/planner/](../src/agent/planner/) | 池选择器体系 | RulePoolSelector, LLMPoolSelector, HybridPoolSelector |
| [src/agent/context/builder.py](../src/agent/context/builder.py) | 上下文构建 | SlotContextBuilder.build() |
| [src/agent/context/types.py](../src/agent/context/types.py) | 上下文类型 | ContextPack, ContextSlot |
| [src/agent/context/providers/](../src/agent/context/providers/) | 槽位提供者 | CurrentInputProvider, RecentObsProvider, ... |
| [src/agent/pools/base.py](../src/agent/pools/base.py) | 池接口 | Pool Protocol, PoolRouter Protocol |
| [src/agent/pools/router.py](../src/agent/pools/router.py) | 池路由 | AgentPoolRouter.pick() |
| [src/agent/pools/chat_pool.py](../src/agent/pools/chat_pool.py) | 聊天池实现 | ChatPool |
| [src/agent/pools/aggregator.py](../src/agent/pools/aggregator.py) | 聚合器 | DraftAggregator.aggregate() |
| [src/agent/speaker/speaker.py](../src/agent/speaker/speaker.py) | 发言者 | AgentSpeaker.speak() |
| [src/agent/registry.py](../src/agent/registry.py) | Agent 配置注册 | AgentConfigRegistry |
| [config/agent/agent.yaml](../config/agent/agent.yaml) | Agent 配置 | version, pool_selector, pools |

---

## 11. 常见问题与答案

**Q: 为什么 Agent 不直接读 session_state？**  
A: 为了强制所有上下文通过 slots/ContextPack，便于追踪、替换、升级。

**Q: Pool 能否完全自定义格式？**  
A: 可以，但必须返回 dict，aggregator 和 speaker 需要能处理你的输出。建议遵循 `{"draft": ...}` 约定。

**Q: PoolSelector 能否返回自定义字段？**  
A: RoutingPlan 只有 4 个字段，额外信息放在 `meta` dict。但记住下游可能只识别标准字段。

**Q: 一个 session 能否并发处理多条消息？**  
A: 不能。设计是单 session 串行。若需并发，应该拆成多 session 或使用不同 session_key。

**Q: 如何调整 PoolSelector 的超时？**  
A: 修改 `config/agent/pool_selector/default.yaml` 或 `config/agent/agent.yaml` 中的 `timeout_seconds`。

**Q: Aggregator 能否访问 Pool 的完整结果？**  
A: 能，`raw` 参数是 Pool 返回的整个 dict。

---

## 12. 参考文献

- [README.md](../README.md) - 项目总览
- [PROJECT_MODULE_DEEP_DIVE.md](./PROJECT_MODULE_DEEP_DIVE.md) - 全系统深潜
- [DESIGN_DECISIONS.md](./DESIGN_DECISIONS.md) - ADR 与设计选择
- [GATE_COMPLETE_SPECIFICATION.md](./GATE_COMPLETE_SPECIFICATION.md) - Gate 详细规范
