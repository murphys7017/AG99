# 运行时功能统一实施计划

## 文档状态

- 状态：待实施；Phase 0 调查与边界确认已完成，下一步只执行 Phase 1。
- 更新日期：2026-08-03。
- 代码基线：`45e0422fb`（`refactor: consolidate output lifecycle`）。
- 日志基线：`data/logs/astrbot.log` 与 `data/logs/astrbot.trace.log` 的 2026-08-03 样本。
- 任务类型：架构重构与性能修复。
- 实施风险：高。涉及 Persona、插件生命周期、Agent 工具循环、ProviderRequest、
  Prompt 上下文、群聊准入和超时边界，必须逐阶段迁移。
- 第一实施阶段：只统一 Persona 工具执行，不修改插件目标配置、不调整群聊回复策略、
  不处理流式输出。

本文是后续实现的执行依据，不代表所有目标已经完成。每个 Phase 完成后，必须更新本文的
状态、验收结果和剩余风险；已经稳定的事实再同步到
[当前状态](../current-state.md) 与对应模块文档。

## 一、结论先行

当前最优先的问题不是“17 个插件逐个判断”，而是 Persona 在存在可用工具时，先额外执行
一次独立的工具预判模型调用，再执行一次最终人格表达模型调用。17 个工具 schema 是在同一
次预判请求中提供给模型的，并不是 17 次独立模型判断；但这次预判在绝大多数不使用工具的
普通对话中仍然会完整消耗一次模型延迟。

目标不是删除 Persona 工具能力，也不是重新设计插件挂载配置，而是把它恢复成标准 Agent
循环：

```text
Router
  -> resolve personal_expression capabilities once
  -> build one Persona request
  -> run Persona plugin lifecycle once
  -> shared Agent loop
       -> business tool call: execute and continue
       -> persona_expression: terminal structured result
  -> Output
```

普通无工具消息应在 Persona 第一次模型响应中直接调用 `persona_expression`。只有模型实际
选择了业务工具，才继续下一轮模型调用。

后续再按“一个职责一个 owner”的原则，依次统一工具解析、ProviderRequest 生命周期、上下文
预算、超时、群聊准入和类型化诊断。这里的“统一”不是把所有功能塞进一个巨型类，而是每个
职责只有一个事实源、一个写入 owner 和一条主链。

## 二、已冻结的设计决策

除非后续出现新的运行事实并明确修改本文，实施过程中不得重新讨论或悄悄改变以下边界：

| 编号 | 决策 |
| --- | --- |
| D-001 | `plugin_runtime_targets` 只决定插件 LLM 生命周期 Hook 在 `core` 还是 `personal_expression` 生效。 |
| D-002 | `plugin_tool_targets` 只决定插件 FunctionTool 对 `core` 还是 `personal_expression` 可见。 |
| D-003 | 插件 LLM 生命周期默认属于 `personal_expression`；插件工具默认属于 `core`，只有显式声明或配置才进入 Persona。 |
| D-004 | Persona 允许调用明确授权的业务工具；不能因为它是人格表达层就删除工具能力。 |
| D-005 | `persona_expression` 是终止 Agent 循环的结构化输出协议，不是插件业务工具，不进入普通工具执行器。 |
| D-006 | 不再使用独立 LLM 调用预判“是否需要工具”；配置和 capability snapshot 决定工具是否可见，模型在正式 Agent 循环内选择是否调用。 |
| D-007 | 官方 Pipeline Handler、命令、关键词回复、事件监听和 `stop_event` 语义保持原位置，不迁移为 Persona 工具或 Persona Hook。 |
| D-008 | 旧插件不需要为了本次统一修改代码；兼容边界由 AstrBot Runtime 承担。 |
| D-009 | 一次只迁移一个 owner；新 owner 接管后删除旧路径，不长期保留双主链。 |
| D-010 | 流式输出当前为低优先级，不得阻塞本计划的非流式主链收口。 |

## 三、目标与非目标

### 目标

1. 消除 Persona 无工具消息中的额外工具预判模型调用。
2. 让 Persona 和 Core 复用同一套 Agent 工具循环语义，而不是 Persona 手工模拟一套生命周期。
3. 每个 turn、每个 target 只解析一次有效工具集，并让 Prompt schema 与实际执行工具来自同一
   capability snapshot。
