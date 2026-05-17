# Upstream Merge Ledger

This document records upstream changes that were reviewed but not merged into this fork.
Keep appending to it when reviewing future upstream updates, so old merge decisions remain easy to revisit.

## Dynamic Sync Board

Last updated: 2026-05-18

Current comparison baseline:

- Local branch: `master`
- Upstream remote: `upstream` (`https://github.com/AstrBotDevs/AstrBot`)
- Last local upstream snapshot checked: `upstream/master` at `dceacd5a` (`docs: update release version instructions in AGENTS.md`)
- Remote refresh status: `git fetch upstream --prune` and `git ls-remote upstream refs/heads/master` both failed with `Recv failure: Connection was reset`; numbers below are based on the existing local `upstream/master` snapshot.
- Git-only divergence at this snapshot: local-only `272`, upstream-only `181`.
- Patch-equivalence estimate from `git cherry`: `67` upstream commits appear already absorbed, `114` still appear unabsorbed.

Important interpretation:

- This fork often rewrites upstream changes instead of cherry-picking them.
- A commit still shown as upstream-only may already be functionally absorbed if the local patch differs.
- Before merging anything, compare by topic and behavior, not only by commit hash.

Current local upstream-sync commits:

- `e302356b` Add NVIDIA and Ollama embedding providers
- `29e1e1f8` Improve provider response compatibility
- `b4cb5545` Harden outbound media handling
- `0da4b18c` Preserve plugin metadata and install cleanup
- `6096253d` Polish dashboard input and status handling
- `a1e4240d` Auto-select Shipyard Neo profiles by default
- `8cbb60d4` Expose embedding input type setting
- Pending commit in current sync batch: provider streaming empty-assistant filtering, Discord command quota startup guard, Weixin OC session-timeout login-state reset.

Recently absorbed by rewrite:

- Provider/runtime compatibility:
  - OpenAI SDK httpx alignment, empty reasoning handling, DeepSeek v4 reasoning history, `None` tool arguments, context-length retry matching, MiniMax TTS timber-weight fallback, fallback `max_context_tokens`.
- Embedding providers:
  - NVIDIA NIM Embedding and Ollama Embedding source adapters, provider manager imports, default Web config templates, locale hints, and `input_type` metadata.
- Outbound media and platform fixes:
  - Active replies pass image inputs through LLM requests, Tencent SILK magic-byte detection, stricter `SendMessageToUserTool` path handling, Weixin OC media send failure surfacing.
- Plugin/runtime metadata:
  - Handler kwargs preservation, plugin `pages` metadata, repeated install cleanup/error-tracking behavior.
- Dashboard and upload handling:
  - Chat upload filename sanitization, IME Enter guard, provider status test error display, console auto-scroll sync, console log layout improvements.
- Shipyard Neo:
  - Empty profile now means auto-select; any non-empty explicit profile is honored.
- 2026-05-18 follow-up review found these upstream fixes already present in this fork before this batch:
  - Windows updater zip root path normalization.
  - Blank prompt skipping knowledge-base retrieval.
  - Startup warning when default chat provider is missing or invalid.
  - Anthropic custom headers and system-prompt compatibility.
  - OpenRouter `reasoning` key override.
  - Empty-string reasoning content support.
  - Telegram media group scheduled-job exception logging.

### Topic Merge Plan

Use this table as the live working plan. Update `Status`, `Local action`, and `Next check` whenever upstream sync work is done.

