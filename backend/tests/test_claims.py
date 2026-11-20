from datetime import date, timedelta
from uuid import uuid4

import pytest
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

from app import create_app
from db import Database


def test_auth_rejects_header_without_session(tmp_path):
    with TestClient(create_app(tmp_path / "auth.sqlite3")) as anonymous:
        assert anonymous.get("/api/claims").status_code == 401
        assert anonymous.get("/api/claims", headers={"X-User-Id": "99"}).status_code == 401
        assert anonymous.get("/api/claims", headers={"X-User-Id": "1"}).status_code == 401


def test_auth_login_session_and_invalid_password(tmp_path):
    with TestClient(create_app(tmp_path / "auth.sqlite3")) as browser:
        assert browser.get("/api/auth/me").status_code == 401
        login = browser.post("/api/auth/login", json={"username": "employee", "password": "employee123"})
        assert login.status_code == 200
        assert login.json() == {"id": 1, "name": "Employee One", "role": "EMPLOYEE", "permissions": ["create_claims"]}
        assert "napho_session" in browser.cookies
        cookie_header = login.headers["set-cookie"].lower()
        assert "httponly" in cookie_header and "samesite=lax" in cookie_header
        assert "max-age=28800" in cookie_header and "secure" not in cookie_header
        assert browser.get("/api/auth/me").json() == login.json()
        assert browser.post("/api/auth/login", json={"username": "employee", "password": "wrong"}).status_code == 401
        assert browser.get("/api/auth/me").status_code == 401


def test_legacy_manager_identity_migrates_without_changing_claim_or_events(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    application = create_app(path)
    with TestClient(application) as employee:
        assert employee.post("/api/auth/login", json={"username": "employee", "password": "employee123"}).status_code == 200
        claim = create_claim(employee).json()
        assert submit_claim(employee, claim["id"], claim["version"]).status_code == 200
    with TestClient(application) as manager:
        assert manager.post("/api/auth/login", json={"username": "manager", "password": "manager123"}).status_code == 200
        assert manager.post(f"/api/claims/{claim['id']}/review", json={"version": 2, "decision": "APPROVED", "approved_amount_paise": 12500}).status_code == 200
        manager_cookie = manager.cookies.get("napho_session")

    database = Database(path)
    connection = database.connect()
    try:
        connection.execute("UPDATE users SET name = 'Approver' WHERE id = 3 AND name = 'Manager One' AND role = 'APPROVER'")
        before_claim = tuple(connection.execute("SELECT * FROM claims WHERE id = ?", (claim["id"],)).fetchone())
        before_events = [tuple(row) for row in connection.execute(
            "SELECT * FROM claim_events WHERE claim_id = ? ORDER BY id", (claim["id"],)
        ).fetchall()]
    finally:
        connection.close()
    database.initialize()
    database.initialize()

    connection = database.connect()
    try:
        assert tuple(connection.execute("SELECT name, role FROM users WHERE id = 3").fetchone()) == ("Manager One", "APPROVER")
        assert tuple(connection.execute("SELECT * FROM claims WHERE id = ?", (claim["id"],)).fetchone()) == before_claim
        assert [tuple(row) for row in connection.execute(
            "SELECT * FROM claim_events WHERE claim_id = ? ORDER BY id", (claim["id"],)
        ).fetchall()] == before_events
    finally:
        connection.close()
    with TestClient(create_app(path)) as manager:
        manager.cookies.set("napho_session", manager_cookie)
        assert manager.get("/api/auth/me").json()["name"] == "Manager One"
        assert manager.post("/api/auth/login", json={"username": "manager", "password": "manager123"}).json()["name"] == "Manager One"
        assert manager.get(f"/api/claims/{claim['id']}").json()["history"][2]["actor_name"] == "Manager One"


def test_session_logout_tamper_validation_and_header_spoof(application, client, approver):
    cookie = client.cookies.get("napho_session")
    assert cookie
    assert client.get("/api/auth/me").json()["role"] == "EMPLOYEE"
    assert approver.get("/api/auth/me").json()["role"] == "APPROVER"
    claim = create_claim(client).json()
    assert submit_claim(client, claim["id"], 1).status_code == 200
    assert client.post(f"/api/claims/{claim['id']}/review", headers=headers(3), json={"version": 2, "decision": "APPROVED", "approved_amount_paise": 12500}).status_code == 403
    with TestClient(application) as anonymous:
        anonymous.cookies.set("napho_session", cookie + "tampered")
        assert anonymous.get("/api/auth/me").status_code == 401
        assert anonymous.get("/api/claims").status_code == 401
    assert client.post("/api/auth/login", json={"username": 12, "password": "x"}).status_code == 422
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "employee", "password": "employee123"}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/users").status_code == 404
    assert approver.get("/api/auth/me").json()["role"] == "APPROVER"


