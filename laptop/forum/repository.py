"""Storage: small repository interfaces plus their SQLite implementations.

Design patterns / principles:
  * Repository: the rest of the program asks for "topics" or "comments",
    never writes SQL.
  * Interface Segregation: one small interface per kind of data instead of
    one giant "Database" class.
  * Dependency Inversion: ForumService depends on these abstract interfaces;
    SQLite is just one implementation (an in-memory one would also work).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from abc import ABC, abstractmethod

from .models import Comment, Topic, User, Vote


# ------------------------------------------------------------------ interfaces
class UserRepository(ABC):
    @abstractmethod
    def add(self, nickname: str, password_hash: str) -> User: ...
    @abstractmethod
    def by_nickname(self, nickname: str) -> User | None: ...


class TopicRepository(ABC):
    @abstractmethod
    def add(self, topic: Topic) -> None: ...
    @abstractmethod
    def get(self, topic_id: str) -> Topic | None: ...
    @abstractmethod
    def all(self) -> list[Topic]: ...


class CommentRepository(ABC):
    @abstractmethod
    def add(self, comment: Comment) -> None: ...
    @abstractmethod
    def for_topic(self, topic_id: str) -> list[Comment]: ...
    @abstractmethod
    def counts(self) -> dict[str, int]: ...


class VoteRepository(ABC):
    @abstractmethod
    def upsert(self, vote: Vote) -> None: ...
    @abstractmethod
    def scores(self) -> dict[str, int]: ...
    @abstractmethod
    def votes_of(self, voter: str) -> dict[str, int]: ...


class EventLogRepository(ABC):
    """This laptop's own append-only event log (what the other laptop downloads)."""
    @abstractmethod
    def append(self, item_id: str, payload: bytes) -> int: ...
    @abstractmethod
    def get(self, seq: int) -> bytes | None: ...
    @abstractmethod
    def head(self) -> int: ...
    @abstractmethod
    def mark_delivered_up_to(self, seq: int) -> None: ...
    @abstractmethod
    def delivered_items(self) -> set[str]: ...


class PeerProgressRepository(ABC):
    """How far we have read each peer's event log."""
    @abstractmethod
    def get(self, peer: str) -> int: ...
    @abstractmethod
    def set(self, peer: str, seq: int) -> None: ...


class ForumRepository:
    """Groups the repositories so they can be passed around as one object."""

    def __init__(self, users: UserRepository, topics: TopicRepository, comments: CommentRepository,
                 votes: VoteRepository, log: EventLogRepository, progress: PeerProgressRepository):
        self.users, self.topics, self.comments = users, topics, comments
        self.votes, self.log, self.progress = votes, log, progress


