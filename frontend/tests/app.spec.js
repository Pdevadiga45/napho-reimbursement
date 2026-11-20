import { test, expect, request } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { formatRupees, parseRupees } from '../src/money.js';

test('category and expense time controls enforce current contract', async ({ page }) => {
  await page.goto('/');
  await signIn(page, 'employee');
  await page.getByRole('button', { name: 'New claim' }).click();
  await expect(page.getByLabel('Category')).toHaveJSProperty('tagName', 'SELECT');
  await expect(page.getByLabel('Category').locator('option')).toHaveText(['Choose a category', 'Travel and Stay', 'Meals', 'Work & Tech', 'Others']);
  expect(await page.getByLabel('Category').locator('option').evaluateAll((options) => options.map((option) => option.value))).toEqual(['', 'travel and stay', 'meals', 'work & tech', 'others']);
});

test('expense time control is required', async ({ page }) => {
  await page.goto('/');
  await signIn(page, 'employee');
  await page.getByRole('button', { name: 'New claim' }).click();
  await expect(page.getByLabel('Expense date and time')).toHaveAttribute('type', 'datetime-local');
  await expect(page.getByLabel('Expense date and time')).toHaveAttribute('required', '');
});

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
  await expect(page.getByText(/Employee view|Approver view/)).toHaveCount(0);
  expect((await page.request.get('/api/approvals')).status()).toBe(403);
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

async function signIn(page, username) {
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(username === 'manager' ? 'manager123' : 'employee123');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('navigation', { name: 'Workspaces' }).getByRole('button', { name: 'My claims' })).toHaveAttribute('aria-current', 'page');
}

async function submitClaim(page) {
  await page.getByRole('button', { name: 'Submit claim' }).click();
  const modal = page.getByRole('dialog');
  await modal.getByRole('button', { name: /^(Submit|Resubmit) claim$/ }).click();
}

const receiptPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aKsAAAAASUVORK5CYII=', 'base64');
async function attachReceipt(page) {
  await page.locator('.receipt-panel').getByLabel('Choose receipt').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: receiptPng });
  await page.locator('.receipt-panel').getByRole('button', { name: 'Attach receipt' }).click();
  await expect(page.getByRole('img', { name: 'Receipt preview' })).toBeVisible();
}
async function attachApiReceipt(context, claim) {
  const result = await context.post(`/api/claims/${claim.id}/receipt?version=${claim.version}&filename=bill.png`, { headers: { 'Content-Type': 'image/png' }, data: receiptPng });
  expect(result.ok()).toBeTruthy();
  return result.json();
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
  await page.getByLabel('Category').selectOption('others');
  await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
  await page.getByLabel('Requested amount (₹)').fill('10');
  await page.getByLabel('Business purpose / expense details').fill('Check logout guard');

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
    await expect.soft(page.getByRole('button', { name: 'Save as draft' })).toBeDisabled({ timeout: 1000 });
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
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByLabel('Username')).toBeVisible();
  await expect(page.getByLabel('Password')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'My claims' })).toHaveCount(0);
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
  await page.getByLabel('Category').selectOption('others');
  await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
  await page.getByLabel('Requested amount (₹)').fill('10');
  await page.getByLabel('Business purpose / expense details').fill('Session expiry check');
  await page.getByRole('button', { name: 'Save as draft' }).click();
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
  const expenseDate = '2020-01-02T10:00';
  await page.goto('/');
  await signIn(page, 'employee');
  await expect(page.getByText('Amounts are shown in Indian rupees (INR).', { exact: true })).toHaveCount(0);
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
  await page.getByLabel('Category').selectOption('work & tech');
  await page.getByLabel('Expense date and time').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('900');
  await page.getByLabel('Business purpose / expense details').fill('Temporary draft to check edit and delete.');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await expect(page.locator('.form-error')).toContainText('fetch');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await expect(page.locator('.form-error')).toContainText('fetch');
  await page.getByLabel('Claim title').fill('Office supplies sample revised');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await page.unroute('**/api/claims', failFirstTwoCreates);
  expect(idempotencyKeys).toHaveLength(3);
  expect(idempotencyKeys[0]).toBe(idempotencyKeys[1]);
  expect(idempotencyKeys[2]).toBe(idempotencyKeys[1]);
  await expect(page.locator('.claim-detail .status-chip.status-draft')).toBeVisible();
  await page.getByRole('button', { name: 'Edit draft' }).click();
  await page.getByLabel('Requested amount (₹)').fill('950');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.locator('.amount-summary strong').first()).toHaveText('₹950.00');
  await page.getByRole('button', { name: 'Delete draft' }).click();
  await page.getByRole('dialog', { name: 'Delete draft?' }).getByRole('button', { name: 'Delete draft' }).click();
  await expect(page.getByText('No claims yet')).toBeVisible();
  await expect(page.getByRole('button', { name: /Office supplies sample revised/ })).toHaveCount(0);

  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Airport taxi');
  await page.getByLabel('Category').selectOption('travel and stay');
  await page.getByLabel('Expense date and time').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('8000');
  await page.getByLabel('Business purpose / expense details').fill('Airport transfer for a client visit.');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await expect(page.getByRole('heading', { name: 'Airport taxi' })).toBeVisible();
  await attachReceipt(page);
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
  await expect(page.locator('.event-actor')).toHaveText(['Employee One', 'Employee One', 'Employee One', 'Manager One']);
  await page.getByRole('button', { name: 'Mark as paid' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-paid')).toBeVisible();
  await expect(page.getByText('Marked as paid', { exact: true })).toBeVisible();

  await switchUser(page, 'employee');
  await expect(page.locator('.claim-detail .status-chip.status-paid')).toBeVisible();
  await expect(page.getByRole('button', { name: /Airport taxi/ })).toBeVisible();

  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Hotel correction');
  await page.getByLabel('Category').selectOption('travel and stay');
  await page.getByLabel('Expense date and time').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('2000');
  await page.getByLabel('Business purpose / expense details').fill('Hotel stay during a work trip.');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await attachReceipt(page);
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
  await page.getByLabel('Business purpose / expense details').fill('Corrected total after checking the receipt.');
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
  await expect(page.locator('.event-expense').filter({ hasText: 'Requested ₹2,000.00' })).toHaveCount(4);
  await expect(page.locator('.timeline-event time')).toHaveCount(7);
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
  await page.getByLabel('Category').selectOption('others');
  await page.getByLabel('Expense date and time').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('100');
  await page.getByLabel('Business purpose / expense details').fill('Verify that a stale approval is not replayed.');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await expect(page.getByRole('heading', { name: 'Stale review guard' })).toBeVisible();
  const claimLabel = await page.locator('.claim-detail .eyebrow').textContent();
  const claimId = Number(claimLabel.match(/#(\d+)/)[1]);
  await attachReceipt(page);
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
  expect(refreshedClaim.history.map((event) => event.action)).toEqual(['CREATED', 'RECEIPT_ATTACHED', 'SUBMITTED', 'APPROVED']);
});

test('approver owns claims but cannot review their own submission', async ({ page }) => {
  await page.goto('/');
  await signIn(page, 'manager');
  await expect(page.getByText('No claims yet')).toBeVisible();
  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Approver own expense');
  await page.getByLabel('Category').selectOption('travel and stay');
  await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
  await page.getByLabel('Requested amount (₹)').fill('100');
  await page.getByLabel('Business purpose / expense details').fill('Own expense');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await expect(page.getByRole('button', { name: 'Edit draft' })).toBeVisible();
  const claimId = Number((await page.locator('.claim-detail .eyebrow').textContent()).match(/#(\d+)/)[1]);
  await attachReceipt(page);
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
        data: { title, category: 'travel and stay', description: 'Workspace race fixture', expense_date: '2020-01-02T10:00:00Z', amount_paise: 10000 },
      });
      expect(response.status()).toBe(201);
      return response.json();
    };
    const own = await createClaim(approverApi, ownTitle);
    ownId = own.id;
    const other = await createClaim(employeeApi, otherTitle);
    const ready = await attachApiReceipt(employeeApi, other);
    const submission = await employeeApi.post(`/api/claims/${other.id}/submit`, { data: { version: ready.version } });
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
  await page.route('**/api/approvals?*', async (route) => {
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
  await page.unroute('**/api/approvals?*');
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

for (const state of ['first submission', 'direct rejected resubmission', 'corrected draft resubmission']) {
  test(`rendered confirmation for ${state}`, async ({ page }) => {
    await page.goto('/');
    const baseURL = new URL(page.url()).origin;
    const employee = await request.newContext({ baseURL });
    const manager = await request.newContext({ baseURL });
    try {
      for (const [context, username] of [[employee, 'employee'], [manager, 'manager']]) {
        expect((await context.post('/api/auth/login', { data: { username, password: `${username}123` } })).ok()).toBeTruthy();
      }
      const payload = { title: `Confirmation ${randomUUID()}`, category: 'travel and stay', description: 'Confirmation fixture', expense_date: '2020-01-02T10:00:00Z', amount_paise: 10000 };
      const created = await employee.post('/api/claims', { headers: { 'Idempotency-Key': randomUUID() }, data: payload });
      expect(created.status()).toBe(201);
      const claim = await created.json();
      const ready = await attachApiReceipt(employee, claim);
      if (state !== 'first submission') {
        const submitted = await employee.post(`/api/claims/${claim.id}/submit`, { data: { version: ready.version } });
        expect(submitted.ok()).toBeTruthy();
        const rejected = await manager.post(`/api/claims/${claim.id}/review`, { data: { version: (await submitted.json()).version, decision: 'REJECTED', comment: 'Please correct' } });
        expect(rejected.ok()).toBeTruthy();
        if (state === 'corrected draft resubmission') {
          expect((await employee.patch(`/api/claims/${claim.id}`, { data: { ...payload, description: 'Corrected fixture', version: (await rejected.json()).version } })).ok()).toBeTruthy();
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
      const label = state === 'first submission' ? 'Submit claim' : 'Resubmit claim';
      const unexpectedDialogs = [];
      page.on('dialog', async (dialog) => { unexpectedDialogs.push(dialog.message()); await dialog.dismiss(); });
      const trigger = page.getByRole('button', { name: 'Submit claim' });
      await trigger.click();
      const modal = page.getByRole('dialog', { name: `${label}?` });
      await expect(modal).toBeVisible();
      await expect(modal).toContainText(payload.title);
      await expect(modal.getByRole('button', { name: 'Cancel' })).toBeFocused();
      if (state === 'first submission') {
        await page.keyboard.press('Escape');
        await expect(modal).toHaveCount(0);
        await expect(trigger).toBeFocused();
        expect(submits).toBe(0);
        expect(await (await employee.get(`/api/claims/${claim.id}`)).json()).toEqual(before);
        await trigger.click();
      }
      await modal.getByRole('button', { name: 'Cancel' }).click();
      await expect(trigger).toBeFocused();
      expect(submits).toBe(0);
      expect(await (await employee.get(`/api/claims/${claim.id}`)).json()).toEqual(before);
      await expect(page.getByRole('heading', { name: payload.title })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Submit claim' })).toBeVisible();

      await trigger.click();
      await modal.getByRole('button', { name: label, exact: true }).click();
      expect(unexpectedDialogs).toEqual([]);
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

test('delete confirmation cancels and deletes on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto('/');
  const employee = await request.newContext({ baseURL: new URL(page.url()).origin });
  try {
    expect((await employee.post('/api/auth/login', { data: { username: 'employee', password: 'employee123' } })).ok()).toBeTruthy();
    const payload = { title: `Delete ${randomUUID()}`, category: 'travel and stay', description: 'Delete fixture', expense_date: '2020-01-02T10:00:00Z', amount_paise: 10000 };
    const claim = await (await employee.post('/api/claims', { headers: { 'Idempotency-Key': randomUUID() }, data: payload })).json();
    const before = await (await employee.get(`/api/claims/${claim.id}`)).json();
    const unexpectedDialogs = [];
    page.on('dialog', async (dialog) => { unexpectedDialogs.push(dialog.message()); await dialog.dismiss(); });
    let deletes = 0;
    page.on('request', (req) => { if (req.method() === 'DELETE' && new URL(req.url()).pathname === `/api/claims/${claim.id}`) deletes += 1; });
    await signIn(page, 'employee');
    await page.getByRole('button', { name: payload.title }).click();
    const trigger = page.getByRole('button', { name: 'Delete draft' });
    await trigger.click();
    const modal = page.getByRole('dialog', { name: 'Delete draft?' });
    await expect(modal).toContainText(payload.title);
    await expect(modal).toContainText('cannot be undone');
    const box = await modal.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(320);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(640);
    for (const button of [modal.getByRole('button', { name: 'Cancel' }), modal.getByRole('button', { name: 'Delete draft' })]) {
      expect((await button.boundingBox()).height).toBeGreaterThanOrEqual(44);
    }
    await page.mouse.click(1, 1);
    await expect(modal).toHaveCount(0);
    await expect(trigger).toBeFocused();
    expect(deletes).toBe(0);
    expect(await (await employee.get(`/api/claims/${claim.id}`)).json()).toEqual(before);
    await trigger.click();
    await modal.getByRole('button', { name: 'Cancel' }).click();
    await expect(trigger).toBeFocused();
    expect(deletes).toBe(0);
    expect(await (await employee.get(`/api/claims/${claim.id}`)).json()).toEqual(before);
    await trigger.click();
    await modal.getByRole('button', { name: 'Delete draft' }).click();
    await expect(page.getByText('Draft deleted.')).toBeVisible();
    expect(deletes).toBe(1);
    expect((await employee.get(`/api/claims/${claim.id}`)).status()).toBe(404);
    expect(unexpectedDialogs).toEqual([]);
  } finally { await employee.dispose(); }
});

test('confirmation submits the captured version after an external edit', async ({ page }) => {
  await page.goto('/');
  const employee = await request.newContext({ baseURL: new URL(page.url()).origin });
  try {
    expect((await employee.post('/api/auth/login', { data: { username: 'employee', password: 'employee123' } })).ok()).toBeTruthy();
    const payload = { title: `Stale ${randomUUID()}`, category: 'travel and stay', description: 'Snapshot fixture', expense_date: '2020-01-02T10:00:00Z', amount_paise: 10000 };
    const claim = await (await employee.post('/api/claims', { headers: { 'Idempotency-Key': randomUUID() }, data: payload })).json();
    const ready = await attachApiReceipt(employee, claim);
    await signIn(page, 'employee');
    await page.getByRole('button', { name: payload.title }).click();
    await page.getByRole('button', { name: 'Submit claim' }).click();
    const modal = page.getByRole('dialog', { name: 'Submit claim?' });
    await expect(modal).toBeVisible();
    expect((await employee.patch(`/api/claims/${claim.id}`, { data: { ...payload, description: 'External edit', version: ready.version } })).ok()).toBeTruthy();
    const response = page.waitForResponse((res) => res.url().endsWith(`/api/claims/${claim.id}/submit`) && res.request().method() === 'POST');
    await modal.getByRole('button', { name: 'Submit claim' }).click();
    const result = await response;
    expect(result.status()).toBe(409);
    expect(result.request().postDataJSON()).toEqual({ version: ready.version });
    await expect(page.getByRole('alert')).toContainText('changed since it was opened');
    const after = await (await employee.get(`/api/claims/${claim.id}`)).json();
    expect(after.status).toBe('DRAFT');
    expect(after.history.filter((event) => event.action === 'SUBMITTED')).toHaveLength(0);
  } finally { await employee.dispose(); }
});


test('receipt attaches to a merchant draft', async ({ page }) => {
  const title = 'Receipt ' + randomUUID();
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aKsAAAAASUVORK5CYII=', 'base64');
  const pdf = Buffer.from('JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgNzIgNzIgXQovUGFyZW50IDIgMCBSCj4+CmVuZG9iagp4cmVmCjAgNQowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAKMDAwMDAwMDA1NCAwMDAwMCBuIAowMDAwMDAwMTEzIDAwMDAwIG4gCjAwMDAwMDAxNjIgMDAwMDAgbiAKdHJhaWxlcgo8PAovU2l6ZSA1Ci9Sb290IDMgMCBSCi9JbmZvIDEgMCBSCj4+CnN0YXJ0eHJlZgoyNTQKJSVFT0YK', 'base64');
  await page.goto('/');
  await signIn(page, 'employee');
  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill(title);
  await page.getByLabel('Category').selectOption('travel and stay');
  await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
  await page.getByLabel('Requested amount (₹)').fill('100');
  await page.getByLabel('Business purpose / expense details').fill('Client visit');
  await page.getByLabel('Merchant').fill('Example shop');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await expect(page.getByText('Example shop', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Receipt', exact: true })).toBeVisible();
  await page.getByLabel('Choose receipt').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: png });
  await page.getByRole('button', { name: 'Attach receipt' }).click();
  await expect(page.getByRole('img', { name: 'Receipt preview' })).toBeVisible();
  const src = await page.getByRole('img', { name: 'Receipt preview' }).getAttribute('src');
  expect(src).toMatch(/^\/api\/claims\/\d+\/receipts\/\d+$/);
  expect(await (await page.request.get(src)).body()).toEqual(png);
  await page.getByRole('button', { name: 'Edit draft' }).click();
  await page.getByLabel('Merchant').fill('Updated shop');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByText('Updated shop', { exact: true })).toBeVisible();
  await submitClaim(page);
  await expect(page.getByRole('img', { name: 'Receipt preview' })).toBeVisible();
  await expect(page.getByLabel('Choose receipt')).toHaveCount(0);
  await page.getByRole('button', { name: 'Log out' }).click();
  await signIn(page, 'manager');
  await page.getByRole('button', { name: 'Approvals', exact: true }).click();
  await page.getByRole('button', { name: title }).click();
  await expect(page.getByRole('img', { name: 'Receipt preview' })).toBeVisible();
  await expect(page.getByLabel('Choose receipt')).toHaveCount(0);
  await page.getByLabel('Review comment').fill('Please correct the receipt');
  await page.getByRole('button', { name: 'Reject claim' }).click();
  await switchUser(page, 'employee');
  await page.getByRole('button', { name: title }).click();
  await page.getByLabel('Choose receipt').setInputFiles({ name: 'corrected.pdf', mimeType: 'application/pdf', buffer: pdf });
  await page.getByRole('button', { name: 'Replace receipt' }).click();
  await expect(page.getByRole('link', { name: 'Download receipt' })).toBeVisible();
  await expect(page.locator('.claim-detail .status-chip.status-draft')).toBeVisible();
  const oldLink = page.locator('.timeline-event').filter({ hasText: 'Receipt attached' }).getByRole('link', { name: 'View receipt' });
  expect(await oldLink.getAttribute('href')).toBe(src);
  expect(await (await page.request.get(await oldLink.getAttribute('href'))).body()).toEqual(png);
});

test('receipt validation and removal preserve history on narrow screens', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto('/');
  const employee = await request.newContext({ baseURL: new URL(page.url()).origin });
  try {
    expect((await employee.post('/api/auth/login', { data: { username: 'employee', password: 'employee123' } })).ok()).toBeTruthy();
    const title = 'Removal ' + randomUUID();
    const claim = await (await employee.post('/api/claims', { headers: { 'Idempotency-Key': randomUUID() }, data: { title, category: 'travel and stay', description: 'Receipt removal', expense_date: '2020-01-02T10:00:00Z', amount_paise: 10000 } })).json();
    await signIn(page, 'employee');
    await page.getByRole('button', { name: title }).click();
    await page.getByLabel('Choose receipt').setInputFiles({ name: 'bad.txt', mimeType: 'text/plain', buffer: Buffer.from('bad') });
    await expect(page.getByRole('alert')).toContainText('Choose a PNG');
    await page.getByLabel('Choose receipt').setInputFiles({ name: 'large.png', mimeType: 'image/png', buffer: Buffer.alloc(5_000_001) });
    await expect(page.getByRole('alert')).toContainText('5 MB');
    expect((await (await employee.get(`/api/claims/${claim.id}`)).json()).version).toBe(claim.version);
    await page.getByLabel('Choose receipt').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aKsAAAAASUVORK5CYII=', 'base64') });
    await page.getByRole('button', { name: 'Attach receipt' }).click();
    await expect(page.getByRole('img', { name: 'Receipt preview' })).toBeVisible();
    let deletes = 0;
    page.on('request', (req) => { if (req.method() === 'DELETE' && new URL(req.url()).pathname === `/api/claims/${claim.id}/receipt`) deletes += 1; });
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      for (const button of await page.locator('.receipt-panel button').all()) expect((await button.boundingBox()).height).toBeGreaterThanOrEqual(44);
    }
    await page.getByRole('button', { name: 'Remove receipt' }).click();
    const dialog = page.getByRole('dialog', { name: 'Remove receipt?' });
    await expect(dialog).toContainText('bill.png');
    await page.keyboard.press('Escape');
    expect(deletes).toBe(0);
    await page.getByRole('button', { name: 'Remove receipt' }).click();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    expect(deletes).toBe(0);
    await page.getByRole('button', { name: 'Remove receipt' }).click();
    await dialog.getByRole('button', { name: 'Remove receipt' }).click();
    await expect(page.getByText('No receipt attached')).toBeVisible();
    expect(deletes).toBe(1);
    await expect(page.getByText('Receipt removed', { exact: true })).toBeVisible();
  } finally { await employee.dispose(); }
});

test('receipt guidance blocks submission until attached', async ({ page }) => {
  await page.goto('/');
  const employee = await request.newContext({ baseURL: new URL(page.url()).origin });
  try {
    expect((await employee.post('/api/auth/login', { data: { username: 'employee', password: 'employee123' } })).ok()).toBeTruthy();
    const title = `Receipt required ${randomUUID()}`;
    await signIn(page, 'employee');
    await page.getByRole('button', { name: 'New claim' }).click();
    await page.getByLabel('Claim title').fill(title);
    await page.getByLabel('Category').selectOption('meals');
    await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
    await page.getByLabel('Requested amount (₹)').fill('100');
    await page.getByLabel('Business purpose / expense details').fill('Receipt guidance');
    const createdResponse = page.waitForResponse((response) => response.url().endsWith('/api/claims') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Save as draft' }).click();
    const created = await createdResponse;
    expect(created.status()).toBe(201);
    await expect(page.getByRole('heading', { name: title })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Submit claim' })).toBeDisabled();
    await expect(page.locator('.claim-detail')).toContainText(/receipt.*required|required.*receipt/i);
    await page.getByLabel('Choose receipt').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aKsAAAAASUVORK5CYII=', 'base64') });
    await page.getByRole('button', { name: 'Attach receipt' }).click();
    await expect(page.getByRole('button', { name: 'Submit claim' })).toBeEnabled();
    await submitClaim(page);
    await expect(page.locator('.status-submitted').last()).toBeVisible();
    const claim = await created.json();
    const manager = await request.newContext({ baseURL: new URL(page.url()).origin });
    try {
      expect((await manager.post('/api/auth/login', { data: { username: 'manager', password: 'manager123' } })).ok()).toBeTruthy();
      const submitted = await (await manager.get(`/api/claims/${claim.id}`)).json();
      expect((await manager.post(`/api/claims/${claim.id}/review`, { data: { version: submitted.version, decision: 'REJECTED', comment: 'Please correct' } })).ok()).toBeTruthy();
      await page.reload();
      await page.getByRole('button', { name: title }).click();
      await page.getByRole('button', { name: 'Remove receipt' }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Remove receipt' }).click();
      await expect(page.getByRole('button', { name: 'Submit claim' })).toBeDisabled();
      await expect(page.locator('.claim-detail')).toContainText(/receipt.*required|required.*receipt/i);
      await attachReceipt(page);
      await expect(page.getByRole('button', { name: 'Submit claim' })).toBeEnabled();
      await page.getByRole('button', { name: 'Submit claim' }).click();
      await expect(page.getByRole('dialog', { name: 'Resubmit claim?' })).toBeVisible();
    } finally { await manager.dispose(); }
  } finally { await employee.dispose(); }
});

test('local expense time round trips without changing its instant', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: 'http://127.0.0.1:15173', timezoneId: 'Asia/Kolkata' });
  const page = await context.newPage();
  try {
    await page.goto('/');
    await signIn(page, 'employee');
    const title = `Local time ${randomUUID()}`;
    await page.getByRole('button', { name: 'New claim' }).click();
    await page.getByLabel('Claim title').fill(title);
    await page.getByLabel('Category').selectOption('meals');
    await page.getByLabel('Expense date and time').fill('2020-01-02T10:30:25');
    await page.getByLabel('Requested amount (₹)').fill('100');
    await page.getByLabel('Business purpose / expense details').fill('Timezone fixture');
    const response = page.waitForResponse((r) => r.url().endsWith('/api/claims') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Save as draft' }).click();
    const created = await (await response).json();
    expect(new Date(created.expense_date).toISOString()).toBe('2020-01-02T05:00:25.000Z');
    await expect(page.locator('.claim-facts')).toContainText('10:30:25');
    await expect(page.locator('.claim-facts')).toContainText('Asia/Calcutta');
    await page.getByRole('button', { name: 'Edit draft' }).click();
    await page.getByLabel('Merchant').fill('Unrelated edit');
    const patch = page.waitForResponse((r) => r.url().endsWith(`/api/claims/${created.id}`) && r.request().method() === 'PATCH');
    await page.getByRole('button', { name: 'Save changes' }).click();
    expect((await (await patch).json()).expense_date).toBe(created.expense_date);
  } finally { await context.close(); }
});

test('nonexistent local expense time is rejected without creating a draft', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: 'http://127.0.0.1:15173', timezoneId: 'America/New_York' });
  const page = await context.newPage();
  try {
    await page.goto('/');
    await signIn(page, 'employee');
    const title = `Spring gap ${randomUUID()}`;
    let createRequests = 0;
    page.on('request', (request) => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/claims') createRequests += 1;
    });
    await page.getByRole('button', { name: 'New claim' }).click();
    await page.getByLabel('Claim title').fill(title);
    await page.getByLabel('Category').selectOption('meals');
    await page.getByLabel('Expense date and time').fill('2025-03-09T02:30');
    await page.getByLabel('Requested amount (₹)').fill('100');
    await page.getByLabel('Business purpose / expense details').fill('Nonexistent local clock regression');
    await page.getByRole('button', { name: 'Save as draft' }).click();
    await expect.soft(page.getByRole('alert')).toContainText(/valid expense date and time/i, { timeout: 1000 });
    await expect.soft(page.locator('.claim-form')).toBeVisible();
    expect.soft(createRequests).toBe(0);
    const claims = await (await page.request.get('/api/claims')).json();
    expect.soft(claims.some((claim) => claim.title === title)).toBe(false);
  } finally { await context.close(); }
});

test('unchanged stored instant characterization preserves repeated-hour precision', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: 'http://127.0.0.1:15173', timezoneId: 'America/New_York' });
  const page = await context.newPage();
  const employee = await request.newContext({ baseURL: 'http://127.0.0.1:15173' });
  try {
    expect((await employee.post('/api/auth/login', { data: { username: 'employee', password: 'employee123' } })).ok()).toBeTruthy();
    const title = `Repeated hour ${randomUUID()}`;
    const createdResponse = await employee.post('/api/claims', {
      headers: { 'Idempotency-Key': randomUUID() },
      data: { title, category: 'meals', description: 'Repeated-hour precision characterization', expense_date: '2024-11-03T06:30:12.123456Z', amount_paise: 10000 },
    });
    expect(createdResponse.ok()).toBeTruthy();
    const created = await createdResponse.json();
    expect(created.expense_date).toMatch(/\.123456(?:Z|\+00:00)$/);
    await page.goto('/');
    await signIn(page, 'employee');
    await page.getByRole('button', { name: title }).click();
    await page.getByRole('button', { name: 'Edit draft' }).click();
    await page.getByLabel('Claim title').fill(`${title} edited`);
    await page.getByLabel('Merchant').fill('Unrelated edit');
    const patch = page.waitForResponse((response) => response.url().endsWith(`/api/claims/${created.id}`) && response.request().method() === 'PATCH');
    await page.getByRole('button', { name: 'Save changes' }).click();
    expect((await (await patch).json()).expense_date).toBe(created.expense_date);
  } finally {
    await employee.dispose();
    await context.close();
  }
});

test('legacy date and category remain visible and require correction', async ({ page }) => {
  await page.goto('/');
  const employee = await request.newContext({ baseURL: new URL(page.url()).origin });
  try {
    expect((await employee.post('/api/auth/login', { data: { username: 'employee', password: 'employee123' } })).ok()).toBeTruthy();
    const categories = ['Historic category', '__proto__', 'constructor'];
    const createdClaims = await Promise.all(categories.map(async (category) => {
      const title = `Legacy ${category} ${randomUUID()}`;
      const response = await employee.post('/api/claims', {
        headers: { 'Idempotency-Key': randomUUID() },
        data: { title, category: 'meals', description: 'Legacy read fixture', expense_date: '2020-01-02T10:00:00Z', amount_paise: 10000 },
      });
      expect(response.ok()).toBeTruthy();
      return { ...(await response.json()), legacyCategory: category };
    }));
    const legacyById = new Map(createdClaims.map((claim) => [claim.id, claim.legacyCategory]));
    const listDtoIds = [];
    const legacy = (claim) => {
      const category = legacyById.get(claim.id);
      if (!category) return claim;
      return { ...claim, category, expense_date: '2019-04-03', ...(claim.history ? { history: claim.history.map((event) => ({ ...event, category, expense_date: '2019-04-03' })) } : {}) };
    };
    await page.route('**/api/claims?*', async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const response = await route.fetch();
      const claims = await response.json();
      listDtoIds.push(...claims.filter((claim) => legacyById.has(claim.id)).map((claim) => claim.id));
      await route.fulfill({ response, json: claims.map(legacy) });
    });
    await page.route('**/api/claims/*', async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const response = await route.fetch();
      await route.fulfill({ response, json: legacy(await response.json()) });
    });
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await signIn(page, 'employee');
    expect(pageErrors).toEqual([]);
    await expect.poll(() => listDtoIds.length).toBe(createdClaims.length);
    expect([...listDtoIds].sort()).toEqual(createdClaims.map((claim) => claim.id).sort());
    for (const claim of createdClaims) {
      const row = page.locator('.claim-select').filter({ hasText: claim.title });
      await expect(row).toHaveCount(1);
      await expect(row).toContainText(claim.legacyCategory);
      await row.click();
      await expect(page.locator('.claim-detail')).toContainText('2019-04-03 · time not recorded');
      await expect(page.locator('.timeline')).toContainText(claim.legacyCategory);
      await page.getByRole('button', { name: 'Edit draft' }).click();
      await expect(page.getByLabel('Category')).toHaveValue('');
      await expect(page.getByLabel('Expense date and time')).toHaveValue('');
      await expect(page.locator('.claim-form')).toContainText('Original date: 2019-04-03');
      await expect(page.locator('.claim-form')).toContainText(`Original category: ${claim.legacyCategory}`);
      await page.getByRole('button', { name: 'Cancel' }).click();
    }
  } finally { await employee.dispose(); }
});

