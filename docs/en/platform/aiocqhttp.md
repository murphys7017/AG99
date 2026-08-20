# Connect OneBot v11 Protocol Implementations

OneBot is a standardized bot application interface designed to unify bot development across different chat platforms, so developers can write business logic once and use it on multiple platforms.

AstrBot supports all client implementations that implement OneBot v11 reverse WebSocket (AstrBot acts as the server).

Common OneBot v11 implementation projects are listed below:

- [NapCat](https://github.com/NapNeko/NapCatQQ)
- [OneDisc](https://github.com/ITCraftDevelopmentTeam/OneDisc)
- [Tele-KiraLink](https://github.com/Echomirix/Tele-KiraLink)

Please refer to each implementation project's deployment documentation.

## 1. Configure OneBot v11

1. Open AstrBot's WebUI
2. Click `Bots` in the left sidebar
3. In the right panel, click `+ Create Bot`
4. Select `OneBot v11`

Fill in the form:

- ID (`id`): any value, used only to distinguish instances of different platforms.
- Enable (`enable`): check it.
- Reverse WebSocket host: fill your machine IP, usually `0.0.0.0`.
- Reverse WebSocket port: choose any port, default is `6199`.
- Reverse WebSocket token: fill this only when NapCat network configuration has a token set.

Click `Save`.

## 2. Configure the protocol implementation side

Please refer to each protocol implementation project's deployment documentation.

Notes:

1. The implementation must support `Reverse WebSocket`, with AstrBot acting as the server and the implementation client as the client.
2. The reverse WebSocket URL is `ws(s)://<your-host>:6199/ws`.

## 3. Verify

Go to AstrBot WebUI `Console`. If a blue log appears saying `aiocqhttp(OneBot v11) adapter connected.`, the connection is successful.
If after a few seconds you see `aiocqhttp adapter has been closed`, it means the connection timed out (failed). Please double-check your configuration.

## 4. Group member names and cache

To avoid synchronous OneBot requests while processing `@` mentions, AstrBot loads the available group list after the adapter connects and refreshes group member name snapshots in the background. Snapshots are kept for 24 hours, and refreshes are concurrency-limited so they do not block message ingestion or reply generation.

When a snapshot is not ready or OneBot temporarily cannot provide the member list, `@` handling falls back to the nickname carried by the message segment, or to the QQ number when no nickname is available. Group member change notices trigger a background refresh for the affected group. When ID whitelisting is enabled, only permitted groups are prewarmed.
