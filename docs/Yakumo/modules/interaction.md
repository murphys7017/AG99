# Interaction Middleware

## 当前定位

`astrbot/core/interaction/*` 是当前分支新增的 interaction orchestration layer。

它不是某个前端或 Live2D 场景的专用逻辑，而是通用平台交互中间件：

- 对启用平台，输入先经过官方 EventBus、Pipeline、权限和插件处理，再在核心 Agent 开始前进入 middleware。
- Prompt 层先收集一份规范 `ContextPack`；middleware 并发启动轻量 Router 与 Persona Expression，再在 `hybrid` 路径调用独立 Core Planner。Router、Planner、Persona 和 Core 只读取各自投影；直播音频和协议命令使用独立 Core bypass。
- 对 interaction turn，用户可见输出由 `InteractionOutputController` 统一 materialize、发送、记录。
- core 仍负责工具、知识库、subagent、搜索、任务执行等能力。
- middleware 负责 turn owner 语义、人格化表达、stream observation、finalized material 和 completion handoff。

在 Yakumo 的目标态里，interaction middleware 应进一步收口为
`Persona Runtime Shell`。它是人格层的一轮运行外壳，负责把输入 observation、route/reflex
判断、core delegation、输出 materialization 和 finalized material 串起来。

它不应拥有整个人格层的数据本体：

- base persona 仍由 persona repository / manager 管理。
- persona state 由 `PersonaStateService` 这类状态服务管理。
- memory 由 memory service 负责写入、检索和 snapshot。
- provider、tools、skills、subagent 仍应通过 gateway / capability registry 接入。

middleware 的职责是组合这些服务，并在一个 interaction turn 内形成可观测、可扩展、可回滚的执行现场。

## Runtime Observation 边界

当前存在两条语义不同的内部入口：

```text
RuntimeObservation
  -> PersonalRuntimeManager.submit_observation
  -> bounded Inbox / fixed aggregation window / coalesce
  -> ObservationBatch
  -> Deterministic Gate
     -> hold / reject diagnostics
     -> evaluate -> optional Personal Policy
        -> express ActionIntent -> RuntimeObservationEvent -> Persona -> Output
        -> defer persists a no-action deadline

已经决定发送的 RuntimeObservation
  -> RuntimeObservationEvent
  -> PersonalRuntimeManager turn admission
  -> InteractionMiddleware.handle_runtime_observation
  -> Personal Expression
  -> InteractionOutputController
  -> Platform + assistant-only Conversation + lifecycle
```

通用 Intake 表达系统事实，而不是伪造用户消息。Manager 复用官方会话与人格管理器解析
`PersonalRuntimeKey`；每个 Runtime 最多保留 64 条事实，同一显式 coalesce identity 只保留
最新项，第一条事实创建唯一的 1.5 秒固定聚合窗口，后续事实不延长截止时间，窗口结束后关闭为
一个不可变 batch。Gate 只根据结构化 features 和 Runtime state 判断 `evaluate / hold / reject`，
不执行语义决策；hold batch 会返回 Inbox，busy hold 在 turn settle 后重新评估。只有 `evaluate`
可以进入显式启用的 Personal Policy。Policy 通过统一 Prompt 管线读取受限事实，以严格
tool-call 契约返回 `ignore / observe / express / defer / execute`。`express` 被转换为内部
`ActionIntent` 后才进入已经决定发送的输出适配链；`defer` 仅写入无动作截止时间；`execute` 当前只写
diagnostics。通用 Intake 本身不经过 EventBus、Pipeline、Router、Planner、Core、Persona 或 Output；
不支持主动消息的目标可以进入 Intake，但会在 target capability Gate 被拒绝。

`RuntimeObservationEvent` 只适配已经决定发送的可见输出。它与平台消息共享同一个 Runtime 和
session lock，目标必须明确支持主动消息；没有 `visible_reply_material` 时不会请求模型，实际
发送失败会使 turn 失败，不能把未投递内容写成成功历史。

