# Personal / Core 能力语义与联网委派修复计划

日期：2026-09-21

状态：**源码已实施，等待 OLV 实机验收**。B1-B6 已完成；其中 B5 只作为
结构化结果明显违背当前能力事实时的窄保护，不替代 Personal 的模型判断。

执行对象：AstrBot Core。
本计划不要求修改 AG99live，不要求修改用户运行中的配置文件，也不要求重启服务。

## 实施记录

| 批次 | 当前结果 |
| --- | --- |
| B1 | `FunctionTool` 支持工具级及动作级语义能力声明；MiniMax CLI 仅将 `search` 声明为 `web_research` |
| B2 | Core 联网诊断和工具调用提示共用语义能力投影，旧工具名识别只保留为集中 fallback |
| B3 | Personal 按当前轮次配置、插件准入、persona 工具选择和 Core target 生成只读能力摘要；不获得 Core ToolSet |
| B4 | Personal 与 Core Planner Prompt 明确职责，历史和 memory 不再被允许作为当前能力事实 |
| B5 | 仅在实时请求、`reply`、明确能力否认、Core 能力为 `admitted/unknown` 四项同时成立时纠正为 `delegate` |
| B6 | 增加配置 ID、能力 ID、绑定工具、路由纠正原因及 Core 实际联网能力诊断 |

实现特意复用已构建的 `PersonaDefinition` 传给 `CapabilityResolver`，避免为了能力摘要
再次解析人格。能力摘要表示当前执行入口已经准入，不承诺 API Key、网络或第三方服务
一定成功；最终事实仍由 Core 的真实工具快照和执行结果决定。

本地 MiniMax CLI 位于被 `.gitignore` 忽略的 `data/plugins/astrbot_plugin_minimax_cli`，
动作级声明已经同步到当前运行副本，但它不属于 AstrBot Core 的 Git diff，需按插件自身
来源单独保存或同步。

## 1. 背景与问题

当前 OLV 会话已经启用联网入口，但 Personal 仍可能回复“我没有联网工具”并选择
`reply`，导致 Core 根本没有启动。

已确认的链路是：

```text
Personal CapabilitySnapshot(target=personal_expression)
  -> tool_count=0
  -> Personal 仅依据自身工具列表和历史表达判断
  -> 选择 reply
  -> Core Planner 未启动
  -> Core 的 web_search / minimax_cli(search) 没有机会执行
```

这里的 `tool_count=0` 不是错误。Personal 不应直接持有联网、文件、定时任务等
业务工具。错误在于把：

```text
Personal 不直接执行业务工具
```

误解为：

```text
系统整体没有业务能力
```

同时，Core 当前的联网诊断主要识别工具名称。`minimax_cli` 是一个多动作工具，
它的 `search` 动作实际具备联网语义，但不能仅凭工具名被识别为联网能力。

## 2. 冻结的职责边界

### 2.1 Personal

Personal 是唯一的对外人格窗口，负责：

1. 理解用户意图；
2. 使用人格、历史和 memory；
3. 选择 `reply`、`delegate` 或 `silent`；
4. 生成快速确认、进度表达和最终表达；
5. 接收 Core 的事实结果，并统一生成最终用户可见回复。

Personal 不负责：

- 直接执行联网、文件、定时任务或其他业务工具；
- 根据自身 `tool_count` 判断系统整体能力；
- 根据历史中的“没有工具”回复判断当前权限；
- 在 Core 尚未返回结果前声称任务完成、能力不存在或事实已确认；
- 重新实现一套 Core 工具解析逻辑。

### 2.2 Core

Core 是被 Personal 委派后的复杂工作执行层，负责：

1. 接收已经作出的 `delegate` 决策；
2. 由 Core Planner 生成 `CoreTaskSpec`；
3. 根据当前配置和当前轮次快照解析真实工具；
4. 执行联网、文件、定时任务、插件工具和其他外部操作；
5. 返回进度、结果或明确失败事实；
6. 把结果交回 Personal，由 Personal 统一表达。

Core 不负责：

- 再次决定是否应该进入 Core；
- 直接发送最终人格回复；
- 用历史消息代替当前工具快照；
- 把 Core 的完整工具 Schema 复制给 Personal。

### 2.3 三个必须区分的概念

