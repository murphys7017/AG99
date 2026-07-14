---
outline: deep
---

# Function Calling

## Introduction

Function calling aims to provide large language models with **the ability to invoke external tools**, enabling various Agentic functionalities.

For example, when you ask the LLM: "Help me search for information about cats", the model will call external search tools, such as search engines, and return the search results.

Here is the revised text, updated to reflect your new content while maintaining a formal documentation tone:

Currently, supported models include but are not limited to:

- GPT-5.x series
- Gemini 3.x series
- Claude 4.x series
- DeepSeek v3.2 (deepseek-chat)
- Qwen 3.x series

Mainstream models released after 2025 typically support function calling.

Some older models or endpoints do not support function calling. Check the provider's current documentation instead of inferring support solely from whether reasoning mode is enabled.

The current DeepSeek API supports tool calls in both thinking and non-thinking modes. AstrBot respects the Provider's `thinking.type` setting: `disabled` uses non-thinking mode, while `enabled` or an omitted setting uses thinking mode. Both paths preserve the caller's `tool_choice`; AstrBot does not switch thinking modes or silently remove the tool-choice constraint.

In AstrBot, web search, todo reminders, and code interpreter tools are provided by default. Many plugins, such as:

- astrbot_plugin_cloudmusic
- astrbot_plugin_bilibili
- ...

In addition to providing traditional command invocation, also offer function calling capabilities.

Tool management can be done in the WebUI, including viewing tool lists, enabling or disabling tools, and adjusting permissions for plugin and MCP tools.

Some models may not support function calling and will return errors such as `tool call is not supported`, `function calling is not supported`, `tool use is not supported`, etc. In most cases, AstrBot can detect these errors and automatically remove function calling tools for you. If you find that a model doesn't support function calling, disable all function calling tools in the WebUI, then try again. You can also switch to a model that supports function calling.


Below are some common tool calling demos:

![image](https://files.astrbot.app/docs/source/images/function-calling/image.png)

![image](https://files.astrbot.app/docs/source/images/function-calling/image-1.png)


## MCP

Please refer to this documentation: [AstrBot - MCP](/en/use/mcp).
