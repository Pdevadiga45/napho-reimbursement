from datetime import date, timedelta
import base64
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
        assert submit_claim(employee, claim["id"], attach_receipt(employee, claim["id"], claim["version"])).status_code == 200
    with TestClient(application) as manager:
        assert manager.post("/api/auth/login", json={"username": "manager", "password": "manager123"}).status_code == 200
        assert manager.post(f"/api/claims/{claim['id']}/review", json={"version": 3, "decision": "APPROVED", "approved_amount_paise": 12500}).status_code == 200
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
        assert manager.get(f"/api/claims/{claim['id']}").json()["history"][3]["actor_name"] == "Manager One"


def test_session_logout_tamper_validation_and_header_spoof(application, client, approver):
    cookie = client.cookies.get("napho_session")
    assert cookie
    assert client.get("/api/auth/me").json()["role"] == "EMPLOYEE"
    assert approver.get("/api/auth/me").json()["role"] == "APPROVER"
    assert client.get("/api/claims", headers={"X-Expected-User-Id": "1"}).status_code == 200
    for actor in ("3", "invalid", "", "01"):
        assert client.get("/api/claims", headers={"X-Expected-User-Id": actor}).status_code == 401
        response = client.post("/api/claims", headers={"X-Expected-User-Id": actor, "Idempotency-Key": str(uuid4())}, json=claim_payload())
        assert response.status_code == 401
    assert client.get("/api/claims").json() == []
    with TestClient(application) as anonymous:
        assert anonymous.get("/api/claims", headers={"X-Expected-User-Id": "1"}).status_code == 401
    claim = create_claim(client).json()
    assert submit_claim(client, claim["id"], attach_receipt(client, claim["id"], 1)).status_code == 200
    before = client.get(f"/api/claims/{claim['id']}").json()
    mismatch = {"X-Expected-User-Id": "3"}
    assert client.patch(f"/api/claims/{claim['id']}", headers=mismatch, json={**claim_payload(), "version": 3}).status_code == 401
    assert client.post(f"/api/claims/{claim['id']}/receipt?version=3&filename=bill.png", headers=mismatch, content=b"receipt").status_code == 401
    assert client.get(f"/api/claims/{claim['id']}", headers=mismatch).status_code == 401
    assert client.get(f"/api/claims/{claim['id']}").json() == before
    assert client.post(f"/api/claims/{claim['id']}/review", headers=headers(3), json={"version": 3, "decision": "APPROVED", "approved_amount_paise": 12500}).status_code == 403
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
        "category": "travel and stay",
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


def attach_receipt(client, claim_id, version):
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aKsAAAAASUVORK5CYII=')
    response = client.post(f"/api/claims/{claim_id}/receipt", params={"version": version, "filename": "bill.png"}, content=png)
    assert response.status_code == 200
    return response.json()["version"]


def review_claim(client: TestClient, claim_id: int, payload):
    return client.post(
        f"/api/claims/{claim_id}/review", json=payload
    )


def test_submission_requires_receipt_without_mutation(client):
    claim = create_claim(client).json()
    before = client.get(f"/api/claims/{claim['id']}").json()
    assert "submit" in before["allowed_actions"]
    assert before["submission_errors"]
    response = submit_claim(client, claim["id"], claim["version"])
    assert response.status_code == 422
    assert client.get(f"/api/claims/{claim['id']}").json() == before


def test_expense_date_contract(client):
    response = create_claim(client, claim_payload(expense_date="2025-02-03"))
    assert response.status_code == 201
    claim = response.json()
    assert claim["expense_date"] == "2025-02-03"
    assert client.get(f"/api/claims/{claim['id']}").json()["history"][0]["expense_date"] == claim["expense_date"]
    for value in ("2025-02-03T12:30:00+05:30", "2025-02-03T12:30:00", 1738582200, True, "2025-02-30", "2025-2-03"):
        assert create_claim(client, claim_payload(expense_date=value)).status_code == 422
    future = (date.today() + timedelta(days=1)).isoformat()
    assert create_claim(client, claim_payload(expense_date=future)).status_code == 422
    assert create_claim(client, claim_payload(expense_date=date.today().isoformat())).status_code == 201
    for value in (future, "2025-02-03T12:30:00", 1738582200):
        assert client.patch(f"/api/claims/{claim['id']}", json={**claim_payload(expense_date=value), "version": 1}).status_code == 422


