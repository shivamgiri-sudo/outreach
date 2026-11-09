import { useEffect, useMemo, useRef, useState } from "react";
import { api, canWrite, getAccount, getToken, getWorkspace, setAccount, setCanEditContacts, setClient, setMyWorkspaces, setRole, setToken, setWorkspace } from "./api.js";
import Icon from "./icons.jsx";
import { stageLabel } from "./labels.js";
import Login from "./pages/Login.jsx";
import Workflows from "./pages/Workflows.jsx";
import FlowBuilder from "./pages/FlowBuilder.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Leads, { phoneMatches } from "./pages/Leads.jsx";
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
import Tasks from "./pages/Tasks.jsx";
import LinkedInQueue from "./pages/LinkedInQueue.jsx";
import Replies from "./pages/Replies.jsx";
import Lists from "./pages/Lists.jsx";
import Companies from "./pages/Companies.jsx";
import Intelligence from "./pages/Intelligence.jsx";
import Signals from "./pages/Signals.jsx";
import Hygiene from "./pages/Hygiene.jsx";
import Campaigns from "./pages/Campaigns.jsx";
import HiringEngine from "./pages/HiringEngine.jsx";
import MyDay from "./pages/MyDay.jsx";
import Analytics from "./pages/Analytics.jsx";
import SalesFunnel from "./pages/SalesFunnel.jsx";
import Reports from "./pages/Reports.jsx";
import Help from "./pages/Help.jsx";
import ProspectSearch from "./pages/ProspectSearch.jsx";
import { THEMES, getTheme, setTheme } from "./theme.js";
import { DialogProvider } from "./Dialog.jsx";
import { useModalFocus } from "./DrawerShell.jsx";
import { loadBrand, loadPublicBrand, useBrand } from "./brand.js";
import Onboarding from "./pages/Onboarding.jsx";
import { PendingApproval, PlatformBanners, usePlatformStatus } from "./pages/PendingApproval.jsx";
import { platform } from "./platformApi.js";

// [id, label, component, icon]
const PAGES = [
  ["myday", "My day", MyDay, "tasks"],
  ["dashboard", "Dashboard", Dashboard, "dashboard"],
  ["prospects", "Prospect search", ProspectSearch, "search"],
  ["leads", "Leads", Leads, "users"],
  ["hygiene", "Data hygiene", Hygiene, "check"],
  ["lists", "Lists", Lists, "tasks"],
  ["companies", "Companies", Companies, "users"],
  ["campaigns", "Campaigns", Campaigns, "rocket"],
  ["engine", "Hiring engine", HiringEngine, "workflow"],
  ["signals", "Buying signals", Signals, "bell"],
  ["sources", "Data sources", DataSources, "layers"],
  ["analytics", "Analytics", Analytics, "chart"],
  ["funnel", "Sales funnel", SalesFunnel, "chart"],
  ["reports", "Reports", Reports, "tasks"],
  ["insights", "Insights", Insights, "chart"],
  ["intelligence", "Intelligence", Intelligence, "eye"],
  ["growth", "Free experience", Growth, "gift"],
  ["flows", "Flow builder", FlowBuilder, "workflow"],
  ["workflows", "Simple rules", Workflows, "rules"],
  ["outbox", "Outbox & sending", Outbox, "send"],
  ["calls", "Call desk", CallDesk, "phone"],
  ["meetings", "Meetings", Meetings, "calendar"],
  ["replies", "Replies", Replies, "inbox"],
  ["tasks", "Tasks & follow-ups", Tasks, "tasks"],
  ["linkedin", "LinkedIn outreach", LinkedInQueue, "linkedin"],
  ["templates", "Sequences & templates", Templates, "mail"],
  ["stories", "Success stories", Stories, "star"],
  ["prompts", "Prompt generator", PromptGenerator, "wand"],
  ["setup", "Setup", Setup, "settings"],
  ["integrations", "Integrations", Integrations, "plug"],
  ["help", "Help centre", Help, "help"],
];

