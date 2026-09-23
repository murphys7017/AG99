# 插件能力模型与统一准入方案（修正案）

状态：**历史方案与审计记录**。2026-09-18 用户批准开发期破坏性收口，当前实施契约见 [插件能力收口契约](./plugin-capability-cleanup.md)。
下文关于保留两张目标配置和独立 Prompt Contributor 的限制已被取代，不再作为当前实施依据；历史发现保留用于追溯。

本文是对一份外部提案（“插件能力生效位置配置界面”）的复核与修正。核心结论是：
**提案的意图成立，但它的机制与项目已冻结的设计决策直接冲突，必须先分离“准入”与“目标”
两个概念，否则无法落地。**

---

## 0. 摘要

1. 提案要的“用户可审查、可修改的插件能力清单”是对的，而且正是当前缺失的东西。
2. 但提案把这件事做成“一个新的 `plugin_capability_targets` 目标映射，把两个旧配置项迁进去”，
   这一步与已冻结决策冲突（详见 §2.2）：两个旧映射被明确限定为“只覆盖 LLM 生命周期 Hook /
   只覆盖 FunctionTool”，且被明确禁止扩展到 Pipeline Handler、Prompt Extension、Persona Effect；
   同时明文禁止“第二套 Collector target 配置”和“按插件超时”。
3. 修正方向：把提案里的诉求拆成两个正交的东西——
   - **准入（admission）**：这个能力本轮允不允许用。这是官方准入基线的一部分，新增它不违反任何冻结决策。
   - **目标（target）**：这个能力由 Personal 还是 Core 消费。这是已冻结的两个映射，本期不改语义。
4. 真正必须先修的**不是配置，而是准入管线的断点**（见 §1.1）：会话级禁用只作用于消息 Handler；
   四个 Interaction 注册表连 `plugin_set` 都不读；Persona Effect 的归属判定对“插件用 Core 的类注册”
   这种官方推荐写法直接失效。加配置层不能修这些，只会让第八个“各自判断”的注册表出现。
5. 建议顺序：**Owner 显式化 → 唯一准入裁决点 → 能力清单 API/UI → （可选，需修宪）统一 target 配置**。

---

## 1. 复核基线（事实）

### 1.1 注册面 × 准入轴 现状矩阵

四个准入轴：

- **A** 全局启用 `star_map[owner].activated`
- **B** 全局白名单 `plugin_set` → `event.plugins_name`
- **C** 会话级 `session_plugin_config.disabled_plugins`（`astrbot/core/star/session_plugin_manager.py`）
- **D** 目标映射 `plugin_runtime_targets` / `plugin_tool_targets`（`astrbot/core/plugin_runtime.py`）

| 能力面 | 存储 | 消费点 | A | B | C | D |
| --- | --- | --- | --- | --- | --- | --- |
| 消息 Handler | `star_handlers_registry` | `pipeline/waking_check/stage.py:110,177` | ✅ | ✅ | ✅（唯一一处） | — |
| LLM 生命周期 Hook | `star_handlers_registry` | `pipeline/context_utils.py:92-102` | ✅ | ✅ | ❌ | ✅（限 `execution_surface` 传入处） |
| 输出 Hook `on_decorating_result` | 同上 | `output_lifecycle.py:67` | ✅ | ✅ | ❌ | — |
| FunctionTool | `provider_manager.llm_tools` | `capabilities.py:404,406`；`astr_agent_tool_exec.py:324,345,356` | ✅ | ✅ | ❌ | ✅ |
| Prompt Extension Collector | `Context._prompt_extension_collectors` | `prompt/context_collect.py:471-496` | ✅ | ❌ | ❌ | — |
| Interaction Prompt Contributor | `Context._interaction_prompt_contributors` | `interaction/context_builder.py:768` | ✅ | ❌ | ❌ | — |
| Interaction Result Contributor | `Context._interaction_result_contributors` | `interaction/output_controller.py:1925` | ✅ | ❌ | ❌ | — |
| Interaction Stream Decider | `Context._interaction_stream_deciders` | `interaction/output_controller.py:1501` | ✅ | ❌ | ❌ | — |
| Interaction Lifecycle Observer | `Context._interaction_lifecycle_observers` | `interaction/lifecycle.py:84` | ✅ | ❌ | ❌ | — |
| Persona Effect | `Context._persona_effects` | `interaction/expression_agent.py:1654` | ⚠️ 见 §1.3 | ❌ | ❌ | — |
| Runtime Observation Sensor | `Context._runtime_observation_sensors` | 提交路径准入 `context.py:922`；目标查询 `context.py:647` | ✅ | ❌ | ❌ | — |
| 直接发送 | — | `event.send` / proactive dispatcher | ❌ | ❌ | ❌ | — |
| 事件注入（第二注入面） | — | `Context.get_event_queue()`；`StarTools.create_event` → `adapter.commit_event` | ❌ | ❌ | ❌ | — |
| 直接适配器发送 | — | `StarTools.send_message_by_id` | ❌ | ❌ | ❌ | — |
| Postprocessor | `core/postprocess` | `postprocess/manager.py:98-109` | ❌ | ❌ | ❌ | — |
| Web API | `Context.registered_web_apis` | `dashboard/server.py:63-67,205-207` | ❌ | ❌ | ❌ | — |
| Provider / 适配器注册 | `provider_manager` / 平台 map | `register_provider` :1673；`register_platform_adapter` / `register_provider_adapter` | ❌ | ❌ | ❌ | — |
| 全局任务 | `Context._register_tasks` | `core_lifecycle.py:511` | ❌ | ❌ | ❌ | — |
| Cron 任务 | `cron_manager` | `core/cron/manager.py:128,155` | ❌ | ❌ | ❌ | — |
| Agent / Handoff 工具 | `llm_tools.func_list` | `register/star_handler.py:728,755` | ❌ | ❌ | ❌ | — |
| 插件页面 | `StarMetadata.pages` | `dashboard/routes/plugin.py:402-432,785-791` | ❌ | ❌ | ❌ | — |

`❌` 表示该轴在该消费点**完全未被查询**。前 12 行是“有人管一部分”的能力面；
**最后 8 行是任何准入轴都不经过的面**（详见 §1.4）。

“A/B/C/D 全 ✅”只出现在消息 Handler 一行；“❌”表示该轴在该消费点**完全未被查询**。

四个 Interaction 注册表的准入判定集中在 `context.py`：

- `_is_prompt_extension_collector_active`（:1531）
- `_is_interaction_contributor_active`（:1546，四类 Contributor 共用）
- `_is_persona_effect_active`（:1432）
- `_is_runtime_observation_sensor_active`（:1449）

四者都只读 `star_map[...].activated`，且 `list_prompt_extension_collectors()`、
`list_interaction_prompt_contributors()` **签名里根本没有 `event`**，因此结构上无法读 B。

已实测确认（`plugin.activated=True`、`event.plugins_name=["some_other_plugin"]`）：

```
prompt_extension_collectors      : ['FakeCollector']
interaction_prompt_contributors  : ['FakeContributor']
persona_effects                  : ['fake_plugin.effect']
```

### 1.2 两个旧配置项的真实覆盖面

`plugin_runtime_targets`：

- 解析：配置 → 插件声明 `interaction_runtime_target` → 默认 `personal_expression`
  （`plugin_runtime.py:84-95`、`plugin_runtime.py:166-176`）。
- 施加点只有一处：`context_utils.py:97` 的 `call_event_hook`，且仅当调用方显式传了
  `execution_surface`。
- 生产路径的传入者只有 `agent_lifecycle.py`（`:116,133,148,173,182,212,230`，
  `execution_surface=self.execution_surface`），其 surface 由入口决定：
  `internal.py:418-425` 与 `third_party.py:358-366` 为 CORE，`expression_agent.py:994-1005,1312`
  为 PERSONAL_EXPRESSION。
- `astr_agent_hooks.py:24,39,46` 虽然也传 surface，但该模块**已不是生产 owner**，仅作旧外部导入兼容面
  （`docs/Yakumo/current-state.md:193`、`docs/Yakumo/modules/agent.md:41`）。引用它作为施加点会误导。
- **非 Interaction 轮次直接返回 `True`**（`plugin_runtime.py:34-36`），即旧串行路径不受该配置约束。
- **只有 5 个 EventType 真正可被该配置路由**：`OnWaitingLLMRequestEvent`、`OnLLMRequestEvent`、
  `OnLLMResponseEvent`、`OnAgentBeginEvent`、`OnAgentDoneEvent`（外加 `agent_lifecycle` 派发的
  `OnUsingLLMToolEvent` / `OnLLMToolRespondEvent` 与硬编码 persona 的
  `OnPersonaExpressionResultEvent`）。**18 个 EventType 中有 10 个不经过该配置**（见 §1.4 表）。

`plugin_tool_targets`：

- 解析：`插件.工具` 精确项 → 插件项 → 工具 `tool_targets` 声明 → 默认 `{core}`
  （`plugin_runtime.py:112-133`、`agent/tool.py:19-58`）。
- 施加点：`capabilities.py:404`、`astr_agent_tool_exec.py:324,345,356`。

