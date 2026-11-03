// Times are always shown in India Standard Time, whatever the viewer's computer is set to.
const IST_OFFSET_MINUTES = 330;

export function istLabel(iso, withDay = true) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const opts = withDay
    ? { timeZone: "Asia/Kolkata", weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }
    : { timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit" };
  return `${d.toLocaleString("en-IN", opts)} IST`;
}

export function istDayLabel(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "long", day: "numeric", month: "long" });
}

// "YYYY-MM-DD" of a date in IST, `days` from today.
export function istDate(days = 0) {
  const shifted = new Date(Date.now() + (IST_OFFSET_MINUTES + days * 24 * 60) * 60000);
  return shifted.toISOString().slice(0, 10);
}

// Mon–Sat 09:00–18:00 IST counts as business hours.
export function outsideBusinessHours(date, time) {
  if (!date || !time) return false;
  const [h] = time.split(":").map(Number);
  const weekday = new Date(`${date}T12:00:00+05:30`).getUTCDay();
  return weekday === 0 || h < 9 || h >= 18;
}
