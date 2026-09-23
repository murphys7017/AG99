# Core 内部执行器替换实施方案

> 5.6 的逐批编码指令见 [`internal-executor-replacement-implementation-guide.md`](internal-executor-replacement-implementation-guide.md)。本文件保留架构目标、边界和批次定义；实施时以操作级手册中的文件、接口和退出条件为准。

日期：2026-09-23。状态：R1-R7 的最小替换基础已审阅并提交；Codex app-server 已进入普通交互与主动任务的外部执行路径，R8 真实平台验收仍待完成。Native 普通交互仍保留专用生产链路，因此整体执行器生命周期尚未收敛。

逐批编码步骤、修改范围和验证命令见
[内部可替换执行器：5.6 操作级实施手册](internal-executor-replacement-implementation-guide.md)。
本文件定义架构和完成口径；操作手册不得改变这里的 owner、非目标和验收结论。

## 1. 目标与完成口径

Personal 负责统一对外交流和初期响应；Core Head 负责复杂工作的控制、执行身份和终态；Executor Body 负责具体执行。更换 Body 不应要求修改 Personal 的决策 Prompt、OLV adapter、TTS 或插件准入规则。

本方案补齐“内部可替换”的实际运行入口。D5-A/D5-B 已完成显式端口注入，但不能据此宣布生产执行链已经可替换。上轮“可以开始引入执行器”应理解为可以开始接入开发，不是已经存在配置入口。

用户暂时接受当前实测结果作为继续推进依据，已有打断问题仍有不确定性；这不是完整 D6 验收通过。新 Body 首次启用前仍须做最小真实验收。

第一版默认 Native，每个 CoreExecution 只使用一个 Body。先用可运行的测试 Body 走通同一生产装配入口，再接一个指定的真实执行器。没有选定产品前不编写厂商适配。

## 2. 源码事实与缺口

| 位置 | 已有能力 | 尚缺能力 |
| --- | --- | --- |
| `astrbot/core/execution.py` | Head、Session、生命周期、显式 CoreExecutionPort | CoreExecutorBody 只有身份、停止、补充输入，没有完整运行和清理契约 |
| `astrbot/core/astr_agent_run_util.py` | NativeExecutionRun、Adapter、Loop、OutputBridge | Run 仍封装 Native 证据，不能作为通用运行结果 |
| `astrbot/core/astr_main_agent.py:build_main_agent` | 构建上下文、能力、执行规格和 Native Runner | 通用准备与 Native Provider/render/reset 装配仍连在一起 |
| `internal.py:process` | 普通交互的 Native 执行 | 硬编码 Native，直接解释响应、历史与统计 |
| `proactive_agent_turn.py:run_proactive_agent_turn` | Cron/后台任务的 Native 执行 | 另一处硬编码 Native，并有发送工具/投递确认要求 |
| `personal_runtime.py:_FollowUpCoordinator` | 追问 ticket 的接收、消费、撤回 | 仍接触 executor 和 ticket 的具体形状 |
| `CoreExecutionArtifact` | 产物身份和有界诊断 | 明确不传正文，不能单靠 artifact_ready 产生最终回复 |
| `tests/unit/test_core_execution_events.py` | Scripted Body 控制与事件验证 | 没有证明同一生产入口的准备、实际运行、结果表达和结算可替换 |

当前事件枚举也没有 `input_required`。第一版不宣称支持暂停等待用户，也不把未来状态写成当前接口。

## 3. 目标调用链与职责

```text
Personal 委派
  -> Core 请求准入与上下文准备
  -> 按当前配置快照解析 executor_id
  -> Core Head + 对应 Body factory
  -> Core 运行协调器驱动 Body
  -> 执行事实归 Head；结果材料经输出桥进入 Personal/Output
  -> Core 终态结算；资源关闭；释放执行句柄
```

采用普通进程内调用和异步任务。外部执行器可以在自己的 Adapter 内使用子进程或 SDK，但 Personal/Core 通信不因此变成分布式协议。

| Owner | 长期职责 |
| --- | --- |
| Personal Runtime | 对话准入、用户追问顺序、统一表达、原有 turn 总预算 |
| Core Head/Lifecycle | 执行身份、命令接受、事件排序、终态、结算幂等 |
| Core 运行协调器 | 创建/驱动/关闭 Body，把异常和取消交给 Head |
| Body Adapter | 引擎参数转换、执行循环、引擎资源回收、执行材料归一化 |
| Output Controller | Personal 表达、TTS、effect、平台投递、逻辑完成和可见历史 |
| Core Ledger | 执行证据持久化，不作为可见对话历史 |