| 概念 | 含义 | 示例 |
| --- | --- | --- |
| 执行能力 | 系统可以尝试完成的语义能力 | `web_research` |
| 工具绑定 | 承载能力的具体工具 | `web_search_tavily`、`minimax_cli` |
| Tool Call | 模型实际发起的一次调用 | `minimax_cli(action="search")` |

`Personal tool_count=0` 只说明 Personal 没有直接工具，不代表
`web_research` 不存在。

## 3. 目标架构

```text
用户输入
  -> 当前事件绑定的配置文件
  -> InteractionTurnState 冻结配置与准入快照
  -> Core 能力摘要（只读、语义化）
  -> Personal 选择 reply / delegate / silent
       |                         |
       | reply                   | delegate
       v                         v
  统一人格回复              Core Planner（必须 execute）
                                  |
                                  v
                          Core CapabilitySnapshot
                                  |
                                  v
                             实际工具执行
                                  |
                                  v
                         结果/失败事实返回 Personal
                                  |
                                  v
                             统一最终表达
```

Personal 只接收能力摘要，不接收 Core 业务工具 Schema。Core 继续使用现有
`CapabilityResolver` 和 `CapabilitySnapshot`，不新建第二套工具准入系统。

## 4. 能力摘要契约

### 4.1 推荐结构

新增一个只读、可序列化的语义摘要，名称可由实现者确定，但必须保持以下字段：

```json
{
  "format": "execution_capability_summary_v1",
  "target": "core",
  "config_id": "current-config-id",
  "capabilities": [
    {
      "id": "web_research",
      "state": "admitted",
      "executor": "core",
      "bindings": ["web_search_tavily", "minimax_cli"],
      "personal_can_call": false
    }
  ]
}
```

字段语义：

- `id`：稳定的语义能力 ID，不是工具名称；
- `state`：只表示当前轮次已通过配置、Owner、target 等准入，不能保证网络或
  第三方 API 一定成功；
- `executor`：当前由 `core` 执行；
- `bindings`：诊断用的具体工具名称；
- `personal_can_call`：明确告诉 Personal 不应直接调用；
- `config_id`：必须是当前事件实际绑定的配置 ID。

当能力无法确认时使用 `unknown`，不能自动写成 `unavailable`。

### 4.2 第一阶段只实现

本阶段只要求统一：

- `web_research`

以下能力暂不纳入本计划的实现范围：

- `vision`
- `workspace_io`
- `computation`
- `speech`
- `task_scheduling`

Planner 可以继续保留这些现有语义标签，但不能为了本问题扩大能力注册系统。

## 5. 分批实施计划

### B1：能力语义模型与动作级声明

**目标**：让工具可以声明“某个动作具备什么语义能力”，而不是把整个多动作工具
误标成联网工具。

**检查和修改位置：**

- `astrbot/core/agent/tool.py`
  - 在 `FunctionTool` 增加最小声明字段，建议使用动作到能力 ID 的映射；
  - 保持旧工具默认无语义能力；
  - 不改变 `execution_targets` 的现有语义。
- `astrbot/core/capabilities.py`
  - 增加从工具读取语义声明的纯函数；
  - 不在 `CapabilityResolver.resolve()` 内执行工具或读取模型输出；
  - `CapabilitySnapshot` 增加只读语义能力投影方法。
- `data/plugins/astrbot_plugin_minimax_cli/main.py`
  - 仅作为本地插件参考实现，为 `search` 动作声明 `web_research`；
  - 不把 `minimax_cli` 的全部动作标记为联网；
  - 该目录受 `.gitignore` 管理，不能假设它属于 Core 提交。

**建议的语义形态：**

```python
{
    "search": ("web_research",),
}
```

如果当前插件 API 无法安全扩展动作声明，B1 可以先实现 Core 侧的可插拔解析接口，
并对 `minimax_cli` 使用明确的 owner/tool/action 适配器；不得把 `minimax_cli`
硬编码散落到多个模块。

**验收：**

- 普通 `web_search_tavily` 能映射到 `web_research`；
- `minimax_cli(action="search")` 能映射到 `web_research`；
- `minimax_cli(action="text")` 不被误判为 `web_research`；
- 没有声明的旧工具保持原行为。

### B2：统一联网能力投影

**目标**：Core 的联网诊断和 Personal 的能力摘要共用同一个能力判断。

**检查和修改位置：**