4. 每个最终模型分支只构建一个规范 ProviderRequest，并在稳定边界运行插件 Hook。
5. 让 Router、Persona、Planner 和 Core 共享规范事实，但各自拥有明确、有限的上下文预算。
6. 让一次 turn 的 deadline 约束 Provider 超时、重试、fallback 和工具循环，避免超时相乘。
7. 让群聊的所有候选来源只提供证据，由一个准入 owner 决定是否进入 Router。
8. 为每次拒绝、fallback、工具循环和长耗时提供稳定原因码与阶段耗时。

### 非目标

1. 不改变 `plugin_runtime_targets` 和 `plugin_tool_targets` 的含义、默认值和优先级。
2. 不把全部插件或工具默认迁入 Persona，也不把全部插件或工具强制迁回 Core。
3. 不让 Prompt Collector、Router 或插件分别增加一轮“是否使用工具”的模型分类。
4. 不在 Phase 1 修改群聊概率、续接窗口、AngelHeart 判断或主动表达策略。
5. 不在本计划中完成第三方 Execution Backend、MCP 全量迁移或 AG99 私有能力重写。
6. 不为了减少文件行数机械拆类；只有 owner、生命周期或验证边界明确时才拆分。
7. 不把流式输出作为当前验收条件；非流式路径必须先稳定。

## 四、当前问题地图

### 4.1 Persona 工具执行被拆成两次模型任务

当前 `InteractionExpressionAgent` 在检测到 Persona 可见工具后调用
`_run_persona_tool_loop()`。这个内部 Agent 只判断和执行插件工具，不负责生成最终可见回复；
即使返回 `no_tool`，后面仍然会再次调用 Provider 生成 `persona_expression`。

当前普通路径近似为：

```text
Router model
  -> Persona tool-preflight model
       -> no_tool
  -> Persona expression model
       -> persona_expression
```

日志样本中，一次简单 Persona 对话约为：Router 约 1.0 秒，工具预判约 5.7 秒，最终表达约
6.3 秒，总计约 13.4 秒；工具执行次数为 0。这里最确定、最可控的浪费就是中间这次预判。

### 4.2 工具事实有多个解析者

工具目标策略已经集中在 `astrbot/core/plugin_runtime.py`，但工具集合仍会被 Prompt
Collector、System Collector、ExpressionAgent 和 Agent Runner 分别读取、过滤或重建。
这会产生三个风险：

1. Prompt 展示给模型的工具与 Runner 实际可执行工具不一致。
2. Collector 通过其他 Collector 的私有方法获取 Persona 和工具，签名变化容易造成回归。
3. fallback 或插件 Hook 修改请求后，需要用字段差异、快照或重放恢复状态。

### 4.3 Persona 手工维护 Agent 生命周期

Persona 目前单独编排 Waiting、LLMRequest、AgentBegin、LLMResponse、AgentDone，并自行处理
Provider fallback、工具材料、附件和最终结构化输出。官方 Core Agent Runner 又拥有另一套
工具循环和状态转换。两套实现越接近，未来越容易在 Hook 顺序、ProviderRequest 字段、工具
结果或异常语义上漂移。

### 4.4 上下文预算按调用点分散

Persona 已配置 `persona_history_window_size=50`，但 Core 仍可能使用
`provider_settings.max_context_length=-1`。调查样本中曾出现 529 条历史消息、13 个工具、
约 17,419 个输入 token 的 Core 请求。长 Prompt 不仅增加首 token 延迟，也会放大 Provider
超时、重试和同会话排队。

### 4.5 超时和重试会相乘

OpenAI-compatible Provider 默认 timeout 为 120 秒，内部最多重试 10 次；上层还有 fallback
Provider、Agent 循环和 session 串行。一次混合路径样本耗时约 398.6 秒，紧随其后的短消息
因同 session 队头阻塞约 450.6 秒才完成。单层参数看似合理，组合后却没有 turn 级上限。

### 4.6 群聊准入由多个局部规则共同决定

群聊当前同时受到官方唤醒、旧主动回复概率、短窗口续接、模型续接、Personal Runtime
Observation、插件候选和 Router `silent` 的影响。日志样本中：

