# 多模态回合上下文与目标投影整改计划

## 文档状态

- 状态：已实施，待真实平台回归；源码与本文方案已完成一次一致性复核。
- 范围：Interaction Middleware 中 Router、统一 Persona Expression、Core Planner 与 Native Core 的输入上下文、图片传递和媒体降级路径。
- 不在范围：修改人格文案、调整 Router 的职责、替换 Provider、修改 AG99live 插件实现、为媒体能力新增常驻后台服务。
- 关联文档：
  - [目标态](../target-state.md)
  - [消息处理流程详解](../消息处理流程详解.md)
  - [人格架构重构计划](../persona-architecture-refactor-plan.md)
  - [Personal / Router / Plugin 三线并行设计计划](./parallel-plugin-runtime-plan.md)

## 1. 结论与整改目标

当前系统已经具备“一个 `InteractionContextMaterial` 被多条分支复用”的雏形，但它并不是纯事实快照：基础 `InputCollector` 会在上下文构建期间根据一个尚未绑定到实际消费者 Provider 的 `provider_request` 判断图片能力，并可能调用图片转述 Provider 或文件提取服务。于是 Router、统一 Persona Expression 的直接表达调用和 Planner 虽然逻辑上并行，仍可能共同等待与自身无关的媒体工作。

同时，直接表达调用沿用的压缩白名单显式排除了原始图片槽位；Planner 投影也没有图片槽位。结果是：实际选中的 Persona Provider 即使支持图片，也看不到图片；而需要处理截图的 Planner 只能依据附件计数猜测任务，进而把视觉理解错误描述为 `workspace_io`，将 Core 带入读文件工具路径。

整改后的目标不是为四个 Agent 各造一份完整 Prompt，也不是在 Interaction 中另建一套上下文系统；应复用既有 Prompt Pipeline 的**事实收集 -> 派生快照 -> 目标投影 -> Profile -> Renderer -> Apply**主链。需要做的是让每回合的基础事实快照不可变且无 Provider 调用，再根据目标和实际 Provider 能力构造独立视图：

```text
平台 Event
  -> Turn Input Facts（文本、引用、原始媒体引用、身份、时间、历史、记忆、人格快照）
  -> Router View       （严格纯文本）
  -> Persona View      （Provider 支持图片时直接传图）
  -> Planner View      （混合任务需要视觉证据时直接传图）
  -> Core View         （执行时保留原始媒体）
  -> Provider media projection / final gate（能力投影与最终安全校验）
```

这里的“共享”只共享事实、生命周期和按回合缓存；不共享预渲染 Prompt，不共享某个分支的 Provider 决策，也不共享某个 Provider 生成的转述结果作为所有分支的默认真相。

### 1.1 与既有 Prompt 系统的对应关系

本计划不改变现有层的职责，只修复它们在媒体上的断点：

| Prompt 层 | 已有能力 | 本次正确用法 |
| --- | --- | --- |
| Collector | 将运行时输入输出为命名 `ContextSlot`。 | `InputCollector` 只采集原始媒体事实，不在 Interaction base 阶段做转述或文件提取。 |
| Builder | `build(base=...)` 生成版本化、深拷贝的派生 `ContextPack`。 | 非视觉消费者确实需要文字降级时，由显式媒体解析 collector 形成一个分支局部派生包。 |
| Target Projection | 为 Router、Planner、Persona、Core 做确定性、隔离的角色投影。 | 决定某个目标“原则上能否看到原始图片”；Router 始终不能，Persona/Planner/Core 可以。 |
| Media capability projection | 当前尚未成为显式 Prompt 步骤。 | 在目标投影之后、Profile/Renderer 之前，以实际 Provider modalities 决定保留原图还是使用已解析的文字事实；该步骤不调用 LLM。 |
| Profile | 注入局部指令、输出契约与精确隐藏。 | 继续处理直接 Persona 表达的历史预算和表达契约，不再用静态硬隐藏替代媒体能力判断。 |
| Renderer / Apply | 将已经选定的图片编译为 content parts，并写入 `ProviderRequest`。 | 继续作为唯一媒体序列化入口；最终安全门覆盖 request、extra parts 与 contexts。 |