def test_date_persists_in_store_history_and_reopen(application, client):
    created = create_claim(client, claim_payload(expense_date="2025-02-03")).json()
    cid = created['id']
    canonical = '2025-02-03'
    edited = client.patch(f'/api/claims/{cid}', json={**claim_payload(expense_date='2025-02-03'), 'version': 1}).json()
    assert created['expense_date'] == edited['expense_date'] == canonical
    connection = application.state.database.connect()
    try:
        assert connection.execute('SELECT expense_date FROM claims WHERE id = ?', (cid,)).fetchone()[0] == canonical
    finally:
        connection.close()
    with TestClient(create_app(application.state.database.path)) as reopened:
        reopened.post('/api/auth/login', json={'username': 'employee', 'password': 'employee123'})
        detail = reopened.get(f'/api/claims/{cid}').json()
        assert detail['expense_date'] == canonical
        assert [event['expense_date'] for event in detail['history']] == [canonical, canonical]


def test_legacy_timestamp_read_submit_and_edit_preserve_history(application, client):
    claim = create_claim(client).json()
    cid = claim['id']
    old = '2025-02-03T12:30:15+00:00'
    connection = application.state.database.connect()
    try:
        connection.execute('UPDATE claims SET expense_date=? WHERE id=?', (old, cid))
        connection.execute('UPDATE claim_events SET expense_date=? WHERE claim_id=?', (old, cid))
    finally:
        connection.close()
    detail = client.get(f'/api/claims/{cid}').json()
    assert detail['expense_date'] == detail['history'][0]['expense_date'] == old
    version = attach_receipt(client, cid, detail['version'])
    assert client.get(f'/api/claims/{cid}').json()['submission_errors'] == []
    edited = client.patch(f'/api/claims/{cid}', json={**claim_payload(), 'version': version}).json()
    assert edited['expense_date'] == '2025-02-03'
    history = client.get(f'/api/claims/{cid}').json()['history']
    assert history[0]['expense_date'] == old
    assert submit_claim(client, cid, edited['version']).status_code == 200


def test_legacy_editable_values_require_correction_and_preserve_history(application, client):
    claim = create_claim(client).json()
    cid = claim['id']
    # Source-shaped legacy data: its original date and category have no implied replacements.
    connection = application.state.database.connect()
    try:
        connection.execute("UPDATE claims SET category='Travel', expense_date='2025-02-03' WHERE id=?", (cid,))
        connection.execute("UPDATE claim_events SET category='Travel', expense_date='2025-02-03' WHERE claim_id=?", (cid,))
    finally:
        connection.close()
    before = client.get(f'/api/claims/{cid}').json()
    assert (before['category'], before['expense_date']) == ('Travel', '2025-02-03')
    assert len(before['submission_errors']) == 2
    assert submit_claim(client, cid, before['version']).status_code == 422
    assert client.get(f'/api/claims/{cid}').json() == before
    version = attach_receipt(client, cid, before['version'])
    assert submit_claim(client, cid, version).status_code == 422
    corrected = client.patch(f'/api/claims/{cid}', json={**claim_payload(), 'version': version}).json()
    assert submit_claim(client, cid, corrected['version']).status_code == 200
    history = client.get(f'/api/claims/{cid}').json()['history']
    assert (history[0]['category'], history[0]['expense_date']) == ('Travel', '2025-02-03')


