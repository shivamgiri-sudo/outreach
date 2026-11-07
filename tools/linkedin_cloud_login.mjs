// Cloud mode login for tools/linkedin_agent.mjs (--cloud). The agent runs headless on the server, so nobody can type into
// its window. When the browser profile is not logged in, it tells the portal "needs_login" and waits for a one-time login
// job (email + password typed by the owner in the portal, kept in the portal's memory only and deleted when read here).
// It types them into LinkedIn's login form and then follows what LinkedIn shows:
//   verification code -> "needs_code", waits for the code the owner types in the portal, submits it;
//   "check your LinkedIn app" -> "needs_app_approval", waits up to 3 minutes for the owner to approve on the phone;
//   CAPTCHA / puzzle -> "needs_manual". It never tries to solve one. Headless (server) it gives up on this attempt; with a
//   visible window (office runner) it waits for a person to solve it in that window and then carries on.
// With a visible window a person can also just log in by hand there at any time: the agent notices and carries on.
// The password is never logged, never stored and never kept after it was typed.

const CODE_INPUT = 'input[name="pin"], #input__email_verification_pin, input[autocomplete="one-time-code"], #input__phone_verification_pin';
const EMAIL_INPUT = 'input[type="email"], input[name="session_key"], input#username';
const PASSWORD_INPUT = 'input[type="password"]';
const CAPTCHA_FRAME = 'iframe[src*="captcha" i], iframe[title*="captcha" i], iframe[src*="arkose" i], #captcha-internal, iframe[title*="puzzle" i]';
const APP_TEXT = /check (your|the) linkedin app|open (the|your) linkedin app|approve (this|the|your) sign.?in|tap yes|we sent a notification to your|approve from your/i;
const CAPTCHA_TEXT = /quick security check|security verification|solve (this|the) puzzle|i'?m not a robot|verify (that )?you('| a)re (a )?human/i;
const BAD_TEXT = /wrong email or password|couldn.?t find a linkedin account|that.?s not the right password|password you provided must|please enter a valid|hmm, we don.?t recognize|incorrect (email|password)/i;
const BAD_CODE_TEXT = /(code|pin) (you entered )?(is )?(not valid|invalid|incorrect|expired)|wrong (code|pin)|that code didn.?t work|please enter a valid code/i;

// The first VISIBLE element for a selector list (LinkedIn keeps hidden copies of inputs; their ids change every load).
async function visible(page, selector) {
  const all = page.locator(selector);
  for (let i = 0, n = await all.count().catch(() => 0); i < n; i++) {
    const el = all.nth(i);
    if (await el.isVisible().catch(() => false)) return el;
  }
  return null;
}

async function waitVisible(page, selector, sleep, ms = 15000) {
  for (let waited = 0; waited <= ms; waited += 500) {
    const el = await visible(page, selector);
    if (el) return el;
    await sleep(500);
  }
  throw new Error(`no visible ${selector.split(",")[0]} on the page`);
}

export function loggedInUrl(url) {
  try { return !/\/(login|authwall|signup|checkpoint|uas)(\/|$|-)/.test(new URL(url).pathname); }
  catch { return false; }
}

async function bodyText(page) {
  return (await page.locator("body").innerText({ timeout: 3000 }).catch(() => "")).slice(0, 6000);
}

// What the page shows now: ok | code | app | captcha | bad | bad_code | wait
async function classify(page) {
  if (loggedInUrl(page.url())) return "ok";
  if (await visible(page, CODE_INPUT)) {
    const text = await bodyText(page);
    return BAD_CODE_TEXT.test(text) ? "bad_code" : "code";
  }
  const text = await bodyText(page);
  if (APP_TEXT.test(text)) return "app";
  if (CAPTCHA_TEXT.test(text) || await page.locator(CAPTCHA_FRAME).count().catch(() => 0)) return "captcha";
  if (BAD_TEXT.test(text)) return "bad";
  return "wait";
}

async function settleOn(page, sleep, seconds = 45, accept = (k) => k !== "wait") {
  let kind = "wait";
  for (let i = 0; i < seconds; i++) {
    await sleep(1000);
    kind = await classify(page);
    if (accept(kind)) return kind;
  }
  return kind;
}

