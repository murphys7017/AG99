# Memory System Design Spec

本文件是 AstrBot memory 模块的实现导向设计书。

说明：

- 本文件包含一部分较早期的方案讨论
- 涉及 `MemoryOS` 的内容应视为设计参考，而不是当前代码已经采用的唯一落地路线
- 当前代码现状应以 `astrbot/core/memory/*`、`docs/Yakumo/dev/memory/progress.md` 和 `docs/Yakumo/dev/memory/index.md` 为准

它建立在以下文档之上：

- `docs/Yakumo/dev/history/prompt-progress-memory-reference.md`
- `docs/Yakumo/dev/persona-memory-system-design.md`

前者解决“当前 prompt/context 路线走到哪里”，后者解决“memory 最终服务什么目标”，本文件进一步回答：

- memory 模块的职责边界是什么
- memory 数据对象应该长什么样
- 一次请求前后，memory 生命周期如何流转
- AstrBot 应提供哪些稳定接口
- MVP 应先实现哪些内容

## 文档目标

本设计书不追求一步到位定义完整人格成长系统，而是先为 AstrBot 建立一个可以逐步演化的 memory 基础设施。

当前阶段的直接目标是：

- 为 `post process -> memory update` 建立骨架
- 为 `memory snapshot` 建立读取接口
- 为 `collect -> build -> target projection -> render` 提供稳定读取输入
- 为 `persona continuity` 预留状态沉淀位置

## Prompt、Memory 与 Post Process 的系统关系

后续在 AstrBot 中，`Prompt System`、`Memory System`、`Post Process System` 应当是平级模块。

推荐关系：

- `Runtime / Conversation Layer` 产出对话材料
- `Execution System` 负责本轮请求执行
- `Post Process System` 负责回合后任务调度
- `Memory System` 负责 update、store、retrieve、snapshot
- `Prompt System` 负责 collect、build、target projection、profile、layout、render、apply
- `Prompt System` 从 `Memory System` 读取 `MemorySnapshot`、`TopicState`、`PersonaState`

这意味着：

- memory 不属于 prompt 子系统
- prompt 不负责 memory update
- post process 不属于 memory 子系统
- memory 不拥有回合后调度权
- prompt 只消费 memory 的读取结果

## 一、设计范围

当前 memory 模块负责：

- 记录和管理中长期记忆
- 在回合后执行受控 memory update
- 输出当前请求可消费的 `memory snapshot`
- 维护最小可解释的 `persona state`
- 为后续 renderer 提供结构化 memory / state 数据

当前 memory 模块不负责：

- 直接构造最终 prompt
- 接管现有 `PersonaCollector` 或 `InputCollector`
- 直接替换 `ConversationManager`
- 直接替换上下文压缩
- 让 LLM 自由改写 persona prompt

当前 prompt 模块不负责：

- 生成或更新 memory
- 持有 memory store
- 决定 memory consolidation 逻辑

当前 post process 模块负责：

- 接收回合完成事件
- 构造标准化后处理上下文
- 调度一个或多个 post processors

当前 post process 模块不负责：

- 直接持有 memory store
- 决定具体 memory schema
- 直接构造 prompt

## Post Process System 设计

`Post Process System` 不是重新发明一套新的事件机制，而是建立在 AstrBot 现有 hook / event 时机之上的统一编排层。

第一版建议明确复用以下现有时机：

- `EventType.OnLLMResponseEvent`
- `EventType.OnAfterMessageSentEvent`

也就是说：

- 底层仍使用 AstrBot 已有 hook
- 上层由 `PostProcessManager` 统一调度 processor

### 1. `PostProcessTrigger`

表示一次后处理触发点。

第一版建议只定义：

- `on_llm_response`
- `after_message_sent`

后续如有必要，再扩展其他 trigger。

### 2. `PostProcessContext`

表示一次后处理执行的统一输入。

建议至少包含：

- `event`
- `trigger`
- `provider_request`
- `llm_response`
- `conversation`
- `agent_stats`
- `timestamp`

原则：

- 所有 post processors 尽量共享同一份上下文模型
- 不让每个 processor 自己从各处拼隐式依赖

### 3. `PostProcessor`

表示可独立注册的后处理单元。