结论：提案所说“只覆盖部分 Hook 和工具，不够形成完整兼容配置”**成立**。

### 1.3 已有设计意图：你的两个决定其实已经被文档采纳

这是本次复核最重要的发现之一。

- **Effect 归属**：`docs/Yakumo/dev/interaction-output-plugin-contract.md:140` 明确
  “effect 名称和参数 schema 由注册插件拥有，Core 不为具体插件增加专用字段”；
  `parallel-plugin-runtime-plan.md:105` 把 Persona Effect 归属写为 **Personal**。
  ⇒ 你选择的“注册时显式记录真正的 owner”正是文档既定意图，不是新设计。
- **Effect 适用性**：同文件 `:141` “插件应通过 `event_filter` 声明平台、设备和运行时适用性”。
  ⇒ 你选择的“准入只看客户端/运行时是否在线”同样已是既定意图，`event_filter` 就是那个裁决点。
- **Effect 不是工具**：`docs/zh/dev/star/guides/persona-effects.md:9`
  “Persona Effect 不是 Agent Tool……仍是声明式输出，不是可执行工具”。
- **会话级准入是官方准入的一部分**：`parallel-plugin-runtime-plan.md:85`
  “权限、session plugin filtering、Handler 优先级和执行顺序不变”。
  ⇒ 会话过滤被官方承认，但它被写在**Handler 语境**里，并未声明适用于其他能力面（这是缺口，见 §8-Q2）。

同时存在两个**尚未决定**的空白（原文即如此）：

- `runtime-function-unification-plan.md` §二 D-001..D-015 冻结表里，没有“插件的 hard/soft 契约分级”。
  全仓文档中不存在“必发 vs 可选”的插件贡献分类。你设计的“必发 effect”需要被正式写进文档。
- `parallel-plugin-runtime-plan.md:1043-1053` §十九 非目标第 5 条：
  “不修改 `plugin_runtime_targets` 或 `plugin_tool_targets` 体系”；
  `runtime-function-unification-plan.md:126`：“不改变 `plugin_runtime_targets` 和
  `plugin_tool_targets` 的含义、默认值和优先级”。

### 1.4 完全不受任何准入轴约束的面（本次复核新增，重要）

以下能力面**不经过 activated、plugins_name、会话禁用中的任何一个**，插件停用/会话禁用对它们
没有任何影响：

| 面 | 注册点 | 消费点 | 风险 |
| --- | --- | --- | --- |
| 事件注入 | `Context.get_event_queue()` :1621；`StarTools.create_event` :168-203 | `core/event_bus.py:43`；`adapter.commit_event` | **可绕过整条 input→Personal→Output 链路**，且无法归属到插件 |
| 直接适配器发送 | `StarTools.send_message_by_id` :86-120 | 直接调 adapter bot，绕过 `Context` | 绕过统一 Output Runtime 与 `final_text` 约束 |
| Postprocessor | `core/postprocess/__init__.py:22` | `postprocess/manager.py:98-109`（`ON_LLM_RESPONSE` / `AFTER_MESSAGE_SENT` / `AFTER_TURN_COMPLETED`） | 可在回复链路上任意改写 |
| Web API | `Context.register_web_api` :1593 | `dashboard/server.py:63-67,205-207` | 插件停用后路由仍可命中的面 |
| Provider / 适配器注册 | `Context.register_provider` :1673；`register_platform_adapter`、`register_provider_adapter` | provider/平台 map | 停用不回收 |
| 全局任务 | `Context.register_task` :1774 | `core_lifecycle.py:511` | `star_manager.py:1818-1831` 卸载时**不清理**，确定泄漏 |
| Agent / Handoff 工具 | `register/star_handler.py:728,755` | `llm_tools.func_list` | 绕过 `plugin_tool_targets` 的工具目标解析 |
| Cron / 插件页面 | `cron_manager`；`StarMetadata.pages` | cron scheduler；dashboard | 停用不回收 |

> 注意 `Context.activate_llm_tool`（:467）**是**被 gate 的
> （`func_tool_manager.py:1002-1006` 会检查 `star_map[...].activated`）——说明“门禁”在仓库里是零散的，
> 不是统一设计的结果。

### 1.5 三个已确认的实现/文档不一致（需要决策，不是笔误）

1. **`OnCallingFuncToolEvent` 从未被派发**。全仓只有注册锚点（`register/star_handler.py:688`）和
   dashboard 展示（`plugin.py:165,1532`），没有任何 `call_event_hook` / `get_handlers_by_event_type`
   调用它。注册了该监听的插件永远不会被触发。
2. **`OnUsingLLMToolEvent` / `OnLLMToolRespondEvent` 实际上被目标过滤**。
   `agent_lifecycle.py:207-213,224-231` 明确传了 `execution_surface=self.execution_surface`，
   因此在 Interaction 轮次内**会**受 `plugin_runtime_targets` 约束；
   但 `docs/Yakumo/modules/agent.md:35`、`docs/Yakumo/modules/interaction.md`、
   `docs/zh/dev/star/plugin.md:520` 都写“保持全局工具观察语义，**不受**请求生命周期目标过滤”。
   文档与代码矛盾，二选一必须明确——这会直接影响插件观察类 Hook 的行为。
3. **`plugin_set` 有一处读错了位置**。`prompt/collectors/skills_collector.py:91` 从
   `config.provider_settings` 里读 `plugin_set`，而 `plugin_set` 是**顶层**键
   （`config/default.py:353`），`provider_settings` 下不存在它 ⇒ 永远回退 `["*"]`，
   即“按 plugin_set 过滤插件技能”这条逻辑实际从未生效。

### 1.6 直接发送无法归属到插件（已确认）

`event.send` / `emit_output` / `Context.send_message` / `send_message_by_id` 全链路**
没有任何插件身份**：无 `handler_module_path`、无插件 ContextVar、发送时不查 `star_map`。
`output_adapter.py:59-83` 只按 `OUTPUT_ORIGIN_EXTRA_KEY`（Core vs 插件）分支，
`personal_runtime.py:1939` 的 `source` 是硬编码字面量 `"plugin.context.send_message"`。

唯一可用的部分机制：`star_request.py:61-68` 在 `call_handler` 前后设置了
`_interaction_plugin_handler_module_path` / `_interaction_plugin_handler_name`（`finally` 清理），
但只有 `plugin_branch.py:190-206` 在读它来给 `PluginBranchResult` 打 `origin_plugin_id`。
**做“直接发送”清单必须先把这个归属补上**，否则 §3.2 里的 `direct_output` 只能靠静态扫描猜测。
同类无门禁入口还有 `event.react`（`astr_message_event.py:817-824`）、
`event.send_message_with_extras`（`:424-454`）、`event.send_interaction_streaming`（`:456-472`）。

### 1.7 卸载清理矩阵与两处结构性障碍

`star_manager.py:1818-1831` 的 `_remove_plugin_runtime_extensions` **只清理 7 个 Context 注册表**
（prompt collector、四类 interaction contributor、persona effect、runtime sensor）。
**不清理**：`register_web_api`、`register_provider`、`register_task`、postprocessor。
加上 §1.4 的 `register_task` 不回收，插件反复 reload 会累积残留。

两处会影响实施的结构性事实：

- **Runtime Observation Sensor 没有 `list_*` 访问器**（只有 `:828`、`:872`、`:909-916` 的内部读）。
  要做“能力清单”必须先补一个带准入的读取入口，否则清单里看不到它。
- **dashboard 目前完全不读这 7 个注册表**（对
  `prompt_extension_collectors|persona_effects|interaction_stream_deciders|interaction_lifecycle_observers|runtime_observation_sensor`
  在 `dashboard/src` 下 0 命中）。现有的
  `routes/plugin.py:1431` 组件清单只覆盖 `star_handlers_registry` + 页面 + skill，
  所以 §5 的能力 UI 是**新增面**，不是改现有面。
- 另有一条需要单独确认的旁路：`func_tool_manager.py:487-500` 的 `get_full_tool_set`
  **不带 target 过滤**。如果它被非 Core 路径调用，会成为绕过 `plugin_tool_targets` 的漏洞，
  需在 A2 阶段确认所有调用方。

---

## 2. 对提案的评审

### 2.1 成立的部分

| 提案主张 | 判定 |
| --- | --- |
| 应按“能力”而非“整个插件”配置 | ✅ 成立，与 `parallel-plugin-runtime-plan.md:99-105` 的能力归属表一致 |
| Handler 默认 Pipeline | ✅ 成立，且是冻结决策 D-007，不可迁移 |
| Prompt / 人格 Hook / Effect 默认 Personal | ✅ Hook 默认 `personal_expression` 成立（D-003）；Effect 硬绑 Personal 成立 |
| 可执行 FunctionTool 默认 Core | ✅ 成立，D-003 |
| 同一能力不得同时对 Personal 和 Core 可见 | ✅ 成立，对**可执行**能力是硬要求；对只读事实需放宽（见 §3.7） |
| 推测配置必须可被用户确认，不能当事实 | ✅ 成立，与 `全局架构审计` §8“概念漂移”一致 |
| 需要一套底层可迁移的配置模型 | ✅ 意图成立，但机制需改（§2.2） |

