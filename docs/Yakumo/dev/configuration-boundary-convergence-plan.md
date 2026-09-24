# 配置边界与语义收敛方案

状态：配置域模型正在收敛，运行时消费者尚未迁移。

## 1. 目标

解决配置来源分散、配置身份丢失后静默回退、Pipeline 使用默认配置决定运行形态、
以及同一回合被不同模块重新解析配置的问题。

本计划先解决配置模型，再迁移运行时读取。配置文件不能继续同时承担资源注册、
Bot 行为策略、适配器实例和会话路由四种语义。

## 1.1 正式配置域

运行时模型固定为以下五个域：

```text
GlobalRuntime
  进程级配置：数据库、日志、Dashboard、代理、时区

ModelProviderRegistry
  全局 Provider 资源：来源、实例定义、凭据、能力声明

AdapterRegistry
  全局平台适配器实例：平台类型、客户端身份、连接配置

BotProfile
  Profile 行为策略：模型角色、Prompt、Memory、插件、输出、执行器

ConfigRouteTable
  UMO -> BotProfile + AdapterBinding
```

Provider 定义和 Adapter 定义不属于 BotProfile。BotProfile 只保存对它们的引用
以及使用策略。

运行时每一轮再生成一个选择结果：

```text
UMO
  -> ConfigRouteTable
  -> RuntimeSelection(profile_id, adapter_binding_id, provider references)
  -> TurnConfigSnapshot
  -> Personal / Prompt / Core / Output / Plugin admission views
```

### 1.2 持久化与运行时的关系

第一阶段允许继续使用现有 JSON 文件，但必须把它们投影为上述域。不能因为
存储格式暂时不变，就继续让业务模块直接读取完整配置树。

### 1.3 配置域的边界

1. **ModelProviderRegistry** 只回答“有哪些 Provider 资源可用”。
2. **BotProfile** 只回答“这个 Bot 如何使用资源”。
3. **AdapterRegistry/AdapterBinding** 只回答“消息从哪个平台实例进入、向哪里输出”。
4. **ConfigRouteTable** 只回答“这个 UMO 使用哪个 Profile 和适配器绑定”。
5. **TurnConfigSnapshot** 只回答“本轮已经选择了什么”，不重新路由。

## 2. 当前配置投影的审阅结论

当前 `astrbot.core.config.domains` 的投影可以保留为过渡骨架，但不能直接作为最终
运行时模型：

- `ModelProviderRegistry` 不能长期嵌套在每个配置文件的 `ConfigurationDomains` 中，
  应提升为全局注册表。
- 旧版 `BotProfileConfig.adapter_policy` 不能同时承载平台实例详情；当前应以独立的
  `AdapterBinding` 作为后续实现方向。
- TTS 的 Provider 引用和输出行为必须分开，不能在 `model_policy` 与 `output_policy`
  中重复保存同一份设置。
- Profile 需要覆盖图片描述、上下文压缩、联网能力和 Provider Pool 等模型角色策略。
- 必须新增 `RuntimeSelection`，把 UMO、Profile、Adapter 和 Provider 引用在一次选择中
  固定下来。
- 在引用校验完成前，不迁移 ProviderManager、PlatformManager 或 Personal/Core 消费者。

## 3. 目标模型

```text
GlobalRuntime
  -> ModelProviderRegistry
  -> AdapterRegistry
  -> ConfigRouteTable

ConfigRouteTable.resolve(umo)
  -> RuntimeSelection
  -> BotProfile + AdapterBinding + provider references
  -> TurnConfigSnapshot (frozen, per turn)
```

`TurnConfigSnapshot` 至少包含 `profile_id`、`adapter_binding_id`、Provider 角色引用、
`agent_runner_type`、`interaction_config`、`plugin_set`、当前回合 deadline、输出及能力
解析所需的配置投影。

下游模块只能接收自身需要的只读视图，不能反复传递或重新路由完整配置字典。

## 4. 原有层次映射

原先的四个层次仍然保留，但职责被重新定义：

1. **配置存储**：持久化 GlobalRuntime、Provider、Adapter、Profile 和路由数据。
2. **配置路由**：`UmopConfigRouter` 选择 Profile 和 AdapterBinding。
3. **运行时选择**：`RuntimeSelection` 固定本次路由结果。
4. **回合快照**：回合准入时冻结选择结果和窄类型配置视图。