// Menu groups. Ids that are not in PAGES yet (myday, companies, analytics) are skipped, so a new page only needs its
// PAGES entry; when Analytics replaces Insights + Intelligence, drop those two from PAGES and alias them below.
const GROUPS_ALL = [
  ["Today", ["myday", "dashboard"]],
  ["Pipeline", ["prospects", "leads", "companies", "lists", "campaigns", "growth"]],
  ["Conversations", ["replies", "calls", "linkedin", "tasks", "meetings", "outbox"]],
  ["Content & automation", ["templates", "flows", "workflows", "stories", "prompts"]],
  ["Analytics", ["analytics", "funnel", "reports"]],
  ["Data", ["sources", "signals", "hygiene", "engine"]],
  ["Settings", ["setup", "integrations", "help"]],
];
const BY_ID = Object.fromEntries(PAGES.map((p) => [p[0], p]));
// Pages whose every API call is owner-only: hidden from callers and viewers, and a short note if they open the link.
const OWNER_PAGES = new Set(["engine"]);
// A login limited to some workspaces sees only pages built from its own workspace's leads (the server refuses the rest).
const LIMITED_PAGES = new Set(["myday", "dashboard", "leads", "companies", "lists", "analytics", "funnel", "replies", "calls", "tasks", "meetings", "outbox", "templates", "setup", "help", "reports", "linkedin", "flows"]);
const isLimited = (me) => me.role !== "owner" && ((me.workspaces || []).length > 0 || me.role === "admin");
const pageAllowed = (me, id) => (me.role === "owner" || !OWNER_PAGES.has(id)) && (!isLimited(me) || LIMITED_PAGES.has(id));
const GROUPS = GROUPS_ALL.map(([g, ids]) => [g, ids.filter((id) => BY_ID[id])]).filter(([, ids]) => ids.length);
// Old links to the separate Go live page now open the checklist inside Setup.
const ALIASES = { golive: "setup" };
const HOME = BY_ID.myday ? "myday" : "dashboard";
// Phone bottom bar: the four pages people open most, plus "More" for the full menu.
const BOTTOM = [HOME, "leads", "replies", "tasks"].filter((id) => BY_ID[id]);
const SHORT = { myday: "My day", dashboard: "Home", tasks: "Tasks", replies: "Replies", leads: "Leads" };

function pageFromHash() {
  const id = window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  const resolved = ALIASES[id] || id;
  return BY_ID[resolved] ? resolved : HOME;
}

