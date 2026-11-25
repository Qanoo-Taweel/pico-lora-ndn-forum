"""Incoming packet processing as a Chain of Responsibility.

Every packet that arrives over LoRa walks down a chain of small handlers.
Each handler either deals with the packet (and stops the chain) or passes it
to the next one:

    DuplicateInterestFilter -> ContentStoreResponder -> ProducerResponder
        -> PendingInterestSatisfier -> UnhandledPacketLogger

Design patterns:
  * Chain of Responsibility: handlers are linked with set_next(); the sender of
    a packet does not know which handler will process it.
  * Template Method: PacketHandler.handle() is the fixed algorithm; subclasses
    only fill in _try_handle().
  * Open/Closed: a new rule (e.g. a blacklist) is a new handler class inserted
    into the chain; the existing handlers stay untouched.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .packets import Data, Interest, Packet, split_segment_name

if TYPE_CHECKING:
    from .node import NdnNode


class PacketHandler(ABC):
    def __init__(self):
        self._next: PacketHandler | None = None

    def set_next(self, handler: "PacketHandler") -> "PacketHandler":
        self._next = handler
        return handler          # allows a.set_next(b).set_next(c)

    def handle(self, packet: Packet, node: "NdnNode") -> None:
        """Template method: try this handler, otherwise pass the packet on."""
        if not self._try_handle(packet, node) and self._next is not None:
            self._next.handle(packet, node)

    @abstractmethod
    def _try_handle(self, packet: Packet, node: "NdnNode") -> bool:
        """Return True if the packet was fully handled here."""


class DuplicateInterestFilter(PacketHandler):
    """Drops an Interest we have already seen (same nonce)."""

    def _try_handle(self, packet, node):
        if isinstance(packet, Interest) and node.nonces.seen_before(packet.nonce):
            node.notify("drop", packet.name, "duplicate interest")
            return True
        return False


class ContentStoreResponder(PacketHandler):
    """Answers an Interest from the cache if we already have the Data."""

    def _try_handle(self, packet, node):
        if not isinstance(packet, Interest):
            return False
        data = node.content_store.get(packet.name)
        if data is None:
            return False
        node.send_packet(data)
        node.notify("served", packet.name, "from cache")
        return True


class ProducerResponder(PacketHandler):
    """Asks the application (a registered producer) to create the Data."""

    def _try_handle(self, packet, node):
        if not isinstance(packet, Interest):
            return False
        split = split_segment_name(packet.name)
        base, index = split if split else (packet.name, 0)
        producer = node.find_producer(base)
        if producer is None:
            return False
        content = producer.produce(base)
        if content is None:
            node.notify("drop", packet.name, "producer has no such data")
            return True
        segments = node.segmenter.split(base, content)
        for seg in segments:
            node.content_store.put(seg, producer.ttl)
        if index < len(segments):
            node.send_packet(segments[index])
            node.notify("served", packet.name, "produced")
        return True


class PendingInterestSatisfier(PacketHandler):
    """Hands an incoming Data packet to whoever is waiting for it."""

    def _try_handle(self, packet, node):
        if not isinstance(packet, Data):
            return False
        if node.pit.satisfy(packet):
            node.content_store.put(packet, ttl=2.0)   # short: some names (head) change
            return True
        return False


class UnhandledPacketLogger(PacketHandler):
    """End of the chain: nobody wanted this packet."""

    def _try_handle(self, packet, node):
        node.notify("drop", packet.name, "no handler (not for us)")
        return True


def build_default_chain() -> PacketHandler:
    head = DuplicateInterestFilter()
    (head.set_next(ContentStoreResponder())
         .set_next(ProducerResponder())
         .set_next(PendingInterestSatisfier())
         .set_next(UnhandledPacketLogger()))
    return head