因此，媒体转述本身是按需产生的**派生事实**，而“原图还是转述进入此 Provider”是确定性的**能力投影**；两者都不属于 Router 决策、Profile 指令或 Provider Renderer 的业务选择。

## 2. 已核实的现状

下列内容来自当前源码与运行日志，不是本计划的假设。

| 位置 | 当前行为 | 问题 |
| --- | --- | --- |
| `astrbot/core/interaction/context_builder.py` | `build_interaction_context_pack()` 在 Personal、Router 分支绑定各自 Provider 前，以 `event.get_extra("provider_request")` 建构共享基础包。 | 此 request 通常为空，或不等于某个实际消费者 Provider。 |
| `astrbot/core/prompt/collectors/input_collector.py` | `InputCollector.collect()` 同时采集原始图片、图片转述与文件提取；当传入 request 不支持图片或为空时，可能调用 `default_image_caption_provider_id`。 | Provider 依赖工作进入了基础事实构建，违反“Router 不等待 enrichment”的既有设计。 |
| `astrbot/core/interaction/expression_agent.py` | `compact_context=True` 时，直接 Persona 表达使用 `_FAST_PERSONA_SLOT_NAMES`，且进一步隐藏 `input.images`、`input.quoted_images` 及转述槽位。 | 直接 Persona 表达即使使用视觉 Provider 也无法接收当前图片或引用图片。 |
| `astrbot/core/prompt/targets.py` | `PromptTarget.CORE_PLANNER` 白名单只含文本、历史、记忆和附件摘要。 | Planner 无法读取图片或截图内容。 |
| `astrbot/core/prompt/render/interfaces.py` 与 `request_adapter.py` | 图片槽位可以被渲染为 `image_url` content parts，再进入 `extra_user_content_parts` 或上下文消息。 | `image_count=0` 不代表请求没有图片；仅检查 `image_urls` 会漏掉真实媒体内容。 |
| `astrbot/core/agent/runners/tool_loop_agent_runner.py` | 非视觉 Provider 只清空 `image_urls`，没有在同一处明确处理所有 `extra_user_content_parts` 与 context message 中的图片。 | Provider 能力约束不在唯一边界，存在不一致传递和静默信息丢失风险。 |

运行日志还验证了上述链路的后果：在“复述这张图”场景中，Planner 做出了 `execute`，但将任务标为“图像内容识别与复述”并建议 `workspace_io`；后续 Core 请求记录的 `image_count=0`，随后暴露了文件读取工具。这个现象不是工具队列或 AG99live 动作时序问题，而是视觉输入契约在 Planner 与 Core 前已经丢失或被错误降级。

## 3. 不可改变的架构约束

1. **Router 永远是纯文本控制面。** 它只能读取文字、引用文字、附件摘要、有限历史、记忆和会话事实；不接收图片、不触发图片转述、不承担视觉判断。
2. **Persona 是唯一用户可见自然语言表达入口。** Router、Planner、Core 都不得生成用户台词；此整改不得改变 immediate/final 仲裁、effect 或输出 reservation 的所有权。
3. **系统只有一个统一 Persona Expression。** 用户输入到达后的直接表达、携带 Core 结果的表达、插件表达和主动表达，都只是同一表达面在不同材料与发送时机下的调用；不得在架构、Prompt 选择、能力授权或媒体策略中把它们分裂成“快速 Persona”和“最终 Persona”。
4. **Persona 历史默认服从偏长的 `persona_history_window_size`。** 直接表达不得再以固定小窗口覆盖用户配置；只有统一目标预算、明确的上下文上限或与当前输入无关的确定性裁剪才能缩减历史，而且对同一 turn 的不同 Persona 调用必须保持连续性。
5. **媒体能力由实际消费者 Provider 决定。** 不能用 Event 上的旧 request、默认 Core Provider 或另一个并行分支的 Provider 代替。
6. **基础事实构建不得调用模型或外部媒体服务。** 图片转述、文件提取、Provider 特定图片压缩/物化均不得阻塞 Router 的基础视图。
7. **原始媒体优先于转述。** 支持对应模态的 Persona、Planner 和 Core 直接接收原始媒体；转述只服务于不支持该模态、但确实需要理解该内容的消费者。
8. **Core 不依赖 Planner 的视觉摘要作为唯一证据。** Planner 可以把视觉需求写入任务说明，但执行阶段仍必须能拿到同一份原始图片。
9. **不得把视觉理解伪装为工作区 I/O。** `workspace_io` 仅表示需要读取、写入或处理工作区文件，不表示“看图片”。