def test_session_revalidates_identity_against_database(application, client):
    connection = application.state.database.connect()
    try:
        connection.execute("UPDATE users SET role = 'APPROVER' WHERE id = 1")
    finally:
        connection.close()
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/claims").status_code == 401
    assert client.post("/api/auth/login", json={"username": "employee", "password": "employee123"}).status_code == 401


@pytest.fixture
def application(tmp_path):
    return create_app(tmp_path / "claims.sqlite3")


@pytest.fixture
def client(application):
    with TestClient(application) as browser:
        assert browser.post("/api/auth/login", json={"username": "employee", "password": "employee123"}).status_code == 200
        yield browser


@pytest.fixture
def approver(application):
    with TestClient(application) as browser:
        assert browser.post("/api/auth/login", json={"username": "manager", "password": "manager123"}).status_code == 200
        yield browser


def headers(user_id: int = 1) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def claim_payload(**overrides):
    return {
        "title": "Client visit taxi",
        "description": "Taxi from office to customer site",
        "category": "Travel",
        "expense_date": "2025-02-03",
        "amount_paise": 12500,
        **overrides,
    }


def create_claim(client: TestClient, payload=None, idempotency_key=None):
    return client.post(
        "/api/claims",
        headers={"Idempotency-Key": idempotency_key or str(uuid4())},
        json=payload or claim_payload(),
    )


def submit_claim(client: TestClient, claim_id: int, version: int):
    return client.post(
        f"/api/claims/{claim_id}/submit", json={"version": version}
    )


def review_claim(client: TestClient, claim_id: int, payload):
    return client.post(
        f"/api/claims/{claim_id}/review", json=payload
    )


def test_create_list_and_detail_return_owner_and_snapshot_history(client: TestClient, approver: TestClient) -> None:
    created = create_claim(client)

    assert created.status_code == 201
    claim = created.json()
    assert claim["owner_id"] == 1
    assert claim["owner_name"] == "Employee One"
    assert claim["status"] == "DRAFT"
    assert claim["version"] == 1
    assert claim["approved_amount_paise"] is None
    assert claim["created_at"].endswith("+00:00")
    assert claim["updated_at"].endswith("+00:00")

    own_list = client.get("/api/claims")
    assert [row["id"] for row in own_list.json()] == [claim["id"]]
    assert approver.get(f"/api/claims/{claim['id']}").status_code == 404

    detail = client.get(f"/api/claims/{claim['id']}")
    assert detail.status_code == 200
    assert [event["action"] for event in detail.json()["history"]] == ["CREATED"]
    event = detail.json()["history"][0]
    assert event["actor_name"] == "Employee One"
    assert event["title"] == "Client visit taxi"
    assert event["amount_paise"] == 12500


