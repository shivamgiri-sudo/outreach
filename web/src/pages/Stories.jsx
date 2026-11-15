import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { PageHeader, Card, CardHeader, Field, Input, Button, Badge, Switch, EmptyState, Skeleton } from "../ui/index.js";
import "../work-pages.css";

const EMPTY = { title: "", industry: "", result: "", quote: "", permission: false };

export default function Stories() {
  const [stories, setStories] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState("");
  const load = () => api.stories().then((r) => { setStories(r); setError(""); }).catch((e) => { setStories([]); setError(e.message); });
  useEffect(() => { load(); }, []);
  const set = (k, v) => setForm({ ...form, [k]: v });
  const add = async () => { try { await api.addStory(form); setForm(EMPTY); load(); } catch (e) { setError(e.message); } };

  return (
    <>
      <PageHeader title="Success stories"
        description="Only real results with the customer's permission. Stories without permission are never sent." />
      {error && <div className="tip error u-mb-12" role="alert">{error}</div>}
      <div className="grid g2">
        {canWrite() && <Card>
          <CardHeader title="Add a story" />
          <div className="stack">
            <Field label="Title"><Input value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="Retail chain filled 12 roles" /></Field>
            <Field label="Industry"><Input value={form.industry} onChange={(e) => set("industry", e.target.value)} /></Field>
            <Field label="Result (real numbers only)"><textarea rows={3} value={form.result} onChange={(e) => set("result", e.target.value)} /></Field>
            <Field label="Quote (optional)"><Input value={form.quote} onChange={(e) => set("quote", e.target.value)} /></Field>
            <div className="row">
              <Switch checked={form.permission} onChange={(v) => set("permission", v)} label="The customer allowed us to share this" />
              <span className="sub-inline">The customer allowed us to share this</span>
            </div>
            <div><Button disabled={!form.title.trim()} onClick={add}>Save story</Button></div>
          </div>
        </Card>}
        <Card>
          <CardHeader title="Library" />
          {stories === null && <div className="stack" aria-busy="true"><Skeleton height={56} /><Skeleton height={56} /></div>}
          {stories && stories.length === 0 && (
            <EmptyState icon="star" title="No stories yet">Add the first one after a pilot customer agrees to share their result.</EmptyState>
          )}
          <div className="stack">
            {(stories || []).map((s) => (
              <div key={s.id} className="story-item">
                <div className="row u-gap-8">
                  <b>{s.title}</b>{s.industry && <Badge>{s.industry}</Badge>}
                  <Badge tone={s.permission ? "success" : "warning"}>{s.permission ? "OK to share" : "No permission yet"}</Badge>
                </div>
                <p className="u-m-4-0">{s.result}</p>
                {s.quote && <p className="sub u-m0"><i>“{s.quote}”</i></p>}
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