def test_legacy_submitted_claim_remains_readable_and_reviewable(application, client, approver):
    cid = create_claim(client).json()['id']
    connection = application.state.database.connect()
    try:
        connection.execute("UPDATE claims SET status='SUBMITTED', category='Travel', expense_date='2025-02-03' WHERE id=?", (cid,))
        connection.execute("UPDATE claim_events SET category='Travel', expense_date='2025-02-03' WHERE claim_id=?", (cid,))
    finally:
        connection.close()
    before = client.get(f'/api/claims/{cid}').json()
    assert (before['status'], before['category'], before['expense_date'], before['receipt']) == ('SUBMITTED', 'Travel', '2025-02-03', None)
    assert approver.get(f'/api/claims/{cid}').status_code == 200
    assert client.get(f'/api/claims/{cid}').json() == before
    assert review_claim(approver, cid, {'version': before['version'], 'decision': 'APPROVED', 'approved_amount_paise': 12500}).status_code == 200
    detail = client.get(f'/api/claims/{cid}').json()
    assert detail['expense_date'] == detail['history'][0]['expense_date'] == '2025-02-03'


@pytest.mark.parametrize("category", ["travel and stay", "meals", "work & tech", "others"])
def test_categories_are_closed_on_create_and_edit(client, category):
    created = create_claim(client, claim_payload(category=category))
    assert created.status_code == 201
    claim = created.json()
    assert client.patch(f"/api/claims/{claim['id']}", json={**claim_payload(category=category), "version": 1}).status_code == 200
    assert create_claim(client, claim_payload(category="unknown")).status_code == 422
    assert client.patch(f"/api/claims/{claim['id']}", json={**claim_payload(category="unknown"), "version": 2}).status_code == 422
    assert create_claim(client, claim_payload(category=" meals ")).status_code == 422


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
    submitted = submit_claim(client, claim_id, attach_receipt(client, claim_id, created["version"]))
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUBMITTED"

    assert approver.get("/api/approvals").json()[0]["id"] == claim_id
    assert review_claim(
        client,
        claim_id,
        {"version": 3, "decision": "APPROVED", "approved_amount_paise": 7500, "comment": "Within policy"},
    ).status_code == 403
    approved = review_claim(
        approver,
        claim_id,
        {"version": 3, "decision": "APPROVED", "approved_amount_paise": 7500, "comment": "Within policy"},
    )
    assert approved.status_code == 200
    assert approved.json()["amount_paise"] == 10000
    assert approved.json()["approved_amount_paise"] == 7500
    assert approved.json()["version"] == 4
    assert client.post(
        f"/api/claims/{claim_id}/pay", json={"version": 4}
    ).status_code == 403

    paid = approver.post(
        f"/api/claims/{claim_id}/pay", json={"version": 4}
    )
    assert paid.status_code == 200
    assert (paid.json()["status"], paid.json()["version"]) == ("PAID", 5)
    assert approver.post(
        f"/api/claims/{claim_id}/pay", json={"version": 4}
    ).status_code == 409

    history = client.get(f"/api/claims/{claim_id}").json()["history"]
    assert [event["action"] for event in history] == ["CREATED", "RECEIPT_ATTACHED", "SUBMITTED", "APPROVED", "PAID"]
    assert history[3]["actor_name"] == "Manager One"
    assert history[3]["comment"] == "Within policy"
    assert history[3]["amount_paise"] == 10000
    assert history[3]["approved_amount_paise"] == 7500