def test_create_idempotency_replays_once_and_rejects_changed_payload(client: TestClient, approver: TestClient) -> None:
    key = str(uuid4())
    request_headers = {"Idempotency-Key": key}
    payload = claim_payload()

    first = client.post("/api/claims", headers=request_headers, json=payload)
    replay = client.post("/api/claims", headers=request_headers, json=payload)
    changed = client.post(
        "/api/claims", headers=request_headers, json=claim_payload(title="Different expense")
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert changed.status_code == 409
    detail = client.get(f"/api/claims/{first.json()['id']}").json()
    assert [event["action"] for event in detail["history"]] == ["CREATED"]


@pytest.mark.parametrize(
    "payload",
    [
        claim_payload(amount_paise=0),
        claim_payload(amount_paise=-1),
        claim_payload(amount_paise=10_000_000_001),
        claim_payload(amount_paise=True),
        claim_payload(amount_paise=12.5),
        claim_payload(expense_date=(date.today() + timedelta(days=1)).isoformat()),
        claim_payload(title="   "),
        claim_payload(description=""),
        {"title": "title", "description": "only two fields"},
    ],
)
def test_create_rejects_invalid_claim_fields(client: TestClient, approver: TestClient, payload) -> None:
    assert create_claim(client, payload).status_code == 422


def test_partial_approval_and_payment_preserve_requested_amount(client: TestClient, approver: TestClient) -> None:
    created = create_claim(client, claim_payload(amount_paise=10000)).json()
    claim_id = created["id"]
    assert approver.patch(
        f"/api/claims/{claim_id}",
        json={**claim_payload(), "version": 1},
    ).status_code == 404
    submitted = submit_claim(client, claim_id, created["version"])
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUBMITTED"

    assert approver.get("/api/approvals").json()[0]["id"] == claim_id
    assert review_claim(
        client,
        claim_id,
        {"version": 2, "decision": "APPROVED", "approved_amount_paise": 7500, "comment": "Within policy"},
    ).status_code == 403
    approved = review_claim(
        approver,
        claim_id,
        {"version": 2, "decision": "APPROVED", "approved_amount_paise": 7500, "comment": "Within policy"},
    )
    assert approved.status_code == 200
    assert approved.json()["amount_paise"] == 10000
    assert approved.json()["approved_amount_paise"] == 7500
    assert approved.json()["version"] == 3
    assert client.post(
        f"/api/claims/{claim_id}/pay", json={"version": 3}
    ).status_code == 403

    paid = approver.post(
        f"/api/claims/{claim_id}/pay", json={"version": 3}
    )
    assert paid.status_code == 200
    assert (paid.json()["status"], paid.json()["version"]) == ("PAID", 4)
    assert approver.post(
        f"/api/claims/{claim_id}/pay", json={"version": 3}
    ).status_code == 409

    history = client.get(f"/api/claims/{claim_id}").json()["history"]
    assert [event["action"] for event in history] == ["CREATED", "SUBMITTED", "APPROVED", "PAID"]
    assert history[2]["actor_name"] == "Manager One"
    assert history[2]["comment"] == "Within policy"
    assert history[2]["amount_paise"] == 10000
    assert history[2]["approved_amount_paise"] == 7500


def test_rejected_claim_edit_and_resubmit_preserve_old_snapshots(client: TestClient, approver: TestClient) -> None:
    original = claim_payload(amount_paise=8000)
    key = str(uuid4())
    created = create_claim(client, original, idempotency_key=key).json()
    claim_id = created["id"]
    assert submit_claim(client, claim_id, 1).status_code == 200
    rejected = review_claim(
        approver, claim_id, {"version": 2, "decision": "REJECTED", "comment": "Need receipt detail"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["has_review_history"] is True

    edited = client.patch(
        f"/api/claims/{claim_id}",
        json={**claim_payload(title="Updated taxi", amount_paise=9000), "version": 3},
    )
    assert edited.status_code == 200
    assert (edited.json()["status"], edited.json()["version"]) == ("DRAFT", 4)
    assert edited.json()["approved_amount_paise"] is None
    assert submit_claim(client, claim_id, 4).json()["status"] == "SUBMITTED"

    history = client.get(f"/api/claims/{claim_id}").json()["history"]
    assert [event["action"] for event in history] == [
        "CREATED", "SUBMITTED", "REJECTED", "EDITED", "SUBMITTED"
    ]
    assert (history[2]["title"], history[2]["amount_paise"], history[2]["comment"]) == (
        "Client visit taxi",
        8000,
        "Need receipt detail",
    )
    assert (history[3]["title"], history[3]["amount_paise"], history[3]["status"]) == (
        "Updated taxi",
        9000,
        "DRAFT",
    )
    assert approver.get("/api/approvals").json()[0]["status"] == "SUBMITTED"
    replay = create_claim(client, original, idempotency_key=key)
    assert replay.status_code == 200
    assert replay.json()["id"] == claim_id
    assert replay.json()["status"] == "SUBMITTED"
    assert len(client.get(f"/api/claims/{claim_id}").json()["history"]) == 5


def corrected_claim(client, approver):
    created = create_claim(client, claim_payload(amount_paise=8000)).json()
    claim_id = created["id"]
    assert submit_claim(client, claim_id, 1).status_code == 200
    assert review_claim(approver, claim_id, {"version": 2, "decision": "REJECTED", "comment": "Check receipt"}).status_code == 200
    edited = client.patch(f"/api/claims/{claim_id}", json={**claim_payload(title="Corrected taxi", amount_paise=9000), "version": 3})
    assert edited.status_code == 200
    assert (edited.json()["status"], edited.json()["version"]) == ("DRAFT", 4)
    return claim_id


def test_corrected_draft_cannot_be_deleted_and_preserves_history(client: TestClient, approver: TestClient) -> None:
    claim_id = corrected_claim(client, approver)
    before = client.get(f"/api/claims/{claim_id}").json()
    claims_before = client.get("/api/claims").json()
    assert client.delete(f"/api/claims/{claim_id}?version=4").status_code == 409
    assert client.get(f"/api/claims/{claim_id}").json() == before
    assert client.get("/api/claims").json() == claims_before
    assert (before["history"][2]["title"], before["history"][2]["amount_paise"], before["history"][2]["comment"]) == ("Client visit taxi", 8000, "Check receipt")


def test_approver_retains_reviewed_correction_visibility(client: TestClient, approver: TestClient) -> None:
    claim_id = corrected_claim(client, approver)
    private = create_claim(client).json()
    assert approver.get(f"/api/claims/{private['id']}").status_code == 404
    rows = approver.get("/api/approvals").json()
    assert [row["id"] for row in rows] == [claim_id]
    assert private["has_review_history"] is False
    assert rows[0]["has_review_history"] is True
    detail = approver.get(f"/api/claims/{claim_id}")
    assert detail.status_code == 200
    assert detail.json()["has_review_history"] is True
    assert detail.json()["history"][2]["title"] == "Client visit taxi"
    assert review_claim(approver, claim_id, {"version": 4, "decision": "APPROVED", "approved_amount_paise": 9000}).status_code == 409
    assert approver.post(f"/api/claims/{claim_id}/pay", json={"version": 4}).status_code == 409
    assert approver.get(f"/api/claims/{claim_id}").json() == detail.json()
    submitted = submit_claim(client, claim_id, 4)
    assert submitted.status_code == 200
    assert submitted.json()["has_review_history"] is True
    assert approver.get("/api/approvals").json()[0]["has_review_history"] is True
    assert approver.get(f"/api/claims/{claim_id}").json()["has_review_history"] is True
    assert client.delete(f"/api/claims/{private['id']}?version=1").status_code == 204


def test_invalid_transitions_stale_versions_and_review_validation_do_not_mutate(client: TestClient, approver: TestClient) -> None:
    created = create_claim(client).json()
    claim_id = created["id"]
    assert submit_claim(client, claim_id, 1).status_code == 200
    assert submit_claim(client, claim_id, 1).status_code == 409
    assert submit_claim(client, claim_id, 2).status_code == 409
    assert review_claim(
        approver, claim_id, {"version": 2, "decision": "APPROVED", "approved_amount_paise": 13000}
    ).status_code == 422
    assert approver.post(
        f"/api/claims/{claim_id}/pay", json={"version": 2}
    ).status_code == 409

    detail = client.get(f"/api/claims/{claim_id}").json()
    assert (detail["status"], detail["version"]) == ("SUBMITTED", 2)
    assert [event["action"] for event in detail["history"]] == ["CREATED", "SUBMITTED"]


def test_only_owner_can_edit_or_delete_draft_and_delete_cascades_history(client: TestClient, approver: TestClient) -> None:
    created = create_claim(client).json()
    claim_id = created["id"]
    payload = {**claim_payload(title="Changed"), "version": 1}
    assert client.patch(f"/api/claims/{claim_id}", json=payload).status_code == 200
    assert client.delete(
        f"/api/claims/{claim_id}?version=1"
    ).status_code == 409
    assert client.delete(
        f"/api/claims/{claim_id}?version=2"
    ).status_code == 204
    assert client.get(f"/api/claims/{claim_id}").status_code == 404


def test_foreign_owner_claim_is_private_and_immutable(application, client):
    connection = application.state.database.connect()
    try:
        connection.execute("INSERT INTO users (id, name, role) VALUES (2, 'Other employee', 'EMPLOYEE')")
        cursor = connection.execute(
            """INSERT INTO claims (owner_id, title, description, category, expense_date,
               amount_paise, status, created_at, updated_at, version, idempotency_key, creation_payload)
               VALUES (2, 'Foreign', 'Private', 'Other', '2025-02-03', 100, 'DRAFT',
                       '2025-02-03', '2025-02-03', 1, 'foreign-key', '{}')"""
        )
        claim_id = cursor.lastrowid
    finally:
        connection.close()
    assert client.get("/api/claims").json() == []
    assert client.get(f"/api/claims/{claim_id}").status_code == 404
    assert client.patch(f"/api/claims/{claim_id}", json={**claim_payload(), "version": 1}).status_code == 404
    assert client.delete(f"/api/claims/{claim_id}?version=1").status_code == 404
    assert submit_claim(client, claim_id, 1).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"version": 2, "decision": "APPROVED", "approved_amount_paise": 5000},
        {"version": 2, "decision": "REJECTED"},
        {"version": 2, "decision": "REJECTED", "approved_amount_paise": 5000, "comment": "No"},
        {"version": 2, "decision": "APPROVED", "approved_amount_paise": 0},
        {"version": 2, "decision": "APPROVED", "approved_amount_paise": True},
    ],
)
def test_review_requires_valid_decision_amount_and_comment(client: TestClient, approver: TestClient, body) -> None:
    claim = create_claim(client).json()
    submit_claim(client, claim["id"], 1)
    assert review_claim(approver, claim["id"], body).status_code == 422