单目标 Heartbeat Source 已由 Core Lifecycle 托管；它只针对已配置且仍支持主动消息的默认目标
提交可过期、可合并的 `heartbeat` Observation，不构造消息或直接发送。群聊环境观察默认关闭；
启用后，官方 Waking 阶段只让同一默认目标的非唤醒群聊文本继续通过白名单和会话状态检查，
再转换为不含原文的 `conversation_activity` Observation，并在普通限流、插件、Router 和 Core 前
终止该平台事件。Action Coordinator 已实现 `express / defer`；其余 Runtime Sensor、多目标
session registry 与 `execute` 仍未实现。Policy 每日调用上限会在 Provider 请求前写入独立
Personal State Repository。
最近表达、冷却、静音和每日用量具备窄化的重启恢复边界。静音、quiet hours、cooldown 时长与
主动输出上限已经接入用户配置；Gate 立即执行静音、全局时区安静时段和输出预算。`express` 的可见输出
确认送达后才写回复冷却与主动输出计数，`defer` 写入无动作截止时间。因此 Inbox 可以由 Heartbeat 驱动，
但只有显式启用 Policy、配置 Provider 且通过 Gate 才可能主动表达。插件调用
`Context.send_message()` 的纯文本主动输出会建立 `proactive_output` Observation，经同一
session admission 和 Output Controller 发送；纯媒体主动消息暂时保留平台直发。

assistant-only 内容已经进入官方 Conversation、Prompt history 和 Memory history。历史转换
使用空 user payload 标识 assistant-only，不伪造用户消息；各目标 Renderer 再决定具体模型
消息格式。

目标链路：

```text
Input Runtime / Observation
  -> Interaction Middleware / Persona Runtime Shell
      -> Effective Persona Resolver
      -> Fast Route Classifier || Speculative Persona Expression
      -> persona completion / Core Planner
      -> Core Agent / Tools / Capabilities
      -> Output Gateway
          -> Text / Streaming
          -> Voice / TTS
          -> Generic Effect Calls -> Plugin Consumers
      -> Finalized Turn Material
  -> Postprocess / Memory Update
```

## 当前主模块

### `middleware.py`

职责：

- 创建 `InteractionTurnState`
- 入站媒体 materialization
- interaction STT
- observation / reflex 前置判断
- Prompt Collectors：一次收集本轮输入、人格、session、官方对话历史、统一 Memory、执行能力和插件贡献，生成规范 `ContextPack`
- Router：当前只输出 `persona` / `hybrid`，不承担用户可见回复、task planning 或 effect 输出；`silent` 类型保留但未向模型开放。它读取极简事实投影，不为单个插件打补丁，也不枚举或限制核心 Agent 的能力范围
- Core Planner：只在 `hybrid` 后独立判断 `execute` / `not_required`，并仅在 `execute` 时生成 `CoreTaskSpec`；它不读取 Router 的模型决策、Prompt 或输出
- Router/Persona 协同：二者并发启动。Persona 在输出前从 `pending` 原子进入 `committed`；Core 最终结果先提交时可以抑制尚未 committed 的即时表达
- Runtime 所有权：ProcessStage 在插件 Handler 前完成 admission 并取得 session lease；
  `TurnExecutionScope` 持有 Router、Persona、Context Material 和 Stream Observation task，
  lease 释放前统一完成或取消
