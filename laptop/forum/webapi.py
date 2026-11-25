"""The HTTP side: serves the web page and a small JSON API.

    GET  /                      the web page
    GET  /api/meta              node name, categories, sort options
    GET  /api/me                who is signed in
    GET  /api/topics?sort=&q=&category=
    GET  /api/topic?id=...      one topic with its comments
    GET  /api/network           radio + peer status, live packet log, activity feed
    POST /api/<action>          any action known to the UserInteractionFactory

This layer only translates HTTP <-> Python. All rules live in ForumService,
and every POST goes through the UserInteractionFactory (Factory + Command).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from .auth import AuthError
from .interactions import InteractionContext, InteractionError
from .models import CATEGORIES
from .service import ForumError

if TYPE_CHECKING:
    from app import LoRaForumApp

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class ForumWebApi:
    def __init__(self, app: "LoRaForumApp"):
        self.app = app
        self._get_routes = {
            "/api/meta": self._meta,
            "/api/me": self._me,
            "/api/topics": self._topics,
            "/api/topic": self._topic,
            "/api/network": self._network,
        }

    # ---------------------------------------------------------------- GET
    def handle_get(self, path: str, query: dict, token: str | None) -> tuple[int, object]:
        route = self._get_routes.get(path)
        if route is None:
            return 404, {"error": "Not found"}
        user = self.app.auth.user_from_token(token)
        return route(query, user)

    def _viewer(self, user):
        return self.app.auth.public_name(user) if user else None

    def _meta(self, q, user):
        return 200, {"node": self.app.node_name, "peers": self.app.peer_names, "mode": self.app.mode,
                     "categories": list(CATEGORIES), "sorts": self.app.forum.rankings.options()}

    def _me(self, q, user):
        return 200, {"name": self._viewer(user)}

    def _topics(self, q, user):
        topics = self.app.forum.list_topics(self._viewer(user), q.get("sort"), q.get("q", ""), q.get("category", ""))
        return 200, [asdict(t) for t in topics]

    def _topic(self, q, user):
        try:
            page = self.app.forum.topic_page(q.get("id", ""), self._viewer(user))
        except ForumError as e:
            return 404, {"error": str(e)}
        return 200, {"topic": asdict(page.topic), "comments": [asdict(c) for c in page.comments]}

    def _network(self, q, user):
        return 200, self.app.network_status()

    # ---------------------------------------------------------------- POST
    def handle_post(self, action: str, payload: dict, token: str | None) -> tuple[int, object]:
        user = self.app.auth.user_from_token(token)
        ctx = InteractionContext(self.app.forum, self.app.auth, user, self.app.sync_now)
        try:
            interaction = self.app.interactions.create(action, payload)
            return 200, interaction.run(ctx)
        except InteractionError as e:
            return e.status, {"error": str(e)}
        except (AuthError, ForumError) as e:
            return 400, {"error": str(e)}


def make_http_server(api: ForumWebApi, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # keep the terminal clean
            pass

        def _token(self):
            h = self.headers.get("Authorization", "")
            return h[7:] if h.startswith("Bearer ") else None

        def _send(self, status, obj, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html")
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                self._send(*api.handle_get(url.path, query, self._token()))
            except Exception as e:
                self._send(500, {"error": str(e)})

        def do_POST(self):
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                return self._send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "Bad JSON"})
            try:
                self._send(*api.handle_post(url.path[5:], payload, self._token()))
            except Exception as e:
                self._send(500, {"error": str(e)})

    return ThreadingHTTPServer((host, port), Handler)