### 2.2 与已冻结决定冲突的部分（必须先决策，不能直接实现）

这三条是**阻塞项**，不是风格问题：

1. **把两个旧映射迁进新的统一 `plugin_capability_targets`**
   —— 直接违反 `runtime-function-unification-plan.md:126` 与
   `parallel-plugin-runtime-plan.md:1049`。
2. **让新映射同时管 Pipeline Handler / Prompt Extension / Persona Effect 的“位置”**
   —— 直接违反 `parallel-plugin-runtime-plan.md:107-108`：
   “`plugin_runtime_targets` 和 `plugin_tool_targets` 不控制 Pipeline Handler、Prompt Extension 或
   Persona Effect”。其背后的既定归属是：Handler→官方 Pipeline（D-007）、
   Prompt Extension→自身 `meta.targets`、Effect→Personal。
3. **为 Prompt Extension 增加目标配置**（提案的表里 “人格 Prompt 扩展 -> Personal” 隐含可配置）
   —— 违反 `parallel-plugin-runtime-plan.md:117-118`：
   “不得为了降低等待再增加第二套 Collector target 配置、插件预判模型或按插件超时”。

另外两条与提案的“直接发送 → 兼容模式”和“未识别能力 → 禁用”相关，但方向应改：

4. **直接发送**不需要新枚举值。`interaction-output-plugin-contract.md:147-155` 已经给出既定治理：
   输出贡献必须声明 `stage` 和 `latency_class`（`bounded` / `deferred`），
   表现类插件默认不得改写 `final_text`，主动输出须逐步收口到 Interaction output queue。
   所以这一行的正确表达是“**迁移进度**”而不是“位置选择”。
5. **未识别能力默认禁用**有风险。已知注册面是闭合集合，可穷举分类；真正无法分类的是绕过所有
   注册表的代码（例如直接调 `event.send`、自建全局任务、monkeypatch）。对这类情况
   “推测→自动禁用”会静默破坏旧插件，与 D-008“旧插件不需要改代码，兼容由 Runtime 承担”相悖。
   应改为“**标记待确认 + 告警**”，不自动禁用。

### 2.3 两处概念混淆（修正的关键）

提案把两件事合成了一件事：

| 概念 | 问题 | 已冻结？ |
| --- | --- | --- |
| **目标 target**：由 Personal 还是 Core 消费 | 两个旧映射已冻结，且被禁止扩展 | 是 |
| **准入 admission**：本轮允不允许它出现 | 官方准入基线的一部分，**当前实现有断点** | 否，可以新增 |

提案的 `plugin_capability_targets` 里 `"handler": "pipeline"`、`"direct_output": "compatibility"`
这两个值**不是 target**，而是“这类能力走哪条链路/是否还在兼容路径”，属于准入与迁移状态；
`"prompt": "personal"` / `"effect": "personal"` 则根本不是可配置项（前者由 `meta.targets` 决定，
后者硬绑 Personal）。把四类语义塞进一个 `target` 字段，是这个提案无法落地的根因。

---

## 3. 修正后的模型

### 3.1 四层分离

```
① Owner     谁注册了它            → 必须显式记录，禁止从 type(x).__module__ 推断
② Kind      它是什么能力          → 闭合词表，见 §3.2
③ Admission 本轮允不允许它出现    → 唯一裁决点，见 §3.3
④ Target    由 Personal 还是 Core 消费 → 沿用两个冻结映射，本期不改语义
```

四层各自有唯一 owner，互不越权。③ 是本次要新建的东西；④ 是既有的，不动。

### 3.2 能力类型闭合词表

按实际注册面穷举（与 §1.1 一一对应）：

| kind | 含义 | 默认目标 | 目标可选？ |
| --- | --- | --- | --- |
| `handler` | 接管输入的命令/关键词/事件 | `pipeline` | 否（D-007） |
| `llm_hook` | LLM/Agent 生命周期钩子 | `personal_expression` | 是（`plugin_runtime_targets`） |
| `tool` | 模型可调用的 FunctionTool | `core` | 是（`plugin_tool_targets`） |
| `prompt_extension` | 事实贡献，按 `meta.targets` 投影 | 由 `meta.targets` 决定（默认 `core`） | 否（D3/D4） |
| `interaction_prompt` | Interaction 提示贡献 | `personal_expression` | 否 |
| `interaction_result` | 结果贡献 | `personal_expression` | 否 |
| `stream_decider` | 流式决策 | `personal_expression` | 否 |
| `lifecycle_observer` | 只读生命周期观察 | `observe` | 否 |
| `persona_effect` | 结构化表现契约 | `personal_expression` | 否（硬绑，D12） |
| `runtime_sensor` | 只读运行时观察 | `observe` | 否 |
| `direct_output` | 插件直接发送 | `compatibility` | 否；按 `stage`/`latency_class` 迁移 |

第二组：**不参与 Personal / Core 分派**（对应 §1.1 后 8 行与 §1.4）。
它们没有“目标”概念，**也大多不能套用会话级准入**——它们的生命周期不是“某一轮”，
而是“插件启用/运行期间”。它们必须按三种治理方式分批处理，不能塞进同一个
`CapabilityAdmissionSnapshot`：

| kind | 治理方式 | 现状 | 归属阶段 |
| --- | --- | --- | --- |
| `event_injection` | 插件所有权 + 运行时准入 | 无门禁 | 第 7 阶段（最高优先，见 §1.4） |
| `direct_output` | 输出兼容状态 + 迁移告警 | 无归属 | 第 7 阶段 |
| `postprocessor` | 显式声明“是否允许影响最终材料” | 无门禁 | 第 7 阶段 |
| `agent_tool` | 纳入工具能力授权（并入 `tool`） | 绕过目标解析 | 第 3 阶段（随 Tool 准入） |
| `web_api` | 插件启用/卸载级别 | 无门禁 | 第 7 阶段（生命周期组） |
| `provider` / `platform_adapter` / `provider_adapter` | 进程级或实例级生命周期 | 无门禁 | 第 7 阶段（生命周期组） |
| `global_task` | 启动/卸载/回收管理 | 无门禁，卸载不清理 | 第 7 阶段（生命周期组） |
| `cron_job` | 定时任务启用与回收 | 无门禁 | 第 7 阶段（生命周期组） |
| `plugin_page` | 插件启用级别（仅展示） | 无门禁 | 第 7 阶段（低优先） |
| `management_hook` | `OnAstrBotLoaded` / `OnPlatformLoaded` / `OnPluginLoaded` / `OnPluginUnloaded` / `OnPluginError` | 白名单豁免是有意的 | 不纳入会话准入 |

> **语义边界**：`web_api`、`provider`、`platform_adapter`、`global_task`、`cron_job`
> 不进会话准入快照。否则会出现“会话禁用了插件，但全局 Provider/定时任务仍在跑”这种自相矛盾的状态——
> 会话是轮次级概念，而这些是进程级或插件生命周期级概念。它们要的是
> **启用 / 停用 / 卸载 / 回收**，以及 §1.7 指出的回收缺口。

`observe` 与 `compatibility` 是**准入状态**而不是 target 值，界面上单独一列。
第二组在界面上归入“生命周期与其他能力（不参与对话分派）”分区，只暴露启用/停用与回收告警。

### 3.3 唯一准入裁决点

新建 `astrbot/core/plugin_admission.py`：

```python
@dataclass(frozen=True, slots=True)
class CapabilityRef:
    owner_module_path: str      # ①
    kind: CapabilityKind        # ②
    item_name: str | None = None  # 工具名 / effect 名 / collector class 名

@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    allowed: bool
    reason: str      # activated_false / not_in_plugin_set / session_disabled /
                     # reserved / hard_contract / no_adapter_runtime / declared_off
    source: str      # config / declaration / default
    target: str | None

def resolve_capability_admission(event, ref: CapabilityRef) -> AdmissionDecision: ...
```

裁决规则（按 kind 的策略表，而不是一刀切）：

```
若 owner 缺失              → 允许（兼容：无元数据的注册视为系统能力）
若 owner.reserved          → 允许，跳过 B/C
若 not owner.activated     → 拒绝（reason=activated_false）
若 kind 属于 Interaction 面 且 B 可用 → 检查 event.plugins_name
若 kind 声明为 hard        → 跳过 C，改由该能力自身的 event_filter 决定（见 §3.6）
否则若 C 命中              → 拒绝（reason=session_disabled）
目标解析：kind 有 target 语义的走 §3.5，否则 target=None
```

关键点：**能力面差异必须由策略表表达，不能靠每个消费点自己 if**。四类 Contributor、
Collector、Effect、Sensor 的 `list_*` 方法改为接收 `event` 或接收已冻结的准入快照（§3.4）。

### 3.4 每轮一次冻结

`list_*` 目前是“每次调用现算”。应改为在 turn 建立时解析一次，冻结成
`CapabilityAdmissionSnapshot`，挂在 `InteractionTurnState` 上（`_interaction_enabled` 已有先例，
`middleware.py:301`）。这样：