def test_parallel_review_has_one_winner_and_one_event(client: TestClient, approver: TestClient) -> None:
    claim = create_claim(client).json()
    claim_id = claim["id"]
    assert submit_claim(client, claim_id, 1).status_code == 200
    review_body = {"version": 2, "decision": "APPROVED", "approved_amount_paise": 12500}

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: review_claim(approver, claim_id, review_body), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    detail = client.get(f"/api/claims/{claim_id}").json()
    assert (detail["status"], detail["version"]) == ("APPROVED", 3)
    assert [event["action"] for event in detail["history"]] == ["CREATED", "SUBMITTED", "APPROVED"]


def test_sqlite_claim_persists_after_app_reopen(tmp_path) -> None:
    database_path = tmp_path / "persistent.sqlite3"
    first_app = create_app(database_path)
    with TestClient(first_app) as first_client:
        assert first_client.post("/api/auth/login", json={"username": "employee", "password": "employee123"}).status_code == 200
        created = create_claim(first_client).json()

    second_app = create_app(database_path)
    with TestClient(second_app) as second_client:
        assert second_client.post("/api/auth/login", json={"username": "employee", "password": "employee123"}).status_code == 200
        detail = second_client.get(f"/api/claims/{created['id']}")
        assert detail.status_code == 200
        assert detail.json()["history"][0]["action"] == "CREATED"