const PHONE = "(max-width: 760px)";
function useIsPhone() {
  const [phone, setPhone] = useState(() => window.matchMedia(PHONE).matches);
  useEffect(() => {
    const mq = window.matchMedia(PHONE);
    const on = () => setPhone(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return phone;
}

// Reminders bell: unread task and meeting reminders, checked every minute.
function Bell({ onOpen }) {
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const load = () => api.notifications(true).then(setItems).catch(() => {});
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="bell-wrap" onKeyDown={(e) => { if (e.key === "Escape" && open) { e.stopPropagation(); setOpen(false); } }}>
      <button className="bell" aria-label={`Reminders: ${items.length} unread`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="bell" size={18} />{items.length > 0 && <span className="badge-dot">{items.length}</span>}
      </button>
      {open && (
        <div className="bell-panel" role="dialog" aria-label="Reminders">
          {items.length === 0 ? <p className="sub">No new reminders.</p> : items.slice(0, 8).map((n) => (
            <div key={n.id} className="bell-item"><b>{n.title}</b>{n.text && <div className="sub">{n.text.slice(0, 120)}</div>}</div>
          ))}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn ghost" onClick={() => { setOpen(false); onOpen(); }}>Open tasks</button>
            {items.length > 0 && canWrite() && <button className="btn ghost" onClick={() => api.markNotificationsRead().then(() => setItems([]))}>Mark all read</button>}
          </div>
        </div>
      )}
    </div>
  );
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
    ...(q ? leads.filter((l) => [l.company, l.domain, l.contact_name, l.email, l.phone].join(" ").toLowerCase().includes(q) || phoneMatches(l.phone, q)).slice(0, 8)
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
  return <DialogProvider><Shell /></DialogProvider>;
}

function Shell() {
  const [page, setPage] = useState(pageFromHash);
  const [searching, setSearching] = useState(false);
  const [menuOpen, setMenuOpenState] = useState(false);
  const [menuUsed, setMenuUsed] = useState(false);       // slide animation only after the first open (no flash when resizing)
  const setMenuOpen = (open) => { if (open) setMenuUsed(true); setMenuOpenState(open); };
  const phone = useIsPhone();
  const navRef = useRef(null);
  const closeMenu = () => setMenuOpen(false);
  useModalFocus(navRef, closeMenu, phone && menuOpen);
  useEffect(() => { if (!phone) setMenuOpen(false); }, [phone]);
  const [theme, setThemeState] = useState(getTheme());
  const cycleTheme = () => {
    const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    setTheme(next); setThemeState(next);
  };
  const [pending, setPending] = useState(0);
  const [auth, setAuth] = useState({ checked: false, required: false, signedIn: false });
  const [me, setMe] = useState(null);
  const brand = useBrand().public;
  const [wizard, setWizard] = useState(false);
  useEffect(() => { loadPublicBrand(); }, []);
  useEffect(() => {
    if (!auth.signedIn || !me || me.pending) return;
    loadBrand();
    // The first-run wizard: owners only, until they finish or skip it (an install that already has leads is past it).
    if (me.role === "owner") api.onboarding().then((o) => o.show && setWizard(true)).catch(() => {});
  }, [auth.signedIn, me]);

  // go("leads") or go("leads?lead=<id>"): the part after "?" is kept in the hash for the page to read.
  const go = (id) => {
    const [path, qs] = String(id).split("?");
    const [base, ...sub] = path.split("/");            // "setup/members": the page, then its own section
    const target = ALIASES[base] || base;
    const full = `${target}${sub.length ? `/${sub.join("/")}` : ""}${qs ? `?${qs}` : ""}`;
    setMenuOpen(false);
    setPage(target);
    if (window.location.hash.replace(/^#\/?/, "") !== full) window.location.hash = full;
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
    window.addEventListener("popstate", onHash);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("auth-expired", expired);
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const refreshPending = () => api.readiness()
    .then((r) => setPending(r.items.filter((i) => i.required && i.status !== "done").length)).catch(() => {});
  useEffect(() => { if (auth.signedIn && me && !me.pending) refreshPending(); }, [page, auth.signedIn, me]);
  useEffect(() => {
    if (!auth.signedIn) { setMe(null); return; }
    Promise.all([api.me(), platform.status().catch(() => null)]).then(([m, st]) => {
      m = { ...m, pending: st?.account_status === "pending" };
      setRole(m.role); setCanEditContacts(m.can_edit_contacts); setMyWorkspaces(m.workspaces); setClient(m.client); setMe(m);
      if ((m.role === "caller" || m.role === "admin") && !window.location.hash.replace(/^#\/?/, "")) setPage("myday");     // callers start on My day
    }).catch(() => setMe({ role: "owner" }));
  }, [auth.signedIn]);
  const [spaces, setSpaces] = useState([]);
  const [space, setSpace] = useState(getWorkspace());
  const [spacesTick, setSpacesTick] = useState(0);
  useEffect(() => {
    const bump = () => setSpacesTick((n) => n + 1);           // leads moved or a workspace added/removed
    window.addEventListener("workspaces-changed", bump);
    return () => window.removeEventListener("workspaces-changed", bump);
  }, []);
  const platformStatus = usePlatformStatus(auth.signedIn && !!me && !me.pending);
  useEffect(() => { if (auth.signedIn && me && !me.pending) api.workspaces().then((w) => {
    setSpaces(w);
    if (getWorkspace() && !w.some((x) => x.id === getWorkspace())) { setWorkspace(""); setSpace(""); }
  }).catch(() => {}); }, [auth.signedIn, me, page, space, spacesTick]);
  const pickSpace = (id) => { setWorkspace(id); setSpace(id); };
  // Owners (platform staff) pick a client account once more than one exists; default Mas Callnet ("mcn").
  const [accts, setAccts] = useState([]);
  const [acct, setAcct] = useState(getAccount());
  useEffect(() => {
    const bump = () => setSpacesTick((n) => n + 1);
    window.addEventListener("accounts-changed", bump);
    return () => window.removeEventListener("accounts-changed", bump);
  }, []);
  useEffect(() => {
    if (!auth.signedIn || !me || me.role !== "owner" || me.client) { setAccts([]); return; }
    api.accounts().then((r) => {
      const list = r.accounts || [];
      setAccts(list);
      if (list.length > 1 && !list.some((a) => a.id === getAccount())) { setAccount("mcn"); setAcct("mcn"); }
      if (list.length <= 1 && getAccount()) { setAccount(""); setAcct(""); }
    }).catch(() => {});
  }, [auth.signedIn, me, spacesTick]);
  const pickAcct = (id) => { setAccount(id); setAcct(id); setWorkspace(""); setSpace(""); setSpacesTick((n) => n + 1); };
  const signOut = () => api.logout().catch(() => {}).finally(() => {
    setToken(""); setRole("owner"); setAuth((a) => ({ ...a, signedIn: false }));
  });

  if (!auth.checked) return <p className="empty">Loading…</p>;
  if (!auth.signedIn) return <Login onSignedIn={() => setAuth((a) => ({ ...a, required: true, signedIn: true }))} />;
  if (!me) return <p className="empty">Loading…</p>;
  if (me.pending) return <PendingApproval me={me} onSignOut={signOut} />;
  const Current = BY_ID[page][2];
  const acctPicker = (cls) => accts.length > 1 && (
    <select className={cls} aria-label="Account" data-testid="account-picker" value={acct} onChange={(e) => pickAcct(e.target.value)}>
      {accts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.status !== "active" ? ` (${a.status})` : ""}</option>)}
    </select>
  );
  const spacePicker = (cls) => spaces.length > 1 && (
    <select className={cls} aria-label="Workspace" value={space} onChange={(e) => pickSpace(e.target.value)}>
      {(!me.workspaces || me.workspaces.length !== 1) && <option value="">All workspaces</option>}
      {spaces.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.leads})</option>)}
    </select>
  );
  return (
    <div className={`layout${phone ? " is-phone" : ""}${menuOpen ? " menu-open" : ""}${menuUsed ? " menu-anim" : ""}`}>
      {phone && (
        <header className="topbar">
          <button className="icon-btn" aria-label="Open menu" aria-expanded={menuOpen} aria-controls="main-nav" onClick={() => setMenuOpen(true)}>
            <Icon name="menu" size={22} />
          </button>
          <img className="brand-mark" src={brand.header_logo} width="28" height="28" alt={brand.app_name} />
          <span className="topbar-page" aria-hidden="true">{BY_ID[page][1]}</span>
          {acctPicker("ws-mini")}
          {spacePicker("ws-mini")}
          <button className="icon-btn" aria-label="Quick search" onClick={() => setSearching(true)}><Icon name="search" size={20} /></button>
          <Bell onOpen={() => go("tasks")} />
        </header>
      )}
      {phone && menuOpen && <div className="scrim nav-scrim" onClick={closeMenu} aria-hidden="true" />}
      <nav className="side" id="main-nav" aria-label="Main" ref={navRef} role={phone ? "dialog" : undefined}
        aria-modal={phone && menuOpen ? "true" : undefined} aria-hidden={phone && !menuOpen ? "true" : undefined}>
        <div className="brand">
          <img className="brand-mark" src={brand.header_logo} width="34" height="34" alt="" />
          <div className="brand-text"><h1>{brand.app_name}</h1><span className="brand-sub">by {brand.company_name}</span></div>
          {phone ? <button className="icon-btn nav-close" aria-label="Close menu" onClick={closeMenu}><Icon name="close" size={22} /></button>
            : <Bell onOpen={() => go("tasks")} />}
        </div>
        {!phone && accts.length > 1 && (
          <label className="ws-pick" style={{ display: "block", margin: "0 12px 6px" }}>
            <span className="nav-title">Account</span>
            {acctPicker("")}
          </label>
        )}
        {!phone && (spaces.length > 1 || me.client) && (
          <label className="ws-pick" style={{ display: "block", margin: "0 12px 10px" }}>
            <span className="nav-title">{me.client ? <span data-testid="account-name">{me.account_name}</span> : "Workspace"}</span>
            {spacePicker("")}
          </label>
        )}
        {!phone && (
          <button className="search-trigger" onClick={() => setSearching(true)} aria-label="Quick search (Ctrl K)">
            <Icon name="search" size={16} /><span>Search…</span><kbd>Ctrl K</kbd>
          </button>
        )}
        {GROUPS.map(([group, all]) => [group, all.filter((id) => pageAllowed(me, id))]).filter(([, ids]) => ids.length).map(([group, ids]) => (
          <div className="nav-group" key={group} role="group" aria-label={group}>
            <span className="nav-title" aria-hidden="true">{group}</span>
            {ids.map((id) => (
              <button key={id} className={page === id ? "on" : ""} aria-current={page === id ? "page" : undefined}
                aria-label={BY_ID[id][1]} title={BY_ID[id][1]} onClick={() => go(id)}>
                <Icon name={BY_ID[id][3]} />
                <span className="nav-label">{BY_ID[id][1]}</span>
                {id === "setup" && pending > 0 && <span className="badge-dot" aria-label={`${pending} steps left`}>{pending}</span>}
              </button>
            ))}
          </div>
        ))}
        <button className="theme-toggle" onClick={cycleTheme} title="Switch between light, dark and the system setting" aria-label={`Colour theme: ${theme}. Click to change.`}>
          <Icon name={theme === "dark" ? "moon" : theme === "light" ? "sun" : "monitor"} /><span className="nav-label">Theme: {theme === "system" ? "Auto" : theme === "dark" ? "Dark" : "Light"}</span>
        </button>
        {auth.required && (
          <button className="signout" onClick={signOut} title={me.email ? `${me.name} (${me.role})` : `Signed in as ${me.role}`}>
            <Icon name="logout" /><span className="nav-label">Sign out</span>
          </button>
        )}
      </nav>
      <main>
        <PlatformBanners status={platformStatus} />
        {me.role === "viewer" && <p className="tip" role="note" data-testid="viewer-note" style={{ marginBottom: 12 }}>You have a view-only login: you can look at everything, but changes are refused. Ask an owner if you need to change something.</p>}
        {!pageAllowed(me, page)
          ? <><h2>{BY_ID[page][1]}</h2><p className="empty" role="status">{isLimited(me) ? "This page is not available for logins limited to a workspace." : "Only an owner can open this page."}</p></>
          : <Current key={`${page}:${space}`} go={go} onSetupChanged={refreshPending} role={me.role} onOpenWizard={me.role === "owner" ? () => setWizard(true) : undefined} />}
      </main>
      {phone && (
        <nav className="bottombar" aria-label="Quick links">
          {BOTTOM.map((id) => (
            <button key={id} className={page === id ? "on" : ""} aria-current={page === id ? "page" : undefined} onClick={() => go(id)}>
              <Icon name={BY_ID[id][3]} size={20} /><span>{SHORT[id] || BY_ID[id][1]}</span>
            </button>
          ))}
          <button aria-label="More pages" aria-expanded={menuOpen} aria-controls="main-nav" onClick={() => setMenuOpen(true)}>
            <Icon name="menu" size={20} /><span>More</span>
          </button>
        </nav>
      )}
      {wizard && <Onboarding go={go} onClose={() => { setWizard(false); loadBrand(); refreshPending(); }} />}
      {searching && (
        <QuickSearch onClose={() => setSearching(false)}
          onPage={(id) => { setSearching(false); go(id); }}
          onLead={(id) => { setSearching(false); go(`leads?lead=${encodeURIComponent(id)}`); }} />
      )}
    </div>
  );
}