## 4. 目标数据模型与所有权

### 4.1 共享事实快照

保留 `InteractionContextMaterial` 作为回合级 owner，但将其 `prompt_context_pack` 的语义收紧为“基础事实包”。现有 `ContextSlot` 继续作为载体，避免为同一输入再建一套并行 DTO：

- 文本事实：`input.text`、`input.quoted_text`、会话和历史槽位。
- 原始媒体事实：`input.images`、`input.quoted_images`、`input.media_content_parts`、文件记录及其来源、引用关系、输入注解。
- 派生但无外部调用的事实：`input.attachment_summary`、有效人格快照、记忆快照、最近历史。
- 不属于基础包的内容：图片转述、文件内容提取、Provider 特定图片物化结果、插件 Prompt enrichment、任意模型结论。

基础包一经写入 `InteractionContextMaterial` 即不再被业务链路原地修改。各分支只能从它构造新的投影或新的渲染请求。

### 4.2 回合级媒体解析表

在 `InteractionContextMaterial` 内增加一个小型、按回合存活的媒体解析表，而不是把转述再塞回基础 ContextPack。其职责是为同一资产的重复需求提供 single-flight：

```text
MediaResolutionKey
  = asset fingerprint
  + media kind
  + purpose (caption / file_extract)
  + resolver configuration fingerprint

MediaResolutionEntry
  = pending task | successful result | typed unavailable/failure result
```

约束：

- key 不包含 Router、Persona、Planner 这类消费者名称；相同的图片转述请求可以复用。
- key 必须包含转述 Prompt 和 Provider 配置指纹，避免切换配置后错误复用旧转述。
- 只缓存当前 turn；不把图片内容或 URL 写入长期 Runtime State、Conversation 或普通 INFO 日志。
- Provider 支持图片时绝不为了填充此表而预先转述；只有不支持图片且该分支需要语义内容时才启动任务。

建议将“资产解析和 per-turn single-flight”放在明确的 Interaction 辅助模块中，但让它通过现有 `PromptContextBuilder(base=...)` 产出派生 `ContextPack`，而不是直接拼接 Prompt 或维护平行 Pack 语义。可提供类似下列边界，避免各 Agent 直接调用 `InputCollector` 私有方法：

```python
async def build_media_view(
    material: InteractionContextMaterial,
    *,
    target: PromptTarget,
    provider: Provider,
    requirement: MediaRequirement,
) -> ContextPack:
    ...
```

其实现分两步：先按需生成或读取媒体派生事实，再将基础包与该 fragment 交给 `PromptContextBuilder(base=...)`。`MediaRequirement` 是确定性的调用侧意图，例如 `TEXT_ONLY`、`DIRECT_IMAGE_PREFERRED`、`VISUAL_EVIDENCE_REQUIRED`；它不是让模型猜测的 Prompt 字段。

### 4.3 单一 Provider 媒体门

在 Prompt target projection 之后增加一个 provider-aware 的媒体能力投影，并在 Provider 调用前保留一个共享的最终“媒体门”。前者负责正常路径中“原图或文字派生事实”的确定性选择；后者负责防止插件或低层适配重新塞入不受支持的媒体。两者都必须检查**所有**媒体载体：

