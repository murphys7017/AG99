---
outline: deep
---

# AstrBot 插件开发指南 🌠

欢迎来到 AstrBot 插件开发指南！本章节将引导您如何开发 AstrBot 插件。在我们开始之前，希望你能具备以下基础知识：

1. 有一定的 Python 编程经验。
2. 有一定的 Git、GitHub 使用经验。

欢迎加入我们的开发者专用 QQ 群: `975206796`。

> [!NOTE]
> AG99（作者 YakumoAki）额外提供 [Persona Effect](./guides/persona-effects) 和 [Prompt Extension](./guides/prompt-extensions)。前者扩展 Persona 的结构化表现输出，后者向统一 Prompt 管线贡献模型可见事实；两者都不是 LLM Tool。
> Interaction turn 中，普通插件的 LLM 钩子默认增强 Persona Expression，可执行工具默认进入 Core。插件可用 `interaction_runtime_target` 声明生命周期目标、用工具 `tool_targets` 声明工具目标；用户的 `plugin_runtime_targets` 与 `plugin_tool_targets` 配置分别覆盖它们。

## 先选择插件入口

AG99 不会把所有插件自动塞进 Persona。开发前先按插件行为选择入口：

- **需要命令、关键词、协议或独立业务系统接管消息**：使用官方 Pipeline Handler。Handler 可以直接返回或发送结果，也可以 `yield ProviderRequest` 委托 Core；它不经过 Router/Planner 的再次判断。
- **需要让 Persona/Core 看到当前状态或输入资料**：使用 [Prompt Extension](./guides/prompt-extensions)。通过 `meta.targets` 选择 `persona` 或 `core`；Router 和 Core Planner 不接收插件扩展。
- **需要模型执行插件能力**：注册 LLM Tool。工具默认进入 Core，只有工具声明或 `plugin_tool_targets` 配置明确允许时才进入 Persona。
- **需要后台报告设备、日历或世界状态**：使用 Runtime Sensor。Sensor 只提交结构化 Observation，后续是否表达由 Personal Runtime 的 Gate/Policy 决定。
- **插件已经决定内容并需要精确投递**：使用 `Context.send_message()` 或显式输出 API。它保持原有目标和内容，不会再经过“是否应该回复”的模型判断。

普通 Persona 对话中，插件的可见自然语言最终仍由 Persona Expression 统一发送；但独立系统插件、
命令插件和显式发送插件不需要伪装成 Persona 输入。详见 [Interaction Middleware](../../../Yakumo/modules/interaction.md#插件处理时序)。

## 环境准备

### 获取插件模板

1. 打开 AstrBot 插件模板: [helloworld](https://github.com/Soulter/helloworld)
2. 点击右上角的 `Use this template`
3. 然后点击 `Create new repository`。
4. 在 `Repository name` 处填写您的插件名。插件名格式:
   - 推荐以 `astrbot_plugin_` 开头；
   - 不能包含空格；
   - 保持全部字母小写；
   - 尽量简短。
5. 点击右下角的 `Create repository`。

### 克隆项目到本地

克隆 AstrBot 项目本体和刚刚创建的插件仓库到本地。

```bash
git clone https://github.com/AstrBotDevs/AstrBot
mkdir -p AstrBot/data/plugins
cd AstrBot/data/plugins
git clone 插件仓库地址
```

然后，使用 `VSCode` 打开 `AstrBot` 项目。找到 `data/plugins/<你的插件名字>` 目录。

更新 `metadata.yaml` 文件，填写插件的元数据信息。

> [!WARNING]
> 请务必修改此文件，AstrBot 识别插件元数据依赖于 `metadata.yaml` 文件。

### 设置插件 Logo（可选）

可以在插件目录下添加 `logo.png` 文件作为插件的 Logo。请保持长宽比为 1:1，推荐尺寸为 256x256。

![插件 logo 示例](https://files.astrbot.app/docs/source/images/plugin/plugin_logo.png)

### 插件展示名（可选）

可以修改(或添加) `metadata.yaml` 文件中的 `display_name` 字段，作为插件在插件市场等场景中的展示名，以方便用户阅读。

插件展示名和描述支持按 WebUI 语言显示，详见[插件国际化](./guides/plugin-i18n)。

### 插件短描述（可选）

你可以在 `metadata.yaml` 中新增 `short_desc` 字段，作为插件市场卡片上的短描述。它适合写成一句简短介绍；如果没有提供，卡片会回退显示 `desc`。

```yaml
short_desc: 一句话介绍你的插件。
```

### 声明支持平台（Optional）

你可以在 `metadata.yaml` 中新增 `support_platforms` 字段（`list[str]`），声明插件支持的平台适配器。WebUI 插件页会展示该字段。

```yaml
support_platforms:
  - telegram
  - discord
```

`support_platforms` 中的值需要使用 `ADAPTER_NAME_2_TYPE` 的 key，目前支持：

- `aiocqhttp`
- `qq_official`
- `telegram`
- `wecom`
- `lark`
- `dingtalk`
- `discord`
- `slack`
- `kook`
- `vocechat`
- `weixin_official_account`
- `satori`
- `misskey`
- `line`

### 声明 AstrBot 版本范围（Optional）

你可以在 `metadata.yaml` 中新增 `astrbot_version` 字段，声明插件要求的 AstrBot 版本范围。格式与 `pyproject.toml` 依赖版本约束一致（PEP 440），且不要加 `v` 前缀。

```yaml
astrbot_version: ">=4.16,<5"
```

可选示例：

- `>=4.17.0`
- `>=4.16,<5`
- `~=4.17`

如果你只想声明最低版本，可以直接写：

- `>=4.17.0`

当当前 AstrBot 版本不满足该范围时，插件会被阻止加载并提示版本不兼容。
在 WebUI 安装插件时，你可以选择“无视警告，继续安装”来跳过这个检查。

### 调试插件

AstrBot 采用在运行时注入插件的机制。因此，在调试插件时，需要启动 AstrBot 本体。

您可以使用 AstrBot 的热重载功能简化开发流程。

插件的代码修改后，可以在 AstrBot WebUI 的插件管理处找到自己的插件，点击右上角 `...` 按钮，选择 `重载插件`。

如果插件因为代码错误等原因加载失败，你也可以在管理面板的错误提示中点击 **“尝试一键重载修复”** 来重新加载。

### 插件依赖管理

目前 AstrBot 对插件的依赖管理使用 `pip` 自带的 `requirements.txt` 文件。如果你的插件需要依赖第三方库，请务必在插件目录下创建 `requirements.txt` 文件并写入所使用的依赖库，以防止用户在安装你的插件时出现依赖未找到(Module Not Found)的问题。

> `requirements.txt` 的完整格式可以参考 [pip 官方文档](https://pip.pypa.io/en/stable/reference/requirements-file-format/)。

## 开发原则

感谢您为 AstrBot 生态做出贡献，开发插件请遵守以下原则，这也是良好的编程习惯。

- 功能需经过测试。
- 需包含良好的注释。
- 持久化数据请存储于 `data` 目录下，而非插件自身目录，防止更新/重装插件时数据被覆盖。
- 良好的错误处理机制，不要让插件因一个错误而崩溃。
- 在进行提交前，请使用 [ruff](https://docs.astral.sh/ruff/) 工具格式化您的代码。
- 不要使用 `requests` 库来进行网络请求，可以使用 `aiohttp`, `httpx` 等异步网络请求库。
- 如果是对某个插件进行功能扩增，请优先给那个插件提交 PR 而不是单独再写一个插件（除非原插件作者已经停止维护）。