当前已经新增了不改变存储格式的域投影：

- `ModelProviderRegistry`：过渡期 Provider 来源和 Provider 定义投影。
- `BotProfileConfig`：过渡期 Profile 策略投影。
- `ConfigurationDomains`：过渡期组合对象，后续应拆为全局资源注册表和 Profile。

它首先固定“Provider 是资源，Profile 是使用策略”的语义，但不代表运行时隔离已经完成。

## 5. 当前确定的问题

- `AgentRequestSubStage` 在初始化时按 Pipeline 全局配置选择 Internal/ThirdParty，不能反映每个 UMO 的配置。
- `StarRequestSubStage`、Agent wake prefix 和部分 Agent 参数在初始化时捕获默认配置。
- `AstrBotConfigManager.get_conf()` 对缺失路由目标静默回退 `default`。
- 配置元数据读取路径会原地删除 `umop` 字段。
- `_astrbot_config`、`_astrbot_config_id`、`InteractionTurnState.runtime_config_snapshot`、`MainAgentBuildConfig` 共同承载配置投影，缺少单一 owner。
- 多处 `config_id or "default"` 让“配置缺失”和“明确选择 default”无法区分。

## 6. 实施批次

### C0：配置域投影（进行中）

- 保留现有 JSON 配置格式，不迁移已有文件。
- [已完成] 新增 `astrbot.core.config.domains`，提供只读的 Provider/Profile 投影。
- [已完成] ProviderManager 和 PlatformManager 已切换到资源注册表读取；Prompt、Personal、Core 尚未迁移。
- [待完成] 修正域模型：Provider 注册表、AdapterBinding、BotProfile 和路由选择结果分离。
- [待完成] 合并重复的 TTS 语义，补齐图片描述、压缩、联网和 Provider Pool 等角色策略。
- [待完成] 为 Provider、Profile、Adapter 引用增加结构和唯一性校验。

### C1：建立全局资源注册表（完成，CRUD 收口待后续）

- [已完成] 从所有已加载配置中建立统一的 `ModelProviderRegistry` 投影。
- [已完成] Provider ID 重复且内容冲突时明确失败；相同定义可合并。
- [已完成] 建立 `AdapterRegistry` 与 `AdapterBinding`，明确平台实例身份和配置归属。
- [已完成] `AstrBotConfigManager.get_resource_registry()` 提供显式资源入口。
- [已完成] ProviderManager 的初始化/reload 资源读取切换到该注册表。
- [已完成] PlatformManager 的实例化路径切换到该注册表，并向全部 AdapterBinding owner 写回自动生成字段。
- [已完成] 启动期将旧 Profile 内嵌的 Provider source/provider 定义提升到
  `default` 全局资源 owner；相同定义去重，冲突 ID 按 Profile 命名空间化并只改写
  明确的 Provider 引用字段。
- [已完成] 旧 Profile 内嵌的 Adapter 定义迁移为 `adapter_binding_ids` 准入列表；
  实例定义只保留在 `default` 全局资源 owner。
- [待完成] ProviderManager 与 Dashboard 的资源 CRUD 明确暴露全局 owner，移除
  仍假定“当前 Profile 同时拥有资源和策略”的内部实现细节。

当前验证限制：静态检查、受控 Profile 资源迁移模拟和编译已通过；直接独立导入 ProviderManager 会触发仓库现有的
`PersonaManager -> astrbot.api -> KnowledgeBaseManager -> ProviderManager` 循环依赖，
因此本批未进行独立 ProviderManager smoke import，也未重启服务。

### C2：建立显式路由选择结果（基础 API 已完成）

- [已完成] `AstrBotConfigManager.resolve_configuration_selection()` 产出 Profile、AdapterBinding 和 `RuntimeSelection`。
- [已完成] 路由目标不存在时显式失败，不再静默回退 `default`。
- [已完成] 配置元数据读取返回副本，禁止 `pop()` 修改共享映射。
- [已完成] 只有没有专门 UMO 路由时，`default` 才是合法默认 Profile。
- [已完成] EventBus、Cron/主动任务和 Personal 非平台入口使用显式选择结果；
  后台工具完成后的合成任务继承原始回合快照，避免等待期间重新路由。

### C3：在回合入口冻结配置

