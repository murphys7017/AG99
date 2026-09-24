# 配置字段审计清单

状态：第一轮审计进行中；已收敛旧读取名，并按运行时 owner 清理明确废弃字段。

本文记录配置清理的判定边界。目标不是单纯减少 JSON 键数量，而是让每个字段
拥有唯一的语义 owner、作用域和运行时读取入口。

## 判定规则

1. 配置字段必须归属于 `GlobalRuntime`、`ModelProviderRegistry`、`AdapterRegistry`、
   `BotProfile` 或 `ConfigRouteTable` 之一。
2. 持久化字段不是回合状态。回合内生效值只能从 `RuntimeSelection` 冻结为
   `TurnConfigSnapshot`，不能由下游再次按 UMO 解析。
3. 仅凭字段名或默认配置是否出现，不能判断字段已废弃。还必须检查 Python 调用者、
   Dashboard 元数据、迁移代码、公开插件文档和动态配置读取。
4. 删除字段前必须先删除所有运行时读取和展示入口；迁移读取器可以在确认不再支持
   旧存量后单独退场。

## 已确认项目

| 字段 | 当前判断 | 处理建议 |
| --- | --- | --- |
| `provider_settings.streaming_response` | Core 普通路径和 Provider Runner 的规范字段 | 保留为唯一流式配置 |
| `provider_settings.stream` | 后台任务唤醒路径的旧读取名；不在默认配置和配置元数据中 | 已改为读取 `streaming_response`，不得重新加入 schema |
| `provider_settings.streaming_segmented` | 已退场的“非流式平台分段回复”布尔字段；当前策略是两值枚举 | 已删除；加载旧 Profile 时直接删除。统一使用 `unsupported_streaming_strategy` 的 `realtime_segmenting` 或 `turn_off`。 |
| `provider_settings.llm_compress_keep_recent` | 已废弃的“按轮数保留上下文”兼容控制；与 token 比例控制表达同一策略 | 已删除默认值、schema、Dashboard 与运行时透传；加载旧配置时直接删除，只保留 `llm_compress_keep_recent_ratio`。 |
| `provider_settings.agent_runner_type` 与 `*_agent_runner_provider_id` | 已移除的混合字段；过去同时表达普通聊天 Runner 与 Core Body | 启动时一次性迁移后删除。普通聊天 Runner 使用 `agent_runner.mode/provider_id`；委派 Core 使用 `core_execution.executor_id/codex_cli.provider_id`。 |
| `provider_ltm_settings.active_reply.method` | 只有 `possibility_reply` 一个允许值的冗余选择器 | 已删除默认值、schema、Dashboard 与运行时判断；加载旧 Profile 时直接删除，只保留启用开关、候选抽样概率和白名单。 |
| `provider_settings.safety_mode_strategy` | 只有 `system_prompt` 一个允许值的冗余选择器 | 已删除默认值、Dashboard 与运行时分支；安全模式仍由 `llm_safety_mode` 单一开关控制，加载旧 Profile 时直接删除。 |
| `provider_settings.file_extract.provider` | 当前仅有 Moonshot 文件解析实现；写入其他值只会被运行时拒绝 | 已删除 provider selector；文件解析由启用开关和 Moonshot API Key 明确控制，加载旧 Profile 时直接删除。 |
| `provider_settings.provider_pool` | 没有运行时消费者；仅残留在默认值、Dashboard 选择器、Profile 投影与资源迁移改写 | 已删除；加载旧 Profile 时直接删除。通用 Dashboard `provider_pool` 控件仍可供插件自定义元数据使用。 |
| `provider_settings.persona_pool` | 没有运行时、Profile 投影或迁移消费者 | 已删除；加载旧 Profile 时直接删除。通用 Dashboard `persona_pool` 控件仍可供插件自定义元数据使用。 |
| `provider_settings.web_search_link` | 从未被搜索 Prompt 或渲染链读取；WebChat 引用由平台和实际挂载的搜索工具自动决定 | 已删除；加载旧 Profile 时直接删除。不影响真实的 WebChat citation Prompt 或 ChatUI 引用展示。 |
| `provider_settings.identifier`、`group_name_display`、`datetime_system_prompt` | 旧 Prompt 选项；新的 `SessionCollector` 已无条件基于真实事件生成用户、群组和时间槽，运行时没有读取这些开关 | 已删除；加载旧 Profile 时直接删除。用户、群组和时间上下文以当前事件事实为准，不再存在无法实际控制的展示开关。 |
| `provider_settings.image_caption_provider_id` | 旧的普通图片转述 Provider 字段；当前普通输入只读取 `default_image_caption_provider_id` | 已删除；加载旧 Profile 时直接删除，不影响仍由 `provider_ltm_settings.image_caption_provider_id` 负责的群聊上下文预转述。 |
| `provider_settings.default_image_caption_provider_id` | 普通输入图片转述的按需降级 Provider | 与群聊长期上下文图片转述不是同一开关，暂不删除 |
| 顶层 `default_personality` | 旧的根级人格默认值投影；运行时、默认配置、Dashboard 和 typed Profile 均只读取 `provider_settings.default_personality` | 已删除；加载旧 Profile 时直接删除，默认人格只保留 `provider_settings.default_personality` 一个持久化来源。 |
| 顶层 `log_file` | 未出现在默认配置、Schema 或 Dashboard 的旧嵌套日志对象；仅由 `LogManager` 的兼容分支读取 | 已删除；日志与 Trace 只保留顶层 `log_file_*`、`trace_log_*` 字段，加载旧 Profile 时直接删除该对象。 |
| `interaction_middleware` 中旧 `router_*`、`decision_*`、`finalizer_*`、旧 observer 开关和 `plugin_runtime_targets`/`plugin_tool_targets` | 多个本地 Profile 残留的旧编排与插件目标投影；全仓运行时引用为零，当前实现使用 `personal_policy_*`、`expression_*`、`planner_*` 和 `plugin_capability_targets` | 已删除；加载旧 Profile 时直接删除这些字段，不保留旧双路径配置。 |
| `provider_settings.request_max_retries` | 本地 Profile 残留字段；Provider 请求重试由各 Provider/调用路径自行管理，核心源码无该配置读取 | 已删除；加载旧 Profile 时直接删除。 |
| `dashboard.trust_proxy_headers`、`auth_rate_limit`、`totp` | 本地 Profile 残留的未实现 Dashboard 安全实验字段；当前 Dashboard 无读取者 | 已删除；加载旧 Profile 时直接删除。 |
| `provider_ltm_settings.image_caption_provider_id` | 群聊长期上下文图片转述 Provider | 独立的 `group_context.image_caption` 角色，不能与普通图片转述混淆；群聊插件在已准入回合优先读取冻结的 Profile 快照。 |
| `provider_settings.image_caption_prompt` 与 `provider_ltm_settings.image_caption_prompt` | 前者是普通输入转述默认提示词，后者是群聊上下文提示词并可回退前者 | 保留两种角色与回退语义；群聊捕获、Prompt 注入和发送后游标推进统一消费同一回合快照，旧直连入口才读取实时配置。 |
| 顶层 `wake_prefix`、`provider_settings.wake_prefix`、`platform_settings.friend_message_needs_wake_prefix` | 分别涉及 Pipeline 唤醒、Provider 请求裁剪和私聊唤醒策略 | 三者保留。Provider 前缀相对机器人唤醒词的派生已收敛到 `resolve_provider_wake_prefix()`，供执行和 Prompt 共用，避免两侧裁剪语义分叉。 |
| `config_version` | 没有业务运行时读取者，但仍是公开配置格式标记，并被配置文档与格式测试固定 | 暂不删除；应在建立明确的配置迁移/版本策略后再决定退场，不能按普通无消费者字段处理。 |
| `platform_settings.path_mapping` | 文档曾标记为废弃，但当前仍由入站媒体物化、预处理和出站消息链投递读取 | 保留运行时字段；应移除文档中的“已废弃”表述，待替代路径映射机制落地后再迁移。 |
| `interaction_middleware.memory_window_size` 与 `persona_history_window_size` | 分别是基础 Interaction 上下文窗口和 Persona 连续历史窗口 | 不是重复字段，必须保持独立 |
| 顶层 `persona` | 仅服务于已退场的 v3 Persona 迁移；运行时 Persona 已由数据库管理 | 已删除默认值、文档和 v3 迁移读取器；加载旧配置时直接删除该键。开发期不再迁移 `data_v3.db` 中的 Persona JSON。 |
| 顶层 `default_kb_collection` | 已过时，且没有运行时、Dashboard 或迁移消费者 | 已从默认值、schema、中英文文档和本地开发配置删除 |

