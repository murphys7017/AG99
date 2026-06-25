# Output Unification Command Book

> 状态说明（2026-06-25）：
> 本文档保留为历史设计记录。
> 当前 interaction 主链路已经进一步收口为单一 visible-reply persona 入口：
> `first_response`、插件 persona 输出、core final reply、stream interjection 共用同一 persona prompt/render/tool-call JSON 路径；
> 文中的独立 `finalizer`、独立 stream 文案生成、以及 phase 化 persona 设计不再代表当前实现。

这是一份给其他 AI 编码代理使用的命令书。

目标不是讨论方案，而是指导实现：

```text
先统一插件主动发送消息的出口
再决定是否进行人格化处理
旧插件默认不改写内容
但所有输出都必须走同一条中间件 / Output Runtime 链
```

本文只处理“插件主动发送消息”这件事，不处理 Input Bus 全量接入，也不在这一轮迁移所有旧 hook。

## 阅读方式

如果你是执行这份命令书的 AI，请按下面顺序理解：

1. 先看“现状校正”，确认当前系统已经统一拦截输出，但没有区分 plugin/core 身份。
2. 再看 “Layer 1”，只实现最小可工作的 plugin output path。
3. 除非明确被要求，否则不要做 “Layer 2”。
4. 每完成一个 step 都先补测试，再继续下一步。

## 目标结论

本轮要实现的行为是：

```python
await event.send(message)
```

不再等价于“插件直接让平台适配器发消息”，而是变成：

```text
plugin
  -> event.send(...)
  -> unified output entry
  -> optional persona rewrite
  -> Output Runtime delivery
  -> platform event actual send
  -> legacy after-send hooks / finalized material
```

关键语义：

- 旧插件调用 `await event.send(message)` 时，默认不进行人格化改写。
- 即使不人格化，也必须统一经过中间件输出链，不能绕过。
- 后续允许插件显式请求人格化输出。
- 平台 event 子类仍负责最终的平台发送细节。

## 最终接口目标

最终目标接口是：

```python
await event.send(message, persona=False)
await event.send(message, persona=True)
```

但本轮不要直接把这个目标粗暴铺到所有平台子类上。

原因：

- 几乎所有平台 event 子类都重写了 `send(...)`。
- 直接改签名会扩散到大量平台实现。
- 很容易把 streaming、visible completion、平台特有 payload、测试桩一起改炸。

所以命令分为两层：

```text
Layer 1:
先建立统一输出入口和 persona 开关语义
但不要求第一刀就给所有平台 send() 改签名

Layer 2:
在 Layer 1 稳定后，再把 persona 参数暴露到 event.send(...)
```

如果只能做一轮，请只完成 Layer 1。

## 不允许做的事

- 不允许在这一轮重写所有平台适配器。
- 不允许删除现有 `InteractionMiddleware` 的 send interception。
- 不允许破坏旧插件 `await event.send(message)` 的调用方式。
- 不允许让 persona 模式直接绕过 Output Runtime。
- 不允许把 `OnAfterMessageSentEvent` 当成主动发送入口来改。
- 不允许在这轮顺手迁移 Input Bus、Executor hook、system hook。

额外禁止事项：

- 不允许把 plugin 输出复用成 `core_reply` 或 `core_stream` 语义。
- 不允许把 plugin 输出强行写成 `MessageEventResult.is_model_result()`。
- 不允许为了接 persona 模式而直接在 `AstrMessageEvent.emit_output(...)` 中调用 provider。
- 不允许把 `capture_plugin_output(...)` 做成另一个“迷你中间件”；它只能是 Output Runtime 的一个入口。
- 不允许修改 `RespondStage` 的基础发送顺序。

## 现状校正

执行前必须先纠正一个常见误判：

```text
当前系统不是“插件输出完全没有统一”
而是“插件输出已经被 interaction middleware 统一拦截，
但还没有被标记为 plugin output”
```

这意味着：

- middleware 启用时，`event.send(...)` 已经不会直接落到平台适配器。
- 它会先进入 `InteractionOutputController.capture_message_chain(...)`。
- 但 controller 当前只知道“收到一条 outbound message”，并不知道它来自 core 还是 plugin。

