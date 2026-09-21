# 内部可替换执行器：5.6 操作级实施手册

日期：2026-09-21
适用对象：负责实际编码的 5.6 执行模型
前置文档：

- [`internal-executor-replacement-plan.md`](internal-executor-replacement-plan.md)
- [`execution-backend-preparation-plan.md`](execution-backend-preparation-plan.md)
- [`.ai/index.md`](../../../.ai/index.md)

这不是新的架构提案，而是把既定方案翻译成可以逐批执行的编码指令。每次只完成一个批次，跑完本批验证后停止，等待审阅，不要自动进入下一批。

## 0. 总规则

### 0.1 本次目标

把 Core 内部的执行实现从“代码中直接构造 Native”改成：

```text
同一份 Core 请求准备
    -> 按配置快照选择 executor_id
    -> factory 创建 ExecutorRun
    -> Core coordinator 驱动 run
    -> 结果交给现有 Output Controller
    -> Head 负责唯一终态
```

第一阶段只证明 Native 可以被这套抽象承载；第二个真正执行器要在 Native 等价迁移完成后再接入。不要先为某个厂商 SDK 写特殊分支。

### 0.2 明确不做的事

本批及后续 R1-R7 均不得：

- 修改 Personal 的人格 Prompt、快速回复策略、记忆裁剪规则；
- 修改 OLV/平台适配器、TTS、`complete_visible_message` 或消息投递协议；
- 把 Personal 和 Core 改成进程间/分布式协议；
- 新增第二套插件准入、工具权限或模型路由；
- 用关键词、模型名、平台名选择执行器；
- 为了“兼容”保留两条并行终态、历史写入或输出通道；
- 把整个 `AstrMessageEvent`、`Context` 或插件管理器传给第三方执行器；
- 在未验证当前批次的情况下继续下一批；
- 自动重启服务或自动提交 Git。

### 0.3 每批开始和结束都要做

开始前：

```powershell
git status --short
git diff --check
```

阅读本批涉及文件，并确认没有覆盖用户已有修改。结束时汇报：

1. 修改文件和新增接口；
2. 删除或替换了哪些旧路径；
3. 实际执行的验证命令及结果；
4. 未验证项、已知限制和下一批阻塞点。

## 1. 先理解现有边界

### 1.1 现有生产调用者

只允许从以下两个入口切换执行器：

- `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`：普通交互；
- `astrbot/core/proactive_agent_turn.py`：Cron/主动任务。

二者应最终调用同一个 Core 运行协调器。不能在第三处复制一份“选择 + 激活 + 运行 + 结算”逻辑。

### 1.2 现有所有者

| 责任 | 唯一所有者 |
| --- | --- |
| 用户是否委派 Core、初期对外表达 | Personal Runtime |
| execution_id、状态迁移、终态幂等 | `CoreExecutionHead` |
| 创建/驱动/关闭 Body | Core coordinator |
| Provider/SDK/CLI 细节 | Executor Adapter |
| 文本、音频、图片、effect、平台发送 | Output Controller |
| 历史可见写入 | Output Controller/既有历史入口 |
| 执行证据 | Core Ledger |

若实现出现两个所有者，以表格为准修正，不要通过额外回调掩盖。

## 2. R0：建立基线，不改生产行为

### 2.1 必须检查的源码

逐个阅读以下对象，并记录现有调用关系：

- `astrbot/core/execution.py`
- `astrbot/core/astr_agent_run_util.py`
- `astrbot/core/astr_main_agent.py`
- `astrbot/core/core_request_preparation.py`
- `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`
- `astrbot/core/proactive_agent_turn.py`
- `astrbot/core/interaction/personal_runtime.py`
- `astrbot/core/interaction/output_controller.py`
- `tests/unit/test_core_execution_events.py`
- `tests/unit/test_native_executor_adapter.py`
- `tests/test_proactive_agent_turn.py`

重点确认三件事：

