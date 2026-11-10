export default {
  id: "growth", title: "Free experience", group: "Menu pages", order: 100, kind: "page", menu: "Free experience",
  summary: "Offer employers a capped free taste of the product, track whether they reach a result, and talk about paying only afterwards.",
  blocks: [
    ["p", "{{menu:Free experience}} is where you set the free offer and watch how employers use it. The idea: let them try first, give a real but limited taste, see if they get a result, and only then talk about paying."],
    ["h", "Who can use it"],
    ["ul", [
      "Owners can change the offer, the email capacity numbers and partners.",
      "Callers can add partners and change their status. Viewers can only read.",
      "Admins and logins limited to a workspace do not see this page in the menu.",
    ]],
    ["h", "The free offer"],
    ["p", "Four numbers set how much a company gets free: **Free candidates screened**, **Free interviews booked**, **Free period (days)** and **Referral bonus (extra candidates)**. A fifth box, **Where \"Use this draft\" sends them**, is the page people land on. Click **Save offer**. New emails and demo pages use these numbers."],
    ["warn", "The limits must also be set inside the product itself. If they are not, free use has no ceiling. This page only changes what you tell people."],
    ["h", "Did they reach a result?"],
    ["p", "A bar chart counts the companies that reached each step: Invited, Opened demo, Drafted a role, Published role, First candidate screened, First interview booked, Became paying. A big drop between two steps shows where people give up. You record these steps from the lead's **Product / Free trial** tab (see [The lead drawer](#help/leads-drawer))."],
    ["h", "Do today and honest heads-up"],
    ["ul", [
      "**Do today**: companies waiting for a nudge, with the suggested message.",
      "**Time for an honest heads-up**: companies near the end of their free allowance, with the reason and a suggested message. Use it to start the payment talk fairly and early.",
    ]],
    ["h", "How many emails can we safely send?"],
    ["p", "Enter **Mailboxes**, **Emails per mailbox per day** and **Leads to reach**, then click **Recalculate**. The page shows emails a day and a month, and about how many working days it takes to reach your leads. A red warning appears if your numbers are unsafe. Keep the daily number low for new mailboxes."],
    ["h", "Partners and communities"],
    ["p", "A list of staffing agencies, HR groups and associations that may send you referrals, with the referral count so far. To add one, type a **Name** and **Contact** and click **Add**. Change a partner's status between to_contact, contacted, active and paused with the drop-down."],
    ["p", "Related: [The lead drawer](#help/leads-drawer), [Outbox and sending](#help/outbox), [Success stories](#help/stories)."],
  ],
};
