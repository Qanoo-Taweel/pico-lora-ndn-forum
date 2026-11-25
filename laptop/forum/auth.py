"""Accounts and sign-in. Accounts are local to each laptop; only the public
name "nickname@node" travels over LoRa (passwords never leave the laptop)."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from .models import User
from .repository import UserRepository

NICK_RE = re.compile(r"^[A-Za-z0-9_\-]{3,20}$")


class AuthError(ValueError):
    pass


class PasswordHasher:
    """Salted PBKDF2-SHA256, so stolen databases don't reveal passwords."""

    ROUNDS = 120_000

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        return salt.hex() + ":" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt, self.ROUNDS).hex()

    def verify(self, password: str, stored: str) -> bool:
        try:
            salt, digest = stored.split(":")
            calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), self.ROUNDS).hex()
            return hmac.compare_digest(calc, digest)
        except ValueError:
            return False


class TokenSigner:
    """Signed, expiring login tokens (no server-side session table needed)."""

    def __init__(self, secret: bytes, lifetime_s: int = 7 * 86400):
        self._secret, self._lifetime = secret, lifetime_s

    def issue(self, nickname: str) -> str:
        body = "%s:%d" % (nickname, int(time.time()) + self._lifetime)
        return body + ":" + hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()

    def read(self, token: str) -> str | None:
        try:
            body, sig = token.rsplit(":", 1)
            nickname, exp = body.split(":")
        except ValueError:
            return None
        good = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good) or int(exp) < time.time():
            return None
        return nickname


class AuthService:
    def __init__(self, users: UserRepository, node_name: str, secret: bytes,
                 hasher: PasswordHasher | None = None):
        self._users = users
        self._node = node_name
        self._hasher = hasher or PasswordHasher()
        self._tokens = TokenSigner(secret)

    def register(self, nickname: str, password: str) -> tuple[str, User]:
        if not NICK_RE.match(nickname or ""):
            raise AuthError("Nickname: 3-20 letters, digits, _ or -.")
        if len(password or "") < 6:
            raise AuthError("Password needs at least 6 characters.")
        if self._users.by_nickname(nickname):
            raise AuthError("That nickname is taken on this laptop.")
        user = self._users.add(nickname, self._hasher.hash(password))
        return self._tokens.issue(nickname), user

    def login(self, nickname: str, password: str) -> tuple[str, User]:
        user = self._users.by_nickname(nickname or "")
        if not user or not self._hasher.verify(password or "", user.password_hash):
            raise AuthError("Wrong nickname or password.")
        return self._tokens.issue(nickname), user

    def user_from_token(self, token: str | None) -> User | None:
        nickname = self._tokens.read(token or "")
        return self._users.by_nickname(nickname) if nickname else None

    def public_name(self, user: User) -> str:
        return "%s@%s" % (user.nickname, self._node)
