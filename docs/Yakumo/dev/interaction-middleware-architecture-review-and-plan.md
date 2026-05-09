# Interaction Middleware Architecture Review And Refactor Plan

本文件用于说明 AstrBot `interaction middleware` 的架构诊断、已执行修复、当前状态，以及后续修复计划。

它不是 bug 清单，也不是一次性重构提案，而是一份面向实现的收口文档。重点回答三件事：

- 当前中间件到底哪里“不像一个整体”
- 这些问题的根因是什么
- 后续应如何只在中间件内部做最小侵入、最大兼容的修复

本文件讨论范围以 `astrbot/core/interaction/*` 为主，必要时会提及 `astrbot/core/memory/postprocessor.py`，但不以修改 adapter、前端或其他平台层为前提。

## 当前阅读状态

本文件是一份持续演进的架构收口记录，不是只描述当前代码状态的静态报告。

阅读时请按以下边界理解：

- `已确认的问题` 到 `函数级现状与修复步骤`：历史诊断与早期修复依据，主要描述 Phase 1-5 之前的旧状态。
- `当前进度快照`：当前代码已经完成的真实状态。
- `分阶段修复计划` 中第一阶段到第五阶段：已完成或已完成第一轮的康复记录。
- `第六阶段：开发期 fail-fast 与 fallback 去正确性化`：第一轮代码已落地，后续继续清理剩余保护边界。

截至 `30578c4e refactor: consolidate interaction outbound phase`：

- `InteractionTurnState` 已经是 interaction 内部主状态源。
- streaming phase 已经 state-first。
- prompt / result / stream 插件扩展点已改为只读阶段视图。
- outbound phase 已完成第一轮收口，interaction turn 的 TTS / t2i / reply prefix / reasoning display 已迁入 `InteractionOutputController`。
- `RespondStage` 和 `ResultDecorateStage` 已不再拥有 interaction turn 的 completion / decoration 语义。

因此，下文早期章节中的“缺少统一 turn-level state”“发消息尚未接管”等问题，应理解为历史问题和改造动机；当前剩余重点是继续审查 fail-fast 边界，避免内部保护路径被当成正确性证明。

## 一句话结论

当前 `interaction middleware` 已经不再是一个薄拦截层，而是一个事实上的交互编排层。

在 Phase 1-5 之前，问题不在于它“不能工作”，而在于它还没有形成统一的回合模型。旧状态更像是：

- 在入站链路上挂了一层决策
- 在出站链路上挂了一层表达和流式观察
- 在结果末端挂了一层最终改写
- 在回合结束后再反向整理历史与记忆

因此当时它更像“沿链路附着的一组功能”，而不是“围绕同一 turn state 运行的一套系统”。

当前代码已经基本完成 turn state、streaming phase、只读插件视图、outbound phase，以及开发期 fail-fast 的第一轮收口。下一步重点不是继续证明这些主结构存在，而是清理剩余保护边界，避免它们继续被误用为正确性基础。

## 目标与边界

本轮修复计划从一开始遵循以下原则，当前仍然有效：

- 只动中间件主链路，不以修改 adapter 为前提
- 优先修复根因，不以下游补偿作为正确性证明
- 保持旧字段兼容，避免破坏现有插件和核心调用方
- 中间件自己的历史、输出和记忆以中间件真实可见输出为准

本计划不追求：

- 一次性重写整个 interaction 子系统
- 改造平台消息协议
- 要求前端必须理解新增字段后才可工作

## 历史系统定位

从职责上看，Phase 1-5 之前的中间件已经承担了四类工作：

1. 路由决策
2. 用户可见输出编排
3. 语言表达层改写
4. 本地交互历史沉淀

对应代码入口主要是：

- `astrbot/core/interaction/middleware.py`
- `astrbot/core/interaction/output_controller.py`
- `astrbot/core/interaction/finalizer.py`
- `astrbot/core/interaction/context_builder.py`
- `astrbot/core/interaction/memory_store.py`

这个定位本身不是问题。历史问题在于这些职责虽然都在中间件里，但并不是围绕一个统一的“本轮交互状态对象”在运转。当前代码已经通过 `InteractionTurnState` 完成主状态源收口。

## 历史诊断：已确认的问题

本节记录的是 Phase 1-5 之前的历史问题，用于解释为什么需要这轮架构收口。它不等同于当前代码状态；当前状态请以 `当前进度快照` 和后续阶段记录为准。

## 1. 缺少统一的 turn-level state

当前一轮交互的重要信息分散在多处：

- `InteractionDecision`
- `_interaction_immediate_reply`
- `_visible_turn_outputs`
- `_interaction_core_stream_text`
- `_interaction_visible_message_counter`
- `_interaction_core_final_result_consumed`
- `_interaction_core_streaming_result_consumed`
- `_interaction_*_failed`

这些字段大多通过 `event.extra` 传递。这样做可以工作，但有两个结构性问题：

- 没有单一真相源，多个函数各自从 `extra` 中拼接自己需要的局部状态
- 新能力接入时往往不是接入统一模型，而是新增一个额外字段和一段新的链路逻辑

这会导致系统越来越像“在事件对象上挂元数据”，而不是“围绕 turn state 运行”。

## 2. 多个能力被接到同一链路上，但没有共同宿主

当前主要能力包括：

- immediate reply
- stream observation
- stream interjection
- finalizer
- decision context build
- result contribution merge
- visible output recording
- legacy interaction memory cache 与 memory service 写入边界
- turn postprocess

它们都围绕“同一轮对话”工作，但没有共同的一等对象承载这轮对话。

后果是：

- 每个能力都要自己重新理解“这一轮”
- 每个能力都要自己决定该读哪些字段
- 每个能力都需要隐式假设其他能力已经做过什么

这就是当前“像硬凑出来的整体”的根因。

## 3. 一轮中存在多个“会说话的阶段”，但没有统一话语模型

同一轮里，中间件可能会发出多种用户可见文本：

- immediate reply
- stream interjection
- passthrough visible message
- core reply
- finalized core reply
- result contributor override 之后的最终文本

这些文本都在用户视角里表现为“同一个助手在说话”，但内部生成机制是分开的。

当前缺少统一定义：

- 每种文本属于哪种 utterance 类型
- 哪些文本可以进历史
- 哪些文本只用于过渡、不进入记忆
- 哪些文本可以覆盖前面的表达

目前这部分逻辑是存在的，但主要靠局部约定，而不是统一的话语模型。

## 4. immediate reply 与 stream interjection 本质相近，却分属两套系统

两者本质上都属于：

> 核心执行尚未结束时，中间件主动说一句话。

但当前实现中：

- immediate reply 在 `middleware.py` 的决策分支中产生
- stream interjection 在 `output_controller.py` 的流式观察过程中产生

它们分别有：

- 不同的触发时机
- 不同的上下文准备方式
- 不同的存储语义
- 不同的记忆策略

这不是代码错误，但逻辑上不收口。它们应该至少共享同一种“进行中 utterance policy”。

## 5. finalizer 的职责边界仍然偏模糊

`finalizer.py` 现在承担“最终表达层”的职责，但它并不是同一轮文本生成链路中的自然最后一步，而是一层追加改写。

因此当前架构里存在一个模糊点：

- 中间件到底是在“决定谁处理”
- 还是在“替 core 组织用户可见表达”

如果答案是后者，那它就已经是 orchestrator，而不是薄中间件。

这个定位需要在代码结构上被承认，否则实现会持续表现出“路由器代码里混了表达层逻辑”的样子。

## 6. 上下文构建存在重复建模

当前至少有两个地方会构建 interaction 上下文：

- 决策阶段
- 流式观察阶段

严格来说，当前至少有三个阶段在各自拼装“本轮上下文材料”：

- `decision_agent.py` 中的决策阶段
- `output_controller.py` 中的流式观察阶段
- `finalizer.py` / 最终结果整理阶段对本轮材料的局部重组

这说明系统缺少可复用的 turn-local context material。

直接问题有两个：

- 性能上重复构建
- 语义上不同阶段看到的“本轮状态”不一定完全一致

一旦后续再引入新的阶段性能力，这个问题会进一步放大。

## 7. middleware history 方向已经正确，但对输出链路完整性要求很高

当前设计已经明确：

- middleware 的历史应来自 middleware 自己真实发出的可见内容
- 不再以 core 原始 conversation history 作为主上下文来源

这个方向是对的，但也意味着：

- 任一用户可见路径漏记，会导致 interaction memory 丢失上下文
- 任一路径重复记，会导致 interaction memory 污染
- 任一路径记错 turn，会导致历史错配

换句话说，历史模型已经收口了，但它对“输出路径是否全部接入统一记录点”的要求更高了。

## 8. visible 与 memory_relevant 的边界刚建立，但还没有上升为系统规则

目前已经有一个重要边界：

- `visible_output`: 用户确实看见了
- `memory_relevant`: 这段内容是否应该进入 interaction memory

这个边界是合理的，也是必要的。

但目前它主要由个别调用点在维护，还没有被抽象成统一规则。例如：