所以本轮工作不是“从零建立统一发送链”，而是：

```text
在现有统一拦截基础上，
补上 plugin/core origin、
plugin direct/persona mode、
plugin output 的独立 message kind 和记录语义
```

### 当前真实分类行为

`InteractionOutputController._classify_outbound_message(...)` 当前会把输出分成：

- `immediate_reply`
- `streaming_finish_marker`
- `suppressed_duplicate_final`
- `core_final_model_result`
- `core_final_followup_after_stream`
- `passthrough`

因此，插件主动 `event.send(...)` 目前并不一定会被当成 `core_reply`。

更准确地说：

```text
插件输出目前会被并入现有 interaction 输出分类体系，
通常会落到 passthrough，
但系统没有独立的 plugin output 身份、模式和记录语义
```

所以本轮的设计目标不是修复“有没有拦截”，而是修复“拦截后如何正确分类和记录”。

## 现状摘要

当前相关事实：

1. `AstrMessageEvent.send(...)` 是平台发送基类，定义在 `astrbot/core/platform/astr_message_event.py`。
2. 大量平台子类自己重写了 `send(...)`，例如 Telegram、QQ、WebChat、Lark、Slack 等。
3. interaction middleware 当前通过 `MethodType(...)` 动态替换：
   - `event.send`
   - `event.send_streaming`
   - `event.complete_visible_turn`
4. 替换后，`event.send(...)` 不直接发，而是进入 `InteractionOutputController.capture_message_chain(...)`。
5. 真正发给平台时，Output Controller 会调用：
   - `event.send_interaction_message(...)`
   - `event.send_interaction_streaming(...)`
6. `send_interaction_message(...)` 已经是“统一出口”的雏形。

因此，本轮实现的最佳切入点不是新造一个发送系统，而是：

```text
围绕 send_interaction_message / capture_message_chain 建立标准化的 plugin output path
```

## 本轮完成后的理想行为

Layer 1 完成后，理想行为应该变成：

```text
plugin -> event.send(message)
  -> middleware send wrapper
  -> detect origin=plugin
  -> capture_plugin_output(mode=direct)
  -> materialize as plugin_direct
  -> event.send_interaction_message(...)
  -> visible_outputs / finalized material
```

而不是：

```text
plugin -> event.send(message)
  -> capture_message_chain(...)
  -> 混入 core-oriented classification
```

同样地，后续显式人格化应该是：

```text
plugin -> event.send_persona(message)
  -> capture_plugin_output(mode=persona)
  -> rewrite text through persona expression path
  -> materialize as plugin_persona
  -> event.send_interaction_message(...)
```

## 输出身份模型

本轮要建立的最小身份模型如下：

```text
output_origin:
  - core
  - plugin

plugin_output_mode:
  - direct
  - persona
```

二者是不同维度，不要混淆：

- `output_origin` 解决“这是谁发的”
- `plugin_output_mode` 解决“插件输出要不要先人格化”

core 输出永远不读取 `plugin_output_mode`。
plugin 输出默认 `direct`。

## 实施总顺序

严格按这个顺序执行：

1. 定义统一输出模式枚举和请求数据。
2. 给 `AstrMessageEvent` 增加“插件输出入口 helper”。
3. 在 `InteractionOutputController` 中接入 plugin output path。
4. 让 helper 始终走 Output Runtime。
5. 旧 `event.send(message)` 默认转成 direct 模式。
6. 在 middleware 启用和未启用两种情况下都验证兼容。
7. 最后才评估是否把 `persona` 参数公开加到 `event.send(...)`。

## 文件边界

### 本轮主要修改区

- `astrbot/core/platform/astr_message_event.py`
- `astrbot/core/interaction/middleware.py`
- `astrbot/core/interaction/output_controller.py`
- `tests/unit/test_astr_message_event.py`
- `tests/unit/test_interaction_middleware.py`
- `tests/unit/test_interaction_output_controller.py`

### 本轮尽量不动

