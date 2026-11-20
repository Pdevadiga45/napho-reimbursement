import base64
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from PIL import Image

import store
from app import create_app
from db import Database, SCHEMA
from test_claims import application, client, approver, create_claim, claim_payload, submit_claim, review_claim


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aKsAAAAASUVORK5CYII='
)


def upload(client, claim_id, version, filename="bill.png", content=PNG, **kwargs):
    return client.post(f"/api/claims/{claim_id}/receipt", params={"version": version, "filename": filename}, content=content, **kwargs)


def test_merchant_legacy_replay_edit_and_reopen(application, client):
    key = str(uuid4())
    claim = create_claim(client, claim_payload(), key).json()
    connection = application.state.database.connect()
    try:
        legacy = claim_payload()
        connection.execute("UPDATE claims SET creation_payload = ? WHERE id = ?", (json.dumps(legacy), claim['id']))
    finally:
        connection.close()
    assert create_claim(client, legacy, key).status_code == 200
    assert create_claim(client, claim_payload(merchant="Other"), key).status_code == 409
    edited = client.patch(f"/api/claims/{claim['id']}", json={**claim_payload(merchant="  Shop  "), "version": 1}).json()
    assert edited['merchant'] == 'Shop'
    edited = client.patch(f"/api/claims/{claim['id']}", json={**claim_payload(), "version": 2}).json()
    assert edited['merchant'] == 'Shop'
    attached = upload(client, claim['id'], 3).json()
    with TestClient(create_app(application.state.database.path)) as reopened:
        reopened.post('/api/auth/login', json={'username': 'employee', 'password': 'employee123'})
        detail = reopened.get(f"/api/claims/{claim['id']}").json()
        assert detail['receipt'] == attached['receipt']
        assert detail['merchant'] == 'Shop'
        assert [e['merchant'] for e in detail['history']] == ['', 'Shop', 'Shop', 'Shop']
        assert reopened.get(f"/api/claims/{claim['id']}/receipts/{attached['receipt']['id']}").content == PNG
    cleared = client.patch(f"/api/claims/{claim['id']}", json={**claim_payload(merchant='  '), 'version': 4}).json()
    assert cleared['merchant'] == ''
    assert create_claim(client, claim_payload(merchant='x' * 121)).status_code == 422


def test_privacy_stale_validation_and_state(application, client, approver):
    claim = create_claim(client).json()
    cid = claim['id']
    maximum = 2**63 - 1
    with TestClient(application, raise_server_exceptions=False) as checked:
        checked.cookies.set('napho_session', client.cookies.get('napho_session'))
        assert checked.get(f'/api/claims/{maximum}').status_code == 404
        assert checked.get(f'/api/claims/{cid}/receipts/{maximum}').status_code == 404
        for value in (0, -1, maximum + 1):
            for method, path, kwargs in (
                ('get', f'/api/claims/{value}', {}),
                ('patch', f'/api/claims/{value}', {'json': {**claim_payload(), 'version': 1}}),
                ('delete', f'/api/claims/{value}?version=1', {}),
                ('post', f'/api/claims/{value}/submit', {'json': {'version': 1}}),
                ('post', f'/api/claims/{value}/review', {'json': {'version': 1, 'decision': 'APPROVED', 'approved_amount_paise': 1}}),
                ('post', f'/api/claims/{value}/pay', {'json': {'version': 1}}),
                ('post', f'/api/claims/{value}/receipt?version=1&filename=bill.png', {'content': PNG}),
                ('delete', f'/api/claims/{value}/receipt?version=1', {}),
                ('get', f'/api/claims/{value}/receipts/1', {}),
                ('get', f'/api/claims/{cid}/receipts/{value}', {}),
            ):
                assert getattr(checked, method)(path, **kwargs).status_code == 422, (method, path)
    initial_upload = upload(client, cid, 1)
    assert initial_upload.status_code == 200
    attached = initial_upload.json()
    rid = attached['receipt']['id']
    assert client.get(f'/api/claims/{cid}/receipts/{rid}').content == PNG
    other = create_claim(client).json()['id']
    with TestClient(application) as anonymous:
        assert anonymous.get(f'/api/claims/{cid}/receipts/{rid}').status_code == 401
        assert upload(anonymous, cid, 2).status_code == 401
    assert approver.get(f'/api/claims/{cid}/receipts/{rid}').status_code == 404
    assert upload(approver, cid, 2).status_code == 404
    assert client.get(f'/api/claims/{other}/receipts/{rid}').status_code == 404
    assert upload(client, cid, 1).status_code == 409
    before = client.get(f'/api/claims/{cid}').json()
    for name, content in [('bill.pdf', PNG), ('bill.png', b'not png'), ('../bill.png', PNG), ('bad.txt', PNG), ('x\n.png', PNG), ('x' * 201 + '.png', PNG)]:
        assert upload(client, cid, 2, name, content).status_code == 422
    assert upload(client, cid, 2, content=b'x' * (5 * 1024 * 1024 + 1)).status_code == 413
    assert client.get(f'/api/claims/{cid}').json() == before
    assert submit_claim(client, cid, 2).status_code == 200
    assert upload(client, cid, 3).status_code == 409
    assert client.delete(f'/api/claims/{cid}/receipt?version=3').status_code == 409
    response = approver.get(f'/api/claims/{cid}/receipts/{rid}')
    assert response.content == PNG
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['content-disposition'].startswith('inline;')
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'private, no-store'


