# 配置归属与 Metadata Registry 草案

## 1. 目标

将当前以 `*_group` 为中心的原始 metadata 转换为稳定的、可供 Dashboard 工作区消费的配置注册表。迁移期间保留旧字段，避免一次性修改配置文件和插件契约。

## 2. 建议结构

```ts
type ConfigWorkspace =
  | 'persona'
  | 'intelligence'
  | 'channels'
  | 'knowledge'
  | 'capabilities'
  | 'automation'
  | 'operations';

type ConfigScope = 'system' | 'platform' | 'profile' | 'persona' | 'session';

interface ConfigFieldMeta {
  path: string;
  workspace: ConfigWorkspace;
  section: string;
  scope: ConfigScope;
  type: string;
  order?: number;
  advanced?: boolean;
  restartRequired?: boolean;
  dangerous?: boolean;
  secret?: boolean;
  deprecated?: boolean;
  legacyGroup?: string;
  legacySection?: string;
}
```

后端响应建议保留兼容层：

```json
{
  "registry_version": 1,
  "metadata": {
    "workspace": "intelligence",
    "section": "profiles",
    "scope": "profile",
    "fields": {}
  },
  "legacy_metadata": {}
}
```

实际落地初期可以继续返回现有嵌套 metadata，同时在每个 group/section 上附加新字段；前端 registry 负责两种形状归一化。

## 3. 初步归属表

| 现有组/能力 | 工作区 | 主要 section | scope |
| --- | --- | --- | --- |
| `ai_group.agent_runner` | Intelligence | execution profiles | profile |
| `ai_group.ai` | Intelligence | model defaults | profile |
| `ai_group.persona` | Persona | identity and expression | persona |
| `ai_group.knowledgebase` | Knowledge | retrieval | profile |
| `ai_group.websearch` | Capabilities | web access | profile |
| `ai_group.file_extract` | Knowledge | document parsing | profile |
| `ai_group.agent_computer_use` | Capabilities | computer and shell | system |
| `ai_group.proactive_capability` | Automation | proactive behavior | persona |
| `ai_group.truncate_and_compress` | Intelligence | context policy | profile |
| `platform_group` | Channels | adapters/accounts/behavior | platform |
| `provider_group` | Intelligence | providers/models | profile |
| `plugin_group` | Capabilities | plugin runtime | system |
| `ext_group` | Operations | built-in system extensions | system |
| `interaction_middleware_group` | Operations | interaction runtime/diagnostics | system |
| `memory_group` | Knowledge | memory policy/state | profile |
| `system_group` | Operations | runtime/security/update | system |

这张表是 UI 归属，不代表后端模块必须搬家。后端所有权仍按当前领域边界维护。若某项能力同时影响两个层级，优先给出一个主 scope，其余归属通过 section、说明文案或后续子区块表达。

## 4. 归一化规则

1. 有明确用户任务的字段优先映射到对应 workspace。
2. 同一字段只能有一个主编辑位置；其他页面只能显示只读摘要或跳转。
3. `scope` 决定配置可编辑范围，不由页面路径推断。
4. `advanced=true` 的字段默认折叠，但搜索仍可命中。
5. `restartRequired=true` 必须在保存结果中返回影响说明。
6. `dangerous=true` 必须经过权限校验和二次确认。
7. `secret=true` 的字段只允许 masked read；更新时支持保留原值。
8. 插件/平台动态注入项必须提供稳定的 `workspace`、`section` 和来源标识。
9. 旧 group/section 仅用于迁移、诊断和旧链接兼容，不参与新导航排序。

## 5. Registry API 责任

### 后端

- 生成并校验 metadata。
- 注入平台、Provider、插件的动态字段。
- 返回配置版本、重启要求、权限和敏感字段标记。
- 保存时继续验证原始配置路径，不接受前端自定义路径写入。

### 前端

- 将旧 metadata 归一化为 `ConfigRegistry`。
- 按 workspace、section、scope、搜索词和 advanced 状态投影。
- 统一渲染保存状态、重启提示、危险确认和 masked secret。
- 将旧 URL 映射到 registry 中的稳定路径。

## 6. 迁移示例

旧结构：

```json
{
  "ai_group": {
    "metadata": {
      "ai": {
        "provider_settings": {
          "default_provider_id": {}
        }
      }
    }
  }
}
```

归一化后：

```json
{
  "path": "provider_settings.default_provider_id",
  "workspace": "intelligence",
  "section": "profiles",
  "scope": "profile",
  "legacyGroup": "ai_group",
  "legacySection": "ai",
  "advanced": false,
  "restartRequired": false
}
```

## 7. 退出条件

- 新工作区不再依赖 `ConfigPage.vue` 中的硬编码隐藏组集合。
- 所有配置字段都能通过 registry 找到 workspace、section 和 scope。
- 新旧 metadata 在同一配置数据上产生一致的保存结果。
- 动态平台/Provider/插件字段在刷新、切换配置和导入导出后仍保持稳定路径。