- 哪些类型默认 `memory_relevant=False`
- 未来新增 utterance 类型时谁来决定其记忆语义
- 最终持久化时是否应统一过滤某些阶段性输出

如果不继续收口，后续新增功能时还会再次出现“这段话到底算不算历史”的争议。

## 9. 当前中间件的真实架构与代码表象不一致

从行为上看，它已经是一个交互编排器。

但从组织方式上看，很多代码仍然表现为：

- 拦一下
- 判断一下
- 记一点状态
- 下游再补一点

这会导致维护者产生误判，以为这里只是一个薄层，结果在阅读时不断碰到：

- 输出语义
- 记忆语义
- 后处理调度
- 多消息生命周期

这也是后续维护容易越来越乱的原因。

## 10. `decision_agent.py` 仍然游离在统一回合模型之外

当前 `InteractionDecisionAgent` 自己承担了：

- build interaction context pack
- 提取 persona / memory / input payload
- 组装 recent messages
- 构建 decision context

这会带来一个关键问题：

- 即使 middleware 已经引入统一 `turn state`
- 只要 decision agent 仍然自己独立构建 context
- 系统仍然会保留“同一轮上下文被重复构建”的根因

因此 `decision_agent.py` 不能被视为中间件外部模块，它必须纳入 Phase 1 的改造范围。

## 11. `core_bridge.py` 仍然依赖 `event.extra` 解析状态

当前 `core_bridge.py` 负责把：

- `InteractionDecision`
- `CoreTaskSpec`
- execution context block

注入到 core 的 `ProviderRequest` 中。

这条链路的方向是对的，但它当前仍然偏向：

- 从 `event.extra` 取 decision
- 再从 decision 反推出 `CoreTaskSpec`

如果 turn state 成为统一状态源，`core_bridge.py` 就不应继续承担“解析状态”的职责，而应退化为一个薄桥接层：

- 从 turn state 读取已决议的 `CoreTaskSpec`
- 负责把结构化执行意图注入 core request

否则 turn state 只能算“新增状态”，而不是“主状态源”。

## 12. 并发模型尚未定义

当前流式观察链路会创建多个并发任务，它们会围绕同一轮交互读写共享状态。

现状中共享写入主要落在：

- `event.extra`
- stream observation state
- visible outputs
- streaming text buffers

引入 `InteractionTurnState` 之后，如果不提前定义并发模型，问题只会从“分散写 extra”变成“分散写 state”。

必须明确：

- 哪些字段允许并发写
- 哪些字段只能串行写
- 谁拥有写权限
- 插件扩展点是否只能看到只读快照

## 13. 插件扩展点还没有与统一 turn state 对齐

当前存在三类扩展点：

- prompt contributors
- stream deciders
- result contributors

其中：

- `InteractionResultView` 已经开始收口只读视图
- prompt contributor 和 stream decider 仍然更偏向独立参数输入

如果 turn state 成为统一状态源，而插件扩展点仍然各吃各的参数，那么中间件内部统一了，扩展面仍然是散的。

因此插件扩展点也需要对齐为：

- 面向 turn state 的只读阶段视图
- 而不是继续传播大量松散参数

## 14. `message_chain_delivery.py` 已经进入主路径，需要补充边界定义

`message_chain_delivery.py` 负责消息链的物理拆分和发送，它已经位于中间件的用户可见输出主路径上。

因此必须明确它与未来 `InteractionUtterance` 的边界：

- `InteractionUtterance` 负责语义物化
- `message_chain_delivery.py` 负责物理投递与拆分
- delivery 层不应感知 turn state 的业务语义

如果不提前写清楚，后续很容易把 turn 语义继续下沉到 delivery 层。

## 15. 测试迁移策略尚未定义

当前测试大多围绕：

- `event.extra`
- middleware 输出结果
- interaction memory 持久化副作用

如果后续把 turn state 变成主状态源，测试也必须同步演进，否则会出现：

- 新实现已经改成 state 驱动
- 测试仍然只验证旧 extra 语义

这会让双写兼容期的测试价值下降，也不利于后续删除旧字段。

## 16. `AstrMessageEvent.extra` 只是兼容承载，不应被误认为长期归宿

短期内把 `InteractionTurnState` 挂到 `event.extra["_interaction_turn_state"]` 上是正确的兼容策略。

但它只应被视为：

- 兼容落点
- 生命周期共享通道
- 与现有 core / plugin / postprocess 机制桥接的临时承载

长期方向仍应是：

- turn state 由 middleware 自身 runtime context 持有
- `event.extra` 仅保留必要桥接字段

## 根因分析

以上问题可以归结为同一个根因：

> interaction middleware 缺少一个统一的 `turn state` 和统一的 `turn lifecycle owner`。

具体表现为：

- 没有一个对象显式表示“这一轮交互”
- 没有一个对象显式管理“这一轮已经说过什么”
- 没有一个对象显式定义“这一轮何时完成、何时可持久化、何时触发 postprocess”
- 不同阶段通过 `event.extra` 松散协作，而不是通过同一个状态模型协作

因此系统只能表现为“附着式功能集合”。

## 修复目标

后续修复应把 interaction middleware 收口成：

> 一个以 turn 为核心、以用户可见 utterance 为主要材料、以兼容旧 extra 字段为边界的交互编排层。

这个目标拆开后包括四件事：

1. 引入统一 `InteractionTurnState`
2. 建立统一 `InteractionUtterance` 模型
3. 收口统一 turn lifecycle
4. 让 memory / postprocess 只消费中间件显式产出的 turn material

## 目标结构

建议在中间件内部引入以下一等对象。

## 1. InteractionTurnState

建议至少包含：

- `turn_id`
- `session_id`
- `platform_id`
- `user_input`
- `persona_id`
- `decision`
- `utterances`
- `stream_state`
- `visible_message_counter`
- `completion_state`
- `memory_state`
- `postprocess_state`
- `error_state`

旧的 `event.extra` 字段暂时继续保留，但只作为兼容映射层，不作为新的主状态源。

## 2. InteractionUtterance

建议将所有用户可见文本统一为同一种结构，再按类型区分：

- `immediate_reply`
- `stream_interjection`
- `passthrough`
- `core_reply`
- `core_stream`
- `finalized_reply`

每条 utterance 至少包含：

- `turn_id`
- `message_id`
- `kind`
- `text`
- `visible`
- `memory_relevant`
- `source`
- `created_at`

这样可以统一解决：

- message id 生成
- 可见输出记录
- memory 归档材料
- postprocess 可见材料来源

## 3. InteractionTurnLifecycle

建议明确一轮交互的生命周期：

1. `turn_created`
2. `decision_resolved`
3. `pre_core_utterance_emitted`
4. `core_stream_observing`
5. `core_visible_output_completed`
6. `turn_material_finalized`
7. `turn_postprocess_dispatched`
8. `turn_completed`

`InteractionMemoryStore` 不再是 completion 写入 owner；它只保留为
decision/context 构建阶段的 legacy interaction cache。interaction turn 的记忆写入
由 `AFTER_TURN_COMPLETED` postprocess / memory service 消费 finalized material 后负责。

后续任何新能力都只能声明自己接入哪个阶段，而不是自己再额外定义一段时序。

## 并发与可见性模型

为了让 `InteractionTurnState` 可落地，必须同时定义其并发与可见性约束。

建议采用保守模型：

1. `InteractionTurnState` 本身保持可变，但不允许外部任意字段直写
2. 中间件内部提供有限的状态写入入口
3. stream 相关共享状态使用独立 `asyncio.Lock`
4. 对插件与辅助模块只暴露只读视图或阶段性 snapshot
5. 非 stream 阶段尽量保持串行推进，不为了“并发好看”牺牲时序清晰度

推荐分层如下：

- `turn metadata`: 基本不可变，创建后只读
- `decision material`: 决策完成后只读
- `utterance ledger`: 允许追加，不允许原地重写历史 utterance
- `stream state`: 允许并发更新，但必须通过受控入口与锁保护
- `completion flags`: 只能单向推进，不允许回退

不建议一开始就引入过重的 immutable + CAS 方案。当前更适合：

- 有限可变状态
- 明确的 owner
- 小粒度锁
- 对外只读

## 插件扩展点对齐原则

统一 turn state 后，插件扩展点不应直接拿到可变 state 对象，而应按阶段拿到只读视图。

建议分为三类视图：

1. `InteractionDecisionView`
   - 给 prompt contributors 使用
   - 提供 persona / memory / input / recent messages / core capabilities

2. `InteractionStreamView`
   - 给 stream deciders 使用
   - 提供 turn metadata、已有 utterances、当前 stream buffer、当前窗口材料

3. `InteractionResultView`
   - 给 result contributors 使用
   - 提供 decision、immediate reply、core result、final result、turn metadata

原则是：

- 插件扩展点看到的是“阶段性只读事实”
- 不是“整个可变 turn state”
- 这样既能统一扩展口，又不会把中间件内部实现细节泄漏出去

## 函数级现状与修复步骤

