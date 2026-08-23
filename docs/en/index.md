---
layout: home

hero:
  name: AG99
  text: A continuously running, persona-first conversation runtime based on AstrBot
  tagline: AstrBot-compatible infrastructure, reorganized around fast expression, Core execution, and persistent interaction state
  actions:
    - theme: brand
      text: Quick Start
      link: /en/what-is-astrbot
    - theme: alt
      text: Project Identity
      link: /Yakumo/project-identity
    - theme: alt
      text: GitHub Repository
      link: https://github.com/murphys7017/AG99

features:
  - icon: 🧠
    title: Persistent Persona Runtime
    details: Reuses bounded state across turns and owns session leases, continuation windows, cooldowns, budgets, and observations.
  - icon: ⚡
    title: Fast Expression, Separate Core
    details: The Router selects persona, hybrid, or silent; Core Planner independently decides whether execution is needed, and Core results return through Persona Expression.
  - icon: 🧩
    title: Structured Prompt
    details: Canonical facts flow through collect, build, project, render, and apply without putting routing or tool execution inside the Prompt layer.
  - icon: 🔌
    title: AstrBot Compatibility
    details: Platform adapters, providers, plugin APIs, the dashboard, and CLI naming remain available through a documented compatibility boundary.
---
