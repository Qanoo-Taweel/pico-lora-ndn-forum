"""User interactions: everything a user can DO in the forum, one class each.

The web server receives e.g.  POST /api/create_topic  {"title": ...}  and asks
the UserInteractionFactory for the matching interaction object, then runs it.

Design patterns:
  * Factory Method: UserInteractionFactory.create(action) returns the right
    UserInteraction subclass. The web layer never names the concrete classes.
  * Command: each interaction wraps one request (its payload) and has a single
    execute() method; the web layer runs all of them the same way.
  * Template Method: UserInteraction.run() always does
    "check login -> check required fields -> execute()".
  * Open/Closed: adding a new user action = one new class + one register().
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Callable

from .auth import AuthService
from .models import User
from .service import ForumService


class InteractionError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class InteractionContext:
    """What an interaction is allowed to use (passed in = Dependency Injection)."""

    def __init__(self, forum: ForumService, auth: AuthService, user: User | None,
                 sync_now: Callable[[], None] = lambda: None):
        self.forum, self.auth, self.user, self.sync_now = forum, auth, user, sync_now

    @property
    def author(self) -> str:
        return self.auth.public_name(self.user)


class UserInteraction(ABC):
    requires_login = True
    required_fields: tuple[str, ...] = ()

    def __init__(self, payload: dict):
        self.payload = payload or {}

    def run(self, ctx: InteractionContext) -> dict:
        """Template method."""
        if self.requires_login and ctx.user is None:
            raise InteractionError("Please sign in first.", 401)
        missing = [f for f in self.required_fields if not str(self.payload.get(f, "")).strip()]
        if missing:
            raise InteractionError("Missing: " + ", ".join(missing))
        return self.execute(ctx)

    @abstractmethod
    def execute(self, ctx: InteractionContext) -> dict: ...


class RegisterInteraction(UserInteraction):
    requires_login = False
    required_fields = ("nickname", "password")

    def execute(self, ctx):
        token, user = ctx.auth.register(self.payload["nickname"].strip(), self.payload["password"])
        return {"token": token, "name": ctx.auth.public_name(user)}


class LoginInteraction(UserInteraction):
    requires_login = False
    required_fields = ("nickname", "password")

    def execute(self, ctx):
        token, user = ctx.auth.login(self.payload["nickname"].strip(), self.payload["password"])
        return {"token": token, "name": ctx.auth.public_name(user)}


class CreateTopicInteraction(UserInteraction):
    required_fields = ("title",)

    def execute(self, ctx):
        p = self.payload
        topic = ctx.forum.create_topic(ctx.author, p["title"], p.get("body", ""), p.get("category", "general"))
        ctx.sync_now()
        return asdict(topic)


class AddCommentInteraction(UserInteraction):
    required_fields = ("topic_id", "body")

    def execute(self, ctx):
        p = self.payload
        comment = ctx.forum.add_comment(ctx.author, p["topic_id"], p["body"], p.get("reply_to"))
        ctx.sync_now()
        return asdict(comment)


class CastVoteInteraction(UserInteraction):
    required_fields = ("target_id", "value")

    def execute(self, ctx):
        try:
            value = int(self.payload["value"])
        except (TypeError, ValueError):
            raise InteractionError("value must be 1, -1 or 0")
        return asdict(ctx.forum.vote(ctx.author, self.payload["target_id"], value))


class SyncNowInteraction(UserInteraction):
    requires_login = False

    def execute(self, ctx):
        ctx.sync_now()
        return {"ok": True}


class UserInteractionFactory:
    """Factory Method: action name -> UserInteraction object."""

    def __init__(self):
        self._registry: dict[str, type[UserInteraction]] = {}

    def register(self, action: str, cls: type[UserInteraction]) -> None:
        self._registry[action] = cls

    def create(self, action: str, payload: dict) -> UserInteraction:
        cls = self._registry.get(action)
        if cls is None:
            raise InteractionError("Unknown action: %s" % action, 404)
        return cls(payload)

    def actions(self) -> list[str]:
        return sorted(self._registry)


def default_interaction_factory() -> UserInteractionFactory:
    f = UserInteractionFactory()
    f.register("register", RegisterInteraction)
    f.register("login", LoginInteraction)
    f.register("create_topic", CreateTopicInteraction)
    f.register("add_comment", AddCommentInteraction)
    f.register("vote", CastVoteInteraction)
    f.register("sync_now", SyncNowInteraction)
    return f