- Hybrid 协同：Planner 返回 `execute` 后立即放行 Core，不等待 Persona。Planner 只生成 CoreTaskSpec，不向即时 Persona 注入 task summary；若 Core 最终结果先提交，尚未 committed 的即时回复会被抑制
- Core 协同提示：Core 只被告知本轮存在独立的 Persona 快速回复分支，并直接执行、返回实质结果材料；Persona 的内部状态和已发送文本不暴露给 Core
- Context/失败协同：Router、Persona 和 Planner 通过 turn-local single-flight 共享一次 Context Material 构建；单个分支取消不会取消其他分支仍需要的构建。Planner 失败禁止 Core，但已经 emitted 的 Persona 回合仍会正常 finalized
- PERSONA / HYBRID 编排；`silent` 类型仅保留为未向当前 Router Prompt 开放的内部状态
- live audio 与协议命令 Core bypass
- 通用 effect call 的输出与插件消费边界；middleware 不理解 Motion 或 Live2D 语义
- finalized material 校验
- 在 completed 前把规范 user message、AssetRef 元数据和最终 Persona 文本按 `turn_id` 同步幂等提交到官方 Conversation；提交失败时 turn 标记 failed
- 调度 `AFTER_TURN_COMPLETED` postprocess

当前 completion 语义：

- middleware 是 turn material producer
- postprocess 是 completion consumer boundary
- 官方 Conversation 是可见 Dialogue History owner；它在 turn completion 前提交，不由 postprocess 反推或补写
- Core Execution Ledger 是执行连续性 owner，不保存为用户可见对话，也不投影给 Router 或 Persona
- memory service 是 interaction turn 的主记忆写入 owner
- `completed=True` 表示 middleware lifecycle handoff completed，不表示 memory 一定已经写入
- `completion_state.status` 明确区分 `active` / `completed` / `failed` / `cancelled`
- lifecycle observer 是只读快速通知边界，当前由 middleware/output runtime 发布
  `received` / `routing` / `delegated` / `speaking` / `completed` / `failed` / `cancelled`；
  `thinking` / `tool_running` 保留给 Core 或可替换执行器按真实执行状态上报。observer
  应只做本地入队等快速操作，异步处理超过统一短预算会被取消并记录诊断，不阻塞主回复

### `output_controller.py`

职责：

- 捕获 interaction turn 的 `send` / `send_streaming`
- 分类 immediate reply、passthrough、core reply、core stream、streaming finish marker
- **新增** `capture_plugin_output()` — 插件输出的独立入口，支持 `direct` / `persona` 两种模式；默认 finalizes turn，`finalize=False` 仅用于随后还会有最终输出的进度消息
- 统一 visible-reply persona 入口、result contributor、reply prefix、reasoning display、TTS、t2i
- 记录 `InteractionUtterance` 与 visible output
- visible output snapshot 保留与 utterance 相同的 `message_id` / `delivered_message_ids`
- 产出 finalized turn material 后请求 middleware finalization
- Core 最终结果的捕获入口通过 `core_reply_handler` 交回 Middleware，由 Middleware 调用唯一
  Persona Runtime，再把显式 `PersonaExpressionResult` 交给输出物化；插件 persona 模式与流式插话
  仍复用同一个可注入 `visible_reply_renderer`；
  output_controller 自身不直接调 provider 或独立拼装 persona prompt
- 即时表达也由同一个 Persona Runtime 生成，并直接把 `PersonaExpressionResult` 交给
  Output Controller；它不是独立于“统一拟人化”的第二条生成链路

输出分类中的新 message kind：

- `plugin_direct` — 插件输出，不经人格改写
- `plugin_persona` — 插件输出，经人格改写

插件输出所有权约束：

- `event.send()`、`emit_output()`、`send_direct()`、`send_persona()` 默认是最终输出；官方 plugin handler 的输出事务会在 handler 结束前暂缓其 turn completion。
- 插件需要在 yield `ProviderRequest` 前提示用户时，使用 `emit_progress()` 或 `send_progress()`；它们可见但不写入 finalized material，也不触发 turn completion。
- 为兼容旧插件，官方 plugin handler 执行期间的普通 `event.send()` 会先进入输出事务：若 handler 后续 yield `ProviderRequest`，此前输出自动作为 progress；若 handler 正常结束且没有核心请求，则最后一条输出提交为最终回复。
- Handler yield 的 `ProviderRequest` 执行完成后，官方异步生成器会继续运行 post-yield 代码，随后继续剩余 Handler；ProcessStage 在整条 delegated 路径结束后退出，不重复调用默认 Core。
- `Context.send_message()` 的纯文本主动输出进入 Personal Runtime；同一 active turn 可通过
  `finalize=False` 作为 progress，跨 session 输出建立独立 proactive turn。纯媒体主动消息
  因缺少可持久化语义材料，当前仍使用原始平台 sink。

