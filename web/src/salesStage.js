// A lead's sales stage as the Leads list shows it (the stored record; a lead nobody has staged yet is an Inquiry).
export const SALES_STATUSES = ["Inquiry / Raw Lead", "Marketing Qualified Lead (MQL)", "Sales Accepted Lead (SAL)", "Sales Qualified Lead (SQL)",
  "Discovery Meeting Held", "Proposal Submitted", "Negotiation / Commercials", "Closed Won", "Closed Lost", "Recycled / Nurture"];
export const salesStageOf = (lead) => (lead?.sales && SALES_STATUSES.includes(lead.sales.status) ? lead.sales.status : SALES_STATUSES[0]);
