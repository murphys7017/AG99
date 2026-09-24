# Executive Summary

Audit date: 2026-09-21. Scope: the repository's Python runtime, with representative tracing of startup, platform-event processing, Interaction/Personal runtime, Core execution, output delivery, plugin capability control, provider adapters, configuration, Cron/proactive work, tests, and recent Git history. This is a read-only architecture audit; no production code, configuration, tests, or governance files were changed. The only worktree change made by this audit is this report. `astrbot/core/astr_main_agent.py` was already modified before the audit and was not used as a source of new findings beyond its public call sites.

The repository has a clear product-level intent: platform events enter a pipeline; Personal/Interaction decides whether to reply, delegate, or remain silent; Core executes provider/tool work; output is rendered and delivered through platform events; plugins extend several boundaries. The dominant architecture risk is not an absence of abstractions. It is a partially completed migration in which old pipeline/Event contracts and newer typed Interaction/Core contracts run together, linked by event extras, compatibility projections, method interception, and adapters.

| Dimension | Score | Evidence-based assessment |
| --- | ---: | --- |
| Architecture clarity | 5/10 | The primary path is discoverable, but normal Pipeline and Personal/Core orchestration overlap. |
| Concept consistency | 5/10 | `Context`, turn state, execution outcome, event extras, and capability targets have multiple representations. |
| Single-responsibility ownership | 4/10 | Lifecycle, output, and Personal session decisions span several large coordinators. |
| Change predictability | 4/10 | A new turn/output/capability behavior crosses pipeline, Interaction, platform event, and compatibility code. |
| Deletability | 4/10 | Explicit compatibility seams exist, but most remain live and lack a removal gate. |
| Observability | 5/10 | Execution events exist, but no single correlation record reconstructs the complete event-to-delivery path. |
| AI-corruption risk | High | Recent history shows repeated incremental convergence changes, while raw extras, broad catches, mirrors, and very large coordinators still make additive patching easy. |

The recommended direction is subtractive: first make the typed turn/output/execution contracts authoritative; then remove compatibility projections and duplicate entry paths only after live acceptance. Do not add another manager, facade, callback, or event-extra namespace to bridge the current model.

# System Mental Model

## Entry and runtime map

`main.py:main_async` creates `InitialLoader`; `InitialLoader.start` creates `AstrBotCoreLifecycle`; `AstrBotCoreLifecycle.initialize` assembles configuration routing, providers, platforms, conversations, plugins, Cron, pipeline schedulers, Interaction middleware, Personal runtime, execution ledger, output services, dashboard dependencies, and background lifecycle tasks. The lifecycle is currently the composition root and also owns environment mutation, reload, startup, shutdown, and service supervision.

Normal platform work is:

`platform adapter -> EventBus -> PipelineScheduler.execute -> ordered pipeline stages -> ProcessStage -> InternalAgentSubStage -> build_main_agent -> NativeExecutorAdapter/CoreExecutionHead -> provider/tool runner -> platform output`.

`PipelineScheduler.execute` assigns config data to the event and registers it globally (`astrbot/core/pipeline/scheduler.py:88-109`). It also conditionally activates a Personal turn through `getattr`-based optional wiring (`:32-35`). `InternalAgentSubStage.process` builds or adapts the Core request, bridges execution through `NativeExecutorAdapter`, and maintains native-run cleanup (`astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py:215+`).

The newer Interaction path is:

`ProcessStage/PersonalRuntimeManager -> InteractionMiddleware -> Personal planning/expression -> optional Core task spec -> InternalAgentSubStage/Core execution -> InteractionOutputController -> platform delivery/finalization`.

For proactive work, `AstrBotCoreLifecycle` injects callbacks into `star.Context`, which dispatch to `PersonalRuntimeManager.dispatch_proactive_message` or `submit_observation` (`astrbot/core/core_lifecycle.py:350-404`). This is a separate entry route rather than a platform event.

## Principal owners

