import { useEffect, useState } from "react";
import { api } from "../api.js";
import { copyText } from "../clipboard.js";
import { useConfirm, usePrompt } from "../Dialog.jsx";
import { istLabel } from "../time.js";

// The office computer that runs the LinkedIn browser (LinkedIn blocks the portal's cloud server). The owner connects it
// with a one-line command that holds a 15-minute code; afterwards everything is done from this page. Never shows tokens.
const SYSTEMS = [["windows", "Windows"], ["mac", "Mac"], ["linux", "Linux"]];
const HOW = {
  windows: "Open the Start menu, type PowerShell, open Windows PowerShell, paste the line and press Enter.",
  mac: "Open Terminal (Applications, Utilities, Terminal), paste the line and press Enter.",
  linux: "Open a terminal, paste the line and press Enter.",
};

function left(expires) {
  const s = Math.max(0, Math.round((new Date(expires).getTime() - Date.now()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function CopyLine({ label, text, testid }) {
  const [done, setDone] = useState(false);
  return (
    <div className="copy-block">
      {label && <span className="copy-label">{label}</span>}
      <div className="copy-row" style={{ flexWrap: "wrap" }}>
        <code data-testid={testid} style={{ wordBreak: "break-all" }}>{text}</code>
        <button type="button" className="btn ghost" onClick={async () => { setDone(await copyText(text)); setTimeout(() => setDone(false), 2500); }}>
          {done ? "Copied" : "Copy"}</button>
      </div>
    </div>
  );
}

function Steps({ pairing, online, onCancel }) {
  const guess = /Mac/i.test(navigator.platform) ? "mac" : /Linux/i.test(navigator.platform) ? "linux" : "windows";
  const [system, setSystem] = useState(guess);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(t); }, []);
  const expired = new Date(pairing.expires_at).getTime() <= Date.now();
  return (
    <div className="inline-form" data-testid="runner-steps">
      <b>Step 1. On the office computer, paste this one line</b>
      <div className="tab-chips" role="tablist" aria-label="Office computer system">
        {SYSTEMS.map(([key, name]) => <button key={key} type="button" role="tab" aria-selected={system === key}
          className={`tab-chip${system === key ? " on" : ""}`} onClick={() => setSystem(key)}>{name}</button>)}
      </div>
      <p className="sub" style={{ margin: 0 }}>{HOW[system]} It installs everything into that user's own folder, connects the computer to this portal
        and starts it now and whenever that user logs in.</p>
      {expired ? <p className="tip error" role="alert">This line has expired. Press Make a new line.</p>
        : <CopyLine text={pairing.commands[system]} testid="runner-command" />}
      {!expired && <small className="sub-inline">Works once, for {left(pairing.expires_at)} more minutes. It holds a short code, not a password.</small>}
      {pairing.insecure && <p className="tip error" role="note">The portal address is not https://. Set the public https address in Settings before connecting a computer over the internet.</p>}
      <b>Step 2. Wait here for "Office computer online"</b>
      <p className="sub" style={{ margin: 0 }} data-testid="runner-wait">{online ? "The office computer is online." : "Waiting for the office computer… (this page checks by itself)"}</p>
      <b>Step 3. Add the LinkedIn account below and log it in from this page</b>
      <p className="sub" style={{ margin: 0 }}>The LinkedIn window opens on the office computer by itself. Nobody needs to touch it.</p>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <button type="button" className="btn ghost" onClick={onCancel}>{expired ? "Close" : "Cancel"}</button>
      </div>
    </div>
  );
}

export default function OfficeRunner({ st, onDone, onMessage }) {
  const confirm = useConfirm();
  const ask = usePrompt();
  const [pairing, setPairing] = useState(null);
  const r = st.runner;
  useEffect(() => { if (r?.online && pairing && !st.pairing) setPairing(null); }, [r?.online, st.pairing]);
  const connect = async () => {
    if (r && !(await confirm({ title: "Connect a different computer?", confirmLabel: "Yes, replace it",
      message: `${r.label} is connected now. When the new computer connects, ${r.label} is disconnected and its LinkedIn browsers stop. Only one office computer can run the LinkedIn accounts.` }))) return;
    try {
      const res = await api.pairLinkedinRunner(!!r);
      const { code, commands, expires_at, insecure, uninstall, portal, ...rest } = res;
      setPairing({ commands, expires_at, insecure, uninstall, portal });
      onDone(rest);
    } catch (e) { onMessage(e.message); }
  };
  const cancel = async () => { setPairing(null); try { onDone(await api.cancelLinkedinRunnerPair()); } catch (e) { onMessage(e.message); } };
  const rename = async () => {
    const label = await ask("Name for the office computer", r.label);
    if (label === null) return;
    try { onDone(await api.renameLinkedinRunner(r.id, label)); } catch (e) { onMessage(e.message); }
  };
  const revoke = async () => {
    if (!(await confirm({ title: "Disconnect the office computer?", danger: true, confirmLabel: "Disconnect",
      message: `${r.label} stops all LinkedIn work at once and forgets its connection. The LinkedIn accounts stay here; connect a computer again to continue.` }))) return;
    try { onDone(await api.revokeLinkedinRunner(r.id)); onMessage("Office computer disconnected."); } catch (e) { onMessage(e.message); }
  };
  return (
    <div className="card" style={{ marginBottom: 12, background: "var(--bg)" }} data-testid="office-runner">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <b>Office computer</b>
        {r ? <span className={`pill ${r.online ? "ok" : "err"}`} data-testid="runner-status">{r.online ? "Office computer online" : "Office computer offline"}</span>
          : <span className="pill" data-testid="runner-status">Not connected</span>}
      </div>
      {r ? (
        <p className="sub" style={{ marginBottom: 4 }} data-testid="runner-details">
          <b>{r.label}</b>{r.hostname && r.hostname !== r.label ? ` (${r.hostname})` : ""}{r.os ? ` · ${r.os}` : ""}
          {" · "}{r.last_seen ? `last heard from ${istLabel(r.last_seen)}` : "not heard from yet"}</p>
      ) : (
        <p className="sub">LinkedIn blocks logins from our cloud server, so the LinkedIn browser runs on one office computer that stays on during working hours.
          You still do everything from this page; that computer only shows a LinkedIn window that works by itself.</p>
      )}
      {r && !r.online && <p className="tip" role="note">Check that the office computer is switched on, logged in to Windows (or macOS / Linux) and connected to the internet.
        It reconnects by itself.</p>}
      <p className="sub" style={{ marginTop: 4 }}>Keep the office computer switched on and connected during working hours, and do not close the LinkedIn window on it
        (if it is closed, it opens again within a minute).</p>
      {pairing ? <Steps pairing={pairing} online={!!r?.online && !st.pairing} onCancel={cancel} /> : (
        <div className="row" style={{ flexWrap: "wrap" }}>
          <button className={r ? "btn ghost" : "btn"} onClick={connect} data-testid="runner-connect">{r ? "Connect a different computer" : "Connect an office computer"}</button>
          {r && <button className="btn ghost" onClick={rename}>Rename</button>}
          {r && <button className="btn ghost danger" onClick={revoke} data-testid="runner-revoke">Disconnect</button>}
        </div>
      )}
      <details style={{ marginTop: 8 }}>
        <summary className="sub">How to remove it from the office computer</summary>
        <p className="sub">Press Disconnect above, then on the office computer paste the line for its system:</p>
        <CopyLine label="Windows (PowerShell)" text={'powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\\MCN-LinkedIn-Runner\\uninstall.ps1"'} />
        <CopyLine label="Mac (Terminal)" text={'bash "$HOME/Library/Application Support/MCN-LinkedIn-Runner/uninstall.sh"'} />
        <CopyLine label="Linux (terminal)" text={'bash "${XDG_DATA_HOME:-$HOME/.local/share}/mcn-linkedin-runner/uninstall.sh"'} />
      </details>
    </div>
  );
}