- [已完成] EventBus 在调度 Pipeline 前完成显式选择并写入已有 `InteractionTurnState`。
- [已完成] `InteractionTurnState` 冻结 config、AdapterBinding、Provider 角色引用和运行配置快照；首写者冲突会明确失败。
- [已完成] `_astrbot_config` 与 `_astrbot_config_id` 保留为从 typed state 写出的兼容投影。
- [已完成] `PipelineScheduler` 不再以初始化 Profile 覆盖已冻结回合的兼容投影；
  仅无快照的旧直连调用保留默认写入。
- [已完成] 主动任务在合成 Cron Event 上冻结同一份 typed selection；
  Heartbeat、主动消息和 Runtime Observation 在提交前只解析一次配置身份。

### C4：按快照选择 Agent 路径

- [已完成] `AgentRequestSubStage.initialize()` 不再固定选择 Internal/ThirdParty；
  每一回合按冻结快照选择执行分支和 Provider wake prefix。
- [已完成] ThirdParty Agent Runner 的 runner 类型、Provider 配置、流式策略和
  persona 错误文案从当前回合快照读取，而非全局 `astrbot_config`。
- [已完成] WakingCheck 的 wake prefix、权限、群聊 continuation、Handler 准入和
  环境观测策略均从已冻结快照读取；unique-session 仍只改变运行时分组，不重新路由。
- [已完成] Star Request 删除未使用的 prompt prefix、identifier 初始化缓存，避免
  Pipeline 初始化继续捕获 Profile 行为配置。

### C5：收敛下游读取

- [已完成] Pipeline 输入准入的白名单、限流与环境会话观测从本轮冻结快照读取；
  不再因 Scheduler 初始化 Profile 影响不同 UMO 的入站策略。
- [已完成] 内容安全策略与预处理的 STT、路径映射、平台预响应从冻结快照读取；
  内容安全 selector 在每轮按已选 Profile 构造，不复用默认 Profile 的实例。
- [已完成] RespondStage 的流式降级策略和物理消息投递设置从冻结快照读取。
- [已完成] ResultDecorateStage 在普通输出回合按冻结快照构造只读装饰策略；
  回复前缀、TTS、分段、转图、转发及 @/引用回复不再由 Pipeline 初始化 Profile 固定。
- [已完成] ProcessStage 的 Core 启用判定、互动编排、Personal admission 与延迟插件交付
  使用冻结 Profile 的配置及身份，不再经由默认 Pipeline 配置或 compatibility extra 重取。
- [已完成] Prompt 的会话历史、记忆快照和人格关系 Collector，以及 Memory 的
  postprocess 服务选择，优先读取冻结快照。为避免 Prompt/Memory 反向导入
  Interaction 而产生启动循环，这些非 Interaction 边界经惰性投影读取；只有没有
  typed turn state 的旧直连入口才回退到兼容 Event extra。
- Core、Output、Memory、插件准入只读取快照或窄视图。
- 删除内部 `config_id or "default"` 回退；不删除公开 Event extra。

### C6：验收边界

- 两个 UMO 绑定不同 Agent Runner、Provider、插件集合和输出设置。
- 路由目标配置被删除时明确失败。
- 并发回合不读取彼此配置。
- Cron、主动任务、普通消息保持同一配置身份。
- 配置重载不改变已准入回合快照。

## 6.1 旧配置资源迁移规则

2026 年 9 月 24 日的本地配置审计发现同一 `ollama` source 的
`ollama_disable_thinking` 在不同文件中不一致，另有 TTS、Embedding 与 MiniMax
资源定义差异。启动期迁移现在将这些冲突的 Profile-local 资源命名空间化，
并将 Profile 仅保留为对该新全局资源的显式引用；不会静默选择任意一个副本。
迁移完成后 Profile 不再拥有资源定义，后续资源 CRUD 也必须遵循这个 owner。

## 7. 非目标

- 不重写 Personal/Core。
- 不修改 Persona、Effect、TTS 或工具协议。
- 不删除公开插件 API 或 Event 兼容入口。
- 不增加新的全局 Context，不通过自动回退掩盖错误。

## 8. 完成标准

一次回合只有一个明确配置身份；核心消费者从同一快照读取；配置缺失显式失败；
Pipeline 初始化不再决定其他配置文件的运行形态。
