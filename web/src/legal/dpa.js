// DRAFT Data Processing Agreement for lawyer review (see terms.js for the data format).
export default {
  id: "dpa",
  title: "Data Processing Agreement",
  lede: "This agreement covers how Mas Callnet India Pvt Ltd processes personal data on behalf of its customers.",
  sections: [
    { id: "parties", title: "1. Parties and scope", blocks: [
      "This Data Processing Agreement (\"DPA\") is between the customer who accepts it at sign-up (\"Client\", the data fiduciary / controller) and Mas Callnet India Pvt Ltd, CIN [CIN], [REGISTERED ADDRESS] (\"Mas Callnet\", the data processor). It forms part of the Terms of Service. If they conflict on personal data, this DPA prevails.",
      "It applies to personal data in Client Data that Mas Callnet processes for the Client through MCN Outreach (Outreach OS), under the Digital Personal Data Protection Act, 2023 and its rules (\"DPDP Act\") and other data protection law that applies.",
    ] },
    { id: "subject", title: "2. Subject matter and duration", blocks: [
      "Mas Callnet hosts and processes Client Data so the Client can manage leads, run email, WhatsApp and LinkedIn outreach, record replies and calls, and produce reports. The DPA lasts for as long as Mas Callnet holds Client Data, including the exit period in section 12.",
    ] },
    { id: "nature", title: "3. Nature and purpose of processing", blocks: [
      "Storing, organising, retrieving, enriching (where the Client enables it), sending, tracking, exporting, backing up and deleting personal data, solely to provide the Service to the Client and to keep it secure.",
    ] },
    { id: "categories", title: "4. Categories of data and data principals", blocks: [
      { head: ["Item", "Description"], rows: [
        ["Data principals", "The Client's leads and prospects, their colleagues named in the data, and the Client's own users"],
        ["Personal data", "Business contact details (name, job title, company, work email, phone, LinkedIn URL), message content and replies, call notes and outcomes, tracking events (opens, clicks, unsubscribes), custom fields the Client creates"],
        ["Sensitive data", "None intended. The Client must not upload special or sensitive categories without agreeing safeguards first"],
      ] },
    ] },
    { id: "instructions", title: "5. Instructions", blocks: [
      "Mas Callnet processes personal data only on the Client's documented instructions, which are these terms and the Client's use of the Service's features. If Mas Callnet believes an instruction breaks the law, it will tell the Client. The Client is responsible for the lawfulness of its instructions, including having consent or another lawful ground, honouring opt-outs, and complying with DND/TRAI rules.",
    ] },
    { id: "confidentiality", title: "6. Confidentiality", blocks: [
      "Mas Callnet makes sure that people who access personal data are bound by confidentiality duties and are given access only as needed for their work.",
    ] },
    { id: "security", title: "7. Security", blocks: [
      "Mas Callnet applies reasonable security safeguards, at least those in Annex 1, and may improve them over time without lowering the level of protection.",
    ] },
    { id: "subprocessors", title: "8. Sub-processors", blocks: [
      "The Client gives general authorisation for the sub-processors in Annex 2. Mas Callnet remains responsible for them and uses written terms that give comparable protection. Mas Callnet will give the Client at least [SUB-PROCESSOR NOTICE PERIOD, SUGGESTED 14] days' notice of a new or replaced sub-processor, by notice in the Service or email. The Client may object on reasonable data protection grounds within that period; if the parties cannot resolve it, the Client may stop using the affected feature or end the Agreement and receive a refund of prepaid fees for the unused period [CONFIRM REFUND WORDING].",
    ] },
    { id: "assistance", title: "9. Assistance", blocks: [
      "Taking into account the nature of the processing, Mas Callnet will help the Client, by the Service's tools and on reasonable request, to respond to requests from data principals (access, correction, erasure, grievance), and with data protection impact assessments and consultations with authorities. The in-app erase tools let the Client find and delete a person's records. Mas Callnet may charge reasonable costs for extra assistance beyond normal Service features [CONFIRM].",
      "If Mas Callnet receives a request directly from a data principal about Client Data, it will pass it to the Client and not respond on the Client's behalf unless the law requires.",
    ] },
    { id: "breach", title: "10. Personal data breach", blocks: [
      "Mas Callnet will notify the Client without undue delay and within [BREACH NOTICE PERIOD, SUGGESTED 48] hours of becoming aware of a personal data breach affecting Client Data. The notice will describe, as far as known, what happened, the data and people affected, likely effects, and the steps taken or proposed. Mas Callnet will help the Client meet its duty to inform the Data Protection Board of India and affected data principals.",
    ] },
    { id: "audit", title: "11. Audits and information", blocks: [
      "Mas Callnet will give the Client the information needed to show compliance with this DPA. The Service provides the change log (a hash-chained record of changes) and an audit pack export that the Client can use for this. If that is not enough, the Client may, on [AUDIT NOTICE PERIOD, SUGGESTED 30] days' notice, no more than once a year and subject to confidentiality, audit relevant records and controls, at the Client's cost and without disrupting other customers.",
    ] },
    { id: "return", title: "12. Return and deletion", blocks: [
      "At the end of the Agreement, the Client can export its data. Mas Callnet will then delete Client Data from live systems within [DELETION PERIOD, SUGGESTED 30] days and from backups as they expire, unless the law requires it to keep some. The change-log hash chain is kept as an audit record; it holds identifiers and timestamps, not lead content. Mas Callnet will confirm deletion in writing on request.",
    ] },
    { id: "transfers", title: "13. Transfers outside India", blocks: [
      "Mas Callnet will transfer personal data outside India only through the sub-processors in Annex 2, in line with the DPDP Act and any restrictions the Government of India notifies. [TRANSFER SAFEGUARDS - CONFIRM WITH COUNSEL]",
    ] },
    { id: "liability", title: "14. Liability and governing law", blocks: [
      "Liability under this DPA is subject to the limits in the Terms of Service. This DPA is governed by the laws of India, and the courts at [JURISDICTION CITY] have jurisdiction, as set out in the Terms of Service.",
    ] },
    { id: "annex1", title: "Annex 1 - Security measures", blocks: [
      { head: ["Control", "What it means in practice"], rows: [
        ["Encryption of secrets", "Connection keys, tokens and similar secrets are encrypted at rest. Traffic uses HTTPS"],
        ["Access control", "Passwords stored as salted hashes; two-step login; roles (owner, admin, member, caller) limit what each user can do"],
        ["Workspace isolation", "Each workspace's data is separated; users see only workspaces they belong to"],
        ["Audit log", "A hash-chained change log records who changed what; tampering is detectable. Login history records IP and browser (most recent 5000 rows kept)"],
        ["Signed links", "Unsubscribe, tracking and report links are signed and cannot be forged"],
        ["Abuse and rate limiting", "IP rate limits on sign-in and public endpoints; sending limits, bounce and complaint monitoring, and automatic holds"],
        ["Hosting and backups", "Hosted on Oracle Cloud Infrastructure in Mumbai; regular backups with restricted access"],
        ["Erasure tools", "In-app tools to find and permanently delete a person's records"],
        ["Staff access", "Limited to people who need it for support and operations; confidentiality obligations apply"],
      ] },
    ] },
    { id: "annex2", title: "Annex 2 - Sub-processors", blocks: [
      "Entries marked [CONFIRM] must be verified by the company before this page is published.",
      { head: ["Sub-processor", "Purpose", "Location"], rows: [
        ["Oracle Cloud Infrastructure (MySQL HeatWave)", "Hosting and database", "Mumbai, India [CONFIRM]"],
        ["Resend", "Email delivery", "[CONFIRM LOCATION]"],
        ["Hostinger", "Mailbox for reply capture", "[CONFIRM LOCATION]"],
        ["Apollo", "Lead enrichment, if enabled", "[CONFIRM LOCATION]"],
        ["Hunter", "Email finding and verification, if enabled", "[CONFIRM LOCATION]"],
        ["Firecrawl", "Website enrichment, if enabled", "[CONFIRM LOCATION]"],
        ["Anthropic", "AI drafting, only if the Client enables it", "[CONFIRM LOCATION]"],
        ["Google", "Sign-in; Google Sheets if connected", "[CONFIRM LOCATION]"],
        ["Meta", "Lead Ads import, if connected", "[CONFIRM LOCATION]"],
        ["Billing provider", "Payments", "[TBD - not yet selected]"],
      ] },
    ] },
  ],
};