- 同一轮内不会因为插件中途 reload 而前后不一致；
- 会话配置只在轮次边界读取一次，避免每轮多次读 `sp.get_async`；
- 为 `plugin_set`（只有 `event` 上有）提供统一入口。

**作用域限制**：快照**只覆盖 Interaction 能力**（§3.2 第一组，外加 `event_injection` /
`direct_output` / `postprocessor`）。进程级与插件生命周期级能力（`web_api`、`provider`、
`platform_adapter`、`global_task`、`cron_job`）**不进快照**，按 §3.2 第二组另行治理。

**两个实施约束**：

- `runtime_observation_sensor` **没有 event**（其提交路径是 `context.py:889-961`，只有
  `session` 参数，见 §1.1）。因此它的会话级准入不能读 `event.plugins_name`，
  只能按 `_resolve_runtime_observation_session(session)`（`:925`）解析出的目标会话去查
  `session_plugin_config`。这是 Q2 说“扩展到 Sensor”时必须单独处理的例外。
- 快照需要在 **Runtime Observation 与主动表达** 创建时也冻结同一份有效选择，
  否则这两条路径会绕过会话配置（对应 §8-Q2 的落地要求）。

### 3.5 Owner 显式化（根因修复，必须先做）

问题：`context.py:1038-1041`、`:1213-1218`、`:1365-1372` 以及 sensor 注册处，
都用 `type(obj).__module__` 反推 owner。对“插件在自己模块里定义子类”的写法侥幸可用；
对官方推荐的“直接用 Core 的类实例”写法（`AG99Live` 的
`core_compatibility.py:54` → `persona_effect_spec=PersonaEffectSpec`）则退化为
`astrbot.core.interaction.effects`，`star_map` 查不到 → `_is_persona_effect_active` 无条件 `True`。

实测：

```
owner_module_path : astrbot.core.interaction.effects
star_map hit?     : None
listed despite activated=False -> ['ag99live.motion']
unregister_persona_effects(module_prefix='data.plugins.astrbot_plugin_ag99live_adapter') removed -> 0
```

修复（不触碰公开插件 API，符合 D-008）：

1. `Context` 增加加载期环境量，例如
   `context.plugin_load_scope(module_path)` 上下文管理器。
2. `StarManager` 用它包住两次“执行插件代码”的位置：插件类实例化
   （`star_manager.py:1238-1249`）和 `initialize()`（`star_manager.py:1330`）。
   只包 `initialize()` 不够——`AG99Live` 在 `MyPlugin.__init__` 里注册（`main.py:53`）。
3. 四个注册表 + `_normalize_plugin_owner_module` 优先取环境量，取不到再回退现有推断
   （保护 builtin/核心侧与懒注册插件）。
4. 顺带修好按 `module_prefix` 卸载清理（`star_manager.py:1830`）。

收益是根因级的：同时修复停用判定、卸载清理、同名 effect 冲突，
以及“未来任何用 Core 提供的实例注册的能力”。

### 3.6 hard / soft 契约分级（填补 O12 空白）

文档目前没有插件贡献的 hard/soft 分级。建议正式引入，写在 `PersonaEffectSpec.metadata` 已有基础上：

| 分级 | 声明 | 准入（Permission） | 适用性（Applicability） | 失败/超时行为 |
| --- | --- | --- | --- | --- |
| `hard` | `required_per_segment: true` | **照常受约束**：全局启用、所有权、`plugin_set`、会话禁用 | 自身 `event_filter`（平台/设备/客户端在线） | 缺失即轮次失败（`expression_agent.py:296-312`） |
| `soft` | 默认 | 受约束 | 自身 `event_filter` | 记录诊断后跳过（现有 Contributor 行为） |

**关键修正**：`hard` 豁免的只是“**软丢弃**”（超时/失败/`best_effort` 跳过），
**不是豁免准入**。此前版本写的“hard 不受 B/C 约束”是错的，它会让用户在界面上禁用了动作插件后，
模型仍然收到 `required_per_segment` 契约，配置即可信度归零。

据此把两件事彻底分开（这是 §3.1 四层的进一步细化）：

```
Permission   = 插件允不允许施加这个契约
             = activated ∧ ownership ∧ plugin_set ∧ ¬session_disabled
Applicability = 本轮这个事件需不需要它
             = event_filter（平台 / 设备 / olv_pet_adapter 是否在线）
hard effect 必须同时满足两者；仅豁免软丢弃。
```

这条与“启用插件 ⇒ 每轮都必须有 motion”并不矛盾：启用/在线决定 Applicability，
准入决定 Permission，两者都通过才生效。

**待确认**：先前单独确认过“Effect 准入只看客户端适配器/运行时是否在线”，
与本文现在写下的“Permission 还要叠加 `plugin_set` 与会话禁用”不一致（见 §8-Q10）。
需要你明确取哪一个；我倾向现在这个写法（两条件同时成立），因为它与 Q2 保持自洽。

### 3.7 互斥与冲突规则

- **可执行能力**（`tool`、`persona_effect`、`direct_output`）：**同一轮只允许一个目标**。
  工具当前已是单值（`tool_supports_target`），保持。
- **只读事实**（`prompt_extension`）：`meta.targets` 允许同时含 `persona` 与 `core`，
  **不应**施加互斥。同一事实同时供两个面消费不产生重复执行或权限问题，提案的
  “重复调用、结果重复写入”风险不适用于只读项。这是对提案的一处收紧修正。
- **同名冲突**：`_ensure_persona_effect_name_available`（`context.py:1416`）目前是全局唯一名，
  一旦 owner 修好，卸载清理失败导致的残留会阻塞其他插件注册同名 effect。
  §3.5 修完后此风险自然消除。

---

## 4. 配置模型

### 4.1 新增：准入配置（不违反任何冻结决策）

```json
{
  "interaction_middleware": {
    "plugin_capability_admission": {
      "astrbot_plugin_ag99live_adapter": {
        "handler": "enabled",
        "llm_hook": "enabled",
        "tool": "enabled",
        "persona_effect": "hard",
        "direct_output": "compatibility"
      }
    }
  }
}
```

值域：`enabled` / `disabled` / `compatibility` / `hard`（仅 effect）/ `inherit`。
**注意这里没有 target 值**，因此不触碰 D1–D3、D4。缺省 `inherit` ⇒ 回到默认规则。

### 4.2 保持不变：两个 target 映射

`plugin_runtime_targets` 与 `plugin_tool_targets` 的语义、默认值、优先级、键规则全部不动。
新 UI 只是把它们**读出来展示**在同一张能力表里。

### 4.3 如果确实要做成统一 target：修宪清单

若你坚持提案原样（一个 `plugin_capability_targets` 统一决定所有能力的位置），
必须先显式修改以下文档条目，不能绕过：

| 需修改 | 位置 | 为什么必须明确改 |
| --- | --- | --- |
| 非目标“不修改两个 target 体系” | `parallel-plugin-runtime-plan.md:1049` | 否则新键属于违规新增 |
| 非目标“不改变含义/默认值/优先级” | `runtime-function-unification-plan.md:126` | 迁移键会改变读取路径 |
| 能力边界“不控制 Handler/Prompt Extension/Effect” | `parallel-plugin-runtime-plan.md:107-108` | 提案正是要控制这三者 |
| 禁止“第二套 Collector target 配置” | `parallel-plugin-runtime-plan.md:117-118` | 提案给了 `prompt` 一个 target |
| 非目标“不改公开插件 API” | `parallel-plugin-runtime-plan.md:1047` | 若要求插件声明新字段则冲突 |

我的建议：**不要走这条路**。理由不是条款神圣，而是这三项本来就不需要“位置”配置——
Handler 恒为 Pipeline、Effect 恒为 Personal、Prompt Extension 由 `meta.targets` 自决。
给它们加 target 只会制造出“配置了一个语义上不可能的值”的新失败模式。

### 4.4 默认值总表

| kind | 默认准入 | 默认目标 | 来源 |
| --- | --- | --- | --- |
| `handler` | enabled | `pipeline` | D-007 |
| `llm_hook` | enabled | `personal_expression` | D-003 |
| `tool` | enabled | `core` | D-003 |
| `prompt_extension` | enabled | `meta.targets`（默认 `core`） | `context_collect.py:396-398` |
| `interaction_prompt` / `interaction_result` / `stream_decider` | enabled | `personal_expression` | `context_builder.py:100` |
| `lifecycle_observer` / `runtime_sensor` | enabled | `observe` | 现状 |
| `persona_effect` | enabled（`hard` 视为 hard） | `personal_expression` | D12 |
| `direct_output` | compatibility + 迁移告警 | — | `interaction-output-plugin-contract.md:147-155` |

---

## 5. 配置界面

已有可复用的地基，不必从零做：

- `dashboard/src/components/shared/ConfigItemRenderer.vue:45-53` 已有
  `select_plugin_set`、`plugin_runtime_target_map`、`plugin_tool_target_map` 三个 `_special` 分发。
- `dashboard/src/components/shared/PluginTargetMapEditor.vue` 已有
  `scope → target` 编辑对话框，`mode: 'plugin' | 'tool'`，`validTargets = {core, personal_expression}`。