1. 哪些地方仍调用 `NativeExecutionRun.from_runner`；
2. 哪些地方直接写 `complete`/`fail`/`cancel`；
3. 哪些地方直接向 Output Controller、历史或 Ledger 写入。

### 2.2 基线命令

只跑与执行器相关的最小集合：

```powershell
uv run pytest tests/unit/test_core_execution_events.py tests/unit/test_native_executor_adapter.py tests/test_proactive_agent_turn.py -q
uv run ruff check astrbot/core/execution.py astrbot/core/astr_agent_run_util.py astrbot/core/proactive_agent_turn.py astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py
```

如果基线已经失败，先记录失败，不要把基线问题伪装成 R1 修复成果。

## 3. R1：拆分“请求准备”和“Native 构建”

### 3.1 目标

`build_main_agent()` 目前同时做上下文准备、Provider 请求构造、Native Runner 构造。先把通用准备抽出来，保持输出和 Hook 时序不变。

### 3.2 建议类型

在 `astrbot/core/astr_main_agent.py` 定义一个内部准备结果，名称可按代码风格调整，但语义必须包含：

```python
@dataclass
class PreparedCoreExecution:
    execution_spec: CoreExecutionSpec
    deadline_view: CoreExecutionDeadlineView | None
```

`CoreExecutionSpec` 已经拥有 `ContextPack` 和 `CoreCapabilitySnapshot`。因此准备对象不得再保存
`context_pack`、`capabilities`、`task_spec` 或新的执行身份副本；若新增 `PreparedCoreExecution`，它只能
容纳 Spec 之外、且没有其他 canonical owner 的执行期事实，例如现有
`CoreExecutionDeadlineView`。`AgentRequestLifecycle` 与 `ProviderRequest` 是 Native Provider/Hook
边界的对象，不得因为“通用准备”被提前提升为任意 Body 的公共字段。Native factory 消费准备结果后
自行渲染并构造 `ProviderRequest`，其他 Body 也只能消费自己的目标输入。

### 3.3 具体操作

1. 从 `build_main_agent()` 中先抽出生成 `CoreExecutionSpec` 所需的上下文、插件准入和 Prompt
   pipeline 事实；只读 deadline 继续使用现有 `CoreExecutionDeadlineView`。
2. 保留一个 Native 专属函数，例如 `_build_native_runner(prepared, ...)`，只负责 Native render、
   `ProviderRequest`、`AgentRequestLifecycle`、Native adapter 和 runner reset。
3. 普通交互和主动任务先继续走 Native，但调用关系变成：

   ```text
   prepare_core_execution -> build_native_executor_run
   ```

4. 外部 executor 路径不能先执行 `_build_native_runner()`。
5. `OnLLMRequest` 等既有 Hook 的触发顺序、准入快照和错误传播保持不变；只有真正生成
   ProviderRequest 的 Body 才拥有该请求类型的 Hook 扩展点。

### 3.4 R1 完成条件

- Native 普通交互和 Cron 行为不变；
- `prepare_core_execution()` 自身不构造 Native Runner 或 `ProviderRequest`；
- 后续 factory 可只消费准备结果而无需重新构造 ContextPack、能力或执行身份；
- `build_main_agent()` 不再是唯一的执行器选择点；
- 相关测试通过；
- 不新增第二个 deadline owner。

完成后停止，不要在同一批创建 factory 或配置项。

## 4. R2：定义最小运行契约

### 4.1 文件

新增目录建议为 `astrbot/core/executors/`：

```text
astrbot/core/executors/
  __init__.py
  contracts.py
```

通用生命周期仍放在 `execution.py`；不要为了目录整洁大规模搬迁现有类型。

### 4.2 类型契约

在 `contracts.py` 定义项目现有 dataclass/Enum 风格的类型，不能用散落字典和字符串：