- `astrbot/core/pipeline/result_decorate/stage.py`
- `astrbot/core/platform/sources/*/*event.py`
- `astrbot/core/interaction/finalizer.py`
- `astrbot/core/interaction/router_agent.py`

**实际修改（必要修正，未超边界）**：

- `respond/stage.py`：为 `deliver_message_chain` 中的 `event.send()` 和 `event.send_streaming()` 加了
  CORE origin 标记（`temporary_output_origin(event, OutputOrigin.CORE)`），防止非 interaction 事件的
  核心输出被误判为 plugin output。未改动 RespondStage 的基础发送顺序。
- `expression_agent.py`：新增 `rewrite_plugin_output()` 和配套 prompt/helper 函数。这是将
  persona rewrite 从 output_controller 迁入正确层的必要改动，属于 expression 层的正常扩展。

如果你发现自己已经开始批量改平台 event 子类、pipeline stage 的发送顺序或 finalizer 的核心语义，
说明你已经超出本轮边界。

## Layer 1 详细命令

Layer 1 的目标：

```text
不改旧插件调用
先让插件主动发送都走统一出口
同时支持 direct / persona 两种模式
默认 direct
```

### Step 1: 新增输出模式定义

新增文件建议：

```text
astrbot/core/interaction/output_modes.py
```

新增内容：

```python
from dataclasses import dataclass
from enum import Enum
from typing import Any

from astrbot.core.message.message_event_result import MessageChain


class PluginOutputMode(str, Enum):
    DIRECT = "direct"
    PERSONA = "persona"


@dataclass(slots=True)
class PluginOutputRequest:
    message: MessageChain
    mode: PluginOutputMode = PluginOutputMode.DIRECT
    source: str = "plugin"
    metadata: dict[str, Any] | None = None
```

要求：

- 这里只定义 direct / persona。
- 不在这一轮加入 silent、background、presence 等更多模式。
- `message` 只接受 `MessageChain`。
- 允许 `metadata` 为空；不要强行定义庞大的 schema。

### Step 2: 给 `AstrMessageEvent` 增加统一插件输出 helper

修改文件：

```text
astrbot/core/platform/astr_message_event.py
```

新增常量建议：

```python
PLUGIN_OUTPUT_MODE_DIRECT = "direct"
PLUGIN_OUTPUT_MODE_PERSONA = "persona"
```

新增方法：

```python
async def emit_output(
    self,
    message: MessageChain,
    *,
    mode: str = PLUGIN_OUTPUT_MODE_DIRECT,
    metadata: dict[str, Any] | None = None,
) -> None:
    ...
```

实现要求：

1. 优先从 `event.get_extra("_interaction_output_controller")` 读取当前 Output Controller。
2. 如果 controller 存在：
   - 调用新的 controller 方法，例如 `capture_plugin_output(...)`。
   - 不直接调用平台 `send(...)`。
3. 如果 controller 不存在：
   - `direct` 模式回退到旧 `self.send(message)`。
   - `persona` 模式暂时也回退到旧 `self.send(message)`，但写入一个 extra 标记，便于后续观察。
4. helper 本身不做人格改写，只负责分发。

推荐伪代码：

```python
async def emit_output(self, message, *, mode="direct", ):
    controller = self.get_extra("_interaction_output_controller")
    if controller is not None:
        await controller.capture_plugin_output(
            message,
            self,
            mode=mode,
        )
        return

    if mode == "persona":
    await self.send(message)
```

禁止：

- 禁止在 `emit_output(...)` 里直接导入 provider 或调用 LLM。
- 禁止在这里构造 finalized material。
- 禁止在这里偷偷设置 `event.set_result(...)`。

### Step 3: 给 `AstrMessageEvent.send(...)` 增加最小兼容桥

这一步有两种可执行方案。

#### 方案 A，推荐

先不改 `send(...)` 签名，只改行为入口。

修改基类：

```python
async def send(self, message: MessageChain) -> None:
    await self._record_send_operation()
```

保持不变。

然后在 middleware interception 的 wrapper 中，把插件主动发送分流到新 helper。

优点：

