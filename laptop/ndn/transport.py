"""Serial transports: how the laptop talks to the Pico over USB.

Design patterns:
  * Adapter: PyserialTransport and PosixTtyTransport adapt two very different
    serial APIs (the pyserial library / raw POSIX termios) to one small
    interface, SerialTransport. MemoryTransport adapts an in-memory "fake
    radio" to the same interface for tests and the simulator.
  * Factory Method: TransportFactory.create() decides which adapter to build,
    so the rest of the program never imports pyserial or termios.
  * Liskov Substitution: any SerialTransport can replace any other.
"""
from __future__ import annotations

import glob
import os
import queue
import random
import select
import threading
import time
from abc import ABC, abstractmethod


class SerialTransport(ABC):
    """A line-based text channel (one command per line)."""

    @abstractmethod
    def write_line(self, line: str) -> None: ...

    @abstractmethod
    def read_line(self, timeout: float) -> str | None:
        """Return the next line, or None after `timeout` seconds."""

    def close(self) -> None:
        pass


class PyserialTransport(SerialTransport):
    """Adapter for the pyserial library (works on macOS, Linux and Windows)."""

    def __init__(self, port: str):
        import serial  # optional dependency
        self._s = serial.Serial(port, 115200, timeout=0.05)
        self._buf = b""

    def write_line(self, line: str) -> None:
        self._s.write((line + "\n").encode())
        self._s.flush()

    def read_line(self, timeout: float) -> str | None:
        end = time.monotonic() + timeout
        while True:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line.decode(errors="replace").strip()
            if time.monotonic() >= end:
                return None
            self._buf += self._s.read(self._s.in_waiting or 1)

    def close(self) -> None:
        self._s.close()


class PosixTtyTransport(SerialTransport):
    """Adapter for a raw POSIX tty (macOS / Linux) using only the standard library."""

    def __init__(self, port: str):
        import termios
        import tty
        self._fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(self._fd)
        attrs = termios.tcgetattr(self._fd)
        attrs[2] |= termios.CLOCAL | termios.CREAD
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
        self._buf = b""

    def write_line(self, line: str) -> None:
        data = (line + "\n").encode()
        while data:
            try:
                n = os.write(self._fd, data)
                data = data[n:]
            except BlockingIOError:
                select.select([], [self._fd], [], 0.1)

    def read_line(self, timeout: float) -> str | None:
        end = time.monotonic() + timeout
        while True:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line.decode(errors="replace").strip()
            left = end - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([self._fd], [], [], left)
            if r:
                try:
                    chunk = os.read(self._fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise OSError("serial port closed (was the Pico unplugged?)")
                self._buf += chunk

    def close(self) -> None:
        os.close(self._fd)


class MemoryTransport(SerialTransport):
    """A fake Pico + radio living in memory. Two of them form a simulated LoRa link.

    It speaks exactly the same line protocol as bridge_main.py on the real Pico,
    so everything above it behaves as with real hardware.
    """

    def __init__(self, loss: float = 0.0, airtime_s: float = 0.0):
        self._inbox: queue.Queue[str] = queue.Queue()
        self._peers: list[MemoryTransport] = []
        self.loss = loss
        self.airtime_s = airtime_s
        self._inbox.put("READY")

    @classmethod
    def pair(cls, loss: float = 0.0, airtime_s: float = 0.0) -> tuple["MemoryTransport", "MemoryTransport"]:
        a, b = cls(loss, airtime_s), cls(loss, airtime_s)
        a._peers.append(b)
        b._peers.append(a)
        return a, b

    def write_line(self, line: str) -> None:
        if line == "PING":
            self._inbox.put("PONG")
            return
        if not line.startswith("TX "):
            self._inbox.put("ERR unknown command")
            return
        payload = line[3:].strip()

        def deliver():
            if self.airtime_s:
                time.sleep(self.airtime_s)
            for p in self._peers:
                if random.random() >= self.loss:
                    p._inbox.put("RX %s -60" % payload)
            self._inbox.put("OK")

        threading.Thread(target=deliver, daemon=True).start()

    def read_line(self, timeout: float) -> str | None:
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty:
            return None


class TransportFactory:
    """Factory Method: pick the right serial adapter for this computer."""

    @staticmethod
    def find_pico_port() -> str | None:
        try:
            from serial.tools import list_ports
            for p in list_ports.comports():
                if p.vid == 0x2E8A:  # Raspberry Pi's USB vendor id
                    return p.device
        except ImportError:
            pass
        ports = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyACM*"))
        return ports[0] if ports else None

    @staticmethod
    def create(port: str | None = None) -> SerialTransport:
        port = port or TransportFactory.find_pico_port()
        if not port:
            raise OSError("No Pico found. Plug it in, close Thonny, or pass --port.")
        try:
            return PyserialTransport(port)
        except ImportError:
            if os.name != "posix":
                raise OSError("On Windows install pyserial first:  py -m pip install pyserial")
            return PosixTtyTransport(port)
