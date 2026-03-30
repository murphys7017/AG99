# AG99 路线图（原 MK2）

> 说明：本项目历史名为 MK2，部分文档标题保留以便对齐历史记录。

最后更新：2026-02-23

## 1. 当前状态

主链路已稳定运行：

1. `Adapter -> Bus -> Router -> Worker -> Gate -> AgentQueen -> emit -> Bus -> Egress`
2. System Reflex + Nociception 可闭环运行
3. Memory 已接入 Core 主流程（event/turn，fail-open）
4. 测试分层可用（offline / integration）

## 2. 已完成里程碑

### A. 基础主干

1. InputBus / SessionRouter / SessionState / Core worker
2. 多 session 隔离与串行语义
3. session GC 与生命周期管理

### B. Gate 与系统反射

1. Gate pipeline（scene/feature/scoring/dedup/policy/finalize）
2. `DROP/SINK/DELIVER` 动作语义
3. Gate 配置热更新与运行时 override
4. System Reflex 建议调节（白名单 + cooldown + TTL）

### C. Agent Phase0/1

1. AgentQueen 编排主链路
2. Rule/LLM/Hybrid pool_selector
3. ContextBuilder + PoolRouter + Aggregator + Speaker
4. 失败 fallback 与回流防循环

### D. Memory 接入

1. Core 自动初始化（可关闭）
2. Event 持久化
3. Turn 生命周期持久化
4. 失败队列落盘、轮转、dead-letter

### E. 文档治理

1. Active/Archive 文档分层
2. ADR 与模块深潜文档对齐代码
3. 部署/测试文档保持可执行命令

## 3. 下一阶段（优先级）

### P1：Agent 执行能力补齐

1. 落地 `code/plan/creative` 可执行 pool（不再只回退 chat）
2. 对接真实工具调用与结果回灌
3. 补强 pool 级指标与错误分类

### P2：可观测性增强

1. 引入跨层 trace_id
2. 增加 Gate/Agent/Memory 分段延迟指标
3. 统一结构化日志字段

### P3：结构治理

1. 拆分 `core.py`（worker orchestration / system handler / memory hooks）
2. 收敛实验脚本与主干接口，减少双轨实现

## 4. 执行建议

1. 每次迭代先保证 `pytest -m "not integration" -q` 通过。
2. integration 测试独立 Job 执行。
3. 文档按 Active 单文档策略维护，避免同主题多版本漂移。

