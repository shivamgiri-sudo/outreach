import { useEffect, useState } from "react";
import { api } from "../api.js";

const EMPTY = { title: "", industry: "", result: "", quote: "", permission: false };

export default function Stories() {
  const [stories, setStories] = useState([]);
  const [form, setForm] = useState(EMPTY);
  const load = () => api.stories().then(setStories);
  useEffect(() => { load(); }, []);
  const set = (k, v) => setForm({ ...form, [k]: v });

  const add = async () => { await api.addStory(form); setForm(EMPTY); load(); };

  return (
    <>
      <h2>Success stories</h2>
      <p className="sub">Only real results with the customer's permission. Stories without permission are never sent.</p>
      <div className="grid g2">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Add a story</h3>
          <label>Title</label><input value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="Retail chain filled 12 roles" />
          <label>Industry</label><input value={form.industry} onChange={(e) => set("industry", e.target.value)} />
          <label>Result (real numbers only)</label><textarea rows={3} value={form.result} onChange={(e) => set("result", e.target.value)} />
          <label>Quote (optional)</label><input value={form.quote} onChange={(e) => set("quote", e.target.value)} />
          <label className="row" style={{ fontWeight: 400 }}>
            <input type="checkbox" style={{ width: "auto" }} checked={form.permission} onChange={(e) => set("permission", e.target.checked)} />
            The customer allowed us to share this
          </label>
          <button className="btn" disabled={!form.title.trim()} onClick={add}>Save story</button>
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Library</h3>
          {stories.length === 0 && <p className="empty">No stories yet. Add the first one after a pilot customer agrees.</p>}
          {stories.map((s) => (
            <p key={s.id}>
              <b>{s.title}</b> {s.industry && <span className="pill">{s.industry}</span>}{" "}
              <span className={`pill ${s.permission ? "ok" : "warn"}`}>{s.permission ? "OK to share" : "No permission yet"}</span>
              <br />{s.result}{s.quote && <><br /><i>“{s.quote}”</i></>}
            </p>
          ))}
        </div>
      </div>
    </>
  );
}
