# Upstream Merge Ledger

This document records upstream changes that were reviewed but not merged into this fork.
Keep appending to it when reviewing future upstream updates, so old merge decisions remain easy to revisit.

## Dynamic Sync Board

Last updated: 2026-06-05

Current comparison baseline:

- Local branch: `master`
- Upstream remote: `upstream` (`https://github.com/AstrBotDevs/AstrBot`)
- Last local upstream snapshot checked: `upstream/master` at `1ad2b2c38` (`fix(core): avoid duplicate image insertion when re-querying an empty LLM response`)
- Remote refresh status: HTTPS `git fetch upstream --prune` succeeded on 2026-06-05 and moved `upstream/master` from `adae1f359` to `1ad2b2c38`.
- Git-only divergence at this snapshot: local-only `321`, upstream-only `193`.
- Patch-equivalence estimate from `git cherry`: `~120` upstream commits still appear unabsorbed after patch-equivalence filtering (40 new upstream commits since last refresh were triaged).

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
- `PENDING` Absorb v4.25.2 small/compatibility parity batch (20 small upstream commits).

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
- 2026-06-05 v4.25.2 small/compat parity batch (20 upstream commits rewritten locally):
  - `9fc03fa95` `astrbot/dashboard/routes/file.py`: removed useless no-op `pass` log statement; behavior unchanged.
  - `49036f8f9` `astrbot/core/tools/message_tools.py`: `SendMessageToUserTool` description now documents full tool semantics so the model can reliably distinguish tool-emitted vs user-emitted messages.
  - `92b2ce872` `.github/workflows/docker-image.yml`: bumped `docker/setup-qemu-action` from `v4.0.0` to `v4.1.0`; minor CI chore.
  - `fbc0633cd` zh terminology alignment: `astrbot/core/config/default.py` and the `zh-CN` config-metadata locale now use `词元（Tokens）` instead of `令牌` for `max_context_tokens`/`max_tokens` hints.
  - `61b6813dc` dashboard FAQ + download naming: `docs/{en,zh}/faq.md` and `astrbot/dashboard/routes/static_file.py` now point users at the real `AstrBot-{tag}-dashboard.zip` archive name instead of the non-existent `dist.zip`.
  - `6a467fc04` `astrbot/core/provider/sources/whisper_api_source.py`: `open(..., "rb")` for the temp audio file is now wrapped in a `with` context manager so the handle is closed even on error.
  - `d912e1497` `astrbot/core/utils/media_utils.py`: `ensure_wav` returns the original path immediately when the source file is missing, instead of letting `subprocess` raise an opaque `FileNotFoundError`. Fixed the failing `test_whisper_api_source.py::test_get_text_converts_opus_files_to_wav_before_transcription` test that depended on this guard.
  - `f01dc474e` `astrbot/core/provider/sources/gemini_embedding_source.py`: batch text inputs are now wrapped in `google.genai.types.Content(parts=[Part(text=...)])` to match the current Gemini SDK embedding API contract.
  - `072691877` `astrbot/core/provider/sources/openai_embedding_source.py`: when the configured model is not a Qwen embedding model, the unsupported `dimensions` parameter is dropped from the request to avoid 400 errors from non-Qwen OpenAI-compatible embedding endpoints (notably SiliconFlow).
  - `bd597859f` `astrbot/core/provider/sources/openai_source.py` + test: extracted `_IMAGE_FORMAT_MIME_TYPES`, `_detect_image_format`, `_image_format_to_mime_type`, and `_base64_image_ref_to_data_url` so that `base64://` references are wrapped as a data URL with the **actual** detected MIME (PNG/GIF/WebP/etc.) instead of always defaulting to `image/jpeg`. File-image references now go through the same detector with a `mode="safe"|"strict"` policy. `tests/test_openai_source.py` gains a focused PNG-preservation case. Invalid `base64://` content gracefully falls back to `image/jpeg` (preserving the historical calling contract), and invalid local files return `None`/`raise ValueError` per mode.
  - `7d45a247d` `astrbot/core/message/components.py` (Reply): `Reply.toDict()` now returns `{"type": "reply", "data": {"id": str(self.id)}}` per the OneBot V11 spec instead of leaking `_session_id` and `sender_id`. Added `tests/unit/test_aiocqhttp_reply.py` with three focused tests covering OneBot V11 format, `str(id)` coercion, and that `_session_id`/`sender_id` are not leaked.
  - `4bb1b897d` `astrbot/core/message/components.py` (Record): `Record._decode_file_uri` and `Record._resolve_file_source` are introduced so record components can decode `file://` URIs and fall back through `(file, url, path)` to a real local path. Fixed a latent `.jpg`→`.wav` extension bug introduced by the previous fallback chain.
  - `9a648eb42` `astrbot/core/platform/sources/wecom_ai_bot/wecomai_event.py`: added an opt-in `strip_result=True` parameter to `_extract_plain_text_from_chain`. Default remains `True`; streaming sites that need raw whitespace now pass `False`.
  - `e4044cc5a` `astrbot/core/astr_main_agent.py` + `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`: empty-message detection now also counts `event.message_obj.message` so reply/quoted messages can satisfy the gate and skip a no-op LLM call. Mirrors upstream's "re-query empty response" guard.
  - `1ad2b2c38` `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`: provider stats persistence now retries SQLite `OperationalError` (`database is locked`) with bounded exponential backoff (`PROVIDER_STATS_SQLITE_LOCK_RETRY_ATTEMPTS=3`, `PROVIDER_STATS_SQLITE_LOCK_RETRY_BASE_DELAY=0.2`), preventing occasional stats-write races from dropping usage records.
  - `4b097011c` `astrbot/core/astr_main_agent.py` + `astrbot/core/agent/runners/tool_loop_agent_runner.py`: empty `modalities=[]` is now treated identically to `None` (unconfigured) in five call sites (`_provider_supports_modality`, `_assemble_request_context_for_provider`, `_should_fix_modalities_for_provider`, `_func_tool_for_provider`, and the tool-loop cached-images check), so providers whose config was migrated to an empty list can still pass images, quoted images, and tools. Includes the upstream test fix in `tests/test_tool_loop_agent_runner.py` (`SimpleNamespace` mock now exposes `mime_type`).
  - `e5d7b4309` `astrbot/dashboard/routes/plugin.py`: `_apply_plugin_page_security_headers` skips `X-Frame-Options` and `frame-ancestors` only when `ASTRBOT_LAUNCHER` env is set to `1` or `true`, so the Tauri launcher can keep embedding plugin pages while standalone deployments keep the existing CSP posture.
  - `25b134444` `dashboard/src/views/ConsolePage.vue` + 3 i18n: replaced the inline `status` flash for pip-install results with `useToast`; added localized `installSuccess` / `installFailed` / `requestFailed` keys.
  - `d16e6a869` `dashboard/src/components/shared/ListConfigItem.vue`: the single-item setter no longer trims its value, so values like `"hello world"` or a single space are preserved; the existing watch filter still trims incoming entries so pure-whitespace values are still rejected.
  - `b0bb5c547` `dashboard/src/composables/useMessages.ts` + `ReasoningSidebar.vue` + `ReasoningBlock.vue` + 3 i18n: new `reasoningActivityCounts` / `reasoningActivityTitle` exports aggregate think + tool call counts; static `tm("reasoning.thinking")` titles are replaced with dynamic titles; added `thinkSummary` / `toolSummary` / `summarySeparator` i18n keys for en/zh/ru.
  - All 20 commits were rewritten locally to preserve Yakumo's prompt/memory/postprocess/interaction architecture. No upstream-cherry-pick was used. `git cherry` will still show these as `+` because patch contents differ; the functional absorption is recorded here.

