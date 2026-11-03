// Shared loading / error / empty states so no page is ever stuck on "Loading…".
export function ErrorState({ message, detail, onRetry }) {
  return (
    <div className="card state" role="alert">
      <b>{message}</b>
      {detail && <p className="sub">{detail === "Failed to fetch" ? "The app server is not answering. Is it running?" : detail}</p>}
      {onRetry && <button className="btn ghost" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ children, action }) {
  return (
    <div className="empty">
      <p>{children}</p>
      {action}
    </div>
  );
}
