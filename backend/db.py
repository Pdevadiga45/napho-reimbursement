"""SQLite storage helpers for the reimbursement API."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DB_PATH = Path(__file__).resolve().with_name("napho.sqlite3")

SEED_USERS = (
    (1, "Employee One", "EMPLOYEE"),
    (3, "Manager One", "APPROVER"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('EMPLOYEE', 'APPROVER'))
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    amount_paise INTEGER NOT NULL CHECK (amount_paise BETWEEN 1 AND 10000000000),
    approved_amount_paise INTEGER
        CHECK (approved_amount_paise IS NULL OR approved_amount_paise BETWEEN 1 AND amount_paise),
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'PAID')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    idempotency_key TEXT NOT NULL,
    creation_payload TEXT NOT NULL,
    UNIQUE (owner_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS claim_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    expense_date TEXT NOT NULL,
    amount_paise INTEGER NOT NULL,
    approved_amount_paise INTEGER,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    comment TEXT,
    event_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_owner_created ON claims(owner_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_claims_status_created ON claims(status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_claim ON claim_events(claim_id, id);
"""


class Database:
    """A path-bound database; construction never opens or mutates the file."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path if path is not None else DEFAULT_DB_PATH
        self.path = Path(configured).expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=2.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 2000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            connection.executescript(SCHEMA)
            connection.executemany(
                "INSERT OR IGNORE INTO users (id, name, role) VALUES (?, ?, ?)",
                SEED_USERS,
            )
            connection.execute(
                "UPDATE users SET name = 'Manager One' WHERE id = 3 AND name = 'Approver' AND role = 'APPROVER'"
            )
        finally:
            connection.close()