当前失败策略：

- interaction outbound materialization 失败不降级成文本成功发送
- TTS / t2i / finalizer 失败会写 failure ledger 并抛错
- 缺 persist callback 是 turn finalization failure，不是 memory persist failure
- 主运行时不再维护独立 finalizer；core final reply 和 stream interjection 统一走 persona visible-reply

### `turn_state.py`

职责：

- `InteractionTurnState`
- `InteractionUtterance`
- `InteractionStreamState`
- completion state
- failure ledger
- 受控读写函数

必要的 `event.extra` 只用于官方接口衔接或只读诊断；内部主链路以 turn state 为唯一可写状态。

### `turn_context.py` 与当前迁移状态

`PersonalTurnContext` 当前拥有 turn admission 所需的 turn、session、actor、input、observation、
runtime config、ProviderRequest 和官方 event 引用。普通平台事件与已经决定发送的
`RuntimeObservationEvent` 会建立该类型；通用 `submit_observation()` 不创建 event 或 turn
context，只将事实写入对应 Runtime Inbox。

它尚未成为整个 Interaction 的唯一调用参数。Router、Persona、Planner、Output 和
RespondStage 仍以 `AstrMessageEvent` 为兼容载体；静态分析在 Interaction 包中确认了
117 个 literal extra key、225 次 literal get/set 和 22 次动态 key 调用。部分 extra 是
只读诊断，但 route、output deferral、completion 和兼容回调仍包含可写协调状态。因此当前
准确描述是“typed admission context + event compatibility state”，不是完整的 typed
Personal Runtime。

task scope 和 immediate/final output reservation 已迁入 typed turn state。后续继续迁移
output intent、诊断和兼容投影；不能为减少 extra 数量而同时维护一套平行字段。

### `contributors.py`

职责：

- prompt / result / stream / lifecycle 插件扩展点视图
- 插件只拿阶段 snapshot，不拿可变 turn state
- 保留外部签名兼容，但内部正确性不依赖旧 dict 可变对象
- 插件卸载或热重载时按 module prefix 清理 prompt/result/stream/lifecycle/effect 注册，
  避免旧实例恢复为 active 后造成重复贡献或重复状态通知

### `output_modes.py`

新增模块。定义输出身份模型的最小类型集：

- `PluginOutputMode` — `DIRECT` / `PERSONA` 枚举
- `OutputOrigin` — `CORE` / `PLUGIN` 枚举，标识输出由谁产生
- `PluginOutputRequest` — 插件输出请求的数据封装
- `temporary_output_origin(event, origin)` — context manager，临时设置 `_interaction_output_origin` extra，退出时自动恢复

相关 extra key 常量：

- `OUTPUT_ORIGIN_EXTRA_KEY`（`_interaction_output_origin`）
- `PLUGIN_OUTPUT_MODE_EXTRA_KEY`（`_interaction_plugin_output_mode`）
- `PLUGIN_OUTPUT_LAST_MODE_EXTRA_KEY` / `PLUGIN_OUTPUT_LAST_KIND_EXTRA_KEY`（诊断用）

### `persona_runtime.py`

新增模块。`InteractionPersonaRuntime` 是未来独立 Persona Runtime 层的种子代码。

当前职责：

