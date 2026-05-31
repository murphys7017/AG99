# Yakumo Target State

Yakumo 的最终目标不是单纯把 AstrBot 从单体拆成多服务，而是把它从
`session-centric bot runtime` 演进成 `persona-centric interaction runtime`。

原版 AstrBot 的主组织方式更接近：

```text
平台适配器 -> session / conversation -> 选择 provider / persona -> 生成回复
```

Yakumo 的目标组织方式是：

```text
平台输入 -> 交互场景识别 -> Persona Runtime Shell -> 有效人格主体 -> 记忆 / 状态 / 能力编排 -> 对外表达
```

也就是说，`session` 不再是系统里的对话主体，而是输入来源、权限隔离和平台上下文；
`conversation` 不再承载全部人格连续性，而是一段具体 episode；真正持续存在并被长期互动塑造的主体应是
`persona`。

## 核心目标

Yakumo 的核心目标是让 AstrBot 中的人格成为稳定、可解释、可控制的对话主体。

最终系统应形成以下链路：

```text
Base Persona
    + Persona State
    + Memory Snapshot
    + Topic / Relationship / Interaction State
    + Current Input
    -> Effective Persona
    -> Response
```

其中：

- `Base Persona` 是用户配置的人格底座，包括静态 prompt、begin dialogs、工具和 skills 白名单。
- `Persona State` 是长期互动沉淀出的动态人格状态，例如熟悉度、信任、温度、正式程度偏好、直接程度偏好。
- `Memory Snapshot` 是 memory 系统在本轮请求前提供的只读记忆视图。
- `Topic / Relationship / Interaction State` 描述当前话题、关系状态和本轮交互状态。
- `Effective Persona` 是本轮真正参与响应生成的人格结果，不应直接覆盖原始 persona 配置。

Interaction middleware 在这个目标里应定位为 `Persona Runtime Shell`：它不是 persona
数据本体，也不是 memory / provider / capability 的所有者，而是一次交互中人格接收、判断、委派和表达的运行外壳。

## 边界原则

### 1. Session 是隔离边界，不是人格主体

`unified_msg_origin` 仍然必须存在。

它负责：

- 标识消息来源
- 区分平台、群聊、私聊、WebUI 等输入入口
- 承载平台权限、配置、provider 选择和会话级策略
- 防止不同平台或不同窗口的上下文互相污染

但它不应继续被当作长期人格连续性的核心。

### 2. Conversation 是 episode，不是完整记忆

`conversation_id` 仍然有价值。

它负责：

- 保存某一段具体对话历史
- 支持标题、切换、删除和 UI 展示
- 为 memory / postprocess 提供原始材料和来源引用

但 conversation history 只是材料，不等于记忆；它不应承担全部长期关系、偏好和人格状态。

### 3. Persona 是连续主体

persona 不应只被视为一段 system prompt。

它应逐步演进为：

- 静态人格配置的持有者
- 工具 / skills / 能力边界的声明者
- memory 和 state 的消费主体
- 跨 episode 维持连续表达的中心

同一个 base persona 面对不同用户、不同群体或不同场景时，可以形成不同的 `Persona State`。
因此长期状态的 key 不应只有 `persona_id`，还需要结合 `canonical_user_id`、scope、session 或 conversation 等边界。

### 4. Memory 塑造人格，但不直接改写人格底座

memory 系统的目标不是把更多历史塞进 prompt，而是让 persona 在长期互动中形成连续性。

约束：

- 不在 prompt collect 阶段生成或写入 memory。
- 不让 LLM 自由改写 base persona prompt。
- 不把长期成长结果直接覆盖回 `system_prompt`。
- 通过 `Persona State`、`Memory Snapshot` 和 render 阶段的受控组合影响本轮表达。

### 5. Interaction 负责本轮表达闭环

Interaction middleware 的职责不是替代 persona，而是承载一次 interaction turn，并作为
`Persona Runtime Shell` 编排本轮人格运行：

- 输入 materialization
- observation / route decision
- route decision
- turn owner
- core delegation
- output materialization
- finalized material
- postprocess handoff

它应围绕 `Effective Persona` 执行本轮表达，而不是把本轮交互状态混入 base persona。

长期人格状态、记忆、能力注册、provider 选择等不应内聚进 middleware。middleware 应调用独立服务：

- `EffectivePersonaResolver`
- `PersonaStateService`
- `MemorySnapshotReader`
- `RelationshipStateService`
- `CapabilityPolicy`
- `BodyOutputPolicy`

这样它是人格层的运行外壳，而不是新的全局大对象。

### 6. Desktop Body 是本地身体表现层

Yakumo 可以有一个或多个本地 presence client。AG99live 的目标定位是：

```text
AG99live = Yakumo Persona 的 Desktop Body / Presence Client
```

它不是某个聊天 session 的镜像，也不是直接监听群聊原文后自行吐槽。它应消费 Core 已经裁剪、授权和降噪后的身体表达意图：

