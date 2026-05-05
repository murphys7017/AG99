# AstrBot Yakumo Fork

这是 `murphys7017/AstrBot` 对上游 `AstrBotDevs/AstrBot` 的 fork 说明页。

AstrBot 本身已经是一个成熟、知名的多平台 LLM 聊天机器人与 Agent 框架，所以这里不再重复上游 README 的产品介绍、部署广告和生态说明。本 README 只说明：这个仓库和上游有什么不同。

上游入口：

- 上游仓库：https://github.com/AstrBotDevs/AstrBot
- 官方文档：https://docs.astrbot.app/

## 核心差异

### 1. Yakumo 架构实验

本 fork 的主要方向是 Yakumo 架构实验：在保留 AstrBot 原有能力的基础上，逐步把主 Agent、prompt、memory、postprocess、interaction layer 等链路拆得更清楚。

相关文档集中在：

- `docs/Yakumo/`
- `docs/Yakumo/current-state.md`
- `docs/Yakumo/modules/README.md`

`docs/Yakumo` 描述的是本 fork 的真实代码状态、目标结构和开发记录，不是上游官方文档的镜像。

### 2. Prompt 管线不同

上游主线更偏向在主 Agent 链路里直接组织模型可见上下文。本 fork 新增并持续推进 `astrbot/core/prompt/*`，把 prompt 构建拆成：

- `collect`: 收集 system、persona、input、session、policy、memory、history、skills、tools、subagent、knowledge、extension 等上下文。
- `select`: 选择本轮真正进入模型请求的上下文。
- `render`: 渲染模型可见的 prompt/request 结构。
- `apply`: 将结果投影回 `ProviderRequest`。

相关位置：

- `astrbot/core/prompt/`
- `data/config/prompt/context_catalog.yaml`
- `docs/Yakumo/modules/prompt.md`

### 3. 新增 memory 系统

本 fork 新增 `astrbot/core/memory/*`，不是只依赖上游会话历史。当前已落地的主链路包括：

- 回合结束后写入 `TurnRecord`。
- 更新 topic / short-term memory。
- consolidation 生成 session insight / experience。
- experience Markdown 投影。
- long-term memory 第一版。
- 长期记忆文档、索引与 document search。
- 请求前读取 `MemorySnapshot`，供 prompt 管线使用。

仍在推进的部分：

- persona state / persona evolution 更新。
- memory selector/router。
- memory 对最终 prompt render 的完整接管。

相关位置：

- `astrbot/core/memory/`
- `docs/Yakumo/dev/memory/`
- `scripts/import_long_term_memory.py`
- `scripts/manage_identity_mappings.py`

### 4. 新增 interaction persona middleware

本 fork 新增 `astrbot/core/interaction/*`，用于把普通聊天请求拆成更接近“互动人格层”的流程。

它支持：

- `self_reply` / `delegate_to_core` / `hybrid` 路由。
- 进入核心 Agent 前先做 interaction decision。
- 即时口语回复。
- core task spec 注入。
- finalizer 对核心结果做最终表达整理。
- 流式输出观察与插话。
- 独立 interaction memory。
- 插件侧 prompt/result contribution 扩展点。

该能力是本 fork 的实验性增强，默认关闭，需要通过 `interaction_middleware` 配置显式启用。

相关位置：

- `astrbot/core/interaction/`
- `tests/unit/test_interaction_*.py`
- `docs/Yakumo/dev/dialog-worker-live-implementation-plan.md`
- `docs/Yakumo/dialog-worker-live-target-state.md`

### 5. Provider 与附件链路有本地适配

本 fork 保留并扩展了一些上游没有完整保留的 provider/附件适配，尤其是 Volcengine Ark / Doubao 方向：

- `astrbot/core/provider/sources/volcengine_ark_source.py`
- 默认 provider 配置中的 `volcengine_ark`
- 图片输入、文件 URI、附件 payload 的兼容性修复
- OpenAI-compatible provider 的若干稳定性修复

因此，本 fork 不会直接接受会删除这些本地 provider 能力的上游变更。

### 6. WebUI / WebChat 有本地语义

本 fork 的 WebUI / WebChat 改动和本地 prompt、checkpoint、interaction 语义有关，包含：

- conversation checkpoint。
- inline edit / regenerate / thread 相关兼容工作。
- provider 配置页调整。
- reasoning 展示、附件预览、复制行为修复。
- IME 输入和暗色模式细节修复。

这类改动不能简单按上游 WebUI 大改动全量覆盖，需要逐项判断是否符合本 fork 的 checkpoint/prompt 语义。

## 上游合并策略

本 fork 会继续吸收上游的安全修复、provider 稳定性修复、平台兼容修复和小范围 UI 修复。

但不会把以下本地架构直接删除或回退：

- `astrbot/core/prompt/**`
- `astrbot/core/memory/**`
- `astrbot/core/postprocess/**`
- `astrbot/core/interaction/**`
- 本地 provider 适配
- 本地 prompt/checkpoint 语义

上游变更取舍记录在：

- `docs/Yakumo/upstream-merge-ledger.md`

合并与修复原则：

- 不把 fallback 当作正确性证明。
- 不用默认值、重试或静默吞错掩盖主链路问题。
- 优先在根因位置修复，而不是在下游补校正。
- 本地 prompt/memory/interaction 架构优先作为当前 fork 的事实来源。

## 本地运行入口

Core：

```bash
uv sync
uv run main.py
```

Dashboard 开发模式：

```bash
cd dashboard
pnpm install
pnpm dev
```

默认端口：

- Core / API / Dashboard: http://localhost:6185
- Dashboard dev server: http://localhost:3000

## 许可证

本仓库继承上游 AstrBot 的许可证：`AGPL-3.0-or-later`。详见 `LICENSE`。