- `ProviderRequest.image_urls` 与 `audio_urls`；
- `ProviderRequest.extra_user_content_parts`；
- `ProviderRequest.contexts` 中 content list 里的 `image_url` / `audio_url` parts；
- Router、Planner、Persona 直接调用 `provider.text_chat()` 时使用的 messages。

能力投影的输入是“已投影的目标视图 + 实际 Provider modalities + 本次 requirement”，输出只能是：

1. 保留可支持的原始媒体；
2. 用已完成的、同 purpose 的文字降级材料替换不支持的媒体；
3. 返回可诊断的 `media_input_unavailable`，由调用侧走明确失败语义。

最终媒体门不得悄悄保留不支持的内容，也不得在 `VISUAL_EVIDENCE_REQUIRED` 情况下把图片替换为裸 `"[Image]"` 后继续让 Planner 或 Core 假装完成任务。

## 5. 四个目标视图

| 目标 | 可见输入 | 不可见输入与等待规则 | 媒体规则 |
| --- | --- | --- | --- |
| Router | 当前文字、引用文字、附件计数、有限历史、记忆、会话事实。 | 不读取插件 enrichment；不等待媒体解析。 | 严格无图片、无音频、无转述调用。 |
| Persona Expression（直接表达调用） | 人格、当前文字、引用文字、偏长历史、memory、当前及引用图片。 | 不等待未完成的插件 enrichment；是否采用已就绪扩展遵循现有 best-effort 规则。 | 实际表达 Provider 支持 `image` 时直接传图；否则仅在图片参与本轮表达时请求/复用转述。 |
| Persona Expression（携带 Core 结果的调用） | 同一人格、同一连续历史、memory、Core 结果、必要插件表达扩展及同一轮媒体。 | Core 结果到达后才具备该次调用的材料；这不是另一种 Persona。 | 与直接表达调用使用同一媒体与能力策略；不能因为存在可见 Core 材料而盲目隐藏图片。 |
| Core Planner | Router 已决定 `hybrid` 后的文本、历史、记忆和与当前任务关联的图片。 | 不等待无关文件提取；不读取业务工具。 | 需要视觉证据时直接传图；不支持图片时只等待该分支的转述 fallback。 |
| Native Core | 完整 Core 执行材料、工具/技能、TaskSpec、原始当前轮媒体。 | 等待既有插件 enrichment；不反向影响已发送的 Persona。 | 支持图片时直接传图；否则按 TaskSpec 的媒体需求使用转述或给出明确不可用错误。 |

### 5.1 Planner 的视觉触发规则

Planner 只会在 Router 已经选择 `hybrid` 后运行，因此给它当前轮图片不会增加纯聊天路径的模型调用。为避免依赖“模型先看图、再决定自己是否需要看图”的循环，调用侧应根据原始输入做确定性标记：

- 当前消息或引用链存在图片，且文本明确指向图片、截图、照片、图表、界面、代码截图等；
- 当前消息仅有图片，但平台事件本身是一个明确的请求；
- 或路由/平台已提供结构化的“媒体请求”标记。

这时 Planner 使用 `VISUAL_EVIDENCE_REQUIRED`。若存在图片但当前任务明显与其无关，Planner 可以只见附件摘要。判定规则应保守：宁可在已进入 `hybrid` 的 Planner 请求中多传一张合法图片，也不能因关键词遗漏让“帮我看这个截图”失明。

Planner 的结构化结果需要将“执行任务需要视觉输入”与“需要某项执行能力”分开。建议在 `CoreTaskSpec` 增加类型化布尔字段 `requires_visual_understanding`，并在 Planner 输出契约中显式返回它：

- 它为 `true` 时，Core 必须保留对应原始图片或得到同 purpose 的转述；
- 它不是 `suggested_capabilities`，因此不能再被错误映射成 `workspace_io`；
- `workspace_io` 只有用户真的要求操作可访问文件时才允许出现。

