// One list opened below the Lists table: bulk start email / LinkedIn, members with their journey, and the list journey log.
import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { stageLabel } from "../labels.js";
import { istLabel } from "../time.js";
import { useHashParam } from "../route.js";
import ConfirmDialog from "./ConfirmDialog.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import { Journey, result } from "./ListsParts.jsx";
import { Button, Card, Cell, DataTable } from "../ui/index.js";

const when = (iso) => (iso ? istLabel(iso) : "");

export default function ListDetail({ id, onChanged, onClose }) {
  const [d, setD] = useState(null);
  const [picked, setPicked] = useState([]);
  const [msg, setMsg] = useState("");
  const [lead, setLead] = useHashParam("lead");
  const [askEmail, setAskEmail] = useState(false);
  const load = () => api.list(id).then(setD).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, [id]);
  if (!d) return <Card className="mt-4">{msg || "Loading…"}</Card>;
  const only = picked.length ? picked : null;
  const run = async (fn, verb) => { setMsg(""); try { setMsg(result(verb, await fn(id, only))); setPicked([]); load(); onChanged(); } catch (e) { setMsg(e.message); } };
  const all = d.members.length > 0 && picked.length === d.members.length;
  return (
    <Card className="mt-4 list-detail">
      <div className="row between">
        <div>
          <h3 className="m0">{d.name}</h3>
          <p className="sub m0">Made by {d.created_by} · {when(d.created_at)} · {d.stats.members} leads in {d.stats.companies} companies · {d.stats.open_rate ?? 0}% open, {d.stats.reply_rate ?? 0}% reply</p>
        </div>
        <Button variant="secondary" onClick={onClose}>Close</Button>
      </div>
      {canWrite() && <div className="row my-3">
        <Button onClick={() => setAskEmail(true)}>Start email {picked.length ? `for ${picked.length} selected` : "for all"}</Button>
        <Button variant="secondary" onClick={() => run(api.listStartLinkedIn, "LinkedIn started")}>Start LinkedIn {picked.length ? `for ${picked.length} selected` : "for all"}</Button>
        {picked.length > 0 && <Button variant="secondary" onClick={async () => { await api.listMembers(id, { remove: picked }); setPicked([]); load(); onChanged(); }}>Remove selected from list</Button>}
      </div>}
      {askEmail && (
        <ConfirmDialog title="Start the email sequence?" confirmLabel={`Yes, start for ${picked.length || d.members.length}`} onCancel={() => setAskEmail(false)}
          onConfirm={() => { setAskEmail(false); run(api.listStartEmail, "Email started"); }}>
          <p className="m0 mb-3" data-testid="confirm-count">
            This is for <b>{picked.length || d.members.length}</b> lead(s) {picked.length ? <>you ticked in “{d.name}”.</> : <>— every lead in “{d.name}”, including any not on screen.</>}
          </p>
          <p className="m0">Leads with no email, already in the sequence, unsubscribed or on the do-not-contact list are skipped. Sending still follows your daily cap, business hours and practice mode.</p>
        </ConfirmDialog>
      )}
      {msg && <p className="sub" role="status">{msg}</p>}
      <div className="card table-card flat">
        <DataTable tableClass="list-members" maxHeight="min(60vh, 520px)" label="Leads in this list">
          <thead><tr>{canWrite() && <th className="c-pick"><input type="checkbox" aria-label="Select all" checked={all} onChange={() => setPicked(all ? [] : d.members.map((m) => m.id))} /></th>}<th className="c-lead">Lead</th><th className="c-contact">Contact</th><th>Journey</th></tr></thead>
          <tbody>
            {d.members.map((m) => (
              <tr key={m.id}>
                {canWrite() && <td className="c-pick"><input type="checkbox" aria-label={`Select ${m.company}`} checked={picked.includes(m.id)} onChange={() => setPicked((p) => (p.includes(m.id) ? p.filter((x) => x !== m.id) : [...p, m.id]))} /></td>}
                <td className="c-lead"><button className="linkish" title={m.company || m.contact_name} onClick={() => setLead(m.id)}><b>{m.company || m.contact_name}</b></button><small className="block">{m.contact_name} · {stageLabel(m.stage)}</small></td>
                <td className="c-contact"><Cell title={m.email || "no email"} sub={`${m.phone || ""}${m.linkedin_url ? `${m.phone ? " · " : ""}LinkedIn` : ""}`} /></td>
                <td><Journey j={m.journey} /></td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </div>
      <h4>List journey</h4>
      <div className="timeline">{[...d.log].reverse().map((e, i) => <div key={i}><small>{when(e.at)} · {e.by}</small>{e.text}</div>)}</div>
      {lead && <LeadDrawer id={lead} onClose={() => { setLead(null); load(); }} />}
    </Card>
  );
}
