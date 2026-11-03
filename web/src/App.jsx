import { useEffect, useMemo, useRef, useState } from "react";
import { api, getToken, setToken } from "./api.js";
import Icon from "./icons.jsx";
import { stageLabel } from "./labels.js";
import Login from "./pages/Login.jsx";
import Workflows from "./pages/Workflows.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Leads from "./pages/Leads.jsx";
import Templates from "./pages/Templates.jsx";
import Stories from "./pages/Stories.jsx";
import PromptGenerator from "./pages/PromptGenerator.jsx";
import CallDesk from "./pages/CallDesk.jsx";
import Outbox from "./pages/Outbox.jsx";
import Growth from "./pages/Growth.jsx";
import Insights from "./pages/Insights.jsx";
import Setup from "./pages/Setup.jsx";
import DataSources from "./pages/DataSources.jsx";
import Integrations from "./pages/Integrations.jsx";
import Meetings from "./pages/Meetings.jsx";

// [id, label, component, icon]
const PAGES = [
  ["dashboard", "Dashboard", Dashboard, "dashboard"],
  ["leads", "Leads", Leads, "users"],
  ["sources", "Data sources", DataSources, "chart"],
  ["insights", "Insights", Insights, "chart"],
  ["growth", "Free experience", Growth, "gift"],
  ["workflows", "Workflows", Workflows, "workflow"],
  ["outbox", "Outbox & sending", Outbox, "send"],
  ["calls", "Priya's call desk", CallDesk, "phone"],
  ["meetings", "Meetings", Meetings, "calendar"],
  ["templates", "Sequences & templates", Templates, "mail"],
  ["stories", "Success stories", Stories, "star"],
  ["prompts", "Prompt generator", PromptGenerator, "wand"],
  ["setup", "Setup", Setup, "settings"],
  ["integrations", "Integrations", Integrations, "plug"],
];

const GROUPS = [
  ["Overview", ["dashboard"]],
  ["Pipeline", ["leads", "sources", "insights", "growth"]],
  ["Outreach", ["outbox", "workflows", "templates", "calls", "meetings"]],
  ["Library", ["stories", "prompts"]],
  ["Admin", ["setup", "integrations"]],
];
const BY_ID = Object.fromEntries(PAGES.map((p) => [p[0], p]));
// Old links to the separate Go live page now open the checklist inside Setup.
const ALIASES = { golive: "setup" };

function pageFromHash() {
  const id = window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  const resolved = ALIASES[id] || id;
  return BY_ID[resolved] ? resolved : "dashboard";
}

// Ctrl/Cmd + K: jump to any page or lead by typing.
function QuickSearch({ onClose, onPage, onLead }) {
  const [query, setQuery] = useState("");
  const [leads, setLeads] = useState([]);
  const [active, setActive] = useState(0);
  const input = useRef(null);
  useEffect(() => { input.current?.focus(); api.leads().then(setLeads).catch(() => {}); }, []);
  const q = query.trim().toLowerCase();
  const results = useMemo(() => [
    ...PAGES.filter(([, label]) => !q || label.toLowerCase().includes(q)).map(([id, label]) => ({ type: "page", id, label })),
    ...(q ? leads.filter((l) => [l.company, l.domain, l.contact_name, l.email].join(" ").toLowerCase().includes(q)).slice(0, 8)
      .map((l) => ({ type: "lead", id: l.id, label: l.company || l.domain, hint: `${stageLabel(l.stage)} · score ${l.score}` })) : []),
  ], [q, leads]);
  const choose = (r) => (r.type === "page" ? onPage(r.id) : onLead(r.id));
  const onKey = (e) => {
    if (e.key === "Escape") onClose();
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    if (e.key === "Enter" && results[active]) choose(results[active]);
  };
  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="palette" role="dialog" aria-modal="true" aria-label="Quick search">
        <input ref={input} value={query} onKeyDown={onKey} onChange={(e) => { setQuery(e.target.value); setActive(0); }}
          placeholder="Type a page or a company…" aria-label="Search pages and leads" />
        <ul role="listbox">
          {results.map((r, i) => (
            <li key={`${r.type}-${r.id}`} role="option" aria-selected={i === active}>
              <button className={i === active ? "on" : ""} onMouseEnter={() => setActive(i)} onClick={() => choose(r)}>
                <Icon name={r.type === "page" ? BY_ID[r.id][3] : "users"} size={16} />
                <span>{r.label}</span>{r.hint && <small>{r.hint}</small>}
              </button>
            </li>
          ))}
          {results.length === 0 && <li className="sub" style={{ padding: 12 }}>Nothing matches “{query}”.</li>}
        </ul>
      </div>
    </>
  );
}