- `astrbot/dashboard/routes/plugin.py:82-89` 已有组件类型序
  `page/skill/command/llm_tool/listener/hook`，`:1431-1460` 已在产出插件组件清单。
- `/api/plugin/get` 与 `/api/tools/list` 已能列出插件与插件工具（`PluginTargetMapEditor.vue:215-250`）。

改造方向：

1. **扩展清单 API**：把 `get_plugin_components_info`（`plugin.py:1431`）的分类从 6 类扩到
   §3.2 的 11 类，每项带 `kind`、`target`（只读展示）、`admission`（可编辑）、`owner_source`。
2. **新增 `_special: "plugin_capability_map"`** → 新组件 `PluginCapabilityEditor.vue`，
   按插件分组、按 kind 成行。字段严格分成两类：

   **用户可以配置：**
   - 是否启用该插件能力（`enabled` / `disabled`）
   - 是否允许当前会话使用（会话级覆盖）
   - 旧能力是否继续走兼容路径（`compatibility`）

   **系统只读展示：**
   - 能力类型（kind）
   - Owner（来源插件）
   - 固定消费方（Handler→Pipeline、Effect→Personal、Prompt Extension→`meta.targets`）
   - 当前来源（config / declaration / default）
   - 是否为硬依赖（`hard`）
   - 是否存在迁移告警

   界面形态：

   ```text
   AG99Live
     Handler             Pipeline             固定
     LLM Hook            Personal/Core        可配置
     FunctionTool        Core/Personal        可配置
     Prompt Extension    meta.targets         只读
     Persona Effect      Personal             固定（必发）
     Direct Output       Compatibility        迁移状态
   ```

   用户真正需要配置的只有三件事：旧插件的 Hook / 工具归属、旧式直接输出是否继续兼容、
   以及这个能力允不允许参与。Handler、Prompt Extension、Effect 的生效语义由系统契约决定，
   不给用户改——这比让用户直接改所有 target 更准确，也避免制造非法值。
3. **推测配置 + 确认**：清单中 `admission=inherit` 的项标记“推测”，
   在插件详情页与设置页显示“检测到 N 项未确认能力”，并在保存前不改行为。
4. **词表统一**：界面标签用 `Pipeline / Personal / Core / 兼容模式 / 只读观察`，
   但**存储值仍用 `core` / `personal_expression` 等既有英文枚举**，避免出现两套真值。
5. **未识别项**按 §2.2-5 显示为“待确认/需迁移”，**不**自动禁用。

---

## 6. 实施阶段

可并行的两条轨道。**轨道 B 是既有已复核缺陷，不依赖模型重构**。

### 轨道 A：能力模型与准入

| 阶段 | 内容 | 主要文件 | 风险 |
| --- | --- | --- | --- |
| A1 | Owner 显式化 + 按 module_prefix 卸载清理 | `star/context.py`、`star/star_manager.py` | 中 |
| A2 | 唯一准入裁决点 + 策略表；四个 `list_*` 接入 | 新建 `core/plugin_admission.py`、`star/context.py` | 中 |
| A3 | 每轮冻结快照 | `interaction/turn_state.py`、各消费点 | 中 |
| A4 | hard/soft 分级写入文档与 `metadata` 契约 | 文档 + `interaction/effects.py` | 低 |
| A5 | 清单 API 扩到 11 类 | `dashboard/routes/plugin.py` | 低 |
| A6 | 能力配置 UI + 推测确认 | 新建 `PluginCapabilityEditor.vue`、`ConfigItemRenderer.vue` | 低 |
| A7 | 准入配置键 + 校验（互斥、值域） | `core/config/default.py` | 低 |

### 轨道 B：已复核的具体缺陷

| 编号 | 缺陷 | 修法与约束 |
| --- | --- | --- |
| B1 | AG99Live 双注册 | **证据范围**：已确认真实双注册（`remote_operator.py:191-197`）；已用真实函数在**单测级别复现**冲突（抛 `PromptContextConflictError`）；**生产日志中尚未发生**，因为远程执行器配置当时不可用（4 条 `Remote operator prompt skipped: config_unavailable`，2026-09-17 01:45）。结论应表述为“确定存在的重复注册；当远程配置可用且两条路径同时返回扩展时必然触发冲突”。修法：只保留一条正式路径；若保留 Interaction Contributor 需显式 `targets=["core"]`（`context_builder.py:100` 的 `setdefault` 不覆盖显式值） |
| B2 | `wait_complete` 被 `compact_context` 绕过 | **按 D11 删除 `compact_context` 的语义角色**（`multimodal-turn-context-plan.md:233,354`），不要给它补配置 |
| B3 | 普通 Prompt Extension 串行且无总预算 | 两步：① `asyncio.gather` 并行（消除插件之间的串行等待，不违反 D4）；② 引入**所有普通插件贡献共享的 plugin-enrichment 总预算**，超时整体结束、保留已成功贡献、Personal 与 Core 复用同一结果。**不要**新增按插件 target 配置或按插件超时（D4）。预算必须表达为既有 `TurnDeadlineBudget`（`turn_timeout=120`）下的**阶段限额**，只能缩短不能延长（D19），不得成为第二个 deadline owner |
| B6 | §1.5 的三处代码/文档不一致 | **单独修复项，不混入能力模型第一阶段**（Q8）：`OnCallingFuncToolEvent` 从未派发；`OnUsingLLMTool`/`OnLLMToolRespond` 实际受目标过滤而文档称不受；`skills_collector.py:91` 从 `provider_settings` 读顶层 `plugin_set` 导致技能过滤从未生效 |
| B7 | `get_full_tool_set` 无 target 过滤（`func_tool_manager.py:487-500`） | 先确认所有调用方；若被非 Core 路径调用则构成绕过 `plugin_tool_targets` 的漏洞 |
| B4 | 会话级禁用只作用于 Handler | 属准入统一的一部分，随 A2 落地；同时修正会话页文案（`session-management.json:80`） |
| B5 | 四个注册表不读 `plugin_set` | 随 A2 落地 |

B1/B2/B3 与模型无关，可立即做。

---

## 7. 验证计划

按 `.ai/index.md`（不过度加测试，只验主路径边界）：

1. **准入真值表**：对每个 kind 断言 A/B/C 三轴的组合结果（一个表驱动用例）。
2. **Owner 回归**：用“直接用 Core 类实例注册”的 effect 复现当前失效，
   修后断言 `activated=False` 即消失、`unregister(module_prefix=...)` 生效。
3. **端到端**：会话禁用某插件后，确认该插件的 handler / hook / tool / contributor / effect
   全部不再参与（这是 B4 的验收）。
4. **不回归**：`plugin_runtime_targets` / `plugin_tool_targets` 的既有解析顺序与默认值
   行为不变（对齐 `tests/unit/test_interaction_plugin_runtime.py`）。
5. **清单 API**：断言 AG99Live 在界面上呈现为 handler / llm_hook / tool / persona_effect /
   direct_output 五行。

---

## 8. 决策记录与剩余待定

### 8.1 已确认的决策（评审给出，Q1–Q9）

| 编号 | 决策 |
| --- | --- |
| Q1 | **接受**准入与 target 分离 |
| Q2 | 会话级禁用扩展到 Interaction 相关能力：Hook、Tool、Contributor、Effect、Sensor |
| Q3 | **不增加** Prompt Extension 与 Effect 的 target 配置 |
| Q4 | 未识别能力**只告警并标记待确认**，不自动禁用 |
| Q5 | 保留 hard/soft，但 **hard 仍受全局启用、所有权和平台条件约束**（已按此改写 §3.6） |
| Q6 | **不接受**提案原样迁移 `plugin_capability_targets`；维持两个 target 映射的冻结语义 |
| Q7 | 八个新增面**分批处理**，不全部塞进同一个会话准入快照（已按此改写 §3.2/§3.4） |
| Q8 | 三处代码/文档不一致**单独建立修复项**（见 B6），不混入能力模型第一阶段 |
| Q9 | 给直接输出补 Owner 与迁移状态，但**不把 `compatibility` 当成 Personal/Core target** |

### 8.2 采纳的实施顺序

1. Owner 显式记录 + 卸载清理（§3.5）
2. Interaction 能力唯一准入裁决（§3.3）
3. 修 `plugin_set` 与会话禁用对 Hook/Tool/Contributor/Effect 的覆盖（B4/B5）
4. 消除 AG99Live 双注册（B1）
5. 插件富化并行 + 统一总预算（B3）
6. 能力清单 API 与配置界面（§5）
7. 单独治理事件注入、直接发送、全局任务、Cron、Web API、Provider 生命周期（§3.2 第二组）
8. 最后按实际使用情况清理兼容分支

### 8.3 剩余待定

----

下表 Q1–Q9 已由评审给出决策（见 8.1），保留原问题文本以便追溯。

