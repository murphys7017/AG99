---
outline: deep
---

# 函数调用（Function-calling）

## 简介

函数调用旨在提供大模型**调用外部工具的能力**，以此实现 Agentic 的一些功能。

比如，问大模型：帮我搜索一下关于“猫”的信息，大模型会调用用于搜索的外部工具，比如搜索引擎，然后返回搜索结果。

目前，支持的模型包括但远不限于

- GPT-5.x 系列
- Gemini 3.x 系列
- Claude 4.x 系列
- Deepseek v3.2(deepseek-chat)
- Qwen 3.x 系列

2025年后推出的主流模型通常已支持函数调用。

较老的模型或接口可能不支持函数调用，是否支持应以对应服务提供商的当前文档为准，不能仅根据模型是否启用思考模式判断。

DeepSeek 当前接口在思考模式和非思考模式下均支持工具调用。AstrBot 会尊重 Provider 的 `thinking.type` 配置：`disabled` 使用非思考模式，`enabled` 或未配置时使用思考模式；两种模式都保留调用方提供的 `tool_choice`，不会自动切换思考模式或静默删除工具选择约束。

在 AstrBot 中，默认提供了网页搜索、待办提醒、代码执行器这些工具。很多插件，如:

- astrbot_plugin_cloudmusic
- astrbot_plugin_bilibili
- ...

等在提供传统的指令调用的基础上，也提供了函数调用的功能。

相关操作可以在 WebUI 的工具管理页面中完成，包括查看工具列表、开启或关闭工具，以及调整插件和 MCP 工具的使用权限。

某些模型可能不支持函数调用，会返回诸如 `tool call is not supported`, `function calling is not supported`, `tool use is not supported` 等错误。在大多数情况下，AstrBot 能够检测到这种错误并自动帮您去除函数调用工具。如果你发现某个模型不支持函数调用，也可以在 WebUI 中关闭所有函数调用工具，然后再次尝试。或者更换为支持函数调用的模型。


下面是一些常见的工具调用 Demo：

![image](https://files.astrbot.app/docs/source/images/function-calling/image.png)

![image](https://files.astrbot.app/docs/source/images/function-calling/image-1.png)


## MCP

请前往此文档 [AstrBot - MCP](/use/mcp) 查看。
