# Memory Lifecycle

本文件记录当前 AstrBot memory 系统的生命周期链路与各阶段产物。

## 1. 总体链路

当前共识链路：

- `TurnRecord`
- `TopicState`
- `ShortTermMemory`
- `SessionInsight`
- `Experience`
- `LongTermMemory`
- `PersonaState`

可理解为：

`回合记录 -> 短期更新 -> 中期抽象 -> 长期沉淀 -> 人格状态更新`

## 2. 生命周期阶段

### 2.1 回合记录阶段

触发时机：

- 当前回合完成后
- 由 `Post Process System` 驱动

输入：

- 当前用户输入
- 当前助手输出
- 当前会话标识
- 当前时间戳

输出：

- `TurnRecord`

说明：

- `TurnRecord` 是 memory 系统的原始输入材料。
- 当前实现中 `TurnRecord` 每轮都会写入。
- 这一层不直接生成长期记忆。
- 这一层的目标是保证后续所有 memory 更新都有统一来源。

## 3. 短期更新阶段

触发时机：

- `TurnRecord` 写入后按配置判断是否执行
- 仍然属于当前回合后的轻量更新

输入：

- 当前 `TurnRecord`
- 最近若干轮历史材料

输出：

- `TopicState`
- `ShortTermMemory`

说明：

- `TopicState` 表示当前会话正在围绕什么继续聊。
- `ShortTermMemory` 表示下一轮仍需要继续带着的短期上下文抽象。
- 这一阶段只做轻量分析，不做深度人格更新。
- 当前实现会在冷启动时立即分析；之后按 `short_term.update_interval_turns` 或 `short_term.update_min_chars` 节流。
- 当前实现会用一次 `short_term_update` stage 同时更新 `TopicState` 与 `ShortTermMemory`。

## 4. 中期抽象阶段

触发时机：

- 当短期材料累计到一定数量后
- 或按固定时间窗口批量执行
- 或在会话切换时执行

输入：

- 一段时间内的 `TurnRecord`
- 一段时间内的 `TopicState`
- 一段时间内的 `ShortTermMemory`

输出：

- `SessionInsight`
- `Experience`

说明：

- `SessionInsight` 是针对一段对话阶段的中期抽象。
- `Experience` 是和时间线强相关的事件流对象。
- 这一阶段负责把多个短期片段整理成更稳定的中期记忆。

## 5. 长期沉淀阶段

触发时机：

- 当前实现按回合后阈值触发
- 不是独立 scheduler
- 不要求每轮都执行

输入：

- 一批 `Experience`
- 一批 `SessionInsight`
- 当前已有的长期记忆对象

输出：

- `LongTermMemory`

说明：

- `LongTermMemory` 是高价值长期认知对象。
- 长期记忆采用 `SQLite` 索引 + `Markdown` 正文。
- 这一阶段允许对已有长期记忆对象做补充与更新。
- 当前向量索引开启时，会先严格校验 provider 绑定与 embedding provider 可用性。

## 6. 人格状态更新阶段

触发时机：

- 定时任务
- 与长期沉淀阶段同级或相邻

输入：

- `Experience`
- `SessionInsight`
- `LongTermMemory`
- 当前已有 `PersonaState`

输出：

- 更新后的 `PersonaState`
- `PersonaEvolutionLog`

说明：

- `PersonaState` 是当前生效的动态人格状态。
- `PersonaEvolutionLog` 只用于溯源，不作为日常对话主输入。
- 这一阶段不改写静态 persona 底座。

## 7. 请求前读取阶段

触发时机：

- 新一轮请求开始前

输入：

- 当前 `TopicState`
- 当前 `ShortTermMemory`
- 当前可用 `Experience`
- 当前可用 `LongTermMemory`
- 当前 `PersonaState`

输出：

- `MemorySnapshot`

说明：

- `MemorySnapshot` 是给 Prompt System 消费的只读视图。
- Prompt System 只读取 snapshot，不直接参与 memory update。
- `MemoryService.get_snapshot(...)` 默认返回完整只读视图；Prompt 注入裁剪由 `MemoryCollector` 使用独立 read options 完成。
- 当前 snapshot 只用 latest turn 的 `canonical_user_id` 判断是否允许读取中长期层。
- latest turn 没有身份映射时，只返回短期层。
- 有 query 时：
  - `LongTermMemory` 先通过文档搜索检索
  - `Experience` 再通过命中 story 的 links 回查
  - 剩余名额用最近经验补齐

## 8. 各阶段职责边界

### 8.1 `TurnRecord`

- 原始材料
- 不直接参与长期人格更新

### 8.2 `TopicState`

- 当前主题连续性
- 服务下一轮短期接续

### 8.3 `ShortTermMemory`

- 最近若干轮短期抽象
- 服务下一轮连续上下文

### 8.4 `SessionInsight`

- 一段会话阶段的中期抽象
- 是短期层进入中长期层的桥

### 8.5 `Experience`

- 时间线事件流
- 是长期记忆和人格状态更新的重要输入

### 8.6 `LongTermMemory`

- 高价值长期认知对象
- 保存为文档对象

### 8.7 `PersonaState`

- 当前生效的人格动态值
- 缓慢变化

## 9. 当前第一版实现顺序

推荐顺序：

1. `TurnRecord`
2. `TopicState`
3. `ShortTermMemory`
4. `SessionInsight`
5. `Experience`
6. `LongTermMemory`
7. `PersonaState`

说明：

- 第一版先打通短期层与回合后更新链路。
- 中期层和长期层可以逐步补齐。
- 人格状态更新应当晚于短期层落地。

## 10. 当前结论

当前 memory 生命周期可以收敛为：

- 每轮先记录 `TurnRecord`
- 冷启动立即更新 `TopicState` 与 `ShortTermMemory`，之后按配置节流
- 累计后批量生成 `SessionInsight` 与 `Experience`
- 定时生成或更新 `LongTermMemory`
- 定时更新 `PersonaState` 与 `PersonaEvolutionLog`
- 请求前统一读取为 `MemorySnapshot`