下面按实际主链路函数说明当前行为、存在的问题，以及进入函数后的目标步骤。

## 一、`InteractionMiddleware._handle_inbound_async()`

文件：

- `astrbot/core/interaction/middleware.py`

### 当前进入函数后的步骤

1. 刷新 interaction 配置
2. 生成新的 `turn_id`
3. 调用决策器获取 `InteractionDecision`
4. 把 `turn_id` 和 decision 附着到 `event.extra`
5. 根据 `route_mode` 分三条分支：
   - `SELF_REPLY`
   - `HYBRID`
   - `DELEGATE_TO_CORE`
6. 在不同分支里分别决定：
   - 是否先发 immediate reply
   - 是否立刻结束可见回合
   - 是否异步持久化 interaction memory
   - 是否转发给 core

### 当前问题

- 这是整轮交互的事实入口，但没有显式创建 `turn state`
- 后续所有函数都要再次从 `event.extra` 反推这一轮状态
- `SELF_REPLY`、`HYBRID`、`DELEGATE` 的共性逻辑没有被提升为统一回合生命周期

### 修复后的目标步骤

1. 刷新配置
2. 显式创建 `InteractionTurnState`
3. 将 state 写入 `event.extra["_interaction_turn_state"]`
4. 运行决策器，并把 decision 写入 state
5. 根据 decision 计算本轮初始生命周期阶段
6. 对外保留旧兼容字段：
   - `_turn_id`
   - `_interaction_decision`
   - `_interaction_persona_id`
7. 进入统一分支调度：
   - `SELF_REPLY`: 只执行 middleware utterance，随后完成 turn
   - `HYBRID`: 先执行 middleware utterance，再把 turn 交给 core 完成
   - `DELEGATE_TO_CORE`: 直接交给 core，但 turn owner 仍然是 middleware
8. 无论走哪条分支，最终都应通过统一 turn 完成函数收口

## 一点五、`InteractionDecisionAgent.decide()`

文件：

- `astrbot/core/interaction/decision_agent.py`

### 当前进入函数后的步骤

1. 检查是否命中协议命令绕过
2. 取 decision provider
3. 独立调用 `build_interaction_context_pack(...)`
4. 独立提取：
   - persona payload
   - interaction memory payload
   - recent messages
   - input payload
5. 组装 `decision_context`
6. 收集 prompt contributors
7. 构造 decision prompt
8. 调用 decision model
9. 解析 JSON 并生成 `InteractionDecision`

### 当前问题

- 它自己重复构建了 interaction context
- 即使 middleware 已经有 turn state，这里仍然可能看到另一份“本轮材料”
- 它是 turn state 收口中最容易被遗漏的根因点

### 修复后的目标步骤

1. 从 `InteractionTurnState` 读取已缓存 context material
2. 只在 cache 缺失或显式要求 refresh 时重新构建
3. 使用 state 中的 material 组装 `decision_context`
4. prompt contributors 改为消费 decision view，而不是松散参数
5. 产出的 `InteractionDecision` 回写到 turn state

## 二、`InteractionMiddleware._finalize_turn()`

文件：

- `astrbot/core/interaction/middleware.py`

### 当前进入函数后的步骤

1. 从 `InteractionTurnState` 读取 finalized turn material。
2. 校验 material、`turn_id`、`assistant_text` 均已显式存在。
3. 调度 `AFTER_TURN_COMPLETED` postprocess，并传递 explicit turn material。
4. 标记 postprocess dispatched / completed。

### 当前问题

- 旧实现曾从 `visible_reply` 或 visible outputs 反推材料；该路径已移除。
- 旧实现曾在 middleware completion 里直接写 interaction memory；该职责已移交给 postprocess / memory service。
- 当前剩余重点是保证所有 outbound persist 请求前都已经显式 materialized，并且 postprocess 能看到同一份 material。

### 修复后的目标步骤

1. 只消费 `InteractionTurnState.finalized_turn_material`
2. 如果 material 尚未 finalized，则记录 turn finalization failure
3. 调度 `AFTER_TURN_COMPLETED` postprocess
4. 在 state 中标记 `postprocess_dispatched=True`
5. 标记 `completed=True`，表示 middleware lifecycle handoff completed

## 三、`InteractionOutputController.capture_message_chain()`

文件：

- `astrbot/core/interaction/output_controller.py`

### 当前进入函数后的步骤

1. 判断 message 是否为空
2. 判断当前是否正在发 immediate reply
3. 判断这条消息是否是 streaming finish 标记
4. 判断这条消息是不是 core final model result
5. 根据分类选择：
   - immediate reply 直接发
   - passthrough 直接发并持久化
   - core final result 进入单一 core reply handler
6. 发送后把文本记录进 `_visible_turn_outputs`

### 当前问题

- 它实际已经承担“出站编排中心”，但对外看起来像一个 send wrapper
- 它内部混合了：
  - 消息分类
  - 可见发送
  - output record
  - completion handoff
  - finalizer 调用
- 这些能力没有围绕统一 utterance 模型组织

### 修复后的目标步骤

1. 从 `InteractionTurnState` 读取当前 turn 状态
2. 将传入消息先转成 `InteractionUtteranceCandidate`
3. 根据当前 turn phase 和消息来源分类为：
   - immediate utterance
   - passthrough utterance
   - core final utterance
   - streaming finish marker
4. 对分类结果统一执行：
   - 物化 `InteractionUtterance`
   - 生成 `message_id`
   - 发送
   - 写入 turn state 的 `utterances`
5. 若该 utterance 被标记为 turn-closing candidate，则进入统一 finalize turn material 逻辑
6. postprocess handoff 不再由各分支各自决定，而由统一 turn 收口阶段决定；memory 写入由 postprocess / memory service 消费 finalized material 后负责

## 四、`InteractionOutputController.capture_streaming()` / `_wrap_core_stream()`

文件：

- `astrbot/core/interaction/output_controller.py`

### 当前进入函数后的步骤

1. 标记 `_interaction_core_streaming_active`
2. 用 `_wrap_core_stream()` 包装 core 的原始流式生成器
3. 在包装器中累计：
   - `total_text`
   - `pending_text`
4. 每达到 `stream_observation_min_chars` 就发起一次观察
5. 每个观察窗口都可能触发 stream interjection
6. 流结束后等待观察任务完成
7. 把累计的 stream text 记录为可见输出

### 当前问题

- 这是典型的“沿链路挂功能”，而不是“turn state 里的 stream phase”
- 窗口观察、插话、累计文本、最终落库都挤在一起
- stream interjection 的上下文不是 turn-local material，而是现场重建

### 修复后的目标步骤

1. 进入函数时先拿到 `InteractionTurnState.stream_state`
2. 将 streaming phase 标记为 `observing`
3. 每个 chunk 只做一件事：更新 state 中的 stream buffer
4. 当 buffer 达到观察阈值时，调度统一的 `observe_stream_window(state)` 逻辑
5. `observe_stream_window` 决定是否创建 `stream_interjection` utterance
6. 所有 interjection 都走统一 utterance 发送路径
7. 流结束后统一收口：
   - flush 最后一段 pending buffer
   - 等待观察任务
   - 物化 `core_stream` utterance
   - 更新 turn phase

## 五、`InteractionOutputController._decide_stream_interjection_with_model()`

文件：

- `astrbot/core/interaction/output_controller.py`

### 当前进入函数后的步骤

1. 取 provider
2. 调用 `_build_stream_interjection_prompt()`
3. 发起模型调用
4. 解析 JSON
5. 返回 `StreamObservationDecision`

### 当前问题

- 该能力的上下文准备与 decision 阶段重复
- 它没有直接消费 turn state，而是重新 build prompt context
- 它和 immediate reply 的逻辑边界没有统一定义

### 修复后的目标步骤

1. 从 `InteractionTurnState` 读取：
   - 用户输入
   - persona material
   - interaction memory snapshot
   - 已有 utterances
   - 当前 stream buffer
2. 构造统一的 “in-progress turn utterance decision” prompt
3. 只允许输出是否插话及一句短句
4. 返回统一的 `UtteranceDecision`
5. 若允许插话，则交由统一 utterance materializer 处理

## 六、`InteractionOutputController._deliver_core_reply()`

文件：

- `astrbot/core/interaction/output_controller.py`
- `astrbot/core/interaction/finalizer.py`

### 当前进入函数后的步骤

1. 读取 core 结果纯文本
2. 调用 `finalize_response(...)`
3. 如果 finalizer 失败且是 force 模式，则记录失败并抛错
4. 合并 result contributors
5. 发送最终消息
6. 记录 visible output
7. 持久化 interaction memory

### 当前问题

- finalizer、result contributor、最终发送混在同一层
- 这是“最终用户可见 reply”的核心路径，但没有统一的 final materialization 阶段
- finalizer 的职责是“表达层整理”还是“结果改写器”，当前边界不够清晰

### 修复后的目标步骤