def test_initialization_preserves_existing_non_login_user(tmp_path):
    path = tmp_path / "existing.sqlite3"
    first = create_app(path)
    with TestClient(first):
        connection = first.state.database.connect()
        try:
            assert connection.execute("SELECT id FROM users ORDER BY id").fetchall()[1][0] == 3
            connection.execute("INSERT INTO users VALUES (2, 'Legacy employee', 'EMPLOYEE')")
        finally:
            connection.close()
    second = create_app(path)
    with TestClient(second) as browser:
        connection = second.state.database.connect()
        try:
            assert connection.execute("SELECT name FROM users WHERE id = 2").fetchone()[0] == "Legacy employee"
        finally:
            connection.close()
        assert browser.post("/api/auth/login", json={"username": "Legacy employee", "password": "employee123"}).status_code == 401


def test_workspace_lists_and_permissions(client, approver):
    claim = create_claim(client).json()
    assert submit_claim(client, claim["id"], 1).status_code == 200
    assert approver.get("/api/claims").json() == []
    assert client.get("/api/approvals").status_code == 403
    assert approver.get("/api/approvals").json()[0]["allowed_actions"] == ["review"]


def test_workspace_queue_order_privacy_and_payload_ownership(application, client, approver):
    with TestClient(application) as anonymous:
        assert anonymous.get("/api/approvals").status_code == 401
    assert create_claim(approver, claim_payload(owner_id=1)).status_code == 422
    private = create_claim(client).json()
    first = create_claim(client).json()
    second = create_claim(client).json()
    own = create_claim(approver).json()
    assert submit_claim(client, first["id"], 1).status_code == 200
    assert submit_claim(client, second["id"], 1).status_code == 200
    assert submit_claim(approver, own["id"], 1).status_code == 200
    assert [row["id"] for row in approver.get("/api/approvals").json()] == [second["id"], first["id"]]
    assert [row["id"] for row in approver.get("/api/claims?role=employee").json()] == [own["id"]]
    assert approver.get(f"/api/claims/{private['id']}").status_code == 404


