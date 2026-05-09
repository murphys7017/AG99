# Base Renderer Module Design

记录当前 `BasePromptRenderer` 的模块化渲染结论，作为后续实现和 provider-specific renderer 的共同基线。

## 1. Scope

本设计当前只覆盖：

- 基础 renderer 的树结构和模块职责
- collect 输出到 render IR 的落位规则
- 面向 OpenAI 风格请求的通用中间层

本设计当前不覆盖：

- provider-specific 的最终编译优化
- 不同模型家的最佳 prompt 文案微调
- 替换现有主链路请求拼装

## 2. Base IR Tree

```text
prompt
├─ system
│  ├─ core
│  ├─ persona
│  ├─ policy
│  ├─ capability
│  └─ session
├─ context
│  ├─ memory
│  └─ knowledge
├─ history
│  ├─ begin_dialogs
│  └─ conversation
├─ user_input
│  ├─ text
│  ├─ quoted
│  └─ attachments
└─ tools
   ├─ function_tools
   └─ subagent_handoff
```

## 3. Compile Intent

这棵树是中间表示，不是最终 payload。后续默认编译方向为：

- `system/**` -> `system_prompt`
- `context/memory` -> `_no_save` user context message before history
- `context/knowledge` -> `_no_save` user context message before history
- `history/**` -> history messages
- `user_input/**` -> current user message
- `tools/**` -> tool schema

其中 `user_input/**` 的默认编译规则为：

- 纯文本输入 -> `{"role": "user", "content": "..."}`
- 含图片 / 文件 / 引用等多模态输入 -> `{"role": "user", "content": [...]}`
- `content` 优先保持结构化 content parts，不把整棵 `user_input` 子树直接压扁成一段文本

## 4. Design Principles

### 4.1 Keep logical groups and physical nodes decoupled

collect 层的 logical group 不要求和 render 落点一一对应。

典型例子：

- `persona.begin_dialogs` 属于 `persona` group，但落到 `history/begin_dialogs`
- `session.*` 属于 `session` group，但落到 `system/session/*`
- `capability.tools_schema` 属于 `capability` group，但落到 `tools/function_tools`

### 4.2 Keep history as real conversation only

`history` 只表达真实或预设对话：

- `persona.begin_dialogs`
- `conversation.history`

以下内容不伪装成历史消息：

- memory
- knowledge
- policy
- session

### 4.3 Keep schema data structured

工具、handoff、文件、图片等结构化信息优先保真，不为“好看”过早展开为文案。

### 4.4 Keep whitelist out of prompt body

`persona.tools_whitelist` 与 `persona.skills_whitelist` 只作为 renderer 过滤输入，不进入 prompt 正文。

### 4.5 Omit empty nodes

树里没有正文、没有有效子节点的空标签不进入最终渲染结果。

这条规则同时适用于：

- 空的 persona segment
- 只有骨架没有内容的中间路径节点
- 空的 user / session / memory 子标签

这样可以减少调试噪音，也避免把空结构暴露给模型。

## 5. Module Mapping Summary

| Logical Group | Slot | Render Target | Prompt Body | Meta Only | Notes |
|---|---|---|---|---|---|
| `system` | `system.base` | `system/core` | raw text | source info | 原样放入 |
| `system` | `system.tool_call_instruction` | `system/core` | raw text | tool schema mode 等 | 原样放入 |
| `persona` | `persona.segments` | `system/persona` | structured segments | persona source info | 优先于 `persona.prompt` |
| `persona` | `persona.prompt` | `system/persona` | raw text | persona source info | 仅在无 segments 时 fallback |
| `persona` | `persona.begin_dialogs` | `history/begin_dialogs` | begin dialogs 内容 | persona source info | 不放入 system |
| `persona` | `persona.tools_whitelist` | not rendered | none | whole slot | 只参与 tools 过滤 |
| `persona` | `persona.skills_whitelist` | not rendered | none | whole slot | 只参与 skills 过滤 |
| `input` | `input.text` | `user_input/text` | raw text | slot meta | 当前有效文本 |
| `input` | `input.quoted_text` | `user_input/quoted/text` | raw text | slot meta | 引用正文 |
| `input` | `input.quoted_images` | `user_input/quoted/images/image` | `ref` | `transport` `resolution` `reply_id` | 不 dump 原始 JSON |
| `input` | `input.images` | `user_input/attachments/images/image` | `ref` | `transport` | 当前消息图片 |
| `input` | `input.files` | `user_input/attachments/files/file` | `name` `ref` | `source` `reply_id` | `ref` 优先 `url` 否则 `file` |
| `session` | `session.datetime` | `system/session/datetime` | `text` | `iso` `timezone` `source` | 给模型可读时间 |
| `session` | `session.user_info` | `system/session/user_info` | `nickname` `platform_name` `group_name` `is_group` | `user_id` `umo` `group_id` | 不把 id 打进正文 |
| `policy` | `policy.safety_prompt` | `system/policy/safety` | raw text | config info | 原样放入 |
| `policy` | `policy.sandbox_prompt` | `system/policy/sandbox` | raw text | runtime info | 原样放入 |
| `conversation` | `conversation.history` | `history/conversation/turn/*` | user/assistant 文本 | `format` `source` `conversation_id` `turn_count` | 展开为 turn 结构 |
| `knowledge` | `knowledge.snippets` | `context/knowledge/snippets` | `text` | `query` `format` `query_source` | v1 不拆多 snippets |
| `memory` | `memory.topic_state` | `context/memory/topic_state` | useful summary fields | technical fields | 不混入 history |
| `memory` | `memory.short_term` | `context/memory/short_term` | useful summary fields | technical fields | 同上 |
| `memory` | `memory.experiences` | `context/memory/experiences/experience` | summary fields | technical fields | 同上 |
| `memory` | `memory.long_term_memories` | `context/memory/long_term_memories/memory` | summary fields | technical fields | 同上 |
| `memory` | `memory.persona_state` | `context/memory/persona_state` | state fields | technical fields | 同上 |
| `capability` | `capability.skills_prompt` | `system/capability/skills` | rendered skills prompt | runtime / counts / filters | 应用 `persona.skills_whitelist` |
| `capability` | `capability.subagent_router_prompt` | `system/capability/subagent_router` | raw text | config info | 原样放入 |
| `capability` | `capability.tools_schema` | `tools/function_tools/tool` | no raw schema dump | full schema payload | 应用 `persona.tools_whitelist` |
| `capability` | `capability.subagent_handoff_tools` | `tools/subagent_handoff/tool` | no raw schema dump | full schema payload | 不模拟 duplicate removal |

