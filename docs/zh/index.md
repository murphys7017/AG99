---
layout: home

hero:
  name: AG99
  text: 基于 AstrBot 的持续运行 Persona-first 对话 Runtime
  tagline: 在兼容 AstrBot 基础设施的同时，重新组织多平台交互、即时表达与 Core 执行
  actions:
    - theme: brand
      text: 快速开始
      link: /what-is-astrbot
    - theme: alt
      text: 项目身份
      link: /Yakumo/project-identity
    - theme: alt
      text: GitHub 仓库
      link: https://github.com/murphys7017/AG99

features:
  - icon: 🧠
    title: 持续人格 Runtime
    details: 跨 turn 复用有界状态，统一管理会话租约、连续对话、冷却、预算和主动观察。
  - icon: ⚡
    title: 即时表达与 Core 分离
    details: Router 只判断 persona、hybrid、silent；Core Planner 独立决定是否进入执行层，Core 结果仍回到 Persona Expression。
  - icon: 🧩
    title: 结构化 Prompt
    details: 通过 collect、build、project、render、apply 形成目标明确的模型上下文，不把路由和工具执行混进 Prompt 层。
  - icon: 🔌
    title: AstrBot 兼容基础设施
    details: 保留平台适配器、Provider、插件 API、Dashboard 和 CLI 命名，部署与开发指南继续提供兼容文档。
---
