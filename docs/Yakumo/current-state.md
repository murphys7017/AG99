# Yakumo Current State

当前仓库更接近单体式运行时。`main.py` 负责运行环境准备、WebUI 检查和启动入口，真正的系统装配发生在 `astrbot/core/initial_loader.py` 和 `astrbot/core/core_lifecycle.py`。

## 启动链路

1. `main.py`
2. `astrbot/core/initial_loader.py`
3. `astrbot/core/core_lifecycle.py`
4. 初始化配置、数据库、Persona、Provider、平台适配器、知识库、Cron、SubAgent、PluginManager、Pipeline、Dashboard

## 当前主要模块

### 1. 运行时总装配

- `astrbot/core/core_lifecycle.py`
- `astrbot/core/initial_loader.py`

职责：

- 初始化基础组件
- 组装上下文
- 启动平台适配器
- 启动事件总线和流水线
- 启动 Dashboard

问题：

- 生命周期层掌握过多具体实现
- 运行时边界偏弱，后续拆服务时会牵一发而动全身

### 2. Agent 主体

- `astrbot/core/astr_main_agent.py`
- `astrbot/core/astr_agent_context.py`
- `astrbot/core/astr_agent_tool_exec.py`
- `astrbot/core/astr_agent_hooks.py`
- `astrbot/core/agent/*`
- `astrbot/core/prompt/*`

职责：

- 选择模型提供商
- 构造 `ProviderRequest`
- 注入人格、技能、知识库、工具、子代理委派工具
- 运行 tool loop
- 处理 sandbox/local runtime
- 处理主 Agent 输出
- 新的 prompt collect 层开始承担结构化上下文收集

问题：

- `astr_main_agent.py` 职责过载
- Agent 层直接感知 plugin context、persona、knowledge base、skills、cron、sandbox
- Agent 内核和 AstrBot 业务实现没有明确隔离
- 新的 `prompt` 模块已经完成 collect/select/render/apply 主链路，当前默认 `apply_visible` 会接管模型可见 `ProviderRequest` 字段；shadow/legacy 仍作为显式配置模式存在

### 2.5 Interaction Middleware

- `astrbot/core/interaction/*`
- `astrbot/core/voice/*`
- `astrbot/core/memory/postprocessor.py`
- `astrbot/core/postprocess/*`

职责：

- 在 adapter 与 core queue 之间维护 interaction turn state
- 在 core decision 之前处理入站媒体、STT、route decision 与 immediate reply
- 在 interaction turn 中接管 `event.send(...)` / `event.send_streaming(...)` 的语义输出
- 统一 finalizer、result contributor、TTS、t2i、stream observation、stream interjection、utterance ledger 与 finalized turn material
- 将 turn completion 收口为：middleware 产出 finalized material，postprocess consumers 再消费 material；当前 memory service 与 interaction conversation history 都在 `AFTER_TURN_COMPLETED` 阶段落地
- 对普通 core 非 interaction 事件保留原 pipeline STT/TTS 兼容路径

当前已完成：

- `InteractionTurnState`、`InteractionUtterance`、`InteractionStreamState` 已成为主状态模型
- prompt / result / stream 插件扩展点已收口到只读阶段视图
- SELF_REPLY / HYBRID / DELEGATE_TO_CORE 主链路已由 middleware 持有 turn owner 语义
- interaction outbound phase 已迁入 `InteractionOutputController`
- core 旧流程与 middleware 新流程共享 voice service
- interaction 内部主链路开发期 fail-fast，不依赖 fallback 证明正确性

当前仍需继续收口：

- 正式 output gateway 仍未替换当前 `event.send` / `event.send_streaming` interception 形态
- live audio 缺 provider / 文本降级 / completion diagnostics 仍需进一步统一
- 真实平台手动日志断点仍需补齐，尤其是 Record/Image/Text 投递形态与 ledger metadata 的一致性

### 3. 插件与工具整合层

- `astrbot/core/star/context.py`
- `astrbot/core/star/star_manager.py`
- `astrbot/core/star/register/star_handler.py`
- `astrbot/core/provider/register.py`

职责：

- 暴露插件 API
- 维护插件上下文
- 注册命令、事件处理器、工具
- 将插件工具写入全局 `llm_tools`

问题：

- `star.Context` 已经是“大一统上下文”
- 插件系统直接影响 Agent 可见工具集合
- 工具注册中心和插件系统耦合过深

### 4. 基础服务实现

- `astrbot/core/provider/manager.py`
- `astrbot/core/persona_mgr.py`
- `astrbot/core/conversation_mgr.py`
- `astrbot/core/db/*`
- `astrbot/core/platform/*`
- `astrbot/core/voice/*`

职责：

- 提供模型、STT、TTS、会话、数据库、消息平台能力
- `voice` 是共享 STT/TTS service port，core 旧流程与 interaction middleware 都通过它解析 provider、执行转写/合成与记录 diagnostics

问题：

- 这些模块当前是“实现 + 装配目标”混在一起
- 还没有被抽象成稳定的基础接口层

### 5. 能力扩展模块

- `astrbot/core/skills/skill_manager.py`
- `astrbot/core/subagent_orchestrator.py`
- `astrbot/core/tools/*`
- `astrbot/core/computer/*`
- `astrbot/core/knowledge_base/*`
- `astrbot/core/cron/*`

职责：

- 提供 Skills、SubAgent、工具执行、知识库、定时任务等能力

问题：

- 多数能力是直接注入主 Agent，而不是通过独立能力层接入
- 未来拆成多服务时，协议边界尚不清晰

## 当前关键耦合点

### 1. Agent 依赖插件上下文

`astrbot/core/astr_agent_context.py` 中的 `AstrAgentContext` 直接持有 `star.Context`。这意味着 Agent 运行时不是依赖抽象接口，而是依赖完整插件运行时。

### 2. 主 Agent 直接做所有能力注入

`astrbot/core/astr_main_agent.py` 目前统一处理：

- provider 选择
- conversation 获取
- persona 注入
- skills prompt 注入
- knowledge base 注入
- subagent handoff 工具注入
- cron 工具注入
- runtime 工具注入

这使它既是内核，又是平台层，又是能力装配层。

### 3. Tool Registry 不是独立层

全局 `llm_tools` 既被 Provider 层引用，也被 PluginManager、Star 注册器、主 Agent 工具组装逻辑引用。当前没有独立的 Tool Registry/Capability Registry 边界。

### 4. 生命周期层直接掌握所有实现

`astrbot/core/core_lifecycle.py` 负责实例化几乎所有核心组件。这在单体里简单，但会限制未来把 Agent、Plugin、Skill、SubAgent 拆成单独平台或服务。

## 适合拆分的边界

### 1. Agent Kernel

保留纯 Agent 运行能力：

- message model
- tool loop runner
- handoff protocol
- hooks
- response model
- context management

### 2. Agent Platform

主服务器负责：

- API 网关
- 主 Agent 编排
- provider/stt/tts/message/persona/database 的接口访问
- session/conversation 路由
- subagent 调度入口

### 3. Capability Platform

能力平台负责：

- tools
- plugins
- skills
- sandbox/browser/python/shell
- knowledge base
- cron

## 当前拆分判断

当前代码已经具备“可拆”前提，但还不具备“直接服务化”前提。

原因：

- 已经存在主 Agent、SubAgent、Skill、Plugin、Provider、Platform 等天然模块
- 但接口层不足，抽象还没从实现里分离出来
- 更适合先做模块化重构，再做多服务部署
