"""NdnNode: the whole NDN layer behind one easy interface.

    node.register_producer("/forum/qanoo", producer)   # answer Interests under a prefix
    node.publish("/forum/qanoo/hello", b"hi")           # put immutable data in the cache
    content = node.fetch("/forum/zeynep/hello")           # ask the network by name

Design patterns:
  * Facade: hides PacketFactory, Segmenter, ContentStore, PIT, nonce log,
    the handler chain and the LoRa link.
  * Observer: listeners added with subscribe() are told about every packet
    (the web page uses it for the live "radio traffic" panel).
  * Strategy: a Producer object decides how Data is created for a prefix.
  * Dependency Inversion: the node receives a LoRaLinkFacade from outside
    (real radio or simulator); it never creates one itself.
"""
from __future__ import annotations

import random
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable

from .handlers import PacketHandler, build_default_chain
from .link import LinkError, LoRaLinkFacade
from .packets import Data, Interest, Packet, PacketError, PacketFactory, Segmenter, segment_name
from .tables import ContentStore, NonceLog, PendingInterestTable

Listener = Callable[[str, str, str], None]   # (kind, name, detail)


class Producer(ABC):
    """Strategy: creates content for names under a prefix. ttl=None -> immutable."""

    ttl: float | None = None

    @abstractmethod
    def produce(self, name: str) -> bytes | None: ...


class FunctionProducer(Producer):
    def __init__(self, fn: Callable[[str], bytes | None], ttl: float | None = None):
        self._fn, self.ttl = fn, ttl

    def produce(self, name):
        return self._fn(name)


class NdnNode:
    def __init__(self, link: LoRaLinkFacade, chain: PacketHandler | None = None,
                 interest_timeout: float = 2.5, retries: int = 3):
        self.link = link
        self.factory = PacketFactory()
        self.segmenter = Segmenter(self.factory)
        self.content_store = ContentStore()
        self.pit = PendingInterestTable()
        self.nonces = NonceLog()
        self._chain = chain or build_default_chain()
        self._producers: dict[str, Producer] = {}
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self.interest_timeout = interest_timeout
        self.retries = retries
        link.on_frame(self._on_frame)

    # ---------- lifecycle ----------
    def start(self) -> None:
        self.link.start()

    def stop(self) -> None:
        self.link.stop()

    # ---------- Observer ----------
    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def notify(self, kind: str, name: str, detail: str = "") -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, name, detail)
            except Exception:
                pass

    # ---------- producer side ----------
    def register_producer(self, prefix: str, producer: Producer) -> None:
        with self._lock:
            self._producers[prefix.rstrip("/")] = producer

    def find_producer(self, name: str) -> Producer | None:
        with self._lock:
            best = None
            for prefix, producer in self._producers.items():
                if (name == prefix or name.startswith(prefix + "/")) and (best is None or len(prefix) > len(best[0])):
                    best = (prefix, producer)
        return best[1] if best else None

    def publish(self, name: str, content: bytes) -> None:
        """Store immutable content; it will be served to anyone who asks."""
        for seg in self.segmenter.split(name, content):
            self.content_store.put(seg)

    # ---------- consumer side ----------
    def express_interest(self, name: str, timeout: float | None = None, retries: int | None = None) -> Data | None:
        """Send an Interest and wait for the matching Data (with retransmissions)."""
        cached = self.content_store.get(name)
        if cached is not None:
            return cached
        timeout = timeout or self.interest_timeout
        for attempt in range((retries if retries is not None else self.retries) + 1):
            entry = self.pit.expect(name)
            try:
                self.send_packet(self.factory.interest(name))
            except LinkError as e:
                self.notify("error", name, str(e))
            # random extra wait reduces repeated collisions on the shared channel
            if entry.event.wait(timeout + random.uniform(0, 0.4)):
                return entry.data
            self.notify("timeout", name, "attempt %d" % (attempt + 1))
        self.pit.forget(name)
        return None

    def fetch(self, name: str, timeout: float | None = None) -> bytes | None:
        """Fetch a complete (possibly multi-segment) object by name."""
        first = self.express_interest(segment_name(name, 0), timeout)
        if first is None:
            return None
        parts = [first.content]
        last = first.final_block or 0
        for i in range(1, last + 1):
            seg = self.express_interest(segment_name(name, i), timeout)
            if seg is None:
                return None
            parts.append(seg.content)
        return b"".join(parts)

    # ---------- wire ----------
    def send_packet(self, packet: Packet) -> None:
        self.link.send(packet.encode())
        self.notify("out", packet.name, type(packet).__name__)

    def _on_frame(self, frame: bytes, rssi: int) -> None:
        try:
            packet = self.factory.decode(frame)
        except (PacketError, UnicodeDecodeError) as e:
            self.notify("drop", "?", "bad frame: %s" % e)
            return
        self.notify("in", packet.name, "%s %d dBm" % (type(packet).__name__, rssi))
        self._chain.handle(packet, self)

    # ---------- info ----------
    def status(self) -> dict:
        return {**self.link.stats, "cached": len(self.content_store), "pending": len(self.pit),
                "producers": sorted(self._producers), "time": time.time()}
