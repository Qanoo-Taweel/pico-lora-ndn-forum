"""Observer pattern: things that want to know when the forum changes.

ForumEventBus is the "subject". Anything can subscribe() to it and gets told
about every new event, without the ForumService knowing who is listening.
ActivityFeed and RadioTrafficLog are two observers used by the web page.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from .events import CommentAdded, ForumEvent, TopicCreated, VoteCast

EventListener = Callable[[ForumEvent, bool], None]   # (event, came_from_radio)


class ForumEventBus:
    def __init__(self):
        self._listeners: list[EventListener] = []

    def subscribe(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def publish(self, event: ForumEvent, remote: bool) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, remote)
            except Exception as e:
                print("[bus] listener error:", e)


class ActivityFeed:
    """Observer: keeps a short human-readable list of recent forum activity."""

    def __init__(self, size: int = 30):
        self._items: deque[dict] = deque(maxlen=size)
        self._lock = threading.Lock()

    def __call__(self, event: ForumEvent, remote: bool) -> None:
        if isinstance(event, TopicCreated):
            text = "%s started \"%s\"" % (event.topic.author, event.topic.title)
        elif isinstance(event, CommentAdded):
            text = "%s commented" % event.comment.author
        elif isinstance(event, VoteCast):
            text = "%s voted" % event.vote.voter
        else:
            text = event.kind
        with self._lock:
            self._items.appendleft({"text": text, "via_lora": remote, "time": time.time(),
                                    "item": event.item_id})

    def recent(self) -> list[dict]:
        with self._lock:
            return list(self._items)


class RadioTrafficLog:
    """Observer of the NDN node: remembers the last packets for the live radio panel."""

    def __init__(self, size: int = 60):
        self._items: deque[dict] = deque(maxlen=size)
        self._lock = threading.Lock()

    def __call__(self, kind: str, name: str, detail: str) -> None:
        with self._lock:
            self._items.appendleft({"kind": kind, "name": name, "detail": detail, "time": time.time()})

    def recent(self) -> list[dict]:
        with self._lock:
            return list(self._items)
