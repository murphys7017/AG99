# 跨组件一致性整改实施方案

日期：2026-09-21

源码基线：`e363adfc4`

状态：B1-B7 已完成源码实施和离线验证；真实平台验收待执行，不是新的冻结决策。

用途：交给后续编码模型逐批执行；不得把本文件的目标态描述为当前实现。

## 实施状态

本方案 B1 至 B7 已按独立提交完成源码实施与离线验证：

| 批次 | 当前结果 |
| --- | --- |
| B1 | 逻辑消息回执区分物理部分失败、完成通知失败与取消未知状态 |
| B2a | 模型表达统一经过发送前检查 |
| B2b | 插件 Persona 与主动输出传播策略抑制结果 |
| B3 | 普通与主动 Core 共用请求准备生命周期 |
| B4 | 主动 Core 使用目标配置中的唯一总 deadline |
| B5 | 删除无生产消费者的通用命令回执、命令 mailbox 与歧义入口 |
| B6 | 插件能力清单按配置文件读取并明确未进行会话/适用性评估 |
| B7 | Live 普通成功只在公共收尾保存一次历史；失败/取消出口保留 |

这表示本方案的代码批次已经完成，不表示真实平台验收已经完成。OLV 普通闲聊、
Core 工具任务、执行中追问、取消/超时、定时提醒、插件 Persona 抑制、双配置页面
和 Live 成功/取消仍需按第 13 节由用户重启后验证。B6 修改了前端资源，需要重新部署
本次 Dashboard 构建。

交互时序以当前源码为准：Core 工作期间的普通新消息替换旧轮；`provide_input`
仅供明确补充输入流程调用，不作为普通消息的隐式兜底。运行时收尾、配置归属及
插件生命周期清单的后续收敛见 `runtime-boundary-convergence-plan.md`，不回填为
本方案 B1-B7 的真实平台验收结果。

## 1. 目标、范围与完成定义

本轮目标不是继续增加层次，而是消除普通对话、主动任务、插件和输出之间的行为差异。

保持三个边界：

1. Personal 是统一对外交流窗口；快速表达、进度表达和最终表达属于同一职责。
2. Core Head 负责执行身份、控制、事件顺序和终态；不是 Prompt、平台发送或数据库的大容器。
3. Native Executor 是 Core 内部执行实现；本轮不接入第二个 Executor。

本轮允许改动的行为：

- Personal 模型表达补上当前缺失的发送前检查。
- 主动 Core 补上请求 Hook、Hook 后能力校准和总执行预算。
- 消息完成通知失败不再与成功回执混淆。
- 无消费者的“接受命令即算完成控制”接口收窄。
- 插件能力页面明确当前选中的配置文件。
- 删除确认重复的 Live 历史提交调用。

明确不做：

- 不修改人格 Prompt 风格、历史长度、memory 策略、effect schema 或模型选择。
- 不新增 LLM 判断，不引入消息中间件、分布式协议、可靠后台队列。
- 不把插件 Handler-first 路径删除，不把所有 Runner 纳入 Core。
- 不重写 ProviderRequestBuilder，不移动历史与 Ledger 数据库 owner。
- 不自动重试消息、任务或工具副作用，不自动重启 AstrBot。
- 不修改 AG99live 源码、忽略目录内插件或实际配置文件。
- 不为开发阶段的内部过渡 API 保留两套实现；公开插件接口则必须核对调用者。

完成定义：

“代码实现完成”“静态/基本验证通过”“真实平台验收通过”分别记录。只有最后一项完成，
才允许把本轮整体状态改为完成；测试通过不等于用户听到了语音。

## 2. 已复核的问题与证据边界

| 编号 | 已确认的源码事实 | 本轮处理 |
| --- | --- | --- |
| F1 | `capture_message_chain()` 即时分支、`_emit_stream_interjection()` 没有经过最终回复的 PreOutputProcessor | 统一模型表达检查入口 |
| F2 | `run_proactive_agent_turn()` 未派发 waiting/request Hook，未调用 `bind_effective_core_request()` | 共用请求准备步骤 |
| F3 | 主动任务未建立 `TurnDeadlineBudget`，只有工具级限制和最大步数 | 入口创建一次总预算 |
| F4 | `_deliver_visible_message()` 先记录 delivered，再执行 `complete_visible_message()` | 分开物理发送和逻辑完成 |
| F5 | `dispatch_command(CANCEL)` 只登记；运行复现 accepted 后仍 submitted，停止回调 0 次 | 收窄有歧义的控制 API |
| F6 | 能力清单 API 固定读取 default 配置，组件未传配置 ID | 配置感知的只读清单 |
| F7 | Live 分支和公共收尾都调用 `_save_to_history()` | 核对后保留唯一普通成功提交点 |

