# Prompt Development Plan

## 目标

Prompt 系统只做一件事：先完整收集可用事实，再按目标构建模型输入。任何新上下文都必须进入统一数据管线，不能在 Router、Persona、Core 或 provider 旁边重新拼一套字符串。

```text
Collect facts
  -> Build canonical ContextPack
  -> Project by target
  -> Build semantic PromptTree
  -> Serialize by provider
  -> Apply to execution request
```

## 已完成

- Collector 输出统一 `ContextSlot`。
- `PromptContextBuilder` 支持不可变快照式合并、版本与收集 scope。
- 通过 Builder 收集和合并时，同批重复 slot 冲突失败，跨阶段替换可显式声明。
- Router、Persona、Core 使用统一目标投影，不再使用 LLM Selector。
- Router 使用近期历史和人格摘要；Persona 使用完整官方历史和人格材料；Core 的目标投影明确排除人格和 effect 语义。
- 插件 extension targets 在三个目标上统一过滤。
- 插件显式 contexts/content parts 进入 Collector，不再依赖渲染后的补偿追加。
- `PromptTreeBuilder` 已从 Render Engine 抽离。
- provider renderer 已负责协议序列化，并建立了输出契约落地接口。
- 会话保存使用去除 Prompt 脚手架的用户消息。
- 群聊上下文在 Router、Persona、Core 主管线只以动态结构化 slot 进入；未接入 ContextPack 的官方 Agent runner 保留受 Apply 标记保护的钩子桥接。
- 主 Agent 不再直接拼接 Persona、skills、knowledge、policy、tool instruction、历史、图片或文件 Prompt；模型可见内容只有 ContextPack 一条来源。
- persona begin dialogs、官方历史、插件显式 contexts 和当前输入已按所有权建立固定顺序。
- 官方 `on_llm_request` 仍作为最终 `ProviderRequest` 的低层插件钩子；统一 Prompt 渲染在它之前完成，因此钩子修改不会被覆盖。
- 官方第三方 Agent runner 仍可通过该钩子获得群聊上下文，Core 主管线会跳过桥接，避免形成第二份上下文。
- 已公开的 `apply_interaction_core_task_spec` 保留为直接请求兼容接口；主链路只使用 `CoreTaskCollector`，不形成双重注入。

## 当前确认问题

### 1. Provider Renderer 与输出契约能力判断分离

renderer family 决定协议序列化，Provider 的 `supports_output_contract_strategy()` 决定实际契约能力，两者当前没有统一校验。遗漏 renderer metadata 的工具型 Provider 可能静默退回 `prompt_only`。

目标：建立统一 Provider Prompt Capability，明确 renderer family、原生 tool call、输出契约和受控降级能力；禁止按单个 Provider ID 打补丁。

### 2. ContextPack 仍可绕过 Builder 被直接修改

Router、Persona 和 interaction enrichment 仍可直接 `add_slot()` 或删除 slot，同名值会被静默覆盖，绕过 Builder 的冲突检测、显式替换、版本和 collection scope。

目标：所有跨阶段 enrichment 通过返回新快照的 derive/replace API 完成；直接覆盖必须失败，删除也必须形成可诊断的投影或派生操作。

### 3. Prompt tool schema 与实际执行工具不是同一事实来源

RenderResult 可以生成 tool schema，但 Request Adapter 不会据此更新实际 `func_tool`；Core 执行仍读取旧 ProviderRequest 中的工具对象。

目标：明确 capability tree 是实际工具可见集的来源，或者将 RenderResult tool schema 降为纯诊断产物；不能长期维持两个看似等价的工具集合。

### 4. DeepSeek 首轮 Marker 的会话判断不完整

当前只检查 interaction memory，没有检查官方 conversation history，也没有持久化会话级应用状态。memory 缺失或运行时重启后，已有历史的会话仍可能再次注入首轮 Marker。

目标：以官方历史和会话级状态判断首轮，不使用事件级 extra 充当长期状态。

### 5. Context Catalog 尚未形成真实约束

Catalog 中的 required、multiple、lifecycle、llm_exposure 和 redact_fn 多数只用于描述，收集与投影阶段没有统一执行，文档中还保留已经删除的 Selector 阶段说明。

目标：要么让 Catalog 成为可执行契约并在收集、投影、诊断阶段校验，要么删除没有运行时含义的字段，避免提供虚假的安全和生命周期保证。

## 处理顺序

1. 统一 Provider Prompt Capability 与工具事实来源。
2. 收口 ContextPack 派生接口，禁止直接覆盖。
3. 修复首轮 Marker 和 Catalog 契约。
4. 上述边界稳定后，再重新评估上下文预算、Collector 并发和可替换执行器。

## 非目标

- 不重新引入 LLM Selector。
- 不针对单个插件修改 Router 或通用 schema。
- 不让 Core 理解 Motion、Live2D、TTS 等插件领域语义。
- 不把 static Collector 扩展成无失效协议的全局缓存。
- 不把删除内部重复注入实现扩大成删除官方插件钩子或已公开请求接口。