export default function App() {
  const [page, setPage] = useState(pageFromHash);
  const [focusLead, setFocusLead] = useState(null);
  const [searching, setSearching] = useState(false);
  const [pending, setPending] = useState(0);
  const [auth, setAuth] = useState({ checked: false, required: false, signedIn: false });

  const go = (id) => {
    const target = ALIASES[id] || id;
    setFocusLead(null);
    setPage(target);
    if (pageFromHash() !== target) window.location.hash = target;
  };

  const checkAuth = () => api.authStatus()
    .then((d) => setAuth({ checked: true, required: d.auth_required, signedIn: !d.auth_required || !!getToken() }))
    .catch(() => setAuth({ checked: true, required: false, signedIn: true }));
  useEffect(() => {
    checkAuth();
    const expired = () => setAuth((a) => ({ ...a, signedIn: false }));
    const onHash = () => setPage(pageFromHash());
    const onKey = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setSearching(true); } };
    window.addEventListener("auth-expired", expired);
    window.addEventListener("hashchange", onHash);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("auth-expired", expired);
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const refreshPending = () => api.readiness()
    .then((r) => setPending(r.items.filter((i) => i.required && i.status !== "done").length)).catch(() => {});
  useEffect(() => { if (auth.signedIn) refreshPending(); }, [page, auth.signedIn]);

  if (!auth.checked) return <p className="empty">Loading…</p>;
  if (!auth.signedIn) return <Login onSignedIn={() => setAuth((a) => ({ ...a, signedIn: true }))} />;
  const Current = BY_ID[page][2];
  return (
    <div className="layout">
      <nav className="side" aria-label="Main">
        <div className="brand"><span className="brand-mark" aria-hidden="true">O</span><h1>Outreach OS</h1></div>
        <button className="search-trigger" onClick={() => setSearching(true)} aria-label="Quick search (Ctrl K)">
          <Icon name="wand" size={16} /><span>Search…</span><kbd>Ctrl K</kbd>
        </button>
        {GROUPS.map(([group, ids]) => (
          <div className="nav-group" key={group}>
            <span className="nav-title">{group}</span>
            {ids.map((id) => (
              <button key={id} className={page === id ? "on" : ""} aria-current={page === id ? "page" : undefined}
                aria-label={BY_ID[id][1]} title={BY_ID[id][1]} onClick={() => go(id)}>
                <Icon name={BY_ID[id][3]} />
                {BY_ID[id][1]}
                {id === "setup" && pending > 0 && <span className="badge-dot" aria-label={`${pending} steps left`}>{pending}</span>}
              </button>
            ))}
          </div>
        ))}
        {auth.required && (
          <button className="signout" onClick={() => { setToken(""); setAuth((a) => ({ ...a, signedIn: false })); }}>
            <Icon name="logout" />Sign out
          </button>
        )}
      </nav>
      <main>
        <Current key={page} go={go} onSetupChanged={refreshPending} focusLead={page === "leads" ? focusLead : undefined} />
      </main>
      {searching && (
        <QuickSearch onClose={() => setSearching(false)}
          onPage={(id) => { setSearching(false); go(id); }}
          onLead={(id) => { setSearching(false); go("leads"); setFocusLead(id); }} />
      )}
    </div>
  );
}
