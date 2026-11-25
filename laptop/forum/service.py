"""ForumService: the forum's rules (business logic) in one place.

It does not know about HTTP, HTML, SQLite or LoRa:
  * storage comes in through the ForumRepository interfaces (Dependency Inversion),
  * every change is a ForumEvent command (Command pattern),
  * listeners are told through the ForumEventBus (Observer),
  * ordering is delegated to a TopicRankingStrategy (Strategy).
"""
from __future__ import annotations

import time
import uuid

from .events import CommentAdded, ForumEvent, TopicCreated, VoteCast
from .models import (CATEGORIES, MAX_COMMENT, MAX_TITLE, MAX_TOPIC_BODY, Comment, Topic,
                     TopicPage, Vote)
from .observers import ForumEventBus
from .ranking import RankingRegistry, default_rankings
from .repository import ForumRepository


class ForumError(ValueError):
    pass


class ForumService:
    def __init__(self, repo: ForumRepository, bus: ForumEventBus, node_name: str,
                 rankings: RankingRegistry | None = None):
        self.repo = repo
        self.bus = bus
        self.node = node_name
        self.rankings = rankings or default_rankings()

    # ------------------------------------------------------------ commands
    def create_topic(self, author: str, title: str, body: str, category: str = "general") -> Topic:
        title, body = (title or "").strip(), (body or "").strip()
        if not title:
            raise ForumError("A topic needs a title.")
        if len(title) > MAX_TITLE:
            raise ForumError("Title is too long (max %d characters)." % MAX_TITLE)
        if len(body) > MAX_TOPIC_BODY:
            raise ForumError("Text is too long for LoRa (max %d characters)." % MAX_TOPIC_BODY)
        if category not in CATEGORIES:
            category = "general"
        topic = Topic(self._new_id(), title, body, category, author, self.node, time.time())
        self._record(TopicCreated(topic))
        return topic

    def add_comment(self, author: str, topic_id: str, body: str, reply_to: str | None = None) -> Comment:
        body = (body or "").strip()
        if not self.repo.topics.get(topic_id):
            raise ForumError("That topic does not exist (yet) on this laptop.")
        if not body:
            raise ForumError("A comment can't be empty.")
        if len(body) > MAX_COMMENT:
            raise ForumError("Comment is too long for LoRa (max %d characters)." % MAX_COMMENT)
        comment = Comment(self._new_id(), topic_id, body, author, self.node, time.time(), reply_to or None)
        self._record(CommentAdded(comment))
        return comment

    def vote(self, voter: str, target_id: str, value: int) -> Vote:
        if value not in (-1, 0, 1):
            raise ForumError("A vote is +1, -1 or 0.")
        vote = Vote(target_id, voter, value, time.time())
        self._record(VoteCast(vote))
        return vote

    def apply_remote(self, event: ForumEvent) -> None:
        """An event that arrived from the other laptop over LoRa."""
        event.apply(self.repo)
        self.bus.publish(event, remote=True)

    # ------------------------------------------------------------ queries
    def list_topics(self, viewer: str | None = None, sort: str | None = None,
                    search: str = "", category: str = "") -> list[Topic]:
        topics = self._decorate(self.repo.topics.all(), viewer)
        if search:
            s = search.lower()
            topics = [t for t in topics if s in t.title.lower() or s in t.body.lower() or s in t.author.lower()]
        if category:
            topics = [t for t in topics if t.category == category]
        return self.rankings.get(sort).rank(topics)

    def topic_page(self, topic_id: str, viewer: str | None = None) -> TopicPage:
        topic = self.repo.topics.get(topic_id)
        if not topic:
            raise ForumError("Topic not found.")
        scores = self.repo.votes.scores()
        mine = self.repo.votes.votes_of(viewer) if viewer else {}
        delivered = self.repo.log.delivered_items()
        comments = self.repo.comments.for_topic(topic_id)
        for c in comments:
            c.score, c.my_vote = scores.get(c.id, 0), mine.get(c.id, 0)
            c.delivered = c.id in delivered
        return TopicPage(self._decorate([topic], viewer)[0], comments)

    # ------------------------------------------------------------ helpers
    def _record(self, event: ForumEvent) -> None:
        self.repo.log.append(event.item_id, event.to_wire())   # will be served over LoRa
        event.apply(self.repo)
        self.bus.publish(event, remote=False)

    def _decorate(self, topics: list[Topic], viewer: str | None) -> list[Topic]:
        scores = self.repo.votes.scores()
        counts = self.repo.comments.counts()
        mine = self.repo.votes.votes_of(viewer) if viewer else {}
        delivered = self.repo.log.delivered_items()
        for t in topics:
            t.score, t.comment_count = scores.get(t.id, 0), counts.get(t.id, 0)
            t.my_vote, t.delivered = mine.get(t.id, 0), t.id in delivered
        return topics

    def _new_id(self) -> str:
        return "%s:%s" % (self.node, uuid.uuid4().hex[:8])
