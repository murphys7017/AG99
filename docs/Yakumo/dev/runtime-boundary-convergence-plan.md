# 运行时边界收敛修复方案

状态：源码已实施，聚焦离线验证已完成；真实平台验收未执行。本文不新增冻结决策。

## 1. 目标与约束

修复系统级审阅发现的跨轮配置污染、打断身份漂移、取消后无界等待和诊断状态失真。
保留 Personal 统一交流、Core 复杂执行、Core 内部执行器可替换的现有设计。

本方案不是重新设计 Agent，不改变人格风格、effect 协议、历史长度、模型或工具选择。
不新增路由模型、自动重试、分布式队列，不修改 AG99live，不自动重启服务。
不删除公开插件入口，不承诺能够终止任意第三方协程、线程或已经发生的外部副作用。

工作区已有 `process_stage/stage.py` 与 `test_personal_runtime_capability.py` 修改。
执行者必须先审阅其最新 diff，保留相关修改，不混入本方案提交。
下列签名是建议的目标接口；实施前逐一核对当前调用者，不保留无用旧签名桥接。

批次顺序：B1 -> B2/B3 完整批组 -> B4 -> B5。
B2/B3 编码前先完成 B3b 的执行器退出调用链盘点，确定完整收尾边界。
允许先提交独立的旧任务身份固定修复，以及保持原行为的内部准备改动。
但平台终止屏障、释放锁后的清理隔离、执行器资源保护必须全部接通，
才能启用新的有界收尾行为；不得把未接通消费者的接口提交成可用修复。
各独立提交完成审阅与最小验证，完整批组再进行组合验证。
若尚未获得提交授权，只汇报，不自行提交。

### 1.1 证据与修订边界

- 已确认共享控制器保存事件级物化参数；物理投递另有固定读取全局配置的问题，
  两者不是同一种竞态，但均属于 B1 的配置归属范围。
- 已确认旧任务在平台 await 之后才被读取，以及多处取消后无界等待；
  未宣称所有线上卡顿均由它们造成。
- 不把“先取消再通知必然破坏 OLV 时序”作为结论。`head.cancel()` 本身就会
  请求执行器停止；本地 AG99 也按旧 turn_id 终止并根据停止标记跳过正常完成。
  B2 约束身份与屏障，不依赖旧任务一定存活到通知结束。
- 当前延迟启动 deadline 的内部延迟插件事件不进入普通输入打断分支。
  B2 保留入口契约检查，但不将“普通打断缺少 deadline”列为已确认缺陷。
- 当前任务和 Web API 尚未被能力清单枚举。B4 是补齐真实记录并删除过时静态判断，
  不是宣称页面已经显示了这些任务的错误标签。

## 2. B1：输出配置改为调用局部值

文件：`astrbot/core/interaction/output_controller.py`。
核对消费契约：`astrbot/core/message/message_chain_delivery.py`。

### 2.1 数据结构

在同文件增加 frozen、slots dataclass `OutboundMaterializationOptions`：

```python
@dataclass(frozen=True, slots=True)
class OutboundMaterializationOptions:
    reply_prefix: str
    show_reasoning: bool
    tts_enabled: bool
    tts_trigger_probability: float
    tts_dual_output: bool
    tts_use_file_service: bool
    callback_api_base: str
    t2i_enabled: bool
    t2i_word_threshold: int
    t2i_use_network: bool
    t2i_active_template: str
```

只保存标量，不把可变配置字典塞入 frozen 对象。

以 `_resolve_outbound_options(event) -> OutboundMaterializationOptions`
替换 `_refresh_outbound_materialization_config()`。
一次读取 `_get_runtime_config(event)`，沿用既有默认值与数值归一化。
`reply_prefix` 从该配置的 `platform_settings` 读取；
只有配置缺少该字段时才使用构造参数 `platform_settings` 的默认值。
显式空字符串必须保留，不能通过 `or default` 恢复默认前缀。

### 2.2 修改调用链

以下两个入口开头各构造一次 options：

- `materialize_interaction_outbound_message()`
- `materialize_immediate_interaction_outbound_message()`