```python
@dataclass(frozen=True)
class ExecutionProgressUpdate:
    summary: str | None = None

@dataclass(frozen=True)
class ExecutionOutputMaterial:
    text: str | None
    assets: tuple[AssetRef, ...] = ()

@dataclass(frozen=True)
class ExecutionResult:
    output: ExecutionOutputMaterial | None
    artifacts: tuple[CoreExecutionArtifact, ...] = ()
    token_usage: TokenUsage | None = None

@dataclass(frozen=True)
class ExecutionOutputUpdate:
    output: ExecutionOutputMaterial

@dataclass(frozen=True)
class ExecutionFinalUpdate:
    result: ExecutionResult

ExecutionUpdate: TypeAlias = (
    ExecutionProgressUpdate | ExecutionOutputUpdate | ExecutionFinalUpdate
)

class ExecutorRun(Protocol):
    executor_id: str
    def stream(self) -> AsyncIterator[ExecutionUpdate]: ...
    def request_stop(self) -> None: ...
    async def aclose(self) -> None: ...
```

此处应优先复用实际已有的 `AssetRef`、`CoreExecutionArtifact`、`TokenUsage` 和
`CoreExecutionDeadlineView`，不再临时发明 `ExecutionAttachment`、`UsageSummary`、
`CoreExecutionEvidence` 或通用 ticket 类型。Native 的 `NativeExecutionEvidence` 仍是 adapter
私有证据；R5 再决定如何把其中可持久化的部分映射给 Ledger。实际类型名可以适配项目风格，但必须
保留以下语义：

- `stream()` 只能产生上述三个类型；不得回退为 `kind + object payload`、字典或散落字符串；
- stream 没有且仅有一个 `ExecutionFinalUpdate` 时是失败，不以“进程退出码 0”代替业务成功；
- `request_stop()` 不阻塞事件循环；
- `aclose()` 等待 SDK/子进程清理；
- Body 不直接调用 Head 的终态方法；
- Body 不直接发送平台消息。

本批不要把 follow-up 加进 `ExecutorRun`。现有 `FollowUpTicket` 是 Native runner 的具体对象；
它在 R4 由单独的 Core 输入控制契约收口，以免 R2 先把 Native ticket 泄漏成公共协议。

`ExecutionOutputMaterial` 不能直接引用 `MessageChain`、平台 adapter 或 Event。共享结果桥负责转换为既有输出物料；不能把正文塞进 `event.extra`。

### 4.3 Native 适配

在 `astrbot/core/astr_agent_run_util.py` 增加 Native 对 `ExecutorRun` 的实现：

- Native token、tool response、AgentStats 解析只留在 Native adapter；
- 通用边界只暴露 `ExecutionUpdate`/`ExecutionResult`；
- 现有 `NativeExecutionOutputBridge` 在本批只做最小包装，不同时重构 OLV 输出；
- `NativeExecutionRun.finalize()` 不得与未来 coordinator 双写 Head 终态。

## 5. R3：实现唯一运行协调器

### 5.1 文件和入口

新增 `astrbot/core/executors/runtime.py`，定义一个唯一驱动入口，例如：

```python
async def drive_executor_run(
    *,
    head: CoreExecutionHead,
    run: ExecutorRun,
    output_sink: CoreOutputSink,
    deadline: TurnDeadlineBudget,
) -> ExecutionResult:
    ...
```

不要让普通交互和 Cron 各自复制实现。若函数名不同，必须保证只有一个生产驱动实现。

### 5.2 驱动顺序

严格按以下顺序实现：

1. coordinator 取得已创建的 `head` 和 `run`；
2. 通过 Head 激活 Body，一次且仅一次；
3. 按现有 Head 语义发布 submitted/working；
4. 在同一个 `TurnDeadlineBudget` 内消费 `run.stream()`；
5. `PROGRESS` 只进入既有观察/进度策略；不逐 token 发平台消息；
6. `OUTPUT`/`FINAL` 交给 output sink；不直接调用平台 adapter；
7. 正常结束仅由 coordinator 产生一次 completed；
8. 异常、取消、超时分别映射到既有 Head 终态；
9. `finally` 中先 `aclose()`，再释放 Body 句柄；清理错误不能覆盖原始终态。