| 群聊 | 非空消息 | Router 记录 | 相关事实 |
| --- | ---: | ---: | --- |
| `1083316872` | 87 | 4 | AngelHeart 只覆盖该群，并多次判断“不在场/不参与”。 |
| `851957839` | 9 | 0 | 没有候选进入 Router。 |

同时，旧主动回复概率为 `0.02`，而
`personal_conversation_activity_enabled=false` 会让 Heartbeat 持续得到
`heartbeat_without_material`。因此“回复频率低”通常不是 Router 总选择沉默，而是很多消息
根本没有进入 Router。

## 五、目标所有权模型

| 职责 | 目标 owner | 唯一事实或产物 |
| --- | --- | --- |
| 插件生命周期目标与工具目标策略 | Plugin Runtime Policy | 现有目标配置与声明解析结果 |
| 每 target 的可用能力 | Capability Resolver | `CapabilitySnapshot` |
| Prompt 事实收集与目标投影 | Prompt Context Builder / Projection | `ContextPack` 与 target view |
| ProviderRequest 构建 | Request Adapter | 单个规范请求 |
| 插件 LLM 生命周期 | Agent Lifecycle Executor | 一次 run 的 Hook 状态 |
| 业务工具循环 | Shared Agent Runner | 工具调用、结果与循环状态 |
| Persona 终止输出 | Persona terminal contract | `PersonaExpressionResult` |
| Core 执行输入 | Core Execution Preparation | `CoreExecutionSpec` |
| 群聊是否进入 Router | Group Admission Coordinator | `GroupAdmissionDecision` |
| Turn 超时、重试和 fallback | Turn Deadline Budget | 单调递减的剩余预算 |
| 运行状态与诊断 | Typed Turn State / Trace | 状态、原因码与阶段耗时 |

这些 owner 通过类型化产物串联，不允许反向调用其他 owner 的私有方法，也不允许在
`event.extra` 中建立第二个可写事实源。

## 六、目标流程

### 6.1 Persona 路径

```text
official Pipeline / plugin handlers
  -> Router selects persona
  -> Capability Resolver resolves personal_expression tools once
  -> Prompt projects Persona context
  -> Request Adapter builds one ProviderRequest
  -> Persona lifecycle hooks run once
  -> Shared Agent Runner receives:
       business Persona tools
       + terminal persona_expression schema
  -> first model response
       -> persona_expression: finish immediately
       -> business tool call: execute, append result, continue loop
  -> final persona_expression
  -> response/done hooks
  -> Output Runtime
```

关键协议：

- `persona_expression` 与业务工具同时对模型可见，但它是 terminal action，不注册到普通
  FunctionTool Manager，也不触发 `OnUsingLLMTool`。
- 业务工具调用保持官方 `OnUsingLLMTool` / `OnLLMToolRespond` 语义。
- Provider 支持“任意工具 required”时，首轮要求模型选择业务工具或
  `persona_expression`；不应在首轮强制指定 `persona_expression`，否则业务工具永远没有机会。
- 模型返回业务工具后继续循环；模型返回 `persona_expression` 后立即终止，不再追加一次
  “最终表达调用”。
- 对不支持协议工具的 Provider，沿用 Output Contract 的显式受控降级，不静默伪装成功。
- 无工具普通消息的目标调用数是 Router 一次、Persona 一次。

### 6.2 Core 路径

```text
Router selects hybrid
  -> Core Planner
       -> not_required: Persona target flow
       -> execute:
            CoreExecutionSpec
            -> resolve core capabilities once
            -> bounded Core context projection
            -> shared request lifecycle and Agent runner
            -> Core result material
            -> Persona target flow
            -> Output Runtime
```

Core 与 Persona 共享工具执行引擎和请求生命周期，但不共享目标工具集、Prompt Profile、
终止协议或上下文预算。共享执行机制不等于混合职责。

### 6.3 群聊准入路径

```text
group message
  -> candidate evidence sources
       official wake / mention / reply
       recent bot-reply continuation
       legacy passive sample
       Personal Runtime observation
       plugin semantic candidate
  -> Group Admission Coordinator
       ignore / route_required / route_with_silent
  -> Router, only when admitted
  -> persona / hybrid / silent according to allowed mode set
```