向下列方法增加必需的 keyword-only `options` 参数：

- `_apply_interaction_reply_prefix`
- `_apply_interaction_reasoning_display`
- `_apply_interaction_tts`
- `_apply_interaction_t2i`

这四个方法只消费该对象中的输出设置，不在 await 之后重读共享字段。
TTS provider 的选择仍走现有 voice service，不另写 provider resolver。
`message.use_t2i_` 的显式覆盖、即时表达不转图片等既有差异保持原样。

删除构造函数的 refresh 调用，以及实例上的
`reply_prefix/show_reasoning/tts_trigger_probability/t2i_*` 派生字段。
保留 `_get_runtime_config` 等仍被其他消费者使用的读取函数。

### 2.3 物理投递使用当前轮次配置

`_deliver_visible_message()` 目前将 `self.platform_settings` 直接传给
`deliver_message_chain()`，影响文件路径映射、是否分段、分段间隔等。
只修改前述四个物化函数不能完成 B1。

增加 `_resolve_delivery_platform_settings(event) -> dict[str, Any]`：
优先读取已准入的当前轮次配置快照，取其中 `platform_settings` 的局部深拷贝；
无准入快照的现有调用路径使用该事件的既有配置解析入口，不另选默认 bot。
只有字段缺失时允许使用构造默认值，显式空字典、False 和空列表不得恢复成全局值。
物化和投递必须使用相同配置身份，不能在中途重新按会话路由选择另一配置。

在 `_deliver_visible_message()` 开始时获取局部投递设置，
将 `platform_settings=self.platform_settings` 替换为该局部值。
保留 `deliver_message_chain` 的现有接口及路径映射、分段规则，
不为此重写整个投递模块，也不新增全局配置缓存。
`self.platform_settings` 仅作为缺失配置时的默认值，不能写入事件的设置。

### 2.4 验证与完成标准

检查上述方法全部调用者，包括现有直接调用 `_apply_interaction_t2i` 的测试。
一个基本并发场景：A/B 使用不同 TTS 概率、图片阈值和模板，A 在异步边界暂停，
B 开始物化，再恢复 A；各自结果只能使用自身配置。
验证显式空前缀、即时表达和普通最终表达原有行为未变。
补一条投递边界验证：A/B 的路径映射和分段开关不同，
断言实际交给平台的文件路径与物理发送分段各自遵守当前轮次配置。
默认配置、显式空 platform_settings 都要保留既有字段缺失语义。
不以给整个控制器加全局锁代替修复。

## 3. B2：打断固定旧轮次身份，并限制平台终止等待

主要文件：`interaction/personal_runtime.py`。
核对：`pipeline/process_stage/stage.py`、`interaction/turn_state.py`。

### 3.1 固定身份

修改 `_interrupt_active_core_turn_for_new_input`，显式接收本次入站轮次 deadline。
在修改旧轮次之前检查入站预算，已经耗尽则交回现有超时处理，
不能先取消旧任务、再因新预算耗尽丢失平台终止责任。
在第一个 await 之前同时捕获：
`active_turn`、`active_task`、`active_event`、`head`、`executor_id`。
以后不得再通过 `self._active_turn_task` 查找取消对象。

同步阶段先设置旧轮次停止标记并通过 `head.cancel` 确认 Core 取消事实。
成功后，对捕获的旧 event 标记停止/取消，并取消捕获的旧 task。
若 task 是当前协程、已完成或为空，不取消。
不要因为平台通知失败撤销已经成立的 Core 取消事实。
这里不要求 Core 取消必须等待平台确认：`head.cancel()` 可能已经同步触发
执行器停止回调。正确性依靠下一节的准入屏障，不依靠旧任务的存活时长。
屏障必须在可能让出控制权、释放旧 lease 之前登记，不能等通知超时才登记。

保留现有布尔返回语义：
`True` 表示确实取消了旧 Core，不表示平台播放队列确认完成。
不要新增一个包含多种未使用状态的通用打断框架。

### 3.2 平台通知