def test_rejected_replacement_detach_and_historical_read(client, approver):
    cid = create_claim(client, claim_payload(merchant='Original')).json()['id']
    original = upload(client, cid, 1).json()['receipt']
    assert submit_claim(client, cid, 2).status_code == 200
    assert review_claim(approver, cid, {'version': 3, 'decision': 'REJECTED', 'comment': 'Fix'}).status_code == 200
    corrected = upload(client, cid, 4, filename='new.png').json()
    assert (corrected['status'], corrected['version'], corrected['approved_amount_paise']) == ('DRAFT', 5, None)
    assert corrected['receipt']['id'] != original['id']
    assert client.patch(f'/api/claims/{cid}', json={**claim_payload(merchant='New'), 'version': 5}).status_code == 200
    assert client.delete(f'/api/claims/{cid}/receipt?version=6').json()['receipt'] is None
    assert client.delete(f'/api/claims/{cid}/receipt?version=7').status_code == 409
    before = client.get(f'/api/claims/{cid}').json()
    assert submit_claim(client, cid, 7).status_code == 422
    assert client.get(f'/api/claims/{cid}').json() == before
    reattached = upload(client, cid, 7).json()
    assert submit_claim(client, cid, reattached['version']).status_code == 200
    history = client.get(f'/api/claims/{cid}').json()['history']
    assert [e['action'] for e in history] == ['CREATED', 'RECEIPT_ATTACHED', 'SUBMITTED', 'REJECTED', 'RECEIPT_REPLACED', 'EDITED', 'RECEIPT_REMOVED', 'RECEIPT_ATTACHED', 'SUBMITTED']
    assert history[3]['receipt'] == original and history[3]['merchant'] == 'Original'
    assert history[4]['receipt'] == corrected['receipt'] and history[6]['receipt'] is None
    assert approver.get(f"/api/claims/{cid}/receipts/{original['id']}").content == PNG


def test_legacy_rejected_without_receipt_cannot_resubmit(application, client):
    cid = create_claim(client).json()['id']
    connection = application.state.database.connect()
    try:
        connection.execute("UPDATE claims SET status='REJECTED' WHERE id=?", (cid,))
    finally:
        connection.close()
    before = client.get(f'/api/claims/{cid}').json()
    assert 'submit' in before['allowed_actions'] and before['submission_errors']
    assert submit_claim(client, cid, before['version']).status_code == 422
    assert client.get(f'/api/claims/{cid}').json() == before


