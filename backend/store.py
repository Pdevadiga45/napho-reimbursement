"""SQLite queries and persistence for reimbursement claims."""

import sqlite3
from typing import Any


RECEIPT_FIELDS = "r.id AS receipt_id, r.filename AS receipt_filename, r.content_type AS receipt_content_type, r.size AS receipt_size, r.sha256 AS receipt_sha256, r.created_at AS receipt_created_at"

CLAIM_PROJECTION = f"""SELECT c.*, u.name AS owner_name, {RECEIPT_FIELDS},
                  EXISTS(SELECT 1 FROM claim_events e WHERE e.claim_id = c.id
                         AND e.action IN ('APPROVED', 'REJECTED')) AS has_review_history,
                  EXISTS(SELECT 1 FROM claim_events e WHERE e.claim_id = c.id
                         AND e.action = 'SUBMITTED') AS has_submission_history
           FROM claims c JOIN users u ON u.id = c.owner_id
           LEFT JOIN claim_receipts r ON r.id = c.current_receipt_id AND r.claim_id = c.id"""


def fetch_user(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT id, name, role FROM users WHERE id = ?", (user_id,)).fetchone()


def fetch_claim(connection: sqlite3.Connection, claim_id: int) -> sqlite3.Row | None:
    return connection.execute(CLAIM_PROJECTION + " WHERE c.id = ?", (claim_id,)).fetchone()


def list_claims(connection: sqlite3.Connection, owner_id: int) -> list[sqlite3.Row]:
    return connection.execute(CLAIM_PROJECTION +
        " WHERE c.owner_id = ? ORDER BY c.created_at DESC, c.id DESC", (owner_id,)).fetchall()


def list_approvals(connection: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return connection.execute(CLAIM_PROJECTION + """
        WHERE c.owner_id <> ? AND (c.status <> 'DRAFT' OR EXISTS (
            SELECT 1 FROM claim_events e WHERE e.claim_id = c.id
            AND e.action IN ('APPROVED', 'REJECTED')))
        ORDER BY c.created_at DESC, c.id DESC""", (user_id,)).fetchall()


def find_creation(connection: sqlite3.Connection, owner_id: int, key: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT id, creation_payload FROM claims WHERE owner_id = ? AND idempotency_key = ?",
        (owner_id, key),
    ).fetchone()


def create_claim(connection: sqlite3.Connection, owner_id: int, values: dict[str, Any],
                 now: str, key: str, canonical: str) -> int:
    cursor = connection.execute(
        """INSERT INTO claims (
               owner_id, title, description, category, expense_date, amount_paise,
               approved_amount_paise, status, created_at, updated_at, version,
               idempotency_key, creation_payload, merchant
           ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'DRAFT', ?, ?, 1, ?, ?, ?)""",
        (owner_id, values["title"], values["description"], values["category"],
         values["expense_date"], values["amount_paise"], now, now, key, canonical, values["merchant"]),
    )
    return cursor.lastrowid


def edit_claim(connection: sqlite3.Connection, claim_id: int, version: int,
               values: dict[str, Any], next_status: str, now: str) -> int:
    return connection.execute(
        """UPDATE claims SET title = ?, description = ?, category = ?, expense_date = ?,
               amount_paise = ?, merchant = ?, approved_amount_paise = NULL, status = ?,
               updated_at = ?, version = version + 1
           WHERE id = ? AND version = ?""",
        (values["title"], values["description"], values["category"], values["expense_date"],
         values["amount_paise"], values["merchant"], next_status, now, claim_id, version),
    ).rowcount


def delete_claim(connection: sqlite3.Connection, claim_id: int, version: int) -> int:
    return connection.execute("DELETE FROM claims WHERE id = ? AND version = ?", (claim_id, version)).rowcount


def submit_claim(connection: sqlite3.Connection, claim_id: int, version: int, now: str) -> int:
    return connection.execute(
        """UPDATE claims SET status = 'SUBMITTED', approved_amount_paise = NULL,
               updated_at = ?, version = version + 1
           WHERE id = ? AND version = ?""", (now, claim_id, version),
    ).rowcount


def review_claim(connection: sqlite3.Connection, claim_id: int, version: int,
                 status: str, approved_amount: int | None, now: str) -> int:
    return connection.execute(
        """UPDATE claims SET status = ?, approved_amount_paise = ?, updated_at = ?,
               version = version + 1
           WHERE id = ? AND version = ?""", (status, approved_amount, now, claim_id, version),
    ).rowcount


def pay_claim(connection: sqlite3.Connection, claim_id: int, version: int, now: str) -> int:
    return connection.execute(
        """UPDATE claims SET status = 'PAID', updated_at = ?, version = version + 1
           WHERE id = ? AND version = ?""", (now, claim_id, version),
    ).rowcount


def append_event(
    connection: sqlite3.Connection,
    claim: sqlite3.Row,
    actor_id: int,
    action: str,
    comment: str | None,
    event_at: str,
) -> None:
    connection.execute(
        """INSERT INTO claim_events (
               claim_id, owner_id, actor_id, action, title, description, category,
               expense_date, amount_paise, approved_amount_paise, status, version,
               comment, event_at, merchant, receipt_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            claim["id"],
            claim["owner_id"],
            actor_id,
            action,
            claim["title"],
            claim["description"],
            claim["category"],
            claim["expense_date"],
            claim["amount_paise"],
            claim["approved_amount_paise"],
            claim["status"],
            claim["version"],
            comment,
            event_at,
            claim["merchant"],
            claim["current_receipt_id"],
        ),
    )


def fetch_events(connection: sqlite3.Connection, claim_id: int) -> list[sqlite3.Row]:
    rows = connection.execute(
        f"""SELECT e.id, e.action, e.owner_id, owner.name AS owner_name,
                  e.actor_id, actor.name AS actor_name, actor.role AS actor_role,
                  e.title, e.description, e.category, e.expense_date,
                  e.amount_paise, e.approved_amount_paise, e.status, e.version,
                  e.comment, e.event_at, e.merchant, {RECEIPT_FIELDS}
           FROM claim_events e
           JOIN users actor ON actor.id = e.actor_id
           JOIN users owner ON owner.id = e.owner_id
           LEFT JOIN claim_receipts r ON r.id = e.receipt_id AND r.claim_id = e.claim_id
           WHERE e.claim_id = ? ORDER BY e.id""",
        (claim_id,),
    ).fetchall()
    return rows


def insert_receipt(connection, claim_id, uploader_id, filename, content_type, content, sha256, now):
    return connection.execute(
        "INSERT INTO claim_receipts (claim_id, uploader_id, filename, content_type, size, sha256, created_at, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (claim_id, uploader_id, filename, content_type, len(content), sha256, now, content),
    ).lastrowid


def update_receipt(connection, claim_id, version, receipt_id, now):
    return connection.execute(
        "UPDATE claims SET current_receipt_id = ?, status = 'DRAFT', approved_amount_paise = NULL, updated_at = ?, version = version + 1 WHERE id = ? AND version = ?",
        (receipt_id, now, claim_id, version),
    ).rowcount


def fetch_receipt_content(connection, claim_id, receipt_id):
    return connection.execute("SELECT filename, content_type, content FROM claim_receipts WHERE claim_id = ? AND id = ?", (claim_id, receipt_id)).fetchone()
