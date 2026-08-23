# Yakumo Module Notes

`docs/Yakumo/modules` 用于记录 Yakumo 当前运行时的模块职责、调用关系和重构关注点。代码包仍使用 `astrbot` 命名，但这些页面描述的是本仓库的运行时边界，不是上游 AstrBot 的原样说明。

在阅读这里之前，建议先看 `docs/Yakumo/README.md`。那里会先说明：

- 这些文档描述的是 Yakumo 当前实现，不是官方主线原样说明
- 哪些内容是现状，哪些内容是目标态或开发方案
- Yakumo 相对上游重点推进了哪些能力

## 文档列表

- `runtime.md`: 启动入口、生命周期、事件总线、流水线
- `agent.md`: 主 Agent、Agent 内核、Tool Loop、SubAgent
- `prompt.md`: Prompt/Context 收集、目标投影、Profile、Layout、Renderer、Apply 与插件扩展边界
- `interaction.md`: Interaction middleware、turn state、outbound materialization、voice/postprocess 边界
- `foundation.md`: Provider、Persona、Conversation、Platform、Database
- `capability.md`: Plugin、Tool、Skill、Knowledge Base、Cron、Computer Use
- `dashboard.md`: Dashboard 后端、路由、前端

## 阅读顺序

1. `runtime.md`
2. `foundation.md`
3. `agent.md`
4. `prompt.md`
5. `interaction.md`
6. `capability.md`
7. `dashboard.md`

## 当前判断

如果目标是推进 Yakumo 架构，最重要的不是先拆 Dashboard，而是先拆：

- runtime 和 platform 的装配边界
- agent 和 capability 的边界
- foundation 接口和具体实现的边界