平台通知使用捕获的旧 event：
`abort_visible_turn(reason="superseded_by_new_user_input")`。
上限取新轮次剩余预算和内部短阶段上限的较小值，建议阶段上限初值 2 秒。
这是待运行验证的内部保护值，不新增用户配置、不重置总 deadline。

等待应使用有界 task 等待，不能仅用会等待取消收尾的 wait_for。
通知任务从创建时就归属当前 PersonalSessionRuntime 的清理屏障；
通知进行中也要阻止后续同会话轮次开始可见输出。
正常通知完成后撤销其屏障；超时或调用者取消时，对通知任务发取消，
保留未退出任务的跟踪并消费最终异常。
异常或取消退出只证明协程结束，不等于平台已经成功终止：
记录平台终止结果未知，不伪造确认，不通过自动重试重复发送控制消息。
若无法确认后续输出可安全开始，保持受影响目标的拒绝状态，
由明确的平台恢复/目标重新初始化解除；该恢复入口必须在编码前列明。
其他会话不受影响。此保护由 B3 的同一清理记录负责，不另造全局隔离管理器。

新轮次 deadline 已耗尽时走现有 deadline 处理，不启动新的平台通知。
如果旧轮取消已经成立后预算才耗尽，已登记的终止/清理责任仍由屏障持有，
不能随着入站请求退出而遗失。
调用者取消时传播 CancelledError；对旧 task 的取消不能依赖平台 await 成功。
普通输入应复用其已有 deadline；内部延迟插件事件仍不参与主动打断。
若未来调用者传入没有 deadline 的普通输入，明确拒绝错误调用或在正式准入入口
建立预算，不能在打断 helper 内悄悄重置/续期。

### 3.3 身份保护

`PersonalTurnLease.release()` 清理活动字段前，核对
`runtime._active_turn_context is reservation.turn`。
只清理自己拥有的上下文，记录不匹配而不是清空其他轮次。
会话锁仍由持有它的 lease 释放，不能仅因身份不匹配跳过锁的资源收尾。

### 3.4 验证

旧轮 A 的平台通知暂停，A 已结束、会话状态改变后恢复通知：
只能取消捕获的 A，不取消新的活动 task。
组合验证中同会话候选 B/C 必须被屏障拦住，不能为了构造测试而绕过正式准入。
平台通知不返回时，新请求在有限时间内得到明确失败/拒绝状态，
不无限等待、不开始可能被迟到 abort 影响的新可见轮次。
覆盖等待者被取消、通知异常和通知成功三种结果，检查屏障回收/保留符合实际结果。
不宣称此测试验证了 AG99 前端实际播放中断。

## 4. B3a：输出封闭与有界任务清理

主要文件：`interaction/turn_state.py`、`interaction/personal_runtime.py`、
`interaction/output_controller.py`、`prompt/context_collect.py`。

### 4.1 先划清两个不同状态

`execution_scope.closed` 表示不能再创建轮次任务，不等于所有输出都应禁用。
例如 speculative Persona 被抑制时，Core 最终回复仍应允许。
因此不能简单把 scope.closed 或 final_output_status 当作所有输出的拒绝条件。

先核对现有取消/终态输出检查；缺少统一事实时，仅在 TurnState 增加
`output_closed_reason: str | None`，通过
`close_interaction_turn_output(event, *, reason: str)` 首写封闭。
取消、真正结束及进入最终清理时设置；超时先按既有策略处理一次允许的故障提示，
随后封闭，不允许靠临时取消封闭来补发提示；
中间回复、Core 执行完成但最终表达尚未投递时不得设置。

在 `_deliver_visible_message` 的每次物理 send 前检查封闭状态；
对合成/模型等待返回后的发送也必须生效。
若前面已有物理组件成功，后续被封闭不能伪造全部成功或调用逻辑完成回调。
沿用现有 delivery receipt 的取消/失败表示，不再建立第二套回执。
发送前检查不能撤销已经进入平台的 send；仍在进行的物理发送/完成通知也必须
纳入收尾盘点。无法确定结果时保留 unknown/failed，不写“零副作用”。
正常 delayed plugin delivery 使用自己的新轮次，不继承已结束父轮次的发送权限。