@pytest.mark.parametrize("claimant_fixture", ["client", "approver"])
def test_workspace_claimant_lifecycle(request, claimant_fixture, application, monkeypatch):
    import app

    claimant = request.getfixturevalue(claimant_fixture)
    connection = application.state.database.connect()
    try:
        connection.execute("INSERT INTO users (id, name, role) VALUES (4, 'Second Approver', 'APPROVER')")
    finally:
        connection.close()
    monkeypatch.setitem(app.MOCK_ACCOUNTS, "second", ("second123", 4, "Second Approver", "APPROVER"))
    with TestClient(application) as reviewer:
        login = reviewer.post("/api/auth/login", json={"username": "second", "password": "second123"})
        assert login.json()["permissions"] == ["create_claims", "review_claims", "mark_paid"]
        assert reviewer.get("/api/auth/me").json() == login.json()
        creation = create_claim(claimant)
        assert creation.status_code == 201
        created = creation.json()
        claim_id = created["id"]
        assert created["allowed_actions"] == ["edit", "delete", "submit"]
        assert reviewer.get(f"/api/claims/{claim_id}").status_code == 404
        assert reviewer.get("/api/approvals").json() == []
        edit_response = claimant.patch(f"/api/claims/{claim_id}", json={**claim_payload(title="Edited"), "version": 1})
        assert edit_response.status_code == 200
        edited = edit_response.json()
        assert edited["allowed_actions"] == ["edit", "delete", "submit"]
        submitted = submit_claim(claimant, claim_id, 2).json()
        assert submitted["allowed_actions"] == []
        assert reviewer.get("/api/approvals").json()[0]["allowed_actions"] == ["review"]
        if claimant_fixture == "approver":
            before = claimant.get(f"/api/claims/{claim_id}").json()
            for body in ({"version": 3, "decision": "APPROVED", "approved_amount_paise": 12500},
                         {"version": 3, "decision": "REJECTED", "comment": "No"}):
                assert review_claim(claimant, claim_id, body).status_code == 403
                assert claimant.get(f"/api/claims/{claim_id}").json() == before
            assert claimant.get("/api/approvals").json() == []
        rejected = review_claim(reviewer, claim_id, {"version": 3, "decision": "REJECTED", "comment": "Fix"}).json()
        assert rejected["allowed_actions"] == []
        assert claimant.get(f"/api/claims/{claim_id}").json()["allowed_actions"] == ["edit", "submit"]
        corrected = claimant.patch(f"/api/claims/{claim_id}", json={**claim_payload(), "version": 4}).json()
        assert corrected["allowed_actions"] == ["edit", "submit"]
        assert claimant.delete(f"/api/claims/{claim_id}?version=5").status_code == 409
        assert reviewer.get("/api/approvals").json()[0]["allowed_actions"] == []
        assert reviewer.get(f"/api/claims/{claim_id}").json()["has_review_history"] is True
        assert submit_claim(claimant, claim_id, 5).status_code == 200
        approved = review_claim(reviewer, claim_id, {"version": 6, "decision": "APPROVED", "approved_amount_paise": 12500}).json()
        assert approved["allowed_actions"] == ["pay"]
        if claimant_fixture == "approver":
            before = claimant.get(f"/api/claims/{claim_id}").json()
            assert before["allowed_actions"] == []
            assert claimant.post(f"/api/claims/{claim_id}/pay", json={"version": 7}).status_code == 403
            assert claimant.get(f"/api/claims/{claim_id}").json() == before
        paid = reviewer.post(f"/api/claims/{claim_id}/pay", json={"version": 7}).json()
        assert paid["status"] == "PAID" and paid["allowed_actions"] == []
        assert [e["action"] for e in claimant.get(f"/api/claims/{claim_id}").json()["history"]] == [
            "CREATED", "EDITED", "SUBMITTED", "REJECTED", "EDITED", "SUBMITTED", "APPROVED", "PAID"]