证据限制：

- F4 是可达异常分支，不代表已从 OLV 日志证实线上发生。
- F5 当前生产控制走 `cancel()/provide_input()`；不是宣称线上取消全部无效。
- F3 不是“工具完全没有超时”；问题是没有覆盖整次主动执行的累计预算。
- F7 不等于已证实重复插入消息：普通历史是 update，Interaction Ledger 还有幂等保护。
- 这次不是所有第三方插件、所有 Provider 的完整兼容性审计。

## 3. 责任表和统一时序

| 事实 | 唯一责任位置 | 其他组件允许做什么 |
| --- | --- | --- |
| 配置、插件准入、deadline | 当前执行的 TurnState/准入入口 | 读取既有快照 |
| Hook 后请求、有效能力 | 共享请求准备函数 | 使用返回的同一 build result |
| 执行身份、终态、取消 | Core Head | Adapter 上报事实、调用控制入口 |
| Native 步骤和 stream 关闭 | NativeExecutionLoop/Adapter | 输出桥消费材料 |
| 模型表达审核、装饰、物化 | Output Controller + PreOutputProcessor | 不建立第二套输出策略 |
| 物理链统计 | `message_chain_delivery.py` | 不判断平台逻辑 finalize 成功 |
| 逻辑消息完成回执 | TurnState，Output Controller 写入 | Plugin/Cron 消费成功或失败结果 |
| 可见对话历史 | 既有 Personal/Output 持久化路径 | Core Ledger 不冒充对话历史 |
| 执行 Ledger | 既有写库路径 + Head settlement | 不在 finally 外再重复结算 |

目标时序：

```text
普通输入 / 主动触发
  -> 解析目标配置并冻结当前执行快照
  -> 建立或复用当前执行唯一 deadline
  -> waiting Hook
  -> build_main_agent(apply_reset=False)
  -> request Hook
  -> bind_effective_core_request
  -> Runner reset
  -> Head activation
  -> Native loop
  -> 已有 Personal/Output 表达入口
  -> result contributions
  -> 发送前安全检查与装饰 Hook
  -> TTS/图片等物化
  -> 物理发送
  -> 逻辑消息完成通知
  -> 完成回执、可见输出记录
  -> 既有历史提交、turn completion
```

注意：这不是要求每个入口都遍历全图。纯 Personal 不经过 Core，静默主动任务可以没有
可见输出，插件 direct 模式保留直接输出契约。

## 4. F1：统一模型表达的发送前处理

### 4.1 修改文件

- `astrbot/core/output_lifecycle.py`
- `astrbot/core/interaction/output_controller.py`
- `astrbot/core/interaction/middleware.py`
- 若返回值传播涉及插件：`plugin_artifact_delivery.py`、`personal_runtime.py`、
  `plugin_execution_types.py`、`delayed_plugin_delivery.py`
- 相关已有输出测试，不批量扩充内部测试。

### 4.2 返回契约

在 `output_lifecycle.py` 增加小型结果类型，不增加另一套 Processor：

```python
@dataclass(frozen=True, slots=True)
class PreOutputResult:
    message: MessageChain | None
    reason: str | None = None
    stop_requested: bool = False
```

约束：

- `message is not None` 代表允许继续物化，不代表发送成功。
- `reason` 限定为内部短码，如 `unsafe_response`、`decorator_stopped`、
  `empty_after_decoration`；不得存回复正文。
- `stop_requested` 记录装饰 Hook 显式终止事件的意图，不从空消息推断终止整个任务。

将现有方法改为：

```python
async def prepare_interaction_message(
    self,
    event: AstrMessageEvent,
    message: MessageChain,
    result_content_type: ResultContentType,
) -> PreOutputResult:
    ...
```

内部步骤：

1. 使用现有 `create_agent_lifecycle_overlay()/activate_agent_lifecycle()` 隔离临时
   result/stop/extras；不在共享 event 上保存后再跨 await 恢复。
2. 使用独立的消息组件副本构造 `MessageEventResult`，保留 use_t2i/use_markdown/type
   等现有字段；避免 Hook 就地修改组件时连带修改 Core 的原始结果。
3. 复用 `response_is_safe()` 与 `run_decorating_hooks()`，不重写插件准入/target。
4. 保留原有审核前置顺序；如果 Hook 改变了文本，再对最终文本做同一安全策略检查。
   这是本地检查，不追加模型调用。
