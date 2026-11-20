import { useState } from 'react';

export function ReceiptPicker({ onFile, busy, inputRef }) {
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
  return <div className="receipt-picker">
    <label>Choose receipt<input ref={inputRef} type="file" accept=".jpg,.jpeg,.png,.pdf" onChange={choose} disabled={busy} /></label>
    <p className="label-help">PNG, JPG, JPEG or PDF, up to 5 MB (5,000,000 bytes); images up to 20 MP and PDFs up to 10 unencrypted pages.</p>
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}

export default function ReceiptPanel({ claim, editable, busy, onUpload, onRemove }) {
  const [file, setFile] = useState(null);
  const receipt = claim.receipt;
  const url = receipt && `/api/claims/${claim.id}/receipts/${receipt.id}`;

  return <section className="receipt-panel" aria-labelledby="receipt-heading">
    <h3 id="receipt-heading">Receipt</h3>
    {editable && <p className="label-help">A receipt is required before submission or resubmission.</p>}
    {receipt ? <div className="receipt-current">
      <p>{receipt.filename}</p>
      {receipt.content_type.startsWith('image/') && <img src={url} alt="Receipt preview" />}
      <a href={url}>{receipt.content_type === 'application/pdf' ? 'Download receipt' : 'View receipt'}</a>
    </div> : <p>No receipt attached</p>}
    {editable && <div className="receipt-upload">
      <ReceiptPicker onFile={setFile} busy={busy} />
      {file && <p>Selected: {file.name}</p>}
      <div className="form-actions">
        <button className="button button-secondary" type="button" disabled={busy || !file} onClick={async () => { if (await onUpload(file)) setFile(null); }}>{receipt ? 'Replace receipt' : 'Attach receipt'}</button>
        {receipt && <button className="button button-danger-text" type="button" disabled={busy} onClick={onRemove}>Remove receipt</button>}
      </div>
    </div>}
  </section>;
}
