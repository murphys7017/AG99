# Capability Modules

## Plugin / Star

### `astrbot/core/star/context.py`

职责：

- 向插件暴露统一上下文 API
- 持有 provider、platform、conversation、persona、kb、cron、subagent 等对象
- 提供 `llm_generate()`、`tool_loop_agent()` 等调用入口
- 提供工具注册、Web API 注册等能力

说明：

- 当前这是一个“大一统上下文”
- 它连接了插件系统和核心运行时

重构关注点：

- 这是 Capability Platform 和 Agent Platform 的关键边界文件

### 插件运行目标

Interaction turn 中，插件的 LLM 生命周期和插件拥有的 LLM Tool 默认属于
`personal_expression`，适合人格、娱乐和关系增强。插件可在 `Star` 类或
`register_star(..., interaction_runtime_target=...)` 中声明 `core`；会话配置
`interaction_middleware.plugin_runtime_targets` 可覆盖该声明。只有最终解析为
`core` 的插件进入工作执行面，关键词、命令和其他 `AdapterMessageEvent`
Handler 仍由官方 Pipeline 负责。

`Context.tool_loop_agent(..., tool_execution_surface=...)` 把执行面显式传给
工具循环。Persona 面的旧式事件输出会变成模型可见的工具材料，Core 面保留官方
直接输出语义。

### `astrbot/core/star/star_manager.py`

职责：

- 加载、重载、卸载插件
- 处理插件依赖安装
- 扫描插件目录
- 管理插件生命周期

说明：

- 这是插件运行时管理器
- 也会影响工具和命令注册

### `astrbot/core/star/register/star_handler.py`

职责：

- 注册插件 handler
- 注册命令和事件处理器
- 注册函数工具和 agent handoff

说明：

- 这是插件声明式 API 和运行时注册表之间的桥

## Tool

### `astrbot/core/provider/register.py`

职责：

- 提供当前全局工具注册中心 `llm_tools`

说明：

- 当前它是一个全局状态点
- 插件、主 Agent、Provider 都会依赖它

重构关注点：

- 未来应升级为独立 `ToolRegistry` 或 `CapabilityRegistry`

## Skills

### `astrbot/core/skills/skill_manager.py`

职责：

- 管理 skills 配置和本地技能目录
- 读取 Skill 元信息
- 生成 Skills Prompt

说明：

- 当前 Skills 主要以 Prompt 形式注入主 Agent
- 还不是独立协议级能力

## Knowledge Base

### `astrbot/core/knowledge_base/*`

职责：

- 文档解析
- 文档切片
- 向量或稀疏检索
- 检索结果拼装

说明：

- 当前它是主 Agent 的一项能力模块
- 可以保留在 capability 层

## Cron

### `astrbot/core/cron/*`

职责：

- 定时任务管理
- 定时触发消息事件或主动 Agent 行为

说明：

- 这类模块天然适合作为 capability service

## Computer / Sandbox

### `astrbot/core/computer/*`

职责：

- 代码执行
- Shell
- Python
- Browser
- 文件系统
- sandbox runtime 访问

说明：

- 这是高风险、高资源消耗能力
- 非常适合从主 Agent 平台中分层出来

## 当前判断

Yakumo 架构下，这一层最适合作为“能力平台”演进目标。

它包含：

- 插件
- tools
- skills
- knowledge base
- cron
- sandbox/browser/python/shell

这些模块都可以继续保留在同一仓库里，但应通过统一边界接入主 Agent，而不是直接嵌入主 Agent 代码。
