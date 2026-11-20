"""Consistent SQLite backups and verified offline restoration.

This module supplies local backup primitives. Cloud transport and a daily
scheduler must be configured by the operator; no cloud SLA is claimed here.
"""

import sqlite3
from pathlib import Path

from .common import DomainError


class BackupService:
    """Administrative filesystem tool, deliberately outside member operations."""

    @staticmethod
    def verify(path):
        """Check database structure, referential integrity and expected schema."""
        path = Path(path).resolve()
        if not path.is_file():
            raise DomainError("备份文件不存在")
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            checks = connection.execute("PRAGMA integrity_check").fetchall()
            if checks != [("ok",)]:
                raise DomainError("备份完整性校验失败")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise DomainError("备份外键校验失败")
            required = {"users", "expenses", "debts", "payments", "logs", "requests"}
            actual = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if not required <= actual:
                raise DomainError("备份不包含合账数据结构")
            return {
                name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in sorted(required)
            }
        finally:
            connection.close()

    @classmethod
    def create(cls, repository, destination):
        """Use SQLite online backup, including transactions still held in WAL."""
        destination = Path(destination).resolve()
        if destination.exists():
            raise DomainError("备份目标已存在，请指定新的文件名")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents silently overwriting a preexisting file.
        with destination.open("xb"):
            pass
        target = sqlite3.connect(destination)
        try:
            repository.connection.backup(target)
        finally:
            target.close()
        return cls.verify(destination)

    @classmethod
    def restore(cls, source, destination):
        """Restore into a new database; never overwrite a running database."""
        expected = cls.verify(source)
        source_path = Path(source).resolve()
        destination = Path(destination).resolve()
        if destination.exists():
            raise DomainError("恢复目标已存在，禁止覆盖")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb"):
            pass
        original = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
        restored = sqlite3.connect(destination)
        try:
            original.backup(restored)
        finally:
            restored.close()
            original.close()
        actual = cls.verify(destination)
        if expected != actual:
            raise DomainError("恢复后记录数量与备份不一致")
        return actual
