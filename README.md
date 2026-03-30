# 青鸟 AG99
> 欢迎点燃自己的历史

一个长期运行、事件驱动、多会话隔离的 Agent Runtime。  
项目目标不是“单次回答最聪明”，而是构建一个 **稳定、可控、可演进** 的智能体运行时系统。
*历史名称：`mk2`（文档和部分注释中仍可能出现）*

---

## 为什么是 AG99（青鸟）

AG99 是项目的技术代号，**青鸟**是项目的中文名。  
它代表的不只是一个 Agent Demo，而是一套可以持续演进的 runtime：有输入总线、有会话隔离、有前置 Gate、有受控调节、有持久化、有异步出站。

如果你希望它最终成为一个“作品”，而不只是“能跑起来的脚本”，那这个名字很合适。



## Vision：让它像“有神经反射 + 大脑结构”的系统

AG99 追求的不是把一个聊天模型包装成 Demo，而是构建一个**长期运行**的智能体生命体征：
- **反射弧（快、确定性）**：在进入 Agent 之前完成快速决策与自我保护
- **大脑皮层（慢、可推理）**：在可控边界内做规划、整合、多步思考
- **记忆系统（可积累）**：把事件与轮次沉淀为可复用的长期结构
- **行动系统（可扩展）**：输出与工具调用走异步通路，不拖垮核心循环

最终目标（长期）：逐步实现 **自组织 / 自发现 / 自编程**，但始终以“可控、可观测、可回滚”为前置条件。

---

## 项目定位

AG99 是一个 **事件驱动 Agent Runtime**，强调以下能力：

- **多会话隔离**：每个 `session_key` 一个串行 worker，session 内顺序执行、session 间并发
- **前置 Gate 决策**：在 Agent 前做快速规则决策（`DROP / SINK / DELIVER`）
- **Agent 编排**：以 `AgentQueen` 为中心组织 pool_selector / context / pool / aggregator / speaker
- **系统自调节**：通过 System Reflex 对系统痛觉信号与 tuning suggestion 做受控处理（白名单、TTL、冷却）
- **Memory 持久化（fail-open）**：事件与轮次写入失败不阻断主链路
- **异步 Egress**：输出走后台队列，不阻塞 worker 主循环

---

## 类脑结构映射（帮助理解整体架构）

  | 工程模块                                              | 类比                  | 作用                                        |
  | ----------------------------------------------------- | --------------------- | ------------------------------------------- |
  | Adapters / Observation / InputBus                     | 感觉系统 + 编码       | 把外界输入统一成事件协议                    |
  | Gate（DROP/SINK/DELIVER）                             | 脊髓反射 / 脑干快通路 | 快速、同步、确定性决策；优先保护系统        |
  | SystemReflex                                          | 内稳态 / 痛觉与调参   | 对“系统信号”做受控调节（白名单、TTL、冷却） |
  | AgentQueen（pool_selector/context/pool/aggregator/speaker） | 前额叶/执行功能       | 负责规划、整合上下文、调度子能力            |
  | Memory（fail-open）                                   | 海马体 / 长期记忆     | 记忆失败不阻断生存主链路                    |
  | Egress（异步）                                        | 运动输出通路          | 把输出/外部 IO 放到后台，避免阻塞核心循环   |

---

## 当前主链路（已落地）

```text
Adapter
  -> Observation
  -> InputBus
  -> SessionRouter
  -> SessionWorker
  -> Gate
  -> AgentQueen
  -> emit 回流 Bus
  -> Egress
```

### 核心处理语义（当前实现）
- 跨模块统一事件协议：`Observation`
- Gate 输出决策与副作用：`GateDecision` / `GateOutcome(decision + emit + ingest)`
- `DELIVER + MESSAGE` 才会触发 Agent 主处理
- Agent 输出会回流 Bus，并通过回流保护避免自触发循环
- Memory / Egress 默认采用 **fail-open** 思路（异常不直接拖垮主流程）

---

## 设计原则（重要）

### 1) 边界清晰
- Gate 不直接调用 Agent
- Agent 不直接改 Gate 配置
- 运行时调节统一通过 `SystemReflex + GateConfigProvider.update_overrides()`
- 跨模块状态流统一走 `Observation`（尽量避免隐藏 side-channel）

### 2) 并发模型简单可控
- **会话内串行，会话间并发**
- `SessionState` 仅在对应 session worker 中串行修改

### 3) 先稳定，再变聪明
- Gate 保持快速、同步、确定性（不在 Gate 中做 LLM 调用）
- LLM provider 的同步调用由异步链路通过线程包装，避免阻塞事件循环

---

## 核心模块概览

```text
src/
├─ core.py                 # 主编排与 worker 生命周期（当前主入口逻辑集中）
├─ input_bus.py            # 异步输入总线
├─ session_router.py       # 按 session_key 分流、会话 inbox 与状态
├─ gate/                   # 前置决策 pipeline（scene/feature/scoring/policy/...）
├─ agent/                  # AgentQueen 与 pool_selector/context/pool/aggregator/speaker
├─ system_reflex/          # 自调节控制器（白名单、TTL、冷却、回滚）
├─ memory/                 # Event/Turn 持久化、vault、失败队列
├─ adapters/               # 输入/输出适配器
└─ schemas/                # Observation 等跨层协议
```

---

## 快速开始

### 环境要求
- Python 3.11+
- 推荐使用 `uv`

### 安装依赖
```bash
uv sync
```

### 启动系统
```bash
uv run python main.py
```

### 本地离线回归（推荐第一步）
```bash
uv run pytest -m "not integration" -q
```

