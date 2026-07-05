from quart import g, request

from astrbot.core.db import BaseDatabase
from astrbot.dashboard.services.chatui_project_service import (
    ChatUIProjectService,
    ChatUIProjectServiceError,
)

from .route import Response, Route, RouteContext


class ChatUIProjectRoute(Route):
    def __init__(self, context: RouteContext, db: BaseDatabase) -> None:
        super().__init__(context)
        self.routes = {
            "/chatui_project/create": ("POST", self.create_project),
            "/chatui_project/list": ("GET", self.list_projects),
            "/chatui_project/get": ("GET", self.get_project),
            "/chatui_project/update": ("POST", self.update_chatui_project),
            "/chatui_project/delete": ("GET", self.delete_project),
            "/chatui_project/add_session": ("POST", self.add_session_to_project),
            "/chatui_project/remove_session": (
                "POST",
                self.remove_session_from_project,
            ),
            "/chatui_project/get_sessions": ("GET", self.get_project_sessions),
        }
        self.db = db
        self.service = ChatUIProjectService(db)
        self.register_routes()

    @staticmethod
    def _handle_error(exc: ChatUIProjectServiceError):
        return Response().error(str(exc)).__dict__

    async def create_project(self):
        """Create a new ChatUI project."""
        username = g.get("username", "guest")
        post_data = await request.json or {}
        try:
            return Response().ok(data=await self.service.create_project(username, post_data)).__dict__
        except ChatUIProjectServiceError as exc:
            return self._handle_error(exc)

    async def list_projects(self):
        """Get all ChatUI projects for the current user."""
        username = g.get("username", "guest")

        return Response().ok(data=await self.service.list_projects(username)).__dict__

    async def get_project(self):
        """Get a specific ChatUI project."""
        project_id = request.args.get("project_id")
        username = g.get("username", "guest")
        try:
            return Response().ok(data=await self.service.get_project(username, project_id)).__dict__
        except ChatUIProjectServiceError as exc:
            return self._handle_error(exc)

    async def update_chatui_project(self):
        """Update a ChatUI project."""
        post_data = await request.json or {}
        username = g.get("username", "guest")
        try:
            await self.service.update_project(username, post_data)
        except ChatUIProjectServiceError as exc:
            return self._handle_error(exc)

        return Response().ok().__dict__

    async def delete_project(self):
        """Delete a ChatUI project."""
        project_id = request.args.get("project_id")
        username = g.get("username", "guest")
        try:
            await self.service.delete_project(username, project_id)
        except ChatUIProjectServiceError as exc:
            return self._handle_error(exc)

        return Response().ok().__dict__

    async def add_session_to_project(self):
        """Add a session to a project."""
        post_data = await request.json or {}
        username = g.get("username", "guest")
        try:
            await self.service.add_session_to_project(username, post_data)
        except ChatUIProjectServiceError as exc:
            return self._handle_error(exc)

        return Response().ok().__dict__

    async def remove_session_from_project(self):
        """Remove a session from its project."""
        post_data = await request.json or {}
        username = g.get("username", "guest")
        try:
            await self.service.remove_session_from_project(username, post_data)
        except ChatUIProjectServiceError as exc:
            return self._handle_error(exc)

        return Response().ok().__dict__

    async def get_project_sessions(self):
        """Get all sessions in a project."""
        project_id = request.args.get("project_id")
        username = g.get("username", "guest")
        try:
            sessions = await self.service.get_project_sessions(username, project_id)
        except ChatUIProjectServiceError as exc:
            return self._handle_error(exc)
        return Response().ok(data=sessions).__dict__
