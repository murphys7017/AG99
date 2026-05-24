# Upstream Merge Ledger

This document records upstream changes that were reviewed but not merged into this fork.
Keep appending to it when reviewing future upstream updates, so old merge decisions remain easy to revisit.

## Dynamic Sync Board

Last updated: 2026-05-24

Current comparison baseline:

- Local branch: `master`
- Upstream remote: `upstream` (`https://github.com/AstrBotDevs/AstrBot`)
- Last local upstream snapshot checked: `upstream/master` at `ff28eca9c` (`fix(openai): 修复流式响应末尾usage信息丢失问题 (#8306)`)
- Remote refresh status: HTTPS `git fetch upstream --prune` and `git ls-remote upstream refs/heads/master` failed on 2026-05-24 with `Failed to connect to github.com port 443 after 21110 ms`; SSH `git ls-remote git@github.com:AstrBotDevs/AstrBot.git refs/heads/master` succeeded and confirmed `ff28eca9c`, then `upstream/master` was refreshed from the SSH URL.
- Git-only divergence at this snapshot: local-only `286`, upstream-only `194`.
- Patch-equivalence estimate from `git cherry`: `67` upstream commits appear already absorbed, `127` still appear unabsorbed.

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
- `f1392a71` Absorb simple upstream stability updates
- `08144459` Absorb upstream requery and upload format updates
- `aee09530` Prefer bundled dashboard when `data/dist` is stale
- Pending commit in current sync batch: WebUI font stack and outlined action visibility polish.

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
  - Chat upload filename sanitization, IME Enter guard, provider status test error display, console auto-scroll sync, console log layout improvements, and bundled dashboard fallback when `data/dist` is stale.
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
- 2026-05-18 small safety/stability review found these upstream fixes already present in this fork:
  - T2I template SSTI validation and template editor error feedback.
  - Gemini chat provider managed `httpx.AsyncClient`.
  - GitHub dashboard fallback download URL using `AstrBot-{tag}-dashboard.zip`.
  - Baidu web search API key hidden unless web search is enabled.
- 2026-05-24 simple-candidate review found these upstream fixes already present in this fork:
  - SOCKS-compatible updater downloads via `httpx` with `trust_env=True`.
  - MCP input schema normalization for property-level boolean `required` fields.
  - `RegexFilter` accepts both strings and compiled `re.Pattern` instances.
  - `rate_limit_count <= 0` no longer crashes rate-limit checking.
  - Cron run timestamps are serialized with timezone information.
  - Bocha search disables brotli response encoding for aiohttp compatibility.
  - QQ Official API retries transient OS/timeout errors.
  - Platform adapter filter types include newer adapters such as `webchat`, `weixin_oc`, and webhook variants.
  - Metrics upload can be disabled by config or `ASTRBOT_DISABLE_METRICS`.
  - Video attachments, oversized image compression, `None` system prompts, WeCom duplicate text events, file-read modalities, and register decorator kwargs fixes were already present.

### Topic Merge Plan

Use this table as the live working plan. Update `Status`, `Local action`, and `Next check` whenever upstream sync work is done.