1. 接收 core 原始消息，物化 `core_result_candidate`
2. 根据 turn policy 决定是否进入 finalizer
3. finalizer 只负责输出“最终文本建议”，不直接决定发送
4. result contributors 只在统一 final material 阶段合并
5. 物化最终 `core_reply` 或 `finalized_reply` utterance
6. 发送 utterance
7. 将其标记为本轮 closing utterance
8. 由统一 turn completion 逻辑执行后续 memory / postprocess

## 六点五、`apply_interaction_core_task_spec()`

文件：

- `astrbot/core/interaction/core_bridge.py`

### 当前进入函数后的步骤

1. 从 `event.extra` 读取 interaction decision 或 `CoreTaskSpec`
2. 构建 execution context block
3. 将 block 注入 `ProviderRequest.system_prompt`

### 当前问题

- bridge 仍然承担了一部分状态解析职责
- 它还没有正式切换到“以 turn state 为唯一读取源”

### 修复后的目标步骤

1. 从 `InteractionTurnState` 读取已决议的 `CoreTaskSpec`
2. 若当前 turn 不需要 core task spec，则直接返回
3. 构建 execution context block
4. 注入 `ProviderRequest`
5. 旧 extra 字段仅作为兼容镜像，不再作为主读取路径

## 七、`finalize_response()`

文件：

- `astrbot/core/interaction/finalizer.py`

### 当前进入函数后的步骤

1. 检查是否允许 finalizer
2. 根据内容长度、结构化标记等判断是否需要改写
3. 组 prompt
4. 调用模型
5. 返回改写文本

### 当前问题

- 它是一个独立模块，但输入材料仍然偏散
- 它只看当前文本，不是真正看完整 turn state
- 它容易和 earlier utterance 在语气上发生轻微漂移

### 修复后的目标步骤

1. 输入改为 `InteractionTurnState + core_result_candidate`
2. prompt 明确区分：
   - 本轮用户输入
   - 本轮已经说过的 middleware utterances
   - core 原始结果
   - 本轮最终话语边界
3. 输出只允许是一份最终文本建议
4. 不直接触发发送，不直接写记忆

## 八、`build_interaction_context_pack()` / `extract_recent_messages()`

文件：

- `astrbot/core/interaction/context_builder.py`

### 当前进入函数后的步骤

1. 使用 `PersonaCollector`、`InputCollector`、`InteractionMemoryCollector`
2. 构造 interaction 专用 context pack
3. 从 `memory.interaction.recent_turns` 中提取 recent messages

### 当前问题

- 方向正确，但它服务的是多个阶段的“重新构建”
- 缺少 turn-local cached material
- 每个阶段都可能单独调用一次

### 修复后的目标步骤

1. 在 turn 创建阶段就完成一次 interaction context materialization
2. 将以下结果写入 `InteractionTurnState`：
   - persona payload
   - input payload
   - interaction memory payload
   - recent messages
3. 后续阶段优先复用 state 中缓存
4. 只有在显式声明需要 refresh 时才重新构建

## 九、`MemoryPostProcessor._resolve_interaction_turn_material()`

文件：

- `astrbot/core/memory/postprocessor.py`

### 当前进入函数后的步骤

1. 读取 `turn_id`
2. 从 `visible_outputs` 中筛出当前 turn
3. 过滤 `memory_relevant=False` 或 `stream_interjection`
4. 从剩余输出拼接 assistant text
5. 构造本轮 conversation history material

### 当前问题

- 它仍然是在 postprocess 阶段“反推这一轮到底说了什么”
- 如果 turn material 在 middleware 内部能显式产出，这里就不应该自己再推理一次
- 现在虽然逻辑已比以前统一，但仍然带有“收尾补推断”的味道

### 修复后的目标步骤

1. 由 middleware 在 turn completion 时显式生成 `interaction_turn_material`
2. postprocessor 直接读取该 material
3. postprocessor 不再负责解释：
   - 哪些 visible outputs 算 canonical reply
   - 哪些 utterance 应排除
4. postprocessor 只负责消费统一 material 并执行记忆更新

## 当前进度快照

截至当前实现，以下方向已经基本落地：

- `InteractionTurnState` 已成为 interaction 内部主状态源。
- `core_bridge.py` 已只从 turn state 读取 decision / core task spec。
- `decision_agent.py` 已优先复用 turn state 中缓存的 context material。
- `InteractionUtterance`、`InteractionStreamState`、`InteractionTurnCompletionState` 已建立。
- streaming phase 已迁移为 state-first 的 buffer / observation / interjection / final materialization 链路。
- prompt / stream / result 三类插件扩展点已开始使用只读阶段视图。
- memory postprocessor 对 interaction turn 已只消费显式 `turn_material`，不再 fallback 到 provider/context/prompt 推断。
- STT / 入站语音 materialization 已前移到 interaction middleware decision 之前。
- outbound phase 已完成第一轮收口：interaction turn 的 reply prefix / reasoning display / TTS / t2i 已迁入 `InteractionOutputController`。
- `RespondStage` 已不再对 interaction turn 调度普通 `AFTER_TURN_COMPLETED`。
- `ResultDecorateStage` 已对 interaction turn 提前退场，不再运行旧装饰链路或 decorating hook。
- `InteractionOutputController` 无 middleware persist callback 时不再自完成 turn，而是记录 `missing_persist_callback`。
- `InteractionOutputController` 在请求 middleware persist 前会先显式 materialize finalized turn material；persist callback 不再承担 material 构造职责。
- `InteractionMiddleware._schedule_turn_postprocess()` 缺 finalized material 时不再现场重建 material，而是记录 `missing_finalized_turn_material`。
- `InteractionMiddleware._finalize_turn()` 已改为只消费显式 finalized material；缺 material / turn_id / assistant_text 都是 completion contract failure，不再从 visible reply 或 visible outputs 现场反推。
- `InteractionMiddleware._finalize_turn()` 不再写 `InteractionMemoryStore`；interaction turn 的主记忆写入 owner 已收口到 memory postprocessor / memory service。
- `InteractionResultView.decision` 已改为只读 snapshot。
- `InteractionUtterance.metadata` 已用于记录实际投递形态，memory/final material 仍只消费 semantic text。
- interaction middleware 开发期拒绝 `fallback_policy` 配置；内部主链路不提供体验兜底模式。
- decision provider missing / timeout / model error / non-json / invalid payload / low confidence 在 `fail_fast` 下抛错。
- 入站 STT provider missing / path resolution failed / provider error / empty transcription 在 `fail_fast` 下终止本轮正常 decision。
- finalizer provider missing / timeout / model error / empty output 在 `fail_fast` 下抛错；forced finalizer failure 不发送替代文本。
- `InteractionTurnFailure` ledger 已建立，关键失败入口会记录 stage、reason、exception、用户可见动作和 completion 状态。
- decision agent 若返回旧 fallback decision，middleware 会记录 failure 并拒绝继续。
- 通用平台 live audio 语音路径已识别为独立协议：`action_type=live` 必须进入 core audio streaming，不能由 interaction decision 选择 `SELF_REPLY` 或带普通文本 immediate reply 的 `HYBRID`。
- `action_type=live` 事件现在由 middleware 生成显式 `DELEGATE_TO_CORE` protocol decision，并转交 core 的 `run_live_agent()` 产生 `audio_chunk`。
- `audio_chunk` 中的 `Json({"text": ...})` 已进入 stream buffer 与 finalized material；音频 base64 仍只作为平台 streaming payload，不进入 memory text。
- SELF_REPLY 缺少 immediate reply 已前移到 decision validation；middleware 只拒绝契约违规，不再补救转 core。
- SELF_REPLY 成功路径会在 visible completion 后显式 materialize turn material，再进入统一 finalization。
- SELF_REPLY / HYBRID immediate reply 失败与 visible completion 失败在开发期直接暴露，不再转 core 掩盖。
- stream interjection decider / model 失败已接入 failure ledger；由于它不是主回复链路，用户可见动作记录为继续主 stream。

仍然存在的主要结构性缺口：

1. outbound phase 的单元测试已覆盖语义边界，但还缺少真实平台日志/手动验证来证明 Record/Image/Text 投递形态与 ledger metadata 完全一致。
2. `ResultDecorateStage` 对 interaction turn 已提前退场，但普通 pipeline 的非 interaction 行为仍需在后续回归中持续覆盖。
3. 共享语音服务边界已完成第一轮接入；剩余重点是 live audio 缺 provider 的协议诊断和真实平台日志验证。

当前共同根因已经从“收消息/发消息 owner 分裂”缩小为：interaction 主链路应保持开发期 fail-fast，不再新增或保留内部体验兜底。

## 分阶段修复计划

为降低侵入性，基础收口按前三阶段推进；在前三阶段之后继续追加 outbound phase，用于让发消息语义也收口到 middleware / output controller。

## 第一阶段：引入统一 turn state，但保留旧字段兼容

目标：

- 建立 `InteractionTurnState`
- 所有关键函数都能读取同一个 turn object
- 旧的 `event.extra` 字段继续存在
- `decision_agent.py` 对齐 turn state 的缓存上下文

执行重点：