### 5.3 迟到结果和重复终态

coordinator 必须在提交输出前检查 execution_id/turn_id 仍属于当前有效执行：

- 已 cancelled/replaced 的执行，迟到正文丢弃并记录原因；
- 同一执行只能有一个终态；
- `complete_visible_message` 和 `complete_visible_turn` 仍由输出投递链负责；
- 不能因为 Body 完成就跳过 DeliveryReceipt。

### 5.4 两个入口迁移

先改 `internal.py`，再改 `proactive_agent_turn.py`：

- 两处只负责准备参数、选择 run、调用 coordinator、处理各自的历史/投递确认；
- 两处不得再直接解释通用终态；
- Cron 的“提醒已生成”和“提醒已送达”继续分开；
- 不要把主动任务特有的发送能力塞入通用 Body 契约。

## 6. R4：收口 Personal 追问和取消

### 6.1 目标

`personal_runtime.py:_FollowUpCoordinator` 不得再依赖 Native executor、Native ticket 或 `runner.run_context`。

### 6.2 新控制接口

先在调用点审计 `FollowUpTicket` 的 `resolved`、`consumed`、撤回三种实际语义，再定义小型
Core 输入控制协议。`CoreExecutionPort.provide_input()` 现阶段仍返回 `Any | None`，R4 的目标是
把它收紧为一个只暴露以下公共字段的 `CoreFollowUpTicket` 协议；Native `FollowUpTicket` 留在
adapter 内实现该协议或由 adapter 包装，Personal 不得继续读取 Native runner：

```python
class CoreInputControl(Protocol):
    def provide_input(self, message_text: str) -> CoreFollowUpTicket | None: ...
    def cancel_input(self, ticket: CoreFollowUpTicket) -> bool: ...
```

`CoreFollowUpTicket` 的最小公开面是 `resolved: asyncio.Event` 与 `consumed: bool`。Body 同时需要
明确的撤回回调；不能要求 Personal 保存 Native executor 仅为了调用 `cancel_follow_up()`。Personal
只处理：接收、等待 resolved、确认 consumed、撤回。

### 6.3 必须保留的语义

- 返回 `None`：本轮不接收，消息按新轮次规则处理；
- accepted 不等于 consumed；
- 拒绝、取消、关闭时解决所有等待 ticket；
- SDK 不支持执行中输入时明确拒绝，不伪造支持；
- 重复取消不能产生第二次 stop 副作用。

## 7. R5：结果、历史和输出结算

### 7.1 唯一输出路径

通用结果必须经过已有 Core result/output bridge，再进入 `OutputController`。执行器不得：

- 直接调用 `event.send()`；
- 直接写可见助手历史；
- 直接调用 TTS/effect；
- 用 `artifact_ready` 代替正文回复。

### 7.2 结果分层

区分：

1. `ExecutionResult`：正文、附件、usage、执行证据；
2. `CoreExecutionArtifact`：身份和诊断；
3. `DeliveryReceipt`：平台物理投递完成。

三者不可互相替代。Core completed 不代表平台已完成发送。

### 7.3 历史去重

迁移后只保留当前既有历史入口。重点搜索并逐个确认：

```powershell
rg -n "save_history|append.*history|complete_visible|artifact_ready|event\.send" astrbot/core
```

若同一结果在 Stage、coordinator、Output 三处写入，删除重复写入，而不是增加去重标志掩盖所有权错误。

## 8. R6：执行器注册与配置选择

### 8.1 注册表

新增显式 factory registry，不使用插件动态发现：

```python
register_executor_factory("native", build_native_executor_run)
resolve_executor_factory(executor_id)
```

要求：

- 重复 ID 启动即失败；
- 未知 ID 明确报错；
- registry 初始化顺序固定；
- factory 接收最小服务集合，不接收完整 Event/Context；
- 不允许按模型名、消息关键词或平台名偷偷改 executor_id。