- 不需要第一刀改所有平台子类签名。
- 旧插件完全无感。

缺点：

- 还不能公开支持 `await event.send(message, persona=True)`。

#### 方案 B，第二阶段再做

把 `persona` 参数公开暴露到 `event.send(...)`：

```python
async def send(
    self,
    message: MessageChain,
    *,
    persona: bool = False,
    output_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    ...
```

但只有在 Layer 1 稳定后再做。

本命令书要求：

```text
本轮默认执行方案 A
不要直接执行方案 B
```

### Step 4: 修改 middleware 的 send wrapper

修改文件：

```text
astrbot/core/interaction/middleware.py
```

定位函数：

```python
def _install_core_output_interceptor(self, event: AstrMessageEvent) -> None:
```

当前内部有：

```python
async def send_wrapper(wrapped_event, message):
    await output_controller.capture_message_chain(message, wrapped_event)
```

改造目标：

1. 保留现有 core 输出拦截逻辑。
2. 但要区分“core 正在发”和“插件主动发”。
3. 插件主动发默认进入 direct 模式。

新增 event extra 标记建议：

```text
_interaction_output_origin = "core" | "plugin"
_interaction_plugin_output_mode = "direct" | "persona"
```

推荐做法：

- 在需要让 core 产出走原路径的地方，显式设置 `_interaction_output_origin = "core"`。
- 对普通 `event.send(...)` wrapper，如果没有 origin 标记，则视为插件主动输出。

推荐新 wrapper 伪代码：

```python
async def send_wrapper(wrapped_event, message):
    origin = wrapped_event.get_extra("_interaction_output_origin")
    if origin == "core":
        await output_controller.capture_message_chain(message, wrapped_event)
        wrapped_event._has_send_oper = True
        return

    await output_controller.capture_plugin_output(
        message,
        wrapped_event,
        mode=wrapped_event.get_extra(
            "_interaction_plugin_output_mode",
            "direct",
        ),
    )
    wrapped_event._has_send_oper = True
```

要求：

- core 输出和 plugin 输出必须走不同入口。
- 不能把插件输出伪装成 core final result。
- 不允许影响现有 first_response、core_stream、finalizer 行为。

### Step 4.1: core origin 标记规则

如果一个输出本来就属于 interaction/core 产物，必须显式标记：

- `emit_immediate_spoken_reply(...)` 进入前设置 `origin=core`
- core 最终 reply 投递前设置 `origin=core`
- core streaming 投递前设置 `origin=core`

推荐做法不是到处散落 set/unset，而是新增一个小 helper，例如：

```python
def _with_output_origin(
    event: AstrMessageEvent,
    origin: str,
):
    ...
```

或者：

```python
@contextmanager
def output_origin(event, origin):
    ...
```

要求：

- 使用 `try/finally` 恢复旧值。
- 不能让一个 core 标记泄露到插件后续发送。

推荐伪代码：

```python
previous = event.get_extra("_interaction_output_origin")
event.set_extra("_interaction_output_origin", "core")
try:
    await self.capture_message_chain(...)
finally:
    event.set_extra("_interaction_output_origin", previous)
```

### Step 4.2: plugin mode 标记规则

插件主动输出如果没有显式指定 mode，一律视为：

```text
mode = direct
```

如果调用 `event.send_persona(...)`，则设置：

```text
_interaction_plugin_output_mode = "persona"
```

但这个标记只应作为 wrapper 默认值来源。

真正执行时，`capture_plugin_output(...)` 必须接收显式参数，不能只依赖 extra。

### Step 5: 在 Output Controller 增加 plugin output capture

修改文件：

```text
astrbot/core/interaction/output_controller.py
```

新增方法：

```python
async def capture_plugin_output(
    self,
    message: MessageChain | None,
    event: AstrMessageEvent,
    *,
    mode: str = "direct",
    metadata: dict[str, Any] | None = None,
) -> None:
    ...
```

这是本轮最核心的新增函数。

实现分支要求如下。

#### direct 模式

逻辑：

```text
plugin MessageChain
  -> materialize as plugin_direct
  -> deliver through event.send_interaction_message(...)
  -> record visible output
  -> persist finalized material
```