| Topic | Status | Upstream examples | Local action | Next check |
| --- | --- | --- | --- | --- |
| Security fixes | In progress | Upload path traversal, backup importer traversal, password policy, updater zip root path, T2I SSTI validation | Upload filename sanitization, backup-importer handling, updater zip-root handling, and T2I template validation are already present. Password policy remains deferred because upstream `7ddf6371` is a broad auth/onboarding/storage migration, not a small patch. | Review password setup/password hashing as a dedicated auth migration batch. |
| Provider and model runtime | In progress | OpenAI http client, reasoning content, Claude no-arg tools, MiniMax TTS, Embedding providers, Anthropic compatibility | Several small compatibility fixes were rewritten locally. This batch adds shared empty-assistant sanitization to both non-streaming and streaming OpenAI-compatible requests. | After commit, re-run `git cherry` triage and review provider warning/default-model edge cases only if behavior differs locally. |
| Platform adapters and outbound media | Mostly absorbed | Active reply images, Weixin OC send failures/session timeout, Telegram media group errors, Discord startup quota, KOOK role mentions, QQ Official markdown/send fixes, Dingtalk/Feishu QR setup | Active reply image, SILK, Weixin OC send failure/session timeout, Telegram media group logging, Discord command-sync quota handling, QQ Official markdown/active-push fixes, KOOK role mentions, message-tool path handling, and Dingtalk/Lark/Weixin OC one-click QR registration were absorbed. | Re-check only when new upstream platform adapter commits appear; remaining platform `git cherry` positives in this snapshot are rewrite-equivalent. |
| Dashboard UX and WebUI | In progress | IME Enter, console layout, provider config UI, inline edit/regenerate, plugin UI, Noto Sans Cyrillic support, initial password UX, stale `data/dist` fallback | IME, console, upload sanitization, provider test feedback, provider config panel/model-add flow, T2I template error feedback, Baidu search-key visibility, bundled dashboard fallback for stale `data/dist`, Noto Sans Cyrillic font stack, and always-visible outlined action buttons were absorbed. Inline edit/regenerate remains intentionally deferred. | Review initial password UX because it has backend auth-policy implications; keep larger plugin/dashboard features separate. |
| Plugin system | In progress | Plugin pages, plugin i18n, plugin changelogs/update system, plugin storage downloads, install cleanup | Basic `pages` metadata and install cleanup were absorbed. | Review dynamic plugin API routes and plugin update/changelog/storage changes as one feature batch. |
| Knowledge base and retrieval | Deferred | FTS5 sparse retrieval, EPUB upload, blank-prompt KB retrieval skip, Firecrawl search tools | Firecrawl config/tool hook had been absorbed in the earlier review; FTS5/EPUB remain deferred due storage/retrieval impact. | Review blank-prompt skip as a small bugfix; keep FTS5/EPUB as a dedicated migration task. |
| Computer use / sandbox | In progress | Shipyard profile selection, readiness gate, idle sandbox expiry, sandbox image download delivery | Explicit/auto Shipyard profile behavior was absorbed. | Review readiness gate, graceful cleanup, idle expiry, and sandbox image download behavior together. |
| Auth, CLI, deployment, update | Partially absorbed | Initial dashboard password env var, legacy password messages, update progress dialog, deploy scripts, Dingtalk/Lark/Weixin OC QR registration | Platform QR registration (Dingtalk/Lark/Weixin OC) and update progress tracking/dialog were absorbed. Dashboard password policy and deploy scripts remain not started. | Review auth/password and deploy scripts as separate operational batches. |
| Docs, version bumps, dependency chores | Mostly skipped | Version bumps, README/docs URL updates, pnpm action bumps, release instructions | Usually skipped unless they affect this fork's docs or release process. | Keep version/chore commits out of functional sync unless preparing a release. |

### Review Rules for Future Upstream Sync

- Always update this `Dynamic Sync Board` before and after a sync batch.
- Record the exact upstream ref used. If fetch fails, record that and use the latest local snapshot explicitly.
- Prefer small topic batches over broad merges.
- Mark each upstream topic as `Absorbed`, `Deferred`, `Skipped`, or `Needs review`.
- For rewritten merges, record the local commit hash and the upstream commit or PR that inspired it.
- Do not treat `git cherry` as authoritative for this fork; use it only as a triage aid.
- Keep local prompt, memory, postprocess, and interaction architecture as the default source of truth unless an upstream change is explicitly chosen to replace it.

## 2026-05-24 Upstream Snapshot Refresh

Reviewed upstream delta: `dceacd5a..ff28eca9c`.

Remote status:

- HTTPS `git fetch upstream --prune` failed during this review due GitHub connection timeout.
- HTTPS `git ls-remote upstream refs/heads/master` also failed due GitHub connection timeout.
- SSH `git ls-remote git@github.com:AstrBotDevs/AstrBot.git refs/heads/master` succeeded and confirmed `ff28eca9c`.
- `upstream/master` was refreshed from `git@github.com:AstrBotDevs/AstrBot.git` and remained at `ff28eca9c`.

Updated triage counts:

- Git-only divergence: local-only `286`, upstream-only `194`.
- `git cherry -v master upstream/master`: `67` patch-equivalent absorbed, `127` still shown as unabsorbed.
- Interpretation remains unchanged: `+` entries may still be functionally absorbed by local rewrites, so topic review is required before merging or rewriting.

Phase-B sync strategy:

- First planned pseudo-merge boundary: `67c7445d` (`v4.23.6`).
- Rationale: this boundary already has a dedicated 2026-04-27 review, and the remaining `git cherry` positives before it are now classified below.
- Do not pseudo-merge newer upstream history until each later boundary receives the same explicit classification.

