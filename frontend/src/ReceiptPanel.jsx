import { useState } from 'react';

export default function ReceiptPanel({ claim, editable, busy, onUpload, onRemove }) {
  const [file, setFile] = useState(null);
  const [error, setError] = useState('');
  const receipt = claim.receipt;
  const url = receipt && `/api/claims/${claim.id}/receipts/${receipt.id}`;

  function choose(event) {
    const selected = event.target.files?.[0] || null;
    setFile(null);
    setError('');
    if (!selected) return;
    if (!/\.(png|jpe?g|pdf)$/i.test(selected.name) || selected.name.length > 200) {
      setError('Choose a PNG, JPG, JPEG, or PDF file with a name of at most 200 characters.');
    } else if (selected.size > 5 * 1024 * 1024) {
      setError('Receipt must be no larger than 5 MiB.');
    } else {
      setFile(selected);
    }
  }

  return <section className="receipt-panel" aria-labelledby="receipt-heading">
    <h3 id="receipt-heading">Receipt</h3>
    {receipt ? <div className="receipt-current">
      <p>{receipt.filename}</p>
      {receipt.content_type.startsWith('image/') && <img src={url} alt="Receipt preview" />}
      <a href={url}>{receipt.content_type === 'application/pdf' ? 'Download receipt' : 'View receipt'}</a>
    </div> : <p>No receipt attached</p>}
    {editable && <div className="receipt-upload">
      <label>Choose receipt<input type="file" accept=".jpg,.jpeg,.png,.pdf" onChange={choose} disabled={busy} /></label>
      <p className="label-help">PNG, JPG, JPEG or PDF, up to 5 MiB; images up to 20 MP and PDFs up to 10 unencrypted pages.</p>
      {file && <p>Selected: {file.name}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions">
        <button className="button button-secondary" type="button" disabled={busy || !file} onClick={async () => { if (await onUpload(file)) setFile(null); }}>{receipt ? 'Replace receipt' : 'Attach receipt'}</button>
        {receipt && <button className="button button-danger-text" type="button" disabled={busy} onClick={onRemove}>Remove receipt</button>}
      </div>
    </div>}
  </section>;
}