| Concern | Current primary owner | Adjacent competing owner(s) |
| --- | --- | --- |
| Plugin lifecycle and registries | `PluginManager` / Star registries | `plugin_admission`, `plugin_runtime`, `plugin_capability_inventory` |
| Per-turn plugin permission | `PluginAdmissionSnapshot` | event `plugins_name`, session disabled lists, live registry lookup |
| Personal session state and admission | `PersonalRuntimeManager` | `PersonalTurnContext`, `InteractionTurnState`, Pipeline event state |
| Core execution state | `CoreExecutionHead` / `CoreExecutionLifecycle` | `NativeExecutionRun`, `InternalAgentSubStage`, event extras |
| Output preparation and delivery | `InteractionOutputController` plus `PreOutputProcessor` / `TurnDeliveryCoordinator` | RespondStage, platform event methods, delayed/plugin/proactive paths |
| Configuration selection | `AstrBotConfigManager` / `UmopConfigRouter` | scheduler context snapshots and event extras |

## Confidence and scope limits

Findings marked **Confirmed** are supported by source, call sites, tests, and/or explicit compatibility comments. **Strong candidate** items have a clear structural problem but require production traffic or external-plugin checks before deletion. **Needs confirmation** means static inspection cannot establish external usage or required compatibility. Broad test suites were deliberately not run: this audit does not treat test execution as proof that an architecture path should survive.

# Top Problems

## P1: Turn-owner boundaries remain incomplete; duplicated terminal writes require proof

### Evidence

- `PipelineScheduler` owns stage recursion, stopping, config attachment, active-event registration, temporary-file cleanup, and visible-turn completion (`astrbot/core/pipeline/scheduler.py:47-109`).
- It conditionally binds the active Personal turn via `_activate_personal_turn`; the current implementation activates a ContextVar for an already-admitted turn and does not itself admit or finalize a turn (`:32-35`, `:72-73`; `personal_runtime.py:2114-2124`).
- `PersonalRuntimeManager` independently owns admission, interruption, group/runtime binding, observation scheduling, proactive dispatch, and active-runner registration (`astrbot/core/interaction/personal_runtime.py:1309-1500`, `:1582-2260`).
- `InternalAgentSubStage.process` decides whether an event has content, a provider request, a delegated Core task, or media before starting native execution (`astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py:272-294`).

Call chain: `EventBus -> PipelineScheduler.execute -> ProcessStage -> PersonalRuntimeManager/InteractionMiddleware -> InternalAgentSubStage.process -> CoreExecutionHead`.

### Why It Exists

Recent history records a migration from the established pipeline to typed Personal/Core lifecycle control, including commits such as `9f3ddd470 refactor(core): establish execution head boundary`, `f7581a1a9 refactor(execution): establish executor body boundary`, and `f5f37194f refactor(core): inject default executor port`.

### Why It Is Dangerous

Pipeline traversal, Personal session admission, Core execution state, and transport completion all participate in one user interaction. The current evidence establishes an ownership-boundary risk and an optional integration fallback, but does not by itself prove that they write the same terminal state. A duplicate completion or cancellation must be demonstrated by a common identity and write path before the architecture is collapsed.

### Recommended Direction

First record the canonical writer for admission, interruption, conversational terminal state, execution terminal state, visible completion, and cleanup. Retain Pipeline as stage traversal and Personal Runtime as session/admission owner unless a concrete duplicate writer proves otherwise. Replace optional activation only after scheduler construction can validate the required integration.

Canonical direction: explicit owner table before any coordinator merge. Change risk: High. Validation: live ordinary, delegated, cancelled, timeout, media-only, proactive, and group-continuation turns; correlate every terminal write and verify one visible completion. Confidence: **Confirmed** for incomplete boundaries; duplicate orchestration is **Needs confirmation**.

## P1: Output lifecycle is distributed across a 2,947-line controller and legacy delivery services

### Evidence