| 编号 | 问题 | 决策 / 状态 |
| --- | --- | --- |
| Q1 | 是否采用“准入与 target 分离”（§3.1、§4.1-4.2） | **采用**。可落地、不违反任何冻结决策、覆盖提案 90% 诉求 |
| Q2 | 会话级 `disabled_plugins` 是否扩展到 Hook/Tool/Contributor/Effect | 扩到所有面（否则会话页文案是错的）；但这是新语义，需明确写入官方准入基线 |
| Q3 | 是否为 Prompt Extension / Effect 增加 target 配置 | **不加**。二者本就没有“位置”自由度，加了只会产生非法值 |
| Q4 | “未识别能力”默认禁用还是仅告警 | **仅告警 + 待确认**，与 D-008 一致 |
| Q5 | 是否把“hard/soft 插件贡献分级”写入冻结决策表 | **写入**，否则必发 effect 会被后续重构当软贡献丢弃 |
| Q6 | 是否接受 §2.2 的结论：提案原样需修改 5 处冻结条款 | 需你决定；若坚持原样，我按 §4.3 列出改动后再动手 |
| Q7 | §1.4 那 8 个“完全无门禁”的面（事件注入、直接适配器发送、postprocessor、web_api、provider/task/cron/handoff）要不要一并纳入准入 | **建议纳入**。事件注入与直接发送等于第二条输入/输出通道，是当前唯一能绕过 Personal 统一窗口的路径；至少要先补“停用即回收” |
| Q8 | §1.5 的三处不一致怎么定 | 逐条决定：`OnCallingFuncToolEvent`（补派发还是删枚举）、工具观察类 Hook 是否受目标过滤（改文档还是改代码）、`skills_collector` 的 `plugin_set` 读取位置（改代码） |
| Q9 | 要不要给 `direct_output` 补插件归属（§1.6） | **建议补**。否则“直接发送”清单只能靠静态扫描，且 §3.2 的 `compatibility` 状态无法可靠判定 |
| **Q10** | **【未定】** hard effect 的 Permission 到底含不含 `plugin_set` 与会话禁用？（§3.6） | 先前你确认过“Effect 准入只看客户端适配器/运行时是否在线”；Q2+Q5 则要求 hard 仍受全局启用/所有权/平台条件，且会话禁用扩展到 Effect。二者不一致。我倾向 **Permission = activated ∧ ownership ∧ plugin_set ∧ ¬session_disabled**，Applicability 另由 `event_filter` 决定，两者同时成立才生效——这样与 Q2 自洽，也不会出现“界面禁用了动作插件、模型仍被要求必发 effect”。需你确认 |

---

## 附：本次复核用到的实测复现脚本

保留在 `%TEMP%`（未进入仓库）：

- `verify_p1_3.py` — AG99Live 双注册槽冲突复现
- `verify_p1_1_p1_2.py` — 准入轴不对称
- `verify_p2_5.py` — 串行与超时对比
- `verify_effect_ownership.py` — effect 归属推断失效
- `verify_effect_cleanup.py` — 按 module_prefix 清理失效

---

## 10. 实施记录

### 第 1 阶段 — Owner 显式化与卸载清理（已完成）

- `astrbot/core/star/context.py`：新增加载期 `PluginOwnerScope`（`ContextVar` + 线程兜底，
  因为插件构造函数可能在 worker 线程执行）。四个注册表记录
  `owner_plugin_name` / `owner_source`，并统一走 `_resolve_registration_owner`：
  显式 scope 优先，取不到才回退类型模块推断。
- `astrbot/core/star/star_manager.py`：`plugin_owner_scope` 包住**构造函数**与 `initialize()`
  两处插件代码执行点（新旧两条加载路径都覆盖）。仅包 `initialize()` 不够——AG99Live 在
  `MyPlugin.__init__` 中注册。
- 新增 `Context.remove_capabilities_by_owner()` 与 `list_plugin_capability_owners()`。
- 验收（`verify_p1_owner_scope.py`）：停用后 Effect/Contributor/Collector 均不再列出；
  按 owner 清理可移除用 Core 类型注册的 effect；连续 3 次 reload 无残留；
  同名 effect 卸载后可重新注册。旧行为在同脚本中对照展示。

### 第 2 阶段 — 唯一准入裁决点（已完成）

新建 `astrbot/core/plugin_admission.py`：`CapabilityKind`、`CapabilityRef`、
`AdmissionDecision`、`PluginAdmissionSnapshot`、`resolve_capability_admission`、
`build_plugin_admission_snapshot`。判定只返回 `allowed` + `reason`，不执行能力、不决定 target。
`PluginAdmissionSnapshot` 在每轮 Interaction 建立时冻结一次，存入
`InteractionTurnState.plugin_admission`（首写者胜，中途不可替换）。
进程级 kind 显式返回 `not_interaction_capability`，不进快照。
验收（`verify_p2_admission.py`）：三轴真值表、任一轴不可绕过、reserved/owner_missing 例外、
中途改配置不影响已冻结结果、决策记忆化。

### 第 3 阶段 — 轮次级能力接入（已完成）

八个消费面统一经 `capability_allowed()`：Prompt Extension、Interaction Prompt/Result
Contributor、Stream Decider、Lifecycle Observer、Persona Effect、FunctionTool、LLM Hook。
`list_*` 增加可选 `event` 参数；新增 `call_capability_lister()` 兼容旧签名与测试替身。
Runtime Observation Sensor 无 `event`，改在其提交点按目标 session 解析 `session_plugin_config`。
验收（`verify_p3_wiring.py`）：同一插件在「全允许 / 会话禁用 / plugin_set 排除 / 全局停用」
四种组合下，八个面**结论完全一致**。

### 第 4 阶段 — hard / soft 契约（已完成）

新增 `is_hard_contribution()` 与 `HARD_EFFECT_METADATA_KEY`，`expression_agent.py` 中四处
`required_per_segment` 判定改为调用该谓词。语义固定为：
**hard 只豁免软丢弃（超时 / best_effort 跳过），不豁免 Permission 与 Applicability。**
验收（`verify_p4_hard_soft.py`）：四种 Permission 组合下 hard 契约均消失；
`event_filter` 不匹配时同样消失；两轴同时通过才出现；谓词严格 `is True`。

### 回归基线

`tests/unit`：改动前 **1251 passed / 8 failed**；第 1–4 阶段完成后仍为
**1251 passed / 8 failed**，失败项逐条相同，均为既有失败（`FakeEvent` 缺 `get_extra`、
MCP lifecycle、computer 组件等），与本次改动无关。

### 第 5 阶段 — 双注册清理与等待策略（已完成）

**B1 AG99Live 双注册**：`data/plugins/astrbot_plugin_ag99live_adapter/middleware/remote_operator.py`
的 `register_remote_operator_interaction_contributors` 不再注册标准 Prompt Extension Collector，
增强链路只保留 Interaction Prompt Contributor，并显式声明 `targets=["core"]`，
使 JSON 执行协议留在 Core 而不投影到 Persona。
标准 Collector 类保留为**旧 Core 兼容路径**（无 Interaction 契约时使用），并在文档字符串中写明
不得与 Contributor 同时注册（两者产出同一个 `extension.system` 槽）。
验收（`verify_p5_dual_registration.py`）：增强路径只有单一 prompt 生产者；
两条路径的 targets 均为 `core`；单生产者不再冲突；同时注册仍会冲突（证明修复点正确）。

**B2 `compact_context`**：按 D11 删除其等待策略语义。
`expression_agent._prepare_render_result` 不再按 `req.compact_context` 分支，
一律走 `get_or_build_interaction_persona_context_pack()`；字段保留但标注为 inert，
`middleware.py` 的普通首回复不再传该标记。
验收（`verify_p5b_wait_policy.py`）：全模块已无 `if req.compact_context` 分支；
`best_effort` 在 pending 时回退 base、ready 时使用插件包（语义未变）。
`tests/unit/test_interaction_expression_agent.py` 中锁定旧行为的断言已按新契约更新。

### 第 6 阶段 — 富化并发与总预算（已完成）

`_collect_prompt_extension_slots` 由串行 for 改为并发收集
（新增 `_run_prompt_extension_collectors` + `_PromptExtensionCollectionOutcome`）：
- 并发执行，墙钟≈最慢一项而非累加；
- 结果按原优先级顺序重排，槽内容与完成顺序无关，保持确定性；
- 单个失败/取消不影响其他，仍在诊断中列出全部 collector 名；
- 整个收集组共享**一个**由 `TurnDeadlineBudget.remaining()` 派生的阶段预算，
  超时统一取消在途任务并保留已完成贡献。**不新增按插件或按项超时**（D4），
  也不成为第二个 deadline owner（D19）。

验收（`verify_p6_enrichment.py`）：并行重叠已证明（fast 在 slow 结束前完成）；
乱序完成时槽顺序仍确定；单点失败被隔离；0.4s 预算对 5.0s collector 在 0.42s 截断且保留已完成项。

### 第 7 阶段 — 绕过路径审计（已完成，仅审计未改码）

按「事件注入 / 直接发送 / postprocessor / web_api / provider / 全局 task / cron / handoff /
`get_full_tool_set`」逐项核实。结论：

