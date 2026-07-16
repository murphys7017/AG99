# Persona Memory System Design

> **术语说明：** 本文中的 Prompt Selector 表述属于早期设计。当前 Prompt 取舍由确定性的目标投影完成，目标局部指令由 `PromptRenderProfile` 提供；memory 只提供读取快照，不决定 Prompt 目标或布局。

说明：

- 本文件主要记录 persona/memory 结合方向的设计思考
- 涉及 `MemoryOS`、TiMEM、`mk1` 的内容应视为参考来源，而不是当前代码已经按该路线完整落地
- 当前代码现状应以 `astrbot/core/memory/*`、`astrbot/core/prompt/*` 和相关进度文档为准

本文件用于统一 AstrBot 当前 memory 方向的顶层认知。

目标不是直接讨论某个实现细节，而是明确：

- 为什么 `conversation.history` 不应该继续按普通 collector 推进
- 为什么 memory 不能只停留在 prompt/context 优化层
- TiMEM、`mk1`、`MemoryOS` 分别适合借鉴什么
- AstrBot 内部最终应当形成怎样的 memory / state / persona 边界
- 后续 MVP 应该按什么顺序落地

## 一句话目标

AstrBot 后续的 memory 系统，目标不是“让 AI 记住更多历史消息”，而是：

> 让 AstrBot 中的 persona 能在长期互动中形成连续、可解释、可控的关系状态，并在后续对话中稳定体现出来。

换句话说：

- `memory` 不是数据堆积
- `memory` 也不是单纯的历史检索
- `memory` 的最终价值，是让 persona 具备时间连续性

## 当前阶段定位

当前 Yakumo prompt/context 改造仍处于第一阶段：

- 先收集
- 先准备结构化数据
- 先把链路打通
- 先保证日志可见
- 暂不进入统一 render
- 暂不替换现有 `ProviderRequest` 注入行为

当前 prompt 系统总框架仍然成立：

- `Collect -> Build -> Target Projection -> Profile -> Layout/Render -> Execute`

但 memory 相关部分需要额外补充一条长期演化链路：

- `Conversation -> Experience -> Memory -> State -> Persona -> Response`

这里的新增点不是把 memory 并进 prompt，而是建立一个与 prompt 平级的独立 memory 子系统，为后续 `Select / Render` 提供真正有意义的输入。

## Prompt、Memory 与 Post Process 的关系

后续架构中，`Prompt System`、`Memory System`、`Post Process System` 应当是平级模块，而不是从属关系。

推荐关系：

- `Runtime / Conversation` 产出事件与对话材料
- `Execution System` 负责完成本轮请求执行
- `Post Process System` 负责在回合完成后调度后处理任务
- `Memory System` 负责更新、存储、检索、生成 snapshot
- `Prompt System` 负责 collect、build、target projection、profile、layout、render、apply
- `Prompt System` 从 `Memory System` 读取数据

也就是说：

- memory 不属于 prompt 子系统
- prompt 不拥有 memory 生命周期
- prompt 不负责 memory update
- post process 不属于 memory 子系统
- memory 不拥有回合后调度权
- prompt 只消费 `MemorySnapshot`、`TopicState`、`PersonaState`

后续应避免出现这种关系：

- `Prompt -> 内部生成或更新 memory`
- `Memory -> 自己接管整条 post-turn lifecycle`

更合理的关系是：

- `Prompt <- MemorySnapshot / PersonaState / TopicState`
- `Execution Result -> Post Process System -> Memory System`

## 为什么不是 `conversation.history`

当前对 `conversation.history` 的新判断已经基本明确：

- 不建议新增一个简单的 `ConversationHistoryCollector`
- 不建议把 memory 问题理解为“再多收集一些历史消息”
- 不建议让 collector 直接承担摘要生成与记忆更新责任

原因如下：

### 1. 历史消息不是记忆

历史消息只是原始材料。

