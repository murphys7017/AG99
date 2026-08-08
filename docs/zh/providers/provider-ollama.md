# 接入 Ollama

🦙 Ollama 是一款免费、开源的应用程序，让您能在自己的电脑上运行大型语言模型（LLM）。（硬件需满足要求）

## 下载并安装 Ollama

您可以在 [https://ollama.com](https://ollama.com/download) 下载 Ollama。

## 选择想要使用的模型

在 https://ollama.com/search 上选择想要使用的模型。

在终端上 (Windows 上是 Powershell) 输入 `ollama pull <model_name>` 下载模型。

model_name 格式：`<model_name>:<model_version>`。如 `deepseek-r1:8b`。

> 8b 参数量模型需要至少 16GB 显存。有关配置和参数量的详细信息，请参阅其他文档。

拉取完成后，输入 `ollama list` 查看已经拉取的模型。

然后使用 `ollama run <model_name>` 运行模型。

## 配置 AstrBot

打开 AstrBot 控制台 -> 服务提供商页面，点击新增模型提供商，找到并点击 `Ollama`。
![image](https://files.astrbot.app/docs/source/images/ollama/image.png)

保存配置即可。

## 请求参数与上下文窗口

AstrBot 使用 Ollama 原生 `/api/chat` 请求。无需为了调整采样参数或上下文窗口额外创建一个
Ollama 模型；这些参数属于 AstrBot 中对应的服务提供商配置，并会随每次请求发送。

在该提供商的 `custom_extra_body` 中填写 Ollama 原生字段。例如：

```jsonc
{
  "max_context_tokens": 16384,
  "custom_extra_body": {
    "options": {
      "temperature": 0.4,
      "top_p": 0.85,
      "repeat_penalty": 1.05
    },
    "keep_alive": "2h"
  }
}
```

- `max_context_tokens` 是 AstrBot 的上下文上限；对 Ollama Chat，它会作为请求的
  `options.num_ctx` 发送。若同时在 `options` 填写不同的 `num_ctx`，以
  `max_context_tokens` 为准并记录告警。
- `custom_extra_body.options` 可填写 Ollama 的采样与生成选项。`temperature`、`top_p`、
  `stop` 和 `max_tokens` 也可直接写在 `custom_extra_body` 中；其中 `max_tokens` 会映射为
  Ollama 的 `num_predict`。
- `keep_alive` 等非保护字段会作为原生请求顶层字段发送。`model`、`messages`、`tools`、
  `tool_choice` 与 `stream` 由 AstrBot 管理，配置中不能覆盖。

Ollama Embedding 提供商同样支持 `custom_extra_body`。它请求的是 `/api/embed`，通常可用于
保持 embedding 模型热加载：

```jsonc
{
  "embedding_api_base": "http://127.0.0.1:11434",
  "embedding_model": "qwen3-embedding:0.6b",
  "custom_extra_body": {
    "keep_alive": "2h"
  }
}
```

Embedding 的自定义字段会直接写入 `/api/embed` 请求体，不放入 `options`。服务提供商配置更新后
重启 AstrBot 或在 WebUI 中保存并重载对应配置，才会作用于后续请求。

::: tip

对于 Mac/Windows 使用 Docker Desktop 部署 AstrBot 部署的用户，API Base URL 请填写为 `http://host.docker.internal:11434/v1`。\
对于 Linux 使用 Docker 部署 AstrBot 部署的用户，API Base URL 请填写为 `http://172.17.0.1:11434/v1`，或者将 `172.17.0.1` 替换为你的公网 IP（确保宿主机系统放行了 11434 端口）。\
如果 Ollama 使用了 Docker 部署，请确保 11434 端口已经映射到宿主机。

:::


## FAQ

报错：
```
AstrBot 请求失败。
错误类型: NotFoundError
错误信息: Error code: 404 - {'error': {'message': 'model "llama3.1-8b" not found, try pulling it first', 'type': 'api_error', 'param': None, 'code': None}}
```

请先看上面的教程，用 `ollama pull <model_name>` 拉取模型，然后使用 `ollama run <model_name>` 运行模型。
