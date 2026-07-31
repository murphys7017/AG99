# Cost / Context Runtime Plan

这份文档记录 Yakumo 后续需要严肃对待的成本控制与上下文运行时设计。它是计划文档，不是当前代码说明。

背景参考：

- `esengine/DeepSeek-Reasonix` 把 prefix-cache stability 当作 agent loop 的架构不变量，而不是事后优化。
- Reasonix 的公开文档强调 append-only / cache-first loop、长会话 prefix cache 命中、配置化 provider / model / price。
- Reasonix 的工程 spec 记录了 planner / executor 两模型协作时使用独立 session，避免共享 conversation 破坏 prefix cache。

Yakumo 不应直接复制 Reasonix。AstrBot / Yakumo 是多平台聊天与人格 runtime，不是终端 coding agent。
但 Reasonix 对成本控制的工程态度值得吸收：长期运行的 AI 系统必须把模型角色、上下文稳定性、缓存命中、压缩时机和预算边界设计进 runtime。

## 核心结论

Yakumo 后续需要新增一个 `Cost / Context Runtime` 方向。

它的职责不是替代 prompt system、memory system 或 provider manager，而是在这些系统之间建立成本与上下文稳定性的运行规则：

- 记录 provider / model 的 context window、输入价格、输出价格、cache-hit 价格。
- 区分稳定上下文和动态上下文，避免每轮重写可缓存 prefix。
- 为不同模型角色维护独立上下文 session。
- 控制 background mind / heartbeat / subconscious 的调用频率和预算。
- 让压缩成为少数明确的 cache reset point，而不是频繁隐式整理。
- 为每轮调用记录 token、缓存命中、预估成本、实际成本和触发原因。

## Reasonix 值得学习的点

### 1. Cache-stable session 是架构目标

Reasonix 的核心不是“用了某个便宜模型”，而是让 agent loop 的上下文组织方式贴合 DeepSeek prefix cache。

对 Yakumo 的启发：

- 稳定 persona / policy / capability contract 不应每轮重排。
- 动态 observation / memory snapshot / route state 应放在动态区域。
- 不要把时间戳、临时状态、随机顺序 tools 等内容注入稳定 prefix。
- prompt render 应尽量输出稳定顺序和稳定文本。

### 2. 多模型要按角色分 session

Reasonix planner / executor 分离时，会让不同模型跑在独立 session 中，避免一个模型的上下文污染另一个模型的 prefix cache。

对 Yakumo 的启发：

- 主聊天模型、interaction decision 模型、memory analyzer、background mind、小模型反思不应随意共用同一 conversation。
- `Persona Runtime Shell`、`Core Agent`、`Memory Analyzer`、`Background Mind` 应有明确 role session。
- 切换 provider / model 时要记录这是新的 context lane，而不是在原 session 里硬切。

### 3. 压缩是显式 reset point

Reasonix 将 compact 作为少数明确的上下文重置点，并结合 context window 比例触发。

对 Yakumo 的启发：

- Memory consolidation 不等于 prompt compression。
- Prompt compression 不应悄悄改变 persona / memory 的长期语义。
- 触发压缩时要记录 reset reason、保留 recent window，并把旧上下文归档。
- 压缩后的 summary 应成为新的稳定前缀的一部分，但必须可审计、可回滚。

### 4. 成本参数要配置化

Reasonix 示例配置里 provider/model 可以声明 `context_window` 和 `price`，包括 cache-hit、input、output 价格。

对 Yakumo 的启发：

- Provider 配置应支持成本元数据。
- UI / logs / diagnostics 应能展示本轮调用成本。
- 不同 runtime role 可以选择不同 provider 和预算。
- 后台任务默认应更保守，不能无限调用高价模型。

## Yakumo 目标结构

建议后续形成：

```text
Provider Registry
  -> Model Cost Metadata
  -> Context Runtime
      -> Context Lane
      -> Stable Prefix Policy
      -> Dynamic Context Region
      -> Compression / Reset Policy
      -> Budget Gate
  -> Provider Call
  -> Cost Ledger
```

### Model Cost Metadata

每个 provider / model 至少应能声明：

- `context_window`
- `input_price_per_1m`
- `output_price_per_1m`
- `cache_hit_price_per_1m`
- `currency`
- `supports_prefix_cache`
- `cache_policy_notes`

