---
outline: deep
---

# 什么是 AG99？

AG99 是这个仓库当前对外使用的项目名称，由 YakumoAki 创建并基于 AstrBot 独立演进。它是一个以持续人格、低延迟表达和多平台会话为核心的对话 Runtime：同一个 Persona 可以跨 turn 保留受控状态，并在需要时把实质任务交给 Core 执行层。

本页面路径继续使用 `what-is-astrbot`，是为了兼容已有书签和上游文档链接。代码包、CLI、插件前缀和部分配置项仍使用 `astrbot`，这属于兼容边界，不代表本项目仍然只是上游 AstrBot 的配置分支。完整关系见 [项目身份](/Yakumo/project-identity)。Yakumo 是作者名（YakumoAki），不是项目名。

## 核心流程

```text
平台适配器
  -> EventBus / Pipeline / Handler
  -> Interaction Middleware
  -> Personal Runtime + Router
  -> Core Planner
  -> Core 执行层
  -> Persona Expression
  -> Output Runtime
  -> Conversation / Memory
```

普通消息和未被 Handler 接管的有界群聊候选会进入 Interaction Middleware。Personal Runtime 负责本轮准入与会话状态，Router 只返回 `persona`、`hybrid` 或（仅群聊候选）`silent`：

- `persona`：不启动 Core，直接由 Persona Expression 生成可见表达。
- `hybrid`：由 Core Planner 独立判断是否需要执行；需要时 Core 负责工具、知识库、Skills 等实质工作。
- `silent`：取消仍处于 pending 的 Persona 输出，不撤回已经提交或送达的表达。

Core 的结果不会绕过 Persona 直接发送，而是回到同一个 Persona Expression。这样即时回复、插件人格输出和 Core 最终结果共享一致的表达与输出边界。

## 插件如何参与

- Pipeline Handler 继续拥有关键词、命令和协议事件的接管权。
- Prompt Extension 贡献目标明确的结构化事实，不是 LLM Tool。
- 可执行插件工具默认进入 Core，只有明确声明或用户配置授权后才进入 Persona。
- Persona Effect 是结构化表现协议，Motion、Live2D 等具体语义由插件解释。
- Runtime Sensor 只能提交受限、可过期的结构化观察事实，不能提交用户原文、Prompt、工具调用或最终文案。

## 文档导航

- [项目身份](/Yakumo/project-identity)
- [Yakumo 架构索引](/Yakumo/)
- [当前状态](/Yakumo/current-state)
- [部署指南](/deploy/astrbot/package)
- [连接消息平台](/platform/start)
- [连接模型服务](/providers/start)
- [插件开发](/dev/star/plugin-new)

## 当前状态

Yakumo 仍处于持续开发和真实链路验证阶段。判断运行时行为时，以源码和 [当前状态](/Yakumo/current-state) 为准；`dev/` 与 `target-state.md` 中标记为 plan/design 的内容不代表已经完成。

项目继续使用 `AGPL-3.0-or-later` 许可证，并遵守适用的 AstrBot 兼容说明，详见 [LICENSE](https://github.com/murphys7017/AG99/blob/codex/unify-prompt-context-pipeline/LICENSE) 和 [EULA](https://github.com/murphys7017/AG99/blob/codex/unify-prompt-context-pipeline/EULA.md)。