test('pagination uses server pages and status totals', async ({ page }) => {
  await page.goto('/');
  const employee = await request.newContext({ baseURL: new URL(page.url()).origin });
  const created = [];
  try {
    await employee.post('/api/auth/login', { data: { username: 'employee', password: 'employee123' } });
    const prefix = `Page ${randomUUID()}`;
    for (let i = 0; i < 12; i++) {
      const response = await employee.post('/api/claims', { headers: { 'Idempotency-Key': randomUUID() }, data: { title: `${prefix} ${i}`, category: 'meals', description: 'Pagination', expense_date: '2020-01-02T10:00:00Z', amount_paise: 100 } });
      expect(response.ok()).toBeTruthy();
      created.push(await response.json());
    }
    const listResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/claims' && r.request().method() === 'GET');
    await signIn(page, 'employee');
    const total = Number((await listResponse).headers()['x-total-count']);
    await expect(page.locator('.claims-table tbody tr')).toHaveCount(10);
    await expect(page.locator('.claim-select').filter({ hasText: prefix })).toHaveCount(10);
    await expect(page.locator('.pagination')).toContainText(`1–10 of ${total}`);
    await page.getByRole('button', { name: 'Next' }).click();
    await expect(page.locator('.pagination')).toContainText(`11–${Math.min(20, total)} of ${total}`);
    await expect(page.locator('.claims-table tbody tr')).toHaveCount(Math.min(10, total - 10));
    await expect(page.locator('.claim-select').filter({ hasText: prefix })).toHaveCount(2);
    await page.getByRole('button', { name: 'Previous' }).click();
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let started;
    const requested = new Promise(resolve => { started = resolve; });
    let finished;
    const staleFinished = new Promise(resolve => { finished = resolve; });
    await page.route('**/api/claims?*', async route => {
      const url = new URL(route.request().url());
      if (url.searchParams.get('status') !== 'DRAFT') return route.continue();
      const response = await route.fetch();
      started();
      await gate;
      await route.fulfill({ response }).catch(() => {});
      finished();
    });
    await page.getByRole('button', { name: 'Drafts' }).click();
    await requested;
    const submittedResponse = page.waitForResponse(r => new URL(r.url()).searchParams.get('status') === 'SUBMITTED');
    await page.getByRole('button', { name: 'Submitted', exact: true }).click();
    const filtered = await submittedResponse;
    const submittedTotal = Number(filtered.headers()['x-total-count']);
    expect((await filtered.json()).every(claim => claim.status === 'SUBMITTED')).toBeTruthy();
    release();
    await staleFinished;
    await expect(page.locator('.count-pill')).toHaveText(String(submittedTotal));
    await expect(page.locator('.pagination')).toContainText('Page 1');
    await expect(page.getByRole('button', { name: 'Submitted', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.claim-select').filter({ hasText: prefix })).toHaveCount(0);
    for (const claim of created.slice(0, 11)) {
      const withReceipt = await attachApiReceipt(employee, claim);
      expect((await employee.post(`/api/claims/${claim.id}/submit`, { data: { version: withReceipt.version } })).ok()).toBeTruthy();
    }
    await page.getByRole('button', { name: 'Log out' }).click();
    await signIn(page, 'manager');
    const approvalsResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/approvals' && r.request().method() === 'GET');
    await page.getByRole('button', { name: 'Approvals', exact: true }).click();
    const approvals = await approvalsResponse;
    const approvalsTotal = Number(approvals.headers()['x-total-count']);
    await expect(page.locator('.pagination')).toContainText(`1–10 of ${approvalsTotal}`);
    await expect(page.locator('.claims-table tbody tr')).toHaveCount(10);
    await expect(page.locator('.claim-select').filter({ hasText: prefix })).toHaveCount(10);
    await page.getByRole('button', { name: 'Next' }).click();
    await expect(page.locator('.pagination')).toContainText(`11–${Math.min(20, approvalsTotal)} of ${approvalsTotal}`);
    await expect(page.locator('.claim-select').filter({ hasText: prefix })).toHaveCount(1);
  } finally {
    for (const claim of created) await employee.delete(`/api/claims/${claim.id}?version=${claim.version}`);
    await employee.dispose();
  }
});

test('upfront receipt submission validates before mutation and retries upload', async ({ page }, testInfo) => {
  await page.goto('/');
  await signIn(page, 'employee');
  await page.getByRole('button', { name: 'New claim' }).click();
  expect(await page.locator('.claim-form').evaluate(form => {
    const fields = [...form.querySelectorAll('select, input')];
    return fields.findIndex(field => field.type === 'file') === fields.findIndex(field => field.name === 'category') + 1
      && fields.findIndex(field => field.name === 'expense_date') === fields.findIndex(field => field.type === 'file') + 1;
  })).toBeTruthy();
  const title = `Upfront ${randomUUID()}`;
  await page.getByLabel('Claim title').fill(title);
  await page.getByLabel('Category').selectOption('meals');
  await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
  await page.getByLabel('Requested amount (₹)').fill('100');
  await page.getByLabel('Business purpose / expense details').fill('Upfront receipt');
  const mutations = [];
  page.on('request', req => { if (req.method() !== 'GET' && new URL(req.url()).pathname.startsWith('/api/claims')) mutations.push(req); });
  await page.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(page.getByLabel('Choose receipt')).toBeFocused();
  expect(mutations).toHaveLength(0);
  await page.getByLabel('Choose receipt').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: receiptPng });
  await expect(page.locator('.claim-form')).not.toContainText('Choose a receipt before submitting.');
  await page.screenshot({ path: testInfo.outputPath('upfront-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath('upfront-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: 'Submit', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
  expect(mutations).toHaveLength(0);
  let fail = true;
  await page.route('**/api/claims/*/receipt?*', async route => {
    if (fail && route.request().method() === 'POST') { fail = false; await route.fulfill({ status: 503, json: { detail: 'Upload failed' } }); }
    else await route.continue();
  });
  await page.getByRole('button', { name: 'Submit', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Submit claim' }).click();
  await expect(page.getByRole('alert')).toContainText('Upload failed');
  await page.getByRole('button', { name: 'Submit', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Submit claim' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();
  expect(mutations.filter(req => req.method() === 'POST' && new URL(req.url()).pathname === '/api/claims')).toHaveLength(1);
  expect(mutations.filter(req => req.method() === 'POST' && new URL(req.url()).pathname.endsWith('/submit'))).toHaveLength(1);
  await page.screenshot({ path: testInfo.outputPath('saved-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.screenshot({ path: testInfo.outputPath('saved-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill(`Draft with receipt ${randomUUID()}`);
  await page.getByLabel('Category').selectOption('meals');
  await page.getByLabel('Choose receipt').setInputFiles({ name: 'draft.png', mimeType: 'image/png', buffer: receiptPng });
  await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
  await page.getByLabel('Requested amount (₹)').fill('10');
  await page.getByLabel('Business purpose / expense details').fill('Draft receipt');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  await expect(page.locator('.receipt-current')).toContainText('draft.png');
  await expect(page.locator('.claim-detail .status-chip.status-draft')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('editable-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath('editable-mobile.png'), fullPage: true });
});

for (const lost of ['create', 'patch', 'rejected patch', 'upload', 'submit', 'create external unchanged', 'create external edited']) {
  test(`form recovers a lost ${lost} response without duplicate mutation`, async ({ page }) => {
    await page.goto('/');
    await signIn(page, 'employee');
    await page.getByRole('button', { name: 'New claim' }).click();
    const title = `Lost ${lost} ${randomUUID()}`;
    await page.getByLabel('Claim title').fill(title);
    await page.getByLabel('Category').selectOption('meals');
    await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
    await page.getByLabel('Requested amount (₹)').fill('100');
    await page.getByLabel('Business purpose / expense details').fill('Lost response control');
    if (lost === 'upload' || lost === 'submit') await page.getByLabel('Choose receipt').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: receiptPng });
    if (lost === 'patch' || lost === 'rejected patch') {
      await page.getByRole('button', { name: 'Save as draft' }).click();
      if (lost === 'rejected patch') {
        const id = Number((await page.locator('.claim-detail .eyebrow').textContent()).match(/#(\d+)/)[1]);
        const claim = await (await page.request.get(`/api/claims/${id}`)).json();
        const withReceipt = await attachApiReceipt(page.request, claim);
        const submitted = await (await page.request.post(`/api/claims/${id}/submit`, { data: { version: withReceipt.version } })).json();
        const manager = await request.newContext({ baseURL: new URL(page.url()).origin });
        try {
          expect((await manager.post('/api/auth/login', { data: { username: 'manager', password: 'manager123' } })).ok()).toBeTruthy();
          expect((await manager.post(`/api/claims/${id}/review`, { data: { version: submitted.version, decision: 'REJECTED', comment: 'Correct this claim' } })).ok()).toBeTruthy();
        } finally { await manager.dispose(); }
        await page.reload();
        await page.getByRole('button', { name: title }).click();
      }
      await page.getByRole('button', { name: lost === 'patch' ? 'Edit draft' : 'Edit correction' }).click();
      await page.getByLabel('Merchant').fill('Reconciled edit');
      if (lost === 'rejected patch') await page.getByLabel('Choose receipt').setInputFiles({ name: 'corrected.png', mimeType: 'image/png', buffer: receiptPng });
    }
    let committed;
    let uploadCount = 0;
    let submitCount = 0;
    let patchCount = 0;
    let submittedVersion;
    await page.route('**/api/claims**', async route => {
      const req = route.request();
      const path = new URL(req.url()).pathname;
      if (req.method() === 'PATCH' && /\/api\/claims\/\d+$/.test(path)) patchCount++;
      if (req.method() === 'POST' && path.endsWith('/receipt')) uploadCount++;
      if (req.method() === 'POST' && path.endsWith('/submit')) { submitCount++; submittedVersion = req.postDataJSON().version; }
      const target = lost.startsWith('create') ? req.method() === 'POST' && path === '/api/claims'
        : lost.endsWith('patch') ? req.method() === 'PATCH' && /\/api\/claims\/\d+$/.test(path)
          : req.method() === 'POST' && path.endsWith(lost === 'upload' ? '/receipt' : '/submit');
      if (!committed && target) {
        const response = await route.fetch();
        expect(response.ok()).toBeTruthy();
        committed = await response.json();
        return route.abort();
      }
      return route.continue();
    });
    if (lost.startsWith('create')) {
      await page.getByRole('button', { name: 'Save as draft' }).click();
      await expect(page.locator('.form-error')).toBeVisible();
      if (lost !== 'create') {
        const externalResponse = await page.request.patch(`/api/claims/${committed.id}`, { data: { title, category: 'meals', description: 'Lost response control', expense_date: committed.expense_date, amount_paise: 10000, merchant: 'Other tab edit', version: committed.version } });
        expect(externalResponse.ok()).toBeTruthy();
        const external = await externalResponse.json();
        const newer = [];
        for (let i = 0; i < 10; i++) {
          const response = await page.request.post('/api/claims', { headers: { 'Idempotency-Key': randomUUID() }, data: { title: `${title} newer ${i}`, category: 'meals', description: 'Newer owner draft', expense_date: committed.expense_date, amount_paise: 10000 } });
          expect(response.ok()).toBeTruthy();
          newer.push(await response.json());
        }
        if (lost.endsWith('edited')) await page.getByLabel('Claim title').fill(`${title} revised`);
        await page.getByLabel('Choose receipt').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: receiptPng });
        await page.getByRole('button', { name: 'Submit', exact: true }).click();
        const refreshed = page.waitForResponse(r => new URL(r.url()).pathname === '/api/claims' && r.request().method() === 'GET');
        await page.getByRole('dialog').getByRole('button', { name: 'Submit claim' }).click();
        const rows = await (await refreshed).json();
        expect(rows).toHaveLength(10);
        expect(rows.map(claim => claim.id).sort((a, b) => a - b)).toEqual(newer.map(claim => claim.id).sort((a, b) => a - b));
        await expect(page.getByRole('alert')).toContainText('current version has been refreshed');
        await expect(page.locator('.claims-table tbody tr')).toHaveCount(10);
        await expect(page.locator('.claim-detail .eyebrow')).toHaveText(`CLAIM #${committed.id}`);
        await expect(page.locator('.claim-facts')).toContainText('Other tab edit');
        expect(Number((await page.locator('.claim-detail .eyebrow').textContent()).match(/#(\d+)/)[1])).toBe(committed.id);
        const latest = await (await page.request.get(`/api/claims/${committed.id}`)).json();
        expect(latest.status).toBe('DRAFT');
        expect(latest.version).toBe(external.version);
        expect(latest.merchant).toBe('Other tab edit');
        expect(latest.title).toBe(title);
        expect(latest.receipt).toBeNull();
        expect(latest.history.filter(event => event.action === 'CREATED')).toHaveLength(1);
        expect(patchCount).toBe(0);
        expect(uploadCount).toBe(0);
        expect(submitCount).toBe(0);
        await page.getByRole('button', { name: 'All claims', exact: true }).click();
        await expect(page.locator('.claim-detail .eyebrow')).toHaveText(`CLAIM #${committed.id}`);
        for (const navigation of ['Next', 'Previous']) {
          await page.getByRole('button', { name: navigation, exact: true }).click();
          const first = page.locator('.claim-select').first();
          await expect(first).toHaveAttribute('aria-current', 'true');
          const firstTitle = await first.locator('span').textContent();
          await expect(page.locator('.claim-detail h2')).toHaveText(firstTitle);
        }
        await page.getByRole('button', { name: 'Drafts', exact: true }).click();
        const firstDraft = page.locator('.claim-select').first();
        await expect(firstDraft).toHaveAttribute('aria-current', 'true');
        await expect(page.locator('.claim-detail h2')).toHaveText(await firstDraft.locator('span').textContent());
        return;
      }
      await page.getByLabel('Claim title').fill(`${title} revised`);
      await page.getByRole('button', { name: 'Save as draft' }).click();
      await expect(page.getByRole('heading', { name: `${title} revised` })).toBeVisible();
    } else if (lost.endsWith('patch')) {
      if (lost === 'patch') await page.getByRole('button', { name: 'Save changes' }).click();
      else {
        await page.getByRole('button', { name: 'Submit', exact: true }).click();
        await page.getByRole('dialog').getByRole('button', { name: 'Submit claim' }).click();
      }
      await expect(page.locator('.claim-facts')).toContainText('Reconciled edit');
      if (lost === 'rejected patch') {
        await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();
        expect(committed.status).toBe('DRAFT');
        expect(uploadCount).toBe(1);
        expect(submitCount).toBe(1);
        expect(submittedVersion).toBe(committed.version + 1);
      }
    } else {
      await page.getByRole('button', { name: 'Submit', exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Submit claim' }).click();
      if (lost === 'upload') {
        await expect(page.locator('.form-error')).toBeVisible();
        expect(submitCount).toBe(0);
        await page.getByRole('button', { name: 'Submit', exact: true }).click();
        await page.getByRole('dialog').getByRole('button', { name: 'Submit claim' }).click();
      }
      await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();
      expect(uploadCount).toBe(1);
      expect(submitCount).toBe(1);
    }
    const selectedId = Number((await page.locator('.claim-detail .eyebrow').textContent()).match(/#(\d+)/)[1]);
    expect(selectedId).toBe(committed.id);
    const detail = await (await page.request.get(`/api/claims/${committed.id}`)).json();
    expect(detail.history.filter(event => event.action === 'CREATED')).toHaveLength(1);
    expect(detail.history.filter(event => event.action === 'SUBMITTED')).toHaveLength(lost === 'rejected patch' ? 2 : lost === 'submit' || lost === 'upload' ? 1 : 0);
    if (lost === 'rejected patch') {
      expect(detail.history.filter(event => event.action === 'EDITED')).toHaveLength(1);
      expect(detail.receipt.filename).toBe('corrected.png');
    }
    const fixtureClaims = (await (await page.request.get('/api/claims?page=1')).json()).filter(claim => claim.title.startsWith(title));
    expect(fixtureClaims.map(claim => claim.id)).toEqual([committed.id]);
  });
}

test('form stale edit refreshes instead of overwriting another change', async ({ page }) => {
  await page.goto('/');
  await signIn(page, 'employee');
  const title = `Form conflict ${randomUUID()}`;
  const created = await (await page.request.post('/api/claims', { headers: { 'Idempotency-Key': randomUUID() }, data: { title, category: 'meals', description: 'Conflict fixture', expense_date: '2020-01-02T10:00:00Z', amount_paise: 10000 } })).json();
  await page.reload();
  await page.getByRole('button', { name: title }).click();
  await page.getByRole('button', { name: 'Edit draft' }).click();
  await page.getByLabel('Merchant').fill('Local change');
  const external = await page.request.patch(`/api/claims/${created.id}`, { data: { title, category: 'meals', description: 'Conflict fixture', expense_date: '2020-01-02T10:00:00Z', amount_paise: 10000, merchant: 'External change', version: created.version } });
  expect(external.ok()).toBeTruthy();
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('alert')).toContainText('changed since it was opened');
  await expect(page.locator('.claim-detail')).toContainText('External change');
  expect((await (await page.request.get(`/api/claims/${created.id}`)).json()).merchant).toBe('External change');
});

test('expired session invalidates delayed form persistence before a new form', async ({ page }) => {
  await page.goto('/');
  let releaseList, releaseOld, releaseNew, listStarted, oldStarted, newStarted;
  const listGate = new Promise(resolve => { releaseList = resolve; });
  const oldGate = new Promise(resolve => { releaseOld = resolve; });
  const newGate = new Promise(resolve => { releaseNew = resolve; });
  const listPending = new Promise(resolve => { listStarted = resolve; });
  const oldPending = new Promise(resolve => { oldStarted = resolve; });
  const newPending = new Promise(resolve => { newStarted = resolve; });
  let holdList = true, oldId, oldFollowups = 0;
  await page.route('**/api/claims**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() === 'GET' && url.pathname === '/api/claims' && holdList) {
      holdList = false; listStarted(); await listGate; return route.continue().catch(() => {});
    }
    if (req.method() === 'POST' && url.pathname === '/api/claims') {
      const title = req.postDataJSON().title;
      if (title.startsWith('Old session')) {
        const response = await route.fetch();
        expect(response.ok()).toBeTruthy();
        oldId = (await response.json()).id;
        oldStarted(); await oldGate; return route.fulfill({ response }).catch(() => {});
      }
      if (title.startsWith('New session')) {
        const response = await route.fetch();
        expect(response.ok()).toBeTruthy();
        newStarted(); await newGate; return route.fulfill({ response }).catch(() => {});
      }
    }
    if (oldId && req.method() === 'POST' && (url.pathname === `/api/claims/${oldId}/receipt` || url.pathname === `/api/claims/${oldId}/submit`)) oldFollowups++;
    return route.continue();
  });
  try {
    await signIn(page, 'employee');
    await listPending;
    await page.getByRole('button', { name: 'New claim' }).click();
    await page.getByLabel('Claim title').fill(`Old session ${randomUUID()}`);
    await page.getByLabel('Category').selectOption('meals');
    await page.getByLabel('Choose receipt').setInputFiles({ name: 'old.png', mimeType: 'image/png', buffer: receiptPng });
    await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
    await page.getByLabel('Requested amount (₹)').fill('10');
    await page.getByLabel('Business purpose / expense details').fill('Old session');
    await page.getByRole('button', { name: 'Submit', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Submit claim' }).click();
    await oldPending;
    expect((await page.request.post('/api/auth/logout')).status()).toBe(204);
    releaseList();
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    await expect(page.getByRole('alert')).toContainText('session has expired');
    await signIn(page, 'employee');
    await page.getByRole('button', { name: 'New claim' }).click();
    const newTitle = `New session ${randomUUID()}`;
    await page.getByLabel('Claim title').fill(newTitle);
    await page.getByLabel('Category').selectOption('others');
    await page.getByLabel('Expense date and time').fill('2020-01-02T10:00');
    await page.getByLabel('Requested amount (₹)').fill('20');
    await page.getByLabel('Business purpose / expense details').fill('New form must survive');
    await page.getByRole('button', { name: 'Save as draft' }).click();
    await newPending;
    releaseOld();
    await page.waitForTimeout(500);
    expect(oldFollowups).toBe(0);
    await expect(page.getByLabel('Claim title')).toHaveValue(newTitle);
    await expect(page.getByRole('button', { name: 'Saving…' })).toBeDisabled();
    releaseNew();
    await expect(page.getByRole('heading', { name: newTitle })).toBeVisible();
    expect(oldFollowups).toBe(0);
    const newId = Number((await page.locator('.claim-detail .eyebrow').textContent()).match(/#(\d+)/)[1]);
    expect(newId).not.toBe(oldId);
    const newer = await (await page.request.get(`/api/claims/${newId}`)).json();
    expect(newer.receipt).toBeNull();
    expect(newer.history.map(event => event.action)).toEqual(['CREATED']);
    const old = await (await page.request.get(`/api/claims/${oldId}`)).json();
    expect(old.status).toBe('DRAFT');
    expect(old.receipt).toBeNull();
  } finally { releaseList(); releaseOld(); releaseNew(); }
});
