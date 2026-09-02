# 文件职责与迁移映射

本文把当前文件映射到目标工作区，并给出每个文件的具体改造方式。路径以仓库根目录为基准。

## 1. 导航与路由

| 当前文件 | 当前职责 | 目标职责 | 做法 |
| --- | --- | --- | --- |
| `dashboard/src/layouts/full/vertical-sidebar/sidebarItem.ts` | 平铺侧边栏，包含“更多功能” | 稳定的工作区导航 | 将顶层项改为 Persona/Intelligence/Channels/Knowledge/Capabilities/Automation/Operations；旧页面先作为子项或重定向保留 |
| `dashboard/src/router/MainRoutes.ts` | 主路由注册 | 工作区路由与兼容路由 | 新增工作区入口；旧 `/config`、`/providers`、`/persona` 等保留并标记为 legacy/compat |
| `dashboard/src/router/routeConstants.mjs` | 路由常量 | 统一工作区、资源和配置路由名 | 增加 workspace/resource/section 常量，避免字符串散落 |
| `dashboard/src/layouts/full/vertical-sidebar/NavItem.vue` | 渲染导航项 | 支持工作区分组、徽标和权限状态 | 保持现有组件为基础，只增加分组展开、活动态和可选状态徽标 |
| `dashboard/src/composables/usePluginSidebarItems.ts` | 注入插件侧边栏项 | 向 Capabilities/Operations 注入扩展项 | 增加目标工作区字段；没有声明时进入 Capabilities 的“扩展”分组 |

## 2. Dashboard 工作区壳层

| 当前/未来文件 | 责任 | 具体实现 |
| --- | --- | --- |
| `dashboard/src/views/DashboardWorkspace.vue`（新增） | 通用工作区容器 | 提供标题、面包屑、工作区导航、内容插槽、保存状态和权限上下文 |
| `dashboard/src/components/workspace/WorkspaceHeader.vue`（新增） | 工作区头部 | 显示当前资源/配置范围、搜索、刷新和帮助入口；不承载大段说明文本 |
| `dashboard/src/components/workspace/WorkspaceSectionNav.vue`（新增） | 工作区内分区导航 | 根据 metadata registry 生成 section tabs/side nav，支持深链接 |
| `dashboard/src/components/workspace/WorkspaceEmptyState.vue`（新增） | 空状态 | 用于未配置 Provider、未创建 Persona、没有知识库等可操作空状态 |
| `dashboard/src/components/workspace/workspaceRegistry.ts`（新增） | 工作区注册表 | 定义 key、路由、图标、权限、顺序和默认落点；不保存业务数据 |
| `dashboard/src/components/workspace/useWorkspaceRoute.ts`（新增） | 路由解析 | 统一 workspace/resource/section 查询参数和兼容重定向 |

## 3. 配置页面与渲染器

| 当前文件 | 目标变化 |
| --- | --- |
| `dashboard/src/views/ConfigPage.vue` | 收缩为兼容壳层：保留配置选择、未保存确认、保存和高级 JSON 编辑；把 section 渲染委托给新的工作区组件 |
| `dashboard/src/components/config/AstrBotCoreConfigWrapper.vue` | 从“遍历所有 group”改为接收经过 registry 投影的 sections；支持 workspace/section/scope 过滤和高级项折叠 |
| `dashboard/src/components/shared/ConfigItemRenderer.vue` | 增加 `scope`、`advanced`、`restartRequired`、`dangerous` 的统一呈现；敏感值默认 masked |
| `dashboard/src/components/shared/AstrBotConfigV4.vue` | 保留为旧 schema 兼容渲染器；新页面不得再直接依赖其内部 group 顺序 |
| `dashboard/src/components/shared/AstrBotConfig.vue` | 保留旧配置格式兼容；迁移完成前不删除 |
| `dashboard/src/composables/useConfigTextResolver.js` | 从旧 group/section key 解析文本，增加 workspace/section 新 key 的兼容回退 |
| `dashboard/src/components/config/UnsavedChangesConfirmDialog.vue` | 复用到工作区级表单，增加“保存并离开/放弃/继续编辑”三种明确动作 |
| `dashboard/src/components/shared/WaitingForRestart.vue` | 由工作区保存流程统一触发；显示受影响配置项和重启原因 |

## 4. 元数据与后端 API

| 文件 | 具体做法 |
| --- | --- |
| `astrbot/core/config/default.py` | 在现有 metadata 上逐步补充 `workspace`、`section`、`scope`、`advanced`、`restartRequired`、`dangerous`、`order`；保留旧 group |
| `astrbot/core/config/i18n_utils.py` | 支持新字段的 i18n key 生成；旧 `group.section.field` key 继续有效 |
| `astrbot/dashboard/routes/config.py` | 返回 normalized metadata registry；保留旧 metadata 形状，增加版本号、能力声明和配置范围信息 |
| `dashboard/src/i18n/locales/zh-CN/features/config-metadata.json` | 将用户可见标题从历史模块名迁移到工作区/section 语义；旧 key 暂不删除 |
| `dashboard/src/i18n/locales/en-US/features/config-metadata.json` | 与中文保持同一 key 集合，避免新工作区出现语言缺口 |
| `dashboard/src/i18n/locales/*/core/navigation.json` | 增加工作区、资源和兼容入口的导航文本 |
| `dashboard/src/types/config.ts`（新增） | 定义 `ConfigWorkspace`、`ConfigSection`、`ConfigScope`、`ConfigFieldMeta`、`ConfigRegistry` |
| `dashboard/src/composables/useConfigRegistry.ts`（新增） | 获取、缓存、筛选和投影 registry；处理旧 metadata 归一化 |

## 5. 各工作区页面

| 工作区 | 首批复用页面/文件 | 后续拆分 |
| --- | --- | --- |
| Persona | `dashboard/src/views/PersonaPage.vue`、`dashboard/src/views/persona/*`、`personaStore.ts` | Persona 定义、表达规则、关系状态、主动策略分成 sections |
| Intelligence | `ProviderPage.vue`、`components/provider/*`、`components/chat/ProviderConfigDialog.vue`、`ConfigPage.vue` | Provider 资源、Execution Profile、上下文/Agent Runner、模型能力 |
| Channels | `PlatformPage.vue`、`components/platform/*`、平台 metadata 注入逻辑 | 平台连接、账号、群聊行为、消息入口和权限 |
| Knowledge | `views/knowledge-base/*`、`views/alkaid/LongTermMemory.vue`、`KnowledgeBaseSelector.vue` | 知识库、文档、Embedding/Rerank、记忆策略 |
| Capabilities | `ExtensionPage.vue`、`views/extension/*`、`components/extension/*` | 插件、Skills、MCP、工具授权、电脑使用和 Shell |
| Automation | `CronJobPage.vue`、`SubAgentPage.vue`、`SessionManagementPage.vue` | 定时任务、主动任务、子 Agent、自动化会话 |
| Operations | `ConversationPage.vue`、`ConsolePage.vue`、`TracePage.vue`、`Settings.vue`、备份/更新组件 | 会话数据、日志追踪、备份恢复、更新、安全和系统设置 |

## 6. 文件拆分原则

- 页面组件只负责布局和用户流程，不直接遍历后端原始 metadata。
- registry/composable 负责归一化和筛选，renderer 负责控件呈现。
- 工作区不复制配置数据；所有保存仍走现有配置 API。
- 新文件先以适配层形式接入，待旧入口流量归零后再删除旧实现。