运行协调器是 Head 所属运行层的实现，不新增 Session 状态机或第二个 deadline owner。

## 4. 准备与选择入口

建议在 `astrbot/core/executors/` 放置有生产调用者的契约、factory 和具体实现。通用生命周期类型继续留在 `execution.py`，不批量搬家。

建议函数形状如下，均为待实施名称：

```python
async def prepare_core_execution(event, context, config) -> PreparedCoreExecution: ...
def resolve_executor_id(config_snapshot, *, execution_source) -> str: ...
async def build_executor_run(prepared, *, executor_id, services) -> ExecutorRun: ...
async def drive_core_execution(head, run, *, deadline, output_bridge) -> ExecutionResult: ...
```

`PreparedCoreExecution` 携带同一份 CoreExecutionSpec、已准入能力与上下文事实、只读 deadline 视图、执行来源和工作目录。Spec 已有的身份、上下文和能力不能再复制成可以独立变化的第二份字段。`ProviderRequest` 是 Native Provider 的目标请求，不属于通用准备对象；Native factory 在消费通用准备后自行渲染并构造它。宿主服务由 factory 注入，不把整个 Context/Event 交给第三方 Body。

从 `build_main_agent()` 抽出通用准备，再保留 Native 专属的 ProviderRequest 渲染、Runner 构造与 reset。选择外部执行器后不得先创建 Native Runner，亦不得强制配置一个闲置 Native Provider。

现有 OnLLMRequest 等 Hook 的 ProviderRequest 语义必须保留：通用插件上下文与准入共用；实际 LLM 请求 Hook 在相应支持该请求类型的适配路径执行。不能把 SDK/CLI 输入伪装为 ProviderRequest 后宣称所有旧 Hook 都有效。不支持的扩展在能力诊断中明确显示。

配置建议增加当前配置文件下的 `core_execution.executor_id`，缺省 `native`；外部 Body 的配置集中在对应 executor 设置中。按适配器 bot 已绑定的配置快照读取，禁止读全局配置覆盖会话配置。第一版整条执行按配置选 Body，不增加模型路由或关键词判断；未知 ID、缺少必要配置、能力不匹配都明确失败，不静默退回 Native。

factory 第一版使用有限的显式映射，重复 ID 拒绝注册。执行器不是普通 FunctionTool，也不通过 Hook/Tool target 配置选择。不新增插件市场或动态发现框架。

## 5. 最小运行契约

在现有控制契约上补齐运行、关闭和类型化输入回执。先以 Native 可表达的真实语义为准：

```python
class ExecutorRun(Protocol):
    executor_id: str

    def stream(self) -> AsyncIterator[ExecutionUpdate]: ...
    def request_stop(self) -> None: ...
    def request_follow_up(self, message_text: str) -> CoreInputTicket | None: ...
    def cancel_follow_up(self, ticket: CoreInputTicket) -> bool: ...
    async def aclose(self) -> None: ...
```

`ExecutionUpdate` 是内部有类型的进度、输出材料、最终结果之一；运行协调器消费流并保留唯一最终结果。流结束但没有最终结果算失败，不能用“进程退出码为零”替代业务成功。类型定义采用项目现有 dataclass/Enum 风格，避免散落字符串判断。

第一版由协调器通过 Head 发布 submitted、working 和终态。Body 流报告事实材料，不能另开一条 completed/failed 发布路径。迁移 Native 时应删除被替代的 Adapter 终态投影，不能让旧 finalize 与新协调器双写。现有 CoreExecutionPort 是迁移基础，按实际调用缩小 Body 可用面；激活与释放属于协调器。

`request_stop()` 只发停止信号，不能阻塞事件循环；`aclose()` 等待流、SDK 请求或子进程清理。清理超时可升级终止该 Body 自己创建的进程树，不能杀共享服务。清理失败单独记录，不覆盖原始失败/取消，也不能冒充资源已关闭。

## 6. 结果正文、进度与输出

新增内部 `ExecutionResult`，至少承载：结果正文、执行器中立的附件/文件引用、产物 ID 和可选 usage。共享结果桥再把这些材料转换为既有 MessageChain/输出物料；不得要求新 Body 返回 MessageChain、Native LLMResponse、AgentStats 或 Runner messages。Native 的完整运行证据先留在 adapter 私有边界，R5 再只抽取 Ledger 所需的可持久化事实，不能为了泛化而把 live runner 证据塞进通用结果。

