import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { receiptDemoStatus } from '../src/receiptDemo.js';

const png = readFileSync(new URL('../../ocr/examples/demo-receipt.png', import.meta.url));
const receipt = { sha256: createHash('sha256').update(png).digest('hex') };

test('uses the recorded total for the checked-in receipt by default', () => {
  assert.deepEqual(receiptDemoStatus(receipt, 77699), { kind: 'verified', label: 'Amount verified', belowTotal: true });
  assert.deepEqual(receiptDemoStatus(receipt, 77700), { kind: 'verified', label: 'Amount verified', belowTotal: false });
  assert.deepEqual(receiptDemoStatus(receipt, 77701), { kind: 'anomaly', label: 'Anomaly detected', differencePaise: 1 });
});

test('omits the badge without an exact receipt match and a valid request', () => {
  for (const otherReceipt of [null, {}, { sha256: 'b'.repeat(64) }]) {
    assert.equal(receiptDemoStatus(otherReceipt, 77700), null);
  }
  for (const amount of [undefined, null, '77700', 0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(receiptDemoStatus(receipt, amount), null);
  }
});