### Topic Merge Plan

Use this table as the live working plan. Update `Status`, `Local action`, and `Next check` whenever upstream sync work is done.

| Topic | Status | Upstream examples | Local action | Next check |
| --- | --- | --- | --- | --- |
| Security fixes | In progress | Upload path traversal, backup importer traversal, password policy, updater zip root path, T2I SSTI validation | Upload filename sanitization, backup-importer handling, updater zip-root handling, T2I template validation, and the coordinated dashboard PBKDF2/setup migration are present. | Continue treating security changes as coordinated backend/WebUI migrations when protocol or storage contracts change. |
| Provider and model runtime | In progress | OpenAI http client, reasoning content, Claude no-arg tools, MiniMax TTS, Embedding providers, Anthropic compatibility | Several small compatibility fixes were rewritten locally. This batch adds shared empty-assistant sanitization to both non-streaming and streaming OpenAI-compatible requests. | After commit, re-run `git cherry` triage and review provider warning/default-model edge cases only if behavior differs locally. |
| Platform adapters and outbound media | Mostly absorbed | Active reply images, Weixin OC send failures/session timeout, Telegram media group errors, Discord startup quota, KOOK role mentions, QQ Official markdown/send fixes, Dingtalk/Feishu QR setup | Active reply image, SILK, Weixin OC send failure/session timeout, Telegram media group logging, Discord command-sync quota handling, QQ Official markdown/active-push fixes, KOOK role mentions, message-tool path handling, and Dingtalk/Lark/Weixin OC one-click QR registration were absorbed. | Re-check only when new upstream platform adapter commits appear; remaining platform `git cherry` positives in this snapshot are rewrite-equivalent. |
| Dashboard UX and WebUI | In progress | IME Enter, console layout, provider config UI, inline edit/regenerate, plugin UI, Noto Sans Cyrillic support, initial password UX, stale `data/dist` fallback | IME, console, upload sanitization, provider test feedback, provider config panel/model-add flow, T2I template error feedback, Baidu search-key visibility, bundled dashboard fallback for stale `data/dist`, Noto Sans Cyrillic font stack, always-visible outlined action buttons, and setup-page initial password UX were absorbed. Inline edit/regenerate remains intentionally deferred. | Keep larger plugin/dashboard features separate. |
| Plugin system | Mostly absorbed | Plugin pages, plugin i18n, plugin changelogs/update system, plugin storage downloads, install cleanup | Dynamic plugin Web API routing, plugin page i18n bridge/context, plugin changelog/readme surfaces, update download URLs, basic `pages` metadata, and install cleanup are absorbed. | Re-check only if later upstream adds new plugin runtime surfaces beyond the current page/API/update paths. |
| Knowledge base and retrieval | Mostly absorbed | FTS5 sparse retrieval, EPUB upload, blank-prompt KB retrieval skip, RST/ADOC upload, Firecrawl search tools | FTS5 sparse retrieval with BM25 fallback, EPUB parser/upload/read support, RST/ADOC upload support, blank-prompt KB retrieval skip, and Firecrawl config/tool hook are absorbed. | Re-check only if later upstream changes retrieval ranking/storage semantics or adds new document formats. |
| Computer use / sandbox | Mostly absorbed | Shipyard profile selection, readiness gate, idle sandbox expiry, CUA native upload, sandbox image download delivery | CUA runtime, native file upload/write fallback, idle timeout cleanup, WebUI idle-timeout config, explicit/auto Shipyard Neo profile behavior, readiness gate, stale sandbox cleanup, and sandbox image download delivery are absorbed. | Re-check only if later upstream changes sandbox lifecycle, capability contracts, or CUA SDK compatibility. |
| Auth, CLI, deployment, update | Mostly absorbed | Initial dashboard password env var, legacy password messages, update progress dialog, deploy scripts, Dingtalk/Lark/Weixin OC QR registration | Platform QR registration (Dingtalk/Lark/Weixin OC), update progress tracking/dialog, PBKDF2 dashboard password storage/setup flow, core and CLI initial password env support, `astrbot password`, and the final docs-public install scripts were absorbed. | Re-check only if later upstream changes install/update operational contracts. |
| Docs, version bumps, dependency chores | Mostly absorbed | Version bumps, README/docs URL updates, pnpm action bumps, release instructions | User-approved release-maintenance pass aligned core/CLI/package versions to `4.25.1`, added upstream changelogs through `v4.25.1`, refreshed README badge/contributor URLs where still stale, bumped pnpm/docker GitHub Actions, and updated source-deploy docs for generated initial passwords. | Keep broad docs translations and process-only docs separate unless the user asks for another docs sweep. |

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
| `09ab45fc` | Version bump to `4.23.6` | Absorbed/Superseded | Superseded by the user-approved `4.25.1` version alignment pass. |
| `72f4e748` | T2I raw text template rendering | Absorbed | Local T2I rendering fix was already confirmed. |
| `b711425b` | QQ Official message-level Markdown | Absorbed | Confirmed in `MessageChain`, respond stage metadata propagation, and QQ Official send path. |
| `72d65680` | Pre-commit setup docs and minor component table text | Skipped | Development-process docs/chore; not required for runtime sync. |
| `67c7445d` | IME Enter guard | Absorbed | Already present in customized ChatUI input handling. |

