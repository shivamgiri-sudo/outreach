// Setup: a left sub-navigation of sections (grouped), one header per section, deep links as #setup/<section>.
// Sections a role cannot use are not shown (the server refuses them anyway). On phones the sub-navigation is a select.
import { useEffect, useState } from "react";
import { api } from "../api.js";
import GoLive from "./GoLive.jsx";
import BackupRestore from "./BackupRestore.jsx";
import ScoringCard from "./ScoringCard.jsx";
import AuditLog from "./AuditLog.jsx";
import Security from "./Security.jsx";
import TwoStepCard from "./TwoStep.jsx";
import SystemHealth from "./SystemHealth.jsx";
import { GuideButton } from "../GuidePanel.jsx";
import IndustrySetup from "./IndustrySetup.jsx";
import BrandSettings from "./BrandSettings.jsx";
import Connections from "./setup/Connections.jsx";
import OwnPassword from "./setup/OwnPassword.jsx";
import Team from "./setup/Team.jsx";
import Workspaces from "./setup/Workspaces.jsx";
import "../setup.css";

const OWNER = ["owner"];
const ADMINS = ["owner", "admin"];
const EVERYONE = ["owner", "admin", "caller", "viewer"];
// [id, label, roles, description]
const GROUPS = [
  ["Organisation", [
    ["checklist", "Go-live checklist", OWNER, "What is left before real emails go out. Each item links to where it is fixed."],
    ["brand", "Brand & offer", OWNER, "Your name, logo, colours and what you sell, used in emails, pages and the app."],
    ["industry", "Industry & fields", OWNER, "Start from an industry preset and add the custom fields your team fills on each lead."],
    ["scoring", "Lead scoring", OWNER, "How each lead's score is worked out, so the best leads come first."],
  ]],
  ["Team & access", [
    ["workspaces", "Workspaces", ADMINS, "One workspace per client keeps their leads, lists and numbers apart."],
    ["members", "Team members", ADMINS, "Who can sign in, what each role may do and which workspaces they see."],
    ["security", "Security", ADMINS, "Two-step login for the team and the history of every sign-in."],
    ["audit", "Audit", OWNER, "Who changed what and when, with a tamper check."],
  ]],
  ["Integrations", [
    ["connections", "Connections", OWNER, "Email sending, reply capture, data providers and other services. Keys are stored on your server and shown masked."],
  ]],
  ["Data", [
    ["backup", "Backup & restore", OWNER, "Download everything as one file, or restore from one."],
    ["health", "System health", OWNER, "Background jobs, storage and the last errors, checked live."],
  ]],
  ["My account", [
    ["account", "My password & two-step", EVERYONE, "Your own password and two-step login."],
  ]],
];
const LEGACY = { team: "members", checklist: "checklist", brand: "brand", connections: "connections", industry: "industry", backup: "backup",
  audit: "audit", security: "security", health: "health", account: "account" };

function fromHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const slash = /^setup\/([a-z-]+)/.exec(raw);
  if (slash) return slash[1];
  const tab = /[?&]tab=([a-z]+)/.exec(raw);
  return tab ? LEGACY[tab[1]] || "" : "";
}

export default function Setup({ onSetupChanged, go, tab: initialTab, role = "owner", onOpenWizard }) {
  const groups = GROUPS.map(([g, list]) => [g, list.filter(([, , roles]) => roles.includes(role))]).filter(([, list]) => list.length);
  const all = groups.flatMap(([g, list]) => list.map((s) => [...s, g]));
  const fallback = role === "owner" ? "checklist" : role === "admin" ? "members" : "account";
  const pick = (id) => (all.some((s) => s[0] === id) ? id : fallback);
  const [section, setSection] = useState(() => pick(initialTab || fromHash()));
  const [items, setItems] = useState([]);
  const load = () => api.integrations().then((list) => { setItems(list); onSetupChanged?.(); }).catch(() => {});
  useEffect(() => { if (role === "owner") load(); }, []);
  useEffect(() => {
    const sync = () => { const id = fromHash(); if (id) setSection(pick(id)); };
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("hashchange", sync); window.removeEventListener("popstate", sync); };
  }, [role]);
  const open = (id) => {
    setSection(id);
    const next = `#setup/${id}`;
    if (window.location.hash !== next) window.history.pushState(null, "", next);
    document.querySelector("main")?.scrollIntoView?.({ block: "start" });
  };
  const current = all.find((s) => s[0] === section) || all[0];
  const [, title, , description, group] = current;
  const done = items.filter((i) => i.status === "configured").length;
  const summary = (section === "connections" || section === "checklist") && items.length > 0;

  return (
    <div className="setup">
      <h2 className="setup-title">Setup</h2>
      <div className="setup-grid">
        <nav className="setup-nav" aria-label="Setup sections">
          {groups.map(([g, list]) => (
            <div key={g} className="setup-nav-group" role="group" aria-labelledby={`sn-${g.replace(/\W+/g, "")}`}>
              <span className="setup-nav-title" id={`sn-${g.replace(/\W+/g, "")}`}>{g}</span>
              {list.map(([id, label]) => (
                <a key={id} href={`#setup/${id}`} className={id === section ? "on" : ""} aria-current={id === section ? "page" : undefined}
                  onClick={(e) => { e.preventDefault(); open(id); }} data-testid={`setup-nav-${id}`}>{label}</a>
              ))}
            </div>
          ))}
        </nav>
        <div className="setup-nav-mobile">
          <label htmlFor="setup-section">Section</label>
          <select id="setup-section" value={section} onChange={(e) => open(e.target.value)}>
            {groups.map(([g, list]) => <optgroup key={g} label={g}>{list.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</optgroup>)}
          </select>
        </div>
        <div className="setup-body">
          <header className="setup-head">
            <p className="setup-kicker">{group}</p>
            <h2 data-testid="setup-section-title">{title}</h2>
            <p className="setup-desc">{description}</p>
            {summary && <p className="setup-summary" data-testid="setup-summary"><b>{done} of {items.length}</b> connected. Everything works without these; connect each one when you are ready.</p>}
          </header>
          {section === "account" && <div className="setup-stack"><OwnPassword /><TwoStepCard /></div>}
          {section === "security" && <Security />}
          {section === "checklist" && <GoLive embedded go={(page) => (page === "setup" ? open("connections") : go?.(page))} />}
          {section === "brand" && <BrandSettings onOpenWizard={onOpenWizard} />}
          {section === "backup" && <><p><GuideButton keys="app:backup" label="How do backups work?" testId="backup-guide" /></p><BackupRestore /></>}
          {section === "audit" && <AuditLog />}
          {section === "health" && <SystemHealth />}
          {section === "workspaces" && <Workspaces owner={role === "owner"} />}
          {section === "members" && <Team workspaceAdmin={role === "admin"} />}
          {section === "scoring" && <ScoringCard />}
          {section === "industry" && <IndustrySetup />}
          {section === "connections" && <Connections items={items} onReload={load} onSetupChanged={onSetupChanged} go={go} />}
        </div>
      </div>
    </div>
  );
}