### 4.2 有界清理接口

扩展现有 TurnExecutionScope，不建立另一套通用任务执行器：

```python
async def close(self, *, timeout_seconds: float) -> TurnCleanupResult:
    ...
```

`TurnCleanupResult` 为 frozen dataclass，仅包含未退出任务的 tuple。
流程：标记 closed -> 对未完成任务发 cancel -> 使用 asyncio.wait 等待有限时间
-> 分离 done/pending -> 返回结果。
不得在超时分支继续 await gather(pending)，否则恢复原来的无限等待。
重复 close 必须返回当前剩余任务，而非因为 closed=True 就假称已完成。

保留对 pending 的强引用与 done callback；回收时消费异常并删除引用。
回调是同步短操作，不创建新的无限后台清理任务。
`cancel_and_detach` 也必须保留可追踪记录，不能从所有权集合中彻底消失。
detach 表示不阻塞正常业务阶段，不表示后续 shutdown 不再管理它。

执行清理预算独立命名为 teardown grace，建议初值 1 秒：
业务 deadline 到期后只允许取消和回收，不再允许模型、工具、发送或重试。
这不是给任务续期。具体数值应通过实际取消日志校准。

### 4.3 不能安全退出的任务

不要声称可强杀不合作的协程。若 pending 仍存在：

- 旧轮次输出已封闭，不再提交旧轮次新输出。
- 在 PersonalSessionRuntime 保存未退出任务集合及平台终止结果，状态为清理隔离中。
- 屏障安装后才释放原 lease 的资源锁，新准入在获取锁前和获取锁后均检查，
  包括旧轮释放前已经排队的调用者；明确拒绝，
  不把新任务投入无限等待，不与未知旧副作用并行。
- 最后一个任务退出、且没有未解决的平台终止/执行器资源状态时移除隔离；
  不能把单纯 task.done() 当作全部恢复。拒绝结果不能把新任务标成成功。
- 只限制受影响 session。日志包含 turn_id、task name、role 和耗时，不记录正文。

给 TurnAdmission 增加独立 `cleanup_pending` 结果，不复用 `skipped_busy`：
后者当前会继续运行部分插件，不能作为清理隔离出口。
ProcessStage 遇此结果必须停止该事件，不启动插件、Personal 或 Core。
同样检查 `submit_runtime_observation_event()`、延迟插件输出和主动任务的准入消费者，
不能只接 ProcessStage；定时/主动调用者收到明确的未投递结果，不能记录提醒成功。
群聊静默记录；私聊只有平台终止屏障允许发送时才使用既有失败提示策略，
禁止为了提示失败而绕过屏障，也禁止回退成“模型不可用”。

这些记录归属现有 PersonalSessionRuntime，不新增独立队列或持久化隔离数据库。
`is_idle()`、runtime 淘汰和 shutdown 必须识别未清理记录，
不能通过淘汰并重建 runtime 绕过屏障。重绑定可能改变 runtime key 的路径也需核对，
不得让同一输出目标的未结束终止操作因配置/人格切换失去归属。
实现范围仅是现有运行链的安全收尾，不承诺拦截全部第三方直接网络发送。

插件可能绕过系统封装调用外部服务，封闭输出不能撤销已经发生的副作用；
隔离仅提供安全退化，不应在文档中写成完整沙箱。

### 4.4 Prompt 收集

`_await_prompt_extension_collectors` 的 drain/超时分支改用同一有界取消语义。
插件 collector task 纳入当前 scope 跟踪；超限 pending 不再被当作成功 contribution。
只读已完成 task 的结果，保留已有确定顺序与失败隔离。
不在每个 collector 上分配完整 grace，整组共享一次清理上限。
若当前路径没有 TurnState，先审计调用者归属；不允许无 owner 地遗留 task。

### 4.5 最小验证

一个 collector 收到取消后仍等待受控事件，另一个正常完成：
正常结果保留，等待有限结束，pending 被跟踪。
同 session 新任务明确拒绝，不执行插件；其他 session 正常工作。
旧任务退出后 session 恢复；旧任务迟到输出不发送。
增加已排队请求与主动/延迟输出消费者的基本检查，确保清理结果没有被忽略。
不增加全量模拟测试矩阵。