- `InteractionOutputController` is 2,947 lines and owns preparation, stream handling, output deduplication, persistence callbacks, artifacts, delivery, completion, and error recovery (`astrbot/core/interaction/output_controller.py`).
- `PreOutputProcessor` separately owns response safety and decorating hooks; `TurnDeliveryCoordinator` owns after-send hooks, visible completion, postprocess scheduling, and request snapshots (`astrbot/core/output_lifecycle.py:48-203`, `:206-348`).
- Legacy pipeline stages still instantiate or consume those services through `PipelineContext`, `result_decorate/stage.py`, and `respond/stage.py` (`astrbot/core/pipeline/context.py:16-34`).
- `AstrMessageEvent` retains output hook installation, original-method references, legacy-extra mirrors, and fallback delivery paths (`astrbot/core/platform/astr_message_event.py:372-429`, `:752+`).

Call chain: `InternalAgentSubStage -> event.send / output adapter -> InteractionOutputController -> PreOutputProcessor -> TurnDeliveryCoordinator -> event.complete_visible_turn -> postprocess`.

### Why It Exists

The system must preserve platform plugins that call Event send methods while Interaction takes over per-turn delivery. `PreOutputProcessor` and `TurnDeliveryCoordinator` are useful shared boundaries, but the controller has accumulated unrelated policy and transport responsibilities around them.

### Why It Is Dangerous

Output suppression, safety, hooks, visible completion, persistence, artifacts, stream completion, and postprocess can diverge by source. A contributor can add a direct send or an extra callback that bypasses the desired transaction without an obvious compiler failure.

### Recommended Direction

Keep `PreOutputProcessor` as the policy boundary and `TurnDeliveryCoordinator` as post-delivery lifecycle. Split `InteractionOutputController` by the existing boundaries: output intent/arbitration, platform delivery, and turn settlement. Converge all origins, including pipeline, delayed plugins, and proactive delivery, through one typed output intent before removing Event interception.

Canonical owner: an output transaction rooted in the controller's successor. Change risk: High. Validation: ordinary, Core-final, streaming, plugin-decorated, artifact, failed delivery, duplicate suppression, and visible completion behavior per platform. Confidence: **Confirmed**.

## P1: Event extras are an untyped parallel state transport for typed turn and execution contracts

### Evidence

- There are 329 `set_extra`/`get_extra` usages in Interaction, Pipeline, execution, and `AstrMessageEvent` alone.
- `PipelineScheduler.execute` writes `_astrbot_config` and `_astrbot_config_id` to the event (`astrbot/core/pipeline/scheduler.py:95-96`).
- `InternalAgentSubStage` reads `provider_request`, `enable_streaming`, Core task data, and Interaction runtime configuration from event state (`astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py:227-272`).
- `AstrMessageEvent` explicitly states that typed Interaction state is mirrored to legacy extra keys and that copied branch events use legacy fallbacks (`astrbot/core/platform/astr_message_event.py:76-80`, `:387-429`).
- Typed alternatives exist: `CoreExecutionSpec`, `CoreExecutionOutcome`, `InteractionTurnState`, `PersonalTurnContext`, `ContextPack`, and `PluginAdmissionSnapshot`.

### Why It Exists

Event extras were the original extension envelope. Typed owners were added incrementally to avoid breaking platform adapters and plugins.

### Why It Is Dangerous

Ownership is no longer evident from a field's type or constructor. Copying an event can copy a stale compatibility projection, and an unset key often silently selects an old branch. This is the strongest local indicator of AI-era patch accumulation.

### Recommended Direction

Define the permitted event-boundary fields and make typed turn state the source of truth. Treat each legacy extra as a named compatibility projection with a producer, consumers, deprecation criterion, and removal validation. Do not add new raw extras for Core, output, plugin admission, or configuration.

Canonical owner: typed turn/execution objects. Change risk: High. Validation: branch/copy event handling, plugin hooks, delayed delivery, Core cancel/follow-up, and external adapter compatibility. Confidence: **Confirmed**.

## P2: Lifecycle composition is concentrated in a god object with hidden global side effects

### Evidence

- `AstrBotCoreLifecycle.initialize` constructs or configures configuration routing, migrations, interaction services, memory, plugins, providers, knowledge, schedulers, event bus, platforms, background services, and dashboard shutdown (`astrbot/core/core_lifecycle.py:247-461`).
- Its constructor also mutates process proxy environment variables (`:74-129`).
- It owns pipeline reload, platform loading, service task tracking, start, stop, and restart (`:463-677`).
- `InitialLoader.start` catches initialization failure, returns, then independently gathers the Core lifecycle and dashboard process (`astrbot/core/initial_loader.py:20-55`).