def test_pdf_cascade_and_transaction_rollback(application, client, monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    pdf = io.BytesIO()
    writer.write(pdf)
    cid = create_claim(client).json()['id']
    def fail(*args, **kwargs):
        raise RuntimeError('event failure')
    with monkeypatch.context() as m:
        m.setattr(store, 'append_event', fail)
        with pytest.raises(RuntimeError):
            upload(client, cid, 1)
    connection = application.state.database.connect()
    try:
        assert connection.execute('SELECT count(*) FROM claim_receipts').fetchone()[0] == 0
    finally:
        connection.close()
    attached = upload(client, cid, 1, 'résumé.pdf', pdf.getvalue()).json()
    rid = attached['receipt']['id']
    download = client.get(f'/api/claims/{cid}/receipts/{rid}')
    assert download.content == pdf.getvalue()
    assert download.headers['content-disposition'].startswith('attachment;')
    assert "filename*=UTF-8''r%C3%A9sum%C3%A9.pdf" in download.headers['content-disposition']
    assert client.delete(f'/api/claims/{cid}?version=2').status_code == 204
    connection = application.state.database.connect()
    try:
        assert connection.execute('SELECT count(*) FROM claim_receipts').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM claim_events').fetchone()[0] == 0
    finally:
        connection.close()


def test_legacy_schema_migration_is_additive(tmp_path):
    path = tmp_path / 'old.sqlite3'
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute("INSERT INTO users VALUES (1, 'Employee One', 'EMPLOYEE')")
    connection.execute("INSERT INTO claims (owner_id, title, description, category, expense_date, amount_paise, status, created_at, updated_at, version, idempotency_key, creation_payload) VALUES (1, 'Old', 'Old', 'Other', '2025-01-01', 100, 'DRAFT', 'then', 'then', 1, 'old', '{}')")
    connection.commit()
    connection.close()
    database = Database(path)
    database.initialize()
    database.initialize()
    connection = database.connect()
    try:
        row = connection.execute('SELECT title, merchant, current_receipt_id FROM claims').fetchone()
        assert tuple(row) == ('Old', '', None)
        assert connection.execute('SELECT count(*) FROM claim_receipts').fetchone()[0] == 0
    finally:
        connection.close()


def test_upload_and_submit_compete_for_same_version(client):
    cid = create_claim(client).json()['id']
    initial = upload(client, cid, 1).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        upload_future = pool.submit(upload, client, cid, initial['version'], 'replacement.png')
        submit_future = pool.submit(submit_claim, client, cid, initial['version'])
        statuses = sorted([upload_future.result().status_code, submit_future.result().status_code])
    assert statuses == [200, 409]
    detail = client.get(f'/api/claims/{cid}').json()
    assert detail['version'] == 3 and len(detail['history']) == 3
    assert detail['history'][0]['action'] == 'CREATED'
    assert detail['history'][1]['action'] == 'RECEIPT_ATTACHED'
    if detail['status'] == 'DRAFT':
        assert detail['history'][2]['action'] == 'RECEIPT_REPLACED'
        assert detail['receipt']['id'] != initial['receipt']['id']
    else:
        assert detail['status'] == 'SUBMITTED'
        assert detail['history'][2]['action'] == 'SUBMITTED'
        assert detail['receipt'] == initial['receipt']


def test_pdf_limits_and_truncated_image(client):
    cid = create_claim(client).json()['id']
    writer = PdfWriter()
    for _ in range(11):
        writer.add_blank_page(width=10, height=10)
    output = io.BytesIO()
    writer.write(output)
    assert upload(client, cid, 1, 'many.pdf', output.getvalue()).status_code == 422
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt('secret')
    output = io.BytesIO()
    writer.write(output)
    assert upload(client, cid, 1, 'locked.pdf', output.getvalue()).status_code == 422
    assert upload(client, cid, 1, 'truncated.png', PNG[:40]).status_code == 422
    large_image = io.BytesIO()
    Image.new('1', (4500, 4500)).save(large_image, format='PNG')
    assert upload(client, cid, 1, 'large.png', large_image.getvalue()).status_code == 422
    assert client.get(f'/api/claims/{cid}').json()['version'] == 1


def test_stream_limit_without_content_length(client):
    cid = create_claim(client).json()['id']
    def chunks():
        for _ in range(6):
            yield b'x' * (1024 * 1024)
    response = client.post(f'/api/claims/{cid}/receipt?version=1&filename=bill.png', content=chunks())
    assert response.status_code == 413
    assert client.get(f'/api/claims/{cid}').json()['version'] == 1


@pytest.mark.parametrize('streamed', [False, True])
def test_exact_receipt_byte_boundary(client, streamed):
    cid = create_claim(client).json()['id']
    content = PNG + b'\x00' * (5_000_000 - len(PNG))
    def body(data):
        return iter([data[:2_500_000], data[2_500_000:]]) if streamed else data
    assert upload(client, cid, 1, content=body(content + b'\x00')).status_code == 413
    response = upload(client, cid, 1, content=body(content))
    assert response.status_code == 200
    assert response.json()['receipt']['size'] == 5_000_000