候选来源只提交证据，不直接决定发送。建议保留以下区别：

| 候选来源 | 默认准入语义 |
| --- | --- |
| 官方命令或协议 Handler | 继续由官方 Pipeline 处理，不进入对话 Router。 |
| 明确 @、回复 Bot、确定性名称唤醒 | `route_required`，Router 只选 `persona/hybrid`。 |
| 插件语义判断“可能在叫 Bot” | `route_with_silent`，Router 可以复核并沉默。 |
| 短窗口自然续接 | 保留当前确定性续接窗口，再由统一 owner 记录原因。 |
| 长窗口模型续接、旧 2% 被动采样 | `route_with_silent`。 |
| Personal Runtime Observation | 只提供主动表达材料，不直接冒充当前消息唤醒。 |

这个矩阵在 Phase 6 实施前必须用真实群日志再次确认；Phase 1 不改变它。

## 七、分阶段实施

### Phase 0：基线、边界与回归样本

状态：已完成调查，文档化完成后关闭。

范围：

1. 固化 D-001 至 D-010。
2. 记录 Persona 无工具、Persona 单工具、Core 长上下文、Provider 超时、群聊低准入和同会话
   队头阻塞样本。
3. 确认现有配置、插件声明和官方 Pipeline 兼容边界。

验收：本文包含当前问题地图、性能基线、阶段顺序和停止线。

### Phase 1：统一 Persona 工具执行

状态：下一阶段，只允许实施本阶段。

目标：删除独立的 Persona 工具预判模型调用，让业务工具与终止
`persona_expression` 在一次标准 Agent 循环中协作。

预计涉及：

- `astrbot/core/interaction/expression_agent.py`
- `astrbot/core/agent/runners/tool_loop_agent_runner.py`
- `astrbot/core/astr_agent_tool_exec.py`
- `astrbot/core/output_contract.py`
- ProviderRequest / renderer 中组合业务工具与 terminal contract 的边界
- `tests/unit/test_interaction_expression_agent.py`
- `tests/test_tool_loop_agent_runner.py`

实施内容：

1. 为共享 Runner 增加明确的 terminal action 概念，或提供等价的可复用终止协议接口。
2. 将已解析的 Persona 业务工具与 `persona_expression` schema 组成同一轮可见能力。
3. 第一次模型调用允许选择业务工具或 terminal action。
4. 只有业务工具被调用时才执行工具并继续循环；terminal action 直接解析为
   `PersonaExpressionResult`。
5. 删除 `build_persona_tool_loop_instruction()`、`_run_persona_tool_loop()` 及只为两阶段调用存在
   的 `no_tool` 材料拼接。
6. 保持 Persona 生命周期 Hook 每个 Persona run 只运行一次；业务工具观察 Hook 按实际调用
   次数运行。
7. 保持工具附件、旧式可见输出收集和最终 Output 交付语义。
8. fallback Provider 不得重放已经发生的工具副作用；若 terminal 输出失败，只能基于同一 run
   的已记录材料继续或失败。

不变量：

- 不修改两类插件目标配置。
- 不改变工具默认属于 Core 的规则。
- 不改变 Pipeline Handler 和 `stop_event`。
- 不改变群聊 admission、Router 模式或历史窗口。
- `persona_expression` 不被当作业务工具执行。

验收标准：

1. 没有 Persona 业务工具时，Persona 只调用 Provider 一次。
2. 有 Persona 工具但模型不使用时，Persona 仍只调用 Provider 一次，并直接返回
   `persona_expression`。
3. 使用一个业务工具时，模型调用数为“业务工具轮次 + 终止轮次”，不再额外增加预判轮次。
4. 业务工具错误、超时和旧式 `event.send()` 输出能成为模型可见材料，不产生重复用户输出。
5. `OnLLMRequest`、`OnAgentBegin`、`OnLLMResponse`、`OnAgentDone` 顺序与当前 Persona 对外语义
   一致。
6. 同一工具副作用在 Provider fallback 中最多执行一次。

验证：

1. 扩展现有 ExpressionAgent 公共行为测试，覆盖无工具、工具未使用、单工具、工具失败、
   terminal 缺失和 fallback。