### Why It Exists

This is a conventional application composition root that has expanded as subsystems were added.

### Why It Is Dangerous

Startup order, resource ownership, reload behavior, and process-wide settings are coupled. A subsystem's required dependencies cannot be seen from its constructor alone, and partial initialization can be logged and converted into a normal return rather than an explicit failed process state.

### Recommended Direction

Retain one composition root, but move environment/bootstrap, subsystem assembly, and run-supervision into explicit boot phases with declared return values. `InitialLoader` can then become a thin launcher or disappear. Do not introduce a second service locator.

Canonical owner: application bootstrap composition root. Change risk: Medium-High. Validation: clean startup, config reload, failed migration, dashboard disabled, graceful stop, and restart. Confidence: **Confirmed**.

## P2: Core execution is typed but still wrapped by several overlapping native adapters

### Evidence

- `execution.py` contains `CoreExecutionSession`, `CoreExecutionLifecycle`, `CoreExecutionHead`, synchronous command acceptance, event mailboxes, outcome construction, ledger settlement, and an execution-spec adapter in 1,848 lines (`astrbot/core/execution.py:583-1447`, `:1615-1720`).
- `astr_agent_run_util.py` adds `NativeExecutionRun`, `NativeExecutorAdapter`, output bridging, and stable wrappers `run_agent`/`run_live_agent` (`astrbot/core/astr_agent_run_util.py:73-225`, `:942-1005`).
- `InternalAgentSubStage` imports and activates all three native adapter abstractions while also binding the execution head and projecting results (`astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py:31-71`, `:215+`).
- Recent Git history contains more than 25 consecutive Core/Native convergence commits around adapter activation, output bridges, executor binding, terminal projection, and settlement.

### Why It Exists

The repository is actively migrating a native agent runner toward an executor-body boundary without replacing the existing runner in one change.

### Why It Is Dangerous

The same lifecycle facts can be represented by runner state, `NativeExecutionRun`, `CoreExecutionSession`, `CoreExecutionHead`, the execution ledger, and event extras. The wrapper count is justified during migration but makes terminal behavior difficult to prove and expensive to modify.

### Recommended Direction

After live acceptance, choose `CoreExecutionHead`/lifecycle as the sole state and command boundary. Collapse wrappers that only forward lifecycle calls, retaining exactly one native protocol translation layer. Preserve the stable public entry points only where external callers actually use them.

Canonical owner: `CoreExecutionHead` plus its lifecycle. Change risk: High. Validation: interruption ordering, deadline, follow-up, stream closure, terminal-first behavior, ledger settlement, proactive execution, and external callers of the wrappers. Confidence: **Confirmed** for overlap; wrapper deletion is **Needs confirmation**.

## P2: Plugin capability governance is split between admission, target routing, and inventory reflection

### Evidence

- `plugin_admission.py` defines capability kinds, interaction/process scope, owner lookup, per-turn snapshots, and permission resolution (`astrbot/core/plugin_admission.py:1-330`, `:456-582`).
- `plugin_runtime.py` independently resolves configured LLM-hook and tool targets (`astrbot/core/plugin_runtime.py:71-127`).
- `plugin_capability_inventory.py` recreates registry traversal and calls both target resolvers to describe UI state (`astrbot/core/plugin_capability_inventory.py:262-345`). It also records migration states such as `legacy_compatibility`, `needs_review`, `leak_on_unload`, and `no_lifecycle_gate`.
- `resolve_owner_metadata` lazy-imports the Star registry to avoid a circular dependency (`plugin_admission.py:~190`).

### Why It Exists

The model deliberately separates permission, applicability, and execution target. Inventory is a diagnostic/UI projection.

### Why It Is Dangerous

The separation is sound, but each axis has different live sources, snapshots, registry traversal, and fallback behavior. Adding a capability kind risks updating several switches and UI projections. Circular-import avoidance hides a core dependency edge.

### Recommended Direction

