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

// Small line illustrations for empty pages. Decorative: the text next to them says everything.
const ART = {
  leads: (
    <>
      <rect x="22" y="30" width="76" height="54" rx="10" className="art-card" />
      <rect x="34" y="18" width="76" height="54" rx="10" className="art-card art-front" />
      <circle cx="54" cy="40" r="8" className="art-blue" />
      <path d="M42 62a12 12 0 0 1 24 0" className="art-blue" />
      <path d="M76 38h22M76 48h16" className="art-line" />
      <circle cx="104" cy="74" r="12" className="art-dot-red" /><path d="M104 68v12M98 74h12" className="art-plus" />
    </>
  ),
  tasks: (
    <>
      <rect x="30" y="14" width="68" height="80" rx="10" className="art-card art-front" />
      <path d="M44 36l5 5 9-9M44 56l5 5 9-9" className="art-green" />
      <path d="M66 37h20M66 57h16M44 76h40" className="art-line" />
      <circle cx="100" cy="82" r="12" className="art-dot-blue" /><path d="M100 76v6l4 3" className="art-plus" />
    </>
  ),
  campaigns: (
    <>
      <circle cx="62" cy="54" r="36" className="art-ring" /><circle cx="62" cy="54" r="22" className="art-ring" />
      <circle cx="62" cy="54" r="7" className="art-dot-red" />
      <path d="M62 54l34-30M88 24h8v8" className="art-blue" />
      <path d="M20 96h84" className="art-line" />
    </>
  ),
  integrations: (
    <>
      <rect x="14" y="36" width="34" height="34" rx="9" className="art-card art-front" />
      <rect x="80" y="18" width="34" height="34" rx="9" className="art-card art-front" />
      <rect x="80" y="62" width="34" height="34" rx="9" className="art-card art-front" />
      <path d="M48 53h12c6 0 8-18 14-18h6M60 53c6 0 8 26 14 26h6" className="art-blue" />
      <circle cx="31" cy="53" r="6" className="art-dot-red" /><circle cx="97" cy="35" r="5" className="art-dot-green" /><circle cx="97" cy="79" r="5" className="art-dot-blue" />
    </>
  ),
};

export function EmptyArt({ name }) {
  return (
    <svg className="empty-art" viewBox="0 0 128 108" width="128" height="108" aria-hidden="true" focusable="false">{ART[name]}</svg>
  );
}
