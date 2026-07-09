# Interaction Middleware

## 当前定位

`astrbot/core/interaction/*` 是当前分支新增的 interaction orchestration layer。

它不是某个前端或 Live2D 场景的专用逻辑，而是通用平台交互中间件：

- 对启用平台，输入先进入 middleware，再按 decision 转给 core 或由 middleware 自行回复。
- 对 interaction turn，用户可见输出由 `InteractionOutputController` 统一 materialize、发送、记录。
- core 仍负责工具、知识库、subagent、搜索、任务执行等能力。
- middleware 负责 turn owner 语义、人格化表达、stream observation、finalized material 和 completion handoff。

在 Yakumo 的目标态里，interaction middleware 应进一步收口为
`Persona Runtime Shell`。它是人格层的一轮运行外壳，负责把输入 observation、route/reflex
判断、core delegation、输出 materialization、body output intent 和 finalized material 串起来。

它不应拥有整个人格层的数据本体：

- base persona 仍由 persona repository / manager 管理。
- persona state 由 `PersonaStateService` 这类状态服务管理。
- memory 由 memory service 负责写入、检索和 snapshot。
- provider、tools、skills、subagent 仍应通过 gateway / capability registry 接入。

middleware 的职责是组合这些服务，并在一个 interaction turn 内形成可观测、可扩展、可回滚的执行现场。

目标链路：

```text
Input Runtime / Observation
  -> Interaction Middleware / Persona Runtime Shell
      -> Effective Persona Resolver
      -> Fast Route Classifier
      -> Core Agent / Tools / Capabilities
      -> Output Gateway
          -> Chat Reply
          -> Desktop Body Output
          -> Voice / TTS
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
- fast route classifier：只输出 `self_reply` / `hybrid`，不承担用户可见回复或 effect 输出；它使用原生 system base 任务说明，读取裁剪后的聊天记录、interaction memory，以及 router purpose 的本地插件目录，不为单个插件打补丁，也不枚举或限制核心 Agent 的能力范围
- SELF_REPLY / HYBRID / DELEGATE_TO_CORE 编排
- live audio protocol route
- Desktop Body Output intent 调度点
- finalized material 校验
- 调度 `AFTER_TURN_COMPLETED` postprocess

当前 completion 语义：

- middleware 是 turn material producer
- postprocess 是 completion consumer boundary
- memory service 是 interaction turn 的主记忆写入 owner
- `completed=True` 表示 middleware lifecycle handoff completed，不表示 memory 一定已经写入

### `output_controller.py`

职责：

- 捕获 interaction turn 的 `send` / `send_streaming`
- 分类 immediate reply、passthrough、core reply、core stream、streaming finish marker
- **新增** `capture_plugin_output()` — 插件输出的独立入口，支持 `direct` / `persona` 两种模式；默认 finalizes turn，`finalize=False` 仅用于随后还会有最终输出的进度消息
- 统一 visible-reply persona 入口、result contributor、reply prefix、reasoning display、TTS、t2i
- 记录 `InteractionUtterance` 与 visible output
- 产出 finalized turn material 后请求 middleware finalization
- 持有一个可注入的 `visible_reply_renderer: Callable`，所有用户可见自然语言都经这一个 persona 入口；
  output_controller 自身不直接调 provider 或独立拼装 persona prompt

输出分类中的新 message kind：

- `plugin_direct` — 插件输出，不经人格改写
- `plugin_persona` — 插件输出，经人格改写

插件输出所有权约束：

- `event.send()`、`emit_output()`、`send_direct()`、`send_persona()` 默认是最终输出；官方 plugin handler 的输出事务会在 handler 结束前暂缓其 turn completion。
- 插件需要在 yield `ProviderRequest` 前提示用户时，使用 `emit_progress()` 或 `send_progress()`；它们可见但不写入 finalized material，也不触发 turn completion。
- 为兼容旧插件，官方 plugin handler 执行期间的普通 `event.send()` 会先进入输出事务：若 handler 后续 yield `ProviderRequest`，此前输出自动作为 progress；若 handler 正常结束且没有核心请求，则最后一条输出提交为最终回复。

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

旧 `event.extra` 字段仍作为外部兼容镜像存在，但内部主链路应优先使用 turn state。

### `contributors.py`

职责：

- prompt / result / stream 插件扩展点视图
- 插件只拿阶段 snapshot，不拿可变 turn state
- 保留外部签名兼容，但内部正确性不依赖旧 dict 可变对象

### `memory_store.py`

当前定位：

- legacy interaction cache
- decision/context 构建阶段可读取
- 不再作为 turn completion 写入 owner

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
- Persona Runtime 自身的表达规则是原生 `system.base`，不是 `extension.system`；本轮待表达语义、核心流式 `observed_text / total_text / pending_text` 等材料进入原生 `input.visible_reply_material`
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
- fast router 不输出这个结构；它只返回 `self_reply` 或 `hybrid`。

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

## Desktop Body Output 边界

Desktop Body Output 是普通聊天输出之外的本地身体表现通道。AG99live 这类客户端应被视为
Yakumo persona 的 `Desktop Body / Presence Client`，而不是某个 session 的镜像。

它适合表现：

- 群聊或私聊 observation 经 Core 授权后的本地吐槽 / 摘要提醒
- 远程执行器、sandbox、工具任务的状态
- persona 的等待、分心、思考、失败、注意力转移等本地 presence
- 不应发送回原聊天窗口的低声反应或旁白

它不适合：

- 直接监听群聊原文并自行吐槽
- 自动把所有 session 内容搬到本地桌面
- 绕过 Core 的 visibility / privacy / importance / cooldown 判断
- 替代正式群聊或私聊回复

推荐 intent 形态：

```json
{
  "type": "body.commentary",
  "source": {
    "platform": "qq",
    "session": "group_123"
  },
  "visibility": "local_user_only",
  "privacy": "summary_only",
  "importance": 0.45,
  "audience": "local_user",
  "text": "那边群里又开始讨论部署问题了，看起来他们卡在环境变量上。",
  "tone": "casual",
  "motion_hint": {
    "emotion": "thinking",
    "intensity": 0.45
  }
}
```

推荐输出类型：

- `body.commentary`
- `body.state`
- `body.notification`
- `body.task_status`
- `body.attention_shift`
- `body.reflex`

这一路径应由 Core / middleware 产出 body intent，再由 AG99live Adapter 转成桌宠协议；
AG99live Frontend 只负责身体表现，例如气泡、语音、动作、表情、待机状态和任务状态。

## 插件侧两个接口

interaction middleware 对插件主要暴露两个阶段接口：

1. `register_interaction_prompt_contributor(...)`
   - 在 middleware fast route / persona reply 前运行。
   - 用于向 interaction router 或 persona prompt 注入结构化信息。
   - 返回 `PromptExtension` 或 `list[PromptExtension]`。
   - 影响中间件如何判断本轮应该 `self_reply` 还是 `hybrid`，或影响 persona visible-reply 如何表达。

2. `register_interaction_result_contributor(...)`
   - 在 interaction 输出阶段运行。
   - 用于读取本轮 decision、immediate reply、core result、final result、visible outputs 等结果快照。
   - 返回 `InteractionResultContribution`。
   - 可以补充平台侧 extras、client objects，或覆盖最终文本。

这两个接口不是普通 core prompt extension 的替代品。它们只作用在 interaction
middleware 的 turn 内部，用于插件参与“中间件决策”和“中间件输出 materialization”。

### Prompt Contributor

注册方式：

```python
from astrbot.api import star
from astrbot.core.prompt import PromptExtension