Keep permission, applicability, and target as separate domain axes. First compare the existing admission resolver, target resolver, and inventory projection on the same registry/configuration snapshot; only extract shared traversal or descriptor construction where a concrete divergent interpretation is found. Keep the UI inventory as a projection, not a second policy owner. Resolve the Star-registry dependency at bootstrap only if it improves the dependency graph without creating a second registry snapshot.

Canonical owner: capability descriptor plus per-turn snapshot. Change risk: Medium-High. Validation: plugin reload mid-turn, per-session disable, empty/`["*"]` plugin sets, process-level capabilities, dashboard inventory, and legacy external plugins. Confidence: **Strong candidate**.

## P2: Defensive fallback chains conceal integration-contract failures

### Evidence

- `PipelineScheduler._activate_personal_turn` uses `getattr` for both manager and activation callback, returning `nullcontext()` when unavailable (`astrbot/core/pipeline/scheduler.py:32-35`).
- `InternalAgentSubStage` repeatedly defaults runtime settings to instance defaults and normalizes invalid shapes (`astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py:181-208`, `:227-270`).
- `PreOutputProcessor.run_decorating_hooks` logs and continues after any plugin handler exception (`astrbot/core/output_lifecycle.py:125-154`).
- `AstrMessageEvent` keeps method/reference fallbacks for legacy output hooks and copied events (`astrbot/core/platform/astr_message_event.py:372-429`, `:752+`).
- The runtime contains 988 broad `except Exception` occurrences; 677 `getattr`/`.get` occurrences are concentrated in the inspected cross-boundary modules.

### Why It Exists

Most of these defenses protect third-party plugins, optional providers, platform adapters, or ongoing migrations. They are not all defects.

### Why It Is Dangerous

At internal boundaries, a missing owner or malformed runtime contract can become an old default, a skipped hook, or an implicit no-op. This keeps service availability but makes incorrect lifecycle behavior hard to detect and encourages further compatibility patches.

### Recommended Direction

Classify every cross-boundary fallback as external-boundary defense, temporary migration, or invalid internal contract. Keep external plugin/provider isolation; replace internal optional wiring with validation at admission/bootstrap and a correlated diagnostic. Track a bounded removal date/condition for migration fallbacks.

Canonical owner: boundary-specific contract validators. Change risk: Medium. Validation: injected missing/malformed config, plugin exceptions, absent manager, copied events, and platform adapter failures. Confidence: **Confirmed** for the chains; repository-wide count is contextual evidence only.

## P3: Explicit compatibility residue has no unified retirement inventory

### Evidence

- `astrbot/core/provider/entites.py` is a 407-byte misspelled re-export of `entities.py`; no in-repository textual references were found.
- `astrbot/core/platform/message_session.py` retains `MessageSesion = MessageSession` backward compatibility; Core-owned imports now use the canonical `MessageSession` name.
- `Context.registered_web_apis` is now instance-owned, and registrations made inside an explicit plugin owner scope are removed during owner teardown. Ownerless legacy routes remain compatible but are not automatically attributable.
- `AstrMessageEvent` documents legacy output hook and extra-key compatibility (`astrbot/core/platform/astr_message_event.py:76-80`, `:372-429`, `:514+`).
- `openai_source.py:~107` and `anthropic_source.py:~67` each retain a compatibility path for callers without compiled bindings, both annotated for deletion after migration.
- Persona prompt collection still parses legacy free-form prompt text (`astrbot/core/prompt/persona_segments.py:142+`; `collectors/persona_collector.py:147-157`).

### Why It Exists

Public plugins, persisted configurations, provider call styles, and platform adapters cannot necessarily migrate atomically.

### Why It Is Dangerous

Each local compatibility clause appears reasonable, but without a system-level registry it cannot be retired confidently. This is the mechanism by which temporary migration code becomes permanent architecture.

### Recommended Direction

Create no new compatibility adapter. Instead, catalog existing clauses by external contract, current caller, telemetry/usage evidence, breaking-change policy, and removal release. Start with self-contained aliases and then internal mirrors.