## 6. 分阶段实施计划

### Phase 0：固定边界与基线

**目的：** 在改动行为前建立可比较的 trace，不扩大代码范围。

1. 为基础 ContextPack、每个目标投影和每次媒体门记录结构化诊断。
2. 记录 `turn_id`、target、实际 Provider 的 modalities、原始/传递/降级的媒体数量、是否等待 enrichment、耗时和稳定 reason code。
3. 不记录图片 URL、图片字节、转述全文、用户原文或 Provider 密钥；资产仅记录每回合短指纹或计数。
4. 用当前已有“图片复述”“视觉 Provider 的 Persona 图片回复”“纯文本 Router”三条真实日志建立基线。

**涉及文件：** `turn_state.py`、`context_builder.py`、`router_agent.py`、`expression_agent.py`、`core_planner.py`、`astr_main_agent.py`。

**验收：** 能从一个 `turn_id` 区分事实构建、目标投影、媒体解析和 Provider 请求四段耗时；不能再依据 `image_count` 单个字段判断是否传图。

### Phase 1：让基础输入收集回到纯事实

**目的：** 消除 Router/直接 Persona 表达因 Provider 依赖媒体工作被阻塞的根因。

1. 将 `InputCollector` 拆为“原始输入采集”和“按需媒体解析”两个明确职责。
2. 原始采集继续创建图片、引用图片、文件、媒体 content parts 和附件摘要所需记录，但不调用图片转述 Provider、不调用文件内容提取服务。
3. 现有 Native 非 Interaction 主链如仍要求旧行为，改为显式装配解析 collector，再经同一媒体能力投影渲染；不得依赖 `provider_request is None` 这一隐式语义。
4. `interaction_base_collectors()` 只装配纯事实 collector，确保 `build_interaction_context_pack()` 不再从 event extra 猜测任何分支 Provider。
5. 将旧的图片转述缓存复用到新的回合级解析表，或由新解析表统一调旧缓存；同一轮不可并发生成两次相同转述。

**主要触点：**

- `astrbot/core/prompt/collectors/input_collector.py`
- `astrbot/core/prompt/context_collect.py`
- `astrbot/core/interaction/context_builder.py`
- `astrbot/core/interaction/turn_state.py`
- 新增 `astrbot/core/interaction/media_context.py`

**验收：** 带图且配置转述 Provider 的 turn 中，`interaction.context_material scope=base` 不产生 Provider 请求；Router 完成时间不再依赖图片转述耗时。

### Phase 2：实现 Provider 感知的媒体投影和门控

**目的：** 把“是否直接传图、何时转述、如何失败”收敛到单一协议。

1. 实现 `MediaRequirement`、回合级 single-flight 解析表、媒体派生 collector 和 `PromptContextBuilder(base=...)` 派生 helper。
2. 从实际已解析的 `Provider` 读取 modalities；Provider 未声明或声明不完整时视为不支持，不做乐观传递。
3. 在 `PromptRenderEngine` 的目标投影后增加确定性的 provider-aware media projection；统一检查 Request、extra parts 和 contexts 三个媒体入口，取代 Tool Loop 中只裁剪 `image_urls` 的局部逻辑。
4. 图片物化/压缩仍可按 Provider 在分支内执行，但必须是调用前的局部准备，不能回写基础事实包。
5. 明确失败语义：视觉必要而原 Provider 不支持、转述未配置或失败时，Planner/Core 返回 typed failure；Persona 可作简短说明，但不能假称已经看到了图片。

**主要触点：**

- 新增 `astrbot/core/interaction/media_context.py`
- `astrbot/core/prompt/targets.py` 或同层独立 media projection 模块
- `astrbot/core/prompt/builder.py`
- `astrbot/core/prompt/render/interfaces.py`
- `astrbot/core/prompt/render/request_adapter.py`
- `astrbot/core/provider/request_media.py`
- `astrbot/core/agent/runners/tool_loop_agent_runner.py`

