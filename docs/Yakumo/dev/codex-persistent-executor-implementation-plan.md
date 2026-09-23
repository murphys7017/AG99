# Codex 长期 Core 执行器实施计划

日期：2026-09-23
状态：待 5.6 分批执行
前置：`internal-executor-replacement-plan.md` 的 R1-R7 已完成；当前 Native 基线已接受，真实第二 Body 尚未接入。

## 1. 目标

将 Codex 接入为 Core 内部可替换的 Executor Body，并支持同一 bot、会话和工作区的长期执行会话。

目标调用链：

```text
Personal
  -> Core Head
  -> executor_id=codex_cli
  -> Codex Session Manager
  -> codex app-server（Core 持有的长期 stdio 子进程）
  -> ExecutorRun
  -> Core Result Bridge
  -> Personal Expression
  -> Output Controller
```

必须保持：

- Personal 是唯一对外交流窗口；
- Core Head 是任务身份、取消、终态和结算 owner；
- Codex 只负责工作区执行，不接收 Persona Prompt、TTS、effect 或平台 Event；
- Core 与 Personal 仍是同进程内异步通信，不引入分布式消息系统；
- 通过当前适配器 bot 的配置快照选择执行器，不读取其他配置文件；
- Codex 不能绕过 Capability、Output、平台权限或历史写入边界。

## 2. 非目标

本计划不做以下事项：

- 不把 Codex 变成 Personal 或 Planner；
- 不为 Codex 创建第二套人格、记忆或 Prompt 系统；
- 不把 AstrBot 的全部 FunctionTool 直接暴露给 Codex；
- 不开放 `danger-full-access` 或危险绕过审批参数；
- 不把 app-server 暴露为公网服务；
- 不同时接入 OpenCode；
- 不修改 OLV、TTS、Live2D/effect 和平台适配器协议；
- 不自动重启 AstrBot；
- 不以单元测试代替真实工作区验收。

## 3. 会话模型

### 3.1 Core 所有权

新增 `CodexSessionManager`，归 Core Runtime 所有。它负责：

- 按 `ExternalExecutorSessionKey` 查找或创建 Codex 会话；
- 启动和监督一个 `codex app-server --stdio` 子进程；
- 完成 `initialize -> thread/start -> turn/start`；
- 读取 JSON-RPC 响应与通知；
- 串行化同一 thread 的 turn；
- 将 `turn/interrupt` 映射到 Core `request_stop()`；
- 进程崩溃、协议错误或配置变化时废弃 thread 并重建；
- Core shutdown 时关闭所有子进程；
- 不把进程句柄、thread ID 或协议对象写入 Event extra。

同一个会话键只允许一个活动 turn。新的用户输入由 Core Head 决定是 follow-up、取消旧 turn 后开始新 turn，还是建立新执行。

### 3.2 会话键

继续使用：

```text
executor_id
runtime_config_id
session_id
workspace_root
workspace
```

配置、工作区或执行器变化时不得复用旧 thread。

### 3.3 传输

第一版只使用本机 stdio：

```text
codex app-server --stdio
```

不使用 WebSocket。WebSocket 只在未来需要多个宿主共享一个 Codex 服务时重新评估。

## 4. 分批实施

### C1：外部准备边界收口

范围：

- `astrbot/core/astr_main_agent.py`
- `astrbot/core/core_request_preparation.py`
- `astrbot/core/executors/external.py`
- `astrbot/core/executors/assembly.py`
- `astrbot/core/executors/registry.py`

工作：

1. 把 `prepare_core_execution()` 的调用前置为真正的 provider-neutral 路径。
2. 增加 `PreparedExternalExecutor` 或等价类型，包含：
   - `CoreExecutionSpec`
   - 当前冻结的 `CoreExecutionDeadlineView`
   - 已按当前配置计算的 Core target context projection；
   - `ExternalExecutorSessionKey`；
   - 工作区；
   - 能力交集。
3. 外部路径不得调用 `_select_provider()`、`ProviderRequest`、`_build_native_main_agent()` 或 `AgentRunner.reset()`。
4. Native 路径保持现有 Hook、Prompt、流式、TTS 和工具状态行为。
5. 外部 Prompt 不重新用默认配置裁剪历史，必须使用当前 bot 配置下已冻结的 projection。

退出条件：

- `executor_id=native` 行为不变；
- `executor_id=codex_cli` 即使没有 AstrBot Chat Provider，也能完成外部准备；
- 外部路径不会创建 Native Runner；
- 单测能证明两条路径的构造依赖完全分离。

提交：`refactor(core): separate external execution preparation`

### C2：Codex JSON-RPC 会话层

新增建议文件：

- `astrbot/core/executors/codex_session.py`
- `astrbot/core/executors/codex_protocol.py`
- `tests/unit/test_codex_session.py`

工作：

1. 使用 `asyncio.create_subprocess_exec` 启动：

   ```text
   codex app-server --stdio
   ```

2. 实现带 request ID 的 JSON-RPC 调用表。
3. 初始化参数固定声明 AstrBot 客户端信息，并关闭不需要的通知。
4. 处理：
   - `initialize`
   - `initialized`
   - `thread/start`
   - `thread/resume`
   - `turn/start`
   - `turn/interrupt`
   - `turn/started`
   - `turn/completed`
   - `item/agentMessage/delta`
5. 对未知通知进行记录并忽略，不因新增上游通知直接崩溃。
6. stdout 使用有界读取策略；stderr 进入受限诊断日志，不进入用户回复。
7. 所有请求和进程都必须由该 Manager 创建和清理。

退出条件：

