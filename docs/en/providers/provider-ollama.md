# Integrating Ollama

🦙 Ollama is a free, open-source tool that lets you run large language models (LLMs) on your own computer. (hardware must meet requirements)

## Download and Install Ollama

You can download Ollama from [https://ollama.com](https://ollama.com/download).

## Select and Pull a Model

Choose the model you want to use at [https://ollama.com/search](https://ollama.com/search).

In the terminal (PowerShell on Windows), enter `ollama pull <model_name>` to download the model.

model_name format: `<model_name>:<model_version>`. For example, `deepseek-r1:8b`.
> The 8b parameter model requires at least 16GB of video memory (VRAM). Refer to other documentation for detailed information on configurations and parameter sizes.

After pulling is complete, use `ollama list` to view the models you have pulled.

Then use `ollama run <model_name>` to run the model.

## Configure AstrBot

Open the AstrBot WebUI, locate Service Provider Management, click on Add Provider, find and click on `Ollama`.
![image](https://files.astrbot.app/docs/source/images/ollama/image.png)

Save the configuration.

## Request Parameters and Context Window

AstrBot uses Ollama's native `/api/chat` endpoint. You do not need a second Ollama
model just to change sampling parameters or the context window: configure them on
the corresponding AstrBot provider and they are sent with every request.

Set native fields in the provider's `custom_extra_body`, for example:

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

- `max_context_tokens` is AstrBot's context limit. For Ollama Chat it is sent as
  `options.num_ctx`. If `options.num_ctx` conflicts with it, AstrBot uses
  `max_context_tokens` and logs a warning.
- `custom_extra_body.options` accepts native Ollama sampling and generation options.
  `temperature`, `top_p`, `stop`, and `max_tokens` may also be placed directly in
  `custom_extra_body`; `max_tokens` is mapped to Ollama's `num_predict`.
- Non-protected values such as `keep_alive` are sent as top-level native request
  fields. AstrBot owns `model`, `messages`, `tools`, `tool_choice`, and `stream`, so
  they cannot be overridden through this configuration.

The Ollama Embedding provider also supports `custom_extra_body`. It calls
`/api/embed`, where this is useful for keeping an embedding model warm:

```jsonc
{
  "embedding_api_base": "http://127.0.0.1:11434",
  "embedding_model": "qwen3-embedding:0.6b",
  "custom_extra_body": {
    "keep_alive": "2h"
  }
}
```

Embedding custom fields are written directly into the `/api/embed` request body,
not under `options`. Restart AstrBot, or save and reload the relevant WebUI
configuration, before expecting updated provider settings to affect new requests.

::: tip

For Mac/Windows users deploying AstrBot with Docker Desktop, enter `http://host.docker.internal:11434/v1` for the API Base URL.\
For Linux users deploying AstrBot with Docker, enter `http://172.17.0.1:11434/v1` for the API Base URL, or replace `172.17.0.1` with your public IP address (ensure that port 11434 is allowed by the host system).\
If Ollama is deployed using Docker, ensure that port 11434 is mapped to the host.

:::

## FAQ

Error:
```
AstrBot request failed.
Error type: NotFoundError
Error message: Error code: 404 - {'error': {'message': 'model "llama3.1-8b" not found, try pulling it first', 'type': 'api_error', 'param': None, 'code': None}}

```
Please refer to the instructions above and use `ollama pull <model_name>` to pull the model, then use `ollama run <model_name>` to run the model.
