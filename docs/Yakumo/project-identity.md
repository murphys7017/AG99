# AG99 项目身份

AG99 是这个仓库当前对外使用的项目名称，由作者 YakumoAki 创建并基于 AstrBot 独立演进而来。它是一个以持续人格、低延迟表达和多平台会话为核心的对话 Runtime，目标不是把每条消息简单地交给一个 Agent，而是让一个 Persona 在多个 turn、会话和受控观察事件之间持续运行。

Yakumo 是作者名，完整名称为 YakumoAki。`docs/Yakumo` 路径和相关模块名称会继续保留，作为作者的架构文档命名空间，并避免破坏已有链接和内部设计记录。

## 一句话定位

> AG99 是一个 Persona-first、持续运行的多平台对话 Runtime，兼容 AstrBot 的平台适配器、Provider、插件和 CLI 基础设施。

## AG99 与 AstrBot 的关系

本仓库源自 AstrBot，并继续保留以下兼容边界：

- Python 包和导入路径仍使用 `astrbot`。
- CLI 入口仍是 `astrbot`，插件前缀仍是 `astrbot_plugin_`。
- 平台 Adapter、Provider、Pipeline、插件 API、Dashboard 和配置体系继续复用 AstrBot 基础设施。
- 与上游同步的兼容修复仍会记录在 [上游合并记录](./upstream-merge-ledger.md) 中。

但 AG99 已经不是只改变默认配置的 AstrBot 分支。当前仓库新增并持续维护自己的运行时边界：

- **Interaction Middleware**：在官方 EventBus / Pipeline 完成过滤、权限和 Handler 准入后统一维护 interaction turn。
- **Personal Runtime**：按人格、会话和隐私范围复用跨 turn 状态，管理主动观察、冷却、预算和连续对话 owner。
- **Router 与 Core Planner**：Router 只判断 `persona / hybrid / silent`，Core Planner 独立判断是否进入执行层。
- **Persona Expression**：所有用户可见自然语言统一经过同一个表达入口，Core 结果也回到该入口。
- **Structured Prompt**：通过 `collect → build → project → render → apply` 生成目标明确的 Prompt 视图。
- **Observation 链路**：后台事实经过 `Observation → Gate → Policy → ActionIntent → Persona → Output`，不会直接唤醒模型或发送消息。

## 核心流程

```text
Platform Adapter
  -> EventBus / Pipeline / Handler
  -> Interaction Middleware
  -> Personal Runtime + Router
  -> Core Planner
  -> Core Execution
  -> Persona Expression
  -> Output Runtime
  -> Conversation / Memory
```

普通消息可以先得到即时 Persona 表达；只有 Router 选择 `hybrid` 且 Core Planner 判断确有必要时，才进入 Core。Core 的结果不会绕过 Persona 直接发送。群聊候选才允许 Router 返回 `silent`，而且只会取消尚未取得发送权的表达。

## 术语边界

| 术语 | 含义 |
| --- | --- |
| Persona | 持续存在的交互主体及其表达规则 |
| Personal Runtime | 维护 Persona 状态、turn、观察和主动表达策略的运行时 |
| Core | 负责工具、知识库、Skills、SubAgent 等实质执行能力的执行层 |
| Prompt Extension | 向目标 Prompt 贡献结构化事实，不是 LLM Tool |
| Persona Effect | 结构化表现协议，不是 Agent Tool；具体 Motion/Live2D 语义由插件解释 |
| Observation | 受限、可过期的结构化事实，不是用户消息、Prompt 或最终文案 |

## 文档阅读顺序

1. [当前状态](./current-state.md)：代码已经实现的事实。
2. [模块索引](./modules/README.md)：Interaction、Prompt、Runtime 等职责边界。
3. [交互中间件](./modules/interaction.md)：一轮消息如何进入 Router、Persona 和 Core。
4. [结构化 Prompt](./modules/prompt.md)：事实收集、目标投影和 Provider 渲染。
5. `dev/`、`target-state.md`：明确标记为设计或后续计划的内容。

## 稳定性说明

AG99 仍处于持续开发和真实链路验证阶段。兼容命名不代表与上游实现保持行为等价；判断当前行为时，以源码和 `docs/Yakumo/current-state.md` 为准。项目继续遵守 `AGPL-3.0-or-later` 许可及适用的 AstrBot 兼容协议说明。