这些字段不应硬编码在业务逻辑里。

### Context Lane

Context lane 表示一条有稳定上下文策略的模型调用通道。

建议 lane 类型：

- `chat.main`
- `interaction.decision`
- `interaction.finalizer`
- `memory.short_term`
- `memory.consolidation`
- `memory.long_term`
- `background.heartbeat`
- `background.reflection`
- `core.task`
- `subagent.*`

同一 lane 内尽量保持 prefix 稳定；不同 lane 可以使用不同模型、不同预算和不同压缩策略。

### Stable Prefix Policy

稳定 prefix 应只包含长期稳定内容：

- base persona stable segment
- stable system policy
- stable tool / capability contract
- stable output contract
- stable renderer framing

不应包含：

- 当前时间
- 当前 observation
- 最近消息
- 临时 route decision
- 每轮变化的 memory snapshot
- 无稳定排序的工具列表

### Dynamic Context Region

动态区域承载每轮变化内容：

- `InputObservation`
- memory snapshot
- recent turns
- route hints
- task state
- body / presence state
- current output intent

这些内容可以变化，但不应污染稳定 prefix。

### Budget Gate

后台人格能力必须经过预算 gate。

预算 gate 至少考虑：

- role / lane
- session / user / persona
- 每分钟、每小时、每天预算
- importance threshold
- cooldown
- user visibility
- 是否允许使用高价模型
- 是否允许触发 background mind

对于 heartbeat / subconscious / active presence，默认策略应是：

- 先用规则判断是否值得调用模型。
- 低重要度 tick 不调用 LLM。
- 能用 cheap model 不用 expensive model。
- 能产出 no-output material 就不发聊天消息。
- 能复用已有 context lane 就不新建昂贵上下文。

### Cost Ledger

每次 provider call 应记录：

- lane
- provider / model
- trigger reason
- input tokens
- output tokens
- cached input tokens
- cache hit ratio
- estimated cost
- actual cost if provider returns usage
- compact / reset state
- associated turn / observation / task

这份 ledger 后续可以用于 dashboard、日志、调参和安全阈值。

## 与现有阶段计划的关系

当前前置主链是：

```text
official EventBus / Pipeline
  -> Personal Runtime turn admission
  -> Router
      -> persona -> Persona Expression
      -> hybrid -> Planner
          -> execute -> Execution -> Persona Expression
          -> not_required -> Persona Expression
  -> unified Output Runtime
```

但在进入 `Background Mind` 前，必须补上 `Cost / Context Runtime` 的设计和最小实现。

建议顺序调整为：

1. 收口 `PersonalSessionRuntime` 的 turn、mailbox 和 follow-up owner。
2. 将剩余运行状态迁入唯一 `InteractionTurnState`。
3. 统一 Output Dispatcher 和主动消息入口。
4. 固化 Context Snapshot 与 Capability Snapshot。
5. 落地 `Cost / Context Runtime` 的最小预算和 usage ledger。
6. 接入 `Background Mind`，所有模型调用必须经过 budget gate。

## 非目标

当前阶段不追求：

- 直接复刻 Reasonix 的 agent loop。
- 只支持 DeepSeek。
- 立刻实现完整 cache hit 统计。
- 立刻改写所有 prompt renderer。
- 立刻让 dashboard 展示完整成本报表。
- 为了缓存牺牲 persona / memory 的正确边界。

## 第一版最小落点

第一版可以先做文档和配置模型，不急着改 provider call 主链路。

最小可落地项：

- 在 provider config schema 中预留成本元数据字段。
- 在 runtime plan 中明确 context lane 概念。
- 在 background mind 设计前加入 budget gate。
- 在 prompt render 计划中标注 stable prefix / dynamic context region。
- 在 provider call 结果中预留 cost usage ledger 结构。

这样 Yakumo 后续接入小模型、心跳、潜意识和主动 presence 时，不会先把成本问题留成隐患。

## References

- `esengine/DeepSeek-Reasonix`: https://github.com/esengine/DeepSeek-Reasonix
- Reasonix engineering spec: https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/docs/SPEC.md
- Reasonix example config: https://github.com/esengine/DeepSeek-Reasonix/blob/main-v2/reasonix.example.toml
- Reasonix project site: https://esengine.github.io/DeepSeek-Reasonix/