建议能力模型：

- 有稳定 `name`
- 可声明支持的 `trigger`
- 可接收统一 `PostProcessContext`
- 可独立失败，不影响其他 processor

典型示例：

- `MemoryPostProcessor`
- `TracePostProcessor`
- `StatsPostProcessor`

### 4. `PostProcessManager`

职责：

- 注册 processors
- 维护 trigger 到 processors 的映射
- 构造或接收 `PostProcessContext`
- 顺序执行 processors
- 做异常隔离和日志记录

原则：

- manager 只负责编排
- 不承担 memory 业务逻辑
- 不承担 prompt 业务逻辑

## 二、核心设计原则

### 1. Memory 不是历史记录副本

memory 应该来源于对话，但不等于原始对话。

原始对话由：

- `Conversation`
- `platform_message_history`

等现有结构负责持久化。

memory 层只保存：

- 经筛选后的经历
- 中期抽象
- 长期稳定认知
- 与 persona 连续性有关的状态

### 2. 同步回复与异步记忆更新分离

memory update 默认应走异步回合后流程，但这个流程的调度应由 `Post Process System` 负责：

- 主链路只负责尽快生成回复
- 回合完成后由 post process 调度 memory consolidation

这样可以避免：

- 回复阻塞
- collect 阶段职责膨胀
- 难以调试的隐式状态写入

### 3. 先状态化，再人格化

memory 不应直接变成 prompt 文本。

推荐路径是：

- `memory -> state -> persona-aware render`

而不是：

- `memory -> long text appendix`

### 4. 动态状态应受控、可回滚、可解释

第一版动态状态不应过多，也不应过快变化。

应优先使用：

- 小数量字段
- 明确语义
- 缓慢变化
- 支持衰减
- 保留来源与置信度

### 5. 上层边界稳定，底层实现可替换

不论底层最终是：

- 内部自研 memory store
- 集成 `MemoryOS`
- 吸收 TiMEM 的分层思想

Yakumo 上层应稳定围绕以下对象组织：

- `MemoryService`
- `MemorySnapshot`
- `PersonaStateService`
- `MemoryCollector`

## 三、模块边界

建议将 memory 相关逻辑拆为 5 个子模块。

说明：

- 这里定义的是 `Memory System` 内部子模块
- `Post Process System` 不属于 memory 内部模块，而是外部并列系统

### 1. `MemoryService`

职责：

- 接收 memory update 请求
- 驱动 consolidation
- 读写 memory store
- 生成请求前可消费的 `MemorySnapshot`

它是 memory 子系统的统一门面。

说明：

- `MemoryService` 不直接监听 AstrBot hook
- 它通过 `MemoryPostProcessor` 被 `PostProcessManager` 间接调用

### 2. `MemoryStore`

职责：

- 持久化 `Experience`
- 持久化 `SessionInsight`
- 持久化 `PersonaState`
- 支持按 session / user / conversation 查询

它不负责：

- prompt 组织
- selector 策略
- 最终 renderer 决策

### 3. `MemoryConsolidator`

职责：

- 接收本轮对话材料
- 判断本轮更新类型：
  - `merge`
  - `new`
  - `none`
- 产出 memory 写入操作
- 产出 persona state 更新操作

### 4. `PersonaStateService`

职责：

- 维护动态 persona state
- 提供读取接口给上层 resolver / collector
- 控制状态衰减与修正

它应与 `PersonaManager` 协作，但不直接取代当前静态 persona 系统。

### 5. `MemoryCollector`

职责：

- 读取 `MemorySnapshot`
- 转换为 `ContextSlot`
- 写入 `ContextPack`
- 记录调试日志

它必须保持只读，不得在 collect 阶段更新 memory。

说明：

- `MemoryCollector` 在职责上属于 `Prompt System`
- 但它读取的数据归 `Memory System` 提供

### 6. `MemoryPostProcessor`

职责：

- 作为 `PostProcessor` 的一个实现挂入 `PostProcessManager`
- 从 `PostProcessContext` 中提取 memory update 所需材料
- 构造 `MemoryUpdateRequest`
- 调用 `MemoryService.update(...)`

说明：

- `MemoryPostProcessor` 属于 `Post Process System` 与 `Memory System` 的桥接层
- 它不等于 `MemoryService`

