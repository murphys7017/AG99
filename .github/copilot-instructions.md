# AG99 Development Instructions

AG99 is the public project name for this repository. It was created by YakumoAki and is a persona-first, continuously running multi-platform conversation runtime based on AstrBot. It keeps AstrBot-compatible platform adapters, providers, plugins, and CLI naming while adding Personal Runtime, Interaction Middleware, Router, Core Planner, structured Prompt, and unified Persona Expression.

`Yakumo` refers to the author identity (`YakumoAki`) and the retained `docs/Yakumo` architecture namespace, not the public project name.

The `astrbot` package, CLI, and `astrbot_plugin_` prefix are intentionally stable compatibility surfaces. Do not rename them during documentation or code changes unless the task explicitly requests a migration.

Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

## Working Effectively

### Bootstrap and Install Dependencies
- **Python 3.12+ required** - Check `.python-version` file
- Install UV package manager: `pip install uv`
- Install project dependencies: `uv sync` -- takes 6-7 minutes. NEVER CANCEL. Set timeout to 10+ minutes.
- Create required directories: `mkdir -p data/plugins data/config data/temp`

### Running the Application
- Run main application: `uv run main.py` -- starts in ~3 seconds
- Application creates WebUI on http://localhost:6185 (default credentials: `astrbot`/`astrbot`)

### Dashboard Build (Vue.js/Node.js)
- **Prerequisites**: Node.js 20+ and npm 10+ required
- Navigate to dashboard: `cd dashboard`
- Install dashboard dependencies: `npm install` -- takes 2-3 minutes. NEVER CANCEL. Set timeout to 5+ minutes.
- Build dashboard: `npm run build` -- takes 25-30 seconds. NEVER CANCEL.
- Dashboard creates optimized production build in `dashboard/dist/`

### Testing
- Do not generate test files for now.

### Code Quality and Linting
- Install ruff linter: `uv add --dev ruff`
- Check code style: `uv run ruff check .` -- takes <1 second
- Check formatting: `uv run ruff format --check .` -- takes <1 second
- Fix formatting: `uv run ruff format .`
- **ALWAYS** run `uv run ruff check .` and `uv run ruff format .` before committing changes

### Plugin Development
- Plugins load from `astrbot/builtin_stars/` (built-in) and `data/plugins/` (user-installed)
- Plugin system supports function tools and message handlers
- Key plugins: python_interpreter, web_searcher, astrbot, reminder, session_controller

### Common Issues and Workarounds
- **Dashboard download fails**: Known issue with "division by zero" error - application still works
- **Import errors in tests**: Ensure `uv run` is used to run tests in proper environment
=- **Build timeouts**: Always set appropriate timeouts (10+ minutes for uv sync, 5+ minutes for npm install)

## CI/CD Integration
- GitHub Actions workflows in `.github/workflows/`
- Docker builds supported via `Dockerfile`
- Pre-commit hooks enforce ruff formatting and linting

## Docker Support
- Primary deployment method: `docker run soulter/astrbot:latest`
- Compose file available: `compose.yml`
- Exposes ports: 6185 (WebUI), 6195 (WeChat), 6199 (QQ), etc.
- Volume mount required: `./data:/AstrBot/data`

## Multi-language Support
- Documentation in Chinese (`README_zh.md`), English (`README.md`), and inherited compatibility guides under `docs/zh` and `docs/en`
- UI supports internationalization
- Default language is Chinese

Remember: AG99 is an active runtime with real users and an evolving architecture. Read `docs/Yakumo/project-identity.md` and `docs/Yakumo/current-state.md` before changing public behavior, and always test thoroughly so AstrBot compatibility surfaces remain intact.
