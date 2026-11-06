// The owner's brand (names, logo, colours) and their words for stages and lead numbers, loaded once and shared.
// Before sign-in only the public part (/api/brand/public) is read; after sign-in the full brand replaces it.
import { useEffect, useState } from "react";
import { api } from "./api.js";
import { setLabelOverrides } from "./labels.js";

export const DEFAULT_PUBLIC = {
  app_name: "MCN Outreach", company_name: "Mas Callnet", product_name: "BookMyInterview.ai",
  tagline: "", primary_color: "#0369a1", accent_color: "#e8552b", header_logo: "/mcn-logo.png",
  email_logo: "/bmi-logo.png", logo_url: "", custom_logo: false, is_default: true,
};

// The last brand seen is remembered in this browser so the sign-in page doesn't flash the default first.
const CACHE = "outreach-brand";
const cached = () => { try { return JSON.parse(localStorage.getItem(CACHE) || "null"); } catch { return null; } };
let state = { public: { ...DEFAULT_PUBLIC, ...(cached() || {}) }, full: null };
const listeners = new Set();
const emit = () => listeners.forEach((fn) => fn((n) => n + 1));

function applyDocument(pub) {
  try {
    document.title = pub.app_name || DEFAULT_PUBLIC.app_name;
    if (pub.custom_logo && pub.logo_url) {
      let link = document.querySelector("link[rel='icon']");
      if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
      link.href = pub.logo_url;
    }
  } catch { /* no document (tests) */ }
}

export function setBrand(data) {
  if (data && data.public) {
    state = { public: { ...DEFAULT_PUBLIC, ...data.public }, full: data };
    setLabelOverrides(data.stage_labels || {}, data.lead_terms || {});
  } else if (data) {
    state = { ...state, public: { ...DEFAULT_PUBLIC, ...data } };
  }
  applyDocument(state.public);
  try { localStorage.setItem(CACHE, JSON.stringify(state.public)); } catch { /* storage blocked */ }
  emit();
}

export const loadPublicBrand = () => api.brandPublic().then(setBrand).catch(() => {});
export const loadBrand = () => api.brand().then(setBrand).catch(() => {});
export const getBrand = () => state;

export function useBrand() {
  const [, bump] = useState(0);
  useEffect(() => { listeners.add(bump); return () => listeners.delete(bump); }, []);
  return state;
}

// WCAG contrast ratio of two #rrggbb colours (the server enforces at least 3:1 for white text on the main colour).
export function contrast(a, b = "#ffffff") {
  const lum = (hex) => {
    let h = String(hex || "").replace("#", "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    if (!/^[0-9a-f]{6}$/i.test(h)) return null;
    const ch = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const la = lum(a); const lb = lum(b);
  if (la === null || lb === null) return null;
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
}
