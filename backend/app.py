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

import store
from db import Database
from policy import allowed_actions, can_view, permissions_for


MAX_AMOUNT_PAISE = 10_000_000_000
MOCK_SESSION_SECRET = "napho-mock-development-only-session-secret"
MOCK_ACCOUNTS = {
    "employee": ("employee123", 1, "Employee One", "EMPLOYEE"),
    "manager": ("manager123", 3, "Manager One", "APPROVER"),
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


def claim_response(row: sqlite3.Row, user: dict[str, Any]) -> dict[str, Any]:
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
    return {**{field: row[field] for field in fields}, "has_review_history": bool(row["has_review_history"]),
            "allowed_actions": allowed_actions(user, row)}


@contextmanager
def write_transaction(database: Database) -> Iterator[sqlite3.Connection]:
    try:
        with database.transaction() as connection:
            yield connection
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            raise HTTPException(
                status_code=409,
                detail="Database is busy; retry the request",
            ) from error
        raise


def current_user(request: Request) -> dict[str, Any]:
    user_id = request.session.get("user_id")
    expected = next((account for account in MOCK_ACCOUNTS.values() if type(user_id) is int and account[1] == user_id), None)
    if expected is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    connection = request.app.state.database.connect()
    try:
        row = store.fetch_user(connection, user_id)
        if row is None or (row["name"], row["role"]) != (expected[2], expected[3]):
            raise HTTPException(status_code=401, detail="Authentication required")
        return {**dict(row), "permissions": permissions_for(row["role"])}
    finally:
        connection.close()


def require_owner_or_approver_view(user: dict[str, Any], claim: sqlite3.Row) -> None:
    if not can_view(user, claim):
        raise HTTPException(status_code=404, detail="Claim not found")


def require_permission(user: dict[str, Any], permission: str) -> None:
    if permission not in user["permissions"]:
        raise HTTPException(status_code=403, detail="Permission denied")


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
            rows = store.list_claims(connection, user["id"])
            return [claim_response(row, user) for row in rows]
        finally:
            connection.close()

    @application.get("/api/approvals")
    def list_approvals(request: Request, user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
        require_permission(user, "review_claims")
        connection = request.app.state.database.connect()
        try:
            rows = store.list_approvals(connection, user["id"])
            return [claim_response(row, user) for row in rows]
        finally:
            connection.close()

    @application.post("/api/claims", status_code=201, response_model=None)
    def create_claim_route(
        body: ClaimCreate,
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        idempotency_key: UUID = Header(alias="Idempotency-Key"),
    ) -> dict[str, Any] | JSONResponse:
        require_permission(user, "create_claims")
        values = claim_values(body)
        canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            existing = store.find_creation(connection, user["id"], str(idempotency_key))
            if existing is not None:
                if existing["creation_payload"] != canonical:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key was already used with a different payload",
                    )
                row = store.fetch_claim(connection, existing["id"])
                return JSONResponse(status_code=200, content=claim_response(row, user))

            now = utc_now()
            claim_id = store.create_claim(connection, user["id"], values, now, str(idempotency_key), canonical)
            row = store.fetch_claim(connection, claim_id)
            store.append_event(connection, row, user["id"], "CREATED", None, now)
            return claim_response(row, user)

    @application.get("/api/claims/{claim_id}")
    def get_claim(
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        connection = request.app.state.database.connect()
        try:
            row = store.fetch_claim(connection, claim_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Claim not found")
            require_owner_or_approver_view(user, row)
            result = claim_response(row, user)
            result["history"] = [dict(event) for event in store.fetch_events(connection, claim_id)]
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
        require_permission(user, "create_claims")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = store.fetch_claim(connection, claim_id)
            if row is None or row["owner_id"] != user["id"]:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["version"] != body.version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if "edit" not in allowed_actions(user, row):
                raise HTTPException(status_code=409, detail="Claim cannot be edited in its current status")
            values = claim_values(body)
            next_status = "DRAFT"
            now = utc_now()
            updated = store.edit_claim(connection, claim_id, body.version, values, next_status, now)
            if updated != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            row = store.fetch_claim(connection, claim_id)
            store.append_event(connection, row, user["id"], "EDITED", None, now)
            return claim_response(row, user)

    @application.delete("/api/claims/{claim_id}", status_code=204)
    def delete_claim(
        request: Request,
        claim_id: int = PathParam(gt=0),
        version: int = Query(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> Response:
        require_permission(user, "create_claims")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = store.fetch_claim(connection, claim_id)
            if row is None or row["owner_id"] != user["id"]:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["version"] != version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if "delete" not in allowed_actions(user, row):
                raise HTTPException(status_code=409, detail="Only never-submitted drafts can be deleted")
            deleted = store.delete_claim(connection, claim_id, version)
            if deleted != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
        return Response(status_code=204)

    @application.post("/api/claims/{claim_id}/submit")
    def submit_claim(
        body: VersionBody,
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        require_permission(user, "create_claims")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = store.fetch_claim(connection, claim_id)
            if row is None or row["owner_id"] != user["id"]:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["version"] != body.version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if "submit" not in allowed_actions(user, row):
                raise HTTPException(status_code=409, detail="Claim cannot be submitted in its current status")
            now = utc_now()
            updated = store.submit_claim(connection, claim_id, body.version, now)
            if updated != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            row = store.fetch_claim(connection, claim_id)
            store.append_event(connection, row, user["id"], "SUBMITTED", None, now)
            return claim_response(row, user)

    @application.post("/api/claims/{claim_id}/review")
    def review_claim(
        body: ReviewBody,
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        require_permission(user, "review_claims")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = store.fetch_claim(connection, claim_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["owner_id"] == user["id"]:
                raise HTTPException(status_code=403, detail="Cannot review your own claim")
            require_owner_or_approver_view(user, row)
            if row["version"] != body.version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if "review" not in allowed_actions(user, row):
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
            updated = store.review_claim(connection, claim_id, body.version, next_status, approved_amount, now)
            if updated != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            row = store.fetch_claim(connection, claim_id)
            store.append_event(connection, row, user["id"], next_status, body.comment, now)
            return claim_response(store.fetch_claim(connection, claim_id), user)

    @application.post("/api/claims/{claim_id}/pay")
    def pay_claim(
        body: VersionBody,
        request: Request,
        claim_id: int = PathParam(gt=0),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        require_permission(user, "mark_paid")
        database: Database = request.app.state.database
        with write_transaction(database) as connection:
            row = store.fetch_claim(connection, claim_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Claim not found")
            if row["owner_id"] == user["id"]:
                raise HTTPException(status_code=403, detail="Cannot pay your own claim")
            require_owner_or_approver_view(user, row)
            if row["version"] != body.version:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            if "pay" not in allowed_actions(user, row):
                raise HTTPException(status_code=409, detail="Only approved claims can be paid")
            now = utc_now()
            updated = store.pay_claim(connection, claim_id, body.version, now)
            if updated != 1:
                raise HTTPException(status_code=409, detail="Claim version is stale")
            row = store.fetch_claim(connection, claim_id)
            store.append_event(connection, row, user["id"], "PAID", None, now)
            return claim_response(row, user)

    return application


app = create_app()
