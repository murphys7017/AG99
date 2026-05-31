# Input / Core / Output Target State

这份文档描述 Yakumo 当前这条运行时重构线的最终目标，不讨论阶段性过渡实现，只定义目标结构、职责边界和目标运行效果。

核心原则只有一句话：

> 核心是核心，输入系统是输入系统，输出系统是输出系统。

目标不是把当前 middleware 继续做大，而是把“接收世界”“内部决策”“对外表达”三件事彻底拆开，让它们通过明确 contract 协作。

---

## 目标结论

最终运行时应拆成三个正交系统：

1. `Input Runtime`
2. `Core Runtime`
3. `Output Runtime`

它们的关系不是“谁附属于谁”，而是：

- Input Runtime 决定什么进入系统
- Core Runtime 决定系统内部产出什么
- Output Runtime 决定内部产出如何逐步变成用户可见对话

这三个系统之间只通过结构化状态和结构化事件协作，不再互相侵入实现细节。

---

## 一、Input Runtime

Input Runtime 是系统面对外界输入的唯一正式入口。

它负责：

- 接收来自平台、WebUI、主动触发器、系统信号的入站事件
- 标准化文本、媒体、命令、元信息、上下文引用
- 生成统一 observation
- 维护输入侧 turn/session 事实
- 执行前置 gate / route decision
- 决定事件应该被丢弃、吸收、直接回复、交给核心、还是进入 reflex

它不负责：

- 组织最终 prompt
- 执行工具
- 直接拼最终回复
- 直接决定最终发送形式

Input Runtime 的目标不是“思考很多”，而是“先把世界接稳，再把事件分类干净”。

### Input Runtime 的目标特征

- 同一类输入进入系统后有统一表达
- 前置判断优先于大模型思考
- observation 是正式结构，不是临时 extra
- reflex / gate / route 都建立在 observation 之上
- 输入系统可以独立演进，而不拖着核心和输出一起变

---

## 二、Core Runtime

Core Runtime 是系统内部的决策与执行核心。

它负责：

- 基于当前 turn/session 状态做决策
- 使用 prompt / memory / tools / subagent / codex / knowledge 等能力
- 决定本轮内部结果
- 维护任务执行过程中的内部状态
- 产出结构化的 result、stream event、action event、completion signal

它不负责：

- 接入原始平台输入
- 承担平台 send 语义
- 直接 materialize 用户可见输出
- 决定外发 payload 的最终形态

Core Runtime 的目标不是“替系统做所有事”，而是“在明确输入和明确状态之上产出可消费的内部结果”。

### Core Runtime 的目标特征

- 核心能力和平台 IO 解耦
- prompt / memory / tools / subagent / codex 都是 core 内部能力，不直接拥有输入输出通道
- core 输出的是结构化结果，不是隐式副作用
- 可以有不同执行形态，但共享同一 runtime contract

---

## 三、Output Runtime

Output Runtime 是系统把内部结果转成用户可见对话的唯一正式出口。

它负责：

- 接收 core/internal result
- 将内部结果 materialize 成可见输出
- 支持 immediate / progressive / final 三种阶段性表达
- 负责 streaming append、temporary reply、final reply、client objects、TTS、t2i、平台 extras
- 维护输出侧 turn state、visible output ledger、completion state
- 在输出完成后向 postprocess / memory / analytics 交付 finalized material

它不负责：

- 决定某个输入应不应该进入核心
- 决定主任务逻辑
- 修改输入 observation
- 反向侵入 core 的执行细节

Output Runtime 的目标不是“把 send 拦下来”，而是“成为正式的对外表达系统”。

### Output Runtime 的目标特征

- 用户可见对话是逐步追加的正式能力，不是偶发技巧
- 输出系统拥有自己的状态和完成语义
- finalized material 是正式产物，不再依赖后处理临时猜测
- 外部平台差异在输出系统消化，不侵入 core 决策

---

## 四、三者之间的标准关系

最终目标不是三个模块彼此直接调用内部实现，而是遵守以下关系：

### 1. Input -> Core

Input Runtime 向 Core Runtime 提供：

- 标准化输入
- observation
- route decision
- turn/session 视图
- 输入侧约束与前置结论

Core Runtime 不反向控制 Input Runtime 的采集与分类逻辑。

### 2. Core -> Output

Core Runtime 向 Output Runtime 提供：

- 内部结果
- 过程事件
- streaming 片段
- completion signal
- 可选的结构化表达候选

Output Runtime 不反向决定 core 要不要执行某项能力。

### 3. Output -> Postprocess / Memory

Output Runtime 结束后交付：

- finalized turn material
- visible output ledger
- completion diagnostics

postprocess / memory / analytics 消费这份正式产物，而不是重新从零推断“这一轮到底发生了什么”。

---

## 五、Session Owner 与 Turn Owner

最终目标中，系统不能只靠临时 event 对象承载运行时状态。

必须有明确的：

- `Session Owner`
- `Turn Owner`

### Session Owner

负责持有某个 session 的持续状态，例如：

- 最近 observation
- 当前 interaction state
- suppression / cooldown / reflex transient state
- active output state
- pending task / background activity state

目标语义：