## 5. B3b：复核执行器退出是否仍能占住 lease

文件：`executors/runtime.py` 及各 `ExecutorRun.aclose` 实现。

`drive_executor_run` 的 finally 当前直接 await run.aclose。
只修 TurnExecutionScope.close 不能证明整轮收尾有界，必须检查这条链。

本节盘点必须在 B2/B3 编码前完成，而不是 B3a 上线后再补查。
先列出所有 aclose 实现与调用者，区分关闭流、取消本地任务、
外部进程/连接释放及持久会话释放。
若发现无界等待，沿用 B3a 清理上限和隔离归属，不另加无限 gather。
未清理完成的持久执行器资源不得立即重新出租给下一轮。
至少核对 `CodexExecutorRun.aclose()`、`CodexSessionManager.aclose()`、
`ExternalExecutorSessionRegistry` 的 discard/close 路径，以及 Native run 的退出入口。
不得只在外层超时后继续执行无条件 `head.release_executor()`，
却没有记录 run/持久会话是否仍在使用；Head 绑定释放与外部资源可复用不是同一个事实。
Core 的业务终态、资源尚在清理两个事实分别记录；
不得把输出发送失败反向覆盖已经成立的 Core completed。

只有验证旧 run 无法再使用已释放/重新出租的会话，才允许提交该批。
如果需要改变持久 executor pool 的资源租约接口，先列出影响，暂停扩张并请求评审。
不把“有界返回”当成“外部进程已经退出”。
允许独立提交保持现有行为的整理，但 B2/B3 的完成条件统一：
普通取消、通知未完成、collector 不响应取消、执行器关闭不响应取消四条路径
都不能无限占住调用者，也不能让未安全退出的旧资源被下一轮复用。

## 6. B4：能力页面读取真实治理事实

文件：`star/context.py`、`plugin_capability_inventory.py`、
`dashboard/src/components/shared/PluginCapabilityInventory.vue` 与现有语系文件。

### 6.1 先增加真实注册记录

`Context.list_plugin_capability_owners()` 当前仅枚举六类 Interaction 注册表，
不包含 `_register_tasks` 和 `registered_web_apis`。
`build_capability_inventory()` 也不会凭 `_MIGRATION_STATES` 自动产生这些行。

先扩展该列表接口：

- 遍历 `_register_tasks`，通过 `id(task)` 查询 `_registered_task_owners`。
  不执行 awaitable，不根据 coroutine repr 猜 owner，不输出可能含敏感参数的 repr。
- 遍历 `registered_web_apis`，使用与注册/卸载一致的 `(route, tuple(methods))`
  查询 `_registered_web_api_owners`，不自行改变 key 的排序/归一化语义。
- 有 owner 的记录填写 scope 中的 module/plugin name；无 owner 的记录显式归入
  unknown/unowned，不借类型模块名假装已实现回收。
- 每条记录补齐 `item_name` 和当前注册生命周期内稳定的 `registration_id`。
  任务可用注册对象的进程内身份作为只读 ID，不宣称跨重启稳定；
  路由用现有结构化注册 key 表达身份，不用拼接字符串猜解。

`build_capability_inventory()` 必须传递这些字段；
前端 `capabilityKey()` 优先使用 registration_id，避免同一插件多个任务/路由同 key。
不为了清单展示改写公开 `register_task()` 或 `register_web_api()` 的调用约定。

### 6.2 再显示治理状态

在实际记录上增加只读 `lifecycle_management`：

- `owner_managed`：该注册确有 owner，且对应卸载路径已实现回收/撤销。
- `unowned`：注册无 owner，不能按插件回收。
- `not_evaluated`：未审计或当前没有足够事实。

由 Context 根据实际 owner 表生成，不由 dashboard 根据能力 kind 猜测。
任务依据 `_registered_task_owners` 与注册记录；
Web API 依据 `_registered_web_api_owners`。
`owner_managed` 只表示具备管理机制，不保证正在运行的任务已经响应取消。