- `astrbot/core/astr_main_agent.py`
  - 重构 `diagnose_direct_web_research_capability()`；
  - 首先读取 `CapabilitySnapshot` 的语义能力；
  - 仅在没有语义声明时，使用现有工具名识别作为兼容 fallback；
  - fallback 只集中在一个模块，不再在其他调用点重复维护工具名称集合。
- `astrbot/core/tools/web_search_tools.py`
  - 保留现有 `is_web_search_tool_name()`；
  - 将它定位为 legacy/tool-name fallback；
  - 不把 `minimax_cli` 永久塞入通用内置搜索工具名称集合，除非该函数明确支持
    “动作级声明不可用”的兼容路径。
- `astrbot/core/capabilities.py`
  - 增加类似 `semantic_capabilities()` 或等价方法；
  - 返回稳定、去重、排序后的能力 ID 和绑定工具名。

**重要约束：**

- `web_research` 只代表“当前轮有 Core 执行入口”；
- 不代表 API Key 有效、网络可用、服务商登录成功；
- Core 执行失败仍必须返回真实失败事实；
- 不得因为摘要显示 `admitted` 而跳过 Core 的真实工具执行。

**验收：**

- Planner 生成 `web_research` 后，Tavily 和 MiniMax 搜索都能被诊断为已挂载；
- 无搜索工具时仍能诊断为未挂载；
- `minimax_cli` 只有 `search` 动作时才计入；
- 日志中同时保留 `config_id`、语义能力 ID、绑定工具名。

### B3：按当前配置生成 Personal 能力摘要

**目标**：Personal 能知道“Core 可以尝试做什么”，但不获得业务工具。

**建议新增位置：**

- 新建 `astrbot/core/interaction/execution_capability_summary.py`，或放入已有
  `runtime_context_projection.py`，但只能有一个 canonical builder；
- 输入必须包括：
  - `event`；
  - `plugin_context`；
  - 当前 `CapabilitySnapshot` 或可重建的 Core 请求；
  - 当前配置快照；
  - 当前 `config_id`。
- 输出为不可变/只读摘要，供 Prompt collector 使用。

**接入位置：**

- `astrbot/core/interaction/expression_agent.py`
  - 在 `_prepare_render_result()` 构建 Personal prompt pack 时投影摘要；
  - 不修改 `_resolve_personal_expression_capabilities()` 的目标语义；
  - 不将 Core 工具加入 Personal 的 `ToolSet`；
  - 在 `build_persona_runtime_system_prompt()` 或独立 context slot 中加入职责说明。
- `astrbot/core/prompt/collectors/system_collector.py` 或专用 collector
  - 只添加语义摘要和当前配置范围；
  - 不添加完整工具 Schema、API Key、插件内部参数或历史失败文本。

**Personal 必须看到的规则：**

```text
Personal 当前不直接调用业务工具。
Personal tool_count=0 不代表系统整体没有工具。
如果 Core 能力摘要显示 web_research=admitted，
需要天气、最新信息、网页查询等实时信息时必须选择 delegate。
摘要 unknown 时也不能直接声称系统没有联网能力，应交由 Core 检查。
历史和 memory 不能作为当前工具权限或能力状态的依据。
```

**验收：**

- Personal 日志显示 `tool_count=0` 时，同时能看到 `core_capability_ids`；
- 当前配置关闭 Tavily 且未启用 MiniMax 搜索时，摘要不显示 `admitted`；
- 不同配置文件的摘要互不污染；
- Personal Prompt 中没有 Core 工具的完整 JSON Schema。

### B4：明确 Core Planner 的职责

**目标**：避免 Personal 和 Planner 各自重新判断一次“是否进入 Core”。

**修改位置：**

- `astrbot/core/interaction/core_planner.py`
  - 保留“任务已经由 Personal 委派，Planner 必须返回 `execute`”；
  - 明确要求实时信息任务标记 `web_research`；
  - 明确“能力不可用”只能依据 Core 当前快照和执行结果；
  - 禁止 Planner 生成用户可见回复。
- `astrbot/core/interaction/types.py`
  - 保持 `CoreTaskSpec.requires_direct_web_research()` 作为执行边界判断；
  - 如新增语义 ID，统一归一化入口，不增加多个关键词判断函数。

**不得做：**