5. 从 overlay 中取出装饰结果，返回可用链或明确的抑制原因。
6. 不在此方法里发送、TTS、完成 turn、持久化或取消 Executor。

legacy `response_is_safe()` / `run_decorating_hooks()` 仍供 ResultDecorateStage 使用；
本批不重写非 Interaction 管线。

### 4.3 由 Output Controller 安排时序

可添加一个薄方法，避免每个分支重新解释抑制原因：

```python
async def _prepare_model_expression(
    self,
    event: AstrMessageEvent,
    message: MessageChain,
    *,
    message_kind: str,
) -> MessageChain | None:
    ...
```

只负责调用 Processor、记录一次抑制诊断、返回准备后的链。
模型表达显式用 `ResultContentType.LLM_RESULT`；不能从 Core 当前 event.result 猜测类型。

接入点：

| 路径 | 插入位置 |
| --- | --- |
| 即时 Personal | result contributions/文本 override 之后，immediate materialization 之前 |
| Core-final 经 Personal 的表达 | 现有 PreOutputProcessor 位置，替换返回值处理，不重复调用 |
| stream interjection | 最终文本形成之后，immediate materialization 之前 |
| `plugin_persona` | Persona 改写之后、materialization 之前 |

不对每个流式 token/chunk 运行 Hook；这里只接已经完整形成的逻辑表达。
`plugin_direct`、原始媒体及协议输出不能因为此整改被强制人格重写。
对经过 ResultDecorateStage 的原始插件输出也不能再次装饰。

抑制政策：

- 空表达或安全拦截只代表这一条候选没有投递，不自动取消正在执行的 Core。
- 装饰 Hook 显式 `stop_event()` 时，由 Output Controller 在退出 overlay 后传播该
  明确停止意图；不能因隔离而吞掉插件原有 stop 语义。
- Core-final 的既有 suppressed/cancelled 处理保持；不要把最终状态写为 delivered。
- 即时表达被抑制时不能写 EMITTED；已有 COMMITTED 预留应经 TurnState helper 转为
  SUPPRESSED，不能回退 PENDING 让同一候选重新发送。
- 纯回复轮次应正常走既有完成流程但标记 suppressed；delegate 轮次没有显式 stop
  时继续 Core，不因为没说开场白而取消任务。

### 4.4 必须修改成功传播，不能“无异常就当发送成功”

`emit_immediate_spoken_reply()` 与 middleware 的 `_emit_immediate_reply()`、
`_emit_immediate_reply_or_record_failure()` 改为传播 `bool`：

- 完整逻辑发送成功才返回 True。
- 空表达、审核抑制返回 False，不能进入 send_failed 异常日志。
- 真正投递失败仍抛异常，保留既有错误记录。

`capture_message_chain()` 的内部调用若需要该返回值，同步改为 bool 并检查所有分支；
完成标记、重复抑制和无消息分支不能凭空返回 True。
`emit_failure_reply()` 要依据实际结果将 final reservation 结算为 DELIVERED 或 SUPPRESSED。

`capture_plugin_output()` 的 Persona 抑制也必须传递到调用者：

- 返回 bool，并更新 `PluginOutputSink` 注解。
- `PluginArtifactDeliveryCoordinator` 不得在 False 时标记 DELIVERED_INLINE。
- 如需表达审核抑制，在既有 `PluginDeliveryDisposition` 加 `SUPPRESSED_BY_OUTPUT_POLICY`，
  不把它冒充重复去重，也不创建新账本。
- Personal 主动输出和 delayed delivery 的调用者不能忽略 False。
- 本批只要求抑制不被算成成功；不顺带设计跨平台重发机制。

### 4.5 最小验证

复用 `test_output_lifecycle.py` 和既有 Interaction 输出用例：

- 即时表达被检查拦截后，没有平台输出、没有 TTS 请求、没有可见历史。
- 装饰后的文本与后续 TTS、semantic_text、可见历史一致，effect/附件未无故丢失。
- 两个任务共享 event 时，一个表达的 Hook result 不覆盖另一任务的 Core result。
- 插件 Persona 被抑制时，artifact 不标记已投递。

只补建立上述边界所必需的基本输入输出用例，不以私有方法调用次数作为验收目标。

## 5. F2：统一 Core 请求准备

### 5.1 组织方式

新增 `astrbot/core/core_request_preparation.py`，定位为 Native Core 请求准备协调函数。
不是另一个 builder，不导入 Stage，不操作平台、数据库或可见输出。