| Topic | Status | Upstream examples | Local action | Next check |
| --- | --- | --- | --- | --- |
| Security fixes | In progress | Upload path traversal, backup importer traversal, password policy, updater zip root path | Upload filename sanitization was absorbed; older backup-importer and updater zip-root handling are already present. Password policy remains deferred because upstream `7ddf6371` is a broad auth/onboarding/storage migration, not a small patch. | Review password setup/password hashing as a dedicated auth migration batch. |
| Provider and model runtime | In progress | OpenAI http client, reasoning content, Claude no-arg tools, MiniMax TTS, Embedding providers, Anthropic compatibility | Several small compatibility fixes were rewritten locally. This batch adds shared empty-assistant sanitization to both non-streaming and streaming OpenAI-compatible requests. | After commit, re-run `git cherry` triage and review provider warning/default-model edge cases only if behavior differs locally. |
| Platform adapters and outbound media | In progress | Active reply images, Weixin OC send failures/session timeout, Telegram media group errors, Discord startup quota, KOOK role mentions, Dingtalk/Feishu QR setup | Active reply image, SILK, Weixin send failure, Telegram media group logging, and message-tool path handling were absorbed. This batch adds Discord command-quota startup guard and Weixin OC session timeout cleanup. | Evaluate KOOK/Dingtalk/Feishu as feature work after stability fixes. |
| Dashboard UX and WebUI | In progress | IME Enter, console layout, provider config UI, inline edit/regenerate, plugin UI, Noto Sans Cyrillic support, initial password UX | IME, console, upload sanitization, and provider test feedback were absorbed. Inline edit/regenerate remains intentionally deferred. | Review Noto Sans/font stack and initial password UX because they are low-risk user-facing polish. |
| Plugin system | In progress | Plugin pages, plugin i18n, plugin changelogs/update system, plugin storage downloads, install cleanup | Basic `pages` metadata and install cleanup were absorbed. | Review dynamic plugin API routes and plugin update/changelog/storage changes as one feature batch. |
| Knowledge base and retrieval | Deferred | FTS5 sparse retrieval, EPUB upload, blank-prompt KB retrieval skip, Firecrawl search tools | Firecrawl config/tool hook had been absorbed in the earlier review; FTS5/EPUB remain deferred due storage/retrieval impact. | Review blank-prompt skip as a small bugfix; keep FTS5/EPUB as a dedicated migration task. |
| Computer use / sandbox | In progress | Shipyard profile selection, readiness gate, idle sandbox expiry, sandbox image download delivery | Explicit/auto Shipyard profile behavior was absorbed. | Review readiness gate, graceful cleanup, idle expiry, and sandbox image download behavior together. |
| Auth, CLI, deployment, update | Not started | Initial dashboard password env var, legacy password messages, update progress dialog, deploy scripts | Not yet absorbed in this pass. | Review auth/CLI/deploy as a separate operational batch. |
| Docs, version bumps, dependency chores | Mostly skipped | Version bumps, README/docs URL updates, pnpm action bumps, release instructions | Usually skipped unless they affect this fork's docs or release process. | Keep version/chore commits out of functional sync unless preparing a release. |

### Review Rules for Future Upstream Sync

- Always update this `Dynamic Sync Board` before and after a sync batch.
- Record the exact upstream ref used. If fetch fails, record that and use the latest local snapshot explicitly.
- Prefer small topic batches over broad merges.
- Mark each upstream topic as `Absorbed`, `Deferred`, `Skipped`, or `Needs review`.
- For rewritten merges, record the local commit hash and the upstream commit or PR that inspired it.
- Do not treat `git cherry` as authoritative for this fork; use it only as a triage aid.
- Keep local prompt, memory, postprocess, and interaction architecture as the default source of truth unless an upstream change is explicitly chosen to replace it.

## How to Update

- Add a new dated section for each upstream review.
- Record the upstream ref or tag that was reviewed.
- Separate decisions into `Not Merged`, `Deferred`, and `Already Absorbed`.
- For each skipped item, include the reason and the condition that would make it worth revisiting.
- Prefer linking to commit hashes, PR numbers, or file paths when available.

## 2026-04-27 Upstream Review

Reviewed upstream: `upstream/master` at `67c7445d` (`v4.23.6`)

Local strategy:

