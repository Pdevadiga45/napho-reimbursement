import { useState } from 'react';

export function ReceiptPicker({ file, onFile, busy, inputRef }) {
  const [error, setError] = useState('');
  function choose(event) {
    const selected = event.target.files?.[0] || null;
    onFile(null);
    setError('');
    if (!selected) return;
    if (!/\.(png|jpe?g|pdf)$/i.test(selected.name) || selected.name.length > 200) {
      setError('Choose a PNG, JPG, JPEG, or PDF file with a name of at most 200 characters.');
    } else if (selected.size > 5_000_000) {
      setError('Receipt must be no larger than 5 MB.');
    } else onFile(selected);
  }
  function clear() {
    onFile(null);
    setError('');
    if (inputRef.current) inputRef.current.value = '';
  }
  return <div className="receipt-picker">
    <label>Choose receipt (mandatory)<input ref={inputRef} type="file" accept=".jpg,.jpeg,.png,.pdf" onChange={choose} disabled={busy} /></label>
    <p className="label-help">PNG/JPG/PDF · Max 5 MB · Images 20 MP · PDFs 10 pages (no password)</p>
    {(file || error) && <div className="receipt-selection">{file && <span>{file.name}</span>}<button type="button" aria-label="Clear selected receipt" disabled={busy} onClick={clear}>×</button></div>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}

export default function ReceiptPanel({ claim, editable, busy, onRemove }) {
  const receipt = claim.receipt;
  const url = receipt && `/api/claims/${claim.id}/receipts/${receipt.id}`;

  return <section className="receipt-panel" aria-labelledby="receipt-heading">
    <h3 id="receipt-heading">Receipt</h3>
    {receipt ? <div className="receipt-current">
      <p>{receipt.filename}</p>
      {receipt.content_type.startsWith('image/') && <img src={url} alt="Receipt preview" />}
      <a href={url}>{receipt.content_type === 'application/pdf' ? 'Download receipt' : 'View receipt'}</a>
    </div> : <p>No receipt attached</p>}
    {editable && receipt && <button className="button button-danger-text" type="button" disabled={busy} onClick={onRemove}>Remove receipt</button>}
  </section>;
}
