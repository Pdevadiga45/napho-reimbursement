import { test, expect, request } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { formatRupees, parseRupees } from '../src/money.js';

test('workspace chrome keeps navigation and actions in context', async ({ page }) => {
  await page.goto('/');
  await signIn(page, 'employee');
  const nav = page.getByRole('navigation', { name: 'Workspaces' });
  await expect(page.getByRole('banner').getByRole('navigation', { name: 'Workspaces' })).toHaveCount(1);
  await expect(page.locator('main .workspace-nav')).toHaveCount(0);
  await expect(page.getByText('REIMBURSEMENTS', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Submit expenses and track your reimbursements.')).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1, name: 'My claims' })).toHaveCount(1);
  await expect(page.locator('main h1')).toHaveCount(1);
  await expect(page.locator('.list-card')).toBeVisible();
  await expect(page.locator('.detail-card')).toBeVisible();
  await expect(nav.getByRole('button', { name: 'Approvals' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New claim' })).toBeVisible();
  await switchUser(page, 'manager');
  await expect(nav.getByRole('button', { name: 'Approvals' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toHaveCount(1);
  await expect(page.getByText('Review and process claims submitted by others.')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New claim' })).toHaveCount(0);
  await nav.getByRole('button', { name: 'My claims' }).click();
  await expect(nav.getByRole('button', { name: 'My claims' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('button', { name: 'New claim' })).toBeVisible();
});

for (const width of [320, 390]) {
  test(`mobile workspace chrome fits at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/');
    await signIn(page, 'manager');
    const header = page.getByRole('banner');
    await expect(header.getByText('Manager One')).toBeVisible();
    const brand = await header.getByRole('link', { name: 'Napho Expenses home' }).boundingBox();
    const identity = await header.locator('.identity-control').boundingBox();
    const nav = await header.getByRole('navigation', { name: 'Workspaces' }).boundingBox();
    expect(brand.y).toBeLessThan(identity.y + identity.height);
    expect(identity.y).toBeLessThan(brand.y + brand.height);
    expect(nav.y).toBeGreaterThanOrEqual(brand.y + brand.height);
    expect(nav.y).toBeGreaterThanOrEqual(identity.y + identity.height);
    for (const control of [header.getByRole('button', { name: 'My claims' }), header.getByRole('button', { name: 'Approvals' }), header.getByRole('button', { name: 'Log out' }), page.getByRole('button', { name: 'New claim' })]) {
      await expect(control).toBeVisible();
      const box = await control.boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

test('employee starts in My claims without approvals or view badges', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Username').fill('employee');
  await page.getByLabel('Password').fill('employee123');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('navigation', { name: 'Workspaces' }).getByRole('button', { name: 'My claims' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('button', { name: 'Approvals' })).toHaveCount(0);
  await expect(page.getByText(/Employee view|Approver view/)).toHaveCount(0);
  expect((await page.request.get('/api/approvals')).status()).toBe(403);
});

test('unauthenticated visitors see sign in', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByLabel('Username')).toBeVisible();
  await expect(page.getByLabel('Password')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'My claims' })).toHaveCount(0);
});

async function signIn(page, username) {
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(username === 'manager' ? 'manager123' : 'employee123');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('navigation', { name: 'Workspaces' }).getByRole('button', { name: 'My claims' })).toHaveAttribute('aria-current', 'page');
}

async function submitClaim(page) {
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Submit claim' }).click();
}

async function switchUser(page, username) {
  await page.getByRole('button', { name: 'Log out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'My claims' })).toHaveCount(0);
  await signIn(page, username);
  if (username === 'manager') {
    await expect(page.getByText('No claims yet')).toBeVisible();
    await page.getByRole('button', { name: 'Approvals', exact: true }).click();
    await expect(page.getByRole('navigation', { name: 'Workspaces' }).getByRole('button', { name: 'Approvals' })).toHaveAttribute('aria-current', 'page');
  }
}

test('pending logout prevents draft creation', async ({ page }) => {
  await page.goto('/');
  await signIn(page, 'employee');
  await expect(page.getByText('No claims yet')).toBeVisible();
  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Logout race');
  await page.getByLabel('Category').fill('Other');
  await page.getByLabel('Expense date').fill(new Date().toISOString().slice(0, 10));
  await page.getByLabel('Requested amount (₹)').fill('10');
  await page.getByLabel('Description').fill('Check logout guard');

  let releaseLogout;
  const logoutGate = new Promise((resolve) => { releaseLogout = resolve; });
  let logoutStarted;
  const logoutRequest = new Promise((resolve) => { logoutStarted = resolve; });
  let claimPosts = 0;
  await page.route('**/api/auth/logout', async (route) => {
    logoutStarted();
    await logoutGate;
    await route.continue();
  });
  await page.route('**/api/claims', async (route) => {
    if (route.request().method() === 'POST') {
      claimPosts += 1;
      await route.abort();
    } else await route.continue();
  });
  try {
    await page.getByRole('button', { name: 'Log out' }).click();
    await logoutRequest;
    await expect.soft(page.getByRole('button', { name: 'Create draft' })).toBeDisabled({ timeout: 1000 });
    await page.locator('.claim-form').evaluate((form) => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    await page.waitForTimeout(300);
    expect.soft(claimPosts).toBe(0);
  } finally {
    releaseLogout();
  }
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
});

test('sign in, restore, log out and expire a session', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByLabel('Username').fill('employee');
  await page.getByLabel('Password').fill('wrong');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert')).toContainText('Invalid username or password');
  await signIn(page, 'employee');
  await expect(page.getByRole('heading', { name: 'Review this claim' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('navigation', { name: 'Workspaces' }).getByRole('button', { name: 'My claims' })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('button', { name: 'Log out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  expect((await page.request.get('/api/claims')).status()).toBe(401);
  await signIn(page, 'employee');
  expect((await page.request.post('/api/auth/logout')).status()).toBe(204);
  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Expired session');
  await page.getByLabel('Category').fill('Other');
  await page.getByLabel('Expense date').fill(new Date().toISOString().slice(0, 10));
  await page.getByLabel('Requested amount (₹)').fill('10');
  await page.getByLabel('Description').fill('Session expiry check');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('session has expired');
});

const validAmounts = [
  ['1', 100],
  ['1.2', 120],
  ['250.07', 25007],
  ['100000000.00', 10000000000],
];

const invalidAmounts = ['0', '0.00', '-1', '1.001', '1e2', '₹10', '100000000.01', ''];

test.describe('rupee amount parsing', () => {
  for (const [input, expected] of validAmounts) {
    test(`parses ${input} as ${expected} paise`, () => {
      expect(parseRupees(input)).toBe(expected);
    });
  }

  for (const input of invalidAmounts) {
    test(`rejects ${JSON.stringify(input)}`, () => {
      expect(() => parseRupees(input)).toThrow();
    });
  }

  test('formats INR using Indian grouping', () => {
    expect(formatRupees(800000)).toBe('₹8,000.00');
    expect(formatRupees(12345678)).toBe('₹1,23,456.78');
  });
});

test('employee and approver claim workflow', async ({ page }) => {
  const expenseDate = new Date().toISOString().slice(0, 10);
  await page.goto('/');
  await signIn(page, 'employee');
  await expect(page.getByText('No claims yet')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New claim' })).toBeVisible();

  const idempotencyKeys = [];
  const failFirstTwoCreates = async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    idempotencyKeys.push(route.request().headers()['idempotency-key']);
    if (idempotencyKeys.length <= 2) return route.abort();
    return route.continue();
  };
  await page.route('**/api/claims', failFirstTwoCreates);
  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Office supplies sample');
  await page.getByLabel('Category').fill('Office');
  await page.getByLabel('Expense date').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('900');
  await page.getByLabel('Description').fill('Temporary draft to check edit and delete.');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.locator('.form-error')).toContainText('fetch');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.locator('.form-error')).toContainText('fetch');
  await page.getByLabel('Claim title').fill('Office supplies sample revised');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await page.unroute('**/api/claims', failFirstTwoCreates);
  expect(idempotencyKeys).toHaveLength(3);
  expect(idempotencyKeys[0]).toBe(idempotencyKeys[1]);
  expect(idempotencyKeys[2]).not.toBe(idempotencyKeys[1]);
  await expect(page.locator('.claim-detail .status-chip.status-draft')).toBeVisible();
  await page.getByRole('button', { name: 'Edit draft' }).click();
  await page.getByLabel('Requested amount (₹)').fill('950');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.locator('.amount-summary strong').first()).toHaveText('₹950.00');
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Delete draft' }).click();
  await expect(page.getByText('No claims yet')).toBeVisible();
  await expect(page.getByRole('button', { name: /Office supplies sample revised/ })).toHaveCount(0);

  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Airport taxi');
  await page.getByLabel('Category').fill('Travel');
  await page.getByLabel('Expense date').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('8000');
  await page.getByLabel('Description').fill('Airport transfer for a client visit.');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByRole('heading', { name: 'Airport taxi' })).toBeVisible();
  await submitClaim(page);
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();

  await switchUser(page, 'manager');
  await page.getByRole('button', { name: /Airport taxi/ }).click();
  await expect(page.getByRole('heading', { name: 'Review this claim' })).toBeVisible();
  await page.getByLabel('Approved amount (₹)').fill('6500');
  await page.getByRole('button', { name: 'Approve claim' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Add a comment when approving less than the requested amount.' })).toBeVisible();
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();
  await page.getByLabel(/Review comment/).fill('Taxi cap policy');
  await page.getByRole('button', { name: 'Approve claim' }).click();
  await expect(page.getByText('Partial approval · ₹6,500.00 approved of ₹8,000.00 requested.')).toBeVisible();
  await expect(page.getByText('₹1,500.00', { exact: true })).toBeVisible();
  await expect(page.getByText('Taxi cap policy')).toBeVisible();
  await page.getByRole('button', { name: 'Mark as paid' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-paid')).toBeVisible();
  await expect(page.getByText('Marked as paid', { exact: true })).toBeVisible();

  await switchUser(page, 'employee');
  await expect(page.locator('.claim-detail .status-chip.status-paid')).toBeVisible();
  await expect(page.getByRole('button', { name: /Airport taxi/ })).toBeVisible();

  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Hotel correction');
  await page.getByLabel('Category').fill('Accommodation');
  await page.getByLabel('Expense date').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('2000');
  await page.getByLabel('Description').fill('Hotel stay during a work trip.');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await submitClaim(page);
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();

  await switchUser(page, 'manager');
  await page.getByRole('button', { name: /Hotel correction/ }).click();
  await page.getByRole('button', { name: 'Reject claim' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Add a reason before rejecting this claim.' })).toBeVisible();
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();
  await page.getByLabel(/Review comment/).fill('Please check the submitted amount.');
  await page.getByRole('button', { name: 'Reject claim' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-rejected')).toBeVisible();
  await expect(page.getByText('Please check the submitted amount.')).toBeVisible();

  await switchUser(page, 'employee');
  await page.getByRole('button', { name: /Hotel correction/ }).click();
  await page.getByRole('button', { name: 'Edit correction' }).click();
  await page.getByLabel('Requested amount (₹)').fill('1900');
  await page.getByLabel('Description').fill('Corrected total after checking the receipt.');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-draft')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete draft' })).toHaveCount(0);
  await switchUser(page, 'manager');
  await expect(page.getByRole('button', { name: 'Reviewed history' })).toHaveCount(0);
  await page.getByRole('button', { name: 'All claims' }).click();
  await page.getByRole('button', { name: /Hotel correction/ }).click();
  await expect(page.locator('.claim-detail .status-chip.status-draft')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Review this claim' })).toHaveCount(0);
  await switchUser(page, 'employee');
  await page.getByRole('button', { name: /Hotel correction/ }).click();
  await submitClaim(page);
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();

  await switchUser(page, 'manager');
  await page.getByRole('button', { name: 'All claims' }).click();
  await page.getByRole('button', { name: /Hotel correction/ }).click();
  await expect(page.getByRole('heading', { name: 'Review this claim' })).toBeVisible();
  await expect(page.getByLabel('Approved amount (₹)')).toHaveValue('1900.00');
  await page.getByRole('button', { name: 'Approve claim' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-approved')).toBeVisible();
  await expect(page.locator('.amount-summary strong').first()).toHaveText('₹1,900.00');
  await expect(page.locator('.event-expense').filter({ hasText: 'Requested ₹2,000.00' })).toHaveCount(3);
  await expect(page.locator('.timeline-event time')).toHaveCount(6);
  await expect(page.locator('.timeline-heading strong').filter({ hasText: 'Rejected' })).toHaveCount(1);
  await expect(page.locator('.timeline-heading strong').filter({ hasText: 'Approved' })).toHaveCount(1);
  await expect(page.locator('.event-actor').filter({ hasText: 'Manager One' })).toHaveCount(2);
  await expect(page.getByText('Please check the submitted amount.')).toBeVisible();
  await page.getByRole('button', { name: 'All claims' }).click();
  await expect(page.getByRole('button', { name: /Hotel correction/ })).toBeVisible();

  await switchUser(page, 'employee');
  await expect(page.getByRole('heading', { name: 'Review this claim' })).toHaveCount(0);
  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Stale review guard');
  await page.getByLabel('Category').fill('Other');
  await page.getByLabel('Expense date').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('100');
  await page.getByLabel('Description').fill('Verify that a stale approval is not replayed.');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByRole('heading', { name: 'Stale review guard' })).toBeVisible();
  const claimLabel = await page.locator('.claim-detail .eyebrow').textContent();
  const claimId = Number(claimLabel.match(/#(\d+)/)[1]);
  await submitClaim(page);

  await switchUser(page, 'manager');
  await page.getByRole('button', { name: /Stale review guard/ }).click();
  await expect(page.getByRole('heading', { name: 'Review this claim' })).toBeVisible();
  const claimResponse = await page.request.get(`/api/claims/${claimId}`);
  expect(claimResponse.ok()).toBeTruthy();
  const staleVersion = await claimResponse.json();
  const externalApproval = await page.request.post(`/api/claims/${claimId}/review`, {
    data: { version: staleVersion.version, decision: 'APPROVED', approved_amount_paise: 10000 },
  });
  expect(externalApproval.ok()).toBeTruthy();
  const externallyUpdated = await externalApproval.json();

  await page.getByRole('button', { name: 'Approve claim' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'This claim changed since it was opened.' })).toBeVisible();
  await expect(page.locator('.claim-detail .status-chip.status-approved')).toBeVisible();
  const refreshed = await page.request.get(`/api/claims/${claimId}`);
  const refreshedClaim = await refreshed.json();
  expect(refreshedClaim.version).toBe(externallyUpdated.version);
  expect(refreshedClaim.history).toHaveLength(3);
});

test('approver owns claims but cannot review their own submission', async ({ page }) => {
  await page.goto('/');
  await signIn(page, 'manager');
  await expect(page.getByText('No claims yet')).toBeVisible();
  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Approver own expense');
  await page.getByLabel('Category').fill('Travel');
  await page.getByLabel('Expense date').fill(new Date().toISOString().slice(0, 10));
  await page.getByLabel('Requested amount (₹)').fill('100');
  await page.getByLabel('Description').fill('Own expense');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByRole('button', { name: 'Edit draft' })).toBeVisible();
  const claimId = Number((await page.locator('.claim-detail .eyebrow').textContent()).match(/#(\d+)/)[1]);
  await submitClaim(page);
  await expect(page.getByText('Another manager must review this claim.')).toBeVisible();
  for (const name of ['Approve claim', 'Reject claim', 'Mark as paid']) await expect(page.getByRole('button', { name })).toHaveCount(0);
  const before = await (await page.request.get(`/api/claims/${claimId}`)).json();
  const response = await page.request.post(`/api/claims/${claimId}/review`, { data: { version: before.version, decision: 'APPROVED', approved_amount_paise: 10000 } });
  expect(response.status()).toBe(403);
  const after = await (await page.request.get(`/api/claims/${claimId}`)).json();
  expect(after.version).toBe(before.version);
  expect(after.history).toEqual(before.history);
  await page.getByRole('button', { name: 'Approvals', exact: true }).click();
  await expect(page.getByRole('navigation', { name: 'Workspaces' }).getByRole('button', { name: 'Approvals' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('button', { name: /Approver own expense/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New claim' })).toHaveCount(0);
});

test('rapid workspace changes discard delayed real collection responses', async ({ page }) => {
  await page.goto('/');
  const suffix = randomUUID();
  const ownTitle = `Race own ${suffix}`;
  const otherTitle = `Race other ${suffix}`;
  const baseURL = new URL(page.url()).origin;
  const approverApi = await request.newContext({ baseURL });
  const employeeApi = await request.newContext({ baseURL });
  let ownId;
  try {
    for (const [context, username] of [[approverApi, 'manager'], [employeeApi, 'employee']]) {
      const login = await context.post('/api/auth/login', { data: { username, password: `${username}123` } });
      expect(login.ok()).toBeTruthy();
    }
    const createClaim = async (context, title) => {
      const response = await context.post('/api/claims', {
        headers: { 'Idempotency-Key': randomUUID() },
        data: { title, category: 'Travel', description: 'Workspace race fixture', expense_date: new Date().toISOString().slice(0, 10), amount_paise: 10000 },
      });
      expect(response.status()).toBe(201);
      return response.json();
    };
    const own = await createClaim(approverApi, ownTitle);
    ownId = own.id;
    const other = await createClaim(employeeApi, otherTitle);
    const submission = await employeeApi.post(`/api/claims/${other.id}/submit`, { data: { version: other.version } });
    expect(submission.ok()).toBeTruthy();
  } finally {
    await approverApi.dispose();
    await employeeApi.dispose();
  }
  await signIn(page, 'manager');
  await expect(page.getByRole('button', { name: ownTitle })).toBeVisible();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let started;
  const requested = new Promise((resolve) => { started = resolve; });
  await page.route('**/api/approvals', async (route) => {
    const response = await route.fetch();
    started();
    await gate;
    await route.fulfill({ response }).catch(() => {});
  });
  try {
    await page.getByRole('button', { name: 'Approvals', exact: true }).click();
    await requested;
    await page.getByRole('button', { name: 'My claims', exact: true }).click();
  } finally {
    release();
  }
  await expect(page.getByRole('navigation', { name: 'Workspaces' }).getByRole('button', { name: 'My claims' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('button', { name: ownTitle })).toBeVisible();
  await expect(page.getByRole('button', { name: otherTitle })).toHaveCount(0);
  await page.unroute('**/api/approvals');
  await page.getByRole('button', { name: 'Approvals', exact: true }).click();
  await expect(page.getByRole('button', { name: otherTitle })).toBeVisible();
  let releaseDetail;
  const detailGate = new Promise((resolve) => { releaseDetail = resolve; });
  let detailStarted;
  const detailRequested = new Promise((resolve) => { detailStarted = resolve; });
  await page.route(`**/api/claims/${ownId}`, async (route) => {
    const response = await route.fetch();
    detailStarted();
    await detailGate;
    await route.fulfill({ response }).catch(() => {});
  });
  try {
    await page.getByRole('button', { name: 'My claims', exact: true }).click();
    await detailRequested;
    await page.getByRole('button', { name: 'Approvals', exact: true }).click();
  } finally {
    releaseDetail();
  }
  await expect(page.getByRole('button', { name: otherTitle })).toBeVisible();
  await expect(page.getByRole('heading', { name: ownTitle })).toHaveCount(0);
});

test('history shows account names without roles and My claims has no currency footnote', async ({ page }) => {
  await page.goto('/');
  const baseURL = new URL(page.url()).origin;
  const employeeApi = await request.newContext({ baseURL });
  const managerApi = await request.newContext({ baseURL });
  try {
    for (const [context, username, password] of [
      [employeeApi, 'employee', 'employee123'],
      [managerApi, 'manager', 'manager123'],
    ]) {
      const login = await context.post('/api/auth/login', { data: { username, password } });
      expect(login.ok()).toBeTruthy();
    }
    const created = await employeeApi.post('/api/claims', {
      headers: { 'Idempotency-Key': randomUUID() },
      data: { title: 'Naming regression', description: 'History names', category: 'Travel', expense_date: new Date().toISOString().slice(0, 10), amount_paise: 12500 },
    });
    expect(created.status()).toBe(201);
    const claim = await created.json();
    expect((await employeeApi.post(`/api/claims/${claim.id}/submit`, { data: { version: claim.version } })).ok()).toBeTruthy();
    expect((await managerApi.post(`/api/claims/${claim.id}/review`, { data: { version: 2, decision: 'APPROVED', approved_amount_paise: 12500 } })).ok()).toBeTruthy();

    await signIn(page, 'employee');
    await page.getByRole('button', { name: 'Naming regression' }).click();
    await expect(page.locator('.event-actor')).toHaveText(['Employee One', 'Employee One', 'Manager One']);
    await expect(page.getByText('Amounts are shown in Indian rupees (INR).', { exact: true })).toHaveCount(0);
  } finally {
    await employeeApi.dispose();
    await managerApi.dispose();
  }
});

for (const state of ['first submission', 'direct rejected resubmission', 'corrected draft resubmission']) {
  test(`native confirmation for ${state}`, async ({ page }) => {
    await page.goto('/');
    const baseURL = new URL(page.url()).origin;
    const employee = await request.newContext({ baseURL });
    const manager = await request.newContext({ baseURL });
    try {
      for (const [context, username] of [[employee, 'employee'], [manager, 'manager']]) {
        expect((await context.post('/api/auth/login', { data: { username, password: `${username}123` } })).ok()).toBeTruthy();
      }
      const payload = { title: `Confirmation ${randomUUID()}`, category: 'Travel', description: 'Confirmation fixture', expense_date: new Date().toISOString().slice(0, 10), amount_paise: 10000 };
      const created = await employee.post('/api/claims', { headers: { 'Idempotency-Key': randomUUID() }, data: payload });
      expect(created.status()).toBe(201);
      const claim = await created.json();
      if (state !== 'first submission') {
        expect((await employee.post(`/api/claims/${claim.id}/submit`, { data: { version: claim.version } })).ok()).toBeTruthy();
        expect((await manager.post(`/api/claims/${claim.id}/review`, { data: { version: 2, decision: 'REJECTED', comment: 'Please correct' } })).ok()).toBeTruthy();
        if (state === 'corrected draft resubmission') {
          expect((await employee.patch(`/api/claims/${claim.id}`, { data: { ...payload, description: 'Corrected fixture', version: 3 } })).ok()).toBeTruthy();
        }
      }
      const before = await (await employee.get(`/api/claims/${claim.id}`)).json();
      await signIn(page, 'employee');
      await page.getByRole('button', { name: payload.title }).click();
      await expect(page.getByRole('heading', { name: payload.title })).toBeVisible();
      let submits = 0;
      page.on('request', (req) => {
        if (req.method() === 'POST' && new URL(req.url()).pathname === `/api/claims/${claim.id}/submit`) submits += 1;
      });
      const expectedPrompt = `Are you sure you want to ${state === 'first submission' ? 'submit' : 'resubmit'} this claim? You cannot edit it while it is under review.`;
      let prompt;
      page.once('dialog', async (dialog) => { prompt = { type: dialog.type(), message: dialog.message() }; await dialog.dismiss(); });
      await page.getByRole('button', { name: 'Submit claim' }).click();
      expect(prompt).toEqual({ type: 'confirm', message: expectedPrompt });
      expect(submits).toBe(0);
      expect(await (await employee.get(`/api/claims/${claim.id}`)).json()).toEqual(before);
      await expect(page.getByRole('heading', { name: payload.title })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Submit claim' })).toBeVisible();

      prompt = undefined;
      page.once('dialog', async (dialog) => { prompt = { type: dialog.type(), message: dialog.message() }; await dialog.accept(); });
      await page.getByRole('button', { name: 'Submit claim' }).click();
      expect(prompt).toEqual({ type: 'confirm', message: expectedPrompt });
      await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();
      expect(submits).toBe(1);
      const after = await (await employee.get(`/api/claims/${claim.id}`)).json();
      expect(after.version).toBe(before.version + 1);
      expect(after.history.slice(0, -1)).toEqual(before.history);
      expect(after.history.at(-1).action).toBe('SUBMITTED');
      expect(after.history.filter((event) => event.action === 'SUBMITTED')).toHaveLength(before.history.filter((event) => event.action === 'SUBMITTED').length + 1);
    } finally {
      await employee.dispose();
      await manager.dispose();
    }
  });
}