真正应该被系统消费的是：

- 哪些经历值得保留
- 这些经历说明了什么
- 它们如何改变当前关系与 persona 状态

因此，单纯的 `history collector` 只会提供更多上下文原文，不会直接产生长期价值。

### 2. `history`、`topic`、`state`、`memory` 不是一回事

后续需要区分：

- `conversation.history`: 原始对话材料或其受控摘要
- `current_topic`: 当前正在围绕什么继续聊
- `chat_state`: 当前交互所处状态
- `memory`: 经过筛选和固化后的长期或中期信息
- `persona_state`: 由 memory 沉淀出的长期行为偏置

这几类内容如果都混在一个 collector 或一个 summary 里，后续目标投影和 layout/renderer 都会失去边界。

### 3. 记忆更新不应发生在 prompt collect 阶段

collect 阶段只负责：

- 读取
- 标准化
- 写入 `ContextPack`
- 记录日志

记忆生成和更新更适合走：

- `post-turn processing`
- 异步 consolidation
- 独立 `Post Process System`
- 独立 `Memory Service`

## 本系统真正要解决的问题

AstrBot 当前已经具备：

- 会话与对话持久化
- 静态 persona 管理与解析
- prompt collect 基础链路
- 上下文压缩

当前真正缺少的是一个独立 memory 域，而不是 prompt 内部的补丁式 history 能力。

当前真正缺少的是：

### 1. Experience 抽取

从一次对话中判断：

- 什么是值得进入记忆层的经历
- 什么只是临时上下文
- 什么应该被忽略

### 2. Memory 固化

将经历沉淀为更稳定的信息，例如：

- 用户偏好
- 用户稳定事实
- 互动风格模式
- 长期项目进展
- 关系信号

### 3. State 建模

在 memory 之上，维护独立状态，而不是只存摘要文本：

- `current_topic`
- `chat_state`
- `relationship_state`
- `persona_state`

### 4. Persona 影响链路

如果 memory 不影响最终行为，那么它对 persona 来说就是不存在的。

后续必须形成：

- `memory snapshot -> persona-aware render -> response bias`

而不是只形成：

- `memory snapshot -> prompt appendix`

## AstrBot 当前模块映射

结合当前代码，后续 memory/persona 系统最适合与 prompt 系统并列存在，并通过只读接口接入 prompt。

建议的大边界应为：

- `Runtime / Conversation Layer`
- `Memory Layer`
- `Prompt Layer`
- `Post Process Layer`
- `Execution Layer`

其中：

- `Memory Layer` 负责写入与读取
- `Prompt Layer` 只负责消费来自 memory 的结果
- `Post Process Layer` 负责回合后任务调度

在这个前提下，后续 memory/persona 系统最适合长在以下边界之间：

### 已有基础设施

- `astrbot/core/conversation_mgr.py`
  - 负责 session / conversation 持久化与切换
- `astrbot/core/persona_mgr.py`
  - 负责 persona 解析与最终生效 persona 选择
- `astrbot/core/prompt/collectors/persona_collector.py`
  - 负责把 persona 信息转成 `ContextSlot`
- `astrbot/core/prompt/context_collect.py`
  - 负责 collect 协调和 `ContextPack`
- `astrbot/core/agent/context/*`
  - 负责上下文压缩
- `astrbot/builtin_stars/astrbot/long_term_memory.py`
  - 当前更像群聊历史增强，不是 persona growth system

### 建议新增的逻辑位置

后续应形成以下逻辑链：

- `Conversation/Event`
- `Execution`
- `Post Process`
- `Experience Extraction`
- `Memory Engine`
- `State Services`
- `Memory Collector`
- `Selector / Renderer`
- `Persona Resolve`

这里需要特别强调：

- `Memory Collector` 属于 Prompt Layer
- `Memory Engine`、`State Services` 属于 Memory Layer
- `Post Process Orchestrator` 属于 Post Process Layer

