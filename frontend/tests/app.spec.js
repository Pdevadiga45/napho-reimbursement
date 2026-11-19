import { test, expect } from '@playwright/test';
import { formatRupees, parseRupees } from '../src/money.js';

test('unauthenticated visitors see sign in', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByLabel('Username')).toBeVisible();
  await expect(page.getByLabel('Password')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Expense claims' })).toHaveCount(0);
});

async function signIn(page, username) {
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(`${username}123`);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Expense claims' })).toBeVisible();
}

async function switchUser(page, username) {
  await page.getByRole('button', { name: 'Log out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Expense claims' })).toHaveCount(0);
  await signIn(page, username);
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
  await expect(page.getByRole('heading', { name: 'Expense claims' })).toBeVisible();
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
  await page.getByRole('button', { name: 'Submit claim' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();

  await switchUser(page, 'approver');
  await expect(page.getByText('Approver view')).toBeVisible();
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
  await expect(page.getByText('Employee view')).toBeVisible();
  await expect(page.locator('.claim-detail .status-chip.status-paid')).toBeVisible();
  await expect(page.getByRole('button', { name: /Airport taxi/ })).toBeVisible();

  await page.getByRole('button', { name: 'New claim' }).click();
  await page.getByLabel('Claim title').fill('Hotel correction');
  await page.getByLabel('Category').fill('Accommodation');
  await page.getByLabel('Expense date').fill(expenseDate);
  await page.getByLabel('Requested amount (₹)').fill('2000');
  await page.getByLabel('Description').fill('Hotel stay during a work trip.');
  await page.getByRole('button', { name: 'Create draft' }).click();
  await page.getByRole('button', { name: 'Submit claim' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();

  await switchUser(page, 'approver');
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
  await switchUser(page, 'approver');
  await page.getByRole('button', { name: 'Reviewed history' }).click();
  await page.getByRole('button', { name: /Hotel correction/ }).click();
  await expect(page.locator('.claim-detail .status-chip.status-draft')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Review this claim' })).toHaveCount(0);
  await switchUser(page, 'employee');
  await page.getByRole('button', { name: /Hotel correction/ }).click();
  await page.getByRole('button', { name: 'Submit claim' }).click();
  await expect(page.locator('.claim-detail .status-chip.status-submitted')).toBeVisible();

  await switchUser(page, 'approver');
  await page.getByRole('button', { name: 'Reviewed history' }).click();
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
  await expect(page.locator('.event-actor').filter({ hasText: 'Approver' })).toHaveCount(2);
  await expect(page.getByText('Please check the submitted amount.')).toBeVisible();
  await page.getByRole('button', { name: 'Reviewed history' }).click();
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
  await page.getByRole('button', { name: 'Submit claim' }).click();

  await switchUser(page, 'approver');
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
