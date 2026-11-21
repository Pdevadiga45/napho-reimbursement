export function getReceiptDemoFixture({
  DEV,
  VITE_RECEIPT_DEMO_ENABLED: enabled,
  VITE_RECEIPT_DEMO_SHA256: sha256,
  VITE_RECEIPT_DEMO_TOTAL_PAISE: total,
}) {
  if (DEV !== true || enabled !== 'true') return null;
  if (sha256 === undefined && total === undefined) {
    return {
      sha256: '14349968f7fe0e72b8dba106b86273ab6ed4b4292c3377517b1619e58a9ea566',
      totalPaise: 77700,
    };
  }
  if (!/^[a-f\d]{64}$/i.test(sha256 ?? '') || !/^\d+$/.test(total ?? '')) return null;
  const totalPaise = Number(total);
  if (!Number.isSafeInteger(totalPaise) || totalPaise <= 0) return null;
  return { sha256: sha256.toLowerCase(), totalPaise };
}

export function receiptDemoStatus(fixture, receipt, amountPaise) {
  if (!fixture || !receipt) return null;
  const unverified = { kind: 'unverified', label: 'Total not verified' };
  if (receipt.sha256 !== fixture.sha256 || !Number.isSafeInteger(amountPaise) || amountPaise <= 0) return unverified;
  const differencePaise = amountPaise - fixture.totalPaise;
  return differencePaise > 0
    ? { kind: 'anomaly', label: 'Anomaly detected', differencePaise }
    : { kind: 'verified', label: 'Amount verified', belowTotal: differencePaise < 0 };
}
