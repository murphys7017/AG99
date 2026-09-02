# 分阶段迁移与验收

## Phase 0：基线与观测

范围：只做文档、路由盘点、配置路径盘点和截图/交互基线。

产物：

- 当前页面到配置路径的清单。
- 旧 `/config` 访问和保存行为记录。
- 关键用户流程：首次配置、添加 Provider、创建 Persona、启用平台、保存并重启。

验收：不改运行时行为；可以明确每个旧页面的保留期限。

## Phase 1：配置 Registry 归一化

范围：后端 metadata 增加新字段，前端新增类型和 composable。

实施：

1. 为每个现有 group/section 建立 `workspace` 和 `scope` 映射。
2. 增加 `advanced`、`restartRequired`、`dangerous`、`order`。
3. `useConfigRegistry` 将旧 metadata 归一化为新结构。
4. 旧 `getVisibleMetadata()` 改为调用 registry 投影，但暂不改变页面布局。

验收：新旧页面显示相同字段；保存 payload 不变；敏感字段不会被明文回显。

## Phase 2：工作区骨架与导航

范围：新增工作区路由、侧边栏分组和通用容器。

实施：

1. `sidebarItem.ts` 改为七个工作区顶层项。
2. 新工作区页面先嵌入现有页面或重定向。
3. 旧“更多功能”入口保留为兼容分组，并显示迁移后的新位置。
4. 对直接访问旧 URL 的请求做稳定重定向，不改变浏览器后退语义。

验收：所有旧链接可打开；新导航不出现孤儿页面；移动端和窄窗口下分组不重叠。

## Phase 3：Intelligence 与 Profile

范围：优先拆开 Provider 资源和执行 Profile。

实施：

1. Provider 页面只管理连接、凭据、模型和能力探测。
2. Profile 页面管理默认 Provider、回退模型、Agent Runner、上下文策略和工具预算。
3. Persona 只保留人格与表达配置，不再显示 Provider 细节。
4. 会话级覆盖在 Profile 页面以“作用域覆盖”呈现，不复制一份全局配置。

验收：修改 Provider 凭据不会误改 Persona；修改 Profile 不会新建 Provider；运行时请求仍能解析到同一 Provider/Profile。

## Phase 4：安全与运行控制入口

范围：吸收官方 Shell session、取消/超时、API Key 子权限、更新器校验等能力的前端入口。

实施：

- Capabilities 中增加 Shell/电脑能力授权和 session 状态。
- Operations 中增加权限、敏感配置、更新来源和校验状态。
- 所有危险操作显示影响范围、权限要求、重启要求和失败恢复入口。

验收：无权限用户看不到危险操作；取消、终止、超时状态可区分；敏感值只显示 masked 状态。

## Phase 5：Persona、Channels、Capabilities

范围：将已有页面迁移到稳定工作区，不改变领域逻辑。

实施：

- Persona：定义、表达、关系、主动策略分别作为 section。
- Channels：连接状态与平台行为分离；平台专属 metadata 仍由后端注入。
- Capabilities：插件、Skills、MCP、工具授权、电脑能力按能力类型分栏。

验收：常见任务不需要进入旧 `/config`；插件注入项能定位到所属工作区；配置路径和权限信息可追踪。

## Phase 6：Knowledge 与 Operations

范围：迁移知识库、记忆、会话、日志、追踪、备份、更新和系统设置。

实施：

- Knowledge 统一知识库、文档解析、Embedding/Rerank、记忆策略入口。
- Operations 统一数据管理和运维入口；高风险设置默认折叠。
- 保留旧页面 URL，通过 route alias 或重定向兼容书签。

验收：数据操作和运行配置不再混在同一页面；备份、恢复、更新流程有明确状态和回滚提示。

## Phase 7：旧入口收口

前置条件：

- 新工作区覆盖全部稳定配置。
- 旧入口访问量/内部引用已盘点。
- 插件和文档中的旧链接已有兼容跳转。

实施：

1. `/config` 默认打开工作区投影，JSON 编辑器移动到“高级模式”。
2. 删除硬编码 group 隐藏列表。
3. 旧 renderer 进入只读兼容阶段，最后再删除。

验收：配置 API、插件 API、导入导出和外部链接均无回归；可以通过 feature flag 回退到旧布局。

## 每阶段通用检查

- TypeScript/Vue 类型检查和构建。
- 配置读取、编辑、保存、重启提示的最小冒烟流程。
- 旧 URL、浏览器刷新、后退和未保存变更确认。
- 中英文 i18n key 完整性检查。
- 敏感配置 masked、权限和危险操作检查。
- 不修改用户已有的未提交后端改动。

