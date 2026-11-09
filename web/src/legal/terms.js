// DRAFT Terms of Service for lawyer review. Plain data: strings are paragraphs, arrays are bullet lists, {head, rows} are tables.
// Anything in [SQUARE BRACKETS] is a placeholder the company or its lawyer must fill; the page lists them all at the top.
export default {
  id: "terms",
  title: "Terms of Service",
  lede: "These terms govern your use of MCN Outreach (Outreach OS), provided by Mas Callnet India Pvt Ltd.",
  sections: [
    { id: "parties", title: "1. Who we are and what these terms cover", blocks: [
      "MCN Outreach (also called Outreach OS, the \"Service\") is provided by Mas Callnet India Pvt Ltd, CIN [CIN], registered office [REGISTERED ADDRESS] (\"we\", \"us\"). \"You\" or \"Client\" means the business that signs up for an account.",
      "By ticking the box on the sign-up form, or by using the Service, you agree to these terms, the Privacy Policy and the Data Processing Agreement (together, the \"Agreement\"). If you sign up for a company, you confirm that you are allowed to bind it.",
    ] },
    { id: "service", title: "2. The Service", blocks: [
      "The Service is a business-to-business outreach tool. It lets you keep leads and companies, build email, WhatsApp and LinkedIn sequences, send and track messages, record replies and calls, and produce reports.",
      "Work is organised in accounts and workspaces. Each user has a role (for example owner, admin, member or caller) that decides what they can see and change. Data in one workspace is not visible to other workspaces.",
      "New accounts are approved by the platform admin before they become active. Until approval, you can sign in but cannot send messages or use the lead tools. We may decline or delay an approval, and we will tell you why where we can.",
    ] },
    { id: "acceptable-use", title: "3. Acceptable use", blocks: [
      "You are responsible for everything you do with the Service and for the people you contact. You agree to:",
      [
        "contact people only where you have a lawful basis: their consent, or a genuine legitimate business reason that the law allows in the country where they are;",
        "honour every unsubscribe, opt-out and \"stop\" request promptly, and keep the Service's suppression list switched on;",
        "not upload or use purchased, rented or scraped lists of people who have not agreed to hear from you, and not send spam;",
        "not scrape or collect data in a way that breaks the terms of the website or platform it comes from;",
        "not use anti-detection, fingerprint-masking, proxy-rotation or similar tooling to hide automation from LinkedIn, WhatsApp, email providers or other platforms;",
        "send WhatsApp messages and SMS only to numbers that have consented to receive them, using approved templates where the platform requires them;",
        "not send unlawful, deceptive, defamatory, hateful or infringing content, malware, or anything that pretends to come from someone else;",
        "not probe, overload or try to break the Service, or access another account's data.",
      ],
      "LinkedIn and similar platforms restrict automation. If you connect or automate a LinkedIn profile, the risk of warnings, limits or account loss is yours alone.",
      "You are the data fiduciary (the person who decides why and how personal data is used) for the leads you load. You must follow the Digital Personal Data Protection Act, 2023, the Telecom Commercial Communications rules of TRAI including the Do Not Disturb (DND) registry, the Information Technology Act, 2000, and any other law that applies to your outreach. We provide tools to help, but compliance is your duty.",
    ] },
    { id: "account", title: "4. Your account and users", blocks: [
      [
        "Give us accurate sign-up details and keep them up to date.",
        "You decide who gets a user seat and what role they have. You are responsible for what your users do.",
        "Keep passwords and access links secret. Turn on two-step login for every user who can send messages or export data, and remove users who leave.",
        "Tell us at once at [SUPPORT EMAIL] if you think an account or credential has been misused.",
      ],
    ] },
    { id: "limits", title: "5. Limits, holds and suspension", blocks: [
      "To protect deliverability, other customers and the platform, the platform admin may set limits (for example daily sending caps). The platform admin may also put sending on hold, restrict features or suspend an account, with notice where practical, when:",
      [
        "bounce, spam-complaint or unsubscribe rates are high;",
        "we see signs of abuse, unlawful contacting, a compromised account or a security risk;",
        "a payment is overdue, or a law, court or regulator requires it;",
        "you break these terms.",
      ],
      "We will lift a hold or suspension when the cause is fixed. We are not liable for lost leads or sales caused by a hold or suspension that follows from this section.",
    ] },
    { id: "fees", title: "6. Fees and payment", blocks: [
      "Fees: [FEES / PLAN PRICING]. Billing terms, invoicing, taxes (including GST), payment dates and late-payment rules: [BILLING TERMS - TO FOLLOW]. Until billing terms are agreed in writing, the Service is provided under the arrangement we have agreed with you separately.",
    ] },
    { id: "data", title: "7. Your data", blocks: [
      "You keep all rights in the data you upload or create in the Service, including leads, templates, messages and notes (\"Client Data\"). You give us a limited licence to host, copy, process and transmit Client Data only to run the Service for you, to keep it secure, and as the Data Processing Agreement allows.",
      "We do not sell Client Data and do not use it to market to others. We process personal data in Client Data as your processor; see the Data Processing Agreement and Privacy Policy.",
      "We may use anonymous, aggregated usage figures (for example, how many messages are sent per day) to run and improve the Service. They do not identify you or your leads.",
    ] },
    { id: "exit", title: "8. Export and deletion when you leave", blocks: [
      "You can export your leads, messages, reports and the audit pack at any time from the Service, and you should do this before you close your account.",
      "After the Agreement ends we will erase Client Data from live systems within [DELETION PERIOD, SUGGESTED 30] days, and from backups as they expire in the normal cycle. We keep the change-log hash chain (a tamper-evident record of who changed what and when, which holds identifiers rather than lead content) as an audit record, and anything the law requires us to keep.",
    ] },
    { id: "availability", title: "9. Availability and support", blocks: [
      "We run the Service with reasonable care and on a best-effort basis. There is no uptime guarantee or service level unless we agree one in writing. We may take the Service down for maintenance and will try to give notice of planned downtime. Support is available at [SUPPORT EMAIL] during [SUPPORT HOURS].",
      "Third-party services we depend on (for example email delivery, WhatsApp, LinkedIn or enrichment providers) may change or fail. We are not responsible for their outages or rule changes.",
    ] },
    { id: "security", title: "10. Security", blocks: [
      "We use technical and organisational measures described in Annex 1 of the Data Processing Agreement. No system is perfectly secure. If we become aware of a breach affecting Client Data, we will tell you as set out in the Data Processing Agreement.",
    ] },
    { id: "ip", title: "11. Intellectual property", blocks: [
      "We own the Service, its software, design and documentation. We give you a non-exclusive, non-transferable right to use the Service during the term. You may not copy, resell, reverse engineer or build a competing service from it, except where the law allows. You own your content; if you send us feedback, we may use it freely.",
    ] },
    { id: "confidentiality", title: "12. Confidentiality", blocks: [
      "Each party will keep the other's non-public information confidential, use it only for the Agreement, and protect it with reasonable care. This does not cover information that is public through no fault of the recipient, was already known, or is independently developed. A party may disclose information when the law or a regulator requires, after telling the other party where lawful.",
    ] },
    { id: "warranties", title: "13. Warranties and disclaimer", blocks: [
      "Each party confirms it has the power to enter the Agreement. You confirm that you have the right to use the Client Data and to contact the people in it.",
      "Except as stated here, the Service is provided \"as is\" and \"as available\". To the extent the law allows, we do not promise that it will be uninterrupted or error-free, that messages will be delivered, opened or answered, or that any outreach will produce sales. AI-drafted text, if you switch it on, may be wrong; you must review it before sending.",
    ] },
    { id: "liability", title: "14. Limitation of liability", blocks: [
      "To the extent the law allows, neither party is liable for indirect or consequential loss, or for loss of profit, revenue, goodwill or data arising from the Agreement.",
      "Our total liability to you for all claims in connection with the Agreement in any 12-month period is limited to [LIABILITY CAP, FOR EXAMPLE FEES PAID IN THE PRECEDING 12 MONTHS]. Nothing here limits liability that cannot be limited by law, such as for fraud.",
    ] },
    { id: "indemnity", title: "15. Indemnity", blocks: [
      "You will defend and compensate us against third-party claims, fines and reasonable costs that arise from your Client Data, your messages and contacting of people, or your breach of section 3, except to the extent we caused the loss. We will tell you promptly of any claim and let you help to handle it.",
    ] },
    { id: "term", title: "16. Term and termination", blocks: [
      "The Agreement starts when you accept it and continues until ended. You may close your account at any time. We may end the Agreement on [NOTICE PERIOD, SUGGESTED 30] days' written notice, or immediately for serious or repeated breach, abuse, or where required by law. Sections that by their nature should continue (data, exit, confidentiality, liability, indemnity, governing law) survive termination.",
    ] },
    { id: "changes", title: "17. Changes to these terms", blocks: [
      "We may update these terms. We will publish the new version here with a new version label and give notice in the Service or by email before material changes take effect. If you keep using the Service after the effective date, you accept the new version; if you do not agree, you can close your account.",
    ] },
    { id: "law", title: "18. Governing law and disputes", blocks: [
      "The Agreement is governed by the laws of India. The courts at [JURISDICTION CITY] have exclusive jurisdiction, subject to any arbitration clause the parties agree in writing. [ARBITRATION CLAUSE, IF DESIRED].",
    ] },
    { id: "contact", title: "19. Contact", blocks: [
      "Mas Callnet India Pvt Ltd, [REGISTERED ADDRESS]. Support: [SUPPORT EMAIL]. Grievance Officer: [GRIEVANCE OFFICER NAME], [GRIEVANCE OFFICER EMAIL]. See the Privacy Policy for privacy requests.",
    ] },
  ],
};