`CoreExecutionArtifact` 继续只记录身份/诊断。实际正文和文件材料由结果对象传给输出桥；不能放进 event metadata，也不能只发 artifact_ready 就认为已回复。

迁移 `NativeExecutionOutputBridge` 中通用材料到 Personal/Output 的调用为共享桥；Native token/工具响应转换留在 Native Adapter。Core 进度事实与可见进度材料分开：完成的搜索阶段可以形成进度材料，纯思考继续遵守现有观察策略，不逐 token 发平台消息，也不将未经筛选的引擎推理自动公开。

共享桥根据 execution_id/turn_id 校验结果仍属于有效执行；已取消或被替代的执行迟到正文不能再次进入输出。计算 completed 与逻辑消息投递完成是不同状态，继续保留 DeliveryReceipt 和 complete_visible_message/turn 的现有边界。

Ledger 使用通用 ExecutionResult 提取执行证据；Native 的消息序列与 token 数据由其 Adapter 转换。可见历史仍只有 Output 写入，不能在替换 Body 后恢复 Stage 的第二次助手历史写入。

Cron 还要保留投递确认语义：Body 完成不等于提醒送达。必须支持既有授权发送能力，或将明确的提醒结果交共享桥并等待回执；第一版只选一种既有路径，不能发送工具和自动最终投递同时执行。

## 7. 追问、取消、终态

先将 Personal 注册对象由具体 executor 改成 Core 控制句柄，actor/session 归属由宿主保存，不能再读取 Runner.run_context。CoreInputTicket 保留当前 resolved、consumed 与撤回所需语义，Native ticket 在 Adapter 内转换。

- 返回 None 表示未接收，消息继续按原有新轮次规则处理。
- 接收不代表消费；只有引擎确实消费后才确认 consumed。
- 拒绝、取消、关闭时必须解决所有等待中的 ticket，不能永久等待。
- SDK 不支持执行中输入时显式拒绝，不伪造支持，也不自动重启任务。

取消由 Head 先确认 cancelled，再发 Body 停止信号；驱动任务在 finally 关闭 Body 并释放。用户替换、deadline、外层任务取消保留各自原因。重复取消只触发一次停止副作用，取消后的迟到结果被抑制。

Session 继续严格拒绝非法迁移；运行协调器在接收迟到事实时统一保留既有终态，不要求每个新 Adapter 复制一套 terminal 检查。构造、reset、激活、运行任一步失败均进入同一结算路径；未完成激活时也必须清理已创建的资源。

取消后的资源清理有短且有界的独立清理等待，不延长任务的执行权限和总预算。不得用后台脱离任务让已取消执行继续产生副作用。

## 8. 能力与权限

执行器可执行能力必须是本轮准入能力与执行器实际支持能力的交集。外部 SDK 自带文件、网络或 shell 能力不意味着获得授权；factory 在启动前显式配置限制。无法兑现当前限制的 Body 拒绝该任务，并说明不支持原因。

插件工具通过既有工具执行边界调用，保留准入、owner、target 与工具生命周期 Hook。仅向外部模型提供 schema 不算接入完成，还必须有经过权限校验的实际调用桥。第一版外部 Body 可以声明仅支持部分能力，不能假装支持所有现有插件。

模型提供商、Core Planner、Executor Body 是三个不同选择。更换执行器不会自动更换 Personal 或 Planner 模型；不能借新 Body 接入建立第二套人格、记忆或插件配置。

## 9. 实施批次

| 批次 | 修改范围与具体工作 | 删除/替换目标 | 退出条件 |
| --- | --- | --- | --- |
| R1 输入准备 | `astr_main_agent.py`、`core_request_preparation.py`；拆通用准备与 Native 构建 | 外部路径必须先构建 Native 的依赖 | Native 请求/Hook 时序保持；通用准备不携带 ProviderRequest；非 Native 不创建 Native Runner |
| R2 运行契约 | `executors/contracts.py`、Native Adapter；定义 Update/Result/close | Native 结果类型穿透通用边界 | Native 可经新契约完成、失败和清理 |
| R3 运行协调 | `executors/runtime.py`、Head、两个生产装配点 | 重复激活标记、重复异常终态分支、被替代的 NativeExecutionRun 包装 | 两入口共用驱动；构造到释放有明确 owner |
| R4 控制收口 | Personal follow-up、Head、Native ticket adapter | Personal 读取具体 executor 上下文 | 输入确认、拒绝、撤回、取消后 ticket 均可结束 |
| R5 输出结算 | Native output bridge、共享结果桥、Ledger 调用点 | Native 专有响应/统计解析与通用结算混杂 | 实际文本/附件到 Personal，历史一次，Cron 投递单独确认 |
| R6 选择配置 | 默认配置/schema、按配置 snapshot 的 factory、配置说明 | 两处写死 Native 构造 | 同一配置入口切换 Body；未知值明确报错 |
| R7 替换证明 | 测试用运行 Body，经 R6 同一入口运行 | 仅“注册成功”的替换性证明 | 不改 Personal/Head/Output 即可产出结果、取消、补输入和结算 |
| R8 真实适配 | 已选产品 Adapter、对应配置与基本真实验收 | 无关 SDK 细节泄漏 | Native 与真实 Body 通过相同核心验收 |