## 首批收敛结果

后台任务唤醒原先读取不存在的 `provider_settings.stream`，导致它与普通 Core
任务的流式配置分叉。现已统一读取 `provider_settings.streaming_response`，并将
单元测试改为验证规范字段。

`default_kb_collection` 已从配置模型移除。知识库选择由 `kb_names`、会话配置和
知识库自身的 Provider 绑定承担，不再保留无消费者的单一默认集合字段。

内置插件中未被加载的旧 `long_term_memory.py` 已删除。当前群聊上下文的唯一实现是
`GroupChatContext`：它同时服务 Prompt Extension、外部 Agent Runner 请求装饰和
发送后游标推进，不再存在第二条旧的内存记录/图片转述链路。

## 下一批范围

1. 从 `DEFAULT_CONFIG`、配置元数据、Dashboard、迁移代码、文档和 Python 动态读取
   生成完整字段矩阵。
2. 已完成 `agent_runner` 与 `core_execution` 的物理拆分，以及旧的上下文压缩轮数控制删除；下一批处理图片转述角色和唤醒前缀，再决定其余物理
   JSON 重排。
3. 对明确废弃且无运行时消费者的字段，删除默认值、schema、Dashboard 展示和文档；
   删除前保留一次启动配置未知字段检查，避免静默吞掉仍在使用的字段。
4. 每组字段独立提交并验证双 Profile、Cron、普通消息、Personal 和外部执行器路径。

## 非目标

- 本轮不删除公开插件配置接口或动态插件自定义字段。
- 本轮不把语义不同的 Provider 角色强行合并成一个 `provider_id`。
- 本轮不改变 `RuntimeSelection` 和 `TurnConfigSnapshot` 的 owner 设计。
