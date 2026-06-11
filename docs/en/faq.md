# FAQ

## Dashboard Related

### Encountering 404 Error When Opening the Dashboard

Download `AstrBot-vxxxxx-dashboard.zip` from the [release](https://github.com/AstrBotDevs/AstrBot/releases) page, extract it, and move it to `AstrBot/data`. If it still doesn't work, try restarting your computer (based on community feedback).

### First Login Account and Random Password

On first startup, the WebUI username is usually `astrbot`, and AstrBot generates a random initial password in the startup logs. Log in with the `Initial username` and `Initial password` shown in the logs, then complete the account setup flow.

For automated deployments, set `ASTRBOT_DASHBOARD_INITIAL_PASSWORD` before the first config file is generated. The password must be at least 8 characters and include uppercase letters, lowercase letters, and digits.

### Forgot Dashboard Password

If you forgot your AstrBot dashboard password, first try running this from the AstrBot root directory:

```bash
astrbot conf set dashboard.password new-password
```

If the CLI is unavailable, stop AstrBot and edit `AstrBot/data/cmd_config.json`. In the `"dashboard"` object, delete these keys:

- `username`
- `password`
- `pbkdf2_password`
- `password_storage_upgraded`
- `password_change_required`
- `jwt_secret`

Save the file and restart AstrBot. AstrBot will regenerate the default username and a random initial password; check the startup logs. The legacy MD5 password field is kept only for migration compatibility and should not be generated or edited manually.

### Correct Password Cannot Log In After Upgrading AstrBot

If you are sure the dashboard password is correct but still cannot log in after upgrading AstrBot, the old WebUI static files may be incompatible with the newer backend.

Solution:

1. Stop AstrBot.
2. Delete the `dist` folder under AstrBot's `data` directory: `AstrBot/data/dist`.
3. Restart AstrBot.
4. Open the dashboard in your browser, then press `Ctrl+Shift+R` or `Ctrl+F5` (or `Cmd+Shift+R` on macOS) to force refresh the page.

After restart, AstrBot will reload or download WebUI files that match the current version.

## Bot Core Related

### How to Let AstrBot Control My Mac / Windows / Linux Computer?

1. In AstrBot WebUI's `Config -> General Config`, find `Use Computer Capabilities`, and select `local` for the runtime environment.
2. In `Config -> Other Config`, find `Admin ID List`, and add your user ID (you can get it through the `/sid` command).

> [!TIP]
> For security reasons, when runtime environment is set to `local`, AstrBot only allows AstrBot administrators to use computer capabilities by default.
> You can select `sandbox` for the runtime environment, which allows all users to use computer capabilities (in an isolated sandbox). For more details, see [AstrBot Sandbox Environment](/en/use/astrbot-agent-sandbox.md)

### Bot Cannot Chat in Group Conversations

1. In group chats, to prevent message flooding, the bot will not respond to every monitored message. Please try mentioning (@) the bot or using a wake word to chat, such as the default `/`, for example: `/hello`.

### No Permission to Execute Admin Commands

1. `/reset, /persona, /dashboard_update, /op, /deop, /wl, /dewl` are the default admin commands. You can use the `/sid` command to get a user's ID, then add it to the admin ID list in Settings -> Other Settings.

### Chinese Characters Garbled When Locally Rendering Markdown Images (t2i)

You can customize the font. See details -> [#957](https://github.com/AstrBotDevs/AstrBot/issues/957#issuecomment-2749981802)

Recommended font: [Maple Mono](https://github.com/subframe7536/maple-font).

### Cannot Parse API Returned Completion & LLM Returns `<empty content>`

This is because the provider's API returned empty text. Try the following steps:

1. Check if the API key is still valid
2. Check if the API call limit or quota has been reached
3. Check network connection
4. Try reset
5. Lower the maximum conversation count setting
6. Switch to another model from the same provider / a different provider

## Plugin Related

### Cannot Install Plugin

1. Plugins are installed via GitHub. Access to GitHub from mainland China can indeed be unstable. You can use a proxy, then go to Other Settings -> HTTP Proxy to configure it. Alternatively, download the plugin archive directly and upload it.

### Error `No module named 'xxx'` After Installing Plugin

![image](https://files.astrbot.app/docs/source/images/faq/image.png)

This is because the plugin's dependencies were not installed properly. Normally, AstrBot automatically installs plugin dependencies after installing the plugin, but installation may fail in the following situations:

1. Network issues preventing dependency downloads
2. Plugin author did not include a `requirements.txt` file
3. Python version incompatibility

Solution:

Based on the error message, refer to the plugin's README to manually install dependencies. You can install dependencies in the AstrBot WebUI under `Console` -> `Install Pip Package`.

![image](https://files.astrbot.app/docs/source/images/faq/image-1.png)

If you find that the plugin author did not include a `requirements.txt` file, please submit an issue in the plugin repository to remind the author to add it.