## 四、建议的请求生命周期

## A. 请求前

请求前阶段应发生：

1. 获取当前 `Conversation`
2. 获取当前 `Base Persona`
3. 通过 `MemoryService.get_snapshot(...)` 读取 memory snapshot
4. 通过 `PersonaStateService.get_state(...)` 读取 persona state
5. 由 prompt system 中的 collectors 将：
   - persona
   - input
   - memory
   - topic/state
   写入 `ContextPack`

当前阶段中：

- `PersonaCollector` 和 `InputCollector` 已存在
- `MemoryCollector`、`TopicStateCollector` 还未实现

## B. 请求中

主请求阶段：

- 仍然使用现有主链路构建 `ProviderRequest`
- 暂不在本阶段直接调用 memory update
- 暂不在 collect 阶段写 memory

在当前 target projection / layout / renderer 链路中：

- 只消费 snapshot
- 不直接改写 memory backend

## C. 请求后

当本轮回复生成完成后：

1. AstrBot 现有 hook 触发后处理入口
   - 例如 `OnLLMResponseEvent`
   - 或 `OnAfterMessageSentEvent`
2. `PostProcessManager` 根据 trigger 创建或补齐 `PostProcessContext`
3. `PostProcessManager` 调度匹配 trigger 的 processors
4. `MemoryPostProcessor` 作为其中一个 processor 执行 memory 更新逻辑
5. `MemoryPostProcessor` 采集本轮材料
   - 用户输入
   - AI 输出
   - conversation 引用
   - request 上下文
6. `MemoryPostProcessor` 组装 `MemoryUpdateRequest`
7. `MemoryConsolidator` 评估：
   - 是否形成新经历
   - 是否需要合并已有记忆
   - 是否需要更新 topic/state
   - 是否需要更新 persona state
8. `MemoryStore` 落库
9. 下一轮请求读取新的 snapshot

## 五、最小数据模型

本节只定义 AstrBot 内部稳定抽象，不强制绑定最终数据库 schema。

## 1. `MemoryUpdateRequest`

表示由 `Post Process System` 交给 `Memory System` 的一次 memory update 输入。

建议字段：

- `umo: str`
- `conversation_id: str | None`
- `platform_id: str | None`
- `session_id: str | None`
- `persona_id: str | None`
- `user_message: dict`
- `assistant_message: dict`
- `recent_context: list[dict]`
- `message_timestamp: datetime`
- `source_refs: list[dict]`

说明：

- `user_message` 和 `assistant_message` 可以先用统一 message dict 表示
- `recent_context` 可只保留有限轮数
- `source_refs` 用于后续可解释性和调试

## 2. `Experience`

表示一条被判定为“值得记住”的经历。

建议字段：

- `experience_id: str`
- `umo: str`
- `conversation_id: str | None`
- `scope_type: str`
- `scope_id: str`
- `category: str`
- `summary: str`
- `importance: float`
- `confidence: float`
- `participants: list[str]`
- `keywords: list[str]`
- `source_refs: list[dict]`
- `created_at: datetime`
- `updated_at: datetime`

建议的 `category` 初值：

- `user_fact`
- `user_preference`
- `relationship_signal`
- `project_progress`
- `interaction_pattern`
- `episodic_event`

说明：

- `scope_type/scope_id` 用于支持后续 session 级、user 级、persona 级区分
- `importance` 用于控制保留和召回优先级
- `confidence` 用于控制是否影响长期状态

## 3. `SessionInsight`

表示一段对话结束后，针对当前会话抽象出的中期认知。

建议字段：

- `insight_id: str`
- `umo: str`
- `conversation_id: str | None`
- `time_bucket: str`
- `topic_summary: str | None`
- `progress_summary: str | None`
- `new_facts: list[dict]`
- `new_preferences: list[dict]`
- `relationship_signals: list[dict]`
- `merge_action: str`
- `created_at: datetime`
- `updated_at: datetime`

说明：

- `time_bucket` 可先用简单粒度，如 `session` 或日期字符串
- `merge_action` 表示本轮更接近：
  - `merge`
  - `new`
  - `none`

## 4. `TopicState`

表示当前会话短期内持续有效的话题状态。

