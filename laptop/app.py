"""LoRa NDN Forum - start here.

Real radios (one laptop each):
    python3 app.py --name qanoo  --peer zeynep          (on Qanoo's laptop)
    python3 app.py --name zeynep --peer qanoo          (on Zeynep's laptop)
then open http://127.0.0.1:8000

Simulation (no hardware, both forums on one laptop):
    python3 app.py --sim
then open http://127.0.0.1:8001 and http://127.0.0.1:8002

This file is the "composition root": the only place that creates the concrete
classes and plugs them together (Dependency Injection). LoRaForumApp is the
Facade for the whole program.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ndn import LoRaLinkFacade, MemoryTransport, NdnNode, SerialTransport, TransportFactory  # noqa: E402
from forum.auth import AuthService  # noqa: E402
from forum.events import ForumEventFactory  # noqa: E402
from forum.interactions import default_interaction_factory  # noqa: E402
from forum.observers import ActivityFeed, ForumEventBus, RadioTrafficLog  # noqa: E402
from forum.repository import create_sqlite_repository  # noqa: E402
from forum.service import ForumService  # noqa: E402
from forum.sync import ForumLogPublisher, PeerSynchronizer, RemoteForumProxy  # noqa: E402
from forum.webapi import ForumWebApi, make_http_server  # noqa: E402


class LoRaForumApp:
    """Facade: builds and runs one complete forum node."""

    def __init__(self, node_name: str, peers: list[str], transport: SerialTransport, db_path: str,
                 mode: str = "radio", sync_interval: float = 6.0):
        self.node_name, self.peer_names, self.mode = node_name, peers, mode

        # network layer
        self.link = LoRaLinkFacade(transport)
        self.node = NdnNode(self.link)
        self.traffic = RadioTrafficLog()
        self.node.subscribe(self.traffic)

        # forum layer
        self.repo = create_sqlite_repository(db_path)
        self.bus = ForumEventBus()
        self.activity = ActivityFeed()
        self.bus.subscribe(self.activity)
        self.forum = ForumService(self.repo, self.bus, node_name)
        secret = hashlib.sha256(("lora-forum:" + node_name + ":" + db_path).encode()).digest()
        self.auth = AuthService(self.repo.users, node_name, secret)
        self.interactions = default_interaction_factory()

        # forum <-> network
        events = ForumEventFactory()
        ForumLogPublisher(node_name, self.node, self.repo, events)
        self.proxies = [RemoteForumProxy(p, node_name, self.node, events,
                                         progress_reader=lambda p=p: self.repo.progress.get(p))
                        for p in peers]
        self.sync = PeerSynchronizer(self.forum, self.repo, self.proxies, sync_interval)
        self.api = ForumWebApi(self)

    def start(self) -> None:
        self.node.start()
        self.sync.start()

    def sync_now(self) -> None:
        self.sync.sync_now()

    def network_status(self) -> dict:
        return {"node": self.node_name, "mode": self.mode, "radio": self.node.status(),
                "peers": [p.status() for p in self.proxies], "last_sync": self.sync.last_sync,
                "traffic": self.traffic.recent(), "activity": self.activity.recent()}

    def make_server(self, http_port: int, host: str = "127.0.0.1"):
        """Create the web server, trying the next ports if this one is busy."""
        for port in range(http_port, http_port + 20):
            try:
                server = make_http_server(self.api, port, host)
                self.url = "http://127.0.0.1:%d" % port
                return server
            except OSError:
                print("Port %d is busy, trying %d..." % (port, port + 1))
        raise OSError("No free port found near %d" % http_port)


def open_browser(*urls: str) -> None:
    for url in urls:
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            pass


def run_simulation(loss: float) -> None:
    """Two complete forums in one process, connected by a simulated LoRa link."""
    a_t, b_t = MemoryTransport.pair(loss=loss, airtime_s=0.08)
    data = Path(__file__).resolve().parent / "data"
    data.mkdir(exist_ok=True)
    a = LoRaForumApp("nodeA", ["nodeB"], a_t, str(data / "sim_nodeA.db"), mode="simulated", sync_interval=3)
    b = LoRaForumApp("nodeB", ["nodeA"], b_t, str(data / "sim_nodeB.db"), mode="simulated", sync_interval=3)
    for app in (a, b):
        app.start()
    server_a, server_b = a.make_server(8001), b.make_server(8002)
    threading.Thread(target=server_b.serve_forever, daemon=True).start()
    print()
    print("  Simulated radio link, %.0f%% packet loss." % (loss * 100))
    print("  nodeA forum:  %s" % a.url)
    print("  nodeB forum:  %s" % b.url)
    print("  Keep this window open. Press Ctrl+C to stop.")
    print()
    open_browser(a.url, b.url)
    server_a.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="LoRa NDN forum")
    ap.add_argument("--name", help="this laptop's node name, e.g. qanoo")
    ap.add_argument("--peer", action="append", default=[], help="the other laptop's node name (repeatable)")
    ap.add_argument("--port", help="Pico serial port, e.g. /dev/cu.usbmodem101 or COM3 (auto if omitted)")
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--db", help="database file (default data/<name>.db)")
    ap.add_argument("--sim", action="store_true", help="run two forums with a simulated radio, no hardware")
    ap.add_argument("--loss", type=float, default=0.1, help="simulated packet loss (0-1)")
    args = ap.parse_args()

    if args.sim:
        return run_simulation(args.loss)
    if not args.name or not args.peer:
        ap.error("--name and --peer are required (or use --sim)")
    for n in [args.name] + args.peer:
        if not n.replace("-", "").replace("_", "").isalnum():
            ap.error("node names may only use letters, digits, - and _")

    transport = TransportFactory.create(args.port)
    data = Path(__file__).resolve().parent / "data"
    data.mkdir(exist_ok=True)
    app = LoRaForumApp(args.name, args.peer, transport, args.db or str(data / ("%s.db" % args.name)))
    app.start()
    time.sleep(0.3)
    server = app.make_server(args.http_port)
    print()
    print("  Forum '%s' is running: %s" % (args.name, app.url))
    print("  Talking to: %s over LoRa. Keep this window open, Ctrl+C stops it." % ", ".join(args.peer))
    print()
    open_browser(app.url)
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