def test_rejected_claim_edit_and_resubmit_preserve_old_snapshots(client: TestClient, approver: TestClient) -> None:
    original = claim_payload(amount_paise=8000)
    key = str(uuid4())
    created = create_claim(client, original, idempotency_key=key).json()
    claim_id = created["id"]
    assert submit_claim(client, claim_id, attach_receipt(client, claim_id, 1)).status_code == 200
    rejected = review_claim(
        approver, claim_id, {"version": 3, "decision": "REJECTED", "comment": "Need receipt detail"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["has_review_history"] is True

    edited = client.patch(
        f"/api/claims/{claim_id}",
        json={**claim_payload(title="Updated taxi", amount_paise=9000), "version": 4},
    )
    assert edited.status_code == 200
    assert (edited.json()["status"], edited.json()["version"]) == ("DRAFT", 5)
    assert edited.json()["approved_amount_paise"] is None
    assert submit_claim(client, claim_id, 5).json()["status"] == "SUBMITTED"

    history = client.get(f"/api/claims/{claim_id}").json()["history"]
    assert [event["action"] for event in history] == [
        "CREATED", "RECEIPT_ATTACHED", "SUBMITTED", "REJECTED", "EDITED", "SUBMITTED"
    ]
    assert (history[3]["title"], history[3]["amount_paise"], history[3]["comment"]) == (
        "Client visit taxi",
        8000,
        "Need receipt detail",
    )
    assert (history[4]["title"], history[4]["amount_paise"], history[4]["status"]) == (
        "Updated taxi",
        9000,
        "DRAFT",
    )
    assert approver.get("/api/approvals").json()[0]["status"] == "SUBMITTED"
    replay = create_claim(client, original, idempotency_key=key)
    assert replay.status_code == 200
    assert replay.json()["id"] == claim_id
    assert replay.json()["status"] == "SUBMITTED"
    assert len(client.get(f"/api/claims/{claim_id}").json()["history"]) == 6


def corrected_claim(client, approver):
    created = create_claim(client, claim_payload(amount_paise=8000)).json()
    claim_id = created["id"]
    assert submit_claim(client, claim_id, attach_receipt(client, claim_id, 1)).status_code == 200
    assert review_claim(approver, claim_id, {"version": 3, "decision": "REJECTED", "comment": "Check receipt"}).status_code == 200
    edited = client.patch(f"/api/claims/{claim_id}", json={**claim_payload(title="Corrected taxi", amount_paise=9000), "version": 4})
    assert edited.status_code == 200
    assert (edited.json()["status"], edited.json()["version"]) == ("DRAFT", 5)
    return claim_id


def test_corrected_draft_cannot_be_deleted_and_preserves_history(client: TestClient, approver: TestClient) -> None:
    claim_id = corrected_claim(client, approver)
    before = client.get(f"/api/claims/{claim_id}").json()
    claims_before = client.get("/api/claims").json()
    assert client.delete(f"/api/claims/{claim_id}?version=5").status_code == 409
    assert client.get(f"/api/claims/{claim_id}").json() == before
    assert client.get("/api/claims").json() == claims_before
    assert (before["history"][3]["title"], before["history"][3]["amount_paise"], before["history"][3]["comment"]) == ("Client visit taxi", 8000, "Check receipt")


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
    assert detail.json()["history"][3]["title"] == "Client visit taxi"
    assert review_claim(approver, claim_id, {"version": 5, "decision": "APPROVED", "approved_amount_paise": 9000}).status_code == 409
    assert approver.post(f"/api/claims/{claim_id}/pay", json={"version": 5}).status_code == 409
    assert approver.get(f"/api/claims/{claim_id}").json() == detail.json()
    submitted = submit_claim(client, claim_id, 5)
    assert submitted.status_code == 200
    assert submitted.json()["has_review_history"] is True
    assert approver.get("/api/approvals").json()[0]["has_review_history"] is True
    assert approver.get(f"/api/claims/{claim_id}").json()["has_review_history"] is True
    assert client.delete(f"/api/claims/{private['id']}?version=1").status_code == 204


def test_invalid_transitions_stale_versions_and_review_validation_do_not_mutate(client: TestClient, approver: TestClient) -> None:
    created = create_claim(client).json()
    claim_id = created["id"]
    assert submit_claim(client, claim_id, attach_receipt(client, claim_id, 1)).status_code == 200
    assert submit_claim(client, claim_id, 1).status_code == 409
    assert submit_claim(client, claim_id, 3).status_code == 409
    assert review_claim(
        approver, claim_id, {"version": 3, "decision": "APPROVED", "approved_amount_paise": 13000}
    ).status_code == 422
    assert approver.post(
        f"/api/claims/{claim_id}/pay", json={"version": 3}
    ).status_code == 409

    detail = client.get(f"/api/claims/{claim_id}").json()
    assert (detail["status"], detail["version"]) == ("SUBMITTED", 3)
    assert [event["action"] for event in detail["history"]] == ["CREATED", "RECEIPT_ATTACHED", "SUBMITTED"]


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
        {"version": 3, "decision": "APPROVED", "approved_amount_paise": 5000},
        {"version": 3, "decision": "REJECTED"},
        {"version": 3, "decision": "REJECTED", "approved_amount_paise": 5000, "comment": "No"},
        {"version": 3, "decision": "APPROVED", "approved_amount_paise": 0},
        {"version": 3, "decision": "APPROVED", "approved_amount_paise": True},
    ],
)
def test_review_requires_valid_decision_amount_and_comment(client: TestClient, approver: TestClient, body) -> None:
    claim = create_claim(client).json()
    submit_claim(client, claim["id"], attach_receipt(client, claim["id"], 1))
    assert review_claim(approver, claim["id"], body).status_code == 422