```text
外部会话事件
  -> Input Runtime / Observation
  -> Persona Runtime Shell / Core
  -> visibility / privacy / importance / cooldown 判断
  -> Body Expression Intent
  -> AG99live Adapter
  -> AG99live Frontend
```

Desktop Body Output 是普通聊天输出之外的表现通道，用于本地可见的旁白、吐槽、状态、提醒和任务进度。
它不应自动泄露其他 session 的原文，也不应替代群聊或私聊中的正式回复。

推荐输出类型：

- `body.commentary`
- `body.state`
- `body.notification`
- `body.task_status`
- `body.attention_shift`
- `body.reflex`

每个 body intent 至少应能表达：

- `visibility`
- `privacy`
- `source_scope`
- `audience`
- `importance`
- `cooldown`
- `text` 或结构化状态
- `motion_hint` / `voice_hint` / `expression_hint`

## 目标结构

### 1. Agent Platform

作为主服务器部署。

职责：

- 统一网关
- 对外 API
- 主 Agent
- 会话路由和输入隔离
- Effective Persona 解析
- Persona Runtime Shell / interaction middleware
- Desktop Body Output 调度
- provider/stt/tts/message platform/persona/database 的基础接口访问
- subagent 调度
- 认证、配置、观测、状态管理

这一层负责“输入隔离、人格解析、人格运行、决策、编排、路由和输出调度”，不负责承载所有具体能力实现。

### 2. Capability Platforms

作为一个或多个独立平台或服务部署。

职责：

- 子 Agent 服务
- 插件服务
- Skills 服务
- Tool 执行服务
- Sandbox/Browser/Python/Shell 服务
- Knowledge Base 服务
- Cron 服务

这一层负责“执行、扩展、专用能力”。

## 最终运行效果

### 1. 主服务器只保留核心控制面

主服务器接收来自消息平台或 WebUI 的请求，完成：

- 用户会话识别
- 人格选择
- provider 选择
- 主 Agent 推理
- 子能力委派
- 结果汇总与回传

### 2. 子能力通过统一协议接入

插件、skills、subagent、tools 不再直接耦合到主 Agent 内部，而是通过统一协议或统一注册中心接入。

推荐统一抽象：

- `AgentService`
- `ToolRegistry`
- `CapabilityRegistry`
- `PersonaResolver`
- `EffectivePersonaContext`
- `ConversationStore`
- `ProviderGateway`
- `MessageGateway`
- `MemorySnapshotReader`
- `RelationshipStateService`
- `BodyOutputPolicy`
- `BodyExpressionIntent`

### 3. 主 Agent 只关注编排

主 Agent 的目标不是直接承载所有逻辑，而是：

- 基于 Effective Persona 判断是否直接回答
- 判断是否委派给子 Agent
- 判断是否调用插件/技能/工具
- 判断是否产出 Desktop Body Output
- 汇总外部能力返回结果
- 生成最终输出

### 4. 多平台并行扩展

最终可以支持：

- 一个主 Agent 平台
- 多个面向不同场景的子 Agent 平台
- 多个独立插件或工具执行节点
- 不同部署环境下的水平扩展

## 目标分层

### 1. Kernel Layer

纯 Agent 内核：

- runner
- tool loop
- handoff
- response
- context model

### 2. Platform Layer

主平台：

- 主 Agent
- API gateway
- 会话、Effective Persona、provider、消息平台接口
- Persona Runtime Shell
- Desktop Body Output
- orchestration

### 3. Capability Layer

扩展能力：

- plugins
- skills
- sandbox tools
- subagents
- kb
- cron

## 目标收益

### 1. 架构收益

- 主 Agent 与能力实现解耦
- 插件、技能、工具不再直接侵入内核
- session / conversation / persona / memory 的职责边界更清晰
- 系统边界更清晰

### 2. 工程收益

- 更容易测试
- 更容易替换底层实现
- 更容易做独立部署和灰度发布
- 更容易控制资源隔离

### 3. 产品收益

- 人格可以跨 episode 保持连续性
- 不同用户或场景下的人格状态可解释、可回滚、可调试
- AG99live 等本地 presence client 可以成为 persona 的身体表现层
- 群聊、私聊、任务和远程执行器状态可以被 Core 授权后转成本地身体表达
- 能支持多 Agent 协作
- 能支持不同能力节点独立扩容
- 能支持后续演进成真正的平台化架构

## 第一阶段不追求的效果

第一阶段目标不是立刻完成全面分布式化。

第一阶段只要求做到：

- 把 session、conversation、persona、memory 的语义边界写清楚并在代码中逐步收口
- 抽出 Effective Persona 的解析边界，避免主链路继续散落解析 persona / memory / state
- 将 interaction middleware 明确收口为 Persona Runtime Shell，而不是新的全局大对象
- 定义 Desktop Body Output / Body Expression Intent 的输出边界
- 把 Agent 基础接口抽出来
- 把主 Agent 平台和能力平台的代码边界拆出来
- 让插件、skills、tools、subagent 可以通过统一边界接入

等代码边界稳定后，再决定哪些模块独立进程化、哪些模块继续保留在同一部署单元。
