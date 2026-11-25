"""Tests: run with   python3 -m unittest discover -s tests -v   (from the laptop folder)."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import LoRaForumApp  # noqa: E402
from forum.events import CommentAdded, ForumEventFactory, TopicCreated  # noqa: E402
from forum.models import Comment, Topic  # noqa: E402
from forum.service import ForumError  # noqa: E402
from ndn import FunctionProducer, LoRaLinkFacade, MemoryTransport, NdnNode, PacketFactory  # noqa: E402
from ndn.packets import MAX_FRAME, Segmenter  # noqa: E402


class PacketTests(unittest.TestCase):
    def test_interest_roundtrip(self):
        f = PacketFactory()
        i = f.interest("/forum/qanoo/head/zeynep/3")
        back = f.decode(i.encode())
        self.assertEqual(back.name, i.name)
        self.assertEqual(back.nonce, i.nonce)

    def test_segments_fit_lora(self):
        segs = Segmenter(PacketFactory()).split("/forum/qanoo/log/12", "ğ".encode() * 900)
        self.assertGreater(len(segs), 1)
        for s in segs:
            self.assertLessEqual(len(s.encode()), MAX_FRAME)

    def test_event_factory(self):
        f = ForumEventFactory()
        ev = TopicCreated(Topic("a:1", "Hello", "Body", "general", "k@a", "a", 1.0))
        self.assertIsInstance(f.from_wire(ev.to_wire()), TopicCreated)
        ev2 = CommentAdded(Comment("a:2", "a:1", "Hi", "k@a", "a", 2.0))
        self.assertEqual(f.from_wire(ev2.to_wire()).comment.body, "Hi")


class NdnTests(unittest.TestCase):
    def test_fetch_with_packet_loss(self):
        a, b = MemoryTransport.pair(loss=0.25)
        na, nb = NdnNode(LoRaLinkFacade(a), interest_timeout=0.3, retries=6), NdnNode(LoRaLinkFacade(b), interest_timeout=0.3, retries=6)
        na.start(); nb.start()
        text = ("lora " * 300).encode()
        na.publish("/t/story", text)
        self.assertEqual(nb.fetch("/t/story"), text)

    def test_producer(self):
        a, b = MemoryTransport.pair()
        na, nb = NdnNode(LoRaLinkFacade(a)), NdnNode(LoRaLinkFacade(b))
        na.start(); nb.start()
        na.register_producer("/t/echo", FunctionProducer(lambda n: n.encode(), ttl=1))
        self.assertEqual(nb.fetch("/t/echo/x"), b"/t/echo/x")
        self.assertIsNone(nb.fetch("/nobody/home", timeout=0.2))


class ForumSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ta, tb = MemoryTransport.pair(loss=0.1)
        self.a = LoRaForumApp("nodeA", ["nodeB"], ta, self.tmp.name + "/a.db", sync_interval=0.3)
        self.b = LoRaForumApp("nodeB", ["nodeA"], tb, self.tmp.name + "/b.db", sync_interval=0.3)
        for app in (self.a, self.b):
            app.node.interest_timeout = 0.3
            app.node.retries = 6
            app.start()

    def tearDown(self):
        self.a.sync.stop(); self.b.sync.stop()
        self.tmp.cleanup()

    def wait_for(self, cond, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.1)
        return False

    def test_topic_comment_vote_travel_both_ways(self):
        token_a = self.a.interactions.create("register", {"nickname": "qanoo", "password": "secret1"})
        from forum.interactions import InteractionContext
        ctx_a = InteractionContext(self.a.forum, self.a.auth, None)
        token_a.run(ctx_a)
        user_a = self.a.repo.users.by_nickname("qanoo")
        topic = self.a.forum.create_topic("qanoo@nodeA", "Hello over LoRa", "First post!", "general")

        self.assertTrue(self.wait_for(lambda: self.b.repo.topics.get(topic.id) is not None))
        self.b.forum.add_comment("zeynep@nodeB", topic.id, "Got it on the other laptop")
        self.b.forum.vote("zeynep@nodeB", topic.id, 1)

        self.assertTrue(self.wait_for(lambda: len(self.a.repo.comments.for_topic(topic.id)) == 1))
        self.assertTrue(self.wait_for(lambda: self.a.forum.list_topics()[0].score == 1))
        # B has acknowledged A's topic -> A shows it as delivered
        self.assertTrue(self.wait_for(lambda: self.a.forum.list_topics()[0].delivered))
        self.assertIsNotNone(user_a)

    def test_validation(self):
        with self.assertRaises(ForumError):
            self.a.forum.create_topic("qanoo@nodeA", "", "")
        with self.assertRaises(ForumError):
            self.a.forum.add_comment("qanoo@nodeA", "nodeA:missing", "hi")


if __name__ == "__main__":
    unittest.main()