| 项 | 现状 | 结论 |
| --- | --- | --- |
| `get_full_tool_set`（`func_tool_manager.py:487`） | **全仓无生产调用方**，仅 `tests/unit/test_tool_conflict_resolution.py` 等测试直接调用；生产路径用的是带 target 过滤的 `get_tool_set_for_target` / `get_func(target=)` | **B7 关闭**：当前不构成绕过 `plugin_tool_targets` 的漏洞。保留为回归关注点，若将来新增生产调用方必须复审 |
| `Context.register_task` / `_register_tasks` | `context.py:2210` 写入，`core_lifecycle.py:511` 消费；`star_manager._remove_plugin_runtime_extensions` **不清理** | **确认泄漏面**：插件反复 reload 会累积残留任务。属进程级治理，进第 7 阶段收口清单 |
| `Context.get_event_queue()` | `context.py:2057` 返回 pipeline 队列；内置插件 `builtin_stars/astrbot/main.py:115` 有使用 | **确认第二输入通道**，可绕过统一入口，且无插件归属 |
| `Context.register_web_api` / `registered_web_apis` | `dashboard/server.py:205-207` 消费实例注册表；显式 owner scope 下的路由会在 owner teardown 时移除，ownerless legacy route 仍无 activated / plugins_name 门禁 | **部分收口**：实例共享问题和有 owner 的卸载残留已修复；ownerless 旧注册仍属进程级兼容风险 |
| `register_postprocessor` | 生产路径 `postprocess/manager.py:98-109` | 无门禁 |
| Provider / Platform Adapter / Cron | 各自 registry，无门禁 | 进程级生命周期 |

**已安装插件实测**：`data/plugins` 下**没有任何插件**使用 `register_web_api` / `register_provider` /
`register_task` / `add_basic_job` / `add_active_job` / `get_event_queue` / `send_message_by_id` /
`create_event` / `register_postprocessor`。因此这些面属**潜在风险而非当前故障**，
不需要与 Interaction 准入混在一批处理。

**结论**：第 7 阶段的正确处理是**独立治理**，不并入会话准入快照（符合 Q7）。
其中「`register_task` 卸载不回收」是唯一有确定性后果的一项，建议优先修；
其余按「补 Owner + 记录兼容状态 + 停用即回收」的原则排期。

### 第 8 阶段 — 能力清单 API 与配置界面（已完成）

**读模型**：新增 `astrbot/core/plugin_capability_inventory.py`：
- `describe_capability()` 描述单项能力，返回 `kind` / `owner` / `plugin_id` /
  `permission_state` / `applicability_state` / `target` / `target_reason` /
  `target_editable` / `hard_or_soft` / `migration_state` / `scope`；
- `build_capability_inventory()` 按插件分组汇总；
- `EDITABLE_FIELDS` 与 `READ_ONLY_FIELDS` 显式分离。

可编辑字段刻意只有五项：能力启用、当前会话是否允许、旧式直接输出是否继续兼容、
LLM Hook 归属、FunctionTool 归属。`target_editable=False` 用于契约固定的种类
（handler / prompt_extension / persona_effect / runtime_sensor / lifecycle_observer /
management_hook），避免界面提供"填了也只能填一个值"的非法配置。

**补前置缺口**：`Context.list_runtime_observation_sensors()`——此前 Sensor 没有带准入的
读取入口，清单里看不到它。

**API**：`GET /api/plugin/capabilities`（`astrbot/dashboard/routes/plugin.py`）。

**界面**：新增 `dashboard/src/components/shared/PluginCapabilityInventory.vue`（只读表格，
对话能力与进程级能力分区展示），挂到插件详情页；`core/shared.json` 补 zh-CN / en-US 文案。

验收（`verify_p8_inventory.py`）：字段分离正确；按插件分组且带 owner 归属；
契约固定的种类 `target_editable=False`；Sensor 出现在清单中；
会话禁用时 `permission_state=session_disabled` 且 hard 契约仍如实报告；
进程级种类带 `migration_state`（`leak_on_unload` / `no_lifecycle_gate` / `needs_review` /
`legacy_compatibility`）。

仪表盘 `vue-tsc --noEmit` 与 `vite build` 均通过。

### 第 9 阶段 — 文档与冻结表同步（已完成）

**冻结决策表**（`runtime-function-unification-plan.md` §二）新增 D-016~D-020：
- D-016 Permission 与 Applicability 是正交两轴，轮次级能力必须同时通过；
- D-017 `hard` 只豁免软丢弃，不豁免准入；
- D-018 Owner 必须注册时显式记录，禁止类型模块推断；卸载按 Owner 清理；
- D-019 准入只有一个裁决点，且每轮冻结一次快照；各注册表不得自行判断；
- D-020 富化并发且共享一个由 `TurnDeadlineBudget` 派生的阶段预算，不新增按插件超时、
  不建立第二个 deadline owner。

同文 §三 非目标新增两条：不为 Handler / Prompt Extension / Persona Effect 增加可配置 target；
不把进程级能力纳入轮次准入快照。

**其他同步**：
- `parallel-plugin-runtime-plan.md` §三 能力边界表：补充"归属（target）与准入（admission）
  正交"及两轴公式；
- `modules/interaction.md` 插件类型表：同上；
- `docs/zh|en/dev/star/plugin-new.md`：新增 IMPORTANT 提示，说明停用/会话禁用会让
  **全部**能力面失效、管理钩子除外，以及 `hard` 不等于豁免准入；
- `dashboard` 会话页插件禁用提示（zh-CN / en-US / ru-RU）：改为明确列出会话禁用覆盖的能力面。

### 尚未实施（第 7 阶段收口清单）

进程级能力按 Q7 独立治理，**不**并入会话准入快照。当前状态：

| 项 | 需要做的事 | 优先级 |
| --- | --- | --- |
| `Context.register_task` / `_register_tasks` | 卸载时回收（当前确定泄漏） | 高（唯一有确定后果项） |
| `Context.register_web_api` / `registered_web_apis` | 改为实例属性；停用后不提供活动路由 | 中 |
| `Context.get_event_queue()` / `StarTools.create_event` | 补 Owner、记录兼容状态、纳入启停治理 | 中 |
| `register_postprocessor` | 明确是否允许影响最终材料 | 中 |
| Provider / Platform Adapter / Cron | 按进程生命周期管理 | 低 |
| `get_full_tool_set` | **已关闭**：全仓无生产调用方，保留为回归关注点 | — |

---

## 11. 复核发现与修复记录（第二轮）

上一轮汇报的“九阶段全部完成”**已撤回**：骨架落地，但边界存在 P1 回归。
外部复核提出 7 条，全部经运行时复现确认成立，现已全部修复。

| # | 缺陷 | 复现证据 | 修复 |
| --- | --- | --- | --- |
| 1 | 卸载路径 `module_prefix` 在赋值前被使用 | `NameError: cannot access free variable 'module_prefix'` | 提前计算 `module_prefix`；工具匹配条件补 `module_prefix` 空值保护 |
| 2 | 空 `plugin_set` 被当作“允许全部” | `_resolve_plugin_set([])` → `None` | `None` = 不限制；`[]` = 否决全部（与 `star_handler` 原语义一致）；新增 `resolve_event_plugins_name()` 作为唯一归一化入口 |
| 3 | 主动表达与 Sensor 绕过 `plugin_set` | 事件 `plugins_name is None`；Sensor 只读会话禁用 | `submit_runtime_observation_event` 统一补 `event.plugins_name`；Sensor 按目标会话解析配置并检查 `plugin_set`；`waking_check` 改用同一入口 |
| 4 | 预算分支不隔离异常、耗尽反而无限等待 | 受保护区域外抛错会中断整组；`remaining<=0` → `None` → 无超时 gather；对已取消任务调 `exception()` 抛 `CancelledError` | 新增 `_safe_task_result()`；`_await_prompt_extension_collectors()` 用 `TurnDeadlineBudget.stage("plugin_enrichment", limit)`，耗尽立即取消并返回；新增 `plugin_enrichment_timeout` 组级上限（非按插件超时） |
| 5 | 进程级 Owner fallback 跨任务泄漏 | 无关任务被归属到正在初始化的插件 | 删除 `_PLUGIN_OWNER_SCOPE_FALLBACK` 与 `register/clear_plugin_owner_scope`；仅保留 ContextVar；`terminate()` 也纳入 owner scope |
| 6 | “每轮冻结”未冻结启用状态；两处快照可能分叉 | 同轮同插件两种能力得到不同结论；extra 与 TurnState 指向不同快照 | 快照新增 `owner_states`（整注册表冻结）；`TurnState` 为唯一写入方，extra 仅为读取投影；读取优先 TurnState |
| 7 | 能力清单不完整且显示错误信息 | 必发 Effect 报 `soft`；缺 handler/llm_hook/tool；target 用默认值 | 清单合并 Handler / LLM Hook / Management Hook / FunctionTool；Effect 传真实 metadata；新增 `resolve_plugin_runtime_target()` / `resolve_tool_runtime_target()` 解析真实配置来源 |

补充修复：`test_interaction_plugin_runtime.py` 中一处事件用了 `plugins_name = []`（原意是“不限制”），
在修正语义后正确地变成“否决全部”，已改为 `None` 并加注释。
`plugin_enrichment_timeout` 已补 zh-CN / en-US / ru-RU 的 config-metadata 文案。

**新增端到端用例**：`tests/unit/test_plugin_context_wait_policy.py`（5 个用例，经 Persona Agent）：

