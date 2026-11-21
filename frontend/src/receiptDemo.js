const DEMO_RECEIPT_SHA256 = '14349968f7fe0e72b8dba106b86273ab6ed4b4292c3377517b1619e58a9ea566';
export const RECORDED_TOTAL_PAISE = 77700;

export function receiptDemoStatus(receipt, amountPaise) {
  if (receipt?.sha256 !== DEMO_RECEIPT_SHA256 || !Number.isSafeInteger(amountPaise) || amountPaise <= 0) return null;
  const differencePaise = amountPaise - RECORDED_TOTAL_PAISE;
  return differencePaise > 0
    ? { kind: 'anomaly', label: 'Anomaly detected', differencePaise }
    : { kind: 'verified', label: 'Amount verified', belowTotal: differencePaise < 0 };
}