其中最关键的新增边界不是一个新的 prompt collector，而是：

- `MemoryService`
- `PersonaStateService`

## 三个参考对象分别借什么

## 1. `mk1`

当前对 `mk1` 的结论保持不变：

- 借设计思想
- 借模块边界
- 不直接照搬实现

最值得借鉴的部分：

- 同步回复与异步记忆更新分离
- 回合后处理应有单独阶段，而不是塞进 memory 本体
- memory system 与 assembler 分离
- `current_topic` / `chat_state` 单独建模
- 更新前先判断 `merge / new / none`
- prompt builder / node 风格适合作为未来 renderer 参考

不直接照搬的部分：

- 不直接复制其单体 memory runtime
- 不直接把摘要逻辑塞进主链路
- 不直接合并其全部 prompt 构造实现

## 2. TiMEM

TiMEM 的核心参考价值不在于“照搬一棵记忆树”，而在于它说明了三件事：

- memory 应该分层
- memory 应该沿时间轴固化
- 高层记忆应服务稳定 persona / profile

对 AstrBot 来说，TiMEM 最值得借的点是：

- 短期 / 中期 / 长期分层思想
- consolidation 优先于直接堆积历史
- retrieval 应该按任务目的选择层级
- persona/profile 应位于更高层，而不是和 raw history 混在一起

当前不建议直接照搬的点：

- 不建议第一版就完整实现 L1-L5 记忆树
- 不建议第一版就引入复杂 recall planner
- 不建议将高层 persona 直接等价为可随意改写的 prompt 文本

AstrBot 更适合先做最小三层：

- `Experience`
- `Session Insight`
- `Persona State`

## 3. `MemoryOS`

当前对 `MemoryOS` 的定位应继续保持为：

- `memory backend / memory engine`

即它负责：

- 记忆存储
- 记忆更新
- 记忆检索
- 用户画像或长期记忆管理

不由它直接负责：

- 当前输入 collect
- persona collect
- prompt renderer
- 最终 persona resolve 语义

也就是说：

- `MemoryOS` 是实现手段
- 不是 AstrBot 在 Yakumo 层暴露给上层的核心产品概念

对 AstrBot 内部更稳定的命名应是：

- `MemoryService`
- `MemorySnapshot`
- `PersonaStateService`
- `MemoryCollector`

这样即使未来底层实现替换，Yakumo 上层边界仍然稳定。

## 最终推荐的系统分层

后续推荐将系统明确拆成五层。

## 第一层：Collect Layer

负责：

- 从运行时读取结构化上下文
- 写入 `ContextPack`
- 提供日志和调试可见性

当前已存在：

- `PersonaCollector`
- `InputCollector`

后续新增：

- `MemoryCollector`
- `TopicStateCollector`

约束：

- 只读
- 不生成记忆
- 不更新状态
- 不改写底层 memory backend

## 第二层：Memory Engine Layer

负责：

- `Experience` 写入
- post-turn update
- consolidation
- memory retrieval
- 产出 memory snapshot

这里可以复用或集成 `MemoryOS`，也可以吸收 TiMEM 的分层思想。

这一层不应承担：

- prompt 渲染
- persona prompt 编排
- 当前输入 collect

## 第三层：State Layer

这是当前规划里最需要单独强化的一层。

建议明确建模：

- `current_topic`
- `chat_state`
- `relationship_state`
- `persona_state`

这层的意义在于：

- memory 是材料
- state 是当前生效的解释结果

后续真正影响 persona 的，不应该是一堆散乱记忆文本，而应是经过约束的状态。

## 第四层：Post Process Layer

负责：

- 在本轮请求完成后收集标准化回合材料
- 调度一个或多个 post processors
- 负责异步执行、失败隔离、日志记录

设计原则：

- 不重新发明 AstrBot 底层事件机制
- 优先复用现有 hook / event 时机
- 在现有 hook 之上增加统一编排层

