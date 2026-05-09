# Yakumo Module Notes

`docs/Yakumo/modules` 用于记录当前这个分支里 AstrBot 核心模块的代码职责、调用关系和重构关注点。

在阅读这里之前，建议先看 `docs/Yakumo/README.md`。那里会先说明：

- 这些文档描述的是当前分支，不是官方主线原样说明
- 哪些内容是现状，哪些内容是目标态或开发方案
- 当前分支相对官方重点推进了哪些能力

## 文档列表

- `runtime.md`: 启动入口、生命周期、事件总线、流水线
- `agent.md`: 主 Agent、Agent 内核、Tool Loop、SubAgent
- `prompt.md`: Prompt/Context 构建机制、问题和目标结构
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
