
# Sending Messages

## Passive Messages

Passive messages refer to the bot responding to messages reactively.

```python
@filter.command("helloworld")
async def helloworld(self, event: AstrMessageEvent):
    yield event.plain_result("Hello!")
    yield event.plain_result("你好！")

    yield event.image_result("path/to/image.jpg") # Send an image
    yield event.image_result("https://example.com/image.jpg") # Send an image from URL, must start with http or https
```

### Persona Tool Output

Outside a Persona Expression tool invocation, `event.send()`, `emit_output()`,
`emit_progress()`, and streaming output keep their normal delivery behavior. When
a plugin-owned LLM Tool runs inside Persona Expression, those legacy output paths
are captured as model-visible tool material instead of being delivered immediately.
The final Persona Expression remains the only owner of the user-visible reply.

This applies to text strings, `MessageChain` values, `MessageEventResult` values,
and `Context.send_message()` when it targets the current session. Rich components
such as images, files, and records are delivered with the final Persona reply rather
than reduced to component names. Explicit cross-session `Context.send_message()`
calls retain their original delivery target. Output produced by a background task created by the tool is outside that tool
invocation and is not captured. Use normal explicit output APIs for work that must
continue after the tool returns.

Legacy tools returning `MessageEventResult.set_async_stream(...)` are not supported
inside Persona Expression yet. AstrBot safely closes that returned stream and gives
the tool loop a deterministic compatibility notice instead of silently dropping it.
Return a non-streaming result or explicitly configure that plugin for `core` in
`plugin_tool_targets`.

## Active Messages

Active messages refer to the bot proactively pushing messages. Some platforms may not support active message sending.

For scheduled tasks or when you don't want to send messages immediately, you can use `event.unified_msg_origin` to get a string and store it, then use `self.context.send_message(unified_msg_origin, chains)` to send messages when needed.

```python
from astrbot.api.event import MessageChain

@filter.command("helloworld")
async def helloworld(self, event: AstrMessageEvent):
    umo = event.unified_msg_origin
    message_chain = MessageChain().message("Hello!").file_image("path/to/image.jpg")
    await self.context.send_message(event.unified_msg_origin, message_chain)
```

When it is called inside a Persona Expression tool for the current session,
`Context.send_message()` is captured as tool output and delivered with the final
Persona reply; it cannot take ownership of the turn's final output. Explicit
cross-session sends retain their original delivery target.

`send_message()` treats the message as the final output of the proactive turn by
default. When it is called inside the current active turn for tool or task
progress, pass `finalize=False`. The message still goes through Personal Runtime
and the unified output controller without completing the turn early:

```python
await self.context.send_message(
    event.unified_msg_origin,
    message_chain,
    finalize=False,
)
```

`send_message()` is an explicit compatibility API for content that the caller has
already decided to deliver. It is not reconsidered by Personal Policy and is not
rewritten or suppressed by autonomous-expression deduplication. Cron and plugin
calls retain their exact-content behavior and the platform's
`support_proactive_message` capability semantics.

With this feature, you can store the `unified_msg_origin` and send messages when needed.

For proactive plugin output without an explicit session, pass `None` to use the
Default Proactive Message Target from the basic settings. The call returns
`False` when no default is configured or its adapter is unavailable:

```python
sent = await self.context.send_message(None, message_chain)
```

> [!TIP]
> About unified_msg_origin.
> `unified_msg_origin` is a string that records the unique ID of a session. AstrBot uses it to identify which messaging platform and which session it belongs to. This allows messages to be sent to the correct session when using `send_message`. For more about MessageChain, see the next section.

## Rich Media Messages

AstrBot supports sending rich media messages such as images, audio, videos, etc. Use `MessageChain` to construct messages.

```python
import astrbot.api.message_components as Comp

@filter.command("helloworld")
async def helloworld(self, event: AstrMessageEvent):
    chain = [
        Comp.At(qq=event.get_sender_id()), # Mention the message sender
        Comp.Plain("Check out this image:"),
        Comp.Image.fromURL("https://example.com/image.jpg"), # Send image from URL
        Comp.Image.fromFileSystem("path/to/image.jpg"), # Send image from local file system
        Comp.Plain("This is an image.")
    ]
    yield event.chain_result(chain)
```

The above constructs a `message chain`, which will ultimately send a message containing both images and text while preserving the order.

> [!TIP]
> In the aiocqhttp message adapter, for messages of type `plain`, the `strip()` method is used during sending to remove spaces and line breaks. You can add zero-width spaces `\u200b` before and after the message to resolve this issue.

Similarly,

**File**

```py
Comp.File(file="path/to/file.txt", name="file.txt") # Not supported by some platforms
```

**Audio Record**

```py
path = "path/to/record.wav" # Currently only accepts wav format, please convert other formats yourself
Comp.Record(file=path, url=path)
```

**Video**

```py
path = "path/to/video.mp4"
Comp.Video.fromFileSystem(path=path)
Comp.Video.fromURL(url="https://example.com/video.mp4")
```

## Sending Video Messages

```python
from astrbot.api.event import filter, AstrMessageEvent

@filter.command("test")
async def test(self, event: AstrMessageEvent):
    from astrbot.api.message_components import Video
    # fromFileSystem requires the user's protocol client and bot to be on the same system.
    music = Video.fromFileSystem(
        path="test.mp4"
    )
    # More universal approach
    music = Video.fromURL(
        url="https://example.com/video.mp4"
    )
    yield event.chain_result([music])
```

![Sending video messages](https://files.astrbot.app/docs/source/images/plugin/db93a2bb-671c-4332-b8ba-9a91c35623c2.png)

## Sending Group Forward Messages

> Most platforms do not support this message type. Current support: OneBot v11

You can send group forward messages as follows.

```py
from astrbot.api.event import filter, AstrMessageEvent

@filter.command("test")
async def test(self, event: AstrMessageEvent):
    from astrbot.api.message_components import Node, Plain, Image
    node = Node(
        uin=905617992,
        name="Soulter",
        content=[
            Plain("hi"),
            Image.fromFileSystem("test.jpg")
        ]
    )
    yield event.chain_result([node])
```

![Sending group forward messages](https://files.astrbot.app/docs/source/images/plugin/image-4.png)