`build_capability_inventory` 传递该字段。
删除 GLOBAL_TASK 的静态 `leak_on_unload` 判定。
Web API 的 owner 回收状态不能冒充权限门禁；生命周期治理与权限评估继续分列。
其他实际已枚举但未审计的能力标记 not_evaluated，不猜测已修复。
Provider/Cron 等未被本接口枚举的能力不生成占位记录，不宣称清单已经覆盖全部注册面。

前端显示翻译后的只读状态，保留 config_id 与“未评估会话适用性”说明。
同步 READ_ONLY_FIELDS 和响应结构，不新增可编辑治理开关。

验证 owner task / ownerless task / owned web API 三类清单记录，
并检查同插件多个注册不重名覆盖、卸载后已移除记录不再出现。
执行前端类型检查和构建；本批完成后需要重新部署前端，但不自动操作部署。

## 7. B5：统一交互契约与验收记录

本批不新增运行时模式、不改变当前对话行为。
当前新普通输入优先取消活动 Core，和旧验收矩阵“追问进入同一 execution”不一致。
在取得明确选择前，不实施以下任何一个新机制：

- 不添加新的 LLM 路由判断。
- 不按关键词偷偷区分取消/补充。
- 不为了保留 provide_input 接口而强行让普通消息进入旧执行器。

建议本轮基线：普通新消息保持当前替换行为；
provide_input 仅用于明确进入补充输入流程的调用者。
如果用户希望自然语言追问继续原任务，作为单独行为设计评审，不混入安全修复。

更新以下文件的状态与实际行为：

- `execution-backend-acceptance-matrix.md`
- `cross-component-convergence-plan.md`：删除“尚未执行”与“全部已实施”自相矛盾的描述。
- `legacy-retirement-inventory.md`
- `persona-system-final-goal.md`：只修改确认的交互时序，不改人格表现。

每项区分源码实施、离线验证、真实平台验收，不填写未经执行的通过结论。

## 8. 交付要求

每批报告：实际修改函数、删除项、调用者检查结果、最小验证、未验证项、提交号。
不以测试数量代替架构边界证明。
B1 优先；B2/B3 为完整安全收尾批组，不能只交付“取消后直接放开锁”，
也不能把执行器关闭盘点延后到新清理行为启用之后。
B4 不等同于插件系统全部治理完成，B5 不等同于平台验收通过。
真实验收至少包含：双配置并发、连续三条打断、插件取消不及时、普通成功与定时提醒。
本方案不宣称覆盖整个仓库的所有问题。

## 9. 实施记录与验收边界

- B1：物化参数固定为调用局部标量，物理投递设置在逻辑消息入口按准入配置读取；显式空 `platform_settings` 不回退构造默认值。
- B2/B3：旧轮身份在平台 await 前固定；旧输出封闭、平台 abort 有界等待，结果未知时受影响目标拒绝新准入；未退出的任务留在会话隔离中。轮次清理和 Prompt collector drain 有界，取消不等于已经撤销平台发送或外部副作用。
- B3b：Native run 关闭其活动异步流；Codex run 中断并有界等待，未确认时其持久会话不可复用。执行器关闭任务归属轮次 scope；关闭失败不释放 Core 绑定。ExternalExecutorSessionRegistry 的 discard 只有关闭成功才删除注册，shutdown 的创建任务等待有界。
- B4：任务/Web API 从注册记录进入只读清单；owner 管理状态由 Context 报告，Dashboard 类型检查及生产构建通过。
- B5：普通新消息保持替换旧 Core 的行为，专门补充输入才使用 `provide_input`；验收矩阵记录此区别。

离线聚焦验证 49 项通过；现有主动表达去重断言有 2 项预期 `None` 但实际返回 `False`，Prompt 工具清单断言有 4 项失败，均未改业务行为以掩盖失败。真实 OLV 双配置并发、连续三条打断、插件迟缓取消、普通成功和定时提醒未运行；未知平台 abort 需要显式运行时重启才能解除拒绝状态。未提交、未重启服务。