2. 扩展共享 Runner 的 terminal action 测试，不锁定私有方法调用顺序。
3. 使用私聊 `815049548` 的简单短消息进行日志 smoke test，确认
   `tool_executions=0` 时不存在独立预判请求。
4. 对比修改前后模型调用数、Prompt 大小、总耗时和 Hook 记录。

回滚或停止条件：

- Provider 无法在同一请求中稳定暴露业务工具与 terminal schema。
- 旧插件 Hook 顺序或工具结果语义发生不可接受变化。
- fallback 会重复执行有副作用工具。
- 需要修改目标配置或群聊逻辑才能让 Phase 1 工作。

遇到以上情况应停止本阶段，补齐共享 Runner 或 Provider capability，不得恢复长期双模型预判
作为“临时兼容”。

### Phase 2：统一工具与能力解析

状态：等待 Phase 1 稳定。

目标：每个 turn、每个 target 只形成一次 `CapabilitySnapshot`，Prompt 与执行消费同一快照。

预计涉及：

- `astrbot/core/plugin_runtime.py`
- `astrbot/core/prompt/collectors/tools_collector.py`
- `astrbot/core/prompt/collectors/system_collector.py`
- `astrbot/core/provider/func_tool_manager.py`
- Persona 与 Core request preparation

实施内容：

1. 建立公共 Resolver，输入 event、persona、target、插件配置和注册能力，输出只读 snapshot。
2. 保留现有优先级：用户精确工具覆盖、用户插件覆盖、工具声明、Core 默认值。
3. Prompt Collector 只投影 snapshot，不再自行查找或过滤工具。
4. Runner 只执行 snapshot 中的工具，不再二次从全局 Manager 解析。
5. 删除 `SystemCollector` 对 `ToolsCollector` 私有方法的依赖。
6. 记录工具被纳入或排除的稳定原因码。

验收标准：模型看到的工具名、schema 与 Runner 可执行工具完全一致；一次 target 运行只解析一次
工具集；配置兼容测试全部通过。

验证：复用 `test_interaction_plugin_runtime.py`，增加一个公开输入输出用例校验插件级覆盖与精确
工具覆盖，不为私有 Collector 调用顺序写测试。

回滚或停止条件：发现官方插件依赖在 Prompt 渲染后动态注册工具，或 snapshot 无法表达现有
事件过滤。先补公开扩展边界，不允许恢复多处独立解析。

### Phase 3：统一 ProviderRequest 与插件生命周期

状态：等待 Phase 2。

目标：一次最终分支只构建一个规范 ProviderRequest，并在一个稳定生命周期中运行 Hook、Agent
和 fallback。

预计涉及：

- `astrbot/core/interaction/expression_agent.py`
- Core Agent request preparation
- 共享 Agent lifecycle 模块
- ProviderRequest adapter 与 fallback provider binding

实施内容：

1. 固定顺序：Context projection -> render -> request adapter -> request hooks -> freeze effective
   capability -> agent begin -> model/tool loop -> response hooks -> agent done。
2. 插件 `OnLLMRequest` 可以继续修改公开请求字段；Hook 完成后冻结本次有效请求。
3. fallback 只替换 provider-specific binding，不重新运行业务 Hook，不深拷贝带 handler、event、
   Future 或 Context 的活对象。
4. 删除 ProviderRequest 快照差异回放和 Persona/Core 重复生命周期实现。
5. `OnLLMResponse` 明确位于 Persona 表达结果形成之后、Output 交付之前。

验收标准：每个分支只记录一个 request lifecycle id；无 `deepcopy` 活 handler；fallback 保留插件
修改且不重复副作用；Persona 和 Core 的 Hook 状态机由同一 executor 驱动。

回滚或停止条件：旧插件依赖未公开的对象身份或 Hook 重入。应增加边界适配器和迁移诊断，
不能让两套 lifecycle 都继续写状态。

### Phase 4：统一上下文事实与目标预算

状态：等待 Phase 3。

目标：历史事实只提取一次，各 target 在投影阶段应用独立、可观测且有限的预算。

预计涉及：

- `astrbot/core/prompt/collectors/conversation_history_collector.py`
- Prompt target projection 与 render profile
- Core execution preparation
- `astrbot/core/config/default.py`

实施内容：