- 在 `middleware.py` 创建 state
- 在 `decision_agent.py` 改为优先读取 state 中已 materialize 的 context
- 在 `output_controller.py` 改为优先读写 state
- 定义 turn state 的基础并发模型与受控写入口
- 保留旧字段映射，避免插件和外围逻辑失效

完成标准：

- 不需要从分散的 `extra` 中反推核心 turn 状态
- 新增能力可以优先接入 state，而不是继续堆新 extra
- decision 阶段不再默认独立构建第二份 interaction context

## 第二阶段：统一 utterance 模型与消息物化流程

目标：

- 所有用户可见文本都先物化为 `InteractionUtterance`
- 统一 message id、visible output、memory relevance、发送记录
- `core_bridge.py` 与插件扩展点开始转向 state 驱动

执行重点：

- 重构 `capture_message_chain()`
- 重构流式插话发送路径
- 让 final reply 也走同一 utterance materialization 逻辑
- 让 prompt / stream / result 三类扩展点逐步切换到只读阶段视图
- 明确 `InteractionUtterance` 与 `message_chain_delivery.py` 的边界

完成标准：

- `_record_visible_output()` 不再是分散补记，而是 utterance 发送流程的自然副产物
- interaction memory 的材料来源变得稳定且统一
- `visible_message_id`、turn 内 utterance ledger、物理消息投递边界都变得可解释

## 第三阶段：收口 turn lifecycle 与 postprocess/memory 边界

目标：

- 统一 turn completion
- 统一 turn completion / postprocess handoff
- 统一 turn postprocess dispatch
- `core_bridge.py`、postprocess、memory 只消费显式 turn material

执行重点：

- 让 `_persist_turn()` 只消费 finalized turn material
- 让 `postprocessor.py` 只消费 middleware 明确产出的 turn material
- 让 `SELF_REPLY`、`HYBRID`、`DELEGATE_TO_CORE` 都通过同一套 turn completion 机制收口
- 让 `core_bridge.py` 只从 turn state 读取 `CoreTaskSpec`

完成标准：

- 不再由多个函数各自决定“什么时候这一轮算完成”
- memory 与 postprocess 不再需要从可见输出列表中自行推断完整 turn 语义
- `core_bridge.py` 不再以 `event.extra` 为主状态源

## 第四阶段：收口 streaming phase

目标：

- 将 `InteractionOutputController.capture_streaming()` / `_wrap_core_stream()` 改为明确的 stream phase。
- stream buffer、观察窗口、interjection、最终 `core_stream` materialization 全部由 turn state 受控入口管理。
- 保留旧 `event.extra` 字段作为外部兼容镜像，但内部不再把它们作为正确性来源。

执行重点：

- 在 `turn_state.py` 中引入 `InteractionStreamState`。
- 在 `output_controller.py` 中通过 `update_interaction_turn_stream_buffer(...)` 统一更新 stream text。
- 通过 `schedule_interaction_stream_observation(...)` / `_observe_interaction_stream_window(...)` 统一窗口观察。
- 在 `_finalize_interaction_stream_output(...)` 中统一记录 `core_stream` utterance，并写 finalized turn material。
- stream decider 只接收 `InteractionStreamView.copy_read_only()`。

完成标准：

- stream text、pending text、observation count、observation failures 均以 `turn_state.stream_state` 为主。
- `core_stream` utterance 与 finalized material 在流结束时显式产出。
- stream interjection 进入 utterance ledger，且默认 `memory_relevant=False`。

## 第五阶段：Outbound Phase 收口

状态：第一轮代码落地已完成。

对应提交：

- `30578c4e refactor: consolidate interaction outbound phase`

本阶段完成后，interaction 出站语义已经由 middleware / output controller 持有。旧 pipeline stage 不再作为 interaction turn 的正确性基础。

### 最终目标

interaction middleware 必须成为一轮 interaction turn 的唯一输出语义 owner：

- middleware / output controller 决定这一轮说什么、何时说、怎么记录、何时完成。
- `ResultDecorateStage` 和 `RespondStage` 只继续服务非 interaction 事件。
- platform adapter 与 `message_chain_delivery.py` 仍只负责物理投递，不理解 interaction turn 业务语义。
- TTS / t2i / reply prefix / reasoning display 等最终输出形态，由 interaction output phase 统一 materialize。
- finalized turn material 只来自 turn state / utterance ledger，不由后续 pipeline fallback 反推。
- turn postprocess 只由 middleware 统一 completion 入口触发一次；memory 写入由 postprocess / memory service 作为 consumer 执行。

从用户视角看，`HYBRID` 模式应形成同一 turn 内的完整输出序列：

1. middleware 先发 `immediate_reply`。
2. core 执行中可以产生 stream chunk 与 `stream_interjection`。
3. core 最终结果进入 output controller。
4. output controller 完成 finalizer、result contributor、TTS/t2i 等 outbound materialization。
5. output controller 通过 delivery 层投递最终消息。
6. middleware 基于 ledger 产出 finalized turn material。
7. middleware 只调度一次 turn postprocess；memory 写入由 postprocess / memory service 消费同一份 finalized material。

### Step 1：切断重复 lifecycle owner

状态：已完成。

目的：先消除重复 postprocess、自完成路径和 material fallback，避免继续把旧路径当正确性基础。

需要修改：

- `astrbot/core/pipeline/respond/stage.py`
  - 修改 `_schedule_after_message_sent_postprocess(event)`。
  - 对 `event.get_extra("_interaction_enabled")` 为真且存在 interaction turn state 的事件，不再调度 `PostProcessTrigger.AFTER_TURN_COMPLETED`。
  - `AFTER_MESSAGE_SENT` 是否保留需要明确边界：
    - 若它只表达平台物理消息已发送，可短期保留。
    - 若 downstream processor 会把它当 turn completion，必须一起跳过或加 trigger 侧过滤。

- `astrbot/core/interaction/output_controller.py`
  - 修改 `_persist_interaction_turn(event)`。
  - 当 `_persist_callback is None` 且事件属于 interaction turn 时，不再自行构造 material、调度 postprocess 或 mark completed。
  - 新增或使用现有 `record_interaction_turn_completion_failure(event, "missing_persist_callback")`。
  - 外部测试若直接实例化 `InteractionOutputController`，应显式注入 callback 或改为只验证 output capture，不把无 callback 自完成当正确行为。

- `astrbot/core/interaction/middleware.py`
  - 修改 `_schedule_turn_postprocess(event)`。
  - 删除“缺 finalized material 时调用 `_build_finalized_turn_material(...)`”的内部 fallback。
  - 缺 material 时记录 `_interaction_turn_postprocess_failed=True` 与 completion failure `missing_finalized_turn_material`，并直接返回。
  - 修改 `_finalize_turn(event)`。
  - `_finalize_turn(...)` 只消费已写入 turn state 的 finalized material；缺 material、缺 `turn_id`、缺 `assistant_text` 均记录 completion failure 并返回。
  - SELF_REPLY 成功路径通过 `_materialize_self_reply_turn(...)` 显式写入 material 后再调用 `_finalize_turn(...)`。

新增测试：

- `tests/unit/test_interaction_output_controller.py`
  - `test_output_controller_requires_persist_callback_for_interaction_completion`
- `tests/unit/test_postprocess.py`
  - `test_respond_stage_skips_turn_completed_postprocess_for_interaction_turn`

实现结果：

- `RespondStage._schedule_after_message_sent_postprocess(event)` 对 interaction turn 只保留 `AFTER_MESSAGE_SENT`，不再调度普通 `AFTER_TURN_COMPLETED`。
- `InteractionOutputController._persist_interaction_turn(...)` 无 `_persist_callback` 时记录 `missing_persist_callback` 并返回，不再自行 persist 或 mark completed。
- `InteractionOutputController._persist_interaction_turn(...)` 不再接收 `visible_reply`，persist callback 只消费 event 中显式 finalized material。
- `InteractionOutputController._materialize_finalized_turn(...)` 在 passthrough / core reply / core stream 等请求 persist 前显式写入 finalized material。
- `InteractionMiddleware._schedule_turn_postprocess(...)` 缺 finalized material 时记录 `missing_finalized_turn_material` 并返回，不再重建 material。
- `InteractionMiddleware._finalize_turn(...)` 缺 finalized material 时记录 turn finalization failure 并返回，不再从 reply 字符串或 visible outputs 构造 material。
- `InteractionMiddleware._materialize_self_reply_turn(...)` 负责 SELF_REPLY 成功路径的显式 materialization。
- core final model result 已收口到 `_deliver_core_reply(...)` 单一路径；旧的 `maybe_finalize_and_send(...)` 后续 delivery 分支已删除。

Agent 相关操作：

- `InteractionMiddleware` 仍是 turn lifecycle owner。
- `InteractionOutputController` 在 interaction 模式下只向 middleware callback 请求 completion，不再独立完成 turn。
- `InteractionOutputController` 是 outbound material producer，middleware completion 只消费其显式产物。
- `RespondStage` 对 interaction turn 不再扮演 completion owner。
- `_finalize_turn(...)` 是 completion consumer，不再兼任 material builder。

