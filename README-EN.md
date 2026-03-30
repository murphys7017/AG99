# AG99 Qingniao
> Ignite your own history

A long-running, event-driven, multi-session Agent runtime.  
The goal is not "the smartest single response", but a **stable, controllable, evolvable** runtime system.

> Historical name: `mk2` (you may still see it in older docs/comments)

---

## Why AG99 (Qingniao)

AG99 is the technical codename, and **Qingniao** is the Chinese project name.  
It is not just an Agent demo. It is a runtime that can evolve continuously: input bus, session isolation, pre-agent gate, controlled tuning, persistence, and async egress.

If you want this to become a real long-term work instead of a script that "just runs", this name fits.

---

## Project Positioning

AG99 is an **event-driven Agent runtime**, focused on:

- **Multi-session isolation**: one serial worker per `session_key`; serial within a session, concurrent across sessions
- **Pre-agent Gate decisions**: fast rule-based routing before Agent (`DROP / SINK / DELIVER`)
- **Agent orchestration**: `AgentQueen` coordinates pool_selector / context / pool / aggregator / speaker
- **System self-regulation**: System Reflex handles pain signals and tuning suggestions with controls (whitelist, TTL, cooldown)
- **Memory persistence (fail-open)**: event/turn write failures do not block the main path
- **Async egress**: outputs go through a background queue so worker loops are not blocked

---

## Main Runtime Path (Implemented)

```text
Adapter
  -> Observation
  -> InputBus
  -> SessionRouter
  -> SessionWorker
  -> Gate
  -> AgentQueen
  -> emit back to Bus
  -> Egress
```

### Core Semantics (Current Implementation)
- Unified cross-module event contract: `Observation`
- Gate output contract: `GateDecision` / `GateOutcome(decision + emit + ingest)`
- Agent main handling is triggered only by `DELIVER + MESSAGE`
- Agent outputs flow back into Bus with loop guards to prevent self-trigger loops
- Memory and Egress are designed as **fail-open** (errors should not collapse the main loop)

---

## Design Principles

### 1) Clear Boundaries
- Gate does not call Agent directly
- Agent does not mutate Gate config directly
- Runtime tuning is unified through `SystemReflex + GateConfigProvider.update_overrides()`
- Cross-module state changes should flow via `Observation` (avoid hidden side channels)

### 2) Simple and Controllable Concurrency
- **Serial within one session, concurrent across sessions**
- `SessionState` is updated serially only by its owning session worker

### 3) Stability Before "More Intelligence"
- Gate stays fast, synchronous, and deterministic (no LLM calls inside Gate)
- Synchronous LLM provider calls are wrapped from async paths via threads to avoid blocking the event loop

---

## Core Module Map

```text
src/
├─ core.py                 # runtime orchestration and worker lifecycle
├─ input_bus.py            # async input bus
├─ session_router.py       # session_key routing, inboxes, and session state
├─ gate/                   # pre-agent decision pipeline (scene/feature/scoring/policy/...)
├─ agent/                  # AgentQueen and pool_selector/context/pool/aggregator/speaker
├─ system_reflex/          # self-regulation controller (whitelist, TTL, cooldown, rollback)
├─ memory/                 # Event/Turn persistence, vault, failure queue
├─ adapters/               # input/output adapters
└─ schemas/                # cross-layer contracts such as Observation
```

---

## Quick Start

### Requirements
- Python 3.11+
- `uv` is recommended

### Install
```bash
uv sync
```

### Run
```bash
uv run python main.py
```

### Offline Regression (recommended first)
```bash
uv run pytest -m "not integration" -q
```

If `uv run pytest` has local compatibility issues, fallback to:

```bash
pytest -m "not integration" -q
```

### Integration Tests (real external dependencies)
```bash
uv run pytest -m integration -q
```

> Integration tests depend on real providers / APIs / local services; some live tests are gated by env vars (for example `RUN_LLM_LIVE_TESTS=1`).

---

## Configuration Notes

### `config/gate.yaml`
Defines Gate strategy and runtime behavior, including:
- `scene_policies`
- `rules`
- `drop_escalation`
- `overrides`
- `budget_thresholds`
- `budget_profiles`

Hot reload is supported through config provider snapshot replacement.

### `config/llm.yaml`
- Use environment variables for secrets (avoid plaintext keys in repo)
- Configure provider/model and runtime params

### `config/memory.yaml`
- Controls relational backend, vault, failure queue, etc.
- Memory init failures are fail-open by default

### `config/agent/`
- Agent top-level config and pool_selector sub-config
- Default planning path supports rule / llm / hybrid

---

## Runtime Behavior Highlights (for Troubleshooting)

- One serial worker per session
- Sessions run concurrently
- Worker loop records state first, then Gate, then decides whether to call Agent
- Outputs are sent via independent egress queue + loop to avoid external I/O blocking core workers
- Agent feedback messages include loop guards to prevent self-activation

---

## Documentation Map

### Active
- `docs/README.md`: documentation index (Active / Reference / Experimental / Archive)
- `docs/DEPLOYMENT.md`: deployment and runtime
- `docs/TESTING.md`: test layers and commands
- `docs/MEMORY.md`: current memory implementation
- `docs/PROJECT_MODULE_DEEP_DIVE.md`: maintenance/troubleshooting deep dive
- `docs/DESIGN_DECISIONS.md`: active ADR decisions
- `docs/GATE_COMPLETE_SPECIFICATION.md`: Gate specification
- `docs/SYSTEM_REFLEX_SPECIFICATION.md`: System Reflex specification
- `docs/ROADMAP.md`: phase plan and priorities

### Reference
- `docs/AGENT_REQUEST_STRUCTURE.md`
- `docs/AGENT_REQUEST_QUICK_REFERENCE.md`

### Experimental
- `tools/demo_e2e.py`
- `docs/demo_e2e.md`

> Experimental paths are not part of the stable runtime baseline. For deployment and validation, prioritize `main.py` + Active docs.

---

## Known Boundaries (Pragmatic Notes)

- Default pool availability is effectively `chat`; `code / plan / creative` fallback to `chat` unless custom pools are injected
- Single-session execution is serial by design, so slow requests block following requests in the same session
- `core.py` currently carries substantial orchestration logic and will be further split/refined

---

## Roadmap Direction

### P1: Agent Execution Capability
- Ship executable `code / plan / creative` pools (instead of chat-only fallback)
- Connect real tool invocation and result ingestion
- Improve pool-level metrics and error taxonomy

### P2: Observability
- Introduce cross-layer `trace_id`
- Add segmented latency metrics for Gate / Agent / Memory
- Standardize structured log fields

### P3: Structural Governance
- Split `core.py`
- Converge experimental scripts and mainline interfaces to reduce dual-track drift

---

## Development Workflow Suggestion

For each iteration:

1. Ensure offline regression passes first  
   `pytest -m "not integration" -q`
2. Run targeted tests for your changed area
3. Run integration tests last (if external dependencies are available)
4. Update corresponding Active docs whenever behavior changes

---

## License

MIT License.

See [LICENSE](./LICENSE) for details.

---

## Naming Inspiration

Project codename: **AG99**  
Chinese name: **Qingniao**

The name inspiration comes from **SCP-CN-1559 "Qingniao"**.

The long-term goal is not just to answer questions, but to build an Agent runtime that can run long-term, evolve continuously, and form stable internal order over time.

