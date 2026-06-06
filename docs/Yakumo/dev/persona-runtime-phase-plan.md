# Persona Runtime Phase Plan

这份文档记录 Yakumo 下一阶段的实施计划。它不是当前代码说明，也不是最终目标态说明。

当前共识：

- 第一阶段先完成输入输出解耦。
- interaction middleware 后续扩展为 `Persona Runtime Shell`，作为 Adapter 与 Core 之间的人格运行层。
- runtime 需要重新整理，但第一步不是服务化拆分，而是先把 Input / Persona / Core / Output 的边界接稳。

## 计划主线

目标流程：

```text
Adapter
  -> Input Runtime / Observation
  -> Persona Runtime Shell
      -> Core Agent / Tools / Capabilities when needed
  -> Output Runtime / Output Gateway
  -> Finalized Material
  -> Postprocess / Memory / Persona State Update
```

这个流程里，复杂任务进入 Core；普通寒暄、轻量反应、presence、状态表达可以由 Persona Runtime Shell 决定是否直接处理或只产出 output intent。

## Phase 1: 输入输出解耦

第一阶段先接稳输入和输出，不急着实现完整心跳、潜意识或长期后台人格循环。

### Input Runtime

Input Runtime 负责把外部和内部输入整理成统一 observation。

输入来源包括：

- 平台适配器消息
- WebUI 输入
- 语音、图片、文件、引用消息
- 主动事件
- 后续心跳、idle tick、任务状态、反思触发等内部信号

Observation 至少应表达：

- source / platform / session / conversation
- sender / audience / visibility
- privacy / permission / importance
- raw input 与 materialized input
- attachments / quoted material
- 初步 route / gate 线索

### Output Runtime

Output Runtime 负责把内部 output intent 落到具体目标。

输出目标至少应区分：

- chat reply
- streaming chat reply
- voice / TTS
- Desktop Body / Presence Client
- task status
- local-only notification
- silent finalized material

聊天窗口回复只是 output target 之一，不再是唯一输出形态。

### Finalized Material

Output Runtime 完成后必须产出 finalized material。

Memory / postprocess / persona state 更新只消费 finalized material，不从临时 visible output 或平台发送结果反推完整回合语义。

## Phase 2: Persona Runtime Shell

在输入输出边界稳定后，interaction middleware 扩展为 `Persona Runtime Shell`。

它负责一轮人格运行：

- 接收 observation
- 组合 Effective Persona、memory snapshot、persona state、关系/话题状态和 capability context
- 判断 self reply / delegate to core / hybrid / local presence / silent
- 委托 Core 处理复杂任务
- 向 Output Runtime 提交 output intent
- 交付 finalized material 给 postprocess

它不应拥有长期数据本体：

- base persona 仍由 persona manager / repository 管理
- memory 仍由 memory service 管理
- persona state 后续由 `PersonaStateService` 管理
- provider / tools / skills / subagent 仍通过 gateway 或 capability registry 接入

## Phase 3: Background Mind

完成 Phase 1 和 Phase 2 后，再接默认小模型、心跳、潜意识和主动 presence。

这些能力不应绕过主链路，而应作为内部 observation / intent source 接入：

```text
heartbeat / idle tick / task state / reflection trigger
  -> internal observation
  -> Persona Runtime Shell
  -> output intent / silent material / persona state update
```

这样可以避免后台人格直接发消息、直接写 memory、或绕过隐私/可见性判断。

## 非目标

第一阶段不追求：

- 完整服务化拆分
- 完整人格反思系统
- 默认小模型常驻循环
- 直接把所有 middleware 状态升级成长期人格状态
- 让 AG99live 直接监听所有 session 原文

第一阶段只追求把输入、人格运行、核心执行、输出和 finalized material 的边界接稳。