具体要求：

1. 不调用 persona LLM。
2. 可以复用现有 `materialize_interaction_outbound_message(...)`，但要传入新的 `message_kind="plugin_direct"`。
3. `result_is_model_result=False`。
4. 最终通过 `_deliver_visible_message(...)` 发出。
5. `semantic_text` 直接取 message plain text。
6. `visible_outputs` 记录 kind 为 `plugin_direct`。

补充要求：

- direct 模式可以继续复用 t2i / markdown / platform extras 的 materialization 逻辑。
- 但不能触发 finalizer。
- 不能把 `result_is_model_result=True` 传进去。

#### persona 模式

逻辑：

```text
plugin MessageChain
  -> extract semantic text
  -> persona rewrite / expression path
  -> deliver through event.send_interaction_message(...)
  -> record visible output
  -> persist finalized material
```

第一刀要求非常克制：

1. 只处理纯文本人格化。
2. 如果消息不包含 plain text，可直接回退为 direct。
3. 不做复杂多模态人格改写。

实现方式建议：

- 新增一个轻量 helper，例如：

```python
async def _rewrite_plugin_output_via_persona(
    self,
    event: AstrMessageEvent,
    message: MessageChain,
    metadata: dict[str, Any] | None = None,
) -> MessageChain:
    ...
```

- 该 helper 可以先复用 interaction 的 expression provider 配置。
- 输入是插件给出的 plain text。
- 输出是一个新的 `MessageChain([Plain(rewritten_text)])`。

要求：

- 如果 LLM 重写失败，必须降级到 direct 原文发送。
- 降级时记录日志和 extra 标记，但不能吞消息。
- 第一刀只处理 `message.get_plain_text()` 非空的情况；空文本直接回退 direct。
- 第一刀不要试图人格化图片、文件、语音、卡片或复杂 mixed chain。

推荐伪代码：

```python
async def capture_plugin_output(..., mode="direct", ):
    if message is None:
        return

    if mode == "persona":
        plain = message.get_plain_text().strip()
        if plain:
            try:
                message = await self._rewrite_plugin_output_via_persona(
                    event,
                    message,
                        )
                kind = "plugin_persona"
            except Exception:
                event.set_extra("_interaction_persona_rewrite_failed", True)
                kind = "plugin_direct"
        else:
            kind = "plugin_direct"
    else:
        kind = "plugin_direct"

    materialized_message, materialization = await self.materialize_interaction_outbound_message(
        event,
        message,
        message_kind=kind,
        result_is_model_result=False,
    )
    ...
```

### Step 5.1: persona rewrite helper 的边界

**实现说明（与初始设计的差异）**：

初始设计建议将 `_rewrite_plugin_output_via_persona()` 直接放在 `output_controller.py` 中。
实际实现改为**依赖注入**方式，理由：

1. Output Controller 不应知道 provider、prompt 管线或 expression 配置。
2. 改写逻辑属于 Persona Runtime 的职责，不属 Output Runtime。

因此实际实现为：

- `output_controller.py` 删除了 `_rewrite_plugin_output_via_persona()`，改为持有
  `persona_output_renderer: Callable`（由 middleware 在装配时注入）。
- `persona_runtime.py` 新增 `InteractionPersonaRuntime`，作为未来独立 Persona Runtime 层的种子。
- `expression_agent.py` 新增 `rewrite_plugin_output()`，复用完整的 prompt collect → render 管线
  （persona、memory、session context）。
- `middleware.py` 在构造函数中装配 `persona_runtime`，并将 `_render_plugin_output_via_persona`
  注入 `output_controller.persona_output_renderer`。

```text
输入插件给出的语义文本
  -> InteractionPersonaRuntime.render_plugin_output()
    -> InteractionExpressionAgent.rewrite_plugin_output()
      -> _prepare_render_result(mode="plugin_output_rewrite")
        -> collect_context_pack() + render()
      -> provider.text_chat() + rewrite prompt
      -> return rewritten text
  -> return MessageChain([Plain(rewritten_text)])
```

