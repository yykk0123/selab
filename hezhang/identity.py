"""Salted password authentication and expiring session storage."""

import hashlib
import hmac
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

from .common import AccessDenied, DomainError, identifier, now, text


class IdentityService:
    """Store PBKDF2 hashes and only a digest of each bearer session token."""

    def __init__(self, repository):
        self.repo = repository

    @staticmethod
    def _derive(password, salt):
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), 260000
        ).hex()

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(self, username, password, email=""):
        """Persist a user without storing their plaintext password."""
        username = text(username, "用户名", 60)
        if not isinstance(email, str) or len(email) > 254:
            raise DomainError("邮箱过长或类型不合法")
        salt = secrets.token_hex(16)
        digest = self._derive(password, salt)
        user_id = identifier()
        try:
            with self.repo.transaction():
                self.repo.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, username, email, salt, digest, now()),
                )
        except sqlite3.IntegrityError as exc:
            raise DomainError("用户名已存在") from exc
        return user_id

    def login(self, username, password):
        """Issue a 12-hour session after checking the password in constant time."""
        if not isinstance(password, str) or len(password) > 1024:
            raise AccessDenied("用户名或密码错误")
        rows = self.repo.all("SELECT * FROM users WHERE username = ?", (username,))
        salt = rows[0]["salt"] if rows else "00" * 16
        candidate = self._derive(password, salt)
        expected = rows[0]["password_hash"] if rows else "00" * 32
        if not hmac.compare_digest(candidate, expected) or not rows:
            raise AccessDenied("用户名或密码错误")
        token = secrets.token_urlsafe(32)
        expiry = datetime.now(UTC) + timedelta(hours=12)
        with self.repo.transaction():
            self.repo.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (self._token_hash(token), rows[0]["id"], expiry.isoformat()),
            )
        return token

    def authenticate(self, token):
        """Resolve a session; current membership is checked again by services."""
        if not isinstance(token, str) or not token:
            raise AccessDenied("请先登录")
        rows = self.repo.all(
            "SELECT user_id FROM sessions WHERE token_hash = ? AND expires_at > ?",
            (self._token_hash(token), now()),
        )
        if not rows:
            raise AccessDenied("会话无效或已过期")
        return rows[0]["user_id"]

    def logout(self, token):
        """Revoke this bearer token immediately."""
        with self.repo.transaction():
            self.repo.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),)
            )
