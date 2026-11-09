// DRAFT Privacy Policy for lawyer review (see terms.js for the data format).
export default {
  id: "privacy",
  title: "Privacy Policy",
  lede: "How Mas Callnet India Pvt Ltd handles personal data in MCN Outreach (Outreach OS).",
  sections: [
    { id: "who", title: "1. Who we are", blocks: [
      "MCN Outreach (Outreach OS) is provided by Mas Callnet India Pvt Ltd, CIN [CIN], [REGISTERED ADDRESS] (\"we\"). This policy explains what personal data the Service handles, why, who sees it, and your rights. Contact: [PRIVACY EMAIL]. Grievance Officer: [GRIEVANCE OFFICER NAME], [GRIEVANCE OFFICER EMAIL].",
    ] },
    { id: "roles", title: "2. Our role under the DPDP Act, 2023", blocks: [
      "Under the Digital Personal Data Protection Act, 2023 (the DPDP Act), there are two kinds of data in the Service, and our role differs:",
      [
        "Client lead data (the people our customers contact, and their replies): our customer is the data fiduciary and decides why and how it is used. We are the data processor and act on the customer's instructions under the Data Processing Agreement.",
        "Our own account and login data (our customers' staff who use the Service, billing contacts, security logs): we are the data fiduciary for this data.",
      ],
      "If you are a person whose details are in a customer's lead list, please contact that customer first. You can also contact us and we will help route your request.",
    ] },
    { id: "collect", title: "3. What we collect", blocks: [
      { head: ["Data", "Examples", "Source"], rows: [
        ["Account and login data", "Name, work email, phone, company, role, password hash, two-step login settings, workspace membership", "You or your administrator"],
        ["Client lead data", "Business contact details of leads (name, job title, company, work email, phone, LinkedIn URL), notes, call outcomes, tags, custom fields", "Uploaded or entered by the client, imports, enrichment tools, connected forms"],
        ["Message records", "Email, WhatsApp and LinkedIn messages sent and received, delivery status, replies, unsubscribes", "The Service and the channels you connect"],
        ["Tracking data", "Email opens and link clicks, unsubscribe clicks", "Tracking pixel and signed links in messages"],
        ["Usage and security data", "Login history including IP address and browser/device details, actions in the change log, errors", "Automatically when you use the Service"],
      ] },
      "We do not ask for or intend to hold sensitive categories such as health or financial account data. Customers should not upload them.",
    ] },
    { id: "why", title: "4. Why we use it", blocks: [
      [
        "to provide the Service: store leads, run sequences, send and record messages, produce reports;",
        "to keep the Service secure: sign-in checks, rate limits, abuse and bounce monitoring, fraud prevention;",
        "to keep an audit trail: a tamper-evident change log of who changed what and when;",
        "to support you, to bill you once billing is introduced, and to meet legal duties;",
        "to improve the Service using aggregated, non-identifying statistics.",
      ],
      "We do not sell personal data and do not show advertising.",
    ] },
    { id: "basis", title: "5. Lawful basis and consent", blocks: [
      "For our own account and login data we rely on your consent (given at sign-up) and on legitimate uses the DPDP Act permits, such as providing the service you asked for, security, and complying with law. You can withdraw consent at any time, which may mean we can no longer provide the Service.",
      "For client lead data, the client is responsible for having consent or another lawful ground to hold and contact those people. We process it only on the client's instructions.",
    ] },
    { id: "sharing", title: "6. Who we share data with (sub-processors)", blocks: [
      "We use these providers to run the Service. Entries marked [CONFIRM] must be verified by the company before this page is published.",
      { head: ["Provider", "Purpose", "Data", "Location"], rows: [
        ["Oracle Cloud Infrastructure, Mumbai (MySQL HeatWave)", "Hosting and database", "All Service data", "India [CONFIRM]"],
        ["Resend", "Transactional and cold email delivery", "Recipient address, message content, delivery events", "[CONFIRM LOCATION]"],
        ["Hostinger mailbox", "Reply capture over the client's mailbox", "Replies and mailbox metadata", "[CONFIRM LOCATION]"],
        ["Apollo", "Lead enrichment (if the client uses it)", "Lead name, company, email", "[CONFIRM LOCATION]"],
        ["Hunter", "Email finding and verification (if used)", "Lead name, company domain, email", "[CONFIRM LOCATION]"],
        ["Firecrawl", "Website enrichment (if used)", "Company website addresses", "[CONFIRM LOCATION]"],
        ["Anthropic", "AI drafting, only when the client enables it", "Text the client sends for drafting, which may include lead details", "[CONFIRM LOCATION]"],
        ["Google", "Sign-in, and Google Sheets if connected", "Account email and name; sheet data the client chooses", "[CONFIRM LOCATION]"],
        ["Meta", "Lead Ads import, if connected", "Lead form data the client chooses", "[CONFIRM LOCATION]"],
        ["Billing provider", "Payments", "[TBD - no billing provider has been selected yet]", "[TBD]"],
      ] },
      "We may also disclose data when the law, a court or a regulator requires, or to protect rights and safety. If we are part of a merger or sale, data may transfer to the successor under this policy.",
    ] },
    { id: "retention", title: "7. How long we keep data", blocks: [
      [
        "Client lead data: kept while the account is active. Cold leads that have not been contacted or updated are flagged for review after [COLD LEAD REVIEW PERIOD, SUGGESTED 365] days so the client can delete them.",
        "Login history: the most recent 5000 records are kept; older rows are removed.",
        "Backups: kept for [BACKUP RETENTION PERIOD] and then overwritten.",
        "After you leave: data is exported on request, then erased within [DELETION PERIOD, SUGGESTED 30] days. The change log (hash chain) is kept as an audit record, and we keep what the law requires.",
      ],
    ] },
    { id: "security", title: "8. Security", blocks: [
      [
        "secrets such as connection keys and tokens are encrypted at rest;",
        "two-step login is available, and passwords are stored only as salted hashes;",
        "role-based access and workspace isolation keep each customer's data apart;",
        "a hash-chained audit log makes silent changes detectable;",
        "unsubscribe and tracking links are signed so they cannot be forged;",
        "IP-based rate limits slow down password guessing and abuse.",
      ],
      "See Annex 1 of the Data Processing Agreement for more detail.",
    ] },
    { id: "rights", title: "9. Your rights", blocks: [
      "Under the DPDP Act, 2023, you have the right to access a summary of your personal data and who it is shared with, to correct or complete it, to have it erased, to nominate someone to act for you, and to complain.",
      [
        "Users of the Service: use the in-app tools (profile, and the erase tools for leads and contacts), or email [PRIVACY EMAIL].",
        "People in a client's lead list: contact the client; the client can search for and erase your record with the in-app erase tools. You may also email [PRIVACY EMAIL] and we will pass it to the client and help.",
        "Grievances: write to the Grievance Officer, [GRIEVANCE OFFICER NAME], [GRIEVANCE OFFICER EMAIL]. We aim to acknowledge within [ACKNOWLEDGEMENT PERIOD, SUGGESTED 3] days and respond within [RESPONSE PERIOD, SUGGESTED 30] days.",
        "If you are not satisfied, you may approach the Data Protection Board of India once it is operating and the law allows.",
      ],
    ] },
    { id: "cookies", title: "10. Cookies and browser storage", blocks: [
      "The Service keeps your sign-in token in your browser's session storage only; it is cleared when you close the tab or sign out. We also keep small preferences (such as your colour theme) in your browser. We use no advertising or cross-site tracking cookies. Email messages sent by customers may contain a tracking pixel and tracked links, as described in section 3.",
    ] },
    { id: "transfers", title: "11. International transfers", blocks: [
      "Our main hosting is in India. Some sub-processors in section 6 may process data outside India. We transfer data only to countries and providers the DPDP Act and government notifications allow, and use contracts that require protection comparable to this policy. [TRANSFER SAFEGUARDS - CONFIRM WITH COUNSEL]",
    ] },
    { id: "breach", title: "12. Data breaches", blocks: [
      "If a breach affects personal data, we will contain it, assess it and notify the affected customers without undue delay (within [BREACH NOTICE PERIOD, SUGGESTED 48] hours for client data), and we will notify the Data Protection Board and affected people where the law requires.",
    ] },
    { id: "children", title: "13. Children", blocks: [
      "The Service is for businesses and is not meant for people under 18. We do not knowingly collect children's data. If you think we have, contact [PRIVACY EMAIL] and we will delete it.",
    ] },
    { id: "changes", title: "14. Changes", blocks: [
      "We will publish updates here with a new version label and give notice in the Service or by email for material changes.",
    ] },
    { id: "contact", title: "15. Contact", blocks: [
      "Mas Callnet India Pvt Ltd, [REGISTERED ADDRESS]. Privacy: [PRIVACY EMAIL]. Support: [SUPPORT EMAIL]. Grievance Officer: [GRIEVANCE OFFICER NAME], [GRIEVANCE OFFICER EMAIL].",
    ] },
  ],
};
