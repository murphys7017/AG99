import copy
import traceback

from quart import jsonify, request

from astrbot.core import logger
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle

from .route import Response, Route, RouteContext


class SubAgentRoute(Route):
    def __init__(
        self,
        context: RouteContext,
        core_lifecycle: AstrBotCoreLifecycle,
    ) -> None:
        super().__init__(context)
        self.core_lifecycle = core_lifecycle
        # NOTE: dict cannot hold duplicate keys; use list form to register multiple
        # methods for the same path.
        self.routes = [
            ("/subagent/config", ("GET", self.get_config)),
            ("/subagent/config", ("POST", self.update_config)),
            ("/subagent/available-tools", ("GET", self.get_available_tools)),
        ]
        self.register_routes()

    def _resolve_profile_config(self, config_id: object):
        """Return the explicitly selected Profile configuration.

        Subagent definitions are BotProfile behavior, not global runtime
        resources. Keep the Dashboard API on the same Profile boundary as the
        runtime handoff registry instead of silently targeting ``default``.
        """
        if config_id is None:
            normalized_config_id = "default"
        elif isinstance(config_id, str) and config_id.strip():
            normalized_config_id = config_id.strip()
        else:
            raise ValueError("配置文件 ID 无效")

        config = self.core_lifecycle.astrbot_config_mgr.confs.get(
            normalized_config_id
        )
        if config is None:
            raise ValueError(f"配置文件 {normalized_config_id} 不存在")
        return normalized_config_id, config

    @staticmethod
    def _normalize_subagent_config(raw_config: object) -> dict:
        """Return a response-safe subagent config without mutating its Profile."""
        data = copy.deepcopy(raw_config) if isinstance(raw_config, dict) else {}
        if not data:
            data = {
                "main_enable": False,
                "remove_main_duplicate_tools": False,
                "agents": [],
            }

        # Backward compatibility: older config used `enable`.
        if "main_enable" not in data and "enable" in data:
            data["main_enable"] = bool(data.get("enable", False))

        data.setdefault("main_enable", False)
        data.setdefault("remove_main_duplicate_tools", False)
        data.setdefault("agents", [])

        # None means follow the Profile's configured chat provider.
        if isinstance(data.get("agents"), list):
            for agent in data["agents"]:
                if isinstance(agent, dict):
                    agent.setdefault("provider_id", None)
                    agent.setdefault("persona_id", None)
        return data

    async def get_config(self):
        try:
            config_id, cfg = self._resolve_profile_config(request.args.get("conf_id"))
            data = self._normalize_subagent_config(cfg.get("subagent_orchestrator"))
            return jsonify(Response().ok(data={"conf_id": config_id, **data}).__dict__)
        except Exception as e:
            logger.error(traceback.format_exc())
            return jsonify(Response().error(f"获取 subagent 配置失败: {e!s}").__dict__)

    async def update_config(self):
        try:
            data = await request.json
            if not isinstance(data, dict):
                return jsonify(Response().error("配置必须为 JSON 对象").__dict__)

            config_id, cfg = self._resolve_profile_config(data.get("conf_id"))
            subagent_config = {
                key: copy.deepcopy(value)
                for key, value in data.items()
                if key != "conf_id"
            }
            cfg["subagent_orchestrator"] = subagent_config

            # Persist through the selected Profile's AstrBotConfig instance.
            cfg.save_config()

            await self.core_lifecycle.reload_subagent_orchestrator_profile(config_id)

            return jsonify(Response().ok(message="保存成功").__dict__)
        except Exception as e:
            logger.error(traceback.format_exc())
            return jsonify(Response().error(f"保存 subagent 配置失败: {e!s}").__dict__)

    async def get_available_tools(self):
        """Return all registered tools (name/description/parameters/active/origin).

        UI can use this to build a multi-select list for subagent tool assignment.
        """
        try:
            tool_mgr = self.core_lifecycle.provider_manager.llm_tools
            tools_dict = []
            for tool in tool_mgr.func_list:
                # Prevent recursive routing: subagents should not be able to select
                # the handoff (transfer_to_*) tools as their own mounted tools.
                if isinstance(tool, HandoffTool):
                    continue
                if tool.handler_module_path == "core.subagent_orchestrator":
                    continue
                tools_dict.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        "active": tool.active,
                        "handler_module_path": tool.handler_module_path,
                    }
                )
            return jsonify(Response().ok(data=tools_dict).__dict__)
        except Exception as e:
            logger.error(traceback.format_exc())
            return jsonify(Response().error(f"获取可用工具失败: {e!s}").__dict__)
