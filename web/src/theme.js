// Light / dark / follow-the-system. The choice is remembered in this browser only.
const KEY = "outreach-theme";
export const THEMES = ["system", "light", "dark"];

export function getTheme() {
  try { const v = localStorage.getItem(KEY); return THEMES.includes(v) ? v : "system"; } catch { return "system"; }
}

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme); else root.removeAttribute("data-theme");
}

export function setTheme(theme) {
  try { localStorage.setItem(KEY, theme); } catch { /* private window: the choice lasts until reload */ }
  applyTheme(theme);
}

applyTheme(getTheme());