def test_parallel_review_has_one_winner_and_one_event(client: TestClient, approver: TestClient) -> None:
    claim = create_claim(client).json()
    claim_id = claim["id"]
    assert submit_claim(client, claim_id, attach_receipt(client, claim_id, 1)).status_code == 200
    review_body = {"version": 3, "decision": "APPROVED", "approved_amount_paise": 12500}

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: review_claim(approver, claim_id, review_body), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    detail = client.get(f"/api/claims/{claim_id}").json()
    assert (detail["status"], detail["version"]) == ("APPROVED", 4)
    assert [event["action"] for event in detail["history"]] == ["CREATED", "RECEIPT_ATTACHED", "SUBMITTED", "APPROVED"]


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


def test_workspace_queue_order_privacy_and_payload_ownership(application, client, approver):
    with TestClient(application) as anonymous:
        assert anonymous.get("/api/approvals").status_code == 401
    assert create_claim(approver, claim_payload(owner_id=1)).status_code == 422
    private = create_claim(client).json()
    first = create_claim(client).json()
    second = create_claim(client).json()
    own_list = approver.get("/api/claims")
    assert own_list.status_code == 200
    assert own_list.json() == []
    assert client.get("/api/approvals").status_code == 403
    own = create_claim(approver).json()
    assert submit_claim(client, first["id"], attach_receipt(client, first["id"], 1)).status_code == 200
    assert submit_claim(client, second["id"], attach_receipt(client, second["id"], 1)).status_code == 200
    assert submit_claim(approver, own["id"], attach_receipt(approver, own["id"], 1)).status_code == 200
    queue = approver.get("/api/approvals")
    assert queue.status_code == 200
    assert [row["id"] for row in queue.json()] == [second["id"], first["id"]]
    assert queue.json()[0]["allowed_actions"] == ["review"]
    assert [row["id"] for row in approver.get("/api/claims?role=employee").json()] == [own["id"]]
    assert approver.get(f"/api/claims/{private['id']}").status_code == 404


def test_paginated_workspaces_filter_order_and_count_authorized_claims(client, approver, monkeypatch):
    for browser, path in ((client, "/api/claims"), (approver, "/api/approvals")):
        response = browser.get(path)
        assert response.status_code == 200
        assert response.json() == []
        assert (response.headers["X-Total-Count"], response.headers["X-Page-Size"], response.headers["X-Page"]) == ("0", "10", "1")
    monkeypatch.setattr("app.utc_now", lambda: "2025-01-01T00:00:00+00:00")
    older = create_claim(client).json()["id"]
    monkeypatch.setattr("app.utc_now", lambda: "2025-01-02T00:00:00+00:00")
    submitted_ids = []
    for _ in range(12):
        created = create_claim(client).json()
        submitted_ids.append(created["id"])
        version = attach_receipt(client, created["id"], created["version"])
        assert submit_claim(client, created["id"], version).status_code == 200
    corrected = corrected_claim(client, approver)
    private = create_claim(client).json()["id"]
    own = create_claim(approver).json()
    assert submit_claim(approver, own["id"], attach_receipt(approver, own["id"], 1)).status_code == 200

    def check(browser, path, params, expected, total, page):
        response = browser.get(path, params=params)
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == expected
        assert len(response.json()) <= 10
        assert (response.headers["X-Total-Count"], response.headers["X-Page-Size"], response.headers["X-Page"]) == (str(total), "10", str(page))

    employee_ids = [private, corrected, *reversed(submitted_ids), older]
    approval_ids = [corrected, *reversed(submitted_ids)]
    for browser, path, ids in [(client, "/api/claims", employee_ids), (approver, "/api/approvals", approval_ids)]:
        check(browser, path, {}, ids[:10], len(ids), 1)
        check(browser, path, {"page": 2}, ids[10:], len(ids), 2)
        check(browser, path, {"page": 99}, [], len(ids), 99)
        check(browser, path, {"page": 1_000_000}, [], len(ids), 1_000_000)
        check(browser, path, {"status": "SUBMITTED", "page": 2}, list(reversed(submitted_ids))[10:], 12, 2)
        check(browser, path, {"status": "DRAFT"}, [private, corrected, older] if browser is client else [corrected], 3 if browser is client else 1, 1)
        check(browser, path, {"status": "PAID"}, [], 0, 1)
    assert client.get("/api/approvals").status_code == 403


