// Office landlines are kept on a lead for calls, but never get WhatsApp or SMS (owner rule). This tag marks them.
// Older leads have no phone_type, so an Indian number whose 10-digit part starts with 1-5 counts as a landline too.
export const isLandline = (lead) => !!lead?.phone && (lead.phone_type === "landline"
  || (!lead.phone_type && /^\+91[1-5]\d{9}$/.test(String(lead.phone))));

export default function PhoneTag({ lead }) {
  if (!isLandline(lead)) return null;
  return <span className="pill" data-testid="landline-tag" title="Office landline: callable, no WhatsApp or SMS" style={{ marginLeft: 6 }}>Landline</span>;
}