Canonical owner: a compatibility retirement register maintained with public-interface policy. Change risk: Low to High by item. Validation: external plugin/import scan, persisted-config migration, provider integration tests, and release notes. Confidence: **Confirmed** for existence; required status is mostly **Needs confirmation**.

# Duplicate Concepts

| Concept | Locations / names | Semantic overlap | Canonical direction |
| --- | --- | --- | --- |
| Runtime context | `star.Context`, `PipelineContext`, `PersonalTurnContext`, `InteractionTurnState`, Prompt `ContextPack` | All carry combinations of request/session/config/plugin/runtime data, though at different scopes. | Keep scope in names and pass only typed, bounded views across layers; do not use event extras as a sixth context. |
| Turn terminal result | `InteractionTurnOutcome`, `CoreExecutionOutcome`, visible completion flags, Event stopped/result state | Each expresses a terminal or semi-terminal outcome. | Core owns execution outcome; turn coordinator owns user-visible outcome; platform event owns transport completion only. |
| Output lifecycle | `InteractionOutputController`, `PreOutputProcessor`, `TurnDeliveryCoordinator`, RespondStage, event output hooks | All participate in shaping or completing a reply. | Split policy, delivery, and settlement with one input contract. |
| Plugin capability status | `CapabilityKind` + snapshot, target resolvers, inventory rows, Star handlers | Permission, routing target, and displayed state are repeatedly interpreted. | One descriptor resolved from one snapshot. |
| Configuration projection | config manager/router, scheduler `PipelineContext`, event `_astrbot_config`, Interaction runtime config, `MainAgentBuildConfig` | Config is selected then copied/projection-bound several times. | Config manager selects; an immutable per-turn config projection crosses the boundary. |

# Suspicious Compatibility Code

## Confirmed required compatibility

- `AstrMessageEvent` output hook/original method compatibility is actively referenced by current Interaction output integration. It cannot be deleted before all platform and plugin delivery entry points use a typed output contract.
- `openai_source.py` and `anthropic_source.py` legacy uncompiled-binding paths may protect provider callers outside the repository. Keep pending caller telemetry or a supported API version boundary.
- Legacy persona prompt parsing can protect persisted user configuration. Treat it as a data migration, not a parser duplicate, until old persisted values are measured.

## Likely removable

- `astrbot/core/provider/entites.py`: misspelled forwarding module. Deletion confidence: **Medium**. No repository references were found; raise to High only after packaged/external plugin import analysis and a deprecation window.
- `MessageSesion` alias in `astrbot/core/platform/message_session.py`. Deletion confidence: **Low**; it is public-facing and external imports are unknown.

## Needs confirmation

- Compatibility projections and fallback keys in `AstrMessageEvent` may be used by third-party platform adapters/plugins loaded from the data directory.
- `plugin_capability_inventory` migration-state entries identify candidates, not evidence that a capability has no owner or callers.

# Excessive Defensive Programming

1. `PipelineScheduler._activate_personal_turn -> getattr(manager) -> getattr(activate) -> nullcontext()` turns a missing lifecycle integration into a normal pipeline execution. Establish the coordinator dependency when a scheduler is created.
2. `InternalAgentSubStage.initialize/_build_turn_main_agent_config/process` first snapshots defaults, then conditionally replaces them from runtime mapping, then repeatedly falls back through `getattr`. Validate an immutable runtime config projection once at turn admission.
3. `AstrMessageEvent` stores typed state, original methods, and legacy extras, then falls back to extras for copied events. Preserve this only at an adapter boundary; copied event behavior needs an explicit clone contract.
4. Broad exception continuation around decoration hooks is justified for third-party isolation, but must emit a turn/plugin/correlation identifier and an outcome that permits diagnosis of suppressed output.

# Excessive Abstraction

