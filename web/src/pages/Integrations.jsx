import { useEffect, useState } from "react";
import { api, canAdminister, isOwner } from "../api.js";
import { EmptyArt, ErrorState } from "../states.jsx";
import { istLabel } from "../time.js";
import { CopyRow, StatusPill } from "./integrations/Fields.jsx";
import Wizard, { guideKeys } from "./integrations/Wizard.jsx";
import { GuideButton } from "../GuidePanel.jsx";
import LeadCapture from "./integrations/LeadCapture.jsx";
import CallTracker from "./integrations/CallTracker.jsx";
import Abbr from "../components/Abbr.jsx";

const EXTRA_TABS = [["calltracker", "Call Tracker"], ["capture", "Lead capture"], ["custom", "Custom"], ["webhooks", "Webhook addresses"]];
const OAUTH_ERRORS = {
  access_denied: "You did not allow access, so nothing was connected.",
  token_exchange_failed: "The provider did not accept the sign-in. Check the client ID, secret and redirect URI, then try again.",
  not_set_up: "Save the client ID and secret first.",
};

function hashQuery() {
  return new URLSearchParams(window.location.hash.split("?")[1] || "");
}

function ProviderCard({ channel, provider, onOpen, onActivate }) {
  const ready = ["configured", "error"].includes(provider.status);
  return (
    <div className="card prov-card" data-testid={`prov-${channel.id}-${provider.id}`}>
      <div className="row prov-head">
        <b>{provider.title}</b>
        <span className="row" style={{ gap: 6 }}>
          {provider.recommended && <span className="pill rec">Recommended</span>}
          {provider.active && <span className="pill ok">Active</span>}
        </span>
      </div>
      <p className="sub">{provider.why}</p>
      <div className="row">
        <StatusPill status={provider.status} oauth={provider.oauth} saved={provider.saved} />
        {provider.custom && <span className="pill">Any provider</span>}
      </div>
      {provider.cost_note && <small className="sub-inline">{provider.cost_note}</small>}
      {provider.last_test && (
        <small className={`result ${provider.last_test.ok ? "ok" : "bad"}`}>
          {provider.last_test.ok ? "✓" : "✗"} Last test {istLabel(provider.last_test.at)}
        </small>
      )}
      <div className="row prov-actions">
        <button className="btn" onClick={onOpen}>{!isOwner() ? "View" : provider.saved ? "Open" : "Set up"}</button>
        <GuideButton keys={guideKeys(channel.id, provider)} testId={`card-guide-${channel.id}-${provider.id}`} />
        {isOwner() && channel.kind === "exclusive" && !provider.active && ready && (
          <button className="btn ghost" onClick={onActivate}>Make active</button>
        )}
      </div>
    </div>
  );
}

function ChannelIntro({ channel, onLive, go }) {
  const [live, setLive] = useState(!!channel.live);       // flips at once; the server answer confirms it
  useEffect(() => setLive(!!channel.live), [channel.live]);
  return (
    <div className="channel-intro">
      <p className="sub">{channel.about}</p>
      {["live_whatsapp", "live_sms"].includes(channel.live_flag) && (
        <div className={`tip ${live ? "error" : ""}`}>
          {isOwner() ? <label className="row" style={{ margin: 0 }}>
            <input type="checkbox" checked={live} data-testid={`live-${channel.id}`}
              onChange={(e) => { setLive(e.target.checked); onLive(channel.live_flag, e.target.checked); }} />
            Send for real
          </label> : <b>Send for real: {live ? "on" : "off"}</b>}
          <p style={{ margin: "4px 0 0" }}>
            {live ? "On: messages go to real phones through the active provider. Consent and opt-out rules still apply."
              : "Off (practice mode): messages are prepared and shown, but not delivered."}
          </p>
        </div>
      )}
      {channel.id === "email_send" && (
        <p className="sub">Real email sending is switched on separately: <button className="linkish" onClick={() => go?.("outbox")}>Outbox → Send for real</button>.</p>
      )}
      {channel.id === "calendar" && (
        <p className="sub">Times are shown in <Abbr term="IST">IST</Abbr>. <button className="linkish" onClick={() => go?.("meetings")}>See upcoming meetings</button></p>
      )}
    </div>
  );
}