**验收：** 不支持图片的 Provider 不会收到任一形式的图片 part；支持图片的 Provider 保留原始图片；同一图片的 fallback 转述在一回合内最多执行一次。

### Phase 3：接入 Router 与统一 Persona Expression

**目的：** 保持 Router 纯文本，同时让同一个 Persona Expression 的不同调用时机遵守同一媒体和连续性规则。

1. Router 固定使用 `TEXT_ONLY` projection；删除任何可能由 Router 间接触发媒体解析的路径。
2. 删除或收口 `_FAST_PERSONA_SLOT_NAMES`、`_FAST_PERSONA_HISTORY_TURNS` 与 `compact_context` 中把直接表达误建成独立 Persona 视图的规则。直接表达可以不等待未完成的插件 enrichment，但必须保留当前轮原始输入、人格、memory 和由 `persona_history_window_size` 决定的偏长连续历史。
3. 在 Persona 真正调用 Provider 前应用媒体投影：视觉 Provider 直接接收图片，非视觉 Provider 只在本轮表达需要图片理解时等待 fallback。
4. 携带 Core 结果的 Persona 调用不得因为 `visible_reply_material` 存在就无条件隐藏图片；是否隐藏只由媒体 requirement 和已消费的材料决定。
5. 将 `immediate`、`final` 等字段限定为输出生命周期/诊断时机，不作为 Persona 类型、Prompt 能力或历史预算的分叉条件；保持 Router 不接收插件扩展。

**主要触点：**

- `astrbot/core/interaction/router_agent.py`
- `astrbot/core/interaction/expression_agent.py`
- `astrbot/core/prompt/targets.py`
- `astrbot/core/interaction/context_builder.py`

**验收：** 视觉 Persona 对“这张图是什么”直接根据图片回答；Router 的 context nodes 仍不包含图片/转述槽位；直接表达不等待未完成的插件 enrichment，且不会把 `persona_history_window_size` 强制压缩为固定小窗口。

### Phase 4：接入 Core Planner 并修正任务契约

**目的：** 让 Planner 能正确理解截图，并停止把视觉任务送入工作区工具。

1. 扩展 `PromptTarget.CORE_PLANNER` 的候选图片槽位，但只有 `VISUAL_EVIDENCE_REQUIRED` 时才在最终渲染中保留媒体。
2. 在 `CorePlannerAgent` 中根据当前输入确定媒体 requirement，并在直接 `provider.text_chat()` 调用前经过统一媒体门。
3. 扩展 `CorePlanningDecision` / `CoreTaskSpec` 及严格输出契约，加入 `requires_visual_understanding`。
4. 更新 Planner 系统提示：视觉理解是输入需求，不是 `workspace_io`；没有视觉证据时不得编造图片内容，也不得用文件工具替代视觉 Provider。
5. Planner 失败的回退保持现有“已发送 Persona 可完成本轮”的规则；新增 failure reason 只用于诊断和后续最终表达，不导致重复回复。

**主要触点：**

- `astrbot/core/prompt/targets.py`
- `astrbot/core/interaction/core_planner.py`
- `astrbot/core/interaction/types.py`
- `astrbot/core/interaction/middleware.py`

**验收：** 截图分析任务的 Planner 能基于图片建立 `CoreTaskSpec`，且不会仅因“需要看图”建议 `workspace_io`。

### Phase 5：接入 Native Core 和执行前媒体一致性

**目的：** 保证执行层看到与 Planner 相同的原始证据，并让失败可解释。

