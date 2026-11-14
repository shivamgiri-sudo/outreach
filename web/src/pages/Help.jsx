// Help centre: the user guide (role-aware chapters), the glossary and the setup guides.
//   #help                      guide contents              #help/<chapter>[/<heading>]   one chapter
//   #help/glossary[/<term>]    the glossary                #help/setup-guides  or  #help?guide=<id>   "How do I get this?" guides
import { useEffect, useState } from "react";
import { getRole, isClient } from "../api.js";
import GlossaryPage from "../guide/GlossaryPage.jsx";
import GuideBook from "../guide/GuideBook.jsx";
import HelpSetupGuides from "./HelpSetupGuides.jsx";
import "../guide/guide.css";
import { PageHeader } from "../ui/index.js";

function parse() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const at = raw.indexOf("?");
  const path = (at < 0 ? raw : raw.slice(0, at)).split("/").slice(1).map(decodeURIComponent);
  const params = new URLSearchParams(at < 0 ? "" : raw.slice(at + 1));
  return { a: path[0] || "", b: path[1] || "", guideParam: params.get("guide") };
}

const TABS = [["guide", "User guide", "#help"], ["glossary", "Glossary", "#help/glossary"], ["setup", "Setup guides", "#help/setup-guides"]];

export default function Help({ role }) {
  const [loc, setLoc] = useState(parse);
  useEffect(() => {
    const on = () => setLoc(parse());
    window.addEventListener("hashchange", on);
    window.addEventListener("popstate", on);
    return () => { window.removeEventListener("hashchange", on); window.removeEventListener("popstate", on); };
  }, []);
  const tab = loc.guideParam || loc.a === "setup-guides" ? "setup" : loc.a === "glossary" ? "glossary" : "guide";
  const ctx = { role: role || getRole(), client: isClient() };
  return (
    <div className="help-centre">
      <PageHeader title="Help centre" description="How to use the portal, what the short words mean, and step-by-step guides for connecting other services. Written for people with no technical background." />
      <nav className="g-tabs no-print" aria-label="Help sections">
        {TABS.map(([id, label, href]) => <a key={id} href={href} className={tab === id ? "on" : ""} aria-current={tab === id ? "page" : undefined} data-testid={`help-tab-${id}`}>{label}</a>)}
      </nav>
      {tab === "guide" && <GuideBook chapterId={loc.a} topic={loc.b} ctx={ctx} />}
      {tab === "glossary" && <GlossaryPage focusId={loc.b} />}
      {tab === "setup" && <HelpSetupGuides />}
    </div>
  );
}