### Phase B Boundary: `67c7445d` / `v4.23.6`

Remaining `git cherry +` entries up to this boundary were reviewed for whether a staged `git merge -s ours 67c7445d` would hide unprocessed work.

| Upstream commit | Topic | Phase-B status | Notes |
| --- | --- | --- | --- |
| `1199b704` | KOOK role mentions | Absorbed | Confirmed in local KOOK adapter, role cache, data models, and tests. |
| `ba1e2223` | Video attachment handling for LLM | Absorbed | Direct and quoted video attachments are already appended as text references. |
| `e6b68e9b` | FileReadTool description and modality checking | Absorbed | Local file-read/tool-result modality handling already covers image/PDF/docx-style outputs. |
| `36d6f3b` | WebUI inline edit/regenerate/thread flow | Deferred | Local ChatUI already has edited/regenerate/checkpoint paths, but upstream is a broad DB/history/thread rewrite. Keep as an intentional later feature review instead of importing before the first pseudo-merge. |
| `0748f0a` | Attachment previews and file signatures | Absorbed | Local `useMediaHandling`, chat components, and docs include previews, attachment IDs, and duplicate/signature checks. |
| `bb6619f` | Tool-call/reasoning display improvements | Absorbed/Deferred | Local ChatUI has reasoning panels, tool-call blocks, and message stats. Upstream's broader thread/live-chat rewrite remains tied to the deferred inline-edit batch. |
| `aaec41e` | Upload path traversal | Absorbed | Local upload filename sanitization/path traversal protection was previously confirmed. |
| `8d9ae55` | Clipboard utility and dialog copy fallbacks | Absorbed | Local `dashboard/src/utils/clipboard.ts` and copy actions include secure-context fallback behavior. |
| `f0a1dd7` | Provider config UI | Absorbed | Local provider panels/workspace and model-add flow were rewritten and completed. |
| `5d79c999` | WeCom duplicate text suppression | Absorbed | Local WeCom path has per-session duplicate text suppression. |
| `5ce02da` | Certifi SSL context on Windows | Absorbed | Local SSL compatibility uses system trust store plus certifi fallback. |
| `d4cdeeae` | Sandbox image download delivery | Absorbed | Local sandbox image downloads are delivered as images. |
| `17aea1aa` | Firecrawl web search tools | Absorbed | Local provider settings and main-agent tool hook include Firecrawl. |
| `55c15586` | Empty-assistant filter in streaming OpenAI path | Absorbed | Rewritten locally in the OpenAI-compatible streaming path. |
| `d16ed4e5` | OpenRouter reasoning key override | Absorbed | Already present locally. |
| `3c1d0cd` | MiniMax WAV default output | Absorbed | Local MiniMax/TTS media handling covers WAV/AMR fixes. |
| `bbda1e67` | Oversized image downscale | Absorbed | Already present locally. |
| `07b37b98` | DeepSeek v4 empty reasoning | Absorbed | Already present locally. |
| `415da218` | Empty-string reasoning content | Absorbed | Already present locally. |
| `c5ab4f72` | `/stats` conversation token command | Absorbed | Local builtin conversation command registers `/stats` and reports token totals. |
| `1efe4fd6` | Stats TPM output-token counting | Absorbed | Local stats dashboard separates output-token totals and token trend data. |
| `09ab45fc` | Version bump to `4.23.6` | Skipped | Version churn is not used as functional sync proof for this fork. |
| `72f4e748` | T2I raw text template rendering | Absorbed | Local T2I rendering fix was already confirmed. |
| `b711425b` | QQ Official message-level Markdown | Absorbed | Confirmed in `MessageChain`, respond stage metadata propagation, and QQ Official send path. |
| `72d65680` | Pre-commit setup docs and minor component table text | Skipped | Development-process docs/chore; not required for runtime sync. |
| `67c7445d` | IME Enter guard | Absorbed | Already present in customized ChatUI input handling. |

New upstream commits since previous ledger baseline:

| Upstream commit | Topic | Initial status | Notes |
| --- | --- | --- | --- |
| `5bbcdced` | Skip empty LLM summaries | Absorbed | Rewritten locally in `LLMSummaryCompressor`; empty or whitespace-only summaries now keep original history and log a warning. |
| `de0a7afd` | pnpm action bump | Skipped | CI dependency chore; skip unless preparing CI/release maintenance. |
| `7a9fb33d` | FAQ typo | Skipped | Docs-only upstream typo fix. |
| `c4693fa6` | RST/ADOC knowledge uploads | Absorbed | Rewritten locally as a narrow upload/parser whitelist update using existing `MarkitdownParser`, with WebUI accept/i18n/icon hints. |
| `16593354` | Automated MDI subset generation | Absorbed | Rewritten locally; dashboard dev/build scripts now generate the MDI subset before Vite, and generated subset assets are ignored instead of tracked. |
| `d15606d2` | Dashboard password CLI command | Deferred | Auth/CLI surface; keep with the existing auth/password migration batch. |
| `3290d755` | Prefer bundled dashboard over stale `data/dist` | Absorbed | Rewritten locally; version comparison now selects bundled WebUI when an older user `data/dist` would otherwise shadow it. |
| `587286a9` | Warn when default chat provider is unset or invalid | Absorbed | Already present locally before this batch; lifecycle resets the warning guard after provider reload and warns on missing/invalid default provider ID. |
| `1a030634`/`b3381c64` | Noto Sans Cyrillic WebUI font stack | Absorbed | Rewritten locally with Noto Sans loaded from Google Fonts, global body stack set to Outfit/Noto Sans before CJK fallbacks, and `.Outfit` sharing that stack. |
| `f9cbe790` | Always show outlined action buttons | Absorbed | Rewritten locally by removing default hidden opacity from `OutlinedActionListItem` hover actions. |
| `5be6536f` | SOCKS proxy updater support | Absorbed | Already present locally; updater requests use `httpx.AsyncClient(trust_env=True)` and cleanup partial downloads on failure. |
| `43989471` | Normalize malformed MCP required flags | Absorbed | Already present locally in `_normalize_mcp_input_schema` with unit coverage. |
| `662b1d36` | `RegexFilter` accepts compiled patterns | Absorbed | Already present locally; `regex_str` is derived from the compiled pattern. |
| `29a449f9` | `rate_limit_count=0` guard | Absorbed | Already present locally; rate-limit loop exits when count is non-positive. |
| `47f78be3` | Cron local/timezone display fix | Absorbed | Already present locally; cron next-run and route serialization attach UTC timezone information. |
| `b2a95713` | Bocha brotli workaround | Absorbed | Already present locally; Bocha requests set `Accept-Encoding: gzip, deflate`. |
| `00ebebb1` | QQ Official transient retry expansion | Absorbed | Already present locally; retries include `OSError` and `asyncio.TimeoutError` with five attempts. |
| `2f479b52` | Missing platform adapter filter types | Absorbed | Already present locally; newer adapter names are mapped and `ALL` short-circuits. |
| `e8d3e183` | Disable metrics config | Absorbed | Already present locally in default config, config metadata i18n, and `Metric._is_disabled()`. |
| `ba1e2223` | Video attachment handling for LLM | Absorbed | Already present locally; direct and quoted video attachments are appended as text references. |
| `bbda1e67` | Downscale oversized images | Absorbed | Already present locally; images exceeding max edge compress even when file size is below threshold. |
| `433836d9` | Guard `None` system prompts | Absorbed | Already present locally before persona/skills prompt append paths. |
| `5d79c999` | WeCom duplicate text message suppression | Absorbed | Already present locally with a 15-second per-session text dedup cache. |
| `e6b68e9b` | File-read/modality handling | Absorbed | Already present locally; file read description and modality sanitization are implemented in the runner/provider utility path. |
| `094aef62` | Preserve decorator kwargs in register helpers | Absorbed | Already present locally for platform adapter type, regex, and permission decorators. |
| `0711172f` | Stale command hints | Absorbed | Removed stale slash-command hints in active-reply/T2I/tool-call warning paths and points users to WebUI instead. |
| `3f20bbdf` | T2I Shiki issue | Absorbed | Rewritten locally; Shiki runtime template preparation now runs in the executor to avoid blocking the event loop. |
| `1e48bab5` | Streaming `delta=None` handling | Absorbed | Rewritten locally in OpenAI streaming path; skips `delta=None` state updates and guards final completion extraction. |
| `f5bd4f30` | Preserve original `completion_text` in skills-like tool re-query | Absorbed | Rewritten locally; second-stage skills-like re-query now updates tool-call fields without replacing already-visible assistant text. |
| `fd4fe843` | Docs fix | Skipped | Docs-only unless it affects local Yakumo docs. |
| `dcc99e6b` | ChatUI command suggestions | Absorbed | Rewritten locally against the customized ChatUI; slash-command suggestions load from `/api/commands`, composer focus is restored after chat actions, and provider-selection failures now send a visible LLM error message. |
| `ff28eca9` | OpenAI streaming usage preservation | Absorbed | Rewritten locally; final usage chunks with `choices=[]` are still passed to stream state. |

