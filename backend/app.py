"""HTTP API for employee reimbursement claims."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Path as PathParam, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from starlette.middleware.sessions import SessionMiddleware

from db import Database


MAX_AMOUNT_PAISE = 10_000_000_000
MOCK_SESSION_SECRET = "napho-mock-development-only-session-secret"
MOCK_ACCOUNTS = {
    "employee": ("employee123", 1, "Employee One", "EMPLOYEE"),
    "approver": ("approver123", 3, "Approver", "APPROVER"),
}


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(APIModel):
    username: StrictStr = Field(min_length=1, max_length=100)
    password: StrictStr = Field(min_length=1, max_length=100)


class ClaimFields(APIModel):
    title: StrictStr = Field(max_length=120)
    description: StrictStr = Field(max_length=2000)
    category: StrictStr = Field(max_length=80)
    expense_date: date
    amount_paise: StrictInt = Field(gt=0, le=MAX_AMOUNT_PAISE)

    @field_validator("title", "description", "category")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("expense_date")
    @classmethod
    def expense_date_is_not_future(cls, value: date) -> date:
        if value > datetime.now(timezone.utc).date():
            raise ValueError("expense_date must not be in the future")
        return value


class ClaimCreate(ClaimFields):
    pass


class ClaimUpdate(ClaimFields):
    version: StrictInt = Field(gt=0)


class VersionBody(APIModel):
    version: StrictInt = Field(gt=0)


class ReviewBody(VersionBody):
    decision: Literal["APPROVED", "REJECTED"]
    approved_amount_paise: StrictInt | None = Field(default=None, gt=0, le=MAX_AMOUNT_PAISE)
    comment: StrictStr | None = Field(default=None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def trim_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def claim_values(model: ClaimFields) -> dict[str, Any]:
    return {
        "title": model.title,
        "description": model.description,
        "category": model.category,
        "expense_date": model.expense_date.isoformat(),
        "amount_paise": model.amount_paise,
    }


def claim_response(row: sqlite3.Row) -> dict[str, Any]:
    fields = (
        "id",
        "owner_id",
        "owner_name",
        "title",
        "description",
        "category",
        "expense_date",
        "amount_paise",
        "approved_amount_paise",
        "status",
        "created_at",
        "updated_at",
        "version",
    )
    return {**{field: row[field] for field in fields}, "has_review_history": bool(row["has_review_history"])}


def fetch_claim(connection: sqlite3.Connection, claim_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT c.*, u.name AS owner_name,
                  EXISTS(SELECT 1 FROM claim_events e WHERE e.claim_id = c.id
                         AND e.action IN ('APPROVED', 'REJECTED')) AS has_review_history
           FROM claims c JOIN users u ON u.id = c.owner_id
           WHERE c.id = ?""",
        (claim_id,),
    ).fetchone()


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
               comment, event_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        ),
    )


