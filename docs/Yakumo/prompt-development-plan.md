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
- 同批重复 slot 冲突失败；跨阶段替换必须显式声明。
- Router、Persona、Core 使用统一目标投影，不再使用 LLM Selector。
- Router 使用近期历史和人格摘要；Persona 使用完整官方历史和人格材料；Core 使用官方历史与执行能力，但不读取人格/effect 语义。
- 插件 extension targets 在三个目标上统一过滤。
- 插件显式 contexts/content parts 进入 Collector，不再依赖渲染后的补偿追加。
- `PromptTreeBuilder` 已从 Render Engine 抽离。
- provider renderer 负责协议序列化与输出契约落地。
- 会话保存使用去除 Prompt 脚手架的用户消息。
- 群聊上下文以动态结构化 slot 进入三个目标，并保留 legacy 兼容出口。

## 下一阶段

### 1. Layout Policy 收口

把 Base renderer 中剩余的 `render_*_context` 语义布局方法迁到独立 layout policy。迁移期间保留兼容适配器，完成后 provider renderer 只处理序列化。

验收条件：新增一个 provider renderer 不需要理解 ContextSlot 业务选择规则。

### 2. 上下文预算

引入按 target 与 provider/model 上限分配的预算策略，分别约束 history、memory、knowledge、tools 和 system material。预算只裁剪目标视图，不回写规范 Pack。

验收条件：同一 ContextPack 可针对不同模型窗口稳定渲染，裁剪结果可诊断。

### 3. 执行器端口

让 Core 的目标视图和 capability snapshot 可以交给 Native AstrBot、Codex、OpenCode 等执行后端。知识库、插件工具和 skills 通过统一 capability gateway 暴露，不把第三方执行器协议写入 Collector。

验收条件：替换执行器不改变 Router/Persona Prompt，也不复制知识库和插件注册逻辑。

### 4. 真实链路验证

覆盖 OpenAI、Anthropic、MiniMax 及至少一个第三方执行器，验证：

- 多模态 content parts 顺序
- tool call 与 prompt-only 降级
- 群聊 ambient context
- 长历史裁剪
- 会话保存无 Prompt 脚手架
- interaction Core 最终材料回到统一 Persona Runtime

## 非目标

- 不重新引入 LLM Selector。
- 不针对单个插件修改 Router 或通用 schema。
- 不让 Core 理解 Motion、Live2D、TTS 等插件领域语义。
- 不把 static Collector 扩展成无失效协议的全局缓存。
