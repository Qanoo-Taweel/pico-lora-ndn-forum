"""Forum <-> NDN: how the two laptops share forum events over LoRa.

Names used on the radio (every laptop is the producer of its own names):

    /forum/<node>/head/<asker>/<ack>   -> {"h": latest event number}   (changes, short cache)
    /forum/<node>/log/<n>              -> event number n as JSON       (never changes)

<ack> is how far <asker> has already read. That lets the producer mark its
own posts as "delivered" (the double tick in the web page).

Design patterns:
  * Proxy (remote proxy + caching proxy): RemoteForumProxy implements the same
    ForumEventSource interface as LocalForumEventSource, but the real forum it
    stands for lives on the OTHER laptop. Callers just call
    latest_sequence() / get_event(n); the proxy turns that into NDN Interests
    over LoRa, retries, caches results and tracks whether the peer is online.
  * Facade use: the proxy and publisher only talk to NdnNode, never to the radio.
  * Single Responsibility: publishing (ForumLogPublisher), remote access
    (RemoteForumProxy) and the sync loop (PeerSynchronizer) are separate classes.
"""
from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod

from ndn import NdnNode, Producer

from .events import ForumEvent, ForumEventFactory, UnknownEventError
from .repository import ForumRepository
from .service import ForumService

PREFIX = "/forum"


def head_name(node: str, asker: str, ack: int) -> str:
    return "%s/%s/head/%s/%d" % (PREFIX, node, asker, ack)


def log_name(node: str, seq: int) -> str:
    return "%s/%s/log/%d" % (PREFIX, node, seq)


# ------------------------------------------------------------------ subject interface
class ForumEventSource(ABC):
    """Anything we can read forum events from, by number."""

    @abstractmethod
    def latest_sequence(self) -> int | None: ...

    @abstractmethod
    def get_event(self, seq: int) -> ForumEvent | None: ...


class LocalForumEventSource(ForumEventSource):
    """The real subject: this laptop's own event log."""

    def __init__(self, repo: ForumRepository, factory: ForumEventFactory):
        self._repo, self._factory = repo, factory

    def latest_sequence(self):
        return self._repo.log.head()

    def get_event(self, seq):
        raw = self._repo.log.get(seq)
        return self._factory.from_wire(raw) if raw else None

    def raw_event(self, seq: int) -> bytes | None:
        return self._repo.log.get(seq)


class RemoteForumProxy(ForumEventSource):
    """Proxy: a local stand-in for the forum running on the other laptop."""

    OFFLINE_AFTER_S = 30

    def __init__(self, peer: str, me: str, node: NdnNode, factory: ForumEventFactory,
                 progress_reader=lambda: 0):
        self.peer, self._me, self._node, self._factory = peer, me, node, factory
        self._progress = progress_reader
        self._cache: dict[int, ForumEvent] = {}          # caching proxy part
        self.last_seen: float | None = None
        self.last_head: int | None = None
        self.last_error: str = ""

    @property
    def online(self) -> bool:
        return self.last_seen is not None and time.time() - self.last_seen < self.OFFLINE_AFTER_S

    def latest_sequence(self):
        raw = self._node.fetch(head_name(self.peer, self._me, self._progress()))
        if raw is None:
            self.last_error = "no answer from %s" % self.peer
            return None
        try:
            self.last_head = int(json.loads(raw)["h"])
        except (ValueError, KeyError):
            self.last_error = "bad head from %s" % self.peer
            return None
        self.last_seen = time.time()
        return self.last_head

    def get_event(self, seq):
        if seq in self._cache:
            return self._cache[seq]
        raw = self._node.fetch(log_name(self.peer, seq))
        if raw is None:
            self.last_error = "event %d did not arrive" % seq
            return None
        self.last_seen = time.time()
        try:
            event = self._factory.from_wire(raw)
        except UnknownEventError as e:
            self.last_error = str(e)
            return None
        self._cache[seq] = event
        return event

    def status(self) -> dict:
        return {"peer": self.peer, "online": self.online, "last_seen": self.last_seen,
                "their_latest": self.last_head, "last_error": self.last_error}


# ------------------------------------------------------------------ producer side
class ForumLogProducer(Producer):
    """Strategy given to the NDN node: answers Interests for /forum/<me>/..."""

    ttl = 2.0   # head changes, so it is cached only briefly

    def __init__(self, me: str, source: LocalForumEventSource, repo: ForumRepository):
        self._me, self._source, self._repo = me, source, repo
        self._base = "%s/%s/" % (PREFIX, me)

    def produce(self, name: str) -> bytes | None:
        if not name.startswith(self._base):
            return None
        parts = name[len(self._base):].split("/")
        if parts[0] == "head" and len(parts) == 3 and parts[2].isdigit():
            self._repo.log.mark_delivered_up_to(int(parts[2]))    # the asker's acknowledgement
            return json.dumps({"h": self._source.latest_sequence()}).encode()
        if parts[0] == "log" and len(parts) == 2 and parts[1].isdigit():
            return self._source.raw_event(int(parts[1]))
        return None


class ForumLogPublisher:
    def __init__(self, me: str, node: NdnNode, repo: ForumRepository, factory: ForumEventFactory):
        self.source = LocalForumEventSource(repo, factory)
        node.register_producer("%s/%s" % (PREFIX, me), ForumLogProducer(me, self.source, repo))


# ------------------------------------------------------------------ sync loop
class PeerSynchronizer:
    """Every few seconds: ask each peer proxy for new events and apply them."""

    def __init__(self, service: ForumService, repo: ForumRepository, proxies: list[RemoteForumProxy],
                 interval_s: float = 6.0):
        self._service, self._repo, self.proxies = service, repo, proxies
        self._interval = interval_s
        self._wake = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self.last_sync: float | None = None

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._loop, name="peer-sync", daemon=True).start()

    def stop(self) -> None:
        self._running = False
        self._wake.set()

    def sync_now(self) -> None:
        self._wake.set()

    def sync_once(self) -> int:
        """Returns how many new events were applied."""
        applied = 0
        with self._lock:
            for proxy in self.proxies:
                head = proxy.latest_sequence()
                if head is None:
                    continue
                done = self._repo.progress.get(proxy.peer)
                for seq in range(done + 1, head + 1):
                    event = proxy.get_event(seq)
                    if event is None:
                        break                      # try again next round
                    self._service.apply_remote(event)
                    self._repo.progress.set(proxy.peer, seq)
                    applied += 1
            self.last_sync = time.time()
        return applied

    def _loop(self) -> None:
        while self._running:
            try:
                self.sync_once()
            except Exception as e:
                print("[sync] error:", e)
            self._wake.wait(self._interval)
            self._wake.clear()