提供两个窄函数：

```python
async def begin_core_request_lifecycle(
    event: AstrMessageEvent,
    *,
    hook_dispatcher=call_event_hook,
) -> AgentRequestLifecycle | None:
    ...

async def finalize_core_request_preparation(
    event: AstrMessageEvent,
    build_result: MainAgentBuildResult,
) -> bool:
    ...
```

`begin_core_request_lifecycle()`：

1. 构造 Core surface 的 `AgentRequestLifecycle`，保留现有 reasoning/postprocess 参数。
2. 调用 `dispatch_waiting()`；被插件停止则返回 None。
3. 不调用 builder，不激活 Head。

`finalize_core_request_preparation()`：

1. 要求 build result 来自 `apply_reset=False`，且具有绑定的 request lifecycle。
2. 调用 `dispatch_request()`；停止则返回 False。
3. 调用唯一的 `bind_effective_core_request()`。
4. 更新 `build_result.capabilities` 和 `build_result.execution_spec`，将有效 spec 写回
   TurnState；不能只更新 req.func_tool 而留下过时 spec。
5. 返回 True。这里不 reset、不激活 Head、不运行模型。

不要重新 render 整个 Prompt 覆盖插件修改；继续用既有 Hook 后能力校准规则。
不要复制 `CapabilityResolver` 判断或新增“允许所有 Hook 工具”的旁路。

### 5.2 deferred reset 的资源归属

在现有 `MainAgentBuildResult` 上加入方法，避免两个入口分别管理 coroutine：

```python
async def reset_prepared_runner(self) -> None:
    ...

def discard_pending_reset(self) -> None:
    ...
```

- `reset_prepared_runner()` 取出 reset_coro 后置 None，再 await；未准备或重复 reset 明确报错。
- `discard_pending_reset()` 只 close 未开始的 coroutine 并置 None；已 await 的不再次关闭。
- caller 的 finally 始终调用 discard，覆盖 provider 检查拒绝、Hook 停止和异常。
- 不把 coroutine 存入 Head，不让 builder 为同一 Runner 创建两套 reset。

### 5.3 两条入口改造

普通 `InternalAgentSubStage.process()`：

1. 用 begin helper 代替原先 lifecycle 初始化与 waiting 派发。
2. 保持 `build_main_agent(..., apply_reset=False, request_lifecycle=lifecycle)`。
3. 原有 Provider 校验仍放在 request Hook 前，不在此批删改安全检查。
4. 调用 finalize helper；False 正常退出。
5. 使用最终 spec 绑定 Head/只读 deadline/journal。
6. await `reset_prepared_runner()`，成功后才 activate executor，再注册 Personal follow-up。
7. 输出桥、循环、历史保存和正常错误表达保持原 owner。

主动 `run_proactive_agent_turn()`：

1. 显式 begin lifecycle，传入同一个 lifecycle 构建。
2. 将 builder 调用改为 `apply_reset=False`。
3. finalize helper 通过后，绑定最终 spec、deadline view、journal。
4. reset 成功后 activate，然后运行既有 NativeExecutionLoop。
5. 不增加独立 Output Bridge，不把内部最终 summary 发给用户。

reset/激活顺序的异常注意：

- reset 失败时 Runner 可能还没有 run_context，不能调用依赖 Adapter.event 的 fail/cancel。
- 已有 Head 但未激活时通过该 Head 记录失败/取消；已有 spec 但未绑定时保留当前失败落库。
- 两个入口都显式维护 `runner_reset_completed` 与 `executor_activated`：前者控制证据读取，
  后者控制绑定释放。reset 未完成时，失败/取消持久化 helper 不读取 Adapter.messages/stats；
  可以传 `native_executor=None` 并使用已取得的 spec/request。
- 只有 activate 成功才设置 `executor_activated=True`，才能释放该绑定。
- 未激活、未绑定的情况不能伪造 submitted/completed。
- release 必须在外层 finally 中，即使 Ledger 构造/写入失败也必须执行。
- 当前普通 Stage 的无条件 `native_executor.release_from_core_head()` 也要改成上述条件，
  不能只改 except 而在 finally 再触发未初始化上下文异常。

主动 Hook 停止的本批结果：

- 不请求模型、不执行工具；使用专用 `CoreRequestPreparationStopped` 异常向主动调用者报告，
  记录 `request_stopped_by_plugin`，当前 Cron 按失败保留一次性任务。
