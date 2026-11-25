"""Plain data classes for the forum. No logic, no database code (Single Responsibility)."""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_TITLE = 120
MAX_TOPIC_BODY = 1000
MAX_COMMENT = 500
CATEGORIES = ("general", "question", "announcement", "idea")


@dataclass
class User:
    id: int
    nickname: str
    password_hash: str
    created_at: float


@dataclass
class Topic:
    id: str            # globally unique, e.g. "qanoo:3f9a12c0"
    title: str
    body: str
    category: str
    author: str        # "nickname@node"
    origin: str        # node that created it
    created_at: float
    score: int = 0
    comment_count: int = 0
    my_vote: int = 0
    delivered: bool = False


@dataclass
class Comment:
    id: str
    topic_id: str
    body: str
    author: str
    origin: str
    created_at: float
    reply_to: str | None = None
    score: int = 0
    my_vote: int = 0
    delivered: bool = False


@dataclass
class Vote:
    target_id: str
    voter: str         # "nickname@node"
    value: int         # +1 or -1 (0 = vote removed)
    created_at: float


@dataclass
class TopicPage:
    topic: Topic
    comments: list[Comment] = field(default_factory=list)
