"""Forum events: every change to the forum is one small event object.

Events are what travels over LoRa. Each laptop keeps an append-only log of the
events its own users created; the other laptop fetches that log by name.

Design patterns:
  * Command: each ForumEvent knows how to apply() itself to a repository.
    The same command object is used for local changes and for changes that
    arrive from the other laptop, so both paths share one code path.
  * Factory Method: ForumEventFactory.from_wire() rebuilds the right event
    subclass from the compact JSON that arrived over the radio.
  * Open/Closed: a new kind of change = a new ForumEvent subclass + one
    register() call. Nothing else changes.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable

from .models import Comment, Topic, Vote

if TYPE_CHECKING:
    from .repository import ForumRepository


class ForumEvent(ABC):
    kind: str = ""

    @abstractmethod
    def apply(self, repo: "ForumRepository") -> None: ...

    @abstractmethod
    def to_dict(self) -> dict: ...

    @property
    @abstractmethod
    def item_id(self) -> str: ...

    def to_wire(self) -> bytes:
        """Compact JSON: short keys keep LoRa packets small."""
        return json.dumps({"k": self.kind, **self.to_dict()}, separators=(",", ":"),
                          ensure_ascii=False).encode()


class TopicCreated(ForumEvent):
    kind = "T"

    def __init__(self, topic: Topic):
        self.topic = topic

    @property
    def item_id(self):
        return self.topic.id

    def apply(self, repo):
        repo.topics.add(self.topic)

    def to_dict(self):
        t = self.topic
        return {"i": t.id, "ti": t.title, "b": t.body, "c": t.category, "a": t.author,
                "o": t.origin, "ts": round(t.created_at, 2)}

    @classmethod
    def from_dict(cls, d):
        return cls(Topic(d["i"], d["ti"], d["b"], d["c"], d["a"], d["o"], d["ts"]))


class CommentAdded(ForumEvent):
    kind = "C"

    def __init__(self, comment: Comment):
        self.comment = comment

    @property
    def item_id(self):
        return self.comment.id

    def apply(self, repo):
        repo.comments.add(self.comment)

    def to_dict(self):
        c = self.comment
        d = {"i": c.id, "t": c.topic_id, "b": c.body, "a": c.author, "o": c.origin,
             "ts": round(c.created_at, 2)}
        if c.reply_to:
            d["r"] = c.reply_to
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(Comment(d["i"], d["t"], d["b"], d["a"], d["o"], d["ts"], d.get("r")))


class VoteCast(ForumEvent):
    kind = "V"

    def __init__(self, vote: Vote):
        self.vote = vote

    @property
    def item_id(self):
        return self.vote.target_id

    def apply(self, repo):
        repo.votes.upsert(self.vote)

    def to_dict(self):
        v = self.vote
        return {"t": v.target_id, "a": v.voter, "v": v.value, "ts": round(v.created_at, 2)}

    @classmethod
    def from_dict(cls, d):
        return cls(Vote(d["t"], d["a"], int(d["v"]), d["ts"]))


class UnknownEventError(ValueError):
    pass


class ForumEventFactory:
    """Factory Method: wire bytes -> the right ForumEvent subclass."""

    def __init__(self):
        self._builders: dict[str, Callable[[dict], ForumEvent]] = {}
        for cls in (TopicCreated, CommentAdded, VoteCast):
            self.register(cls.kind, cls.from_dict)

    def register(self, kind: str, builder: Callable[[dict], ForumEvent]) -> None:
        self._builders[kind] = builder

    def from_wire(self, raw: bytes) -> ForumEvent:
        try:
            d = json.loads(raw.decode())
            return self._builders[d["k"]](d)
        except KeyError as e:
            raise UnknownEventError("unknown or incomplete event: %s" % e)
        except (ValueError, TypeError) as e:
            raise UnknownEventError("corrupt event: %s" % e)