R1-R5 作为 Native 等价迁移，逐批审阅，不堆积未验证改动。R6 可以先用配置 schema 的现有页面机制展示；新增专用前端界面另行评估。只有真的修改 Dashboard 资产时才需要重新打包。

R1 实施记录（2026-09-21）：`prepare_core_execution()` 已冻结不持有
`ProviderRequest` 的 `PreparedCoreExecution`（只含 `CoreExecutionSpec` 和只读
deadline view）；Native 专属的 Provider 渲染、生命周期 Hook、Runner reset 与 fallback
Provider 装配已收敛到 `_build_native_main_agent()`。普通交互与主动任务优先消费同一份
准备 deadline view，测试桩仍保留旧路径 fallback。当前 Prompt collector 仅暂时以
`ProviderRequest` 作为输入事实来源，不能把它提升为任意 Body 的运行契约；R2/R6 接入
第二 Body 前应提供中立的 Prompt 输入载体。

R2 实施记录（2026-09-22）：新增 `astrbot.core.executors.contracts`，定义无平台、无
`MessageChain` 的 `ExecutionProgressUpdate`、`ExecutionOutputMaterial`、`ExecutionResult`、
`ExecutionOutputUpdate`、`ExecutionFinalUpdate` 和 `ExecutorRun`。Native 新增
`NativeExecutorRun`，可将 Native 工具阶段归一成粗粒度非可见进度，并产生唯一中立最终结果；
Native 专属的 token/tool/message-chain 解析仍留在 adapter，现有
`NativeExecutionOutputBridge` 仍是生产输出路径。本批未接入运行协调器、没有改动 Head 终态、
没有增加执行器配置或切换现有线上入口；R3 才负责把该契约接入两个生产装配点。

R3 实施记录（2026-09-22）：新增 `drive_executor_run()` 作为 Head 绑定 Body 的激活、流消费、
单终态投影、关闭与 release owner。它复用调用方已有的 `TurnDeadlineBudget`，不创建第二个
deadline；无 final、多 final、取消、超时与异常各自进入既有 Head 终态，清理不覆盖原异常。
主动 Core（Cron/后台）已改由该协调器驱动 Native Body，同时保留原有 Ledger 与投递确认边界。
普通交互尚未迁入：它目前依赖 Native `MessageChain` 的流式、TTS、富组件与工具状态路径，必须等
R5 的共享结果桥建立后再迁移，避免将中立结果契约错误地降级为纯文本输出。

R4 实施记录（2026-09-22）：新增 `CoreFollowUpControl` 和最小 `CoreFollowUpTicket` 表面；
Personal Runtime 现在按 Core Head、executor ID、actor ID 与停止谓词登记输入控制，而不再保存
Native executor、读取 `runner.run_context` 或直接调用 Native `follow_up/cancel_follow_up`。
Head/Lifecycle 负责将 accepted input 和撤回请求转交给当前 Body。Native 仍在 Body 边界实现 ticket；
未支持执行中输入的后续 Body 可拒绝 `provide_input()`，不会伪造支持。

R5 实施记录（2026-09-22）：新增 `ExecutionResultOutputBridge` 和
`InteractionOutputController.deliver_core_execution_result()`，让中立最终文本可进入既有
Core-final 表达、TTS、平台投递和历史边界。运行协调器现在先投影执行 completed，再将最终
结果交给输出 sink；投递失败不会回写为执行 failed，执行完成与物理投递保持不同语义。取消或
失败后的迟到结果会被桥丢弃；已完成执行的当前最终结果允许投递。`ExecutionOutputUpdate`
仍不公开，因为尚未迁移既有流式/TTS/工具状态协议；`AssetRef` 也仍只记录为延迟资产，不能
伪造平台附件发送。Native 普通交互继续使用 `NativeExecutionOutputBridge`，本批不改变线上
富输出链。

