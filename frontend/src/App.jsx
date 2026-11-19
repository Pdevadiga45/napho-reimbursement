import { useEffect, useMemo, useRef, useState } from 'react';
import { formatRupees, parseRupees } from './money.js';

const FILTERS = [
  { id: 'ALL', label: 'All claims' },
  { id: 'SUBMITTED', label: 'Needs review' },
  { id: 'APPROVED', label: 'Approved' },
  { id: 'REJECTED', label: 'Rejected' },
  { id: 'PAID', label: 'Paid' },
  { id: 'REVIEWED', label: 'Reviewed history' },
];
const MY_FILTERS = [
  { id: 'ALL', label: 'All claims' },
  { id: 'DRAFT', label: 'Drafts' },
  { id: 'SUBMITTED', label: 'Submitted' },
  ...FILTERS.slice(2, 5),
];

const STATUS_LABELS = {
  DRAFT: 'Draft',
  SUBMITTED: 'Submitted',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  PAID: 'Paid',
};

const ACTION_LABELS = {
  CREATED: 'Draft created',
  EDITED: 'Claim edited',
  SUBMITTED: 'Submitted for review',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  PAID: 'Marked as paid',
};

function apiErrorMessage(payload) {
  const detail = payload?.detail ?? payload?.message;
  if (Array.isArray(detail)) {
    return detail.map((entry) => entry?.msg || JSON.stringify(entry)).join(' · ');
  }
  if (typeof detail === 'string') return detail;
  return 'The request could not be completed. Please try again.';
}

async function api(path, userId, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && (path.startsWith('/api/claims') || path.startsWith('/api/approvals'))) window.dispatchEvent(new Event('napho-session-expired'));
    const error = new Error(apiErrorMessage(payload));
    error.status = response.status;
    throw error;
  }
  return payload;
}

function Login({ onLogin, busy, message }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="wordmark"><span className="wordmark-name">Napho</span><span className="wordmark-divider">/</span><span>Expenses</span></div>
        <h1>Sign in</h1>
        <p className="auth-intro">Sign in to manage your expense claims.</p>
        <form className="auth-form" onSubmit={(event) => { event.preventDefault(); onLogin(username, password); }}>
          <label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
          <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
          {message && <p className="auth-message" role="alert">{message}</p>}
          <button className="button button-primary" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
        <div className="mock-accounts"><strong>Mock accounts</strong><p>Employee: employee / employee123</p><p>Manager: manager / manager123</p></div>
      </div>
    </main>
  );
}

function todayString() {
  return new Date().toISOString().slice(0, 10);
}

function rupeesInput(paise) {
  const whole = Math.floor(paise / 100);
  const fraction = String(paise % 100).padStart(2, '0');
  return `${whole}.${fraction}`;
}

function dateLabel(value) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium' }).format(new Date(`${value}T00:00:00`));
}