1. 保持 Persona 历史窗口为 50 轮。
2. Router 和 Planner 继续使用窄上下文，不因 Persona 扩长而同步膨胀。
3. Core 不再允许生产请求实际无界；`max_context_length=-1` 必须由明确 token/消息硬上限兜底。
4. 对 conversation history、execution ledger、memory、tool schema 分别记录预算和截断原因。
5. 截断只发生在 target projection，不修改规范 Conversation 或 Memory 事实。
6. 具体 Core 默认上限在实施前以真实会话回放确定；不得直接凭感觉改一个数字。

验收标准：529 条历史样本不再原样进入 Core；Persona 仍能获得 50 轮；日志可看到每类材料
原始量、保留量、估算 token 和截断原因；回复质量回放无明显断层。

回滚或停止条件：截断导致 Core 丢失当前任务必要证据。应调整 CoreExecutionSpec 的任务材料与
对话历史分层，而不是恢复无界历史。

### Phase 5：统一超时、重试、fallback 与队头阻塞

状态：等待 Phase 4。

目标：一次 turn 使用一个单调递减 deadline；子阶段只能消费剩余预算，不能各自重新获得完整
超时。

预计涉及：

- Personal Runtime turn/session 调度
- Router、Planner、Persona 与 Core provider 调用
- `astrbot/core/provider/sources/openai_source.py`
- fallback provider resolution
- tool call timeout

实施内容：

1. 建立 `TurnDeadlineBudget`，为 route、plan、model、tool、fallback 分配可观测子预算。
2. Provider timeout 使用剩余预算的最小值。
3. 只对明确瞬态错误重试；上下文错误、schema 错误、鉴权错误和确定性客户端错误不得盲目
   重试 10 次。
4. fallback 共享同一 deadline，不重置总时钟。
5. 明确同 session 新消息的 absorb、cancel、queue 策略，避免一个慢 Core 请求让后续短消息等待
   数分钟。
6. 超时结果形成可表达的失败材料，并由 Persona 如实收口。

验收标准：任何 turn 的实际耗时不超过配置 deadline 加少量清理时间；120 秒 Provider 超时不再
被放大为约 361 秒；后续消息不再出现约 450 秒的不可解释队头等待；每次重试有错误分类和剩余
预算。

回滚或停止条件：Provider SDK 无法被外部 deadline 取消，或取消会留下继续执行的工具副作用。
先完成取消隔离，不得只在外层返回超时而放任后台继续写状态。

### Phase 6：统一群聊候选与准入

状态：等待 Persona/Core 延迟稳定后实施。

目标：所有群聊候选只产生 evidence，由一个 owner 决定忽略、强制路由或允许 Router 沉默。

预计涉及：

- `astrbot/core/pipeline/waking_check/stage.py`
- `astrbot/core/interaction/group_reply.py`
- `astrbot/core/interaction/conversation_activity_source.py`
- `astrbot/core/interaction/personal_runtime.py`
- 群聊候选插件边界

实施内容：

1. 定义 `GroupAdmissionEvidence` 与 `GroupAdmissionDecision`。
2. 收口官方唤醒、短窗口续接、模型续接、旧 2% 采样、Observation 和插件候选。
3. AngelHeart 等插件只提交“可能被呼唤/适合参与”的证据，不直接发送，也不绕过 Router。
4. 明确 `route_required` 与 `route_with_silent` 的允许模式集合。
5. 一条群消息最多生成一个准入决策和一次 Router 调用。
6. 删除候选布尔值、字符串和 `event.extra` 多处双写。

验收标准：每条未回复消息都有可查询的 admission reason；统计能区分“未进入 Router”和
“Router 选择 silent”；群 `1083316872` 与 `851957839` 的回放结果能用同一决策表解释；插件候选
不会提高为无条件回复。

回滚或停止条件：无法区分官方协议唤醒和语义候选，或统一后破坏命令/权限过滤。先补证据类型，
不能再叠加新的布尔标记。

### Phase 7：类型化状态、诊断与删除过渡路径

状态：最后收口。

目标：删除完成使命的字符串状态、兼容镜像和重复诊断，使性能问题能从单个 turn trace 直接
定位。

预计涉及：