`_rewrite_plugin_output_via_persona(...)` 的职责只能是：

```text
输入插件给出的语义文本
  -> 调一次 persona expression/rewrite path
  -> 返回一个新的纯文本 MessageChain
```

它不能负责：

- 决定路由
- 调用 Executor
- 组装复杂 finalized material
- 修改 turn state 的核心决策
- 直接发送消息

### Step 6: 扩展 `_deliver_visible_message(...)` 的 message kind

修改文件：

```text
astrbot/core/interaction/output_controller.py
```

定位函数：

```python
async def _deliver_visible_message(...)
```

要求：

- 支持新的 `message_kind`：
  - `plugin_direct`
  - `plugin_persona`
- 不改变已有：
  - `immediate_reply`
  - `passthrough`
  - `core_reply`
  - `core_stream`

如果该函数内部依赖 `message_kind` 做 platform extras、client object、finalized material 或 contribution 选择，必须把这两个新 kind 加入分支。

如果你看到这些分支存在任何：

- `if message_kind == "core_reply"`
- `if message_kind in {...}`
- `metadata["message_kind"]`

都必须检查是否要把 `plugin_direct` / `plugin_persona` 补进去。

### Step 7: 统一 finalized material 记录

修改文件：

```text
astrbot/core/interaction/output_controller.py
```

目标：

- 插件主动输出不能只是“发出去就完了”。
- 也必须进入 `visible_outputs` 和 `finalized material`。

要求：

- `plugin_direct` 和 `plugin_persona` 都记录到 turn visible outputs。
- `build_interaction_memory_reply_from_visible_outputs(...)` 能看到这些输出。
- 这样后续 memory、postprocess、trigger 才能天然接上。

补充约束：

- plugin 输出可以进入 `visible_outputs`，但不要冒充 `assistant_text` 的唯一来源。
- 如果一轮里既有 core reply 又有 plugin output，保留真实出现顺序。
- 不要在这轮重写 memory aggregation 规则，只接入已有机制。

### Step 8: 为插件提供显式 persona helper

仍修改：

```text
astrbot/core/platform/astr_message_event.py
```

新增方法：

```python
async def send_persona(
    self,
    message: MessageChain,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    await self.emit_output(
        message,
        mode="persona",
    )
```

新增方法：

```python
async def send_direct(
    self,
    message: MessageChain,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    await self.emit_output(
        message,
        mode="direct",
    )
```

这样即使 `event.send(..., persona=True)` 还没开放，插件作者和后续系统代码也已经有明确入口。

## Layer 2 命令

Layer 2 只有在 Layer 1 测试稳定后再做。

目标是公开支持：

```python
await event.send(message, persona=True)
```

### Step 9: 改 `AstrMessageEvent.send(...)` 签名

修改文件：

```text
astrbot/core/platform/astr_message_event.py
```

目标签名：

```python
async def send(
    self,
    message: MessageChain,
    *,
    persona: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    ...
```

基类默认行为：

- `persona=False` 时保持旧 send 语义。
- `persona=True` 时调用 `emit_output(..., mode="persona")`。

### Step 10: 批量修改平台子类签名

必须逐个修改这些平台 event 类的 `send(...)` 签名，使其至少能接受新关键字参数：

- `astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py`
- `astrbot/core/platform/sources/telegram/tg_event.py`
- `astrbot/core/platform/sources/webchat/webchat_event.py`
- `astrbot/core/platform/sources/lark/lark_event.py`
- `astrbot/core/platform/sources/slack/slack_event.py`
- `astrbot/core/platform/sources/discord/discord_platform_event.py`
- `astrbot/core/platform/sources/line/line_event.py`
- `astrbot/core/platform/sources/kook/kook_event.py`
- `astrbot/core/platform/sources/wecom/wecom_event.py`
- `astrbot/core/platform/sources/wecom_ai_bot/wecomai_event.py`
- `astrbot/core/platform/sources/weixin_oc/weixin_oc_event.py`
- `astrbot/core/platform/sources/weixin_official_account/weixin_offacc_event.py`
- `astrbot/core/platform/sources/qqofficial/qqofficial_message_event.py`
- `astrbot/core/platform/sources/misskey/misskey_event.py`
- `astrbot/core/platform/sources/mattermost/mattermost_event.py`
- `astrbot/core/platform/sources/dingtalk/dingtalk_event.py`
- `astrbot/core/platform/sources/satori/satori_event.py`

