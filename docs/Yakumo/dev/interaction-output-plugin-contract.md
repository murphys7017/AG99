# Interaction Output Plugin Contract

本文记录 Interaction 输出层和插件输出注入的目标契约。它不是某个插件的实现说明，而是 core、plugin、platform 之间的输出边界。

## 目标

所有用户可见输出都应汇入 Interaction Output Runtime，由它统一完成文本发送、TTS、通用 effect 交付、平台扩展、turn/message identity、打断、历史记录和完成回执。具体动作或客户端表现由插件解释，Output Runtime 不理解其领域语义。

执行层只产出结果，不直接决定平台表现：

```text
input
  -> Interaction route decision
  -> silent / persona / hybrid
  -> core, tool, or plugin execution result
  -> Interaction output draft
  -> output plugin contributions
  -> platform delivery
```

插件可以请求输出或注入表现能力，但不应把 `event.send(...)` 当作长期主路径绕过 Interaction。兼容期可以保留旧 API，内部逐步转发到 Output Runtime。

## 插件分类

### Execution Plugin

执行型插件负责做事，例如搜索、记忆、代码、文件处理、远程操作。它可以产出文本、附件、结构化结果或错误，但最终用户可见发送仍应回到 Interaction。

### Output Enrichment Plugin

表现增强插件负责修饰输出，例如 TTS hint、动作 effect、前端 client object、平台卡片建议。它不回答用户问题，不拥有最终文本，只补充输出表现。

### Delivery Plugin

投递插件负责平台私有格式或传输细节，例如特殊 payload、平台回执或兼容包装。它不参与语义决策，也不改写最终回复。

## 核心类型

### `InteractionOutputDraft`

`InteractionOutputDraft` 表示 Interaction 当前准备发送的输出草稿。

字段：

- `turn_id`: 当前 interaction turn。
- `message_id`: 可选；发送阶段分配 visible message id 后再绑定。
- `source`: `interaction | core | plugin | system`。
- `route_mode`: `silent | persona | hybrid`；协议 Core bypass 不伪造 route。
- `phase`: `immediate | final | background`。
- `text`: 当前阶段的候选用户可见文本。
- `semantic_text`: 当前阶段的候选语义文本，供 TTS、memory、analytics 或插件表现增强使用。
- `attachments`: 待输出附件。
- `message_kind`: 输出类型，例如 `immediate_reply`、`core_reply`、`plugin_notice`。
- `latency_policy`: `fast | normal | deferred`。
- `metadata`: 诊断和扩展元数据。

兼容期内，`InteractionResultContribution.final_text_override` 仍可能在 result contributor 合并后改写最终发送文本。因此传给 contributor 的 `output_draft.text` / `semantic_text` 必须视为 `candidate_pre_contribution`，不保证等于最终发送文本。需要基于最终文本工作的表现插件，应等待后续拆分的 text-transform 阶段完成后再运行。

### `InteractionOutputContribution`

`InteractionOutputContribution` 表示插件对输出草稿的补充。

字段：

- `plugin_id`: 贡献来源。
- `stage`: 注入阶段，例如 `output_enrich`、`delivery`。
- `client_objects`: 面向前端或平台 adapter 的结构化对象。
- `platform_extras`: 平台额外 payload。
- `tts_hints`: TTS 建议。
- `delivery_hints`: 投递建议。

动作、灯光或客户端表现等具体领域信息不进入 Core 的固定字段。插件应消费属于自己的
`effect_calls`，并通过通用 `platform_extras` 或 `client_objects` 输出不透明载荷。
- `metadata`: 诊断元数据。
- `latency_class`: `fast | bounded | deferred`。
- `priority`: 合并顺序。

兼容期内，`InteractionOutputContribution` 可以降级为现有 `InteractionResultContribution`。

## 生命周期

### 1. Decision

Interaction route decision 只选择本轮对话的处理路径；用户可见表达与 effect 不属于 route：

- `silent`: 不调用 Persona Expression 或 Core，以无可见输出的合法 material 完成本轮。
- `persona`: 统一 Persona Expression 直接生成最终回复。
- `hybrid`: Persona Expression 生成委派确认，Core 生成主结果；目标态由二者并发执行并通过同一 Output Arbiter 仲裁。

直播音频和协议命令使用独立 Core bypass，不进入对话 Router，也不创建伪造的 route decision。

`confidence` 不属于该契约。它没有外部校准来源，不能参与路由或输出策略。

### 2. Execution

core、tool 或执行型插件完成任务，返回结果材料。该阶段不直接向平台发送用户可见消息。

### 3. Draft

Interaction Output Runtime 构造 `InteractionOutputDraft`，绑定 turn、phase、route、source、message kind 和语义文本。

### 4. Fast Enrichment

只运行低延迟、本地、可预测的表现增强。`persona` 直接回复默认只允许这一阶段的增强，不能被远程 LLM 表现补全阻塞。

当前兼容实现里，旧 `InteractionResultContribution.final_text_override` 与表现增强仍在同一轮 contributor 收集中。新的插件不应继续依赖该字段。后续应拆成 `text_transform` 先确定最终文本，再进入 `output_enrich`。

### 5. Bounded / Deferred Enrichment

较慢的增强必须声明预算：

- `bounded`: 有短超时，失败后 fallback。
- `deferred`: 不阻塞主回复，可后续补发 client object。

### 6. Delivery

Interaction 统一发送文本、语音、通用 client object、平台 extras，并记录 visible output、utterance ledger、finalized material 和完成状态。插件私有 effect 的执行结果可以通过这些通用载荷交付，但不进入 Core 固定字段。

## Effect 规则

- effect 名称和参数 schema 由注册插件拥有，Core 不为具体插件增加专用字段。
- 插件只消费属于自己的 `effect_calls`，未知 effect 应保持隔离而不是猜测执行。
- effect 的解释、资源选择、设备约束和 fallback 都由插件负责。
- 延迟执行的 client object 应尽量绑定 `turn_id` 和 visible message id。
- fallback 应记录可诊断原因，不能把默认表现伪装成模型成功输出。

## 硬约束

- 表现增强插件默认不能改 `final_text`。
- 旧 `final_text_override` 是兼容路径；新表现插件不要把文本改写和表现注入混在一起。
- 远程表现生成不能阻塞 `persona` 直接回复路径。
- 输出贡献必须声明 stage 和 latency class。
- client object 必须尽量绑定 turn/message identity。
- 插件主动输出必须逐步收口到 Interaction output queue。
- 兼容期旧 `event.send(...)` 仍可存在，但不应成为新能力的主路径。

## 迁移顺序

1. 保留现有 `InteractionResultContribution`，新增 `InteractionOutputDraft` 和 `InteractionOutputContribution`。
2. `InteractionResultView` 暴露 `output_draft`，让表现型插件读取统一上下文。
3. 删除 interaction decision 的 `confidence` 字段。
4. 让 OutputController 逐步成为插件主动输出的默认入口。
5. 各插件按类别迁移：先 Output Enrichment，再 Execution Plugin 主动输出，最后 Delivery Plugin 平台细节。