当前可直接复用的时机至少包括：

- `OnLLMResponseEvent`
- `OnAfterMessageSentEvent`

也就是说，`Post Process Layer` 更像：

- 基于现有 hook 的 orchestration layer

而不是：

- 全新独立事件总线

这一层不应承担：

- memory store 本体
- prompt 渲染
- persona 解析

这层的关键关系是：

- `Execution Layer` 产出结果
- `Post Process Layer` 分发后处理任务
- `Memory Layer` 作为其中一个消费者执行更新

后续推荐的最小抽象包括：

- `PostProcessTrigger`
- `PostProcessContext`
- `PostProcessor`
- `PostProcessManager`

### `PostProcessTrigger`

用于描述“什么时候调用 post processor”。

第一版建议只抽象少量稳定触发点：

- `on_llm_response`
- `after_message_sent`

它们底层分别映射到 AstrBot 现有 hook。

### `PostProcessContext`

用于描述一次后处理调用可见的统一上下文。

建议至少包含：

- `event`
- `trigger`
- `provider_request`
- `llm_response`
- `conversation`
- `agent_stats`
- `timestamp`

### `PostProcessor`

表示一个独立的后处理单元。

例如未来可以有：

- `MemoryPostProcessor`
- `TracePostProcessor`
- `StatsPostProcessor`
- `SummaryPostProcessor`

每个 processor 都应满足：

- 独立注册
- 独立执行
- 失败隔离
- 可按 trigger 挂载

### `PostProcessManager`

负责：

- 注册 post processors
- 按 trigger 选择要执行的 processor
- 控制顺序
- 执行异常隔离
- 记录日志

## 第五层：Render Layer

负责：

- 将 persona、input、memory、topic、state、policy、capability 统一组织
- 决定哪些内容进入 system
- 决定哪些内容进入 history
- 决定是否渲染为独立 `<memory>`、`<topic>`、`<state>` 节点

这层才应该承担最终 prompt 结构控制。

这里的关键关系是：

- `Memory Layer` 产出 snapshot 和 state
- `Render Layer` 消费 snapshot 和 state

而不是：

- `Render Layer` 生成 memory

## Persona Continuity 在系统中的落点

后续需要明确区分三种 persona 相关概念：

### 1. Base Persona

由用户或系统配置的人格底座。

来源主要是当前的：

- `Persona`
- `system_prompt`
- `begin_dialogs`
- tools / skills 白名单

### 2. Dynamic Persona State

由长期互动逐步沉淀的动态状态。

例如：

- 对用户的熟悉度
- 关系距离
- 信任趋势
- 偏好的稳定判断
- 对当前用户更合适的回应风格偏置

### 3. Effective Persona

本轮真正参与响应生成的人格结果。

它应当由以下内容组合得到：

- `Base Persona`
- `Persona State`
- 当前 `Topic / Chat State`
- 所选 memory snapshot

这里最关键的原则是：

- 不直接改写原始 persona
- 不把长期成长结果直接覆盖回 `system_prompt`
- 而是在 resolve / render 阶段叠加动态状态

这样做的好处是：

- 可解释
- 可回滚
- 可调试
- 不易人格漂移失控

## 推荐的数据抽象

第一版不建议上复杂大而全模型，更适合先定义最小抽象。

### 1. Experience

表示一次值得记住的经历单元。

建议字段方向：

- `id`
- `umo`
- `conversation_id`
- `turn_range`
- `event_type`
- `content_summary`
- `participants`
- `importance`
- `confidence`
- `created_at`
- `source_refs`

### 2. Session Insight

表示一次会话或一段对话结束后的抽象总结。

建议包含：

- 本轮主要话题
- 是否形成新偏好
- 是否形成新长期事实
- 是否有关系信号变化
- 是否应合并已有记忆

### 3. Memory Snapshot