修改原则：

```python
async def send(
    self,
    message: MessageChain,
    *,
    persona: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    if persona:
        await self.emit_output(message, mode="persona")
        return

    # 保留原平台发送逻辑
    ...
    await super().send(message)
```

注意：

- 这一步改动面很大。
- 如果项目当下优先的是稳定推进，不建议本轮做。

## 需要修改的函数清单

### 必改

`astrbot/core/platform/astr_message_event.py`

- 新增 `emit_output(...)`
- 新增 `send_persona(...)`
- 新增 `send_direct(...)`
- 可选：Layer 2 再改 `send(...)`

`astrbot/core/interaction/middleware.py`

- 修改 `_install_core_output_interceptor(...)`
- 修改内部 `send_wrapper(...)`

`astrbot/core/interaction/output_controller.py`

- 新增 `capture_plugin_output(...)`
- 新增 `persona_output_renderer` 参数（依赖注入）
- 删除原 `_rewrite_plugin_output_via_persona(...)`（移入 expression_agent）
- 扩展 `_deliver_visible_message(...)`
- 扩展 visible output / finalized material 记录逻辑

`astrbot/core/interaction/persona_runtime.py`（新建）

- 新增 `InteractionPersonaRuntime.render_plugin_output(...)`

`astrbot/core/interaction/expression_agent.py`

- 新增 `rewrite_plugin_output(...)`
- 新增 `_prepare_render_result(..., mode="plugin_output_rewrite")`
- 新增 `build_plugin_output_rewrite_system_prompt()` / `build_plugin_output_rewrite_prompt()`
- 新增 `add_plugin_output_rewrite_slots_to_pack()`

### 本轮尽量不改

`astrbot/core/platform/sources/*/*event.py`

- Layer 1 尽量不改
- Layer 2 才批量改 `send(...)` 签名

## 状态标记规范

新增 extra key 规范：

```text
_interaction_output_origin
_interaction_plugin_output_mode
_interaction_plugin_output_metadata
_interaction_persona_rewrite_failed
```

建议语义：

- `_interaction_output_origin`: `core` / `plugin`
- `_interaction_plugin_output_mode`: `direct` / `persona`
- `_interaction_plugin_output_metadata`: 插件输出附带信息
- `_interaction_persona_rewrite_failed`: 人格重写失败后降级标记

不要继续发散生成大量临时 key。

推荐再增加两个只读诊断 key：

- `_interaction_plugin_output_last_mode`
- `_interaction_plugin_output_last_kind`

仅用于测试和调试，不作为业务判断前提。

## 兼容矩阵

执行实现前后，应满足这张最小矩阵：

| 场景 | middleware 关闭 | middleware 开启 |
| --- | --- | --- |
| `event.send(message)` | 走旧平台 send | 走 plugin direct output path |
| `event.send_direct(message)` | 回退旧平台 send | 走 plugin direct output path |
| `event.send_persona(message)` | 回退旧平台 send，并记录 persona unavailable | 走 plugin persona output path |
| core immediate reply | 不适用 | 保持现有行为 |
| core final reply | 不适用 | 保持现有行为 |
| core streaming | 不适用 | 保持现有行为 |

## 实施检查单

每完成一个文件后都要自查：

### `astr_message_event.py`

- 有没有新增 `emit_output(...)`
- 有没有新增 `send_direct(...)`
- 有没有新增 `send_persona(...)`
- fallback 时会不会递归调用自己

### `middleware.py`

- `send_wrapper(...)` 是否区分 core/plugin
- core origin 标记是否会在 `finally` 中恢复
- 有没有影响 `send_streaming(...)` 和 `complete_visible_turn(...)`

### `output_controller.py`