- fake stdio server 可以覆盖初始化、正常完成、协议错误、进程退出；
- request ID 不串线；
- JSON 无效、EOF、超时、重复完成均转成明确的执行错误；
- `aclose()` 可重复调用且不会遗留子进程。

提交：`feat(core): add codex app-server session`

### C3：Codex Executor Body

新增建议文件：

- `astrbot/core/executors/codex_cli.py`
- `tests/unit/test_codex_executor.py`

工作：

1. 实现 `ExecutorRun`：
   - `executor_id = "codex_cli"`
   - `stream()`
   - `request_stop()`
   - `aclose()`
2. `stream()` 将 Codex 通知归一化为：
   - 阶段性 `ExecutionProgressUpdate`；
   - 最终唯一 `ExecutionFinalUpdate`。
3. `item/agentMessage/delta` 只在内部累积，不逐 token 暴露给平台。
4. 最终正文只取 Codex 最终 assistant message，不暴露 reasoning、命令原文和内部协议。
5. 第一版不支持执行中 follow-up 时，明确返回 `None`，不得伪造支持。
6. Codex 能力声明只允许：
   - `workspace_io`
   - 受 sandbox 限制的 shell
7. 外部工具能力必须使用：

   ```text
   Core admitted capabilities ∩ Codex supported capabilities
   ```

8. 不接 AstrBot FunctionTool、联网搜索工具、插件 Hook 或资产发送。

退出条件：

- 正常完成、失败、取消、超时、进程崩溃均只有一个 Core 终态；
- `aclose()` 总会执行；
- 取消后迟到正文不会进入 Output；
- 外部 Body 不直接调用 `event.send()`、TTS、effect 或历史接口。

提交：`feat(core): add codex executor body`

### C4：注册、配置和生产装配

范围：

- `astrbot/core/executors/registry.py`
- `astrbot/core/executors/assembly.py`
- `astrbot/core/config/default.py`
- 普通交互入口
- `astrbot/core/proactive_agent_turn.py`
- 相关配置文档

配置形状：

```yaml
core_execution:
  executor_id: codex_cli
  codex_cli:
    executable: codex
    workspace_root: C:\Users\Administrator\Documents\GitHub
    workspace: AG99live
    sandbox: workspace-write
    approval_policy: never
    model: ""
```

约束：

- `executable` 只能是受允许列表解析出的可执行文件名，不接受任意参数串；
- 禁止危险 bypass 参数；
- `workspace_root` 必须绝对路径且存在；
- `workspace` 必须在 root 内；
- 未知 executor、缺少配置和不支持能力明确失败；
- 不静默回退 Native；
- 普通交互和 Cron 使用同一个 factory/assembly 选择入口。

退出条件：

- 当前 bot 配置可以单独选择 Native 或 Codex；
- 没有配置 Chat Provider 时，Codex Core 任务仍可启动；
- 不同 bot 配置不会共享错误的 Codex thread；
- 仅修改配置即可切换 Body，不修改 Personal 或 Output。

提交：`feat(core): register codex executor`

### C5：共享结果与 Personal 表达验收

工作：

1. 通过 `ExecutionResultOutputBridge` 将 Codex 最终文本交给现有 Core-final Expression。
2. 保持 Personal 统一生成：
   - 最终可见文本；
   - speech cues；
   - effect calls；
   - TTS 与平台投递。
3. Core completed 与平台 DeliveryReceipt 保持分离。
4. 取消、替换、迟到结果必须被结果桥丢弃。
5. Cron 保持“执行完成”和“提醒送达”两个状态。

退出条件：

- Codex 不生成第二条独立用户回复；
- Personal 仍是唯一对外表达窗口；
- 文本、语音、动作和历史只生成一次；
- 失败时有统一的 Personal 可见错误表达。

提交：`feat(core): route codex results through personal output`

### C6：真实最小验收

用户手动启动服务后执行：

1. 单次工作区任务：读取并修改一个测试文件。
2. 连续任务：同一会话追加“继续检查并修正”。
3. 中断任务：执行中发送新任务，确认旧 turn 被中断且无迟到回复。
4. 工作区隔离：切换另一个 bot 或项目，确认不复用 thread。
5. Cron：让 Codex 完成一个短任务并由既有提醒路径投递结果。
6. 无 Provider：关闭 AstrBot Chat Provider，确认 Codex Core 仍能运行。

必须检查日志：

- `execution_id`
- `executor_id`
- `session_key`
- Codex thread ID（仅诊断日志）
- turn 状态序列
- Core terminal event
- Output delivery receipt
- cleanup 结果

退出条件：

- Native 与 Codex 通过同一 Core Head/Output 验收矩阵；
- 无重复文本、音频或 effect；
- 无跨 bot、跨工作区上下文污染；
- 无遗留 Codex 子进程。

## 5. 5.6 执行规则

- 每次只执行一个批次，完成后停止；
- 每批先 `git status --short` 和 `git diff --check`；
- 每批单独审阅并提交；
- 不自动重启服务；
- 不修改 AG99live；
- 不同时开始 OpenCode；
- 失败先修当前批次，不把问题推给下一批；
- 汇报必须区分“代码验证”“fake server 验证”和“真实平台验收”。

## 6. 最终完成标准

只有同时满足以下条件，才可宣布 Codex 接入完成：

1. Core 可在无 Native Provider 的情况下准备并运行 Codex；
2. Codex 长期 session/thread 由 Core Manager 持有；
3. Native 与 Codex 共享 Core Head、取消、结果和 Output 边界；
4. Personal 不直接读取或控制 Codex 协议对象；
5. Codex 无权限旁路；
6. 连续任务、中断、失败、超时和资源关闭通过真实验收；
7. 后续新增 OpenCode 只需新增 Adapter、factory 和配置，不修改 Personal、Output、Core Head。
