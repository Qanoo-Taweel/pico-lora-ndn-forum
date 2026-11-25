"""LoRaLinkFacade: one simple object that hides the Pico, USB and the radio.

Design patterns:
  * Facade: callers only see send(frame) and on_frame(callback). They never see
    hex encoding, the TX/OK/RX line protocol, threads or timeouts.
  * Single Responsibility: this class only moves raw frames; it does not know NDN.
  * Dependency Inversion: it depends on the SerialTransport abstraction, so a
    real Pico and the in-memory simulator are interchangeable.
"""
from __future__ import annotations

import binascii
import queue
import threading
from typing import Callable

from .packets import MAX_FRAME
from .transport import SerialTransport

FrameCallback = Callable[[bytes, int], None]   # (frame bytes, rssi dBm)


class LinkError(OSError):
    pass


class LoRaLinkFacade:
    def __init__(self, transport: SerialTransport, tx_timeout: float = 3.0):
        self._t = transport
        self._tx_timeout = tx_timeout
        self._tx_lock = threading.Lock()
        self._tx_result: queue.Queue[str] = queue.Queue()
        self._inbox: queue.Queue[tuple[bytes, int]] = queue.Queue()
        self._callback: FrameCallback | None = None
        self._running = False
        self.stats = {"tx": 0, "rx": 0, "tx_errors": 0, "bad_lines": 0, "last_rssi": None}

    # ---- public API ----
    def on_frame(self, callback: FrameCallback) -> None:
        self._callback = callback

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._reader, name="lora-reader", daemon=True).start()
        threading.Thread(target=self._dispatcher, name="lora-dispatch", daemon=True).start()

    def stop(self) -> None:
        self._running = False
        self._t.close()

    def send(self, frame: bytes) -> None:
        """Transmit one frame over LoRa. Blocks until the radio has finished sending."""
        if len(frame) > MAX_FRAME:
            raise LinkError("frame too large: %d > %d bytes" % (len(frame), MAX_FRAME))
        with self._tx_lock:
            while not self._tx_result.empty():
                self._tx_result.get_nowait()
            self._t.write_line("TX " + binascii.hexlify(frame).decode())
            try:
                result = self._tx_result.get(timeout=self._tx_timeout)
            except queue.Empty:
                self.stats["tx_errors"] += 1
                raise LinkError("Pico did not confirm the transmission (is bridge main.py running?)")
            if result != "OK":
                self.stats["tx_errors"] += 1
                raise LinkError("Pico reported: " + result)
            self.stats["tx"] += 1

    # ---- internals ----
    def _reader(self) -> None:
        """Reads lines from USB. Frames go to a queue so callbacks may call send()."""
        while self._running:
            try:
                line = self._t.read_line(0.5)
            except OSError:
                if self._running:
                    raise
                return
            if not line:
                continue
            if line.startswith("RX "):
                parts = line.split()
                try:
                    frame = binascii.unhexlify(parts[1])
                    rssi = int(parts[2]) if len(parts) > 2 else 0
                except (ValueError, binascii.Error, IndexError):
                    self.stats["bad_lines"] += 1
                    continue
                self.stats["rx"] += 1
                self.stats["last_rssi"] = rssi
                self._inbox.put((frame, rssi))
            elif line == "OK" or line.startswith("ERR"):
                self._tx_result.put(line)
            # READY, PONG and boot messages are ignored

    def _dispatcher(self) -> None:
        while self._running:
            try:
                frame, rssi = self._inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._callback:
                try:
                    self._callback(frame, rssi)
                except Exception as e:  # never let one bad packet kill the link
                    print("[link] handler error:", e)