建议字段：

- `umo: str`
- `conversation_id: str | None`
- `current_topic: str | None`
- `topic_summary: str | None`
- `topic_confidence: float`
- `last_active_at: datetime`

说明：

- `TopicState` 可以先独立于 `PersonaState`
- 它更多服务短期上下文连续性

## 5. `ChatState`

表示当前交互所处的短期状态。

建议字段：

- `umo: str`
- `conversation_id: str | None`
- `state_name: str`
- `state_reason: str | None`
- `confidence: float`
- `updated_at: datetime`

第一版可以非常克制，只允许少量状态，例如：

- `default`
- `task_oriented`
- `casual_chat`
- `deep_discussion`

## 6. `PersonaState`

表示长期稳定但允许缓慢变化的动态人格状态。

建议字段：

- `state_id: str`
- `scope_type: str`
- `scope_id: str`
- `persona_id: str | None`
- `familiarity: float`
- `trust: float`
- `warmth: float`
- `formality_preference: float`
- `directness_preference: float`
- `evidence_refs: list[dict]`
- `updated_at: datetime`

说明：

- 数值范围建议统一，例如 `0.0 ~ 1.0`
- 这些值不应由单轮消息剧烈改变
- 应支持后续衰减和人工修正

## 7. `MemorySnapshot`

表示某次请求前供 prompt system 读取的只读视图。

建议字段：

- `umo: str`
- `conversation_id: str | None`
- `short_term_summary: str | None`
- `mid_term_summary: str | None`
- `long_term_facts: list[dict]`
- `user_preferences: list[dict]`
- `relationship_signals: list[dict]`
- `current_topic: dict | None`
- `chat_state: dict | None`
- `persona_state: dict | None`
- `debug_meta: dict`

说明：

- `MemorySnapshot` 是读取模型，不是持久化实体
- 它的结构应稳定，便于 collect / render 消费

## 六、scope 设计

memory 系统必须从一开始就考虑 scope，不然后续很容易混淆“这是谁的记忆”。

建议至少区分：

### 1. `session`

对应：

- 某个 `umo`
- 某个具体会话上下文

适合保存：

- 当前话题
- 短期状态
- 与当前 session 强绑定的中期信息

### 2. `user`

对应：

- 跨 conversation 的同一用户或同一关系对象

适合保存：

- 稳定偏好
- 长期事实
- 关系信号
- persona state

### 3. `persona`

对应：

- 某 persona 在特定用户或会话下的动态演化结果

这层是否第一版落地可以暂缓，但设计上应预留。

## 七、Consolidation 策略

`MemoryConsolidator` 的核心任务不是“总结”，而是“裁决 + 更新”。

建议第一版按三段式处理：

### 1. Detect

判断本轮是否值得写入 memory。

可以考虑的信号：

- 用户明确表达了稳定偏好
- 用户透露了稳定事实
- 本轮形成了清晰项目进展
- 本轮出现了明显关系信号
- 本轮只是普通闲聊，无需写入

输出：

- `merge`
- `new`
- `none`

### 2. Consolidate

如果需要更新，则产出：

- 新的 `Experience`
- 新的或更新后的 `SessionInsight`
- 新的或更新后的 `TopicState`
- 新的或更新后的 `PersonaState`

### 3. Validate

在写入前做最小校验：

- 是否和已有事实冲突
- 是否置信度过低
- 是否只是一次性噪声
- 是否超过短期变动阈值

## 八、读取策略

memory 的读取应是“按用途读取”，不是“全量取回”。

这里的“用途”由 Prompt 系统的目标投影和 layout/renderer 决定，但读取动作本身仍由 memory system 提供接口完成。

第一版建议先做简单策略：

### 1. 默认读取

默认读取：

- `current_topic`
- `chat_state`
- 最近 `SessionInsight`
- 有限数量的高置信度长期 facts / preferences
- 当前 `PersonaState`

### 2. 按场景扩展

后续 selector 阶段可以按任务类型决定：

- 闲聊更偏向关系和语气状态
- 任务协助更偏向项目进展和偏好
- 自我/关系问题更偏向长期认知和 relationship signals

### 3. Token 预算约束

后续 renderer 或 selector 需要支持：