- 不伪造 completed，不把它误当等待重试许可，不自动发送额外失败说明。
- 普通交互仍保持 Hook 停止后的正常 return，不把这套主动任务异常搬过去。

### 5.4 删除范围与验证

删除普通 Stage 中已被 helper 替代的 request Hook/能力校准代码；不能只让主动任务复制它。
builder 的其他调用者保持原 apply_reset 默认，不批量改 Personal、第三方 Runner 和插件 helper。

基本验证：

- 普通/主动入口中，一个 request Hook 对请求文本的修改均到达 Provider。
- Hook 删除某 FunctionTool 后，实际请求和有效 spec 均不再包含它。
- Hook 停止时无 Provider 请求、无未 await coroutine warning。
- reset 抛错保留原始异常，不被 Adapter.event 二次异常覆盖。

参考现有 `test_proactive_agent_turn.py`、`test_agent_lifecycle.py`、
`test_astr_main_agent.py`，不重跑无关全仓测试。

## 6. F3：主动任务的唯一执行预算

### 6.1 配置来源

使用目标会话冻结配置的
`load_interaction_agent_config(runtime_config).turn_timeout`。
本批不新建 cron_timeout，不从 tool_call_timeout 推导总预算，也不写死 120。

这会限制主动任务的累计耗时，是明确的行为变化：默认使用既有 turn_timeout。
若真实长任务需要不同上限，应单独评审配置设计，不能由编码者擅自乘以 max_step。

### 6.2 函数与生命周期

可在 `proactive_agent_turn.py` 增加局部 helper：

```python
def _ensure_proactive_execution_deadline(
    event: CronMessageEvent,
    runtime_config: Mapping[str, Any],
) -> TurnDeadlineBudget:
    ...
```

- state.deadline 已存在则复用；否则创建一次并赋给 TurnState。
- 创建时间在配置冻结之后、插件准入查询/历史读取/Hook/build 之前。
- 这是新的主动 execution，不继承原始用户对话已耗尽的 deadline。
- 不把同一 Cron job 的 budget 保留到下次触发。

在 `run_proactive_agent_turn()` 中：

```python
try:
    async with deadline.enforce("proactive_core_execution"):
        # admission, history, request hooks, build, reset, activate, loop
        ...
except TurnDeadlineExceeded:
    # cancel Head when available, preserve deadline_exceeded reason, re-raise
    ...
finally:
    # discard pending reset, settle existing ledger, always release executor
    ...
```

实施注意：

- 将当前 try 之前的异步准入、conversation 查询等纳入保护范围。
- conversation 未取得时不能访问 conversation.cid；记录构建阶段诊断，不伪造对话 ID。
- 总预算到期在 Core 记 cancelled + reason=deadline_exceeded。
- Cron 当前 schema 可保持 status=failed、last_error=明确超时原因：
  它表示计划这次未成功执行，与 Core 的取消事实不是同一枚举，不要求强行相同。
- 外部 shutdown CancelledError 仍重抛，并保持 Cron 的 cancelled 语义。
- Ledger/资源清理置于执行预算外，不能用已耗尽预算跳过清理；也不 shield 整个业务任务。
- 数据库清理失败仍执行 release；复用当前数据库超时和 Cron 关闭保护，不新增第二个业务 deadline。
- 不改变 Core 成功与投递成功的区别；已发出提醒后若后续失败不能自动重跑提醒。

### 6.3 最小验证

- 很小的预算下让受控 Provider 等待，任务终止，Core 终态不是 completed。
- 同一执行没有第二次工具执行和自动重试，后续同版本任务不永久占用 running 标记。
- 任务自身抛 TimeoutError 不被误归为总预算耗尽。
- 一次普通成功任务仍正常通过，不新增 Personal 模型调用。

## 7. F4：物理发送与逻辑完成分离

### 7.1 不变部分

保留 `MessageChainDeliveryResult` 的 sent_any/all_succeeded/计数。
它只回答物理发送，不把 AG99live finalize 语义塞进这一通用层。
`complete_visible_turn()` 的位置和契约保持不变。

### 7.2 TurnState 回执扩展

在既有回执列表中扩展字段，不建立平行列表或第二个 ledger：

```text
physical_status: delivered | partial | failed | unknown
completion_status: not_attempted | pending | completed | failed | unknown | not_required
status: pending | delivered | partial | failed | unknown
failure_stage: physical_send | message_completion | null
```