- 有没有新增 `capture_plugin_output(...)`
- 有没有删除原 `_rewrite_plugin_output_via_persona(...)`（已移到 expression_agent）
- 有没有接收 `persona_output_renderer` 参数注入
- persona 失败是否降级 direct
- plugin output 是否进入 visible output 记录
- 有没有错误触发 finalizer / model_result 路径

### `persona_runtime.py`

- 有没有新增 `render_plugin_output(...)`
- 是否只做编排而不直接调 provider

### `expression_agent.py`

- 有没有新增 `rewrite_plugin_output(...)`
- `_prepare_render_result` 是否通过 `mode` 参数区分 fast_expression 和 plugin_output_rewrite

## 回滚条件

如果出现下面任一现象，应回滚到只做 helper、不做 wrapper 分流的状态：

- core immediate reply 被当成 plugin output
- core final reply 不再经过原 finalizer 路径
- streaming 行为回归
- WebChat / WecomAIBot 的 visible completion 语义被破坏
- 平台 event 子类出现参数不兼容错误

回滚优先级：

1. 保住 core 输出链
2. 保住旧插件 `event.send(...)`
3. 再继续推进 plugin/persona 模式

## 测试命令书

必须新增或修改这些测试。

### `tests/unit/test_astr_message_event.py`

新增测试：

- `emit_output()` 在无 controller 时，`direct` 回退到旧 `send(...)`
- `send_persona()` 在无 controller 时不报错，回退到旧 `send(...)`
- `send_direct()` 调用 `emit_output(mode="direct")`

### `tests/unit/test_interaction_middleware.py`

新增测试：

- 插件主动调用 `event.send(...)` 时走 plugin output path，而不是 core output path
- core 输出仍走原 `capture_message_chain(...)`
- 插件输出默认 mode 为 `direct`
- core origin 标记在调用后会恢复

### `tests/unit/test_interaction_output_controller.py`

新增测试：

- `capture_plugin_output(..., mode="direct")` 不做人格化，直接投递
- `capture_plugin_output(..., mode="persona")` 先重写后投递
- persona 重写失败时降级 direct
- `plugin_direct` / `plugin_persona` 都会记录 visible output
- finalized material 中包含插件输出
- plugin output 不会触发 `result_is_model_result=True` 路径
- plugin output 不会错误使用 `core_reply` message kind

### 如果执行 Layer 2

新增平台签名兼容测试：

- 选至少两个平台事件类做代表测试：
  - `WebChatMessageEvent`
  - `TelegramMessageEvent` 或 `AiocqhttpMessageEvent`
- 验证 `await event.send(message, persona=True)` 不报参数错误

## 验收标准

本轮完成后，以下行为必须成立：

1. 旧插件 `await event.send(message)` 仍可工作。
2. 在 interaction middleware 启用时，插件输出经过统一 Output Runtime。
3. direct 模式不改写文本。
4. persona 模式可以改写文本，失败时降级 direct。
5. 插件输出被记录进 visible outputs 和 finalized material。
6. core first response、core final reply、core streaming 行为不回归。
7. 不需要本轮修改所有平台 event 类。
8. plugin output 不会污染 core output origin 状态。
9. middleware 关闭时，helper fallback 不会递归。

## 推荐提交拆分

推荐分成三个提交或三个 AI 子任务：

1. 数据结构和 event helper
   - `output_modes.py`
   - `AstrMessageEvent.emit_output / send_direct / send_persona`

2. middleware 分流
   - `_install_core_output_interceptor`
   - `send_wrapper` origin 判断

3. Output Controller 接管 plugin output
   - `capture_plugin_output`
   - persona rewrite helper
   - visible output / finalized material / tests

## 给执行 AI 的最后约束

如果你是执行这份命令书的 AI，请遵守：

1. 先做 Layer 1，不要直接做 Layer 2。
2. 如果你发现需要批量修改十几个平台子类，说明你越界了，先停。
3. 任何时候都不要把插件主动输出当成 core final result 复用。
4. 人格化失败必须降级 direct，不能丢消息。
5. 每完成一层都先补测试，再继续下一层。
