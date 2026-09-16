---
outline: deep
---

# What is AG99?

AG99 is the public project name used by this repository. Created by YakumoAki and based on AstrBot, it is a persona-first, continuously running conversation runtime for multi-platform chats: one Persona can retain bounded state across turns and delegate substantial work to a separate Core execution layer when needed.

This page keeps the `what-is-astrbot` path for existing bookmarks and inherited links. The Python package, CLI, plugin prefix, and some configuration keys still use `astrbot` as a compatibility boundary; this repository is not merely an upstream AstrBot configuration fork. See [Project identity](/Yakumo/project-identity) for the full boundary. Yakumo is the author's name, not the project name.

## Core Flow

```text
Platform Adapter
  -> EventBus / Pipeline / Handler
  -> Interaction Middleware
  -> Personal Runtime + Personal Response Plan
  -> Core Planner (delegated turns only)
  -> Core Head / Core Execution
  -> Persona Expression
  -> Output Runtime
  -> Conversation / Memory
```

Normal messages and bounded unaddressed group candidates enter Interaction Middleware. Personal Runtime owns turn admission and session state. One structured Persona response plan returns `reply`, `delegate`, or (only for an eligible group candidate) `silent`:

- `reply`: complete the visible response in Personal without starting Core.
- `delegate`: send one natural, brief acknowledgement, then let Core Planner prepare the delegated work for Core tools, knowledge, Skills, and other substantial work.
- `silent`: emit no visible output; it is available only to eligible group candidates.

Core Planner does not make a second admission decision: it only prepares an executable `CoreTaskSpec` for a delegated turn. The Core Head coordinates task, event, cancellation, and execution lifecycle in-process. It is currently a synchronous entry point: it does not create a separate queue or send platform messages directly. Core results never bypass Persona Expression. Immediate replies, plugin persona output, and Core-final results share one visible-language and output boundary.

## Plugin Participation

- Pipeline Handlers retain ownership of keywords, commands, and protocol events.
- Prompt Extensions contribute target-scoped structured facts; they are not LLM Tools.
- Executable plugin tools default to Core and enter Persona only through explicit declaration or user configuration.
- Persona Effects are a structured presentation protocol; plugins interpret concrete Motion or Live2D semantics.
- Runtime Sensors submit bounded, expiring structured observations and cannot submit user text, prompts, tool calls, or final copy.

## Documentation

- [Project identity](/Yakumo/project-identity)
- [Yakumo architecture index](/Yakumo/)
- [Current state](/Yakumo/current-state)
- [Deployment](/en/deploy/astrbot/package)
- [Messaging platforms](/en/platform/start)
- [Model providers](/en/providers/start)
- [Plugin development](/en/dev/star/plugin-new)

## Current Status

Yakumo is under active development and real-path validation. For runtime behavior, follow the source and [current state](/Yakumo/current-state); `dev/` and `target-state.md` documents marked as plans or designs are not completion claims.

The project continues to use the `AGPL-3.0-or-later` license and follows the applicable AstrBot compatibility notices; see [LICENSE](https://github.com/murphys7017/AG99/blob/codex/unify-prompt-context-pipeline/LICENSE) and [EULA](https://github.com/murphys7017/AG99/blob/codex/unify-prompt-context-pipeline/EULA.md).