- Preserve the local prompt and memory architecture as the source of truth.
- Absorb upstream fixes only when they can fit this architecture with small, isolated changes.
- Do not merge changes that delete or overwrite the local `ContentPack -> Selector -> Render -> ProviderRequest` flow.

Already absorbed locally:

- Version bump to `4.23.6`.
- Firecrawl web search configuration and main-agent tool injection.
- SSL context compatibility using system trust store plus certifi fallback.
- OpenAI-compatible provider fixes for empty assistant messages, streaming sanitization, reasoning empty-string handling, and DeepSeek v4 reasoning history.
- Tool-loop reasoning preservation for empty reasoning content.
- Chat token stats display alignment: cached input tokens are shown separately from uncached input tokens.
- Existing local coverage already includes several upstream fixes: upload filename path traversal protection, backup importer path traversal protection, T2I raw text rendering, IME Enter handling, MiniMax WAV output, OpenRouter reasoning key, rate-limit count zero handling, RegexFilter pattern support, Telegram media group error handling, sandbox image download delivery, and SendMessageToUser workspace-relative file resolution.

### Not Merged

#### Prompt, Memory, and Postprocess Removals

Upstream deletes or reverts large local systems under:

- `astrbot/core/prompt/**`
- `astrbot/core/memory/**`
- `astrbot/core/postprocess/**`
- prompt-extension registration in `astrbot/core/star/context.py`
- memory lifecycle registration in `astrbot/core/core_lifecycle.py`

Reason:

These files are part of the local prompt/memory/postprocess architecture. Taking the upstream deletion would remove local context collection, prompt rendering, selector integration, memory services, prompt extension hooks, and after-send postprocess hooks.

Revisit if:

Upstream later introduces an equivalent or better architecture that can preserve local behavior, or if this fork intentionally drops the local prompt/memory system.

#### `astrbot/core/astr_main_agent.py` Wholesale Merge

Reason:

The upstream version removes local prompt pipeline integration, including `collect_context_pack`, `PromptRenderEngine`, `prompt_selector`, apply-visible/shadow pipeline modes, prompt trace extras, cached image/file extraction hooks, scaffold-free conversation save, and KB retrieval cache usage.

Local action taken:

Only the Firecrawl tool hook was cherry-picked into the local implementation.

Revisit if:

There is a specific independent bug fix in this file that can be extracted without changing the local prompt pipeline.

#### `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py` Wholesale Merge

Reason:

The upstream version removes final prompt trace logging, removes clean conversation-save user message replacement, and drops `prompt_selector` from `MainAgentBuildConfig`. This conflicts with the local prompt audit and selector work.

Revisit if:

There is a narrow runtime bug fix that does not affect final prompt tracing, conversation persistence, or selector config propagation.

#### WebUI Inline Edit, Regenerate, and Thread Flow

Main affected areas:

- `astrbot/dashboard/routes/chat.py`
- `astrbot/dashboard/routes/live_chat.py`
- `dashboard/src/components/chat/Chat.vue`
- `dashboard/src/components/chat/ChatInput.vue`
- `dashboard/src/components/chat/MessageList.vue`
- `dashboard/src/composables/useMessages.ts`

Reason:

The upstream implementation is a large feature set around editing, regeneration, threads, checkpoint IDs, and WebUI history mutation. It conflicts with the local checkpoint/message format decisions and local attachment rendering work. A partial type-only compatibility fix was kept, but the feature itself was not merged.

Revisit if:

The fork decides to implement inline edit/regenerate/thread UX explicitly. At that point, design it against the local checkpoint and prompt pipeline semantics instead of taking the upstream patch wholesale.

#### ChatPoint / Checkpoint Formatting-Only Diff

Main affected areas:

- `astrbot/core/agent/message.py`
- `tests/test_conversation_checkpoint.py`

Reason:

The remaining upstream diff is mostly formatting or test-only churn around checkpoint message dumping. It does not provide enough behavior value to justify merging over local checkpoint semantics.

