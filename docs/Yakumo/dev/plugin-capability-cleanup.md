# 插件能力收口契约

日期：2026-09-18。此文取代旧方案中“两张 target 映射保持不变”和“保留 Interaction Prompt Contributor 独立入口”的限制。项目处于开发期，本次不保留旧配置读取或旧注册 API。

## 不变的架构

Personal 是唯一拟人对外表达窗口，即时回复、过程反馈、最终回复使用同一人格表达机制。Core 负责被委派的复杂工作。此次不新增快速 Agent、不改动作提示内容、不改变 OLV 的音频或动作协议。

能力治理分为：

- **Owner**：谁注册了能力。插件构造和初始化的注册作用域优先于类所在模块。
- **Kind**：Handler、LLM Hook、FunctionTool、Prompt Extension、结果贡献、Persona Effect、Sensor 等能力类型。
- **Permission**：插件激活、有效归属、配置 `plugin_set`、会话禁用列表。明确的空白名单拒绝普通插件。
- **Applicability**：能力声明的事件、平台、设备条件，例如 OLV 是否在线。
- **Target**：能力在哪个消费方使用。准入不等于目标选择。

进程级 Provider、Web API、后台任务仍由进程生命周期管理，不用会话禁用控制。它们的卸载治理不在本次范围内。

## 唯一配置格式

```json
{
  "interaction_middleware": {
    "plugin_capability_targets": {
      "my_plugin": {
        "llm_hooks": "personal_expression",
        "tools": {
          "*": "core",
          "read_memory": "personal_expression"
        }
      }
    }
  }
}
```

第一层键是插件的**注册名称**，与 `plugin_set` 一致，不再同时识别目录名、模块路径和名称。

- `llm_hooks` 只控制目标可路由的 LLM/Agent 生命周期 Hook。默认优先使用插件 `interaction_runtime_target` 声明，无声明时为 Personal。
- `tools` 只控制模型可执行的 FunctionTool。具体工具名优先于 `*`；缺省使用工具 `execution_targets` 声明，未声明则为 Core。
- Handoff 固定为 Core，不能通过配置迁移到 Personal。
- 输出装饰、发送后通知、TTS 状态等固定链路 Hook 不受此目标表移动，但对话级调用受准入约束。
- Prompt 目标由返回值的 `meta.targets` 指定；Persona Effect 固定属于 Personal，不提供无意义的目标选项。
- 不再读取 `plugin_runtime_targets` / `plugin_tool_targets`。本地测试配置里的旧映射已清除，新表为空，需要按新格式重新设置所需覆盖。

前端在插件富化与能力目标配置中按“插件、能力类型、工具名称、生效链路”编辑，随配置文件正常保存。后端保存前验证结构与目标枚举。能力清单与运行时共用解析函数；没有具体会话时准入显示 `not_evaluated`，不能把默认配置下的清单当作会话授权结果。

## 唯一 Prompt 注册入口

```python
class MyPromptCollector:
    plugin_id = "my_plugin.context"
    priority = 50

    async def collect(self, event, plugin_context, config=None, *, provider_request=None):
        return [
            PromptExtension(
                plugin_id=self.plugin_id,
                mount="system",
                value_kind="text",
                value="插件事实",
                meta={"targets": ["persona"]},
            )
        ]

context.register_prompt_extension_collector(MyPromptCollector())
```

删除 `register_interaction_prompt_contributor` 及其注册表、卸载入口、独立收集器和独立超时逻辑。多个插件的同 mount 贡献在一处聚合，不能各自创建竞争的 `extension.system` slot。

收集器并行运行，按优先级稳定组装。整个组使用 `plugin_enrichment_timeout`；Interaction 内它只是现有 `TurnDeadlineBudget` 的阶段上限，不新增轮次 deadline owner。单个可选收集器失败不丢弃其它完成项，取消时回收未完成任务。

`persona_plugin_context_mode` 继续控制等待策略。`best_effort` 不等待未完成的可选插件包；`wait_complete` 等待统一组预算内的结果。删除无效的 `compact_context` 参数。`contributor_timeout` 仅用于结果贡献者。

AG99live 的身份、动作和远程执行提示统一注册到上述入口；原有文本、动作能力目标和远程执行 `core` 目标保留，动作动态上下文显式标为 `persona`。

## 准入与失败

每个事件首写快照，Interaction TurnState 与事件引用同一对象。快照为空不回退实时注册表。未登记 Owner 默认拒绝；明确的 Core 内置能力单独识别。卸载或替换插件实例会使旧轮次中的注册授权失效，新插件不能继承旧轮次授权。

普通 Handler 在执行过滤器前执行准入；输出装饰和发送后 Hook 也执行准入。Sensor 没有 event，按目标会话使用同一个快照构建和裁决入口，不另写一套白名单规则。读取会话策略失败必须报错，不能当作没有禁用配置。

`required_per_segment is True` 的 Effect 仍先过 Permission 和 Applicability。通过准入后，动态 schema 或条件判定异常必须显式失败，不能静默删掉必发契约；被用户禁用的插件不会继续要求必发。可选 Effect 的准备失败记录诊断后跳过。

## 验收与边界

基础验收覆盖：同 mount 的多个 Prompt 合并、快照后新加载/替换 Owner 拒绝、必发 Effect 准备失败、空白名单拒绝、Hook/FunctionTool 目标独立，以及 Personal 等待策略。

静态检查与基础测试不能替代实机验收。仍需用户启动 AstrBot 和 OLV，检查正常拟人回复、Core 委派、动作、会话禁用后的表现，以及配置保存后下一轮的目标选择。不自动启动或重启 AstrBot，不把旧测试数据迁移兼容层留在运行代码中。