function timeLabel(value) {
  if (!value) return 'Time unavailable';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function actionLabel(action) {
  return ACTION_LABELS[action] || action;
}

function ClaimForm({ values, onChange, onSubmit, onCancel, submitLabel, error, busy, saving }) {
  const update = (field) => (event) => onChange({ ...values, [field]: event.target.value });
  return (
    <form className="claim-form" onSubmit={onSubmit}>
      <div className="form-heading">
        <h3>{submitLabel === 'Create draft' ? 'New expense claim' : 'Claim details'}</h3>
        <p>Enter one INR expense. You can review a draft before submitting.</p>
      </div>
      <label>
        Claim title
        <input maxLength="120" name="title" value={values.title} onChange={update('title')} required />
      </label>
      <label>
        Category
        <input maxLength="80" name="category" value={values.category} onChange={update('category')} required />
      </label>
      <label>
        Expense date
        <input type="date" name="expense_date" max={todayString()} value={values.expense_date} onChange={update('expense_date')} required />
      </label>
      <label>
        Requested amount (₹)
        <input
          autoComplete="off"
          inputMode="decimal"
          name="amount"
          placeholder="0.00"
          value={values.amount}
          onChange={update('amount')}
          required
        />
      </label>
      <label>
        Description
        <textarea maxLength="2000" name="description" rows="3" value={values.description} onChange={update('description')} required />
      </label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions">
        <button className="button button-primary" type="submit" disabled={busy}>{saving ? 'Saving…' : submitLabel}</button>
        <button className="button button-quiet" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

function History({ events }) {
  return (
    <section className="history-section" aria-labelledby="history-heading">
      <div className="section-title-row">
        <h3 id="history-heading">History</h3>
        <span className="muted">{events.length} {events.length === 1 ? 'event' : 'events'}</span>
      </div>
      {events.length === 0 ? (
        <p className="empty-inline">No history has been recorded.</p>
      ) : (
        <ol className="timeline">
          {events.map((event) => (
            <li className="timeline-event" key={event.id}>
              <span className={`timeline-dot status-${event.status.toLowerCase()}`} aria-hidden="true" />
              <div className="timeline-content">
                <div className="timeline-heading">
                  <strong>{actionLabel(event.action)}</strong>
                  <time dateTime={event.event_at}>{timeLabel(event.event_at)}</time>
                </div>
                <p className="event-actor">{event.actor_name}</p>
                <p className="event-expense">
                  {event.title} · {event.category} · {dateLabel(event.expense_date)} · Requested {formatRupees(event.amount_paise)}
                  {event.approved_amount_paise !== null && event.approved_amount_paise !== undefined && (
                    <> · Approved {formatRupees(event.approved_amount_paise)}</>
                  )}
                </p>
                {event.comment && <p className="event-comment">“{event.comment}”</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [workspace, setWorkspace] = useState('claims');
  const [authChecking, setAuthChecking] = useState(true);
  const [authBusy, setAuthBusy] = useState(false);
  const [authMessage, setAuthMessage] = useState('');
  const [claims, setClaims] = useState([]);
  const [claimsUserId, setClaimsUserId] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailUserId, setDetailUserId] = useState(null);
  const [listLoading, setListLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [notice, setNotice] = useState('');
  const [conflictNotice, setConflictNotice] = useState('');
  const [pendingMutation, setPendingMutation] = useState('');
  const [filter, setFilter] = useState('ALL');
  const [formMode, setFormMode] = useState(null);
  const [formValues, setFormValues] = useState(null);
  const [formError, setFormError] = useState('');
  const [reviewComment, setReviewComment] = useState('');
  const [approvedAmount, setApprovedAmount] = useState('');
  const [listRevision, setListRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const listRequestId = useRef(0);
  const detailRequestId = useRef(0);
  const creationKey = useRef(null);

  const userId = user?.id ?? null;
  const canReview = user?.permissions?.includes('review_claims');
  const scopeKey = `${userId}:${workspace}`;
  const visibleClaims = claimsUserId === scopeKey ? claims : [];
  const currentDetail = detailUserId === scopeKey && detail?.id === selectedId ? detail : null;
  const actions = currentDetail?.allowed_actions || [];

  useEffect(() => {
    const controller = new AbortController();
    api('/api/auth/me', null, { signal: controller.signal })
      .then(setUser)
      .catch((error) => {
        if (error.name !== 'AbortError' && error.status !== 401) setAuthMessage('Unable to check your session. Please sign in.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setAuthChecking(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const expire = () => { clearSession(); setAuthMessage('Your session has expired. Please sign in again.'); };
    window.addEventListener('napho-session-expired', expire);
    return () => window.removeEventListener('napho-session-expired', expire);
  }, []);

  useEffect(() => {
    if (userId === null) return undefined;
    const requestId = ++listRequestId.current;
    const controller = new AbortController();
    setListLoading(true);
    setListError('');
    setClaims([]);
    setClaimsUserId(null);
    api(workspace === 'claims' ? '/api/claims' : '/api/approvals', userId, { signal: controller.signal })
      .then((result) => {
        if (requestId !== listRequestId.current) return;
        setClaims(result);
        setClaimsUserId(scopeKey);
        setSelectedId((current) => result.some((claim) => claim.id === current) ? current : result[0]?.id ?? null);
      })
      .catch((error) => {
        if (error.name !== 'AbortError' && requestId === listRequestId.current) setListError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted && requestId === listRequestId.current) setListLoading(false);
      });
    return () => {
      controller.abort();
      listRequestId.current += 1;
    };
  }, [userId, workspace, listRevision]);

  useEffect(() => {
    if (userId === null || selectedId === null) {
      setDetail(null);
      setDetailUserId(null);
      setDetailLoading(false);
      return undefined;
    }
    const requestId = ++detailRequestId.current;
    const controller = new AbortController();
    setDetail(null);
    setDetailUserId(null);
    setDetailLoading(true);
    setDetailError('');
    api(`/api/claims/${selectedId}`, userId, { signal: controller.signal })
      .then((result) => {
        if (requestId !== detailRequestId.current) return;
        setDetail(result);
        setDetailUserId(scopeKey);
        setApprovedAmount(rupeesInput(result.amount_paise));
        setReviewComment('');
      })
      .catch((error) => {
        if (error.name !== 'AbortError' && requestId === detailRequestId.current) setDetailError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted && requestId === detailRequestId.current) setDetailLoading(false);
      });
    return () => {
      controller.abort();
      detailRequestId.current += 1;
    };
  }, [userId, workspace, selectedId, detailRevision]);

  const filteredClaims = useMemo(() => {
    if (filter === 'ALL') return visibleClaims;
    if (filter === 'REVIEWED') return visibleClaims.filter((claim) => claim.has_review_history);
    return visibleClaims.filter((claim) => claim.status === filter);
  }, [visibleClaims, filter]);

  function refreshData() {
    setListRevision((revision) => revision + 1);
    setDetailRevision((revision) => revision + 1);
  }

  async function runMutation(label, operation, successMessage, afterSuccess) {
    if (authBusy || pendingMutation) return null;
    setPendingMutation(label);
    setNotice('');
    setConflictNotice('');
    setListError('');
    setDetailError('');
    try {
      const result = await operation();
      setNotice(successMessage);
      afterSuccess?.(result);
      refreshData();
      return result;
    } catch (error) {
      if (error.status === 401) {
        return null;
      } else if (error.status === 409) {
        setConflictNotice('This claim changed since it was opened. The current version has been refreshed; review it before trying again.');
        setFormMode(null);
        setFormValues(null);
        setFormError('');
        refreshData();
      } else {
        setDetailError(error.message || 'The request could not be completed.');
        if (formMode) setFormError(error.message || 'The request could not be completed.');
      }
      return null;
    } finally {
      setPendingMutation('');
    }
  }

  function startNewClaim() {
    setNotice('');
    setConflictNotice('');
    setDetailError('');
    setFormError('');
    setFormValues({ title: '', description: '', category: '', expense_date: todayString(), amount: '' });
    setFormMode('create');
  }

  function startEditingClaim() {
    if (!currentDetail) return;
    setNotice('');
    setDetailError('');
    setFormError('');
    setFormValues({
      title: currentDetail.title,
      description: currentDetail.description,
      category: currentDetail.category,
      expense_date: currentDetail.expense_date,
      amount: rupeesInput(currentDetail.amount_paise),
    });
    setFormMode('edit');
  }

  function cancelForm() {
    setFormMode(null);
    setFormValues(null);
    setFormError('');
  }

  async function saveClaim(event) {
    event.preventDefault();
    if (!formValues || !userId) return;
    let payload;
    try {
      const amount_paise = parseRupees(formValues.amount);
      if (formValues.title.trim().length === 0 || formValues.category.trim().length === 0 || formValues.description.trim().length === 0) {
        throw new Error('Enter a title, category, and description.');
      }
      if (!formValues.expense_date || formValues.expense_date > todayString()) throw new Error('Choose a valid expense date that is not in the future.');
      payload = {
        title: formValues.title,
        description: formValues.description,
        category: formValues.category,
        expense_date: formValues.expense_date,
        amount_paise,
      };
    } catch (error) {
      setFormError(error.message);
      return;
    }

    const creating = formMode === 'create';
    let headers;
    if (creating) {
      const signature = JSON.stringify(payload);
      if (!creationKey.current || creationKey.current.signature !== signature) {
        creationKey.current = { signature, key: crypto.randomUUID() };
      }
      headers = { 'Idempotency-Key': creationKey.current.key };
    }
    const result = await runMutation(
      creating ? 'Creating draft' : 'Saving changes',
      () => creating
        ? api('/api/claims', userId, { method: 'POST', headers, body: JSON.stringify(payload) })
        : api(`/api/claims/${currentDetail.id}`, userId, { method: 'PATCH', body: JSON.stringify({ ...payload, version: currentDetail.version }) }),
      creating ? 'Draft saved.' : 'Claim changes saved.',
      (saved) => {
        if (creating) creationKey.current = null;
        setFormMode(null);
        setFormValues(null);
        setFormError('');
        setSelectedId(saved.id);
      },
    );
    if (result) setFormMode(null);
  }

  async function deleteDraft() {
    if (!currentDetail || !window.confirm('Delete this draft? This action cannot be undone.')) return;
    const id = currentDetail.id;
    const result = await runMutation(
      'Deleting draft',
      () => api(`/api/claims/${id}?version=${currentDetail.version}`, userId, { method: 'DELETE' }),
      'Draft deleted.',
      () => {
        setSelectedId(null);
        setFormMode(null);
      },
    );
    if (result === null) return;
  }

  async function submitClaim() {
    if (!currentDetail) return;
    await runMutation(
      'Submitting claim',
      () => api(`/api/claims/${currentDetail.id}/submit`, userId, { method: 'POST', body: JSON.stringify({ version: currentDetail.version }) }),
      'Claim submitted for review.',
    );
  }

  async function approveClaim(event) {
    event.preventDefault();
    if (!currentDetail) return;
    let approved_amount_paise;
    try {
      approved_amount_paise = parseRupees(approvedAmount);
      if (approved_amount_paise > currentDetail.amount_paise) throw new Error('Approved amount cannot be greater than the requested amount.');
      if (approved_amount_paise < currentDetail.amount_paise && reviewComment.trim().length === 0) {
        throw new Error('Add a comment when approving less than the requested amount.');
      }
    } catch (error) {
      setDetailError(error.message);
      return;
    }
    await runMutation(
      'Approving claim',
      () => api(`/api/claims/${currentDetail.id}/review`, userId, {
        method: 'POST',
        body: JSON.stringify({
          version: currentDetail.version,
          decision: 'APPROVED',
          approved_amount_paise,
          ...(reviewComment.trim() ? { comment: reviewComment.trim() } : {}),
        }),
      }),
      approved_amount_paise < currentDetail.amount_paise ? 'Partial approval recorded.' : 'Claim approved.',
    );
  }

  async function rejectClaim() {
    if (!currentDetail) return;
    if (!reviewComment.trim()) {
      setDetailError('Add a reason before rejecting this claim.');
      return;
    }
    await runMutation(
      'Rejecting claim',
      () => api(`/api/claims/${currentDetail.id}/review`, userId, {
        method: 'POST',
        body: JSON.stringify({ version: currentDetail.version, decision: 'REJECTED', comment: reviewComment.trim() }),
      }),
      'Claim returned with a correction reason.',
    );
  }

  async function markPaid() {
    if (!currentDetail) return;
    await runMutation(
      'Marking claim paid',
      () => api(`/api/claims/${currentDetail.id}/pay`, userId, { method: 'POST', body: JSON.stringify({ version: currentDetail.version }) }),
      'Claim marked as paid.',
    );
  }

  function clearSession() {
    listRequestId.current += 1;
    detailRequestId.current += 1;
    creationKey.current = null;
    setUser(null);
    setWorkspace('claims');
    setSelectedId(null);
    setClaims([]);
    setClaimsUserId(null);
    setDetail(null);
    setDetailUserId(null);
    setFilter('ALL');
    setFormMode(null);
    setFormValues(null);
    setFormError('');
    setReviewComment('');
    setApprovedAmount('');
    setListLoading(false);
    setDetailLoading(false);
    setPendingMutation('');
    setListError('');
    setDetailError('');
    setNotice('');
    setConflictNotice('');
  }

  function changeWorkspace(next) {
    if (authBusy || pendingMutation || next === workspace) return;
    listRequestId.current += 1;
    detailRequestId.current += 1;
    setSelectedId(null);
    setClaims([]);
    setClaimsUserId(null);
    setDetail(null);
    setDetailUserId(null);
    setFilter('ALL');
    setFormMode(null);
    setFormValues(null);
    setFormError('');
    setReviewComment('');
    setApprovedAmount('');
    setListError('');
    setDetailError('');
    setNotice('');
    setConflictNotice('');
    setWorkspace(next);
  }

  async function login(username, password) {
    if (authBusy) return;
    setAuthBusy(true);
    setAuthMessage('');
    try {
      const signedIn = await api('/api/auth/login', null, { method: 'POST', body: JSON.stringify({ username, password }) });
      setUser(signedIn);
    } catch (error) {
      setAuthMessage(error.status === 401 ? 'Invalid username or password.' : 'Unable to sign in. Please try again.');
    } finally { setAuthBusy(false); }
  }

  async function logout() {
    if (authBusy || pendingMutation) return;
    setAuthBusy(true);
    try {
      await api('/api/auth/logout', null, { method: 'POST' });
      clearSession();
      setAuthMessage('');
    } catch {
      setDetailError('Unable to log out. Please try again.');
    } finally { setAuthBusy(false); }
  }

  const hasPartialApproval = currentDetail?.approved_amount_paise !== null
    && currentDetail?.approved_amount_paise !== undefined
    && currentDetail.approved_amount_paise < currentDetail.amount_paise;

  if (authChecking) return <main className="auth-page"><p role="status">Checking your session…</p></main>;
  if (!user) return <Login onLogin={login} busy={authBusy} message={authMessage} />;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="wordmark" href="/" aria-label="Napho Expenses home">
          <span className="wordmark-name">Napho</span><span className="wordmark-divider">/</span><span>Expenses</span>
        </a>
        <div className="identity-control">
          <span className="identity-name">{user.name}</span>
          <button className="button button-secondary" type="button" onClick={logout} disabled={authBusy || Boolean(pendingMutation)}>{authBusy ? 'Logging out…' : 'Log out'}</button>
        </div>
      </header>

      <main className="page-content">
        <nav className="workspace-nav" aria-label="Workspaces">
          <button type="button" aria-current={workspace === 'claims' ? 'page' : undefined} disabled={authBusy || Boolean(pendingMutation)} onClick={() => changeWorkspace('claims')}>My claims</button>
          {canReview && <button type="button" aria-current={workspace === 'approvals' ? 'page' : undefined} disabled={authBusy || Boolean(pendingMutation)} onClick={() => changeWorkspace('approvals')}>Approvals</button>}
        </nav>
        <section className="page-heading" aria-labelledby="page-title">
          <div>
            <p className="eyebrow">REIMBURSEMENTS</p>
            <h1 id="page-title">{workspace === 'claims' ? 'My claims' : 'Approvals'}</h1>
            <p className="page-subtitle">{workspace === 'claims' ? 'Submit expenses and track your reimbursements.' : 'Review and process claims submitted by others.'}</p>
          </div>
          {workspace === 'claims' && user.permissions?.includes('create_claims') && (
            <button className="button button-primary" type="button" onClick={startNewClaim} disabled={authBusy || Boolean(pendingMutation)}>
              <span aria-hidden="true">＋</span> New claim
            </button>
          )}
        </section>

        {listError && <div className="banner banner-error" role="alert">{listError}</div>}
        {notice && <div className="banner banner-success" role="status">{notice}</div>}
        {conflictNotice && <div className="banner banner-warning" role="alert">{conflictNotice}</div>}

        <nav className="filter-bar" aria-label="Claim status filters">
          {(workspace === 'claims' ? MY_FILTERS : FILTERS).map((item) => (
            <button
              aria-pressed={filter === item.id}
              className={`filter-button ${filter === item.id ? 'filter-active' : ''}`}
              key={item.id}
              type="button"
              onClick={() => setFilter(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="workspace-grid">
          <section className="list-card" aria-labelledby="claims-list-heading">
            <div className="card-heading">
              <div>
                <h2 id="claims-list-heading">{workspace === 'approvals' ? 'Team claims' : 'Your claims'}</h2>
                <p>{workspace === 'approvals' ? 'Submitted and reviewed expenses' : 'Current and past expenses'}</p>
              </div>
              <span className="count-pill" aria-label={`${filteredClaims.length} claims`}>{filteredClaims.length}</span>
            </div>
            {listLoading ? (
              <p className="list-loading" role="status">Loading claims…</p>
            ) : userId === null ? (
              <div className="empty-state"><h3>Claims are unavailable</h3><p>Check the connection and reload this page.</p></div>
            ) : visibleClaims.length === 0 ? (
              <div className="empty-state">
                <span className="empty-mark" aria-hidden="true">—</span>
                <h3>No claims yet</h3>
                <p>{workspace === 'approvals' ? 'Submitted or reviewed claims will appear here.' : 'Create a draft to start tracking an expense.'}</p>
                {workspace === 'claims' && user.permissions?.includes('create_claims') && <button className="button button-secondary" type="button" onClick={startNewClaim} disabled={authBusy || Boolean(pendingMutation)}>Create a claim</button>}
              </div>
            ) : filteredClaims.length === 0 ? (
              <div className="empty-state empty-state-compact"><p>No claims match this view.</p></div>
            ) : (
              <div className="table-scroll">
                <table className="claims-table">
                  <thead>
                    <tr><th scope="col">Claim</th><th scope="col">Owner</th><th scope="col">Requested</th><th scope="col">Status</th></tr>
                  </thead>
                  <tbody>
                    {filteredClaims.map((claim) => (
                      <tr className={claim.id === selectedId ? 'selected-row' : ''} key={claim.id}>
                        <td>
                          <button
                            className="claim-select"
                            type="button"
                            aria-current={claim.id === selectedId ? 'true' : undefined}
                            onClick={() => {
                              setConflictNotice('');
                              setSelectedId(claim.id);
                              setFormMode(null);
                              setFormValues(null);
                              setDetailError('');
                            }}
                          >
                            <span>{claim.title}</span><small>{claim.category} · {dateLabel(claim.expense_date)}</small>
                          </button>
                        </td>
                        <td>{claim.owner_name}</td>
                        <td className="amount-cell">{formatRupees(claim.amount_paise)}</td>
                        <td><span className={`status-chip status-${claim.status.toLowerCase()}`}>{STATUS_LABELS[claim.status]}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {workspace === 'approvals' && <p className="list-footnote">New drafts are private; previously reviewed corrections remain accessible.</p>}
          </section>

          <section className="detail-card" aria-labelledby="detail-heading">
            {formMode && formValues ? (
              <ClaimForm
                values={formValues}
                onChange={setFormValues}
                onSubmit={saveClaim}
                onCancel={cancelForm}
                submitLabel={formMode === 'create' ? 'Create draft' : 'Save changes'}
                error={formError}
                busy={authBusy || Boolean(pendingMutation)}
                saving={Boolean(pendingMutation)}
              />
            ) : detailLoading ? (
              <div className="detail-placeholder" role="status"><span className="loading-dot" /> Loading claim…</div>
            ) : !currentDetail ? (
              detailError
                ? <div className="detail-placeholder detail-placeholder-error" role="alert">{detailError}</div>
                : <div className="detail-placeholder">
                  <span className="detail-placeholder-icon" aria-hidden="true">↖</span>
                  <h2 id="detail-heading">Claim details</h2>
                  <p>Select a claim to view its details and history.</p>
                </div>
            ) : (
              <article className="claim-detail">
                <div className="detail-topline">
                  <div>
                    <p className="eyebrow">CLAIM #{currentDetail.id}</p>
                    <h2 id="detail-heading">{currentDetail.title}</h2>
                  </div>
                  <span className={`status-chip status-${currentDetail.status.toLowerCase()}`}>{STATUS_LABELS[currentDetail.status]}</span>
                </div>
                {workspace === 'approvals' && <p className="owner-line">Submitted by <strong>{currentDetail.owner_name}</strong></p>}

                <div className="amount-summary">
                  <div><span>Requested</span><strong>{formatRupees(currentDetail.amount_paise)}</strong></div>
                  <div><span>Approved</span><strong>{formatRupees(currentDetail.approved_amount_paise)}</strong></div>
                  {hasPartialApproval && (
                    <div><span>Not approved</span><strong>{formatRupees(currentDetail.amount_paise - currentDetail.approved_amount_paise)}</strong></div>
                  )}
                </div>
                {hasPartialApproval && <p className="partial-label">Partial approval · {formatRupees(currentDetail.approved_amount_paise)} approved of {formatRupees(currentDetail.amount_paise)} requested.</p>}

                <dl className="claim-facts">
                  <div><dt>Category</dt><dd>{currentDetail.category}</dd></div>
                  <div><dt>Expense date</dt><dd>{dateLabel(currentDetail.expense_date)}</dd></div>
                  <div className="description-fact"><dt>Description</dt><dd>{currentDetail.description}</dd></div>
                </dl>

                {detailError && !formMode && <div className="banner banner-error detail-banner" role="alert">{detailError}</div>}

                {workspace === 'claims' && currentDetail.status === 'REJECTED' && (
                  <div className="correction-note"><strong>Correction needed</strong><span>Review the feedback in history, then edit and resubmit.</span></div>
                )}

                {workspace === 'claims' && currentDetail.status === 'SUBMITTED' && <p className="readonly-hint">Another manager must review this claim.</p>}
                {actions.includes('review') && (
                  <form className="review-panel" onSubmit={approveClaim}>
                    <h3>Review this claim</h3>
                    <label>
                      Approved amount (₹)
                      <input inputMode="decimal" value={approvedAmount} onChange={(event) => setApprovedAmount(event.target.value)} required />
                    </label>
                    <label>
                      Review comment <span className="label-help">(required for partial approval or rejection)</span>
                      <textarea rows="2" value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} maxLength="1000" />
                    </label>
                    <div className="form-actions">
                      <button className="button button-primary" type="submit" disabled={authBusy || Boolean(pendingMutation)}>{pendingMutation === 'Approving claim' ? 'Approving…' : 'Approve claim'}</button>
                      <button className="button button-danger-outline" type="button" onClick={rejectClaim} disabled={authBusy || Boolean(pendingMutation)}>{pendingMutation === 'Rejecting claim' ? 'Rejecting…' : 'Reject claim'}</button>
                    </div>
                  </form>
                )}

                {actions.includes('pay') && (
                  <div className="review-panel paid-panel">
                    <h3>Approval recorded</h3>
                    <p>The approved amount is ready to be marked as paid.</p>
                    <button className="button button-primary" type="button" onClick={markPaid} disabled={authBusy || Boolean(pendingMutation)}>{pendingMutation === 'Marking claim paid' ? 'Marking paid…' : 'Mark as paid'}</button>
                  </div>
                )}

                {(actions.includes('submit') || actions.includes('edit') || actions.includes('delete')) && (
                  <div className="employee-actions">
                    {actions.includes('submit') && <button className="button button-primary" type="button" onClick={submitClaim} disabled={authBusy || Boolean(pendingMutation)}>{pendingMutation === 'Submitting claim' ? 'Submitting…' : 'Submit claim'}</button>}
                    {actions.includes('edit') && <button className="button button-secondary" type="button" onClick={startEditingClaim} disabled={authBusy || Boolean(pendingMutation)}>{currentDetail.status === 'REJECTED' ? 'Edit correction' : 'Edit draft'}</button>}
                    {actions.includes('delete') && <button className="button button-danger-text" type="button" onClick={deleteDraft} disabled={authBusy || Boolean(pendingMutation)}>{pendingMutation === 'Deleting draft' ? 'Deleting…' : 'Delete draft'}</button>}
                  </div>
                )}
                {actions.includes('submit') && currentDetail.status === 'DRAFT' && <p className="readonly-hint">Once submitted, this claim is locked while it is under review.</p>}
                <History events={currentDetail.history || []} />
              </article>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
