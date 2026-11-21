# Napho Reimbursement Management

A React and Python application for creating, reviewing and tracking expense claims.

## Run locally

**Prerequisites:** Git, Python 3.14+, [uv](https://docs.astral.sh/uv/), and Node.js 22.12+ with npm. You don't need a database server, `.env` file, API keys, or GPU.

```sh
git clone https://github.com/Pdevadiga45/napho-reimbursement.git
cd napho-reimbursement
```

**Terminal 1 — backend** (from the repository root):

```sh
cd backend
uv sync --locked
uv run uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

**Terminal 2 — frontend** (open a new terminal at the repository root):

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Keep both terminals running and open [the app](http://127.0.0.1:5173). Vite forwards `/api` requests to the backend on port 8000. To check that the backend is up, open [the API docs](http://127.0.0.1:8000/docs). If a port is already in use, stop the process using it or choose another port. If you change the backend port, set `NAPHO_API_TARGET` to the new backend URL before starting Vite.

| Local demo account | Username | Password |
| --- | --- | --- |
| Employee One | `employee` | `employee123` |
| Manager One | `manager` | `manager123` |

Startup creates `backend/napho.sqlite3` and both mock accounts automatically. Claims and attachments persist there across restarts; a fresh checkout has no claims. Set `NAPHO_DB_PATH` before starting the backend to use a separate database file. These accounts are for local development; don't expose the dev servers publicly.

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
- New claims use Travel and Stay, Meals, Work & Tech, or Others. Expense dates are calendar dates. Editing a historic claim requires a current category when its historic category is no longer supported.

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

## Receipt OCR prototype

The standalone [CPU receipt OCR tool](ocr/README.md) uses RapidOCR to read receipt images and a Python rule-based selector to find the final INR amount. It is not wired into the claims app. The [proof of concept](ocr/EVALUATION.md) shows five receipts whose final totals were extracted correctly.

**Approvals** shows a badge for valid requests whose receipt SHA-256 exactly matches the redacted ₹777.00 sample. The status includes **Recorded OCR result · no live OCR**. Requests at or below the recorded total are covered. Only requests above it are flagged. [Redacted receipt sample](ocr/examples/demo-receipt.png)

## Known limitations

- Receipt OCR is a proof of concept. It has not been validated on 100–150+ diverse digital and physical receipts or load-tested for scale.
- SQLite permits one writer at a time. Concurrent writes are handled safely, but contention can return a retryable conflict. High-concurrency performance has not been load-tested.

OpenAI Codex was used to assist with implementation and unit tests.