- `express_visible_reply(...)` — 统一 persona visible-reply 入口，接收“待表达材料”请求
- `render_plugin_output(...)` / `render_core_reply(...)` / `render_stream_interjection(...)` 只是同一入口的薄包装
- 本身不做 LLM 调用，只做编排
- 当前默认输出契约是严格 `tool_call`：注册虚拟工具 `persona_expression`，返回 `spoken_reply` 与 `effect_calls`，且 `allow_text_fallback=False`
- 当 renderer/provider 明确不支持协议级 tool-call 时，才受控降级为 prompt-only JSON；这不是 router/decision 的职责
- Persona Runtime 的表达规则、最终 request prompt 和输出契约由目标 `PromptRenderProfile` 提供；本轮待表达语义、核心流式 `observed_text / total_text / pending_text` 等事实由 Collector 写入原生 `input.visible_reply_material`
- 对 DeepSeek-V4 / `deepseek-reasoner` 这类 reasoning 模型，首轮 persona user input 会额外注入一次“角色沉浸模式” marker，
  用于约束 `<think>` 里的思维风格；稳定人格设定仍留在 `system`，marker 不作为长期人格本体

它不属于 Output Runtime，也不属于 middleware 核心链路，而是 Persona 层的轻量入口。当前挂在 `InteractionMiddleware` 下由构造函数装配。

## Voice 边界

`astrbot/core/voice/*` 是共享 STT/TTS service port：

- core 旧 pipeline 使用它保留普通 STT/TTS 兼容行为
- interaction middleware 使用它支持入站 STT 与出站 TTS
- failure policy 由调用方决定

当前策略：

- core 普通 TTS provider missing 时 warning 并继续文本输出
- interaction TTS provider missing / file registration failed / config missing 时 fail-fast
- live audio 是通用平台音频流协议，不是 Live2D 专用路径

## Persona Effect 输出契约

当前 persona visible-reply 的结构化结果由虚拟工具 `persona_expression` 承载，参数约束是：

```json
{
  "spoken_reply": "string",
  "effect_calls": [
    {
      "name": "effect.name",
      "arguments": {}
    }
  ]
}
```

补充约束：

- `effect_calls` 是固定字段；没有 effect 时返回空数组，而不是省略字段。
- effect 的 `arguments` 由注册的 `PersonaEffectSpec.parameters` 决定。
- motion 类 effect 如果包含 `axes`，运行时会把 `axes.*` 统一视为 `number` schema。
- `intent_tags` 是否必填不由 persona 顶层决定，而由具体 effect schema 决定；例如 motion effect 可在 `arguments` 内要求它。
- Router 不输出这个结构；它当前只返回 `persona` 或 `hybrid`。

## Postprocess / Memory 边界

interaction turn completion 的数据流：

```text
InteractionOutputController
    -> explicit finalized turn material
    -> InteractionMiddleware._finalize_turn()
    -> dispatch AFTER_TURN_COMPLETED postprocess
    -> MemoryPostProcessor
    -> MemoryService.update_from_postprocess(...)
```

约束：

- memory 只消费 finalized material，不从 visible outputs 临时推断完整 turn 语义
- `stream_interjection` 默认 `memory_relevant=False`
- Record/Image/Audio 投递形态记录在 utterance metadata 中，memory 使用 semantic assistant text

## Effect 插件边界

Persona Runtime 可以随 `spoken_reply` 生成通用 `effect_calls`。Core 只负责 effect spec 的注册、
结构化结果校验和阶段性传递，不内置动作、灯光、Live2D 或其他客户端领域模型。

插件负责：

- 注册自己拥有的 effect 名称及参数 schema。
- 通过 `register_persona_effect(..., event_filter=...)` 声明 effect 对当前事件是否可用；平台、设备或运行时不匹配时，不应让该 effect 进入 Persona 输出契约。
- 从当前阶段的 `InteractionResultView.effect_calls` 读取属于自己的调用。
- 将参数解释为插件私有行为，并通过 `platform_extras`、`client_objects` 或插件自己的传输链路交付。
- 自行处理设备能力、资源映射、动作约束和降级策略。

