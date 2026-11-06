// In-app replacements for window.confirm / prompt / alert: accessible, themed, and promise based.
//   const confirm = useConfirm();  if (!(await confirm("Delete this list?"))) return;
//   const ask = usePrompt();       const name = await ask("Name this list", "Default");   // null when cancelled
//   const say = useAlert();        await say("Sent.");
// Options objects also work: confirm({ message, title, confirmLabel, cancelLabel, danger }).
// Without <DialogProvider> (for example in a test render) the hooks fall back to the browser's own boxes.
import { createContext, useCallback, useContext, useId, useRef, useState } from "react";
import { useModalFocus } from "./DrawerShell.jsx";

const Ctx = createContext(null);

function Box({ req, onDone }) {
  const panel = useRef(null);
  const [value, setValue] = useState(req.defaultValue ?? "");
  const id = useId();
  const cancel = () => onDone(req.kind === "confirm" ? false : req.kind === "prompt" ? null : undefined);
  const ok = () => onDone(req.kind === "confirm" ? true : req.kind === "prompt" ? value : undefined);
  const first = req.kind === "prompt" ? "input" : req.danger ? "[data-cancel]" : "[data-ok]";
  useModalFocus(panel, cancel, true, first);
  const title = req.title || (req.kind === "confirm" ? "Please confirm" : req.kind === "prompt" ? "One more detail" : "Note");
  return (
    <>
      <div className="scrim dialog-scrim" aria-hidden="true" onClick={cancel} />
      <div ref={panel} className="dialog" role={req.kind === "prompt" ? "dialog" : "alertdialog"} aria-modal="true"
        aria-labelledby={`${id}-t`} aria-describedby={`${id}-m`} data-testid="app-dialog">
        <form onSubmit={(e) => { e.preventDefault(); ok(); }}>
          <h2 id={`${id}-t`} className="dialog-title">{title}</h2>
          {req.kind === "prompt" ? (
            <label id={`${id}-m`} className="dialog-msg" htmlFor={`${id}-i`}>{req.message}</label>
          ) : <p id={`${id}-m`} className="dialog-msg">{req.message}</p>}
          {req.kind === "prompt" && (
            <input id={`${id}-i`} type={req.inputType || "text"} value={value} autoComplete="off"
              onChange={(e) => setValue(e.target.value)} data-testid="dialog-input" />
          )}
          <div className="dialog-actions">
            {req.kind !== "alert" && <button type="button" className="btn ghost" data-cancel onClick={cancel}>{req.cancelLabel || "Cancel"}</button>}
            <button type="submit" className={`btn${req.danger ? " danger-solid" : ""}`} data-ok data-testid="dialog-ok">{req.confirmLabel || "OK"}</button>
          </div>
        </form>
      </div>
    </>
  );
}

export function DialogProvider({ children }) {
  const [queue, setQueue] = useState([]);
  const ask = useCallback((req) => new Promise((resolve) => setQueue((q) => [...q, { ...req, resolve, key: Math.random() }])), []);
  const done = (value) => { queue[0]?.resolve(value); setQueue((q) => q.slice(1)); };
  return (
    <Ctx.Provider value={ask}>
      {children}
      {queue[0] && <Box key={queue[0].key} req={queue[0]} onDone={done} />}
    </Ctx.Provider>
  );
}

const norm = (kind, opts, defaultValue) => ({ kind, ...(typeof opts === "string" ? { message: opts } : opts), ...(defaultValue !== undefined ? { defaultValue } : {}) });

export function useConfirm() {
  const ask = useContext(Ctx);
  return useCallback((opts) => (ask ? ask(norm("confirm", opts)) : Promise.resolve(window.confirm(norm("confirm", opts).message))), [ask]);
}

export function usePrompt() {
  const ask = useContext(Ctx);
  return useCallback((opts, defaultValue) => {
    const req = norm("prompt", opts, defaultValue);
    return ask ? ask(req) : Promise.resolve(window.prompt(req.message, req.defaultValue ?? ""));
  }, [ask]);
}

export function useAlert() {
  const ask = useContext(Ctx);
  return useCallback((opts) => (ask ? ask(norm("alert", opts)) : Promise.resolve(window.alert(norm("alert", opts).message))), [ask]);
}