表示当前请求给 prompt 系统读取的只读视图。

建议至少拆成：

- `short_term_summary`
- `mid_term_summary`
- `long_term_facts`
- `user_preferences`
- `relationship_signals`
- `persona_adjustments`

### 4. Persona State

表示长期稳定但允许缓慢变化的动态人格状态。

建议第一版只保留少量受控维度，例如：

- `familiarity`
- `trust`
- `warmth`
- `formality_preference`
- `directness_preference`

原则是：

- 数量少
- 变化慢
- 可解释
- 可衰减

## Post Process 与 Memory Update 原则

后续 memory update 应优先采用回合后异步执行，但调度职责应属于 `Post Process System`，而不是由 prompt 系统或 memory 系统独自承担。

推荐流程：

1. 主链路完成本轮响应
2. `Post Process System` 收集本轮输入输出、conversation、相关上下文
3. `Post Process System` 调用 memory update 入口
4. memory engine 判断本轮是：
   - `merge`
   - `new`
   - `none`
5. 需要时生成 `Experience`
6. 需要时更新 `Session Insight`
7. 需要时更新 `Persona State`

这样做的收益：

- 不阻塞主回复
- 更容易做失败重试
- 更容易做审计和调试
- 更容易控制污染与漂移

## 当前推荐的 MVP 范围

第一版不建议直接追求“完整人格成长系统”，而是做最小闭环。

### MVP 目标

- 能在回合后产生受控 memory update
- 能在请求前读取 memory snapshot
- 能将 memory snapshot 作为外部输入接入 collect 链路
- 能通过有限的 persona state 影响最终 render

### MVP 建议顺序

#### 第一步：先定义稳定边界

明确新增接口或服务概念：

- `MemoryService`
- `PersonaStateService`
- `MemorySnapshot`
- `MemoryCollector`

#### 第二步：先做 post process 骨架

先不追求复杂检索，先跑通：

- 回合结束事件
- post process 调度
- memory 更新调用
- 写入最小 memory 结果

#### 第三步：先做最小状态建模

优先做：

- `current_topic`
- `chat_state`
- `persona_state`

不要一开始引入过多状态字段。

#### 第四步：接入 collect

新增：

- `MemoryCollector`
- 必要时新增 `TopicStateCollector`

collect 只负责读取 snapshot，不做生成。

#### 第五步：在 render 阶段体现行为影响

第一版只做少量可控行为偏置，例如：

- 是否更熟悉
- 是否更直接
- 是否更温和
- 是否更贴近用户惯用风格

不要第一版就尝试复杂情绪系统或自由人格漂移。

## 明确不做什么

当前阶段明确不做：

- 不做完整对话存档替代系统
- 不把 memory 等价成向量库检索
- 不让 memory backend 直接主导 prompt 结构
- 不直接让 LLM 自由改写 persona prompt
- 不把所有历史都强行塞进 system prompt
- 不在 prompt collect 阶段生成摘要或写 memory

## 最终结论

当前 AstrBot 的 memory 方向，应理解为：

- 不是补一个 `conversation.history` collector
- 不是单纯扩展上下文压缩
- 也不是把 `MemoryOS` 或 TiMEM 原样搬进来

而是建立一套新的长期链路：

> `Conversation -> Experience -> Memory -> State -> Persona -> Response`

在这条链路中：

- `mk1` 提供边界设计参考
- TiMEM 提供分层与时间固化参考
- `MemoryOS` 提供 memory engine 能力参考
- Post Process 系统负责回合后任务调度
- Memory 系统负责 update / store / retrieve / snapshot
- Yakumo prompt 系统负责 collect / select / render / execute，并从 memory 系统读取输入

最终要达成的不是“AI 更会引用过去”，而是：

> AI 在 AstrBot 中能够被过去的互动稳定塑造，并以可解释、可控制的方式体现为 persona 的连续性。
