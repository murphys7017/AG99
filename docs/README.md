# AstrBot Docs

这个目录大部分内容仍然来自上游 AstrBot 官方文档，用于保留插件开发、平台接入、部署和功能使用说明。

本 fork 自己的架构说明集中在：

- `docs/Yakumo/`
- `docs/Yakumo/README.md`
- `docs/Yakumo/current-state.md`
- `docs/Yakumo/modules/README.md`
- `docs/Yakumo/upstream-merge-ledger.md`

面向插件作者的 fork 扩展 API 已同步到普通中英文开发文档，而不只存在于 Yakumo 笔记：

- `docs/zh/dev/star/guides/persona-effects.md`
- `docs/en/dev/star/guides/persona-effects.md`
- `docs/zh/dev/star/guides/prompt-extensions.md`
- `docs/en/dev/star/guides/prompt-extensions.md`

Persona Effect 是 Persona 输出协议，不是 Agent Tool。Router 仍只返回固定分类词，不注册工具，也不接收 effect schema。

Prompt Extension 用于在统一 Prompt 管线中贡献模型可见事实。它不是 LLM Tool。Interaction turn 中，插件的 `on_llm_request` 默认在 Persona Expression 的预工具请求上运行一次，其非工具修改会保留到最终人格表达；`plugin_runtime_targets` 与 `interaction_runtime_target` 只控制插件 LLM 生命周期。插件 LLM 工具独立解析且默认进入 Core；工具自己的 `tool_targets` 可以声明 Persona，用户也可通过 `plugin_tool_targets` 按插件或具体工具覆盖。Persona 工具中的旧式事件输出会作为工具材料交给最终人格表达，富媒体作为最终消息附件投递，而不是直接形成第二条可见回复。它们都不是 Router、Planner 或内部 Persona 工具调用的事实入口，跨目标事实仍应使用 Prompt Extension。

`docs/Yakumo` 下的 `dev/*`、`target-state.md` 和早期中文详解文档包含历史设计记录，可能落后于当前代码。判断本 fork 与上游差异时，优先看 `README.md`、`docs/Yakumo/current-state.md` 和 `docs/Yakumo/modules/*`。

如果需要查看上游官方文档，请访问：

- https://docs.astrbot.app/
- https://github.com/AstrBotDevs/AstrBot