def event_response(connection: sqlite3.Connection, claim_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT e.id, e.action, e.owner_id, owner.name AS owner_name,
                  e.actor_id, actor.name AS actor_name, actor.role AS actor_role,
                  e.title, e.description, e.category, e.expense_date,
                  e.amount_paise, e.approved_amount_paise, e.status, e.version,
                  e.comment, e.event_at
           FROM claim_events e
           JOIN users actor ON actor.id = e.actor_id
           JOIN users owner ON owner.id = e.owner_id
           WHERE e.claim_id = ? ORDER BY e.id""",
        (claim_id,),
    ).fetchall()
    return [dict(row) for row in rows]


@contextmanager
def write_transaction(database: Database) -> Iterator[sqlite3.Connection]:
    connection = database.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except sqlite3.OperationalError as error:
        if connection.in_transaction:
            connection.rollback()
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            raise HTTPException(
                status_code=409,
                detail="Database is busy; retry the request",
            ) from error
        raise
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def current_user(request: Request) -> dict[str, Any]:
    user_id = request.session.get("user_id")
    expected = next((account for account in MOCK_ACCOUNTS.values() if type(user_id) is int and account[1] == user_id), None)
    if expected is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    connection = request.app.state.database.connect()
    try:
        row = connection.execute(
            "SELECT id, name, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None or (row["name"], row["role"]) != (expected[2], expected[3]):
            raise HTTPException(status_code=401, detail="Authentication required")
        return dict(row)
    finally:
        connection.close()


def require_owner_or_approver_view(user: dict[str, Any], claim: sqlite3.Row) -> None:
    if user["role"] == "EMPLOYEE" and claim["owner_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Claim not found")
    if user["role"] == "APPROVER" and claim["status"] == "DRAFT" and not claim["has_review_history"]:
        raise HTTPException(status_code=404, detail="Claim not found")


def create_app(db_path: str | Path | None = None) -> FastAPI:
    configured_path = db_path if db_path is not None else os.environ.get("NAPHO_DB_PATH")
    database = Database(configured_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        yield

    application = FastAPI(lifespan=lifespan)

    @application.middleware("http")
    async def clear_login_session(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/auth/login":
            request.session.clear()
        return await call_next(request)

    application.add_middleware(
        SessionMiddleware, secret_key=os.environ.get("NAPHO_SESSION_SECRET", MOCK_SESSION_SECRET),
        session_cookie="napho_session", max_age=28800, same_site="lax", https_only=False,
    )
    application.state.database = database

    @application.post("/api/auth/login")
    def login(body: LoginBody, request: Request) -> dict[str, Any]:
        request.session.clear()
        account = MOCK_ACCOUNTS.get(body.username)
        if account is None or not secrets.compare_digest(body.password.encode(), account[0].encode()):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        request.session["user_id"] = account[1]
        try:
            return current_user(request)
        except HTTPException:
            request.session.clear()
            raise

    @application.get("/api/auth/me")
    def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return user

    @application.post("/api/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        request.session.clear()
        return Response(status_code=204)

    @application.get("/api/claims")
    def list_claims(
        request: Request, user: dict[str, Any] = Depends(current_user)
    ) -> list[dict[str, Any]]:
        connection = request.app.state.database.connect()
        try:
            if user["role"] == "EMPLOYEE":
                rows = connection.execute(
                    """SELECT c.*, u.name AS owner_name,
                              EXISTS(SELECT 1 FROM claim_events e WHERE e.claim_id = c.id
                                     AND e.action IN ('APPROVED', 'REJECTED')) AS has_review_history FROM claims c
                       JOIN users u ON u.id = c.owner_id
                       WHERE c.owner_id = ?
                       ORDER BY c.created_at DESC, c.id DESC""",
                    (user["id"],),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT c.*, u.name AS owner_name,
                              EXISTS(SELECT 1 FROM claim_events e WHERE e.claim_id = c.id
                                     AND e.action IN ('APPROVED', 'REJECTED')) AS has_review_history FROM claims c
                       JOIN users u ON u.id = c.owner_id
                       WHERE c.status <> 'DRAFT' OR EXISTS (
                           SELECT 1 FROM claim_events e WHERE e.claim_id = c.id
                           AND e.action IN ('APPROVED', 'REJECTED'))
                       ORDER BY c.created_at DESC, c.id DESC"""
                ).fetchall()
            return [claim_response(row) for row in rows]
        finally:
            connection.close()

    @application.post("/api/claims", status_code=201, response_model=None)
    def create_claim_route(
        body: ClaimCreate,
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ) -> dict[str, Any] | JSONResponse:
        if user["role"] != "EMPLOYEE":
            raise HTTPException(status_code=403, detail="Only employees can create claims")
        values = claim_values(body)
        canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            existing = connection.execute(
                "SELECT id, creation_payload FROM claims WHERE owner_id = ? AND idempotency_key = ?",
                (user["id"], str(idempotency_key)),
            ).fetchone()
            if existing is not None:
                if existing["creation_payload"] != canonical:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key was already used with a different payload",
                    )
                row = fetch_claim(connection, existing["id"])
                return JSONResponse(status_code=200, content=claim_response(row))

            now = utc_now()
            cursor = connection.execute(
                """INSERT INTO claims (
                       owner_id, title, description, category, expense_date, amount_paise,
                       approved_amount_paise, status, created_at, updated_at, version,
                       idempotency_key, creation_payload
                   ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'DRAFT', ?, ?, 1, ?, ?)""",
                (
                    user["id"],
                    values["title"],
                    values["description"],
                    values["category"],
                    values["expense_date"],
                    values["amount_paise"],
                    now,
                    now,
                    str(idempotency_key),
                    canonical,
                ),
            )
            row = fetch_claim(connection, cursor.lastrowid)
            append_event(connection, row, user["id"], "CREATED", None, now)
            return claim_response(row)

    @application.get("/api/claims/{claim_id}")
    def get_claim(
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        connection = request.app.state.database.connect()
        try:
            row = fetch_claim(connection, claim_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Claim not found")
            require_owner_or_approver_view(user, row)
            result = claim_response(row)
            result["history"] = event_response(connection, claim_id)
            return result
        finally:
            connection.close()

    @application.patch("/api/claims/{claim_id}")
    def edit_claim(
        body: ClaimUpdate,
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        if user["role"] != "EMPLOYEE":
            raise HTTPException(status_code=403, detail="Only employees can edit claims")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = fetch_claim(connection, claim_id)
            if row is None or row["owner_id"] != user["id"]:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["version"] != body.version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if row["status"] not in ("DRAFT", "REJECTED"):
                raise HTTPException(status_code=409, detail="Claim cannot be edited in its current status")
            values = claim_values(body)
            next_status = "DRAFT"
            now = utc_now()
            updated = connection.execute(
                """UPDATE claims SET title = ?, description = ?, category = ?, expense_date = ?,
                       amount_paise = ?, approved_amount_paise = NULL, status = ?,
                       updated_at = ?, version = version + 1
                   WHERE id = ? AND version = ?""",
                (
                    values["title"],
                    values["description"],
                    values["category"],
                    values["expense_date"],
                    values["amount_paise"],
                    next_status,
                    now,
                    claim_id,
                    body.version,
                ),
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            row = fetch_claim(connection, claim_id)
            append_event(connection, row, user["id"], "EDITED", None, now)
            return claim_response(row)

    @application.delete("/api/claims/{claim_id}", status_code=204)
    def delete_claim(
        request: Request,
        claim_id: int = PathParam(gt=0),
        version: int = Query(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> Response:
        if user["role"] != "EMPLOYEE":
            raise HTTPException(status_code=403, detail="Only employees can delete claims")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = fetch_claim(connection, claim_id)
            if row is None or row["owner_id"] != user["id"]:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["version"] != version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if row["status"] != "DRAFT":
                raise HTTPException(status_code=409, detail="Only drafts can be deleted")
            if connection.execute(
                "SELECT 1 FROM claim_events WHERE claim_id = ? AND action = 'SUBMITTED' LIMIT 1",
                (claim_id,),
            ).fetchone():
                raise HTTPException(status_code=409, detail="Only never-submitted drafts can be deleted")
            deleted = connection.execute(
                "DELETE FROM claims WHERE id = ? AND version = ?", (claim_id, version)
            )
            if deleted.rowcount != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
        return Response(status_code=204)

    @application.post("/api/claims/{claim_id}/submit")
    def submit_claim(
        body: VersionBody,
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        if user["role"] != "EMPLOYEE":
            raise HTTPException(status_code=403, detail="Only employees can submit claims")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = fetch_claim(connection, claim_id)
            if row is None or row["owner_id"] != user["id"]:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["version"] != body.version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if row["status"] not in ("DRAFT", "REJECTED"):
                raise HTTPException(status_code=409, detail="Claim cannot be submitted in its current status")
            now = utc_now()
            updated = connection.execute(
                """UPDATE claims SET status = 'SUBMITTED', approved_amount_paise = NULL,
                       updated_at = ?, version = version + 1
                   WHERE id = ? AND version = ?""",
                (now, claim_id, body.version),
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            row = fetch_claim(connection, claim_id)
            append_event(connection, row, user["id"], "SUBMITTED", None, now)
            return claim_response(row)

    @application.post("/api/claims/{claim_id}/review")
    def review_claim(
        body: ReviewBody,
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        if user["role"] != "APPROVER":
            raise HTTPException(status_code=403, detail="Only approvers can review claims")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = fetch_claim(connection, claim_id)
            if row is None or (row["status"] == "DRAFT" and not row["has_review_history"]):
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["version"] != body.version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if row["status"] != "SUBMITTED":
                raise HTTPException(status_code=409, detail="Only submitted claims can be reviewed")

            if body.decision == "APPROVED":
                approved_amount = body.approved_amount_paise
                if approved_amount is None or not 1 <= approved_amount <= row["amount_paise"]:
                    raise HTTPException(
                        status_code=422,
                        detail="approved_amount_paise must be between 1 and the requested amount",
                    )
                if approved_amount < row["amount_paise"] and body.comment is None:
                    raise HTTPException(status_code=422, detail="A comment is required for partial approval")
                next_status = "APPROVED"
            else:
                if body.approved_amount_paise is not None:
                    raise HTTPException(status_code=422, detail="Rejected claims cannot have an approved amount")
                if body.comment is None:
                    raise HTTPException(status_code=422, detail="A comment is required for rejection")
                approved_amount = None
                next_status = "REJECTED"

            now = utc_now()
            updated = connection.execute(
                """UPDATE claims SET status = ?, approved_amount_paise = ?, updated_at = ?,
                       version = version + 1
                   WHERE id = ? AND version = ?""",
                (next_status, approved_amount, now, claim_id, body.version),
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            row = fetch_claim(connection, claim_id)
            append_event(connection, row, user["id"], next_status, body.comment, now)
            return claim_response(fetch_claim(connection, claim_id))

    @application.post("/api/claims/{claim_id}/pay")
    def pay_claim(
        body: VersionBody,
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        if user["role"] != "APPROVER":
            raise HTTPException(status_code=403, detail="Only approvers can mark claims paid")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = fetch_claim(connection, claim_id)
            if row is None or (row["status"] == "DRAFT" and not row["has_review_history"]):
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["version"] != body.version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if row["status"] != "APPROVED":
                raise HTTPException(status_code=409, detail="Only approved claims can be paid")
            now = utc_now()
            updated = connection.execute(
                """UPDATE claims SET status = 'PAID', updated_at = ?, version = version + 1
                   WHERE id = ? AND version = ?""",
                (now, claim_id, body.version),
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            row = fetch_claim(connection, claim_id)
            append_event(connection, row, user["id"], "PAID", None, now)
            return claim_response(row)

    return application


app = create_app()