验收标准：

- `SELF_REPLY`、`HYBRID`、`DELEGATE_TO_CORE` 的 `AFTER_TURN_COMPLETED` 均只由 middleware 调度一次。
- 缺 finalized material 时不会进入 memory postprocessor。
- output controller 无 callback 时不会把 turn 标记为 completed。

### Step 2：修复插件只读视图最后缺口

状态：已完成。

目的：result contributor 不得拿到可变 decision 本体。

需要修改：

- `astrbot/core/interaction/output_controller.py`
  - 修改 `_collect_result_contributions(...)`。
  - `InteractionResultView(decision=...)` 不再传 `InteractionDecision` 对象本体。
  - 改为传 `decision.to_dict()` 的深拷贝或 frozen snapshot。

- `astrbot/core/interaction/contributors.py`
  - 修改 `InteractionResultView.copy_read_only()` 与 `as_read_only_mapping()`。
  - 对 `decision` 也调用 `freeze_interaction_snapshot(...)`。
  - 若需要类型清晰，可将字段标注从 `decision: Any` 改为 `decision: Any | None`，并在构造处保证它是 snapshot。

新增测试：

- `tests/unit/test_interaction_output_controller.py`
  - `test_result_contributor_receives_read_only_view`
  - 验证 contributor 修改 view 中 decision / metadata / visible_outputs / utterances / material snapshot 均不能污染 turn state。

实现结果：

- `_collect_result_contributions(...)` 传入 `decision.to_dict()` snapshot。
- `InteractionResultView.copy_read_only()` 与 `as_read_only_mapping()` 对 `decision` 同样执行 `freeze_interaction_snapshot(...)`。

Agent 相关操作：

- result contributor 只能影响 `InteractionResultContribution` 返回值。
- result contributor 不能修改当前 turn 的 route mode、core task spec、plugin hints 或 fallback 标记。

验收标准：

- 三类插件扩展点均只获得只读阶段事实。
- 不存在插件通过 view 污染 `InteractionTurnState` 的路径。

### Step 3：新增 outbound materialization 入口

状态：已完成。

目的：把 interaction turn 最终输出形态从 `ResultDecorateStage` 迁到 `InteractionOutputController`。

需要新增：

- `astrbot/core/interaction/output_controller.py`
  - 新增 `materialize_interaction_outbound_message(event, message, *, message_kind, result_is_model_result=False) -> tuple[MessageChain, dict[str, Any]]`。
  - 新增 `_apply_interaction_reply_prefix(event, message) -> MessageChain`。
  - 新增 `_apply_interaction_reasoning_display(event, message) -> tuple[MessageChain, dict[str, Any]]`。
  - 新增 `_apply_interaction_tts(event, message, *, result_is_model_result) -> tuple[MessageChain, dict[str, Any]]`。
  - 新增 `_apply_interaction_t2i(event, message) -> tuple[MessageChain, dict[str, Any]]`。
  - 新增 `_record_outbound_materialization_failure(event, stage, reason)`。

需要调整：

- `capture_message_chain(...)`
  - 在最终 core reply / passthrough / forced finalizer failure 发送前调用 `materialize_interaction_outbound_message(...)`。
  - `_record_visible_output(...)` 仍记录 canonical semantic text。
  - utterance metadata 记录实际投递形态，例如：
    - `delivered_as="text"`
    - `delivered_as="record"`
    - `delivered_as="image"`
    - `tts_source_text`
    - `tts_audio_path`
    - `tts_audio_url`
    - `t2i_source_text`
    - `t2i_image_url`

- `capture_streaming(...)`
  - streaming chunk 本身仍不逐个进入 ledger。
  - 流结束后的 `core_stream` materialization 记录 semantic text。
  - 是否对 stream final text 做 TTS/t2i 应保持关闭，除非后续明确设计“stream 汇总转语音”。

- `InteractionUtterance`
  - 已新增 `metadata: dict[str, Any] = field(default_factory=dict)`。
  - `materialize_utterance(...)` 增加 `metadata` 参数。
  - `append_interaction_turn_visible_output(...)` 可选择接收 `metadata`，但 memory material 仍只消费 canonical text。

需要从旧路径迁出的逻辑：

- `astrbot/core/pipeline/result_decorate/stage.py`
  - TTS 逻辑：[当前 `should_tts` 分支]
  - t2i 逻辑
  - reply prefix 逻辑
  - reasoning display 注入逻辑

Agent 相关操作：

- finalizer 继续只产出 final text。
- result contributor 继续产出 `InteractionResultContribution`。
- output controller 在 final text 已确定后执行 outbound materialization。
- Agent / core 不需要知道最终输出是 text、record 还是 image。

实现结果：

- `capture_message_chain(...)` 在 passthrough / core reply / forced finalizer failure 发送前调用 `materialize_interaction_outbound_message(...)`。
- `_record_visible_output(...)` 继续记录 semantic text，同时把 delivered shape 写入 utterance metadata。
- TTS / t2i 启用后失败不降级为文本发送；会记录 `_interaction_outbound_materialization_failed`、stage、failure reason，并抛出异常。
- streaming chunk 仍不逐个进入 utterance ledger；streaming final `core_stream` 仍记录 semantic text，未对 stream final text 执行 TTS/t2i。

验收标准：

- interaction turn 的最终可见输出不再依赖 `ResultDecorateStage` 改写。
- TTS/t2i 后的实际投递形态能在 utterance metadata 中解释。
- interaction memory 仍只使用 semantic assistant text，不被音频路径或图片路径污染。

### Step 4：让 ResultDecorateStage 对 interaction turn 退场

状态：已完成。

目的：避免旧 pipeline 装饰层继续改写 interaction 输出。

需要修改：

- `astrbot/core/pipeline/result_decorate/stage.py`
  - 在 `process(event)` 中识别 interaction turn：
    - `event.get_extra("_interaction_enabled")`
    - 或 `get_interaction_turn_state(event) is not None`
  - 对 interaction turn 跳过：
    - reply prefix
    - segmented reply
    - TTS
    - t2i
    - reasoning display
    - forward message transformation
  - 若仍需要 content safety check，应明确它是“core result safety check”还是“final outbound safety check”。
    - 建议短期保留现有非 stream content safety。
    - 长期应迁到 output controller 的 final text safety hook。

新增测试：

- `tests/unit/test_postprocess.py` 或新增 result decorate 测试：
  - `test_result_decorate_stage_skips_interaction_turn_reply_prefix`

实现结果：

- `ResultDecorateStage.process(event)` 在 content safety / decorating hook / reply prefix / segmented reply / TTS / t2i / reasoning display / forward transform 之前识别 interaction turn 并直接返回。
- 非 interaction 事件仍走原普通 pipeline 装饰逻辑。

Agent 相关操作：

- interaction Agent 的输出表达不再由普通 pipeline 装饰层二次改写。
- 非 interaction Agent / 普通 pipeline 行为保持原状。

验收标准：

- interaction turn 的 output controller 是唯一 outbound materialization owner。
- 非 interaction 事件的 TTS/t2i/reply prefix 不回退。

### Step 5：统一 final material 与 delivered shape

状态：已完成第一轮。

目的：让 finalized material、memory、postprocess、实际投递形态之间边界清楚。

需要修改：

- `astrbot/core/interaction/memory_store.py`
  - 检查 `build_interaction_memory_reply_from_visible_outputs(...)` 是否只依赖 semantic utterance text。
  - 确认 `memory_relevant=False` 的 utterance 不进入 canonical assistant reply。

- `astrbot/core/interaction/middleware.py`
  - `_build_finalized_turn_material(...)` 只作为显式 materializer 使用。
  - `_finalize_turn(...)` 只消费已经 materialized 的 turn material。
  - 不再从旧 extra 或 downstream pipeline 输出反推。

- `astrbot/core/memory/postprocessor.py`
  - 保持 interaction turn 只消费 explicit `ctx.turn_material`。
  - 不增加新的推断路径。

新增测试：

- `tests/unit/test_interaction_output_controller.py`
  - `test_tts_materialization_records_record_delivery_but_memory_uses_text`
  - `test_t2i_materialization_records_image_delivery_but_memory_uses_text`
  - `test_tts_materialization_failure_is_not_downgraded_to_text`

实现结果：

- utterance metadata 记录 `delivered_as="text" | "record" | "image"` 以及 TTS/t2i source 和输出地址。
- finalized material / memory 使用 canonical semantic text，不使用 Record/Image 路径。
- `memory_relevant=False` 的 stream interjection 不进入 canonical assistant reply。

Agent 相关操作：

- `HYBRID` 中 immediate reply、stream interjection、core final reply 都归同一个 turn ledger。
- final material 的 `assistant_text` 来自 canonical semantic utterance，而不是 platform payload。

验收标准：

- 用户实际收到的 Record/Image/Text 与 ledger metadata 对得上。
- memory/postprocess 看到的是同一份 finalized material。
- 没有重复 postprocess，memory 写入只由 postprocess / memory service 消费 finalized material 后发生。