- `delivered` 只在物理全部成功且逻辑完成成功时成立。
- `unknown` 用于取消时无法确认外部效果；不得叫作“安全未发送”。
- 无 message ID 的既有非分段路径可标 not_required；Interaction 正常逻辑输出必须有 ID。
- 回执成功只代表适配器完成调用成功，不代表客户端播放或用户阅读。

将原 `record_interaction_turn_delivery_receipt()` 拆为两个明确 helper：

```python
def begin_interaction_turn_delivery_receipt(
    event, *, message_id: str, message_kind: str,
    delivery_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ...

def finish_interaction_turn_delivery_receipt(
    event, *, message_id: str,
    physical_result: MessageChainDeliveryResult | None,
    completion_status: str,
    failure_stage: str | None = None,
) -> dict[str, Any]:
    ...
```

状态写入全部在 helper 内完成；返回诊断副本，调用者不得原地修改列表里的 dict。
同一 message_id 更新同一回执；重复开始报错，终态重复完成只允许相同结果，禁止失败转成功。
状态取值用 Literal 或已有风格的 Enum 校验，不能接受任意字符串。
`MessageChainDeliveryResult` 在 TurnState 中只用于类型标注时使用 TYPE_CHECKING 导入，
不要为了类型提示引入 TurnState -> delivery -> platform event 的运行时初始化环。

### 7.3 `_deliver_visible_message()` 的写法

1. 取得 message_id，begin receipt。
2. await deliver_message_chain。
3. 全失败/部分失败：finish 相应物理结果，completion=not_attempted，抛现有投递异常。
4. 全成功：await event.complete_visible_message(message_id=...)。
5. completion 成功才 finish delivered，随后记录 fingerprint，返回物理可见 ID。
6. completion 抛普通异常：finish physical=delivered、completion=failed、status=failed；
   保留 message_id，重新抛原异常。
7. completion 期间取消：finish completion=unknown，再重抛 CancelledError。
8. physical send 期间取消而拿不到完整计数：finish physical=unknown、
   completion=not_attempted，不伪造 failed_count。
9. 每种终态只发一次最终 receipt trace；如保留过程 trace，事件名须明确是进度。

不要 catch 后返回物理 ID 冒充成功，不重发已经成功的音频/文本，不自动再次 finalize。
不要把已完整物理发送但 finalize 异常的情况写为“没有任何副作用”。

### 7.4 调用者与持久化边界

- `_record_visible_output()` 仍只在逻辑完成成功后执行。
- Plugin artifact 保留已有异常处理，但不得与 receipt 的成功状态冲突。
- `Context.send_message()` 维持公开 bool 返回；内部必须把真实失败传递为 False/异常。
- Cron 不改为读取 unrelated turn 的全部 receipts；先保持当前关联发送调用的成功传播。
- 已发送但历史保存失败不得回滚 receipt 成失败，也不得为补历史重发消息。
- `_has_send_oper` 是行为标记，不升级成权威回执；不要用它覆盖完成失败异常。

### 7.5 基本验证

- Record + Plain + Image 全成功：一个逻辑完成通知，一个成功回执。
- 物理部分成功：不调用逻辑完成，回执 partial。
- 物理全成功但完成通知失败：回执非 delivered，调用者失败，没有成功 fingerprint。
- 完成通知期间取消：unknown，不自动重试。

现有 `test_output_lifecycle.py` 和相关消息投递用例优先复用。

## 8. F5：Core 控制入口去歧义

### 8.1 选择收窄，不造 worker

当前实际生产控制是：

```python
head.activate_executor(...)
head.provide_input(executor_id=..., message_text=...)
head.cancel(executor_id=..., metadata={"reason": "user_cancelled"})
```

保持这些入口为唯一业务控制 API。
本轮不把 `dispatch_command()` 补成另一套带 ticket/dedup/retry 的控制分发器。

在 `execution.py`：

1. 将 Lifecycle/Session 的命令登记方法改成内部 `_record_command()` /
   `_accept_command_record()`；具体命名允许按本地风格调整，但 docstring 必须说明只记录。
2. `start()/cancel()/provide_input()` 内部记录 CoreCommand；保留原 first-write 和发布顺序。
3. 删除 Head 对外的 `accept_command()/dispatch_command()`。
4. `CoreCommandReceipt` / disposition 若仅服务被删除入口，删除类型及 __all__ 导出；
   如发现外部真实调用，不加兼容壳，先报告调用者并确认迁移范围。
5. 命令 mailbox 若全仓仍只有测试使用，删除该 mailbox 类型、订阅管理和对应内部
   command publisher 链；CoreCommand 记录/去重所需语义保留。
