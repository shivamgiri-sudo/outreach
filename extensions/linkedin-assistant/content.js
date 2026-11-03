/*
 * MCN LinkedIn Assistant - content script (runs on linkedin.com pages).
 *
 * HARD RULE: LinkedIn forbids automation. This extension is an ASSISTANT; a human does every action on LinkedIn.
 * This script therefore MUST NOT, and does not:
 *   - click any LinkedIn button, press Send/Connect, or submit any form
 *   - scroll, paginate or scrape search results, lists or feeds; crawl or open other profiles
 *   - simulate typing or mouse movement, or use human-like / randomised timing; it has no timers at all
 *   - hide itself or rotate anything
 * It ONLY:
 *   1. reads the ONE profile page the person has opened (name, headline, company, degree, profile URL) when asked
 *   2. fills text into the open note / message box ONCE, only when the person pressed "Insert message" in the
 *      extension's own side panel (never over text the person already typed, unless they confirm)
 *   3. after an insert, watches (read-only) whether that box closes, so the panel can ASK the person to confirm
 *      "Mark as sent". Nothing is ever reported to the server from here.
 * tests/test_extension_rules.py checks this folder for forbidden calls.
 */
"use strict";

(function () {
  const BOX_SELECTORS = [
    "textarea#custom-message",                                  // connection request: "Add a note"
    "textarea[name='message']",
    "div.msg-form__contenteditable[contenteditable='true']",    // messaging box
    "div.msg-form__msg-content-container [contenteditable='true']",
    "div[role='textbox'][contenteditable='true'][aria-label*='essage']",
  ];

  function onProfilePath() { return /^\/in\/[^/]+/.test(location.pathname); }
  function onMessagingPath() { return /^\/messaging\//.test(location.pathname); }
  function text(el) { return el ? (el.textContent || "").replace(/\s+/g, " ").trim() : ""; }

  function nameFromTitle() {
    // "(3) Ravi Kumar | LinkedIn" or "Ravi Kumar - Head of TA - Acme | LinkedIn"
    return document.title.replace(/^\(\d+\)\s*/, "").split("|")[0].split(" - ")[0].trim();
  }

  function readProfile() {
    if (!onProfilePath()) return { onProfile: false, onMessaging: onMessagingPath(), url: location.origin + location.pathname };
    const top = document.querySelector("main section") || document;
    const h1 = document.querySelector("main h1") || document.querySelector("h1");
    const name = text(h1) || nameFromTitle();
    const headline = text(top.querySelector(".text-body-medium.break-words")) || text(top.querySelector(".text-body-medium"));
    // current company: the top-card "Current company" button, else "... at Company" in the headline
    let company = text(document.querySelector("button[aria-label^='Current company']")).replace(/^Current company:\s*/i, "");
    if (!company) {
      const m = headline.match(/\bat\s+(.+?)(?:\s*[|·•].*)?$/i) || headline.match(/@\s*(.+)$/);
      company = m ? m[1].trim() : "";
    }
    const degreeText = text(document.querySelector(".distance-badge .dist-value")) || text(document.querySelector("span.dist-value"));
    const degree = (degreeText.match(/[123](st|nd|rd)/) || [""])[0];
    const pending = !!Array.from(document.querySelectorAll("main button, main a")).find((b) => /^pending\b/i.test(text(b)) || /pending/i.test(b.getAttribute("aria-label") || ""));
    return { onProfile: true, onMessaging: false, url: location.origin + location.pathname, name, headline, company, degree, pending };
  }

  function findBox() {
    for (const sel of BOX_SELECTORS) {
      const el = document.querySelector(sel);
      if (el && el.isConnected) return el;
    }
    return null;
  }

  function boxText(el) { return "value" in el && el.tagName === "TEXTAREA" ? el.value : (el.innerText || ""); }

  let watching = null;

  // Read-only: tell the side panel when the box the person is using disappears (they probably pressed Send).
  function watchBox(box) {
    if (watching) watching.disconnect();
    watching = new MutationObserver(() => {
      if (!box.isConnected) {
        watching.disconnect();
        watching = null;
        chrome.runtime.sendMessage({ type: "box-closed" }).catch(() => {});
      }
    });
    watching.observe(document.body, { childList: true, subtree: true });
  }

  function insertOnce(value, replace) {
    const box = findBox();
    if (!box) return { ok: false, reason: "no_box" };
    if (boxText(box).trim() && !replace) return { ok: false, reason: "not_empty" };
    box.focus();
    if (box.tagName === "TEXTAREA") {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(box, value);
    } else {
      box.textContent = "";
      const lines = value.split("\n");
      lines.forEach((line, i) => {
        const p = document.createElement("p");
        if (line) p.textContent = line; else p.appendChild(document.createElement("br"));
        box.appendChild(p);
      });
    }
    box.dispatchEvent(new Event("input", { bubbles: true }));    // one plain input event so LinkedIn notices the text; nothing is submitted
    watchBox(box);
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || !msg) return false;
    if (msg.type === "read-profile") { sendResponse(readProfile()); return false; }
    if (msg.type === "has-box") { sendResponse({ box: !!findBox() }); return false; }
    if (msg.type === "insert" && typeof msg.text === "string" && msg.text.length <= 3000) {
      sendResponse(insertOnce(msg.text, !!msg.replace));
      return false;
    }
    return false;
  });
})();
