# 配置字段审计清单

状态：第一轮审计进行中；已收敛一个旧读取名并删除一个无消费者的废弃字段。

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
| `provider_settings.llm_compress_keep_recent` | 已废弃的“按轮数保留上下文”兼容控制；与 token 比例控制表达同一策略 | 已删除默认值、schema、Dashboard 与运行时透传；加载旧配置时直接删除，只保留 `llm_compress_keep_recent_ratio`。 |
| `provider_settings.agent_runner_type` 与 `*_agent_runner_provider_id` | 已移除的混合字段；过去同时表达普通聊天 Runner 与 Core Body | 启动时一次性迁移后删除。普通聊天 Runner 使用 `agent_runner.mode/provider_id`；委派 Core 使用 `core_execution.executor_id/codex_cli.provider_id`。 |
| `provider_ltm_settings.active_reply.method` | 只有 `possibility_reply` 一个允许值的冗余选择器 | 已删除默认值、schema、Dashboard 与运行时判断；加载旧 Profile 时直接删除，只保留启用开关、候选抽样概率和白名单。 |
| `provider_settings.safety_mode_strategy` | 只有 `system_prompt` 一个允许值的冗余选择器 | 已删除默认值、Dashboard 与运行时分支；安全模式仍由 `llm_safety_mode` 单一开关控制，加载旧 Profile 时直接删除。 |
| `provider_settings.file_extract.provider` | 当前仅有 Moonshot 文件解析实现；写入其他值只会被运行时拒绝 | 已删除 provider selector；文件解析由启用开关和 Moonshot API Key 明确控制，加载旧 Profile 时直接删除。 |
| `provider_settings.default_image_caption_provider_id` | 普通输入图片转述的按需降级 Provider | 与群聊长期上下文图片转述不是同一开关，暂不删除 |
| `provider_ltm_settings.image_caption_provider_id` | 群聊长期上下文图片转述 Provider | 应在 typed Profile 中明确为 `group_context.image_caption` 角色，避免与普通图片转述混淆 |
| `provider_settings.image_caption_prompt` 与 `provider_ltm_settings.image_caption_prompt` | 前者是普通输入转述默认提示词，后者是群聊上下文提示词并可回退前者 | 先收敛命名和文档，不直接合并 |
| 顶层 `wake_prefix`、`provider_settings.wake_prefix`、`platform_settings.friend_message_needs_wake_prefix` | 分别涉及 Pipeline 唤醒、Provider 请求裁剪和私聊唤醒策略 | 语义不同但命名相近，先保留，后续通过 typed view 改名，不做机械删除 |
| `interaction_middleware.memory_window_size` 与 `persona_history_window_size` | 分别是基础 Interaction 上下文窗口和 Persona 连续历史窗口 | 不是重复字段，必须保持独立 |
| 顶层 `persona` | v3 Persona 迁移输入，运行时 Persona 已由数据库管理 | 从新配置展示和默认模板中移除前，保留迁移读取器并确认无外部依赖 |
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