- `best_effort` + 任务未完成 → 使用基础包；
- `wait_complete` + 任务未完成 → 等待并最终使用插件包；
- Personal 与 Core 复用同一 task，且只执行一次；
- `compact_context` 不再绕过等待策略。

该用例经**变异验证**：临时恢复 `compact_context` 旁路后，
`test_persona_expression_waits_when_mode_is_wait_complete` 会失败；恢复修复后通过。
（第一版用例无法捕获该回归——因为断言场景下旁路与策略结果相同——已重写为可判别场景。）

**验证基线**：`tests/unit` = **1256 passed / 8 failed**，失败项与改动前逐条一致（既有失败）。

---

## 12. AG99Live 上游改动说明（不改 data/）

**重要**：AG99Live 的源码不在本仓库。`data/plugins/astrbot_plugin_ag99live_adapter/`
是**部署副本**（`data/` 被 `.gitignore:29` 忽略，且该目录内没有 `.git`）。
其源码仓库由 `metadata.yaml` 声明：

```yaml
repo: https://github.com/murphys7017/AG99live
```

因此 P5/B1 的修复必须提交到 **AG99live 仓库**，不能留在 `data/`。
本节只说明改法；本仓库不包含该插件的源码，故不做改动。

以下路径均相对 AG99live 仓库根目录。涉及 1 个文件：
`middleware/remote_operator.py`。

### 12.1 必改：删除重复注册（B1 冲突的根因）

`register_remote_operator_interaction_contributors()` 里**删除**标准 Collector 的注册：

```python
def register_remote_operator_interaction_contributors(context: Any) -> None:
    register_extension = getattr(context, "register_prompt_extension_collector", None)
    if callable(register_extension):
        register_extension(AG99liveRemoteOperatorPromptExtensionCollector())   # ← 删除这 3 行

    register_prompt = getattr(context, "register_interaction_prompt_contributor", None)
    ...
```

删除即可，无需替代分支。原因：该函数**只**在
`middleware/__init__.py::register_ag99live_interaction_contributors()` 中、
且**已通过 `supports_interaction_contributors(context)` 检查之后**才被调用。
也就是说旧 Core 根本走不到这里（旧 Core 由 `main.py` 的
`_official_core_compatibility` 分支改用 `<@anim>` inline motion）。
所以这次注册从来不是"兼容旧 Core 的路径"，而只是**同一份内容在增强链路上的第二个生产者**：

- Contributor 经 `build_prompt_extension_slots(source="interaction_prompt_contributors")`
- Collector 经 `build_prompt_extension_slots(source="prompt_extension_collectors")`

两者产出同名槽 `extension.system`，但 `source` 与 `meta.targets` 不同，
于是 `_add_collected_slot()` 抛 `PromptContextConflictError`。
只要远程执行器配置可用且两条路径都返回扩展，就会触发。

### 12.2 必改：Contributor 显式声明 `targets=["core"]`

```python
class AG99liveRemoteOperatorPromptContributor:
    plugin_id = "ag99live.remote_operator.prompt"
    priority = 35

    async def collect(self, event, plugin_context, view):
        del plugin_context, view
        return collect_remote_operator_prompt_extension(
            event,
            plugin_id=self.plugin_id,
            targets=["core"],          # ← 新增
        )
```

必要性：`InteractionPromptContributorCollector.collect()`
（AstrBot `interaction/context_builder.py`）会对贡献做
`meta.setdefault("targets", ["persona"])`。不显式声明，远程执行器的 JSON 执行协议
就会被投影到 Persona，而它本来只应出现在 Core 请求上。

### 12.3 必改：`collect_remote_operator_prompt_extension` 支持显式 targets

```python
def collect_remote_operator_prompt_extension(
    event: Any,
    *,
    plugin_id: str,
    targets: list[str] | None = None,        # ← 新增参数
) -> Any | None:
    ...
    meta: dict[str, Any] = {                 # ← 原为字面量
        "scope": "dynamic",
        "node_type": "ag99live_remote_operator_routing",
    }
    if targets is not None:
        meta["targets"] = list(targets)
    return capabilities.prompt_extension(
        plugin_id=plugin_id,
        mount="system",
        title="AG99live Remote Operator Routing",
        value_kind="text",
        value=build_remote_operator_prompt(online_config),
        order=35,
        meta=meta,
    )
```

### 12.4 建议：删除已失效的 Collector 类

`AG99liveRemoteOperatorPromptExtensionCollector` 在 12.1 之后**全仓库无任何引用**
（已 grep 确认：插件内只有定义处）。可直接删除；若为兼容外部导入而保留，
请在类文档字符串中写明它不再被注册，并**不要**在 12.1 中恢复注册。

### 12.5 验收

1. 启动日志不再出现 `Remote operator prompt skipped: config_unavailable` **成对**出现
   （原先同一轮会由两条路径各调一次，间隔约 100ms）。
2. 远程执行器在线且配置可用时，Persona 首回复不抛 `PromptContextConflictError`。
3. `extension.system` 槽中远程执行器条目 `meta.targets == ["core"]`。
4. 无 Interaction 能力的旧 Core 仍走 `<@anim>` inline motion，行为不变。

### 12.6 本仓库侧：可选的核心加固（需你决定）

即便插件修好，AstrBot 侧仍有一个健壮性缺口：
`prompt/context_collect.py::_add_collected_slot()` 对同一 `collect_context_pack`
内的同名槽只做"相等则跳过、否则抛错"，**没有** `merge_context_packs()` 里那种
`extension.*` 合并分支。因此任何插件只要在同一轮注册两条产同名槽的路径，
就会让该轮失败。

可选加固（**未实施**，需你确认）：
在 `_add_collected_slot` 中，当 `slot.name.startswith("extension.")` 时复用
`builder._merge_extension_slot()` 的按 `(plugin_id, title, value)` 去重合并语义，
把"冲突"降级为"合并 + 诊断"。这会让框架对这类插件错误更宽容，
但也意味着同槽冲突不再显式失败——与现有 fail-fast 契约相悖。
倾向：**保持 fail-fast**，仅在冲突信息中补充两句诊断，便于定位是哪两条路径。

---

## 13. 复核发现与修复记录（第三轮）

第二轮修复有效，但复核又发现 5 项遗漏。全部经运行时复现确认并修复。

| # | 缺陷 | 复现证据 | 修复 |
| --- | --- | --- | --- |
| 1 | 收集任务被外层取消后子任务仍在跑 | `collector still running after parent cancellation: True (count=1)` | 新增 `drain()`：任何退出路径（含外层取消）都先取消并 `gather` 等待未完成子任务，再传播取消。无预算分支同样加 `finally` |
| 2 | 冻结 Owner 查找丢失模块前缀匹配 | 同一 owner 路径：实时 `plugin_not_in_plugin_set`（拒绝）vs 冻结 `owner_missing`（放行） | 新增 `_frozen_owner_state()`，镜像 `_owner_metadata` 的前缀匹配；真正未知的 owner 仍为 `owner_missing` |
| 3 | 准入混用实时与冻结状态 | 停用插件后冻结快照允许、但 `_is_*_active()` 已拒绝 | 新增 `Context._capability_visible()`：有冻结快照时**以快照为准**，不再叠加实时检查；无快照时仍用实时检查 |
| 4 | `plugin_enrichment_timeout` 未接通 | 配置 9.0 → 构建结果 3.0 | `build_interaction_prompt_build_config()` 读取并传入该字段 |
| 5 | 清单界面 key 冲突 | 多个 `llm_hook` 行 `plugin_id` 为空 → key 全为 `"llm_hook|"` | Vue 改用 `capabilityKey()`：`owner_module_path + kind + plugin_id + item_name` |

**关于第 2 项我的首次复现是错的**：我最初用了同级路径
（`...p.middleware.deep`），而 `_owner_metadata` 的前缀分支匹配的是**后代**路径
（`owner.startswith(candidate + ".")`），因此实时也查不到，导致该条被判为"不成立"。
改为真正的后代路径（`...p.main.middleware.deep`）后立即复现。
补充说明可达性：`_normalize_plugin_owner_module()` 会把标准插件树
（含 `plugins` / `builtin_stars` 段）归一到 `...main`，所以常规插件不受影响；
受影响的是模块树不含这两个段的插件与内嵌场景。缺陷真实，但触发面比复核描述窄。

**关于第 3 项的设计选择**：采用**冻结为准**，与已冻结决策 D-019
（"每轮 Interaction 冻结一次快照"）一致。代价是中途停用插件不会在本轮立即生效，
下一轮生效；如需"实时撤销"，应显式改为实时策略并让**所有**入口一致，
而不是当前这种两套判断并存。此项在选择冻结策略后已自洽。

**验证**：`verify_5fixes.py` = 11/11 通过；
既有验证脚本（F1/F3/F4/F5+F6/F7）全部 EXIT=0；
新增端到端用例 `tests/unit/test_plugin_context_wait_policy.py` = 5 passed；
仪表盘 `vue-tsc --noEmit` 通过；
`tests/unit` = **1256 passed / 8 failed**，失败项与基线逐条一致。
