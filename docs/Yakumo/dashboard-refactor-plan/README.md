# AG99 Dashboard 与配置面板重构总纲

## 文档定位

- 适用项目：AG99（基于 AstrBot 的持续演进版本）
- 目标：在保留 Persona-first、Interaction Middleware、Core Planner/Execution 边界的前提下，重构前端 Dashboard 的信息架构与配置面板。
- 本文档只描述目标结构、文件职责和迁移顺序，不直接修改运行时行为。
- 计划目录：`docs/Yakumo/dashboard-refactor-plan/`

## 1. 总体目标

当前 Dashboard 的主要问题不是页面数量少，而是能力已经增长到无法继续依赖“一个配置页 + 更多功能”来承载：

1. 配置组按历史实现模块划分，用户很难判断某项配置属于人格、模型、平台还是运行时。
2. Provider、Profile、Persona、Session/Runtime State 的生命周期没有在界面上清楚分开。
3. `ConfigPage.vue` 仍承担配置选择、筛选、编辑、保存、代码模式和测试聊天等过多职责。
4. 侧边栏把会话、定时任务、子 Agent、控制台、追踪等工作流压进“更多功能”。
5. 官方的大规模 Dashboard 重构可以提供交互参考，但不能直接覆盖 AG99 的 Persona-first 领域边界。

重构后的 Dashboard 应让用户先按任务进入工作区，再在工作区内选择资源或配置范围：

```text
Persona       人格与表达
Intelligence  模型、Provider、Profile、上下文与 Agent 执行
Channels      平台、账号、群聊与消息入口
Knowledge     知识库、文档、检索与记忆
Capabilities  插件、Skills、MCP、工具和电脑能力
Automation    Cron、主动任务、子 Agent、会话自动化
Operations    会话数据、日志、追踪、备份、更新和系统设置
```

## 2. 设计原则

### 2.1 按用户任务分区，不按 Python 模块分区

`ai_group`、`provider_group`、`interaction_middleware_group` 等历史组可以继续作为兼容元数据，但不应直接成为最终导航。最终导航只暴露稳定的用户任务入口。

### 2.2 明确五类配置归属

```text
system    系统安装、认证、更新、日志和安全
platform  平台/账号/通道连接与平台行为
profile   模型执行配置、Provider 绑定、上下文与 Agent Runner
persona   人格定义、表达规则、关系和主动表达策略
session   会话级覆盖、项目级覆盖和临时运行状态
```

其中：

- Provider 是可复用的资源，不等同于 Profile。
- Profile 是一次模型/Agent 执行所需的配置集合。
- Persona 是身份、表达和关系语义，不承担模型路由或工具执行。
- Runtime State 是运行事实，不能为了界面方便塞进 Profile。

### 2.3 新旧入口并存迁移

- 旧 `/config` 保留为兼容入口和高级入口。
- 新工作区先通过同一套后端配置 API 读取数据，不立即迁移配置存储格式。
- 元数据增加 `workspace`、`section`、`scope` 等字段后，旧 `group` 继续保留一段过渡期。
- 每个工作区都必须能定位回原始配置路径，保证可诊断和可回滚。

## 3. 目标页面结构

```text
/dashboard/default              总览：运行状态、待处理事项、最近活动
/persona                        Persona 工作区
/intelligence                   Intelligence 工作区
/intelligence/providers         Provider 资源
/intelligence/profiles          执行 Profile
/channels                       Channels 工作区
/knowledge                      Knowledge 工作区
/capabilities                   Capabilities 工作区
/automation                     Automation 工作区
/operations                     Operations 工作区
/config                         兼容配置入口/高级 JSON 编辑器
```

页面不是一次性全部新建。第一阶段先完成配置归属模型与 registry 归一化，第二阶段再建立路由、导航和工作区壳层，具体功能通过已有页面嵌入或重定向，避免大规模并行重写。

## 4. 推荐吸收顺序

1. **配置归属模型与 metadata registry**：先定义每项配置“属于谁”。
2. **Dashboard 工作区骨架**：建立导航、路由和工作区布局。
3. **Intelligence / Execution Profile**：拆开 Provider 资源、模型能力和 Agent 执行配置。
4. **Shell、权限、取消、更新器安全**：优先吸收官方高风险运行能力的界面入口。
5. **Persona Workspace**：收拢 Persona、表达、关系和主动策略。
6. **Channels Workspace**：平台、账号、群聊行为和消息入口。
7. **Capabilities Workspace**：插件、Skills、MCP、工具和电脑使用能力。
8. **Knowledge Workspace**：知识库、文档解析、检索、Embedding/Rerank 和记忆。
9. **Operations / Data Workspace**：会话、日志、追踪、备份、更新、系统设置。
10. **GenUI、市场体验和零散平台修复**：等待输出协议与权限边界稳定后再进入。

顺序的核心依据是：先建立“配置属于谁”的模型，再建立页面位置，最后移动具体功能。这样不会出现前端已经分区、后端仍然由超级配置和隐式归属驱动的情况。

## 5. 非目标

- 不直接复制官方 Dashboard 的页面实现或路由命名。
- 不在本计划中重写 Persona Runtime、Interaction Middleware 或 Core Execution。
- 不一次性删除旧配置字段、旧 API 和旧页面。
- 不把所有高级选项都平铺到首页；高级项应通过稳定的“高级设置”入口访问。
- 不为了页面分区引入新的运行时状态副本。

## 6. 成功标准

- 用户能从工作区名称判断配置用途，不需要先理解内部模块名。
- Provider、Profile、Persona、Platform、Session 的边界在导航和表单中一致。
- 同一配置项只有一个权威编辑位置，旧入口只提供兼容访问。
- 旧配置文件、插件元数据和外部 API 继续可用。
- 新页面支持搜索、未保存变更提示、保存/重启提示、权限校验和高级模式。
- 每个阶段都能单独验证和回滚。