6. event mailbox 与同步 journal subscriber 不因为名字相似被一并删除。
   本批不重新设计 Personal 的异步进度消费。

实施前必须再次 `rg` 搜索生产调用和公开导出；不要只凭当前计划认定永远无调用。

验收：

- 从真实控制入口 cancel 时状态变 cancelled，停止能力被触发，重复取消不重复副作用。
- provide_input 只有 Native 接受后才产生补充输入记录，ticket 语义不变。
- 无调用者仍能通过“接受 CANCEL 记录”得到控制成功假象。
- 删除只验证旧 receipt/mailbox 机械行为的项目自有过渡测试，保留控制边界的基本测试。

## 9. F6：配置感知的插件能力清单

### 9.1 API

修改 `PluginRoute.get_plugin_capabilities()`：

```text
GET /api/plugin/capabilities?config_id=<id>
```

- 无参数默认 `default`，但返回体必须显式显示这个作用域。
- 使用 `astrbot_config_mgr.confs[config_id]`；不存在返回现有 Response.error 形态，
  不静默回退默认配置。
- 调用现有 `build_capability_inventory(event=None, context=..., runtime_config=selected)`。
- 返回 data 增加：

```json
{
  "evaluation_context": {
    "config_id": "default",
    "scope": "configuration",
    "session_evaluated": false,
    "applicability_evaluated": false
  },
  "plugins": []
}
```

本批只校正配置目标视图，不伪造 event/session，不让一个配置清单显示“会话已允许”。
保留 interaction permission 的 not_evaluated；前端使用中文标签，不直接显示内部英文枚举。

### 9.2 页面

文件：

- `dashboard/src/components/shared/PluginCapabilityInventory.vue`
- `dashboard/src/views/extension/PluginDetailPage.vue`
- 对应现有 i18n 文件。

实现要求：

1. PluginDetailPage 用已有 `/api/config/abconfs` 的 `data.info_list` 获取配置选择项。
2. 页面持有 selectedConfigId；给清单组件新增必需 `configId` prop。
3. 清单 `load(configId)` 请求对应 API；`watch(configId, ..., { immediate: true })`
   代替只在 mounted 加载。
4. 配置快速切换用请求序号或 AbortController 阻止旧响应覆盖新选择。
5. 清单表头附近显示配置名；权限和适用性明确显示“未进行会话评估”。
6. 保持只读，不另外创造 target 编辑与保存配置入口。
7. 加载、失败、无能力状态保持完整，不残留上一配置的表格冒充新配置。

基本验证：

- 两份配置相反的 Hook/FunctionTool target 在页面切换后显示不同结果。
- 无效配置 ID 失败，不显示默认结果。
- 类型检查、构建、桌面/窄屏查看；不顺手改全站样式。
- 本批前端需要重新构建部署，其他批次纯后端不要求前端部署。

## 10. F7：删除 Live 重复历史提交

文件：`pipeline/process_stage/method/agent_sub_stages/internal.py`。

当前 Live 分支 yield 返回后有一次 `_save_to_history()`，公共尾部又调用一次。
先读完整异常路径和 `_save_to_history()`，再删除 Live 分支内的普通成功保存块。
保留公共正常保存和独立失败/取消保存，不新增 `history_saved` extra 掩盖重复控制流。

保持：

- streaming 消费完毕后才判断最终 Runner 状态。
- Interaction 可见历史由 Personal 保存，Core 只保存既有执行证据。
- 非 Interaction Live 成功只提交一次原有历史。
- 取消的 partial evidence 仍有出口，不能以“只保存一次”为由删掉异常收尾。
- 不调整 TTS 音频队列或重新播放流。

普通成功一次与取消一次基本验收即可，不为每条内部分支创建大量 mock 测试。

## 11. 批次、顺序和提交边界

建议执行顺序：

| 批次 | 内容 | 完成后再进行 |
| --- | --- | --- |
| B1 | F4 回执与逻辑完成失败 | B2 |
| B2a | F1 Processor 隔离、即时/最终/插话处理和抑制返回 | B2b |
| B2b | F1 插件 Persona/主动调用者的抑制传播 | B3 |
| B3 | F2 两条 Core 请求准备、reset 资源收口 | B4 |
| B4 | F3 主动执行总预算与清理 | B5 |
| B5 | F5 命令入口减法清理 | B6 |
| B6 | F6 配置感知清单 | B7 |
| B7 | F7 Live 重复持久化删除、更新进度文档 | 真实验收 |