若本机 `uv run pytest` 有兼容问题，可回退为：

```bash
pytest -m "not integration" -q
```

### 集成测试（依赖真实外部服务）
```bash
uv run pytest -m integration -q
```

> 集成测试依赖真实 provider / API / 本地服务；部分 live 用例受环境变量控制（如 `RUN_LLM_LIVE_TESTS=1`）。

---

## 配置说明（高频）

### `config/gate.yaml`
负责 Gate 策略与运行时行为的关键配置，例如：
- `scene_policies`
- `rules`
- `drop_escalation`
- `overrides`
- `budget_thresholds`
- `budget_profiles`

支持热更新（通过配置 provider 检测并替换快照）。

### `config/llm.yaml`
- 使用环境变量注入密钥（避免明文提交）
- 配置 provider / model 等参数

### `config/memory.yaml`
- 控制关系库、vault、失败队列等
- Memory 初始化失败默认不阻断主流程（fail-open）

### `config/agent/`
- Agent 总配置与 pool_selector 子配置
- 默认规划路径可使用 rule / llm / hybrid 方案

---

## 运行时行为要点（排障常用）

- 每个 session 一个串行 worker
- session 之间并发执行
- worker 主循环会先记录状态、再过 Gate、再决定是否进入 Agent
- 输出通过独立 egress 队列与后台 loop 发送，避免外部 IO 阻塞核心 worker
- Agent 回流消息带防循环保护（避免自激活）

---

## 文档导航

### Active（当前生效）
- `docs/README.md`：文档总览（Active / Reference / Experimental / Archive）
- `docs/DEPLOYMENT.md`：部署与运行
- `docs/TESTING.md`：测试分层与命令
- `docs/AGENT_SYSTEM_ARCHITECTURE.md`：Agent 子系统架构与链路说明
- `docs/MEMORY.md`：Memory 当前实现
- `docs/PROJECT_MODULE_DEEP_DIVE.md`：模块深潜（维护/排障入口）
- `docs/DESIGN_DECISIONS.md`：仍有效的 ADR
- `docs/GATE_COMPLETE_SPECIFICATION.md`：Gate 设计规范（用于策略/重构讨论）
- `docs/SYSTEM_REFLEX_SPECIFICATION.md`：System Reflex 规范
- `docs/ROADMAP.md`：阶段计划与下一步优先级

### Reference（开发参考）
- `docs/AGENT_REQUEST_STRUCTURE.md`
- `docs/AGENT_REQUEST_QUICK_REFERENCE.md`
- `docs/CONTEXT_CATALOG_PROFILE_SUMMARY.md`：Context Catalog / Prompt Profile / Presets 配置规范与验证快照

### Experimental（实验链路）
- `tools/demo_e2e.py`
- `docs/demo_e2e.md`

> 实验链路不作为当前稳定运行基线；部署/验证优先以 `main.py` + Active 文档为准。

---

## 当前已知边界（务实说明）

- 默认 pool 路由实际可用池以 `chat` 为主，`code / plan / creative` 在未注入自定义 pool 时会回退到 `chat`
- 单个 session 是串行语义，慢请求会阻塞该 session 后续消息（这是设计选择，不是 bug）
- `core.py` 当前承担较多编排职责，后续会继续拆分与收敛

---

## 路线图（下一阶段方向）

### P1：Agent 执行能力补齐
- 落地 `code / plan / creative` 可执行 pool（不再仅回退 chat）
- 对接真实工具调用与结果回灌
- 补强 pool 级指标与错误分类

### P2：可观测性增强
- 引入跨层 `trace_id`
- 增加 Gate / Agent / Memory 分段延迟指标
- 统一结构化日志字段

### P3：结构治理
- 拆分 `core.py`
- 收敛实验脚本与主干接口，减少双轨漂移



### Self-*（长期目标，分阶段落地）

#### Self-Discovery（自发现）
- 能力扫描：发现本机硬件/服务/API/工具能力 → 注册为 capability
- 权限与沙箱：每个 capability 都有可审计权限边界
- 可观测：发现过程可追踪（trace_id / 结构化日志）

#### Self-Organization（自组织）
- pool 动态路由：根据指标与策略选择最合适的执行链路
- 受控自调节：SystemReflex 基于“痛觉信号”进行白名单内的策略更新
- 稳定性红线：超过阈值自动降级、隔离、熔断

#### Self-Programming（自编程）
- 代码改动走“提案 → 补丁 → 测试 → 门禁 → 合并”的闭环
- 默认沙箱执行与回放验证，禁止直接在生产链路自修改
- 所有自编程产物可追溯、可回滚、可冻结

---

## 开发建议

每次迭代建议按这个顺序做：

1. 先保证离线回归通过  
   `pytest -m "not integration" -q`
2. 再跑你改动相关的定向测试
3. 最后再跑 integration（如有外部依赖）
4. 代码行为变化时，同步更新对应 Active 文档（避免文档漂移）

---

## License

MIT License.

See [LICENSE](./LICENSE) for details.

---

## 取名灵感

项目代号 **AG99**、中文名 **青鸟**。

名称灵感来自 **SCP-CN-1559《青鸟》**。

这个项目希望做的，也不只是“能回答问题”的程序，而是一套能够长期运行、持续演进、逐步形成自己秩序的 Agent Runtime。

## TODO

- [ ] 为 pool_selector 增加更细粒度的信息规划（从“类型级”提升到“item 级/字段级”）
- [ ] 预算控制补充摘要与压缩策略，减少简单硬截断
- [ ] 优化 prompt render 的 Lost-in-the-middle 问题

