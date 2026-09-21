# Execution Backend 真实验收矩阵

更新时间：2026-09-21

本矩阵用于 D6。它不是自动化测试替代品，而是启动 AstrBot、OLV 或 Live 后按日志和
外部表现逐项核对的统一记录格式。每项都要同时记录 `turn_id`、`execution_id`、
`executor_id` 和逻辑输出段 `message_id`。

本矩阵不把 D5-A/D5-B 的显式端口注入视为“可替换执行器已完成”。R1-R7 完成后，先按
[内部可替换执行器：5.6 操作级实施手册](internal-executor-replacement-implementation-guide.md)
验证 Native 与测试 Body 的同一生产装配入口，再开始真实外部 Body 的平台验收。

## 0. D5/R7 装配前提

| 场景 | 必须观察到 | 禁止出现 |
| --- | --- | --- |
| 默认 Native | 当前 bot 绑定的配置快照解析 `executor_id=native`，只构造 Native run | 从其他 bot/config 读取 executor；静默选择其他实现 |
| 测试 Body | 仅测试环境通过同一 factory/coordinator 入口选择 Scripted Body，并返回同类结果材料 | 直接调用 Body 私有方法绕过 Head、coordinator 或输出桥 |
| 无效配置 | 未知 ID、缺少必需配置或能力不匹配明确失败并留下 execution diagnostics | 静默回退 Native、继续发送半成品结果 |

## 1. OLV 普通委派

| 场景 | 操作 | 必须观察到 | 禁止出现 |
| --- | --- | --- | --- |
| 普通委派成功 | 发送一个必定进入 Core 并调用工具的任务 | Personal 快速表达一次；Core 依次出现 `submitted/working/progress/completed`；最终文本、音频和 effect 各自只交付一次 | Core 直接调用平台发送；Personal/Core 重复最终回复；同一 `message_id` 多次完成 |
| 执行中追问 | Core 工作期间发送补充信息 | `provide_input` 带原 `execution_id + turn_id`，由当前 Body 接收；任务继续或明确进入 `input_required` 策略 | 新建第二个 execution；追问污染下一轮 Prompt；绕过 Head 直接写 Runner mailbox |
| 用户取消 | 执行中取消 | 只有一个 `cancel` 命令和一个 `cancelled` terminal event；Body 收到一次 stop | 取消后又出现 completed；重复取消再次发送可见消息 |
| deadline | 让任务自然超时 | 记录 `CoreCommandOrigin.CORE_HEAD`；Core 为 `cancelled` 或明确失败；Personal 可以单独完成自己的 turn | deadline 被记成用户主动取消；迟到结果覆盖终态 |

## 2. Cron / 主动任务

| 场景 | 操作 | 必须观察到 | 禁止出现 |
| --- | --- | --- | --- |
| 一次性提醒成功 | 创建短延迟任务 | Cron 有唯一 `execution_id`；Personal 统一表达；文本/音频完成；任务状态成功 | Cron 自己直发第二条；音频缺失但状态成功 |
| 任务失败 | 使用会失败的工具或无效配置 | Core `failed` 与 Cron `last_error` 对应；失败只保留一次历史/诊断 | 失败被标为 completed；重复重试造成重复表达 |
| 无目标/不支持 | 目标平台不支持主动消息 | 明确 rejected/unsupported 记录；不伪造 delivered | 仍写成功 receipt 或历史 |

## 3. Live

| 场景 | 操作 | 必须观察到 | 禁止出现 |
| --- | --- | --- | --- |
| 正常 Live | 发起一轮 Live 对话 | Native loop 与 Live TTS bridge 各自只消费一份流；成功历史只写一次；最终完成在最后输出后发生 | Live 分支和公共尾部重复写历史；重复 synth finished |
| Live 取消 | 中途停止 | 取消事实和 TTS 清理可追踪；不生成成功终态 | 取消后补写成功历史 |

## 4. 日志核对字段

优先搜索这些诊断事件：

- `core_execution_event`：检查序列、`execution_id`、`turn_id`、`executor_id`、事件类型。
- `core_execution_ledger_settled`：检查终态只结算一次，`inserted/deduplicated` 合理。
- `output_delivery_receipt`：检查逻辑段和物理消息的数量、成功/失败组件。
- `complete_visible_message` / `control.synth_finished`：检查逻辑消息和整轮完成顺序。
- `plugin.delayed_delivery`：检查迟到 artifact 的 `parent_turn_id`、`delayed_turn_id` 和 disposition。

## 5. 通过门槛

- OLV、Cron、Live 各至少完成一次成功和一次失败或取消。
- 每个场景都能从 `turn_id -> execution_id -> executor_id -> message_id` 还原链路。
- 没有重复最终回复、重复历史、重复 TTS 或迟到结果覆盖终态。
- Native 与测试 Scripted Body 的协议级结果已一致；真实平台只负责验证装配和输出边界。
- 任一项失败时，只记录最小复现日志和所属 owner，不在验收过程中临时增加旁路。
