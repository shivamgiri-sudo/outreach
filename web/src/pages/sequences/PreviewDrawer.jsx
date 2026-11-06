import { useEffect, useState } from "react";
import { isOwner } from "../../api.js";

// The designed email for one step, as sent (sample person). `onSend(to)` sends it to a real inbox (owner only).
export default function PreviewDrawer({ shown, onClose, onSend }) {
  const [note, setNote] = useState("");
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  if (!shown) return null;
  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer" role="dialog" aria-modal="true" aria-label={`Designed email: ${shown.name}`} style={{ maxWidth: 720 }}>
        <div className="row" style={{ justifyContent: "space-between" }}><b>{shown.name}</b><button className="btn ghost" onClick={onClose}>Close</button></div>
        <p className="sub">Subject: <b>{shown.subject}</b> · sample person: Priya Sharma, Acme Retail</p>
        {isOwner() && onSend && (
          <form className="row" style={{ marginBottom: 8 }} onSubmit={(e) => {
            e.preventDefault();
            onSend(e.target.elements.to.value).then((r) => setNote(`Preview sent to ${r.to}.`)).catch((err) => setNote(err.message));
          }}>
            <input name="to" type="email" aria-label="Send this preview to" placeholder="Send this preview to (your email)" required style={{ flex: 1, minWidth: 0 }} />
            <button className="btn">Send preview</button>
          </form>
        )}
        {note && <p className="sub" role="status">{note}</p>}
        <iframe title="Designed email preview" srcDoc={shown.html} sandbox="" style={{ width: "100%", height: "70vh", border: "1px solid var(--line, #e2e8f0)", borderRadius: 10, background: "#fff" }} />
      </div>
    </>
  );
}