插件不得假设其他插件认识自己的 effect，也不应要求 Router 或 Core Agent 理解具体动作语义。
AG99live、Live2D 或桌面身体表现只是这一通用扩展机制的消费者，不是 Interaction 主流程节点。
`list_persona_effects(event=event)` 用于构建当前 Persona 契约；不传 `event` 的调用只用于注册表管理和诊断，仍会列出所有已启用注册项。
`event_filter` 必须是同步、无副作用的判断函数；判断抛出异常时 Core 会关闭当前事件上的该 effect，避免把不适用的 schema 暴露给模型。

## 插件侧两个接口

interaction middleware 对插件主要暴露两个阶段接口：

1. `register_interaction_prompt_contributor(...)`
   - 在本轮规范 `ContextPack` 构建阶段运行一次。
   - 用于向统一 Prompt 事实包注入结构化信息。
   - 返回 `PromptExtension` 或 `list[PromptExtension]`。
   - 通过 `meta.targets` 声明 Router、Core Planner、Persona 或 Core 是否可见；不接收任何模型决策。

2. `register_interaction_result_contributor(...)`
   - 在 interaction 输出阶段运行。
   - 用于读取本轮 decision、immediate reply、core result、final result、visible outputs 等结果快照。
   - 返回 `InteractionResultContribution`。
   - 可以补充平台侧 extras、client objects，或覆盖最终文本。

这两个接口不是普通 core prompt extension 的替代品。前者是 interaction turn 的事实采集兼容入口，后者用于 interaction 输出 materialization。两者都不能让插件把 Router 或 Planner 的模型决策重新注入 Prompt。

跨 Core 与 Interaction 都需要的模型事实应优先使用通用 `PromptExtensionCollectorInterface`。`on_llm_request` 只覆盖统一 Prompt Apply 后的 Core 请求，不保证参与 Router、Planner 或 Persona 的轻量调用。Prompt 各层完整边界见 `modules/prompt.md`。

### Prompt Contributor

注册方式：

```python
from astrbot.api import star
from astrbot.core.prompt import PromptExtension


class LocalPluginDirectoryContributor:
    plugin_id = "example.plugin_catalog"
    priority = 50

    async def collect(self, event, plugin_context, view):
        return PromptExtension(
            plugin_id=self.plugin_id,
            mount="capability",
            value={
                "plugins": [
                    {
                        "name": "Local Character Adapter",
                        "description": "负责本地角色的设备能力和前端显示。",
                    }
                ]
            },
            meta={"targets": ["router", "core_planner"]},
        )


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.context.register_interaction_prompt_contributor(
            LocalPluginDirectoryContributor()
        )
```

`collect(event, plugin_context, view)` 的 `view` 是只读 `InteractionPromptView`，其 `purpose` 为 `context_collection`。它提供规范事实快照，而不是 Router、Planner 或 Persona 的局部视图；插件必须在返回的 `PromptExtension.meta.targets` 中声明目标。
如果插件希望 Router 或 Core Planner 知道本地能力，返回精简插件目录并标记相应 targets。目录只说明插件是什么、负责什么；投影会丢弃运输外壳，只把 `name` / `description` 放进最终 Prompt。Router 不理解插件私有协议、动作参数或输出 schema；Core Planner 也不接收 Router 的决策。
如果插件希望影响 Persona visible reply，应返回目标为 `persona` 的 `PromptExtension`。中间件自己的 persona runtime 指令和 visible reply material 不走 extension。
常用字段：

- `view.turn_id`
- `view.platform_id`
- `view.session_id`
- `view.persona`
- `view.input`
- `view.memory`
- `view.recent_messages`
- `view.capabilities`
- `view.context_snapshot`

推荐 mount 选择：

