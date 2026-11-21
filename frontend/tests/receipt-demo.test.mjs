import assert from 'node:assert/strict';
import { test } from 'node:test';
import { getReceiptDemoFixture, receiptDemoStatus } from '../src/receiptDemo.js';

const receipt = { sha256: 'a'.repeat(64) };
const optedIn = { DEV: true, VITE_RECEIPT_DEMO_ENABLED: 'true' };
const localOverride = {
  VITE_RECEIPT_DEMO_SHA256: receipt.sha256,
  VITE_RECEIPT_DEMO_TOTAL_PAISE: '12345',
};
const env = {
  ...optedIn,
  ...localOverride,
};
const fixture = () => getReceiptDemoFixture(env);

for (const [setting, flag] of [
  ['absent', {}],
  ['false', { VITE_RECEIPT_DEMO_ENABLED: 'false' }],
]) {
  test(`keeps the badge disabled with a complete local override when opt-in is ${setting}`, () => {
    const configured = getReceiptDemoFixture({ DEV: true, ...localOverride, ...flag });
    assert.equal(receiptDemoStatus(configured, receipt, 12345), null);
  });
}

test('uses the shipped receipt and recorded total with exact development opt-in', () => {
  const shippedReceipt = { sha256: '14349968f7fe0e72b8dba106b86273ab6ed4b4292c3377517b1619e58a9ea566' };
  const configured = getReceiptDemoFixture(optedIn);
  assert.deepEqual(configured, { ...shippedReceipt, totalPaise: 77700 });
  assert.equal(receiptDemoStatus(configured, shippedReceipt, 77700).kind, 'verified');
  assert.equal(receiptDemoStatus(configured, shippedReceipt, 77701).differencePaise, 1);
  assert.equal(receiptDemoStatus(configured, receipt, 77700).kind, 'unverified');
  for (const enabled of [undefined, '', 'false', 'TRUE', true, 'true ']) {
    assert.equal(getReceiptDemoFixture({ ...optedIn, VITE_RECEIPT_DEMO_ENABLED: enabled }), null);
  }
  assert.equal(getReceiptDemoFixture({ ...optedIn, DEV: false }), null);
});

test('honors complete local overrides and fails closed on partial or invalid overrides', () => {
  assert.deepEqual(fixture(), { ...receipt, totalPaise: 12345 });
  for (const override of [
    { VITE_RECEIPT_DEMO_SHA256: receipt.sha256 },
    { VITE_RECEIPT_DEMO_TOTAL_PAISE: '12345' },
    { VITE_RECEIPT_DEMO_SHA256: '' },
    { VITE_RECEIPT_DEMO_TOTAL_PAISE: '' },
    { ...env, VITE_RECEIPT_DEMO_TOTAL_PAISE: '0' },
  ]) {
    assert.equal(getReceiptDemoFixture({ ...optedIn, ...override }), null);
  }
});

test('requires development mode and both valid fixture settings', () => {
  for (const override of [
    { DEV: false }, { DEV: undefined }, { VITE_RECEIPT_DEMO_SHA256: '' },
    { VITE_RECEIPT_DEMO_SHA256: 'bad' }, { VITE_RECEIPT_DEMO_TOTAL_PAISE: '' },
    { VITE_RECEIPT_DEMO_TOTAL_PAISE: '1.5' }, { VITE_RECEIPT_DEMO_TOTAL_PAISE: '9007199254740992' },
  ]) {
    const disabled = getReceiptDemoFixture({ ...env, ...override });
    assert.equal(disabled, null);
    assert.equal(receiptDemoStatus(disabled, receipt, 12345), null);
  }
  assert.equal(receiptDemoStatus(fixture(), null, 12345), null);
});

test('verifies covered amounts and flags only requests above the recorded total', () => {
  const status = (amountPaise) => receiptDemoStatus(fixture(), receipt, amountPaise);
  for (const amount of [undefined, null, '', '12345', 0, -1, 12345.6, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(status(amount).label, 'Total not verified');
  }
  assert.deepEqual(status(12345), { kind: 'verified', label: 'Amount verified', belowTotal: false });
  assert.deepEqual(status(12344), { kind: 'verified', label: 'Amount verified', belowTotal: true });
  assert.deepEqual(status(12346), { kind: 'anomaly', label: 'Anomaly detected', differencePaise: 1 });
});

test('requires a matching persisted receipt hash', () => {
  for (const sha256 of [undefined, null, 'invalid', 'b'.repeat(64)]) {
    assert.equal(receiptDemoStatus(fixture(), { sha256 }, 12345).label, 'Total not verified');
  }
});
