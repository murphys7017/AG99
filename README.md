# AG99

> A persona-first, continuously running conversation runtime for multi-platform chats.

AG99 is an independent conversation runtime by YakumoAki, based on AstrBot. It keeps AstrBot's platform adapters, model providers, plugin APIs, dashboard, and CLI compatibility while adding a different interaction runtime: Personal Runtime, Router, Core Planner, structured Prompt, unified Persona Expression, Memory integration, and controlled proactive observation. The `docs/Yakumo` path is retained for the author's architecture notes and implementation records.

[简体中文](./README_zh.md) · [Project identity](./docs/Yakumo/project-identity.md) · [Architecture docs](./docs/Yakumo/) · [Issue tracker](https://github.com/murphys7017/AG99/issues)

## What AG99 Changes

AstrBot's compatible infrastructure remains the foundation, but the main interaction path is now organized around a persistent Persona:

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

- **Personal Runtime** keeps bounded state across turns and owns admission, session leases, follow-up windows, cooldowns, budgets, and proactive runtime observations.
- **Router** makes only the lightweight `persona / hybrid / silent` decision. `silent` is available to bounded group-chat candidates and only cancels pending output.
- **Core Planner** independently decides whether a hybrid turn needs the execution layer. It does not reuse Router's model decision or prompt.
- **Persona Expression** is the single visible-language boundary. Immediate replies, plugin persona output, and Core results use the same expression path.
- **Structured Prompt** collects canonical facts once, projects target-specific views, and renders provider requests without making routing decisions or executing tools.
- **Observation** follows `Observation -> Gate -> Policy -> ActionIntent -> Persona -> Output`; it cannot directly call Core, tools, or Output.

This is not a cosmetic configuration fork. AG99 is a separate runtime direction built on AstrBot code and plugin surfaces, while continuing to follow the upstream project's license and compatibility obligations.

## Compatibility Boundary

The following names intentionally remain stable:

- Python package and imports: `astrbot`
- CLI entry point: `astrbot`
- Plugin prefix: `astrbot_plugin_`
- Existing platform adapters, providers, Pipeline handlers, plugin APIs, and dashboard routes

Compatibility does not mean identical behavior. The current implementation and the Yakumo architecture documents are authoritative for this repository; upstream AstrBot documentation is retained as compatibility material for shared deployment, platform, and plugin concepts.

## Current Status

AG99 is under active development and real-path validation.

| Area | Status |
| --- | --- |
| Interaction Middleware | Main path implemented; edge cases continue to be verified |
| Personal Runtime | Cross-turn state, observation intake, Gate, Policy boundary, and delivery feedback implemented |
| Router / Core Planner | Separate responsibilities and bounded fail-closed behavior implemented |
| Persona Expression | Unified visible-reply path implemented; provider capability fallbacks remain bounded |
| Structured Prompt | Collect/build/project/render/apply pipeline implemented and still being split into stable modules |
| AstrBot compatibility | Platform, provider, plugin, and CLI compatibility maintained where explicitly documented |

Do not treat this repository as a drop-in stable replacement for upstream AstrBot without testing your own adapters and plugins.

## Quick Start

```bash
uv sync
uv run main.py
```

The default WebUI/API endpoint is `http://localhost:6185`. The optional dashboard development server can be started with:

```bash
cd dashboard
pnpm install
pnpm dev
```

## Documentation

- [Project identity](./docs/Yakumo/project-identity.md): name, positioning, compatibility, and terminology.
- [Yakumo architecture index](./docs/Yakumo/README.md): current boundaries and reading order.
- [Current state](./docs/Yakumo/current-state.md): implementation facts, not future design.
- [Interaction Middleware](./docs/Yakumo/modules/interaction.md): turn orchestration and output ownership.
- [Structured Prompt](./docs/Yakumo/modules/prompt.md): canonical facts and target projections.
- [Memory design](./docs/Yakumo/dev/memory/index.md): memory boundaries and progress.
- [Compatibility docs](./docs/): deployment, platform, provider, and plugin guides inherited or adapted from AstrBot.

The `dev/` and `target-state.md` documents describe plans or design exploration unless they explicitly say otherwise. When documentation and code disagree, follow the code and update the current-state record.

## License

AG99 continues to use the upstream project's `AGPL-3.0-or-later` license and retains the applicable AstrBot compatibility notices. See [LICENSE](./LICENSE) and [EULA.md](./EULA.md).
