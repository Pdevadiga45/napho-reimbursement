const MAX_PAISE = 10_000_000_000;

export function parseRupees(value) {
  if (typeof value !== 'string' || !/^\d+(?:\.\d{1,2})?$/.test(value.trim())) {
    throw new Error('Enter a positive amount with up to two decimal places.');
  }

  const [rupeesText, fractionText = ''] = value.trim().split('.');
  const rupees = Number(rupeesText);
  const paise = Number(fractionText.padEnd(2, '0'));
  const amount = rupees * 100 + paise;

  if (!Number.isSafeInteger(amount) || amount <= 0 || amount > MAX_PAISE) {
    throw new Error('Amount must be greater than ₹0 and no more than ₹10,00,00,000.');
  }
  return amount;
}

export function formatRupees(paise) {
  if (paise === null || paise === undefined) return '—';
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(paise / 100);
}