先修回执再增加检查，是为了保证新引入的抑制/失败不会被旧成功判断吞掉。
B2a/B2b 是同一契约的两批接线；中间不得宣称全部输出一致性已经完成。

每批建议一个语义完整提交，提交前必须有用户明确授权；“让我写计划”不是提交授权。
不把八批代码攒成一个大提交，不在修复批次中夹带 Provider、模型或实际配置变化。

文档在对应批次完成后更新，不提前批量打勾：

- `execution-backend-preparation-plan.md`
- `current-state.md`
- `全局架构审计-当前问题.md`
- 必要时更新 `.ai/state.yaml` 中本轮实际范围和验证缺口。

历史实施记录保留日期，不把旧断言原地改成“当时已完成”。

## 12. 给编码模型的执行约束

每次只执行一个批次，优先使用本地代码证据：

1. 阅读 `.ai/index.md`、本批章节、当前 diff、所有直接调用者。
2. 用不超过十行列出：修改文件、成功路径、拒绝路径、异常清理、删除点。
3. 实现最小变化；新增函数必须有当前真实调用者，不能只为未来接口占位。
4. 搜索旧入口与返回值消费者，确保没有“新增 helper 但旧逻辑仍执行”。
5. 基本验证通过后审阅 diff，再汇报完成点、未验证项与实际行为变化。
6. 用户授权后才提交，提交完成再次检查工作区。

禁止事项：

- 不通过 return True、吞异常、补默认权限使测试变绿。
- 不新增兼容开关掩盖两条实现不一致。
- 不把测试替身缺字段直接解释为生产可选字段；先核对真实契约。
- 不对实际 data/config 做修改，不停止 main.py，不触发真实外发/定时副作用。
- 不把个人表达的快与最终表达重新拆成两个 Agent。
- 不把所有事实都搬进 Head，不增加新路由模型、轮询器或缓存。
- 不新增自动恢复/重试，因为本轮没有解决副作用幂等。

需要停下来报告而不是擅自扩展的情况：

- 发现冻结条款与本方案冲突。
- 删除候选 API 有真实外部调用者。
- 新的检查必须更改 effect/Prompt/TTS 对外协议才能工作。
- 真实验收暴露 AG99live 的行为差异；记录边界证据，再单独协调该仓库。

## 13. 验证与真实验收

离线验证使用隔离根目录，例如：

```powershell
$env:ASTRBOT_ROOT = (Join-Path $PWD '.tmp/component-convergence')
try {
    python -m pytest -q <本批相关模块>
} finally {
    Remove-Item Env:ASTRBOT_ROOT
}
```

另运行修改文件的 Ruff、compileall、`git diff --check`；不反复重跑未变化的大套件。
仅文档改动不运行业务测试。没有运行的验证明确写为未执行。

由用户手动重启后进行：

| 场景 | 检查结果 |
| --- | --- |
| OLV 普通闲聊 | Personal 只表达一次，文本/音频/effect 正常 |
| Core 工具任务 | 即时与最终表达不重复，请求 Hook 生效 |
| 执行中追问 | 输入仍进入同一有效 Core，不丢 ticket，不变成第二次任务 |
| 用户取消/超时 | 唯一终态，停止回调生效，没有迟到成功覆盖 |
| 一次定时提醒 | 触发、表达、音频、逻辑完成与 Cron 状态对应 |
| 定时任务失败/超时 | 保留并停用一次性失败任务，不自动重试副作用 |
| 插件 Persona 审核抑制 | 不发送，不将 artifact/Cron 标成成功投递 |
| 两份 Bot 配置 | 页面显示的 target 与各自配置对应 |
| Live 普通成功/取消 | 成功历史一次，取消证据保留 |

禁止用任意敏感内容测试安全检查；离线用受控策略返回值即可。
Provider 慢、客户端播放失败和服务器投递失败要分开记录。

## 14. 何时继续 Executor 解耦

满足下列条件才进入下一个解耦设计批次：

- 普通和主动 Core 的请求生命周期及能力快照一致。
- 每次 execution 有且只有一个总预算 owner。
- 输出成功、抑制、失败、未知外部效果能够区分。
- Personal 输出没有被 Core 或插件新增旁路替代。
- 本轮真实验收取得证据，已知阻断问题清零。

下一步再评估执行任务启动/取消句柄的归属，以及是否有第二个真实 Executor 的需求。
不能把“Head 接管 Prompt、数据库、所有输出”当作解耦完成定义，也不能凭新增类型数量
或测试数量宣布 Phase 9 已完成。