- Interaction TurnState / Personal Runtime state
- `event.extra` 兼容投影
- Agent、Prompt、Provider、Group Admission 日志与 trace

实施内容：

1. 类型化 capability、admission、deadline、request lifecycle 和 terminal result。
2. 修正类型声明与实际返回不一致，例如声明 `str | None` 却返回 `False` 的续接辅助逻辑。
3. 为每个 turn 记录 admission、router、prompt build、provider wait、tool execution、fallback、
   expression 和 delivery 时间。
4. 统一稳定原因码，避免依赖自然语言日志猜测。
5. 删除被新 owner 替代的 `_interaction_*` extra、私有 callback、预判 prompt 和旧兼容分支。
6. 对仍需保留的公开兼容入口标注 owner、只读/写入方向和退出版本。

验收标准：关键状态只有一个可写事实源；单条慢消息可从 trace 直接解释各阶段耗时；静态检查
不再发现已知返回类型不一致；旧主链代码已删除而不是仅标记 unused。

## 八、旧插件兼容保证

### 保持不变

1. 插件无需新增声明即可继续加载。
2. 未声明的 LLM 生命周期 Hook 默认在 Persona Expression 生效。
3. 未声明的 FunctionTool 默认只在 Core 生效。
4. 用户配置继续高于插件或工具声明。
5. Pipeline Handler、命令、关键词、权限、白名单、事件终止和直接结果保持官方顺序。
6. Persona 工具实际调用时继续触发全局工具观察 Hook。
7. 旧 Persona 工具产生的文本和附件继续作为模型可见材料，最终用户可见文本仍由 Persona
   Expression 独占。
8. Core-only 插件不会因为 Persona 统一 Agent Runner 而被加载到 Persona 请求。

### 允许改变的内部实现

1. 删除独立 Persona 工具预判 Prompt 与模型调用。
2. Persona 与 Core 复用共享 Runner 和 lifecycle executor。
3. 工具 schema、执行对象和日志从同一 capability snapshot 派生。
4. fallback 不再通过深拷贝活对象或字段差异回放插件修改。
5. 群聊候选通过类型化 evidence 进入统一 admission。

### 兼容验证矩阵

| 插件类型 | 必测行为 |
| --- | --- |
| 仅 Pipeline Handler | 触发、终止、直接结果与旧路径一致。 |
| 默认 LLM Hook 插件 | 只在 Persona 生命周期运行一次。 |
| 显式 Core LLM Hook 插件 | Persona 不运行，Core 请求运行一次。 |
| 默认工具插件 | 只出现在 Core。 |
| 显式 Persona 工具插件 | 可在 Persona 正式 Agent 循环中调用。 |
| 产生旧式可见输出的 Persona 工具 | 输出被捕获为工具材料，不重复发送。 |
| 有副作用工具 | fallback、超时和重试不重复执行。 |
| 关键词替代回复插件 | 仍可终止后续 Persona/Core。 |

## 九、性能基线与目标指标

以下数字来自 2026-08-03 的本地日志样本，只用于对比，不作为跨 Provider 的绝对 SLA。

| 指标 | 当前样本 | 目标 |
| --- | ---: | ---: |
| 普通 Persona 路径的 Persona 模型调用 | 2 次 | 1 次 |
| Persona 工具未使用时的工具执行 | 0 次 | 0 次 |
| 普通 Persona 总耗时 | 约 13.4 秒 | 同 Provider 暖态下降至少 35%，主要以调用数验收 |
| Core 历史消息 | 最高观察到 529 条 | 不超过 target 配置与硬预算 |
| Core 输入 token | 约 17,419 | 有明确预算和截断诊断，不再随完整历史无界增长 |
| 慢 hybrid turn | 约 398.6 秒 | 不超过 turn deadline |
| 同会话后续短消息 | 约 450.6 秒 | 不再被前一请求无界阻塞 |
| 群聊未回复原因 | 需跨多处日志推断 | 每条候选有统一 admission/route 原因码 |

性能验收优先级：

1. 模型调用数与工具执行数。
2. Provider wait 与 Prompt token。
3. 总 turn latency。
4. 群聊 admission 和 Router 比例。

不得通过减少 Persona 50 轮历史、禁用插件 Hook、隐藏工具或跳过最终人格表达伪造性能提升。