class LocalPluginDirectoryContributor:
    plugin_id = "example.plugin_catalog"
    priority = 50

    async def collect(self, event, plugin_context, view):
        if view.purpose == "router":
            return PromptExtension(
                plugin_id=self.plugin_id,
                mount="capability",
                value={
                    "plugins": [
                        {
                            "name": "AG99 Live Adapter",
                            "description": "负责本地虚拟角色的动作、表情、语音和前端显示。",
                        }
                    ]
                },
            )

        return None


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.context.register_interaction_prompt_contributor(
            LocalPluginDirectoryContributor()
        )
```

`collect(event, plugin_context, view)` 的 `view` 是只读 `InteractionDecisionView`。router purpose 下视图会被裁剪为路由所需的轻量上下文；persona_reply purpose 下才暴露人格、完整表达材料等。
如果插件希望 router 知道有哪些本地插件，应在 `view.purpose == "router"` 时返回精简的插件目录。插件目录只说明插件是什么、负责什么；router 会丢弃 `PromptExtension` 的运输外壳字段，只把插件 `name` / `description` 放进最终 prompt。router 只判断当前请求是否明确可由本地插件/拟人层完整处理，能则 `self_reply`，否则 `hybrid` 交给核心 Agent。router 不理解也不应硬编码插件私有协议、动作参数或输出 schema；具体参数生成仍属于 persona/output/plugin 层。
如果插件希望影响 persona visible-reply，应在 `view.purpose == "persona_reply"` 时返回插件自己的 `PromptExtension`。中间件自己的 persona runtime 指令和 visible reply material 不走 extension。
常用字段：

- `view.turn_id`
- `view.platform_id`
- `view.session_id`
- `view.persona`
- `view.input`
- `view.interaction_memory`
- `view.recent_messages`
- `view.capabilities`
- `view.decision_context`

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
- `view.decision`
- `view.immediate_reply`
- `view.core_result`
- `view.final_result`
- `view.visible_outputs`
- `view.utterances`
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