### 8.2 配置

当前配置文件增加：

```yaml
core_execution:
  executor_id: native
```

读取必须来自当前适配器 bot 绑定的配置快照，不读其他配置文件，也不由全局配置覆盖。外部执行器自己的配置放在其 namespace 下。

未知 executor、缺少必要配置、能力不匹配都失败并对外给出统一错误；第一版不静默回退 Native。

### 8.3 两个入口统一

普通交互和 Cron 都调用同一个 `resolve_executor_id(config_snapshot, execution_source)`。不要让 Cron 有自己的默认值，也不要把 Personal 的 provider 设置当 executor 配置。

## 9. R7：用测试 Body 证明真正可替换

### 9.1 测试 Body

在测试目录定义 `ScriptedExecutorBody`，通过与生产相同的 factory/coordinator 入口运行，不要直接调用私有 adapter 方法。至少提供四个脚本：

1. 输出正文后正常完成；
2. 中途失败；
3. 收到 stop 后取消；
4. 接收一条 follow-up 后继续并完成。

### 9.2 必测断言

只断言公开结果和状态：

- 同一 execution_id 只有一个终态；
- 结果材料进入现有 output sink；
- 取消后的迟到输出不会投递；
- `aclose()` 总会执行；
- follow-up accepted/consumed 语义正确；
- Native 和 Scripted 通过同一入口得到同一类 `ExecutionResult`。

不要把测试写成“某个私有函数被调用几次”的顺序锁定。

## 10. R8：接入真实执行器前的门槛

只有 R1-R7 全部通过，才能开始真实 Adapter。接入时按以下顺序：

1. 先写该引擎的最小 `ExecutorRun` wrapper；
2. 明确它支持的输入、取消、附件和工具能力；
3. 用 capability intersection 拒绝不支持的 Core 请求；
4. 不把 SDK 自带网络、文件、shell 权限当成 AstrBot 授权；
5. 先跑本地最小任务，再做 OLV 普通工具任务和 Cron 提醒；
6. 核对 Personal 文本、音频、历史、DeliveryReceipt，没有重复回复后再扩大范围。

## 11. 每批验证命令

R1-R5 至少执行：

```powershell
uv run ruff check astrbot/core/execution.py astrbot/core/astr_agent_run_util.py astrbot/core/astr_main_agent.py astrbot/core/core_request_preparation.py astrbot/core/proactive_agent_turn.py astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py astrbot/core/interaction/personal_runtime.py
uv run python -m compileall -q astrbot/core
uv run pytest tests/unit/test_core_execution_events.py tests/unit/test_native_executor_adapter.py tests/test_proactive_agent_turn.py -q
```

R6-R7 额外执行：

```powershell
uv run pytest tests/unit/test_executor_registry.py tests/unit/test_executor_runtime.py -q
```

如果文件尚未创建，不要为了满足命令创建空测试；在汇报中说明“本批未有对应测试文件”。

## 12. 交付汇报模板

每批完成后按以下格式汇报：

```text
批次：R?
完成：<实际完成的接口/调用链>
修改文件：<列表>
删除/替换：<旧路径及原因>
验证：<命令 + 结果>
未验证：<真实平台/外部 SDK/配置等>
风险：<是否存在兼容或回滚风险>
下一步：<只列下一批，不直接执行>
```

“代码能导入”“单测通过”不能写成“真实执行器已接入”。完成 R7 只能表述为“Native 与测试 Body 共享替换链已验证”；完成 R8 后才可以表述为“指定真实执行器已接入”。

## 13. 最终完成判据

新增第三个执行器时，只需：

1. 新增一个 Adapter/Run；
2. 注册一个唯一 `executor_id`；
3. 增加它自己的配置和能力声明；
4. 不修改 Personal、Output Controller、平台 adapter、Core Head、共享 coordinator、历史结算。

若新增执行器仍需要修改上述共享模块，说明解耦尚未完成，应回到对应批次修正，而不是继续堆适配分支。