### Step 6：回归与手动验证

状态：自动化回归已完成；手动/日志验证待补。

必须运行：

```bash
uv run pytest tests/unit/test_interaction_middleware.py tests/unit/test_interaction_output_controller.py -q
uv run pytest tests/unit/test_postprocess.py tests/unit/test_memory_runtime.py -q
uv run pytest tests/unit/test_interaction_context_builder.py tests/unit/test_interaction_decision_agent.py -q
uv run ruff format .
uv run ruff check .
```

建议补充手动或日志验证：

1. `SELF_REPLY`
   - 只发送 immediate reply。
   - turn material 只生成一次。
   - postprocess 只调度一次。

2. `HYBRID`
   - immediate reply 先发。
   - core final reply 后发。
   - 若启用 TTS，最终投递为 Record，但 memory 中仍是文本。
   - postprocess 只调度一次。

3. `DELEGATE_TO_CORE`
   - core reply / stream reply 进入 output controller。
   - finalized material 明确产出。
   - postprocess handoff 由 middleware 收口，memory 写入由 postprocess / memory service 负责。

4. streaming
   - stream chunk 正常发出。
   - stream interjection 独立记录且 `memory_relevant=False`。
   - final `core_stream` utterance 与 material 一致。
   - 通用平台 live audio 必须通过 `audio_chunk` 流式协议播放语音。
   - `audio_chunk` 的音频 base64 进入 WebChat back queue 并由 websocket `t=response` 推给前端。
   - `audio_chunk` 附带的文本进入 interaction stream material；base64 音频数据不进入 memory。

5. 非 interaction 普通事件
   - `ResultDecorateStage` 的 TTS/t2i/reply prefix 仍然工作。
   - `RespondStage` 的普通 postprocess 仍然工作。

## 第七阶段：共享语音服务边界与通用平台音频收口

状态：共享语音服务边界已完成第一轮接入；live audio 协议诊断待补。

### 总目标

语音能力需要同时支持两条流程：

1. core 旧流程：
   - `PreProcessStage` 继续支持普通事件 STT。
   - `ResultDecorateStage` 继续支持非 interaction 事件 TTS。
   - core live stage 继续支持 live audio streaming。
   - 这些路径属于对既有生态、平台行为和插件配置的兼容边界，不能直接删除。
2. interaction middleware 新流程：
   - inbound voice 在 decision 前完成 STT materialization。
   - outbound reply 在 output controller 中完成 TTS materialization。
   - 通用平台 live audio 走 `audio_chunk` streaming 协议，并纳入 interaction turn state / stream material。
   - middleware 内部不能依赖 core 旧路径作为失败兜底；缺 provider、provider error、空结果必须进入可观测 failure。

最终形态不是“core 或 middleware 二选一”，而是建立共享 voice service port：

- provider 解析、输入校验、失败原因、diagnostics 统一。
- core 与 middleware 都调用同一套服务接口。
- core 旧阶段保留为兼容调用方。
- middleware 成为 interaction turn 的语义 owner，但不垄断所有非 interaction 事件。

### 当前修复

- `action_type=live` 是通用平台音频流协议入口，不走普通 interaction decision。
- middleware 对 live event 生成显式 `DELEGATE_TO_CORE` protocol decision，并保持 turn state / output interceptor。
- core pipeline 继续通过 `run_live_agent(...)` 产生 `MessageChain(type="audio_chunk")`。
- WebChat back queue 继续把 `audio_chunk` 转为 live websocket `t=response`，前端 live audio 视图继续播放该音频帧。
- `InteractionOutputController._extract_observable_stream_text(...)` 从 `audio_chunk` 的 `Json({"text": ...})` 提取 spoken text，用于 stream buffer、`core_stream` utterance、finalized material 与 memory。

### 仍未完成

- TTS / STT provider 解析已集中到 `astrbot/core/voice/service.py`。
- `PreProcessStage`、`ResultDecorateStage`、`InteractionMiddleware`、`InteractionOutputController`、core live stage 已改为调用共享 voice service。
- `run_live_agent(...)` 仍负责使用已解析的 TTS provider 生成音频 chunk；这是底层 runner 执行职责，不再负责 provider 解析。
- `run_live_agent(...)` 在缺 TTS provider 时仍可能发送普通文本流；这对 live audio 语音协议来说不是正确完成，但普通非 live core 流程仍需要保留兼容文本输出。
- live audio 缺 provider fail-fast、音频 chunk materialization、音频统计与 completion failure 还没有完全统一接入 interaction turn diagnostics。

### 下一步建议

1. 共享 voice service port 已新增：
   - `astrbot/core/voice/service.py`
   - `resolve_stt_provider(plugin_context, event)`
   - `resolve_tts_provider(plugin_context, event)`
   - `transcribe_record(plugin_context, event, record_component, *, stage)`
   - `synthesize_text(plugin_context, event, text, *, stage)`
   - 返回值带 provider id、source text、输出路径/URL、诊断 metadata。
2. core 兼容接入已完成：
   - `PreProcessStage` 调用共享 STT service，保留原有启用开关和普通事件行为。
   - `ResultDecorateStage` 调用共享 TTS service，继续只处理非 interaction 事件。
   - core live stage 通过共享 TTS service resolve provider，再调用 live audio runner。
3. middleware 接入已完成：
   - `_transcribe_inbound_records(...)` 调用共享 STT service。
   - `_apply_interaction_tts(...)` 调用共享 TTS service。
   - live audio protocol route 使用同一套 TTS provider 解析与 diagnostics。
4. 下一步 live audio fail-fast 规则：
   - live event 缺 TTS provider 时记录 `live_tts_provider_unavailable`，不标记成功语音 turn。
   - 不能把普通文本流当作 live audio 语音协议的成功完成。
   - 若未来要允许“无语音文本模式”，必须是显式用户配置的外部兼容模式，并写入 failure/diagnostics，不能污染成功状态。
5. 下一步将 LiveMode completion material 纳入统一 stream phase：
   - `audio_chunk` 文本作为 canonical spoken text。
   - 音频 chunk metadata 作为 delivered shape / diagnostics，不进入 memory。
6. 下一步增加端到端日志断点：
   - middleware live protocol route。
   - shared voice service provider id。
   - core `run_live_agent()` 首个 `audio_chunk`。
   - webchat back queue `type=audio_chunk`。
   - websocket `t=response`。
   - frontend `playAudioChunk(...)` 调用。

### 兼容性原则

- core 旧流程继续支持 STT / TTS，不能因 interaction middleware 重构被删除。
- middleware 新流程也必须支持 STT / TTS，且必须通过 turn state / utterance ledger / finalized material 记录语义。
- 共享 voice service 是能力抽象，不是 fallback。
- 非 interaction 事件继续走 core pipeline；interaction 事件走 middleware owner。
- 外部平台兼容可以保留保护模式，但必须可观测，不能写成成功状态。

## 第六阶段：开发期 fail-fast 与 fallback 去正确性化

状态：第一轮代码已落地，剩余为边界审查和补充验证。

### 最终目标

interaction middleware 的内部主链路必须直接暴露真实错误：

- 内部缺 provider、缺 context material、缺 finalized material、缺 callback、LLM 返回非 JSON、schema invalid、TTS/t2i 失败等，都不能靠 fallback 被解释成“正常完成”。
- 开发期不保留内部 fallback；外部边界若将来需要保护，必须单独设计并经确认。
- 开发期默认 fail-fast：主链路失败应抛错或终止当前 interaction turn，方便直接定位根因。
- 不把生产体验保护作为当前开发目标。

### Step 1：明确 fail-fast 配置与边界

状态：已完成第一轮。

需要修改：

- `astrbot/core/interaction/middleware.py`
  - 在 middleware 边界拒绝 `interaction_middleware.fallback_policy`。
  - 默认行为就是开发期 fail-fast。

Agent 相关操作：

- interaction Agent 的决策、表达、输出 materialization 不应依赖 fallback policy。
- 不允许阶段自行决定降级继续运行。

验收标准：

- middleware 范围内配置 fallback policy 会直接报错。
- 主链路错误不会被静默转为 delegate/core/text 输出。

实现结果：

- `InteractionMiddleware` 初始化和刷新配置时拒绝 `interaction_middleware.fallback_policy`。
- 旧 fallback decision 若到达 middleware，会记录 failure 并终止该 turn。

### Step 2：收口 decision fallback

状态：已完成第一轮。

需要修改：

- `astrbot/core/interaction/decision_agent.py`
  - `build_fallback_decision(...)` 不再作为内部正确性兜底。
  - provider unavailable、timeout、model error、non-json、invalid payload、low confidence 等场景在 fail-fast 模式下抛出明确异常。

- `astrbot/core/interaction/middleware.py`
  - `_decide_or_fallback(...)` 改名或拆分为 `_decide_interaction_route(...)`。
  - fail-fast 下不捕获并转换 decision pipeline error。
  - fail-fast 下记录 `_interaction_decision_failed=True`、reason、原始错误类型，然后抛错。