- 同一 session 内部状态有唯一 owner
- 同一 session 内事件按明确顺序处理
- 不同 session 可以并发

### Turn Owner

负责持有某一轮交互的局部生命周期，例如：

- 这轮输入的标准化结果
- 这轮 route decision
- 这轮 core 执行状态
- 这轮输出进度
- 这轮 finalized material

目标语义：

- 一轮交互从输入到完成有明确边界
- progressive append 知道自己正在追加到哪个 turn
- completion 后状态收口一致

---

## 六、输入与输出的彻底解耦

这条重构线的核心目标之一，是把输入和输出从“同一条消息处理流程里的两个阶段”提升为“两个独立系统”。

最终应满足：

- 输入系统可以只做 observation / gate / route，而完全不关心最终文案
- 输出系统可以只关心如何逐步表达，而不关心原始平台事件细节
- core 可以专注于“做什么”，而不需要承担“怎么进来、怎么发出去”

也就是说，系统不再默认“一次输入对应一次立即完成的回复”。

系统应该允许：

- 输入先被接住
- 内部过程持续进行
- 输出逐步追加
- 最终表达在完成时再收口

这正是后续要支持的“像 Codex 一样逐渐追加对话”的前提。

---

## 七、目标交互形态

最终用户可见交互不应只是一条最终消息，而应支持：

### 1. Immediate

输入刚进入系统时的快速反馈。

例如：

- 已收到
- 正在检查仓库
- 正在分析配置

### 2. Progressive

执行过程中的逐步追加输出。

例如：

- 已找到关键模块
- 已完成第一轮检查
- 正在验证假设
- 已拿到部分结果

### 3. Final

最终整理后的正式表达。

例如：

- 最终结论
- 修改结果
- 风险说明
- 下一步建议

这里的关键不是“能不能流式输出”，而是：

> Progressive output 是正式的运行时能力，而不是对最终回复的临时补丁。

---

## 八、Observation / Reflex 在目标结构中的位置

在最终目标中：

- observation 属于 Input Runtime
- reflex 属于系统级调节层
- core 不是 observation 的 owner
- output 不是 reflex 的 owner

### Observation

Observation 是系统对输入、运行态、环境态、对话态的统一事实表达。

它的作用是：

- 为 gate 提供依据
- 为 reflex 提供依据
- 为 route decision 提供依据
- 为后续 memory / analytics 提供统一事实源

### Reflex

Reflex 不是普通聊天逻辑，也不是简单通知插件。

它的目标是：

- 基于 observation 和 session/runtime state 做系统级快速调节
- 决定 absorb / suppress / defer / notify / reroute / degrade / escalate
- 必要时产生输入侧决策或输出侧意图

Reflex 默认不直接替代 core，但可以在 core 之前或之外决定某些系统行为。

---

## 九、Codex / 执行能力在目标结构中的位置

在最终结构中，Codex CLI 这类能力属于 Core Runtime 的执行能力，而不是输入系统或输出系统的一部分。

这意味着：

- 是否需要进入 workspace、是否需要调查仓库、是否需要改代码，是 core 的决策问题
- 如何把 Codex 的过程反馈逐步显示给用户，是 output 的职责
- 哪些输入值得触发这类执行能力，是 input + gate + route 的职责

这样可以避免：

- 输入系统直接绑死某种执行器
- 输出系统知道太多执行内部细节
- Codex 能力反向侵入整条运行时链路

---

## 十、和当前系统相比，最终目标最重要的变化

相对当前实现，最终目标最重要的不是“功能更多”，而是边界改变：

### 当前倾向

- middleware 同时承担输入、部分核心路由、部分输出收口
- event/send interception 承载过多语义
- runtime state 仍有较多兼容性承载
- 输入、核心、输出尚未形成正式独立系统

### 目标态

- Input Runtime 是正式输入系统
- Core Runtime 是正式内部决策系统
- Output Runtime 是正式对外表达系统
- Session/Turn owner 明确
- observation/finalized material 成为正式产物
- progressive append 成为一等能力

---

## 十一、最终收益

如果达到这个目标，系统将获得以下能力：

### 1. 架构收益

- 输入、核心、输出职责清晰
- 系统级 observation / reflex 有正式挂点
- core 不再被平台 IO 和输出细节拖住

### 2. 交互收益

- 能稳定支持逐步追加对话
- 能支持临时回复、过程反馈、最终收口并存
- 用户可见交互更接近真实持续运行系统，而不是一次性问答机

### 3. 工程收益

- 更容易测试输入策略、核心决策、输出表达
- 更容易替换输出形态或接入新执行能力
- 更容易给 memory / analytics / reflex 提供统一事实源

### 4. 演进收益

- 后续接入 self_reflex、system reflex、主动通知、Codex 执行、自修复闭环时，不需要重新打碎主链路

---

## 十二、最终判断

这条重构线的真正目标，不是把当前 AstrBot 改成“更复杂的聊天机器人”，而是把它推进成一个：

- 持续运行
- 有输入系统
- 有内部核心
- 有输出系统
- 支持 observation / reflex / progressive dialogue

的运行时。

如果未来要继续朝 AG99 那类结构靠近，那么这份目标态就是必要前提，而不是额外装饰。