## 6. Detailed Subtrees

### 6.1 Input

```text
user_input
├─ text
├─ quoted
│  ├─ text
│  └─ images
│     └─ image
└─ attachments
   ├─ images
   │  └─ image
   └─ files
      └─ file
```

### 6.2 Conversation

```text
history
├─ begin_dialogs
└─ conversation
   └─ turn
      ├─ user
      └─ assistant
```

### 6.3 Memory

```text
context
└─ memory
   ├─ topic_state
   ├─ short_term
   ├─ experiences
   │  └─ experience
   ├─ long_term_memories
   │  └─ memory
   └─ persona_state
```

### 6.4 Capability

```text
system
└─ capability
   ├─ skills
   └─ subagent_router

tools
├─ function_tools
│  └─ tool
└─ subagent_handoff
   └─ tool
```

## 7. Module Decisions

### 7.1 `render_system_context()`

- 目标节点：`system/core`
- `system.base` 与 `system.tool_call_instruction` 原样进入
- 不额外改写内容

### 7.2 `render_persona_context()`

- 优先渲染 `persona.segments`
- 没有 segments 时才 fallback 到 `persona.prompt`
- `persona.begin_dialogs` 明确落到 `history/begin_dialogs`
- whitelist 不进入正文
- segment 标签尽量直接使用现有 segment key

### 7.3 `render_input_context()`

- 当前轮文本、引用文本、当前附件、引用附件分开
- 图片只保留 `ref` 作为正文主值
- 文件保留 `name` 和 `ref`
- 结构化字段如 `transport` / `resolution` 保留在 meta
- compile 阶段优先产出结构化 content parts：
  - 文本 -> `type=text`
  - 图片 -> `type=image_url`
  - 文件 -> 内部扩展 part（如 `type=file_ref`）
- 这层只保留 provider-adaptable IR，不在 base renderer 里提前做各家 provider 的最终格式转换

### 7.4 `render_session_context()`

- `session` 逻辑上独立，物理上落到 `system/session`
- `session.datetime` 给模型看可读时间文本
- `session.user_info` 只暴露有助于回复风格的字段
- 各类 ID 放 meta

### 7.5 `render_policy_context()`

- policy prompt 原样进入 `system/policy`
- `safety` 在前，`sandbox` 在后
- 不做二次改写

### 7.6 `render_conversation_context()`

- `conversation.history` 展开为 `turn -> user / assistant`
- 只保留消息内容
- `format` / `source` / `conversation_id` / `turn_count` 留在 meta

### 7.7 `render_knowledge_context()`

- `knowledge.snippets` 放入 `context/knowledge/snippets`
- compile 阶段生成独立 `_no_save` user context message，不进入 `system_prompt`
- 正文只保留 `text`
- `query` 等调试字段留在 meta

### 7.8 `render_memory_context()`

- memory 全部放 `context/memory`
- compile 阶段生成独立 `_no_save` user context message，不进入 `system_prompt`
- 不伪装成历史消息
- 只渲染对模型理解状态有帮助的字段
- 技术性字段留在 meta

### 7.9 `render_capability_context()`

- `skills_prompt` -> `system/capability/skills`
- `subagent_router_prompt` -> `system/capability/subagent_router`
- `tools_schema` -> `tools/function_tools/tool`
- `subagent_handoff_tools` -> `tools/subagent_handoff/tool`
- `tools/function_tools/tool/parameters` v1 不展开，直接保留原 schema

## 8. Implementation Implications

为满足这些落位规则，render 层需要支持：

- 一个 logical group 写入多个物理节点
- renderer 能按路径解析任意 target node
- tree node 既能承载正文，也能承载结构化 meta payload

这意味着后续实现时不能继续假设：

- 一个 group 只对应一个 target
- 所有 slot 都能直接 `str(value)` 写到默认节点

## 9. Current Status

当前可作为实现基线的结论：

- collect 协议先不改
- selector 继续保持 passthrough
- render 先完成树构建与模块渲染规则
- provider-specific compile 后续单独细化
- 空节点默认裁剪，不进入最终输出
