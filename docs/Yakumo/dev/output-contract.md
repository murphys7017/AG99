# Output Contract

记录当前结构化输出约束的统一链路。这里描述的是跨 prompt、render、request、provider 和 parser 的公共机制，不属于 interaction decision 私有实现，也不属于某个 renderer 的私有规则。

## 目标

输出约束不再只依赖“请输出 JSON”这类软 prompt 文本，而是作为一等数据贯穿完整调用链：

`prompt 声明 -> render 编译 -> request 投影 -> provider 落地 -> response 解析`

当前第一目标是让高约束场景脱离“手写自由文本 JSON prompt”。其中：

- `interaction decision` 仍以协议级 `tool_call` 为主成功路径
- persona visible-reply 当前主链路已经明确为严格 `json_object`

## 核心类型

### `OutputContract`

文件：

- `astrbot/core/output_contract.py`

字段：

- `mode`: `text | json_object | tool_call`
- `strict`: 是否要求强约束
- `schema`: JSON object schema 或 tool call 参数 schema
- `preferred_tool_name`: 首选工具名
- `allow_text_fallback`: parser 是否允许文本 fallback

`OutputContract` 表达“希望得到什么输出”，不表达 provider 应该如何组织私有 payload。

### `CompiledOutputContract`

文件：

- `astrbot/core/output_contract.py`

字段：

- `contract`
- `strategy`: `prompt_only | protocol_tool_call | protocol_native_json`
- `degraded`
- `degrade_reason`
- `tool_name`
- `tool_schema`
- `fallback_prompt_text`

`CompiledOutputContract` 表达 renderer 对契约的编译结果。provider 应优先消费这个 compiled binding，而不是重新从裸 `OutputContract` 推导 schema。

## 数据流

1. prompt/context pack 通过 `meta["output_contract"]` 声明 `OutputContract`。
2. `BasePromptRenderer._compile_output_contract(...)` 读取声明并生成 `CompiledOutputContract`。
3. 派生 renderer 通过 `resolve_output_contract_strategy(...)` 声明协议级能力。
4. `RenderResult` 同时携带 `output_contract` 和 `compiled_output_contract`。
5. `ProviderRequestAdapter` 把两者投影到 `ProviderRequest`。
6. provider 调用优先消费 `compiled_output_contract`，再兼容旧入口的裸 `output_contract`。
7. response parser 根据契约决定是否允许文本 fallback。

## 策略边界

### `protocol_tool_call`

当前 strict 结构化输出的主实现。

- OpenAI-compatible provider 使用 function tool / required tool choice。
- Anthropic-compatible provider 使用 tool schema / required tool choice。
- renderer 只产出 tool name 和 tool schema，不直接构造 provider 私有 payload。

### `protocol_native_json`

保留策略名，当前还不是通用实现目标。后续如果某个 provider 有稳定原生 JSON schema 通道，可以在不改变 `OutputContract` 的前提下补 provider-specific 落地。

### `prompt_only`

这里需要区分两种情况：

1. `tool_call -> prompt_only`

- 这是协议级能力不可用时的受控降级。
- `RenderResult.metadata` 中应体现 `output_contract_degraded=True`。

2. `json_object -> prompt_only`

- 这是当前的原生契约落地方式，不应视为退化。
- 例如 persona visible-reply 当前就是 `strict json_object + allow_text_fallback=False`。

因此，`prompt_only` 本身只表示“最终通过 prompt 文本约束落地”，不自动等价于“失败降级”。

- fallback 文本由 `build_output_contract_fallback_prompt(...)` 统一生成。
- 只有 `tool_call` strict 契约降到 `prompt_only` 时，才应标记 `degraded=True`。
- 普通非高约束场景可以原生使用 strict `json_object`。
- 高约束 `tool_call` 场景不能把受控降级后的 `prompt_only` 当成功路径。

## Renderer 职责

renderer 负责把声明编译成策略：

- `BasePromptRenderer`: 对非 text 输出契约默认编译为 `prompt_only`；仅 `strict tool_call` 降到 `prompt_only` 时记为 `degraded`。
- `OpenAIPromptRenderer`: 对 `tool_call` 编译为 `protocol_tool_call`，保持 OpenAI-compatible message/tool schema 形态。
- `AnthropicPromptRenderer`: 对 `tool_call` 编译为 `protocol_tool_call`，保持 Anthropic content block/tool schema 形态。
- `MiniMaxPromptRenderer`: 默认保持 `prompt_only`；只有 provider 显式启用 `minimax_enable_tool_call` 时才把 `tool_call` 编译为 `protocol_tool_call`。

renderer 不负责：

- 直接设置 provider 私有 payload。
- 决定 parser 是否接受文本 fallback。
- 在业务 prompt 中手写大段结构化输出约束。

## Provider 职责

provider 负责把 compiled binding 落到自身协议：

- 优先使用 `CompiledOutputContract.tool_name` 和 `tool_schema` 构造单工具集。
- strict tool call 场景必须要求 tool choice。
- 不支持协议级策略时必须显式失败或受控降级，不能静默忽略。
- 裸 `OutputContract` 兼容路径只用于旧调用点迁移，后续应逐步删除。

当前已真实处理 `protocol_tool_call` 的基础 provider：

- `ProviderOpenAIOfficial` 及其 OpenAI-compatible 子类
- `ProviderAnthropic` 及其 Anthropic-compatible 子类

Gemini、VolcEngine Ark 等 provider 当前没有 provider-specific renderer。strict contract 到达这些 provider 时，应按场景策略显式失败或受控降级。

## Interaction Decision

interaction decision 是当前首个高约束消费者。

默认契约：

- `mode="tool_call"`
- `strict=True`
- `preferred_tool_name="interaction_decision"`
- `allow_text_fallback=False`
- `schema=build_interaction_decision_tool_parameters()`

运行规则：

- render 结果必须是 `protocol_tool_call`。
- `prompt_only` 不能作为成功路径。
- parser 优先读 tool call payload。
- strict 且 `allow_text_fallback=false` 时，裸文本 JSON 不算成功。

## Persona Visible Reply

persona visible-reply 是当前另一个重要消费者，但它和 interaction decision 的策略不同。

默认契约：

- `mode="json_object"`
- `strict=True`
- `allow_text_fallback=False`
- `schema` 固定为 `spoken_reply` + `effect_calls`

运行规则：

- 当前主链路允许 renderer 编译成 `prompt_only`，且这不是 degraded。
- parser 必须解析单个 JSON object；自由文本不算成功。
- `effect_calls` 使用固定字段；无 effect 时返回空数组。
- 具体 effect 的 `arguments` 由注册的 effect schema 决定。

## 观测字段

`RenderResult.metadata` 需要保留：

- `output_contract_requested`
- `output_contract_strategy`
- `output_contract_degraded`
- `output_contract_degrade_reason`

主请求日志和 prompt shadow/apply 摘要应能看到 `output_contract` 与 `compiled_output_contract`，用于判断当前场景到底是协议级支持、受控降级，还是未声明输出契约。

## 后续收口

- 逐步删除 provider 中裸 `OutputContract` 重建 schema 的旧入口。
- 给 Gemini / VolcEngine Ark 等 provider 增加明确 renderer family 或 provider-specific renderer。
- 将更多结构化提取、修复器、prompt-json 分析场景迁移到同一契约链路。
- 继续减少业务模块手写“只输出 JSON”文本，统一走 fallback compiler。
