// One place for the words people see, so raw codes like "whatsapp_active" never reach the screen.
export const STAGE_LABELS = {
  new: "New", verified: "Verified", enriched: "Enriched", contacted: "Contacted", engaged: "Engaged",
  interested: "Interested", whatsapp_active: "WhatsApp active", call_done: "Call done", trial: "Trial",
  customer: "Customer", advocate: "Advocate", not_now: "Not now", unsubscribed: "Unsubscribed", disqualified: "Disqualified",
};
export const STAGE_ORDER = Object.keys(STAGE_LABELS);
export const stageLabel = (s) => STAGE_LABELS[s] || s;

const SOURCE_LABELS = {
  apollo: "Apollo", sales_navigator: "Sales Navigator", job_board: "Job board", web_scraper: "Website",
  manual: "Added by hand", bmi_sheet: "Team sheet", bmi_demo_sheet: "Team sheet (demos)", job_page: "Job page",
  referral: "Referral", enrichment: "Website lookup", contact_scraper: "Contact scraper",
};
export const sourceLabel = (s = "") => SOURCE_LABELS[s.split(":")[0]] || s.split(":")[0].replace(/_/g, " ");

export const BAND_LABELS = { hot: "Hot", warm: "Warm", cold: "Cold" };

export const SEQUENCE_STOPPED = {
  replied: "they replied", unsubscribed: "they unsubscribed", bounced: "the address bounced", send_failed: "sending failed 3 times",
};