### Phase B Boundary: `afe99955` / `v4.24.0`

Remaining `git cherry +` entries from `v4.23.6` through this boundary were reviewed before considering a staged `git merge -s ours afe99955`.

| Upstream commit | Topic | Phase-B status | Notes |
| --- | --- | --- | --- |
| `cb5c172e` | CUA computer-use sandbox | Absorbed | Local CUA booter, config metadata, GUI tools, sandbox routing, and unit tests are already present. |
| `e218620a` | One-line deploy script | Absorbed | Final install script assets were absorbed as docs-public install helpers without importing the broad startup/config churn from the initial upstream patch. |
| `e4a9274b` | Deploy script path/gitignore fix | Absorbed | Covered by adding the final `docs/public/install.ps1` and `docs/public/install.sh` paths. |
| `9016a3b2` | pnpm action bump | Absorbed/Superseded | Superseded by the user-approved pnpm/action-setup `v6.0.7` alignment pass. |
| `4d9340c` | Windows/Linux deploy scripts | Absorbed | Added final Windows/Linux docs-public install scripts. |
| `1801834` | Remove BOM from install script | Absorbed | Absorbed by using the final upstream `install.ps1` content after the BOM removal. |
| `d8de0035` | Attachment saved events in WebChat/LiveChat | Absorbed | Rewritten locally in `chat.py` and `live_chat.py`; generated image/record/file/video attachments now emit `attachment_saved` with attachment ID/type before final message save. |
| `6b36e1ab` | Comment out `tool_choice="required"` in skills-like re-query | Skipped | Upstream appears to be a temporary provider-debug compatibility change. Local skills-like re-query intentionally keeps `required` semantics and has tests for the re-query path. |
| `eb69bf36` | Shipyard Neo readiness gate and cleanup | Absorbed | Local Shipyard Neo booter waits for ready status, deletes failed/timed-out sandboxes, and shuts down stale Neo sandboxes with `delete_sandbox=True`; tests cover these paths. |
| `587286a9` | Warn when default chat provider is unset | Absorbed | Already present locally in core lifecycle provider initialization. |
| `7c185f8e` | Plugin detail page | Absorbed | Local extension page includes `PluginDetailPage` and installed/market detail navigation. |
| `938c2417` | OpenAI SDK httpx alignment | Absorbed | Already present locally via provider SDK httpx module selection in proxy client creation. |
| `34dc91e4` | Skills WebUI editing and broad UI polish | Absorbed/Skipped | Skills routes and WebUI support list/upload/download/edit/delete plus Neo sync. Unrelated generated icon/font and broad layout churn remains skipped. |
| `d72cb78f` | Plugin pinning | Absorbed | Local extension preference storage and installed plugin tab pin/unpin ordering are present. |
| `2e49eb84` | Plugin internationalization | Absorbed | Plugin metadata includes i18n, dashboard config route exposes plugin i18n, and WebUI resolves localized plugin text. |
| `6f839173` | Plugin detail/install i18n experience | Absorbed | Local market/install/detail flow carries plugin i18n, pages, components, and localized display fields. |
| `a2335010` | Metrics batching/perf | Absorbed | Local metrics already batches counters, uploads asynchronously, stores platform stats, and supports config/env disabling. |
| `ac5cb9b5` | Official plugin storage downloads | Absorbed | Local plugin install/update routes pass `download_url`, updater supports archive downloads, and WebUI selects official storage URLs. |
| `bc1e7c95` | Plugin short description | Absorbed | Local plugin metadata/search/i18n handling includes short-description support. |
| `aa0b7a2c` | Fallback max context tokens | Absorbed | Already present locally in default config, metadata i18n, and main-agent build path. |
| `1f9c2c2b` | Anthropic custom headers/system prompt compatibility | Absorbed | Already present locally. |
| `750597d` | Provider model-add flow | Absorbed | Rewritten locally by wiring available-model clicks to the model config dialog before save. |
| `56ec44eb` | Logger cleanup | Skipped | Cosmetic log-churn; not needed for functional sync. |
| `dee4f14a` | Ruff format | Skipped | Formatting-only upstream churn. |
| `f2370cd1` | Plugins can add skills | Absorbed | Local `SkillManager` discovers plugin `skills/`, plugin changes sync skills to active sandboxes, and plugin components expose skill entries. |
| `6eb8a51` | System prompt guide docs | Skipped | Upstream docs-only guide; not required for current Yakumo runtime sync. |
| `fff9c8ee` | Plugin custom pages WebUI | Absorbed | Local plugin page bridge/auth/routes, page component serialization, docs, and `PluginPagePage` are present. |
| `afe99955` | Version bump to `4.24.0` | Absorbed/Superseded | Superseded by the user-approved `4.25.1` version alignment pass. |