## 十、风险与停止线

### 主要风险

1. 将 `persona_expression` 混入普通 ToolSet 后，被错误执行或触发插件工具 Hook。
2. Provider 的 tool choice 语义不同，导致首轮被强制终止或无法调用业务工具。
3. Plugin Hook 修改 ProviderRequest 后，snapshot 与实际请求不一致。
4. fallback 重复工具副作用或重复插件 Hook。
5. Core 上下文截断破坏长任务连续性。
6. turn deadline 只停止等待，没有真正取消后台 Provider 或工具任务。
7. 群聊统一 admission 时把明确唤醒、语义候选和主动 Observation 混为一类。
8. 为统一而新增巨型 Coordinator，把分散问题换成新的补丁吸附点。

### 全局停止线

出现以下任一情况时，停止继续扩展当前 Phase，先修复边界：

1. 需要同时修改三个以上后续 Phase 才能让当前 Phase 通过。
2. 新旧主链同时拥有写状态或发送输出的能力。
3. 无法用测试或日志证明插件 Hook 和工具副作用只执行一次。
4. 需要改变旧插件公开 API 才能继续。
5. 关键验证失败、相关工作树出现冲突修改，或无法回放真实日志样本。
6. 性能提升来自禁用功能，而不是删除重复工作或收紧预算。

## 十一、实施纪律

每个 Phase 都使用相同循环：

```text
re-read this plan
  -> inspect current code and dirty worktree
  -> confirm Phase scope and invariants
  -> implement one owner migration
  -> delete replaced path
  -> run minimal public-boundary validation
  -> review compatibility and diagnostics
  -> update this document and current-state docs
  -> commit only when explicitly requested
```

实施中必须遵守：

1. 不跨 Phase 顺手修复相邻问题。
2. Phase 内发现根因属于后续 Phase 时，记录证据，不提前搭第二套抽象。
3. 每次提交只包含一个可解释的 owner 迁移或与其不可分割的验证。
4. 完成新 owner 后，在同一 Phase 删除旧主路径。
5. 代码审阅优先检查重复模型调用、重复 Hook、重复工具副作用、重复发送和隐藏 fallback。
6. 文档中的当前事实必须在实现后同步，不能让计划描述被误认为现状。

## 十二、进度清单

- [x] Phase 0：记录基线、冻结目标配置与兼容边界。
- [ ] Phase 1：统一 Persona 工具执行，删除独立工具预判模型调用。
- [ ] Phase 1 验收：私聊 `815049548` 无工具样本只产生一次 Persona Provider 调用。
- [ ] Phase 1 验收：Persona 单工具、工具失败、附件、fallback 和 Hook 兼容通过。
- [ ] Phase 2：建立每 target 唯一 CapabilitySnapshot。
- [ ] Phase 2 验收：Prompt schema 与 Runner 工具完全一致。
- [ ] Phase 3：统一 ProviderRequest 与 Agent lifecycle。
- [ ] Phase 3 验收：无活对象 deepcopy、无 Hook 或副作用重放。
- [ ] Phase 4：统一上下文事实与 target 预算。
- [ ] Phase 4 验收：Core 529 条历史样本被稳定限界，Persona 保持 50 轮。
- [ ] Phase 5：统一 deadline、重试、fallback 和 session 队列。
- [ ] Phase 5 验收：长请求与后续短消息都受可解释总预算约束。
- [ ] Phase 6：统一群聊候选与准入。
- [ ] Phase 6 验收：两个目标群的未回复与回复原因可由统一决策解释。
- [ ] Phase 7：类型化状态与诊断，删除过渡路径。
- [ ] 全量兼容回放与最终架构审阅。

## 十三、相关文档

- [Yakumo 架构索引](../README.md)
- [当前状态](../current-state.md)
- [Interaction 模块](../modules/interaction.md)
- [Prompt 模块](../modules/prompt.md)
- [Prompt Development Plan](../prompt-development-plan.md)
- [Output Contract](output-contract.md)
- [Interaction Output Plugin Contract](interaction-output-plugin-contract.md)
- [Personal Runtime 前置主链清理计划](execution-backend-preparation-plan.md)
- [Input / Core / Output 目标态](input-core-output-target-state.md)