- 优先注入 state
- 再注入短期 topic
- 最后才扩展更多 memory facts

理由：

- state 对行为影响更直接
- 过量 memory 文本会稀释 prompt 信号

## 九、与现有 AstrBot 模块的接入点

## 1. 与 `ConversationManager` 的关系

`ConversationManager` 仍然负责：

- conversation 持久化
- 当前 session 绑定的 conversation

memory 模块读取它的结果，但不替换它。

## 2. 与 `PersonaManager` 的关系

`PersonaManager` 仍然负责：

- 静态 persona 解析
- 默认 persona 选择
- session 规则覆盖

memory 模块新增的是：

- `PersonaStateService`

两者关系应为：

- `PersonaManager` 解析 `Base Persona`
- `PersonaStateService` 提供 `Dynamic Persona State`
- 后续 resolver / renderer 组合得到 `Effective Persona`

## 3. 与 `ContextPack` 的关系

memory 在 prompt collect 阶段应只通过：

- `MemoryCollector`
- `TopicStateCollector`

把 snapshot 写入 `ContextPack`。

这意味着：

- `ContextPack` 只看见读取结果
- 看不见底层更新过程

## 4. 与现有 `long_term_memory.py` 的关系

当前 `astrbot/builtin_stars/astrbot/long_term_memory.py` 更像：

- 群聊历史缓冲
- 群聊 prompt 增强
- 主动回复辅助上下文

它不应直接扩展为新的 memory 架构核心。

如果后续需要兼容：

- 可作为额外输入源
- 但不应作为中长期 persona memory 的主实现

## 十、调试与可解释性

memory 系统如果缺少可解释性，后续会非常难维护。

第一版就建议保留：

### 1. source refs

每条经验、每次状态更新都应尽可能关联来源：

- 来自哪次 conversation
- 来自哪轮输入输出
- 为什么触发更新

### 2. debug meta

`MemorySnapshot` 建议包含：

- 本次使用了哪些记忆
- 哪些候选被丢弃
- 当前 persona state 来源

### 3. 可审计更新记录

建议至少保留最小日志：

- update request 摘要
- consolidator 决策
- 写入结果

## 十一、MVP 实现建议

第一版建议分四步。

### 第一步：定义抽象与接口

先实现最小接口，不急着完整接 MemoryOS：

- `MemoryService`
- `PersonaStateService`
- `MemorySnapshot`
- `MemoryUpdateRequest`

### 第二步：打通 post process 骨架

目标：

- 在主链路响应完成后，能触发一次 post process 调度
- 通过 post process 调用 memory update
- 即使内部先是空实现，也先把时机打通

### 第三步：实现最小持久化

优先落地：

- `SessionInsight`
- `TopicState`
- `PersonaState`

`Experience` 可以先简化存储，不必第一版就做复杂检索。

### 第四步：接入 collect

新增：

- `MemoryCollector`
- 可选 `TopicStateCollector`

让 `ContextPack` 中先出现：

- `memory.snapshot`
- `session.current_topic`
- `session.chat_state`
- `persona.dynamic_state`

## 十二、明确暂缓项

当前建议暂缓：

- 完整多层 memory tree
- 复杂 recall planner
- 大规模 embedding / rerank 方案
- 自动无限学习
- 自由文本人格漂移
- 多 persona 复杂冲突求解

这些内容都可以建立在当前设计书定义的接口之上，后续渐进演化。

## 十三、最终结论

AstrBot 的 memory 模块第一版应被定义为：

> 一个与 Prompt System 平级、由 Post Process System 在回合后驱动更新、独立于静态 persona、逐步沉淀经验、状态与长期认知，并在请求前以 `MemorySnapshot` 形式提供给 Prompt System 消费的基础服务。

这意味着它的核心价值不是：

- 保存更多聊天记录
- 做一个新的历史摘要器

而是：

- 从对话中筛出有价值的经历
- 将经历固化为可读可控的状态
- 为后续 persona continuity 提供稳定输入

后续真正的完整链路应是：

`Conversation -> Execution -> Post Process -> MemoryUpdateRequest -> Consolidator -> Store -> Snapshot -> Collect -> Build -> Target Projection -> Profile -> Layout/Render -> Effective Persona -> Response`
