# 旧代码退休清单

日期：2026-09-23

本文记录迁移期间发现的旧入口、兼容别名和重复状态路径。清单的目的不是立即删除代码，而是为每一项补齐 owner、保留理由、删除条件和验证方式，避免把仍属于公开插件 API 的入口误判为死代码。

## 已部分处理

### `Context._register_tasks` 类级共享状态

- **位置**：`astrbot/core/star/context.py`
- **问题**：旧实现把 `_register_tasks` 定义为类属性，多个 `Context` 实例或 reload 生命周期可能共享任务。
- **处理**：改为实例属性，解决多个 Context 实例之间共享列表的问题；启动时不再依赖 `task.__name__`。
- **范围**：不改变 `register_task()` 的公开兼容入口。
- **未完成**：已启动任务仍统一存放在 Core 生命周期的 `curr_tasks`，没有按插件 owner 回收；插件 reload 后的任务清理仍需单独处理。
- **验证**：静态检查、最小导入检查；真实插件 reload 仍需运行时验收。

### `Context.registered_web_apis` 类级共享状态

- **位置**：`astrbot/core/star/context.py`
- **问题**：Web API 注册表原来是类属性，可能跨 Context 实例和 reload 生命周期共享。
- **处理**：改为 Context 实例属性；Dashboard 继续从当前 `star_context` 读取。
- **未完成**：Web API 尚未记录 owner，插件卸载时不会按插件自动撤销路由。
- **验证**：静态检查、Dashboard 调用点检查；插件 reload 后路由回收仍需运行时验收。

## 低风险候选

### `astrbot/core/provider/entites.py`

- **类型**：拼写错误兼容转发模块。
- **当前证据**：仓库内未发现生产调用者。
- **删除条件**：确认 `data` 目录插件、已安装插件和公开文档没有导入 `provider.entites`。
- **验证**：全仓搜索、插件目录搜索、启动导入检查。

### 仓库内部及公开兼容边界的 `MessageSesion` 使用

- **类型**：`MessageSession` 的历史拼写别名。
- **当前证据**：仓库内部的适配器、Dashboard、Context 和迁移脚本已迁移到 `MessageSession`；兼容别名仍存在于平台边界。
- **处理策略**：继续保留 `MessageSesion = MessageSession` 供外部插件使用；完成公开 API 使用确认和弃用周期后再删除。

## 暂不删除

### `run_agent()` / `run_live_agent()`

可能仍是第三方插件公开入口。需要外部使用证据和替代 API 后再弃用。

### Event 输出兼容镜像

包括原始发送方法、`extra` 镜像和旧完成回调。它们仍服务于插件分支事件和平台兼容路径，必须先完成 typed output intent 收敛。

### Native 执行兼容层

`execution.py`、`astr_agent_run_util.py` 和无 `CoreExecutionHead` 的路径仍处于外部执行器迁移阶段。需完成普通交互、主动任务、取消、超时和跨配置验收后再删除。

## 高风险结构性清理

以下项目不能与低风险旧代码清理混做：

1. 收敛 `PipelineScheduler` 与 `PersonalRuntimeManager` 的回合所有权。
2. 收敛 `InteractionOutputController`、`PreOutputProcessor` 和 `TurnDeliveryCoordinator` 的输出职责。
3. 将 Event extra 中的核心状态迁移到 `InteractionTurnState` 和 Core typed state。
4. 统一配置身份，停止内部路径静默回退到 `default`。
5. 为进程级插件能力补齐 owner 与卸载治理，包括 `register_web_api`、
   `register_provider` 和已启动的 `register_task`。

每项都需要单独的设计变更、调用者审计和真实平台验收。
