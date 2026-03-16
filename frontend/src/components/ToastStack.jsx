export default function ToastStack({ toasts }) {
  if (!toasts.length) return null;

  return (
    <div className="toast-stack" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast-banner toast-${toast.kind}`}>
          {toast.text}
        </div>
      ))}
    </div>
  );
}
