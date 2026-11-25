"""Strategy pattern: different ways to order the topic list.

The ForumService does not contain any sorting rules. It is given a
TopicRankingStrategy and just calls rank(). The user picks one in the web page.
Adding a new ordering = adding one class (Open/Closed).
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from .models import Topic


class TopicRankingStrategy(ABC):
    key: str = ""
    label: str = ""

    @abstractmethod
    def rank(self, topics: list[Topic]) -> list[Topic]: ...


class NewestFirst(TopicRankingStrategy):
    key, label = "new", "Newest"

    def rank(self, topics):
        return sorted(topics, key=lambda t: t.created_at, reverse=True)


class TopVoted(TopicRankingStrategy):
    key, label = "top", "Top voted"

    def rank(self, topics):
        return sorted(topics, key=lambda t: (t.score, t.created_at), reverse=True)


class MostDiscussed(TopicRankingStrategy):
    key, label = "discussed", "Most discussed"

    def rank(self, topics):
        return sorted(topics, key=lambda t: (t.comment_count, t.created_at), reverse=True)


class Trending(TopicRankingStrategy):
    """Votes and comments count more when they are recent (like Hacker News)."""
    key, label = "trending", "Trending"

    def rank(self, topics):
        now = time.time()

        def heat(t: Topic) -> float:
            hours = max(0.0, (now - t.created_at) / 3600)
            return (t.score + 0.5 * t.comment_count + 1) / (hours + 2) ** 1.5

        return sorted(topics, key=heat, reverse=True)


class RankingRegistry:
    def __init__(self, *strategies: TopicRankingStrategy):
        self._by_key = {s.key: s for s in strategies}
        self.default = strategies[0]

    def get(self, key: str | None) -> TopicRankingStrategy:
        return self._by_key.get(key or "", self.default)

    def options(self) -> list[dict]:
        return [{"key": s.key, "label": s.label} for s in self._by_key.values()]


def default_rankings() -> RankingRegistry:
    return RankingRegistry(NewestFirst(), Trending(), TopVoted(), MostDiscussed())