@pytest.mark.parametrize("path,fixture", [("/api/claims", "client"), ("/api/approvals", "approver")])
def test_paginated_workspaces_reject_invalid_queries(request, path, fixture):
    browser = request.getfixturevalue(fixture)
    for params in ({"page": 0}, {"page": -1}, {"page": "abc"}, {"page": "1.5"}, {"page": 1_000_001},
                   {"page": "999999999999999999999999999999999999"}):
        assert browser.get(path, params=params).status_code == 422
    for status in ("UNKNOWN", ""):
        assert browser.get(path, params={"status": status}).status_code == 422


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
        submitted = submit_claim(claimant, claim_id, attach_receipt(claimant, claim_id, edited["version"])).json()
        assert submitted["allowed_actions"] == []
        assert reviewer.get("/api/approvals").json()[0]["allowed_actions"] == ["review"]
        if claimant_fixture == "approver":
            before = claimant.get(f"/api/claims/{claim_id}").json()
            for body in ({"version": 4, "decision": "APPROVED", "approved_amount_paise": 12500},
                         {"version": 4, "decision": "REJECTED", "comment": "No"}):
                assert review_claim(claimant, claim_id, body).status_code == 403
                assert claimant.get(f"/api/claims/{claim_id}").json() == before
            assert claimant.get("/api/approvals").json() == []
        rejected = review_claim(reviewer, claim_id, {"version": 4, "decision": "REJECTED", "comment": "Fix"}).json()
        assert rejected["allowed_actions"] == []
        assert claimant.get(f"/api/claims/{claim_id}").json()["allowed_actions"] == ["edit", "submit"]
        corrected = claimant.patch(f"/api/claims/{claim_id}", json={**claim_payload(), "version": 5}).json()
        assert corrected["allowed_actions"] == ["edit", "submit"]
        assert claimant.delete(f"/api/claims/{claim_id}?version=6").status_code == 409
        assert reviewer.get("/api/approvals").json()[0]["allowed_actions"] == []
        assert reviewer.get(f"/api/claims/{claim_id}").json()["has_review_history"] is True
        assert submit_claim(claimant, claim_id, 6).status_code == 200
        approved = review_claim(reviewer, claim_id, {"version": 7, "decision": "APPROVED", "approved_amount_paise": 12500}).json()
        assert approved["allowed_actions"] == ["pay"]
        if claimant_fixture == "approver":
            before = claimant.get(f"/api/claims/{claim_id}").json()
            assert before["allowed_actions"] == []
            assert claimant.post(f"/api/claims/{claim_id}/pay", json={"version": 8}).status_code == 403
            assert claimant.get(f"/api/claims/{claim_id}").json() == before
        paid = reviewer.post(f"/api/claims/{claim_id}/pay", json={"version": 8}).json()
        assert paid["status"] == "PAID" and paid["allowed_actions"] == []
        assert [e["action"] for e in claimant.get(f"/api/claims/{claim_id}").json()["history"]] == [
            "CREATED", "EDITED", "RECEIPT_ATTACHED", "SUBMITTED", "REJECTED", "EDITED", "SUBMITTED", "APPROVED", "PAID"]