// personAtWindow: the browser window is visible on a computer where a person can use it (the office runner). Then a person
// may finish what the agent must not do itself: solve a CAPTCHA in that window, or simply log in there by hand. The agent
// only WATCHES the page for that (it reads its own window's address and text: no clicks, no LinkedIn requests) and carries
// on by itself once the account is in, so nobody has to press Restart afterwards. Headless (the server) it cannot, so a
// CAPTCHA still ends the attempt there.
export async function cloudLogin({ page, api, base, log, sleep, settle = 1500, pollSeconds = 3, personAtWindow = false, personWaitSeconds = 900 }) {
  const report = (state, note = "") => api("/api/extension/cloud/state", "POST", { state, note }).catch((e) => log("could not report state:", e.message));
  const poll = pollSeconds * 1000;
  const inByHand = () => personAtWindow && page.url().startsWith(base) && loggedInUrl(page.url());
  const show = () => page.bringToFront().catch(() => {});
  let note = personAtWindow
    ? "Not logged in to LinkedIn yet. Enter the account's LinkedIn email and password here, or log in yourself in the LinkedIn window on the office computer."
    : "Not logged in to LinkedIn yet. Enter the account's LinkedIn email and password here.", waitState = "needs_login";
  for (;;) {
    await report(waitState, note);
    waitState = "needs_login";
    log(personAtWindow ? "Not logged in to LinkedIn. Waiting for login details from the portal, or for a login by hand in this window…"
      : "Not logged in to LinkedIn. Waiting for login details from the portal…");
    let job = null;
    for (;;) {                                   // waits as long as it takes; no LinkedIn traffic meanwhile
      if (inByHand()) { log("Logged in by hand in the LinkedIn window."); return; }
      try { job = (await api("/api/extension/cloud/login-job")).job; }
      catch (e) { if (e.status === 401 || e.status === 403) throw e; log("could not ask for a login job:", e.message); }
      if (job && job.email && job.password) break;
      await sleep(poll);
    }
    await report("logging_in", "typing the login details into LinkedIn");
    log("Login details received from the portal; logging in…");
    let outcome;
    try {
      await page.goto(base + "/login", { waitUntil: "domcontentloaded" });
      await sleep(settle);
      if (loggedInUrl(page.url())) { job = null; return; }          // the session was still good after all
      const email = await waitVisible(page, EMAIL_INPUT, sleep);
      const password = await waitVisible(page, PASSWORD_INPUT, sleep);
      await email.fill(job.email);
      await password.fill(job.password);
      job = null;                                // nothing keeps the password after it was typed
      await password.press("Enter");
      outcome = await settleOn(page, sleep);
    } catch (e) {
      job = null;
      log("the login page did not work as expected:", String(e.message).split("\n")[0].slice(0, 160), "at", page.url().split("?")[0]);
      waitState = "needs_login";
      note = `The LinkedIn login page did not work as expected (${String(e.message).split("\n")[0].slice(0, 120)}). Try again.`;
      continue;
    }
    // A CAPTCHA with a person at the window: bring the window forward and wait for the person to solve it. The agent never
    // touches the puzzle; it only watches until the page moves on (logged in, a code, the app, or a wrong password).
    const personSolves = async () => {
      await report("needs_manual", "LinkedIn shows a puzzle. Solve it in the LinkedIn window on the office computer; the agent carries on "
        + `by itself as soon as the account is in (it waits up to ${Math.round(personWaitSeconds / 60)} minutes).`);
      log("LinkedIn shows a CAPTCHA. Waiting for a person to solve it in this window (the agent does not solve it)…");
      await show();
      const kind = await settleOn(page, sleep, personWaitSeconds, (k) => k !== "wait" && k !== "captcha");
      return kind === "captcha" || kind === "wait" ? "timeout_manual" : kind;
    };
    if (outcome === "captcha" && personAtWindow) outcome = await personSolves();
    if (outcome === "code" || outcome === "bad_code") {
      let tries = 0;
      while ((outcome === "code" || outcome === "bad_code") && tries < 3) {
        await report("needs_code", tries ? "LinkedIn did not accept that code. Type the newest code LinkedIn sent." : "LinkedIn sent a verification code (email or SMS). Type it here.");
        log("LinkedIn asks for a verification code; waiting for it from the portal…");
        let code = null;
        for (let waited = 0; waited < 600000 && !code; waited += poll) {
          try { code = (await api("/api/extension/cloud/code")).code; } catch (e) { if (e.status === 401 || e.status === 403) throw e; }
          if (!code) await sleep(poll);
        }
        if (!code) { outcome = "timeout_code"; break; }
        tries += 1;
        await report("logging_in", "submitting the code");
        const box = await waitVisible(page, CODE_INPUT, sleep, 5000).catch(() => null);
        if (!box) { outcome = await classify(page); break; }
        await box.fill(code);
        await box.press("Enter");
        outcome = await settleOn(page, sleep, 45, (k) => k !== "wait" && k !== "code");
        if (outcome === "wait") outcome = await classify(page);
      }
    }
    if (outcome === "app") {
      await report("needs_app_approval", "LinkedIn wants you to approve this sign-in in the LinkedIn app on your phone. Waiting up to 3 minutes.");
      log("LinkedIn asks to approve the sign-in in its app; waiting up to 3 minutes…");
      outcome = await settleOn(page, sleep, 180, (k) => k === "ok" || k === "captcha" || k === "code");
      if (outcome === "app" || outcome === "wait") outcome = "timeout_app";
    }
    if (outcome === "captcha" && personAtWindow) outcome = await personSolves();     // a puzzle after the app approval
    if (outcome === "ok") { log("Logged in to LinkedIn."); return; }
    note = {
      captcha: "LinkedIn showed a CAPTCHA / puzzle. The agent does not solve those. Wait a few hours and try again, or log in to this account from your own computer first.",
      timeout_manual: "The puzzle was not solved within the waiting time. Solve it in the LinkedIn window on the office computer (the agent "
        + "notices when you are in), or press Log in again.",
      bad: "LinkedIn did not accept the email or password. Check them and try again.",
      bad_code: "LinkedIn did not accept the code three times. Log in again to get a new code.",
      timeout_code: "No code arrived within 10 minutes. Log in again to get a new code.",
      timeout_app: "The sign-in was not approved in the LinkedIn app within 3 minutes. Log in again.",
      code: "LinkedIn kept asking for a code. Log in again.",
    }[outcome] || "LinkedIn did not finish the login (it showed a page the agent does not know). Try again later.";
    log("Login did not finish:", outcome);
    if (outcome === "captcha" || outcome === "timeout_manual") waitState = "needs_manual";   // stays visible in the portal until someone acts
  }
}