1. Core 构建 `CoreExecutionSpec` 时保留原始媒体事实，不把 Planner 的描述当成唯一视觉上下文。
2. `PromptTarget.CORE` 在实际 Core Provider 支持图片时传递原始图片；不支持时按 `requires_visual_understanding` 选择缓存转述或 typed failure。
3. 复核 `NativeExecutionAdapter`、`ProviderRequestAdapter` 和 `ToolLoopAgentRunner`，保证同一媒体门覆盖最终实际请求。
4. 当视觉输入不可用时，阻止以文件读取工具“补救”；Core 返回清晰的内部失败材料，由统一 Persona 表达为真实、一次性的用户回复。

**主要触点：**

- `astrbot/core/astr_main_agent.py`
- `astrbot/core/execution/*`
- `astrbot/core/prompt/render/request_adapter.py`
- `astrbot/core/agent/runners/tool_loop_agent_runner.py`

**验收：** Core 请求的结构化媒体统计与渲染结果一致；视觉任务不会产生无依据的 `astrbot_file_read_tool` 调用。

### Phase 6：删除过渡判断并做真实回归

**目的：** 避免长期维持“按 event extra 猜 Provider”与“按实际 Provider 投影”两套语义。

1. 删除 Interaction 基础构建中依赖 `event.get_extra("provider_request")` 判定图片能力的旧路径。
2. 删除快速 Persona 的图片硬隐藏和 Planner 的附件摘要替代视觉证据的临时补丁。
3. 仅在确有线上回滚需要时使用短期行为开关；开关必须注明 owner、移除条件和删除日期，不能成为永久兼容分支。
4. 更新流程文档，使“Collector 只收集事实、Projection 不调用 LLM”重新与源码一致。

**验收：** 搜索不到由 Interaction base `InputCollector` 发起的转述调用；文档、日志和代码均将 Router 描述为纯文本目标。

## 7. 验证矩阵

实现时只添加覆盖公开边界的最小测试；主要验收以真实平台日志为准。

当前实现状态：Phase 1 至 Phase 5 的源码边界已接入，Phase 6 的真实平台回归与旧日志基线复核仍待运行环境执行。已完成的自动化边界包括基础输入不触发媒体解析、非视觉分支按需生成转述、视觉任务与 `workspace_io` 解耦、统一 Persona 保留配置历史，以及 Interaction 直接请求的 Provider 模态清理。Persona 首选 Provider 失败并切换到不支持图片的回退 Provider 时，会在请求媒体规范化前保留本轮原始图片引用，并仅补做一次图片转述；该过程不重放已执行的请求或 Agent 生命周期钩子。

| 场景 | 应有行为 |
| --- | --- |
| 纯文本闲聊 | 基础事实、Router、直接 Persona 表达并行；无媒体解析调用。 |
| 视觉 Provider 的 Persona 图片问答 | Router 只见摘要；Persona 直接收到当前图片并一次性回复，不产生转述调用。 |
| 非视觉 Provider 的 Persona 图片问答 | 只有 Persona 分支启动一次转述；Router 不等待；失败时 Persona 明确说明未能读取图片。 |
| “看这个截图并帮我处理” | Router 选择 `hybrid`；Planner 收到图片并标记视觉需求；Core 收到同一原图或受控转述。 |
| 图片与当前任务无关的混合请求 | Planner 可只见摘要；不因附件存在误判为工作区文件任务。 |
| 引用消息含图片 | 当前与引用图片的来源、回复关系和顺序均保留；不会把引用图片误当当前图片。 |
| 切换 Persona/Planner Provider | 每一分支根据当轮实际 Provider modalities 重投影；不复用另一个 Provider 的能力判断。 |
| 图片转述 Provider 不可用 | 不阻塞 Router；视觉必要的 Planner/Core 产生 typed failure；无虚假成功、无文件工具替代。 |

最小自动化边界包括：纯基础收集不触发 Provider、Router 投影没有媒体、视觉 Persona 渲染含 image part、非视觉分支只得到已批准的文本 fallback、Planner 的视觉任务不产生 `workspace_io`。不为私有 helper 的调用顺序建立脆弱测试。

## 8. 可观测性与性能判定

新增或统一的诊断字段应至少包含：

