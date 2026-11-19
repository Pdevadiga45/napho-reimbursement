import { useLayoutEffect, useRef } from 'react';

export default function ConfirmDialog({ title, description, claimTitle, confirmLabel, trigger, onCancel, onConfirm }) {
  const dialogRef = useRef(null);

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    dialog.showModal();
    return () => {
      dialog.close();
      if (trigger?.isConnected) trigger.focus();
    };
  }, [trigger]);

  return (
    <dialog
      ref={dialogRef}
      className="confirm-dialog"
      aria-labelledby="confirm-title"
      aria-describedby="confirm-description confirm-claim"
      onCancel={(event) => { event.preventDefault(); onCancel(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onCancel();
      }}
    >
      <h2 id="confirm-title">{title}</h2>
      <p id="confirm-claim" className="confirm-claim">{claimTitle}</p>
      <p id="confirm-description">{description}</p>
      <div className="confirm-actions">
        <button className="button button-secondary" type="button" autoFocus onClick={onCancel}>Cancel</button>
        <button className="button button-primary" type="button" onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </dialog>
  );
}