# ------------------------------------------------------------------ SQLite
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, nickname TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS topics(id TEXT PRIMARY KEY, title TEXT, body TEXT, category TEXT,
    author TEXT, origin TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS comments(id TEXT PRIMARY KEY, topic_id TEXT, body TEXT, author TEXT,
    origin TEXT, created_at REAL, reply_to TEXT);
CREATE TABLE IF NOT EXISTS votes(target_id TEXT, voter TEXT, value INTEGER, created_at REAL,
    PRIMARY KEY(target_id, voter));
CREATE TABLE IF NOT EXISTS event_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT,
    payload BLOB, created_at REAL, delivered INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS peer_progress(peer TEXT PRIMARY KEY, last_seq INTEGER);
CREATE INDEX IF NOT EXISTS idx_comments_topic ON comments(topic_id);
"""


class SqliteDatabase:
    """One shared connection guarded by a lock (the web server and the radio use threads)."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def execute(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur


class SqliteUserRepository(UserRepository):
    def __init__(self, db: SqliteDatabase):
        self.db = db

    def add(self, nickname, password_hash):
        now = time.time()
        cur = self.db.execute("INSERT INTO users(nickname,password_hash,created_at) VALUES(?,?,?)",
                              (nickname, password_hash, now))
        return User(cur.lastrowid, nickname, password_hash, now)

    def by_nickname(self, nickname):
        rows = self.db.query("SELECT * FROM users WHERE nickname=?", (nickname,))
        return User(**dict(rows[0])) if rows else None


class SqliteTopicRepository(TopicRepository):
    def __init__(self, db: SqliteDatabase):
        self.db = db

    def add(self, t):
        self.db.execute("INSERT OR IGNORE INTO topics VALUES(?,?,?,?,?,?,?)",
                        (t.id, t.title, t.body, t.category, t.author, t.origin, t.created_at))

    def get(self, topic_id):
        rows = self.db.query("SELECT * FROM topics WHERE id=?", (topic_id,))
        return Topic(**dict(rows[0])) if rows else None

    def all(self):
        return [Topic(**dict(r)) for r in self.db.query("SELECT * FROM topics")]


class SqliteCommentRepository(CommentRepository):
    def __init__(self, db: SqliteDatabase):
        self.db = db

    def add(self, c):
        self.db.execute("INSERT OR IGNORE INTO comments VALUES(?,?,?,?,?,?,?)",
                        (c.id, c.topic_id, c.body, c.author, c.origin, c.created_at, c.reply_to))

    def for_topic(self, topic_id):
        return [Comment(**dict(r)) for r in
                self.db.query("SELECT * FROM comments WHERE topic_id=? ORDER BY created_at", (topic_id,))]

    def counts(self):
        return {r["topic_id"]: r["n"] for r in
                self.db.query("SELECT topic_id, COUNT(*) n FROM comments GROUP BY topic_id")}


class SqliteVoteRepository(VoteRepository):
    def __init__(self, db: SqliteDatabase):
        self.db = db

    def upsert(self, v):
        # the newest vote of a person wins, wherever it was cast
        self.db.execute("""INSERT INTO votes VALUES(?,?,?,?)
            ON CONFLICT(target_id, voter) DO UPDATE SET value=excluded.value, created_at=excluded.created_at
            WHERE excluded.created_at >= votes.created_at""",
                        (v.target_id, v.voter, v.value, v.created_at))

    def scores(self):
        return {r["target_id"]: r["s"] for r in
                self.db.query("SELECT target_id, SUM(value) s FROM votes GROUP BY target_id")}

    def votes_of(self, voter):
        return {r["target_id"]: r["value"] for r in
                self.db.query("SELECT target_id, value FROM votes WHERE voter=?", (voter,))}


class SqliteEventLogRepository(EventLogRepository):
    def __init__(self, db: SqliteDatabase):
        self.db = db

    def append(self, item_id, payload):
        return self.db.execute("INSERT INTO event_log(item_id,payload,created_at) VALUES(?,?,?)",
                               (item_id, payload, time.time())).lastrowid

    def get(self, seq):
        rows = self.db.query("SELECT payload FROM event_log WHERE seq=?", (seq,))
        return bytes(rows[0]["payload"]) if rows else None

    def head(self):
        return self.db.query("SELECT COALESCE(MAX(seq),0) h FROM event_log")[0]["h"]

    def mark_delivered_up_to(self, seq):
        self.db.execute("UPDATE event_log SET delivered=1 WHERE seq<=? AND delivered=0", (seq,))

    def delivered_items(self):
        return {r["item_id"] for r in self.db.query("SELECT item_id FROM event_log WHERE delivered=1")}


class SqlitePeerProgressRepository(PeerProgressRepository):
    def __init__(self, db: SqliteDatabase):
        self.db = db

    def get(self, peer):
        rows = self.db.query("SELECT last_seq FROM peer_progress WHERE peer=?", (peer,))
        return rows[0]["last_seq"] if rows else 0

    def set(self, peer, seq):
        self.db.execute("INSERT INTO peer_progress VALUES(?,?) ON CONFLICT(peer) DO UPDATE SET last_seq=excluded.last_seq",
                        (peer, seq))


def create_sqlite_repository(path: str) -> ForumRepository:
    """Factory function that wires all SQLite repositories to one database file."""
    db = SqliteDatabase(path)
    return ForumRepository(SqliteUserRepository(db), SqliteTopicRepository(db), SqliteCommentRepository(db),
                           SqliteVoteRepository(db), SqliteEventLogRepository(db), SqlitePeerProgressRepository(db))