Agent 相关操作：

- Agent 决策失败不能被视作“自然 delegate_to_core”。
- fallback decision 不能进入成功样本或作为路由正确性证明。

验收标准：

- provider missing / invalid JSON 的测试必须看到异常或明确失败字段。
- 没有测试再以 fallback decision 作为主链路成功依据。

实现结果：

- `InteractionDecisionError` 已加入 `decision_agent.py`。
- provider unavailable、timeout、model error、non-json、invalid payload、low confidence 在 `fail_fast` 下抛错。
- SELF_REPLY 缺少 `immediate_spoken_reply` 在 decision validation 阶段抛错。
- `_decide_or_fallback(...)` 已改为 `_decide_interaction_route(...)`。
- middleware 捕获 decision pipeline error 后记录 `_interaction_decision_failed` 与 failure ledger，然后抛错。
- 已覆盖 missing plugin context / decision pipeline error / low confidence 的 fail-fast 测试。
- 已覆盖 `fallback_policy` 配置被 middleware 拒绝、旧 fallback decision 被 middleware 拒绝的测试。

### Step 3：收口入站 STT / media materialization 失败语义

状态：已完成第一轮。

需要修改：

- `astrbot/core/interaction/middleware.py`
  - `_materialize_inbound_media(...)`
  - `_transcribe_inbound_records(...)`
  - provider unavailable、audio path resolution failed、STT failed 在 fail-fast 模式下抛错。

Agent 相关操作：

- decision agent 只能消费 materialized input。
- STT 未完成时不能让 decision 误以为空文本输入是用户真实意图。

验收标准：

- 启用 STT 且 provider 缺失时，interaction turn 不进入正常 decision 成功路径。
- STT 失败不会污染 interaction memory 或 recent messages。

实现结果：

- `_materialize_inbound_media(...)` 的 record normalize 失败在 `fail_fast` 下抛错。
- `_transcribe_inbound_records(...)` 对 plugin context missing、provider unavailable、audio path resolution failed、source unavailable、provider error、empty transcription 均记录失败。
- STT 失败不进入正常 decision。
- 已覆盖 STT provider missing fail-fast 测试。

### Step 4：收口 finalizer fallback 与 forced failure 输出

状态：已完成第一轮。

需要修改：

- `astrbot/core/interaction/finalizer.py`
  - provider unavailable / model error / invalid finalizer output 在 fail-fast 模式下抛错。

- `astrbot/core/interaction/output_controller.py`
  - `FinalizerMode.FORCE` 失败时不发送“最终回复整理失败，请查看日志。”之类的替代文本。
  - 记录失败并抛错。

Agent 相关操作：

- finalizer 是表达层主链路，不应把失败消息当作正常 assistant answer。

验收标准：

- forced finalizer failure 不再污染 finalized turn material 的 canonical assistant text。
- 不产生 failure notice。

实现结果：

- `InteractionFinalizerError` 已加入 `finalizer.py`。
- finalizer plugin context missing、provider unavailable、timeout、model error、empty output 在 `fail_fast` 下抛错。
- `FinalizerMode.FORCE` 失败时默认 fail-fast，不发送替代文本。
- 已覆盖 forced finalizer failure fail-fast 测试。

### Step 5：统一 failure diagnostics

状态：已完成第一轮。

需要新增或调整：

- `astrbot/core/interaction/turn_state.py`
  - 增加统一 failure ledger，例如 `InteractionTurnFailure` 或 completion failure list。
  - 保留旧 `_interaction_*_failed` extra 镜像，但内部以 failure ledger 为主。

- 所有关键失败入口统一记录：
  - stage
  - reason
  - exception type
  - user visible action taken
  - whether turn material was finalized
  - whether postprocess handoff or memory consumer was skipped

Agent 相关操作：

- Agent/subagent 相关失败不能只写 warning。
- failure ledger 可作为调试、前端显示和后续审计来源。

验收标准：

- 任一失败场景都能从 turn state 解释“哪里失败、是否发过消息、是否调度 postprocess、memory consumer 是否写入、是否完成 turn”。

实现结果：

- `InteractionTurnFailure` 已加入 `turn_state.py`。
- `InteractionTurnState.failures` 成为 failure ledger。
- `record_interaction_turn_failure(...)` 双写 turn state 与 `_interaction_turn_failures` extra，并同步 completion failure reason。
- decision、STT、finalizer、SELF_REPLY 发送/完成失败、stream interjection skip/failure 的关键入口已接入 ledger。

### Step 6：回归与手动验证

状态：单元回归已完成；真实平台手动验证仍待执行。

必须新增测试：

- decision provider missing fail-fast。
- decision invalid JSON fail-fast。
- STT provider missing fail-fast。
- finalizer provider missing fail-fast。
- forced finalizer failure 不污染 memory。
- fallback policy 配置被 middleware 拒绝。

必须运行：

```bash
uv run pytest tests/unit/test_interaction_middleware.py tests/unit/test_interaction_decision_agent.py tests/unit/test_interaction_output_controller.py -q
uv run pytest tests/unit/test_interaction_context_builder.py tests/unit/test_memory_runtime.py -q
uv run ruff format .
uv run ruff check .
```

已运行：

```bash
uv run pytest tests/unit/test_interaction_middleware.py tests/unit/test_interaction_output_controller.py tests/unit/test_interaction_context_builder.py tests/unit/test_interaction_decision_agent.py tests/unit/test_memory_runtime.py tests/unit/test_postprocess.py -q
uv run ruff format .
uv run ruff check .
```

结果：

- `168 passed`
- `ruff check` 通过
- 剩余 warnings 为既有 SwigPy deprecation 与 aiosqlite event-loop-close 测试环境 warning。

### 第六阶段剩余审查点

1. 真实平台链路还需验证：文本、TTS Record、t2i Image 的 delivered payload、message id、utterance metadata 与 finalized material 是否一致。

## 兼容性策略

为了兼容现有生态，必须坚持以下策略：

1. 旧的 `event.extra` 字段短期内全部保留
2. 新增 `InteractionTurnState` 后，先做双写，不立即删旧字段
3. `visible_message_id` 继续保持字符串语义稳定
4. `turn_id` 继续作为一轮多消息的公共标识
5. `message_id` 的唯一性继续由中间件内部保证，不要求 adapter 变更
6. `event.extra["_interaction_turn_state"]` 作为兼容承载保留，但不定义为长期目标

## 测试迁移策略

本次重构必须采用“状态迁移与测试迁移同步推进”的方式。

建议按 phase 对齐：

### Phase 1

- 保留现有 `event.extra` 语义测试
- 新增 turn state 一致性测试
- 验证 state 与旧 extra 双写结果一致
- 验证 `decision_agent.py` 优先使用 state cache，而不是重复构建 context

### Phase 2

- 新增 utterance 级测试
- 验证：
  - `message_id` 生成
  - `turn_id` 归属
  - `memory_relevant` 过滤
  - visible output ledger 追加顺序
- 验证 `message_chain_delivery.py` 只负责物理投递，不篡改 utterance 语义
- 验证三类扩展点看到的是只读阶段视图

### Phase 3

- 新增 turn completion 测试
- 验证 memory / postprocess / core bridge 只消费 finalized turn material
- 验证 `SELF_REPLY`、`HYBRID`、`DELEGATE_TO_CORE` 三种模式最终都能统一收口
- 验证删除或弱化旧 extra 主读取路径后，行为不回退

## 不建议采用的修复方式

以下方式虽然可能暂时缓解表面问题，但不应视为根因修复：

- 继续新增 `_interaction_*` extra 字段来协调更多分支
- 在 output controller 下游再补一层历史修正
- 在 memory postprocess 里加入更多推断逻辑
- 依赖 adapter 或前端配合来定义 turn 语义
- 在 finalizer 或 stream interjection 上堆更多 prompt 规则来掩盖状态不统一

这些方式只会让系统更像拼装层，而不是让它成为整体。

## 验证要求

每个阶段完成后，至少应验证以下链路：

1. `SELF_REPLY` 单轮闭环是否稳定
2. `HYBRID` 是否保持“一轮内多消息”的统一 turn 语义
3. `DELEGATE_TO_CORE` 是否仍由 middleware 持有 turn owner 语义
4. 流式输出场景下是否能：
   - 正确累计 stream text
   - 正确按窗口观察
   - 正确发出 interjection
   - 正确落 interaction memory
5. interaction memory 是否只基于 middleware 自己真实发出的 canonical utterance
6. postprocess 是否只消费 middleware 最终确认的 turn material

## 最终结论

当前 interaction middleware 的主要问题，不是代码局部报错，而是：

> 它已经承担了交互编排职责，却还没有一个与之相称的统一回合模型。

因此后续修复必须围绕以下根因展开：

- 建立统一 `turn state`
- 建立统一 `utterance` 模型
- 建立统一 `turn lifecycle`
- 让 memory 和 postprocess 只消费中间件显式产出的 turn material

只有这样，interaction middleware 才会从“沿链路附着的一组能力”真正收口为“一个完整的交互编排层”。