Revisit if:

Future upstream checkpoint work fixes a real behavior bug or adds a compatible checkpoint API.

#### Dependency Removals

Main affected file:

- `pyproject.toml`

Reason:

Upstream removes dependencies that are still relevant to this fork, including local provider and media features. Local policy for this review was to keep dependency declarations unchanged except for the version bump.

Revisit if:

A dependency is proven unused in this fork after checking provider registration, media/TTS/STT paths, and optional feature gates.

#### Volcengine Ark Provider Removal

Main affected areas:

- `astrbot/core/provider/sources/volcengine_ark_source.py`
- provider config defaults and dashboard provider-source mapping

Reason:

This fork keeps the Volcengine Ark compatibility work. Upstream removes the provider path, which would be a feature regression locally.

Revisit if:

The provider is replaced by a cleaner OpenAI-compatible path that fully covers local Doubao/Volcengine image and request-format behavior.

#### QQ Official Message-Level Markdown Control

Main affected areas:

- `astrbot/core/message/message_event_result.py`
- `astrbot/core/pipeline/respond/stage.py`
- `astrbot/core/platform/sources/qqofficial/qqofficial_message_event.py`

Reason:

This is a useful platform feature, but it touches message chain semantics and respond-stage behavior. The local respond stage also includes postprocess dispatch, so this should not be merged opportunistically.

Revisit if:

There is a platform-specific need for per-message markdown control. Merge as a dedicated feature with tests covering postprocess hooks and non-QQ platform behavior.

#### KOOK Role Mention Support

Main affected areas:

- `astrbot/core/platform/sources/kook/**`
- `tests/test_kook/**`

Reason:

The feature is large and platform-specific. It is potentially valuable, but it requires a dedicated KOOK adapter review and test run because it changes role caching, event parsing, message conversion, and test fixtures.

Revisit if:

KOOK role mention support is needed by users of this fork. Merge as an isolated platform feature.

#### Knowledge Base FTS5 and EPUB Support

Main affected areas:

- `astrbot/core/db/vec_db/faiss_impl/document_storage.py`
- `astrbot/core/knowledge_base/retrieval/**`
- `astrbot/core/knowledge_base/parsers/**`
- dashboard knowledge-base upload UI

Reason:

Both are valuable knowledge-base features, but they touch retrieval/storage behavior and dependencies. They should be evaluated separately from prompt pipeline merging, especially because the fork already has local KB caching in prompt collection.

Revisit if:

Knowledge-base retrieval quality or EPUB upload support becomes a current priority. Merge with storage migration and retrieval tests.

#### `/stats` Command and WebUI Stats Feature Expansion

Main affected areas:

- `astrbot/builtin_stars/builtin_commands/commands/conversation.py`
- `astrbot/builtin_stars/builtin_commands/main.py`
- dashboard stats components and i18n

Reason:

Part of this feature already exists locally. The remaining upstream changes are useful but not urgent. The only small compatible display correction was absorbed.

Revisit if:

Conversation-level token usage command behavior needs to be aligned with upstream or exposed more clearly in the WebUI.

#### Clipboard Utility and Provider Config UI Refactors

Main affected areas:

- `dashboard/src/utils/clipboard.ts`
- provider-source UI composables and config UI files

Reason:

These are frontend quality-of-life refactors with conflict risk against local WebUI work. They are not required for the prompt/memory merge goal.

Revisit if:

The dashboard has copy-action bugs or provider config usability becomes a priority.

#### Test-Only and Fixture-Only Diffs

Examples:

- `tests/unit/test_upload_filename_sanitization.py`
- `tests/test_kook/data/kook_ws_event_group_message_with_mention.json`
- selected checkpoint and upload tests

Reason:

Some tests duplicate behavior already covered locally, while others depend on features intentionally not merged. They should not be added unless the corresponding behavior is merged.

Revisit if:

The related production code is merged or changed locally.
