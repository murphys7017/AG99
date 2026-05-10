# Prompt Module

`astrbot/core/prompt/*` 是本 fork 相对上游最核心的改动之一。它把原本散落在主 Agent、pipeline、provider request 组装过程里的模型可见上下文，收口成结构化的 `ContextPack -> Select -> Render -> ProviderRequest` 链路。

这份文档描述当前代码状态，不再沿用早期设计草案中的占位内容。

## 当前定位

上游 AstrBot 主线更偏向在 `astrbot/core/astr_main_agent.py` 和相关 pipeline 阶段里直接拼装 `ProviderRequest`。

本 fork 仍保留主 Agent 的能力装配责任，但新增了 prompt pipeline：

- collect：由 collector 把 persona、input、session、policy、memory、history、skills、tools、subagent、knowledge、extension 等信息收集成 `ContextPack`。
- select：由 selector 决定本轮真正进入模型请求的上下文。
- render：由 `PromptRenderEngine` 和 renderer 把 `ContextPack` 渲染为 `RenderResult`。
- apply：由 `ProviderRequestAdapter` 把 `RenderResult` 投影回 `ProviderRequest`。

当前默认模式已经不是纯 shadow。`prompt_pipeline_mode` 未配置时会进入 `apply_visible`，即 prompt pipeline 会覆盖模型可见字段；只有显式配置 legacy/shadow 时才走旧链路或影子对比。

## 主要代码位置

- `astrbot/core/prompt/context_collect.py`
- `astrbot/core/prompt/context_types.py`
- `astrbot/core/prompt/context_catalog.py`
- `astrbot/core/prompt/collectors/*`
- `astrbot/core/prompt/render/selector.py`
- `astrbot/core/prompt/render/engine.py`
- `astrbot/core/prompt/render/interfaces.py`
- `astrbot/core/prompt/render/request_adapter.py`
- `astrbot/core/prompt/extensions/*`
- `data/config/prompt/context_catalog.yaml`
- `astrbot/core/astr_main_agent.py`
- `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py`

## Collect 阶段

入口是 `collect_context_pack(...)`。

默认 collector 包括：

- `SystemCollector`
- `PersonaCollector`
- `InputCollector`
- `SessionCollector`
- `PolicyCollector`
- `MemoryCollector`
- `ConversationHistoryCollector`
- `SkillsCollector`
- `ToolsCollector`
- `SubAgentCollector`
- `KnowledgeCollector`

同时支持插件通过 prompt extension 注册补充上下文。extension 会被规范化为 `ContextSlot`，并按 mount 进入 renderer。

当前 collect 阶段仍保留非严格模式下的 fail-open 行为：collector 异常会记录 warning 并继续；严格模式由 `is_prompt_pipeline_strict(config)` 控制。这里是 prompt 子系统边界的临时保护，不应作为主链路正确性的证明。

## Select 阶段

入口是 `build_prompt_selector(config)` 和 `select_context_pack_async(...)`。

当前默认 selector 配置在 `provider_settings.prompt_selector` 下，默认 `enable=False`。在未启用 LLM selector 时，主要行为是规则化/透传选择；启用后可以使用独立 provider/model 做更细粒度的上下文筛选。

selector 的输出会写入事件 extra：

- `prompt_selected_context_pack`

## Render 阶段

入口是 `PromptRenderEngine.render(...)`。

当前默认 renderer 是 `BasePromptRenderer`，会把已选择的 `ContextPack` 编译为：

- `system_prompt`
- history/context messages
- 当前 user message
- tool schema 相关输出
- prompt tree / trace 信息

extension mount 的当前语义：

- `system`：稳定系统规则。
- `capability`：稳定能力契约。
- `context`：当前请求动态事实，渲染为 history 后、memory/knowledge 前的 `_no_save` context message。
- `input`：贴近当前用户输入的补充材料。

动态运行时事实不应塞进稳定 system prefix，否则会污染 prompt cache 和系统级语义。

## Apply 阶段

入口是 `apply_render_result_to_request(...)`。

`ProviderRequestAdapter` 当前会原地更新：

- `request.system_prompt`
- `request.contexts`
- `request.prompt`
- `request.extra_user_content_parts`
- `request.image_urls`
- `request.audio_urls`

它只负责把 `RenderResult` 投影到 AstrBot 现有 `ProviderRequest` contract，不负责 provider-specific 发送细节。后续的 modalities 修正、provider 适配、工具执行仍在主 Agent 和 provider source 链路中完成。

## 主 Agent 接入方式

`astrbot/core/astr_main_agent.py` 当前存在三种模式：

- `apply_visible`：渲染结果直接应用到 live `ProviderRequest`，这是当前默认。
- `shadow`：克隆 request 后应用渲染结果，只记录 diff，不影响实际请求。
- `legacy`：不使用 prompt pipeline 接管模型可见字段。

相关诊断 extra 包括：

- `prompt_render_result`
- `prompt_apply_result`
- `prompt_shadow_provider_request`
- `prompt_shadow_apply_result`
- `prompt_shadow_diff`

会话保存也会优先使用 prompt pipeline 生成的当前用户消息表达，避免把内部 context message 或附件结构错误写入普通会话历史。

## 和 Memory 的关系

prompt module 不直接写 memory。它通过 `MemoryCollector` 读取 `MemorySnapshot`，把 memory service 已经产出的短期、中期、长期记忆投影到模型可见上下文。

memory 写入发生在回合完成后的 postprocess/memory service 链路中。interaction turn 也遵循同一原则：middleware 产出 finalized material，postprocess/memory 消费 material，prompt 下轮只读 snapshot。

## 和 Interaction Middleware 的关系

interaction middleware 在 decision 阶段也复用 prompt render 能力。插件通过 `register_interaction_prompt_contributor(...)` 提供的 `PromptExtension` 会进入 interaction decision prompt，而不是普通 core prompt 的直接替代品。

当前约束：

- 普通 core prompt extension 影响主 Agent 可见上下文。
- interaction prompt contributor 只影响 middleware decision prompt。
- 两者都使用 `PromptExtension` 数据结构，但作用阶段不同。

## 仍需继续收口

- `astr_main_agent.py` 仍承担过多能力装配逻辑，prompt pipeline 还没有把主 Agent 完全拆薄。
- collect 阶段非严格模式仍有 fail-open 行为，后续需要按主链路要求继续收紧。
- provider-specific render 规则仍主要依赖通用 renderer + 后续 provider 适配，尚未完全模块化。
- selector 默认未启用 LLM 选择，当前多数场景仍是规则化/透传选择。
- prompt trace、conversation save、attachment projection 仍需要结合真实平台日志继续验证。