- `turn_id`、`target`、`provider_id_hash`、`provider_modalities`；
- `base_facts_duration_ms`、`target_projection_duration_ms`、`media_resolution_duration_ms`、`provider_wait_duration_ms`；
- `source_image_count`、`direct_image_part_count`、`caption_count`、`dropped_media_count`；
- `media_requirement`、`media_resolution_source`、`media_failure_reason`；
- Router 与 Persona 的共同 `t0`，以及 Router 是否等待任何媒体解析。

成功标准不是简单压低总耗时，而是满足以下因果关系：

1. 基础事实构建的 Provider 调用数恒为零。
2. Router 的等待链中没有图片转述、文件提取或 Provider 特定媒体物化。
3. 支持图片的 Persona/Planner/Core 不出现不必要的转述调用。
4. 同一资产的 fallback 不重复调用，失败原因可从同一 `turn_id` 追踪。
5. 用户看到的可见回复仍由 Persona 统一产生，且一次 turn 不因媒体失败生成额外回复。

## 9. 风险与决策点

- **媒体物化成本：** 原图转 data URL 或压缩仍可能耗时，但它属于实际视觉消费者的局部准备，不能回到 Router 的公共关键路径。先记录耗时，再决定是否需要本地单次缓存。
- **Provider 声明不可信：** 模型配置声明 `image` 是直接传递的前提；Provider 运行时拒绝媒体时必须记录 `provider_media_rejected`，不能自动改用另一个分支的 Provider。
- **文件与图片边界：** 本次首先统一图片。文件提取遵循同一“按需 enrichment”原则，但 Office/PDF/OCR 的具体解析策略另行评审，避免把本计划扩成文件能力重构。
- **Planner 判定保守性：** 文本明确引用图片时必须传图；对附件无关的情况可节约视觉输入。规则应配置为内部确定性策略，不应让 Router 进行视觉判定。
- **兼容性：** `default_image_caption_provider_id` 首轮保留为 fallback Provider 配置，语义从“基础收集的默认预处理”收紧为“非视觉消费者的按需降级”。文档和配置说明必须同步更新。

## 10. 建议的提交顺序

1. Phase 0 与 Phase 1：纯事实边界和基线诊断。
2. Phase 2：回合级媒体解析表与统一媒体门。
3. Phase 3：Router/Persona 接入，先解决“视觉 Persona 看不到图”。
4. Phase 4：Planner 视觉输入与 `requires_visual_understanding` 契约。
5. Phase 5：Core 一致性与工具路径保护。
6. Phase 6：真实平台回归、文档同步和旧判断删除。

每一批只提交一个完整可运行的边界，不把阶段间的半成品通过默认 fallback 留在生产路径。若真实回归暴露模型 Provider 对 content parts 的兼容差异，先在媒体门修复适配，再继续下一阶段；不要在 Persona、Planner、Core 各自加特例。

## 11. 本次实施记录

- 基础 `InteractionContextMaterial` 由纯事实 `InputCollector` 构建；图片转述和文件提取只在绑定实际消费者 Provider 后通过 `PromptContextBuilder(base=...)` 派生。
- Router 继续只投影文本与附件摘要；统一 Persona Expression 不再以 `compact_context` 分裂人格、历史或媒体策略，历史长度服从 `persona_history_window_size`。
- Planner 和 Core 的目标投影包含当前/引用图片；视觉 Provider 直接接收原图，非视觉分支使用按需转述。Planner 通过 `requires_visual_understanding` 表达输入需求，不再把视觉理解映射为 `workspace_io`。
- Tool Loop 以及 Interaction 的 Router、Planner、Personal Policy 直接 Provider 请求均执行模态安全过滤，覆盖上下文消息和额外内容块。
- 自动化验证：相关 Interaction、Prompt 收集和 Planner 测试共 `144 passed`；定向 Ruff、`compileall` 与 `git diff --check` 通过。真实平台回归、Provider 兼容性和完整测试套件仍未在本次执行。
