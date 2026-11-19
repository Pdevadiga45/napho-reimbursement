# Napho reimbursement demo

An assignment demo for expense reimbursements. Everyone can claim their own
expenses; selected accounts can also review and pay claims submitted by others.

The backend uses Python, FastAPI, and the standard library `sqlite3` module. The
frontend uses React and Vite.

## Setup

Install Python 3.14 or newer, [uv](https://docs.astral.sh/uv/), and Node.js with
npm. Use Node 22.12 or newer for Vite 7 (Node 26 has also been used).

In one terminal:

```sh
cd backend
uv sync --locked
uv run uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open http://127.0.0.1:5173 and sign in with either mock account:

| Role | Username | Password |
| --- | --- | --- |
| Employee | `employee` | `employee123` |
| Approver | `approver` | `approver123` |

API documentation is
at http://127.0.0.1:8000/docs. Vite proxies `/api` to the backend; set
`NAPHO_API_TARGET` when starting Vite to use a different API address.

The backend initializes the schema and mock users automatically at startup.
Data persists locally in `backend/napho.sqlite3` by default. Set `NAPHO_DB_PATH`
before starting the backend to use another database path.

## Checks

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

Playwright starts its own backend on port 18000 and frontend on port 15173,
with a separate test database under `frontend/test-results`.

## Model and assumptions

My claims contains the signed-in user's own claims. Approvals is available to
reviewers and contains other users' non-private claims, including previously
reviewed claims being corrected. The backend enforces ownership and permissions
and returns `allowed_actions` for each claim; the frontend uses those actions to
display controls. Permissions are additive, with ownership and claim state
constraining each action, rather than a general role-management framework.

| Account | Own claims | Other users' claims |
| --- | --- | --- |
| Employee | Create, edit, submit and track | No approvals access |
| Approver | Create, edit, submit and track | Review submitted claims and mark approved claims paid |

Both accounts are claimants. An approver cannot review or pay their own claim;
with the current two mock accounts, an approver's own submission needs another
approver before it can be completed. Draft deletion and other actions also depend
on claim state and history. These principles are inspired by [Ramp's user roles](https://support.ramp.com/hc/en-us/articles/360042579734-What-are-the-types-of-Ramp-users-), [Ramp's approval policies](https://support.ramp.com/hc/en-us/articles/27132114712723-Setting-up-expense-approval-policies-transactions-and-reimbursements), and [Expensify's expense and report actions](https://help.expensify.com/articles/new-expensify/reports-and-expenses/Expense-and-Report-Actions).

Each expense is one claim. SQLite stores users, claims, and immutable claim event
snapshots with UTC timestamps. Amounts are integer paise; INR is the only currency.
An expense date cannot be in the future. Creation uses a per-owner idempotency
key; transactional updates and version conflicts (HTTP 409) prevent duplicate
reviews and events from stale requests.

A rejected claim can be corrected to a draft and submitted again. A partial
approval remains `APPROVED`, with requested and approved amounts stored
separately. Rejection and partial approval require a comment. Marking a claim
paid records the approved amount; there is no payment processing. Only drafts
that have never been submitted can be deleted, along with their history. New
drafts are private to their owner; approvers can still access previously reviewed
claims while they are being corrected and after resubmission.

Sessions use a signed, HttpOnly development cookie. Set the optional
`NAPHO_SESSION_SECRET` environment variable before starting the backend to replace
the development signing secret; no dotenv file is loaded automatically. The public
mock credentials and development cookie settings are not production security.
This is a single-instance demo: SQLite is suitable for its small local workload,
not a multi-instance deployment. Receipts, production authentication, pagination,
deployment, and migrations are outside the demo. AI is deliberately excluded
pending a separate bonus discussion.