| Chain | Contribution | Assessment |
| --- | --- | --- |
| `InternalAgentSubStage -> NativeExecutionRun -> NativeExecutorAdapter -> CoreExecutionHead -> CoreExecutionLifecycle -> CoreExecutionSession` | Pipeline adaptation, native runner protocol adaptation, command/event boundary, lifecycle, state machine. | Each currently has a stated purpose. Remove only wrappers proven to be pure forwarding after the shared run coordinator replaces their behavior; do not prescribe a fixed layer count in advance. |
| `Event send -> event output adapter/hooks -> InteractionOutputController -> PreOutputProcessor -> TurnDeliveryCoordinator -> event completion` | Compatibility interception, arbitration, policy, post-delivery lifecycle. | Too many owners for one output; preserve only policy/delivery/settlement. |
| `plugin_admission -> plugin_runtime target resolver -> plugin_capability_inventory` | Permission, target selection, UI projection. | The split is domain-valid, but snapshot traversal should be shared rather than rebuilt in inventory. |

# Dead / Legacy Code Candidates

| Candidate | Evidence | Deletion confidence | Future validation |
| --- | --- | --- | --- |
| `astrbot/core/provider/entites.py` | Pure re-export of `entities.py`; no in-repo references found. | Medium | Search installed/data-directory plugins, package consumers, release history. |
| `MessageSesion` alias | Explicit backward-compatibility alias. | Low | Public API search and deprecation cycle. |
| Provider uncompiled-binding compatibility paths | Explicit comments say delete after migration. | Low | Provider contract/version inventory and integration checks. |
| Event extra compatibility mirrors | Explicitly described as legacy in `AstrMessageEvent`. | Low | Inventory every writer/reader including external plugins and copied event paths. |

# Single Source of Truth Violations

- **Turn state:** Pipeline stop/result/visible completion, `InteractionTurnState`, `PersonalTurnContext`, and Core session outcome overlap. The canonical split should be execution outcome in Core, conversational outcome in the turn coordinator, and transport outcome in the platform adapter.
- **Configuration:** `AstrBotConfigManager` selects configuration, but scheduler context, event extras, Interaction runtime state, and `MainAgentBuildConfig` can each hold projections. First designate the existing admission-time projection and its permitted update phase; do not introduce another aggregate turn context solely to hold duplicate fields.
- **Output decision:** OutputController, pipeline RespondStage, `TurnDeliveryCoordinator`, and event hooks all affect delivery/completion. Preserve exactly one delivery transaction.
- **Plugin capability:** Snapshot permission and configured targets are intentionally separate owners. Confirm whether inventory produces a divergent result before extracting shared descriptor/traversal code.

# Observability Gaps

- There is no demonstrated single correlation record that joins platform event ID, config ID, admission decision, Personal decision, Core execution ID, plugin capability decisions, output origin, platform delivery receipt, visible-turn completion, and ledger settlement.
- Broad exception paths frequently log a local error but do not necessarily preserve the turn outcome or fallback selection in one queryable trace.
- Compatibility branches are documented in comments but are not visibly metered. For public/external compatibility, usage evidence is required; for internal transition code in this development-stage repository, caller migration plus focused validation can be a sufficient deletion gate.
- The current worktree state records live acceptance still pending for several recent Core/Interaction changes in `.ai/state.yaml`; static tests cannot establish platform lifecycle ordering.

# Architectural Simplification Opportunities

1. **Delete:** Remove `provider/entites.py` only after external-import confirmation.
2. **Converge:** Designate existing typed per-turn owners and retire Core/output/config event extras one named key at a time; do not introduce an aggregate context that duplicates `InteractionTurnState` or a late-created `CoreExecutionSpec`.
3. **Merge:** Route all output origins through a small typed intent and retain `PreOutputProcessor` and `TurnDeliveryCoordinator` as distinct policy/settlement services.
4. **Converge:** Prove or disprove duplicate terminal writes, then remove only the redundant lifecycle path while retaining distinct Pipeline traversal, Personal admission, Core execution, and transport responsibilities.
5. **Delete:** After live acceptance, remove forwarding Native execution wrappers that have no translation rule or external caller.
6. **Converge:** Share capability registry traversal or descriptor construction only after same-snapshot comparison proves duplicated interpretation.
7. **Refactor:** Break `AstrBotCoreLifecycle` into explicit bootstrap phases while retaining one composition root.
8. **Delete:** Retire legacy persona/provider/event branches through measured compatibility milestones, not additional fallback layers.

# Potential Delete List

