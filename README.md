# Napho Reimbursement Management

A React and Python application for creating, reviewing and tracking expense claims.

## Setup

Requires Python 3.14+, [uv](https://docs.astral.sh/uv/), and Node.js 22.12+ with npm. From the repository root, start each service in a separate terminal.

```sh
cd backend
uv sync --locked
uv run uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open http://127.0.0.1:5173. API documentation is available at http://127.0.0.1:8000/docs.

Add an optional merchant and choose a receipt when creating or editing a claim. A receipt is required before submission or resubmission, but not to save a draft. Owners can attach, replace or remove it while the claim is editable. PNG, JPG, JPEG and PDF files are supported up to 5 MB (5,000,000 bytes), with images limited to 20 megapixels and unencrypted PDFs to 10 pages. Submitted claims show the receipt to authorized reviewers. If an upload fails after saving, the form retains the saved draft for retry.

| Account | Username | Password |
| --- | --- | --- |
| Employee One | `employee` | `employee123` |
| Manager One | `manager` | `manager123` |

Startup creates the schema and mock accounts. Data persists in `backend/napho.sqlite3`; set `NAPHO_DB_PATH` to use another location. Claims are not preloaded in a fresh checkout.

## Structure

- `frontend/`: React UI; communicates with the backend through `/api` requests.
- `backend/app.py`: FastAPI endpoints, validation, workflow decisions and HTTP responses.
- `backend/store.py`: SQLite queries and claim persistence.
- `backend/policy.py`: permissions and ownership/state-based action rules.
- `backend/db.py`: SQLite schema, initialization, connections and transactions.

## Data model and design decisions

- **Users** hold identity and role. **Claims** belong to a user and hold expense details, status, requested/approved amounts and a version. **Claim events** link each claim to the acting user and preserve expense snapshots, decisions, comments and UTC timestamps.
- Money is stored as integer paise, avoiding floating-point rounding. Partial approval preserves the requested amount and records the approved amount separately.
- The backend enforces permissions, ownership and valid states. Managers can submit their own claims but cannot review or mark them paid.
- Transactions and version checks reject stale competing actions with HTTP 409. Per-owner idempotency keys prevent duplicate creation; workflow changes and their history are recorded together.
- Only never-submitted drafts can be deleted. Corrections and resubmissions retain previous decisions and remain accessible to authorized reviewers.
- Receipt bytes are stored privately in SQLite. Claim history records receipt snapshots and retains links to earlier files after replacement or removal.
- New claims use Travel and Stay, Meals, Work & Tech, or Others. Expense date and time is entered in the browser's local timezone and stored as a UTC instant. Historic date-only values remain date-only; editing them requires a time and a current category when the historic category is no longer supported.

## Assumptions

- One expense is one claim; INR is the only currency, and expense dates cannot be in the future.
- Mock logins are sufficient. Marking a claim paid records an outcome; it does not transfer money.
- Rejection and partial approval require a comment. A partial approval has status `APPROVED`.
- Both accounts can manage personal claims. Only the manager can process others' claims; a manager's own claim needs another authorized reviewer.

## Tests

Run these commands from the respective directories after setup:

```sh
cd backend
uv run pytest -q
```

```sh
cd frontend
npx playwright install chromium
npm test
npm run build
```

API tests cover lifecycle transitions, ownership, amounts, history, duplicate requests and concurrent reviews. Frontend tests cover the user workflows, permissions, confirmations, session handling and responsive layout. Playwright uses separate test servers and an isolated database, not the local showcase data.

## Known limitations

- Claim lists show ten records per page, with status filters applied on the server.
- SQLite permits one writer at a time. Concurrent writes are handled safely, but contention can return a retryable conflict. High-concurrency performance has not been load-tested.

OpenAI Codex was used to assist with implementation and unit tests.