- `capability`: 对 router 推荐放精简插件目录；对 persona_reply 可放插件自己的稳定能力契约。
- `context`: 当前请求动态事实，例如设备状态、运行时状态、临时 session facts。
- `system`: 仅用于稳定决策规则；不要放动态事实。
- `input`: 仅用于确实需要贴近当前用户输入的补充材料。

middleware 会把 `capability/system` 渲染进稳定 system prompt，把 `context`
渲染为 history 后、memory/knowledge 前的独立 context message。这样动态事实不会污染
system prefix，也不会进入会话历史。

失败语义：

- contributor 抛异常会记录 `_interaction_prompt_contributor_failures`。
- 返回值不是 `PromptExtension`、`list[PromptExtension]` 或 `None` 会直接失败。
- invalid mount 会直接失败。
- interaction 主链路开发期按 fail-fast 处理，不用 fallback 掩盖 contributor 错误。

### Result Contributor

注册方式：

```python
from astrbot.api import star
from astrbot.core.interaction import InteractionResultContribution


class MotionResultContributor:
    plugin_id = "example.motion"
    priority = 50

    async def collect(self, event, plugin_context, view):
        final_text = view.final_result or view.core_result or view.immediate_reply
        if not final_text:
            return None

        return InteractionResultContribution(
            plugin_id=self.plugin_id,
            platform_extras={
                "motion_intent": {
                    "action": "nod",
                    "reason": "assistant_acknowledged_user",
                }
            },
            client_objects=[
                {
                    "type": "motion_intent",
                    "action": "nod",
                    "source": "interaction_result_contributor",
                }
            ],
            metadata={
                "text_length": len(final_text),
                "phase": view.metadata.get("phase"),
            },
            priority=50,
        )


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.context.register_interaction_result_contributor(
            MotionResultContributor()
        )
```

`collect(event, plugin_context, view)` 的 `view` 是只读 `InteractionResultView`。
常用字段：

- `view.turn_id`
- `view.platform_id`
- `view.session_id`
- `view.purpose`
- `view.route_decision`
- `view.output_draft`
- `view.immediate_reply`
- `view.core_result`
- `view.final_result`
- `view.effect_calls`
- `view.visible_outputs`
- `view.utterances`
- `view.turn_material_snapshot`
- `view.final_candidate_material`
- `view.finalized_turn_material`
- `view.metadata`

`InteractionResultContribution` 字段语义：

- `platform_extras`: 合并到平台侧 extras，用于平台 adapter 或前端消费。
- `client_objects`: 追加到客户端对象列表，用于 UI、动作、Live2D、sidecar 等消费。
- `final_text_override`: 覆盖最终要发送的文本；只在确实需要改写最终表达时使用。
- `metadata`: contributor 自己的诊断和附加信息。
- `priority`: 合并顺序，数值越小越先处理。

result contributor 只能基于只读结果快照产出 contribution。不要修改 `view`，也不要把
motion/audio/image 等物理投递结果伪装成成功文本；中间件的输出 materialization 仍由
`InteractionOutputController` 统一处理。

失败语义：

- contributor 抛异常会记录 `_interaction_result_contributor_failures` 并打日志。
- 非 `InteractionResultContribution` 返回值会被忽略。
- result contributor 是输出扩展边界，不应作为主链路正确性的 fallback。

## 仍需继续收口

- **output gateway**：`capture_plugin_output()` 已建立 `plugin_direct` / `plugin_persona` 路径，
  origin 路由已接入 send_wrapper，但 `event.send` interception 仍为 MethodType 替换形态，
  后续可演进为正式 Output Gateway
- observation contributor / reflex contributor / body output contributor 扩展点
- relationship scope resolver、visibility/privacy policy、attention/cooldown policy
- Effective Persona Resolver 与 middleware decision 的明确接缝
- live audio 缺 provider / 文本降级 / completion diagnostics
- 真实平台日志断点，验证 payload、ledger、material、postprocess 输入一致
- `event.extra["_interaction_turn_state"]` 作为兼容承载的长期替代方案