New upstream commits since previous ledger baseline:

| Upstream commit | Topic | Initial status | Notes |
| --- | --- | --- | --- |
| `2d786268` | SQLAlchemy compatibility on macOS | Absorbed | Rewritten locally with SQLite `NullPool`, connect-time PRAGMAs, SQLModel document table creation via compiled SQLite DDL, and a guarded unique `documents.doc_id` index migration. |
| `7ff58f29` | FAQ hard-refresh note | Skipped | Docs-only upstream FAQ note; not required for local Yakumo docs unless dashboard troubleshooting docs are refreshed. |
| `000d638c` | QQ Official WebSocket docs typo | Skipped | Docs-only typo fix. |
| `bc35daa1` | Restore mobile provider-source deletion | Absorbed | Rewritten locally in the customized provider-source panel; mobile layout now exposes a selected-source delete button and delete actions have accessible labels/titles. |
| `284c4082` | GitHub Actions dependency bump | Absorbed | User requested dependency/version maintenance; docker setup-buildx/login/build-push actions were bumped to upstream versions. |
| `ae44163b` | Smooth markdown streaming | Absorbed | Rewritten locally by threading existing message streaming state into `MarkdownRender` and upgrading `markstream-vue`/`stream-markdown` through `pnpm install --lockfile-only`. |
| `23d70dbd` | Plugin card direct access and embedded page height | Absorbed | Rewritten locally: plugin list exposes lightweight `pages` names only, card opens the first Plugin Page directly, and embedded page/state height uses the upstream smaller viewport offset. Full page components remain detail-only. |
| `538772c3` | Xiaomi and Xiaomi Token Plan LLM providers | Absorbed | Rewritten locally as two explicit provider adapters: OpenAI-compatible Xiaomi uses the OpenAI prompt renderer and MiMo endpoint/model fallback; Xiaomi Token Plan uses the Anthropic prompt renderer, fixed Token Plan endpoint, and Bearer authorization header. Default config templates, provider manager dynamic imports, WebUI icons, and focused tests were added. |
| `89153fdf` | MiMo reasoning content compatibility | Absorbed | Rewritten locally in the OpenAI-compatible provider: assistant messages with `reasoning_content` are preserved by sanitization, and MiMo reasoning models receive empty `reasoning_content` on assistant history when missing. |
| `465a685b` | EULA hint for first notification | Skipped | Product/legal first-notification UX; not part of this fork's current runtime sync. |
| `85f9c4df` | MiMo voice-design TTS payload | Absorbed | Rewritten locally; `voicedesign` TTS models omit unsupported `audio.voice`, while regular TTS models still include it. |
| `7f94bce3` | QQ Official split message chain by media | Absorbed | Rewritten locally with `MessageChain.derive()` so split media chunks preserve `use_markdown_`, `use_t2i_`, and chain type; event sends and proactive `send_by_session` now send at most one media component per QQ Official API call. |
| `a221c74b` | Plugin metadata repo type guard | Absorbed | Rewritten locally by serializing plugin `repo` as a string in plugin list and detail responses. |
| `26e867cc` | Image requests route to vision fallback | Absorbed | Rewritten locally in `build_main_agent`: image requests switch to the first configured fallback chat provider that explicitly supports `image`, preserving the local prompt pipeline and existing modality downgrade fallback. |
| `5bbcdced` | Skip empty LLM summaries | Absorbed | Rewritten locally in `LLMSummaryCompressor`; empty or whitespace-only summaries now keep original history and log a warning. |
| `de0a7afd` | pnpm action bump | Absorbed/Superseded | Superseded by the user-approved pnpm/action-setup `v6.0.7` alignment pass. |
| `7a9fb33d` | FAQ typo | Skipped | Docs-only upstream typo fix. |
| `c4693fa6` | RST/ADOC knowledge uploads | Absorbed | Rewritten locally as a narrow upload/parser whitelist update using existing `MarkitdownParser`, with WebUI accept/i18n/icon hints. |
| `a38e9881`/`35245519` | FTS5 sparse knowledge retrieval | Absorbed | Present locally in `DocumentStorage` and `SparseRetriever`: contentless FTS5 index creation/rebuild/search is used when available, with in-memory BM25 fallback when FTS5 is unavailable. Unit coverage exists in `tests/unit/test_document_storage_fts.py` and `tests/unit/test_sparse_retriever.py`. |
| `c2aeeac4`/`d9ab3534` | Legacy `documents_fts` recovery | Absorbed | Present locally; startup validates the `documents_fts` virtual table, recreates invalid legacy non-FTS tables, and tests cover recovery. |
| `e852e906`/`76ee4f27` | EPUB knowledge upload support | Absorbed | Present locally via `EpubParser`, parser selection for `.epub`, dashboard upload accept/i18n, file-read EPUB support, and parser/file-read tests. |
| `16593354` | Automated MDI subset generation | Absorbed | Rewritten locally; dashboard dev/build scripts now generate the MDI subset before Vite, and generated subset assets are ignored instead of tracked. |
| `d15606d2` | Dashboard password CLI command | Absorbed | Rewritten locally for the coordinated PBKDF2 migration: `astrbot password` validates password strength, writes `dashboard.pbkdf2_password` plus legacy `dashboard.password`, clears the change-required flag, and can optionally update the dashboard username. |
| `0e6ad1c` | CLI initial dashboard password env | Absorbed | Rewritten locally for the PBKDF2 migration: `astrbot init` creates `data/cmd_config.json` from defaults with `ASTRBOT_DASHBOARD_INITIAL_PASSWORD` written to PBKDF2 and legacy hash fields, marks storage upgraded, requires first login password change, and does not overwrite an existing config. |
| `93428a79` | Core initial dashboard password env | Absorbed | Core config initialization now resolves `ASTRBOT_DASHBOARD_INITIAL_PASSWORD` when generating first-use dashboard credentials, validates the password policy, and otherwise generates a random compliant password. |
| `7ddf6371` | Dashboard password policy/PBKDF2 setup flow | Absorbed | Rewritten locally as a coordinated backend/WebUI migration: plaintext login verifies PBKDF2 or legacy MD5 storage, MD5 hash strings no longer authenticate directly, legacy configs can log in with the real password and must set a new password to upgrade, setup-status/setup routes are exposed, stat hints include legacy/upgrade state, and the login WebUI now sends plaintext. |
| `e05dd650` | Legacy password login guidance | Absorbed | The local login failure mode now matches the PBKDF2 migration boundary: legacy MD5 storage requires the real plaintext password, not the stored hash, and tests cover the failed-hash-login path plus upgrade. FAQ copy was not copied because local docs are maintained separately. |
| `3290d755` | Prefer bundled dashboard over stale `data/dist` | Absorbed | Rewritten locally; version comparison now selects bundled WebUI when an older user `data/dist` would otherwise shadow it. |
| `587286a9` | Warn when default chat provider is unset or invalid | Absorbed | Already present locally before this batch; lifecycle resets the warning guard after provider reload and warns on missing/invalid default provider ID. |
| `bc2c67d4` | Dynamic plugin Web API routes | Absorbed | Already present locally; `/api/plug/<path>` uses Werkzeug route matching so plugin APIs can expose dynamic path params while preserving method checks. |
| `319f50be` | Plugin changelogs and update download URLs | Absorbed | Already present locally; plugin routes expose changelog reads, update/install paths pass `download_url`, batch update supports per-plugin download URLs, and the WebUI has changelog/update flows. |
| `cb4f941e` | Plugin page internationalization | Absorbed | Already present locally; Plugin Page bridge receives signed initial context with locale/i18n, page serialization includes `i18n_key`, and frontend plugin i18n utilities resolve page metadata. |
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
| `5745ce5b`/`24bb25c6` | CUA native file upload interfaces | Absorbed | Present locally; `CuaBooter.upload_file` prefers sandbox/native `upload_file`, then `files.upload`, then `files.write_bytes`, with POSIX shell/base64 fallback only when native APIs are unavailable. Tests cover native upload, write-bytes fallback, native failure propagation, shell quoting, and non-POSIX rejection. |
| `49cd4d2a`/`5115f112` | CUA idle sandbox expiry | Absorbed | Present locally; `computer_client` tracks per-session CUA idle state, refreshes expiry on reuse, shuts down idle CUA sandboxes proactively, and leaves non-CUA booters unscheduled. |
| `f02845eb`/`3036a3f1` | CUA idle timeout config exposure | Absorbed | Present locally; `cua_idle_timeout` is the dashboard-exposed CUA lifecycle knob, while `cua_ttl` remains hidden from configurable metadata. |
| `1b09132e`/`a1e4240d` | Shipyard Neo profile selection | Absorbed | Present locally; a non-empty configured profile is honored exactly, while an empty profile auto-selects the best available Bay profile and falls back to `python-default`. |
| `eb69bf36`/`f9644946` | Shipyard Neo readiness gate and cleanup | Absorbed | Present locally; `ShipyardNeoBooter` waits for ready status, deletes failed/expired/timed-out sandboxes, closes clients on shutdown, and stale Neo booters are evicted with `delete_sandbox=True`. |
| `3a1d6c8f` | `None` tool-call arguments | Absorbed | Present locally in the OpenAI-compatible completion parser and tool-loop runner; `None` arguments from providers are normalized to `{}` for no-parameter tools. |
| `a09657e6` | MiniMax TTS timber-weight parsing | Absorbed | Present locally; empty or invalid `minimax-timber-weight` config falls back to a default timber-weight payload instead of crashing JSON parsing. |
| `871b9327` | Tencent SILK magic-byte detection | Absorbed | Present locally; audio magic detection recognizes both standard `#!SILK_V3` and Tencent `\x02#!SILK_V3`, and `ensure_wav` routes SILK through the Tencent converter. |
| `22ba831a` | Send-message missing path guard | Absorbed | Present locally; missing local/sandbox media paths return an error before constructing/sending message components, with component-specific path error text. |
| `1d696264` | Active reply image passthrough | Absorbed | Present locally; active reply calls collect image components from the triggering message and pass them through `event.request_llm(..., image_urls=...)`. |
| `d1059cd5` | Windows updater zip root normalization | Absorbed | Present locally; updater zip extraction normalizes archive roots and test coverage exercises Windows-style archive-root behavior. |
| `116c66b5` | Blank-prompt KB retrieval skip | Absorbed | Present locally; main-agent build skips KB retrieval when prompt text is blank and no image inputs are present. |
| `041c35c3` | Plugin install temp cleanup and failed-tracking guard | Absorbed | Present locally; repeated file installs that conflict with an existing plugin skip failed-plugin tracking and remove temporary `plugin_upload_*` directories. Current plugin install flow tests remain focused on dependency install behavior. |
| `7d402fa1` | NVIDIA and Ollama embedding providers | Absorbed | Rewritten locally before this pass; `nvidia_embedding_source.py` and `ollama_embedding_source.py` are registered in provider manager, exposed in default config templates, and NVIDIA embedding has `input_type` metadata. |
| `010e6d2e`/`39386eeb`/`989cc0d6`/`c4810804`/`c77cb0f4`/`02291a32`/`d609f23b` | Version bumps and changelogs | Absorbed | User requested version/changelog maintenance; CLI/package/core versions are aligned to `4.25.1`, and release changelogs `v4.24.3`, `v4.24.5`, `v4.25.0`, and `v4.25.1` were added. |
| `4bcaaab4`/`1d3f54ca` | GitHub Actions dependency bumps | Absorbed | User requested dependency maintenance; pnpm/action-setup was bumped to `v6.0.7` in docs/dashboard/release workflows. |
| `9165278d`/`77fa0e46` | README contributor/Trendshift image updates | Absorbed | User requested README maintenance; stale localized README contributor/Trendshift badges were updated to the upstream values. English README was already current. |
| `942dcdfc`/`bd9aade8`/`cb90de75`/`ef73d2da` | General upstream docs corrections | Skipped | Docs-only upstream cleanup; local Yakumo/runtime docs are maintained separately unless the content affects current fork behavior. |
| `48e111e4` | Remove upstream-only test | Skipped | Test-churn only; no local runtime behavior to absorb. |
| `37d61592`/`7d72e3a9`/`2d6f5e64`/`4672a04e` | Random initial password docs/messages | Absorbed | Runtime behavior and WebUI startup/setup messaging were absorbed through random generated first-use credentials and setup flow; source-deploy docs now point users to console-generated credentials and `ASTRBOT_DASHBOARD_INITIAL_PASSWORD` instead of fixed defaults. |
| `8b16e4d6` | File component filename sanitization | Absorbed | Rewritten locally in `File._download_file`; remote names are reduced to a safe basename, dangerous characters/NULs are replaced or removed, and downloads stay under AstrBot temp storage. |
| `9688a64c` | Plugin detail sub-command count label | Absorbed | Rewritten locally in the extension detail i18n files for English, Chinese, and Russian. |
| `e960c149` | Duplicate plugin display guard | Absorbed/No-op | Local plugin list code did not contain the duplicate append path, so the upstream fix is already functionally satisfied. |
| `022a5dd9` | Avoid duplicate quoted-image captioning | Absorbed | Rewritten locally so quoted images are not captioned when the main provider supports image input, and the main provider is not used as an implicit caption fallback when no caption provider is configured. |
| `9bd38cad` | Trim segmented reply text | Absorbed | Rewritten locally in result decoration so split plain segments are stripped before being appended. |
| `e087b9de` | Marketplace/local plugin name matching | Absorbed | Rewritten locally by exposing `marketplace_name` from plugin APIs and matching extension market entries by normalized repo or marketplace name with local-name fallback. |
| `adae1f359` | Command suggestion wake words and hover info | Absorbed | Rewritten locally against the customized chat input: `/api/commands` returns per-config `wake_prefix`, suggestions trigger/display with configured prefixes, and command descriptions are shown in a hover tooltip. |

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

Current status:

Absorbed after later review. Current local code includes FTS5-backed sparse retrieval with BM25 fallback, legacy `documents_fts` recovery, EPUB parser/upload/read support, blank-prompt KB retrieval skip, and RST/ADOC upload support.

Revisit if:

Upstream later changes retrieval ranking/storage semantics, adds a new document format, or introduces a migration that conflicts with local prompt KB caching.

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