R6 实施记录（2026-09-22）：新增显式内建 factory registry 与
`core_execution.executor_id` 配置（默认 `native`）。普通交互和主动任务都从当前适配器 bot
绑定的运行时配置快照调用同一 `resolve_executor_id()`；未知或错误配置明确失败，不会静默回退
Native。当前只有 Native factory，普通交互的 Native 富输出尚未可由通用 factory 直接构造，
因此 R6 的“第二 Body 生产装配”仍依赖后续选定的适配器，而不是在当前路径伪造非 Native 支持。

R7 实施记录（2026-09-22）：新增 Scripted test Body，验证它可经同一
`core_execution.executor_id` 解析、factory 和 `drive_executor_run()` 返回中立结果并始终关闭。
该验证证明注册/选择/驱动的替换骨架，不替代实际平台输出、取消、补输入和 Cron 投递的真实
适配验收；这些仍是 R8 引入指定执行器时的门槛。

R1-R7 装配收敛记录（2026-09-22）：新增 `executors/assembly.py`，普通交互和主动
任务均通过 `build_native_executor_assembly()` 将当前配置已解析的 `executor_id`、Native
runner 和 Core port 装配为同一份 Native Body attachment。旧的 `NativeExecutionRun` 改名为
`NativeExecutionAttachment`，与中立 `NativeExecutorRun` 明确区分。普通交互继续持有
`NativeExecutionOutputBridge`，不会因此丢失流式、TTS、工具状态或富组件；主动任务从同一
assembly 创建 registry 管理的中立 run。非 Native 仍会在 Native assembly 边界明确拒绝，等待
真实 Adapter 自行提供装配路径。

每批完成后同步实际进度，提交按用户指令执行；服务由用户手动启动。失败先修当前批，不以“下一批会解决”为理由继续扩大范围。

Native 基线验收记录（2026-09-22）：用户已完成当前 Native
执行链的手动 OLV/Cron smoke 测试并确认没有阻塞问题。保留日志可见当前 bot
配置下的能力快照、Personal 表达、OLV 图像输入和 turn settlement。此记录只允许
继续收敛普通交互的装配边界；它不替代中断边界的专项验收，也不表示第二执行器已接入。

R8 Codex 初始接入记录（2026-09-23）：普通交互与主动任务现可按各自冻结的 bot
配置选择 `codex_cli`，共用外部 Core coordinator、Head 终态、结果桥与 Core-owned
session registry；取消与超时会向 Codex Body 发出停止请求。外部交互 Head 会绑定
Interaction Core execution journal；session key 优先使用类型化轮次状态中的配置 ID，缺失时
不再静默归入 `default`。Session registry 使用全局字典锁加按 key 串行锁，进程启动/关闭不占用
全局锁；Codex stderr 只保留有界尾部并记录截断诊断。

该记录不代表 R8 验收完成：Codex 当前明确运行在无 AstrBot Core 工具能力的受限模式，不接入
插件 FunctionTool、联网搜索或其他 Core 工具。Native 普通交互仍使用专用 Native runner 与富输出
桥，尚未迁入同一生产 coordinator。Codex 普通交互、Cron、连续 thread、跨 bot/workspace 隔离、
取消后的迟到结果、配置变更、进程崩溃恢复及文本/语音/effect/历史/DeliveryReceipt 均须由真实
运行验收；目前的单元测试不能替代这些检查。

## 10. 最小验证与交付

基本验证集中在公开行为，不添加大量内部调用次数 mock：

1. 同一入口分别选择 Native 与运行测试 Body，都返回真实结果材料到现有输出边界。
2. 执行中取消，只有一个终态，迟到正文不投递，资源关闭；激活前失败也回收资源。
3. 输入支持/拒绝两种情况，ticket 没有悬挂或错误消费。
4. 两个 bot 绑定不同配置，只影响各自执行器与权限。
5. 普通 OLV 工具任务与 Cron 提醒实测，核对 Personal 表达、语音、历史和 DeliveryReceipt。

第二 Body 的协议单测通过不能替代第 1 项生产入口验证。尚未选定的外部 SDK、外部输入确认语义、真实插件工具桥均保留为具体适配时的待验证项。

完成判据：新增第三个执行器只需新增具体 Adapter、factory 注册与配置，不再修改 Personal、平台 adapter、共享生命周期和结果结算逻辑。D6 未完整验收前，只标记“内部替换链已实现，平台验收待完成”。