export default function Integrations({ go }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [query] = useState(hashQuery);
  const [tab, setTab] = useState(query.get("tab") || (query.get("connected") || query.get("oauth_error") ? "calendar" : !isOwner() && canAdminister() ? "calltracker" : "email_send"));
  const [open, setOpen] = useState(null);
  const [hooks, setHooks] = useState(null);
  const [notice, setNotice] = useState(() => {
    if (query.get("connected")) return { ok: true, text: `Connected to ${query.get("connected")}. Run the test on its card to confirm.` };
    if (query.get("oauth_error")) return { ok: false, text: OAUTH_ERRORS[query.get("oauth_error")] || `Sign-in failed (${query.get("oauth_error")}).` };
    return null;
  });

  const load = () => api.channels().then((d) => { setData(d); setError(""); return d; }).catch((e) => { setError(e.message); return null; });
  useEffect(() => {
    load();
    if (query.toString()) window.history.replaceState(null, "", "#integrations");
  }, []);
  useEffect(() => {
    if (tab === "webhooks" && isOwner()) api.channelWebhooks().then((d) => setHooks(d.webhooks)).catch((e) => setError(e.message));
  }, [tab, data]);

  if (!data) return error ? <ErrorState message="Could not load integrations" detail={error} onRetry={load} /> : <p className="empty">Loading…</p>;

  const hub = data.channels.filter((c) => c.kind !== "legacy");
  const current = data.channels.find((c) => c.id === tab);
  const connected = hub.filter((c) => c.status === "configured").length;
  const activate = (channel, provider) => api.setActiveChannel(channel.id, provider.id).then(load).catch((e) => setError(e.message));
  const setLive = (flag, on) => api.setLive({ [flag]: on }).then(load).catch((e) => setError(e.message));
  const openProvider = open && data.channels.find((c) => c.id === open[0]);
  const card = (channel, provider) => (
    <ProviderCard key={`${channel.id}-${provider.id}`} channel={channel} provider={provider}
      onOpen={() => setOpen([channel.id, provider.id])} onActivate={() => activate(channel, provider)} />
  );

  return (
    <>
      <div className="page-intro">
        <div><h2>Integrations</h2>
          <p className="sub">{connected} of {hub.length} channels connected. Every card has plain steps, a test button, and there is a custom option for any provider not listed.</p></div>
        <EmptyArt name="integrations" />
      </div>
      {notice && (
        <div className={`tip ${notice.ok ? "" : "error"}`} role="status" style={{ marginBottom: 12 }}>
          {notice.text} <button className="linkish" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}
      {error && <div className="tip error" role="alert" style={{ marginBottom: 12 }}>{error}</div>}
      <div className="tabs scroll" role="tablist" aria-label="Channels">
        {[...data.channels.map((c) => [c.id, c.title, c.status]), ...EXTRA_TABS.filter(([id]) => isOwner() || id === "custom" || (id === "calltracker" && canAdminister()))].map(([id, label, status]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => setTab(id)} data-testid={`tab-${id}`}>
            {label}{status === "configured" && <span className="tab-dot" aria-label="connected" />}
          </button>
        ))}
      </div>

      {current && (
        <>
          <ChannelIntro channel={current} onLive={setLive} go={go} />
          <div className="prov-grid">{current.providers.map((p) => card(current, p))}</div>
        </>
      )}

      {tab === "calltracker" && (canAdminister() ? <CallTracker /> : <p className="sub">Only the owner or a workspace admin can connect Call Tracker.</p>)}

      {tab === "capture" && (isOwner() ? <LeadCapture go={go} /> : <p className="sub">Only the owner can connect lead sources.</p>)}

      {tab === "custom" && (
        <>
          <p className="sub">Use these when your provider is not in the list: describe its <Abbr term="API">API</Abbr> once (address, headers, body with placeholders) and test it here.</p>
          {hub.map((c) => {
            const customs = c.providers.filter((p) => p.custom);
            return customs.length ? (
              <div key={c.id}>
                <h3>{c.title}</h3>
                <div className="prov-grid">{customs.map((p) => card(c, p))}</div>
              </div>
            ) : null;
          })}
        </>
      )}

      {tab === "webhooks" && isOwner() && (
        <div className="card">
          <b>Addresses to paste into provider dashboards</b>
          <p className="sub" style={{ margin: "2px 0 12px" }}>
            One per saved provider that sends things back (replies, bounces, bookings). Each contains a secret code: treat it like a password.
          </p>
          {hooks === null && <p className="sub">Loading…</p>}
          {hooks?.length === 0 && <p className="sub">Nothing yet. Save a reply-capture, bounce, WhatsApp, SMS or booking provider first.</p>}
          {hooks?.map((h) => (
            <div key={`${h.channel}-${h.provider}`} className="hook-item">
              <b>{h.channel_title}: {h.title}</b> <small className="sub-inline">→ {h.where}</small>
              <CopyRow value={h.url} />
              {["meta", "calcom", "calendly"].includes(h.auth) && <CopyRow label={h.auth === "meta" ? "Verify token" : "Secret"} value={h.secret} />}
            </div>
          ))}
        </div>
      )}

      {open && openProvider && (
        <Wizard key={open.join("-")} channel={openProvider} provider={openProvider.providers.find((p) => p.id === open[1])} meta={data}
          onClose={() => setOpen(null)} onChanged={load} />
      )}
    </>
  );
}