Simple batch review:

- `7a519d4d` (`websearch_firecrawl_key`) was already absorbed before this batch; `provider_settings.websearch_firecrawl_key` exists in `DEFAULT_CONFIG`.
- `22ba831a` (`send_message_to_user` missing local/sandbox path handling) was already absorbed before this batch; missing paths stop message construction before send.
- `720d384b` (console auto-scroll ref synchronization) was already absorbed before this batch; `ConsolePage` initializes `ConsoleDisplayer.autoScroll` on mount and keeps it synced.

Medium batch rewrite:

- `dcc99e6b` (ChatUI command suggestions) absorbed by adding a local `CommandSuggestion` composer panel, preserving the customized chat layout and adding visible provider-selection error replies.
- `37142fd2` (update progress tracking) absorbed by threading download progress callbacks through core/WebUI downloads, adding `/api/update/progress`, and showing per-stage progress inside the existing update dialog.
- `16593354` (automated MDI subset generation) absorbed by running the subset generator in dashboard dev/build scripts and removing generated subset assets from version control.

Simple follow-up review:

- `fbe9a38c`/`fd2ca702` (dark-mode code blocks inside list items) were already present locally in `ThemeAwareMarkdownCodeBlock`.
- `dd716e61` (thinking/response separator) was already present locally in result decoration.
- `c9182c27` (console log level alignment and mobile layout) was already present locally in `ConsoleDisplayer`.
- `224915fb` (plugin publishing 16MB size limit docs) was already present locally in English and Chinese docs.
- `f86de988` (Discord command-sync daily quota handling) was already present locally in the Discord adapter.
- `0830f48a`/`718449d6` path-conflict self-healing and GitHub dashboard fallback download behavior were already present locally.
- `35f5d7e` (AMR audio quality and opus conversion simplification) was absorbed by routing opus conversion through `convert_audio_format` and adding AMR ffmpeg filters.
- `f6a99a25` (SiliconFlow knowledge-base API key wording) was absorbed as a docs-only correction.
- README-only Trendshift/contributor image updates remain skipped for this fork because they only affect upstream repository presentation.

Platform adapter review:

- Reviewed upstream platform-related commits still shown as `git cherry` positives against `ff28eca9c`: `1199b704` (KOOK role mentions), `b711425b` (QQ Official message-level Markdown control), `f86de988` (Discord command-sync quota), `094c2de8` (Weixin OC media send failures), `6982ef7d` (Weixin OC session timeout), `aace90da`/`c88025c2`/`a1e95081` (Lark/Dingtalk/Weixin OC QR registration and random ID suffix).
- `1199b704` is functionally absorbed locally. KOOK has role cache support, `(rol)` parsing, role-update system-event cache invalidation, `user/me` and `user/view` response models, and matching `tests/test_kook` fixtures/tests.
- `b711425b` is functionally absorbed locally. `MessageChain` carries `use_markdown_`, `derive()` preserves metadata through respond-stage splitting, and QQ Official switches to plain text when `use_markdown(False)` is set.
- `f86de988`, `094c2de8`, and `6982ef7d` are functionally absorbed locally. Discord startup survives command-sync daily quota (`30034`), Weixin OC raises on failed outbound media/text segments, and Weixin OC clears login/account state on session timeout.
- Lark/Dingtalk/Weixin OC one-click registration and the random platform ID suffix were already absorbed in the 2026-05-18 platform QR registration merge.
- Additional platform-adapter fixes confirmed present locally: `489e2a33` shared AppID hint text, `ffc31b30` QQ Official private active push without cached `msg_id`, `00ebebb1` QQ Official transient retries, `2f479b52` platform adapter filter types, and `e98eb92b` Telegram media-group scheduled job error logging.

Provider/WebUI experience review:

- Reviewed upstream `f0a1dd79` (provider config UI improvement) and `750597d` (model-add flow improvement).
- The broad provider config UI from `f0a1dd79` was already mostly rewritten locally: `ProviderChatCompletionPanel`, provider-source/model split panels, ChatUI `/models` workspace entry, provider config dialog wrapper, capability badges, and responsive provider page layout are present. The remaining unrelated ChatUI/layout/font subset churn was not re-applied.
- Completed the remaining `750597d` flow by wiring both `ProviderPage` and `ProviderChatCompletionPanel` to the existing `useProviderModelConfigDialog` composable. Clicking an available model now opens the model config dialog before saving, uses the narrowed model schema, and shares the same add/edit save path.
- Added a bounded scroll area for the available-model list to keep large provider model catalogs from expanding the provider page indefinitely.

## 2026-05-18 Platform QR Registration Merge

Reviewed upstream commits: `c88025c2` (dingtalk QR registration), `aace90da` (feishu/lark QR registration), `b991e819` (weixin_oc QR login), `8dde2292` (lark bot info), `a1e95081` (random suffix for weixin/dingtalk id).

### Absorbed

#### Dingtalk One-Click QR Registration

- New file: `astrbot/core/platform/sources/dingtalk/app_registration.py`
- Implements device code flow: init → begin → poll
- Returns `client_id` + `client_secret` on success
- Dashboard endpoint: `POST /api/platform/registration/dingtalk`

#### Lark/Feishu One-Click QR Registration

- New file: `astrbot/core/platform/sources/lark/app_registration.py`
- Supports Feishu (China) and Lark (Global) with automatic domain resolution
- Device code flow with `client_secret` fallback polling for Lark
- Returns `app_id` + `app_secret` + `tenant_brand` + `domain` on success
- Dashboard endpoint: `POST /api/platform/registration/lark`

#### Lark Bot Info Retrieval

- New file: `astrbot/core/platform/sources/lark/bot_info.py`
- After QR registration success, fetches bot name and open_id via tenant access token
- Integrated into lark registration handler for enriched response

#### Weixin OC QR Login

- New file: `astrbot/core/platform/sources/weixin_oc/login_registration.py`
- Uses existing `WeixinOCClient` for API calls
- Returns `weixin_oc_token` + `account_id` + `base_url` + `user_id` on success
- Dashboard endpoint: `POST /api/platform/registration/weixin_oc`

#### Dashboard Registration Endpoint

- Modified: `astrbot/dashboard/routes/platform.py`
- Added `POST /api/platform/registration/<platform_type>` endpoint
- Three handler methods: `_handle_lark_registration`, `_handle_dingtalk_registration`, `_handle_weixin_oc_registration`
- Random 4-letter suffix appended to platform ID on success (prevents duplicate IDs)

#### Frontend Registration Component

- New file: `dashboard/src/components/platform/PlatformRegistrationAction.vue`
- QR code display with polling state machine (idle → starting → pending → created/error)
- Auto-fills platform config credentials on success
- **Bug fix vs upstream**: local `QrCodeViewer` always re-encodes input as QR code via `QRCode.toDataURL()`. Weixin OC returns a base64 PNG image directly. Added `isBase64Image` computed property to bypass `QrCodeViewer` for base64 images.

#### Frontend AddNewPlatform Integration

- Modified: `dashboard/src/components/platform/AddNewPlatform.vue`
- Lark/Dingtalk: scan vs manual creation mode radio switch
- Weixin OC: direct QR display (no manual mode)
- `canSave` requires mode selection for lark/dingtalk; requires `weixin_oc_token` for weixin_oc
- `handlePlatformRegistrationCreated` appends suffix to platform ID on success
- `resetForm` clears creation modes

#### i18n

- Modified: `dashboard/src/i18n/locales/zh-CN/features/platform.json`
- Modified: `dashboard/src/i18n/locales/en-US/features/platform.json`
- Modified: `dashboard/src/i18n/locales/ru-RU/features/platform.json`
- Added `registrationAction` section with status texts, mode labels, and per-platform titles

### Not Merged

#### Test Files

- `tests/test_dingtalk_app_registration.py`
- `tests/test_lark_app_registration.py`
- `tests/test_weixin_oc_login_registration.py`
- `tests/test_discord_command_sync.py`
- `tests/test_cli_init.py`

Reason: Tests depend on upstream test fixtures and mock infrastructure that may not align with local test setup. Can be added later if test coverage becomes a priority.

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
