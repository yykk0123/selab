"""SQLite repository, schema and serialized business transactions."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .common import Conflict, DomainError, encode, identifier, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledgers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('OPEN', 'CLOSED')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    ledger_id TEXT NOT NULL REFERENCES ledgers(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL CHECK(role IN ('ADMIN', 'MEMBER', 'READER')),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'LEFT', 'REMOVED')),
    joined_at TEXT NOT NULL,
    PRIMARY KEY (ledger_id, user_id)
);
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    ledger_id TEXT NOT NULL REFERENCES ledgers(id),
    name TEXT NOT NULL,
    budget INTEGER CHECK(budget >= 0),
    threshold INTEGER CHECK(threshold > 0),
    UNIQUE (ledger_id, name)
);
CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    ledger_id TEXT NOT NULL REFERENCES ledgers(id),
    submitter TEXT NOT NULL REFERENCES users(id),
    category_id TEXT NOT NULL REFERENCES categories(id),
    amount INTEGER NOT NULL CHECK(amount > 0),
    kind TEXT NOT NULL CHECK(kind IN ('EXPENSE', 'INCOME')),
    title TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    reasons TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    parent_id TEXT REFERENCES expenses(id),
    adjustment_id TEXT
);
CREATE TABLE IF NOT EXISTS contributions (
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL CHECK(amount >= 0),
    PRIMARY KEY (expense_id, user_id)
);
CREATE TABLE IF NOT EXISTS shares (
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL CHECK(amount >= 0),
    PRIMARY KEY (expense_id, user_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    reviewer TEXT REFERENCES users(id),
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS debts (
    id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    debtor TEXT NOT NULL REFERENCES users(id),
    creditor TEXT NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL CHECK(amount > 0),
    paid INTEGER NOT NULL DEFAULT 0 CHECK(paid >= 0 AND paid <= amount),
    active INTEGER NOT NULL DEFAULT 1,
    adjustment_id TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    debt_id TEXT NOT NULL REFERENCES debts(id),
    payer TEXT NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL CHECK(amount > 0),
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'CONFIRMED', 'REJECTED')),
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS adjustments (
    id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    requester TEXT NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL,
    replacement TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
    id TEXT PRIMARY KEY,
    ledger_id TEXT NOT NULL REFERENCES ledgers(id),
    actor TEXT NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    object_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    actor TEXT NOT NULL REFERENCES users(id),
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload TEXT NOT NULL,
    result_id TEXT NOT NULL,
    PRIMARY KEY (actor, request_id)
);
CREATE INDEX IF NOT EXISTS expense_query
    ON expenses(ledger_id, category_id, occurred_at);
CREATE INDEX IF NOT EXISTS debt_expense ON debts(expense_id, active);
CREATE INDEX IF NOT EXISTS payment_debt ON payments(debt_id, status);
CREATE INDEX IF NOT EXISTS log_query ON logs(ledger_id, created_at);
"""


class Repository:
    """Own one connection; use a separate instance in each worker thread."""

    def __init__(self, path="hezhang.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript(SCHEMA)

    def close(self):
        """Release the database connection."""
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    @contextmanager
    def transaction(self):
        """Lock before reading budgets or statuses; roll back all failed writes."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def execute(self, sql, parameters=()):
        """Execute parameterized SQL within the caller's transaction."""
        return self.connection.execute(sql, parameters)

    def one(self, sql, parameters=()):
        """Fetch one row, with a domain error for absent objects."""
        row = self.execute(sql, parameters).fetchone()
        if row is None:
            raise DomainError("记录不存在")
        return dict(row)

    def all(self, sql, parameters=()):
        """Return plain dictionaries suitable for JSON presentation."""
        return [dict(row) for row in self.execute(sql, parameters).fetchall()]

    def log(self, ledger_id, actor, action, object_id, reason=""):
        """Append an audit entry in the same transaction as its operation."""
        self.execute(
            "INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (identifier(), ledger_id, actor, action, object_id, reason, now()),
        )

    def replay(self, actor, request_id, operation, payload):
        """Return an earlier result only if the complete request matches."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise DomainError("写操作必须提供非空 request_id")
        rows = self.all(
            "SELECT * FROM requests WHERE actor = ? AND request_id = ?",
            (actor, request_id),
        )
        if not rows:
            return None
        row = rows[0]
        if row["operation"] != operation or row["payload"] != encode(payload):
            raise Conflict("请求标识已用于不同操作或不同内容")
        return row["result_id"]

    def remember(self, actor, request_id, operation, payload, result_id):
        """Persist an idempotency result atomically with business changes."""
        self.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
            (actor, request_id, operation, encode(payload), result_id),
        )