- `astrbot/core/provider/entites.py` forwarding module, pending external usage confirmation.
- `MessageSesion` compatibility alias, pending public API deprecation.
- Legacy Event extra mirrors and original-method output hooks, after every internal/external consumer has moved to typed output intent.
- Uncompiled provider binding compatibility branches in OpenAI/Anthropic sources, after caller/version evidence.
- Native execution forwarding wrappers that only relay Head/Lifecycle calls, after real interruption/proactive acceptance and caller tracing.

# Refactoring Order

1. **Confirmed deletion preparation:** inventory compatibility writers/readers and external consumers. Instrument public boundaries where practical; internal transition code may be removed after callers migrate and focused validation passes.
2. **Concept and contract convergence:** complete the existing typed owners for selected config, admission snapshot, Core spec, and correlation IDs; stop adding event extras. Do not create an aggregate turn context that duplicates those owners.
3. **Responsibility ownership:** document and test terminal writers across Core, Personal, pipeline, and platform delivery. Selectively remove a path only after a duplicate write or obsolete transition path is proven.
4. **Output convergence:** represent all output sources as typed intents; separate output policy, delivery, and settlement; prove one completion per turn.
5. **Compatibility removal:** remove event-extra projections, aliases, old provider/persona paths, and optional wiring after their callers migrate and the appropriate internal or external deletion gate is met.
6. **Call-chain simplification:** collapse native forwarding layers and make `InitialLoader`/bootstrap boundaries explicit.
7. **Observability and rationale:** retain only diagnostics needed to reconstruct the canonical path; document why any remaining compatibility boundary exists and its removal gate.

## Reverse Check

This review assesses system coherence rather than local style. It does not assume that a wrapper, fallback, or test is valuable merely because it exists. Findings distinguish active compatibility from deletion candidates, name canonical owners, and assign **Needs confirmation** where external usage or live platform behavior cannot be established statically. No remediation is claimed complete.

## Follow-up Audit: Configuration and Cross-Path Review (2026-09-24)

This follow-up rechecked the findings through source-level call-chain inspection,
not test outcomes. Existing executor/configuration worktree edits were preserved.

### Confirmed findings

1. **Per-configuration Agent routing is not isolated.**
   `AgentRequestSubStage.initialize()` chooses Internal or ThirdParty execution
   from the pipeline context configuration, while events can later carry a
   configuration selected by UMO routing. Wake prefixes, prompt prefix/identifier,
   and several sub-stage settings are also captured at initialization.
2. **Configuration lookup has a silent fallback and read-side mutation.**
   `AstrBotConfigManager.get_conf()` falls back to `default` for a missing routed
   configuration. `_load_conf_mapping()` and `get_conf_list()` remove `umop` from
   shared metadata dictionaries in place. Missing routed configurations must be
   explicit failures; reads must not mutate mappings.
3. **Cron/proactive execution is a second lifecycle path.**
   It constructs synthetic events, prepares prompts, selects execution, delivers
   output, and settles ledger records outside the ordinary platform-turn route.
4. **Execution session ownership remains Codex-specific.**
   `ExternalExecutorSessionRegistry` reads `unusable` and `invalidate()` directly
   from `CodexSessionManager`; selection is extensible but resource lifecycle is not.
5. **Command, plugin, Personal, and ordinary LLM decisions are split.**
   Protocol bypass, wake-prefix handling, Handler execution, and AgentRequest use
   separate gates, requiring one explicit admission order.

### Findings narrowed after recheck

- `CronMessageEvent.send()` does not establish a physical double-send: its base
  Event call records send state after `Context.send_message()` performs delivery.
  Duplicate state/receipt accounting remains a risk.
- Same-session `Context.send_message()` may enter the Personal dispatcher. The
  confirmed issue is multiple user-visible output paths with different transaction
  and settlement guarantees, especially cross-session/platform-direct output.

### Next bounded work item

Configuration convergence is the first cleanup target. Define one immutable
admission-time `TurnConfigSnapshot` containing configuration identity and the
runtime projections consumed by the turn. Pipeline, Personal, Prompt, Core, Output,
Cron, and plugin admission must consume that snapshot; a missing routed configuration
must not select `default` implicitly.
