# Interaction Middleware

## 当前定位

`astrbot/core/interaction/*` 是当前分支新增的 interaction orchestration layer。

它不是某个前端或 Live2D 场景的专用逻辑，而是通用平台交互中间件：

- 对启用平台，输入先进入 middleware，再按 decision 转给 core 或由 middleware 自行回复。
- 对 interaction turn，用户可见输出由 `InteractionOutputController` 统一 materialize、发送、记录。
- core 仍负责工具、知识库、subagent、搜索、任务执行等能力。
- middleware 负责 turn owner 语义、人格化表达、stream observation、finalized material 和 completion handoff。

## 当前主模块

### `middleware.py`

职责：

- 创建 `InteractionTurnState`
- 入站媒体 materialization
- interaction STT
- route decision
- SELF_REPLY / HYBRID / DELEGATE_TO_CORE 编排
- live audio protocol route
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
- 统一 result finalizer、result contributor、reply prefix、reasoning display、TTS、t2i
- 记录 `InteractionUtterance` 与 visible output
- 产出 finalized turn material 后请求 middleware finalization

当前失败策略：

- interaction outbound materialization 失败不降级成文本成功发送
- TTS / t2i / finalizer 失败会写 failure ledger 并抛错
- 缺 persist callback 是 turn finalization failure，不是 memory persist failure

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

### `finalizer.py`

职责：

- 判断 core result 是否需要 finalization
- 调用 finalizer provider 生成最终表达
- 开发期 fail-fast，不发送替代文本掩盖失败

### `memory_store.py`

当前定位：

- legacy interaction cache
- decision/context 构建阶段可读取
- 不再作为 turn completion 写入 owner

## Voice 边界

`astrbot/core/voice/*` 是共享 STT/TTS service port：

- core 旧 pipeline 使用它保留普通 STT/TTS 兼容行为
- interaction middleware 使用它支持入站 STT 与出站 TTS
- failure policy 由调用方决定

当前策略：

- core 普通 TTS provider missing 时 warning 并继续文本输出
- interaction TTS provider missing / file registration failed / config missing 时 fail-fast
- live audio 是通用平台音频流协议，不是 Live2D 专用路径

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

## 插件侧两个接口

interaction middleware 对插件主要暴露两个阶段接口：

1. `register_interaction_prompt_contributor(...)`
   - 在 middleware decision 前运行。
   - 用于向 interaction decision prompt 注入结构化信息。
   - 返回 `PromptExtension` 或 `list[PromptExtension]`。
   - 影响中间件如何判断本轮应该 `self_reply`、`hybrid` 还是 `delegate_to_core`。

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


class MotionPromptContributor:
    plugin_id = "example.motion"
    priority = 50

    async def collect(self, event, plugin_context, view):
        return [
            PromptExtension(
                plugin_id=self.plugin_id,
                mount="capability",
                title="Motion Contract",
                value={
                    "motion_available": True,
                    "supported_actions": ["nod", "shake_head", "wave"],
                },
                value_kind="mapping",
                order=10,
                meta={
                    "scope": "static",
                    "node_type": "motion_contract",
                },
            ),
            PromptExtension(
                plugin_id=self.plugin_id,
                mount="context",
                title="Motion Runtime State",
                value={
                    "current_pose": "idle",
                    "can_interrupt": True,
                },
                value_kind="mapping",
                order=20,
                meta={
                    "scope": "dynamic",
                    "node_type": "motion_runtime_state",
                },
            ),
        ]


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.context.register_interaction_prompt_contributor(
            MotionPromptContributor()
        )
```

`collect(event, plugin_context, view)` 的 `view` 是只读 `InteractionDecisionView`。
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

- `capability`: 稳定能力契约，例如插件能提供哪些动作、哪些协议、哪些输出能力。
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

- 正式 output gateway 替换当前 send interception 形态
- live audio 缺 provider / 文本降级 / completion diagnostics
- 真实平台日志断点，验证 payload、ledger、material、postprocess 输入一致
- `event.extra["_interaction_turn_state"]` 作为兼容承载的长期替代方案