- 不让 Planner 决定回到 Personal；
- 不让 Planner 直接给用户发送人格回复；
- 不让 Planner 用历史中的“没有工具”覆盖当前快照；
- 不引入第二个 router。

### B5：Personal 路由保护

**目标**：对模型明显违背结构化职责的结果提供最小安全保护。

这批是 B1-B4 验证后才允许执行，不能先做。

**建议位置：**

- `astrbot/core/interaction/middleware.py`
  - 在结构化 `turn_action` 已解析、尚未完成 `reply` 路由时检查；
- 可新增纯函数，例如：

```python
def should_correct_capability_denial(
    request_text: str,
    expression: PersonaExpressionResult,
    capability_summary: ExecutionCapabilitySummary,
) -> bool:
    ...
```

**触发条件必须同时满足：**

1. 当前请求明确需要实时信息或外部执行；
2. Personal 选择 `reply`；
3. 回复声称“没有工具/无法联网/查不了”等能力否定；
4. 当前 Core 摘要存在 `web_research=admitted` 或状态为 `unknown`。

**修正行为：**

- 只修正路由为 `delegate`；
- 不改写用户原始请求；
- 不把关键词检查当作主路由；
- 不在此处执行工具；
- 记录诊断原因 `capability_denial_route_corrected`。

如果摘要为 `unavailable`，不要强制委派；应保留事实型不可用回复或按既有错误流程处理。

### B6：日志与最小验证

**必须新增或补齐的诊断字段：**

- `config_id`
- `personal_tool_count`
- `core_capability_ids`
- `core_capability_bindings`
- `personal_turn_action`
- `route_correction_reason`
- `core_tool_names`
- `direct_web_research_required`

日志不得包含 API Key、完整 Prompt 或用户敏感内容。

**最小验证脚本/测试：**

1. `web_search_tavily` 映射 `web_research`；
2. `minimax_cli(search)` 映射 `web_research`；
3. `minimax_cli(text)` 不映射；
4. Personal 工具为空但 Core 摘要存在时，结构化结果选择 `delegate`；
5. Core 摘要不存在时，不能生成“系统绝对没有工具”的事实；
6. 两个配置文件分别生成各自摘要；
7. 真实天气请求进入 Core，Core 再调用实际搜索工具；
8. 无联网能力时，Core 返回真实不可用结果，Personal 统一表达。

优先使用纯函数和最小公共入口验证；不增加大规模 mock 测试，不把私有调用次数
作为验收标准。

## 6. 执行顺序与停点

本次实施已按以下顺序完成；后续修改仍应保持该依赖顺序：

1. B1 能力语义模型；
2. B2 Core 统一联网判断；
3. B3 Personal 能力摘要；
4. B4 Planner Prompt/职责收口；
5. B6 日志和基础验证；
6. B5 窄范围路由保护。

任何一批出现以下情况必须停下：

- 需要把 Core 工具直接暴露给 Personal；
- 需要修改 AG99live；
- 需要合并多个配置文件；
- 需要修改 `execution_targets` 的既有语义；
- 需要新增第二套 Router、Planner 或消息队列；
- 需要删除历史/memory；
- 需要重启 AstrBot；
- 无法确认当前事件绑定的 `config_id`。

## 7. 明确非目标

- 不改 Personal 的人格风格、语气和输出效果；
- 不改变 `persona_expression` 的 tool-call 输出契约；
- 不改变 effect schema；
- 不把 Personal 改造成业务工具 Agent；
- 不把 Core 工具 Schema 复制到 Personal；
- 不用关键词规则替代模型路由；
- 不合并不同适配器或不同配置文件的能力；
- 不修改 OLV 插件或动作协议；
- 不保留旧插件配置兼容层；
- 不自动启动、重启或部署服务；
- 不提交当前工作区已有的用户修改，尤其是
  `astrbot/core/astr_agent_tool_exec.py` 的循环导入修复。

## 8. 完成定义

代码完成不等于真实平台完成。必须分别记录：

1. 静态检查和最小验证通过；
2. 当前配置下能力摘要与 Core 快照一致；
3. OLV 真实天气请求确实走 `delegate -> Core -> search`；
4. 搜索失败时没有伪造成功；
5. Personal 最终统一表达 Core 结果；
6. 普通闲聊不误启动 Core；
7. 两个配置文件互不串能力。

只有第 3-7 项由用户实测确认后，才能把本计划标记为完成。
