// Recipe: connect Facebook and Instagram Lead Ads.
export default {
  id: "recipe-meta-lead-ads", title: "Connect Meta Lead Ads", group: "How-to recipes", order: 20, kind: "recipe",
  roles: ["owner"],
  summary: "Bring leads from Facebook and Instagram Lead Ads into your lead list within seconds, with the campaign and ad that produced them.",
  blocks: [
    ["h", "Before you start"],
    ["ul", [
      "Only the owner can do this. Other logins see the message **Only the owner can connect lead sources.**",
      "You need admin access to the Facebook Page, a Meta developer app (developers.facebook.com), and a Lead Ads form that is used in a live ad.",
      "You will copy values between two browser tabs: this app and Meta. Keep both open.",
    ]],
    ["p", "This connects [[Meta Lead Ads]] through a [[Webhook|webhook]]: Meta calls an address in this app each time someone fills in your form."],
    ["h", "Steps"],
    ["steps", [
      "Open {{menu:Integrations}} and click the **Lead capture** tab. Click **Add connector**.",
      "Choose **Facebook and Instagram Lead Ads**. Type a name only you will see, for example Diwali Meta ads. Click **Create connector**. A panel opens.",
      "The panel shows a **Callback URL**, a **Verify token** and a **Capture token (secret)**. The secret token is shown in full only once, so copy it now if you need it. The panel's **How do I get this? Full step-by-step guide** button explains each Meta screen.",
      "In Meta Ads Manager, create a Lead Ads form (Instant Form) and use it in your ad.",
      "In developers.facebook.com, open your Meta App, add the **Webhooks** product, choose **Page** and press **Subscribe to this object**.",
      "Paste the **Callback URL** and the **Verify token** from this app, then subscribe to the **leadgen** field.",
      "Subscribe your Facebook Page to the app (Page settings, or the Leads Access tool).",
      "Back in this app, under **Settings**, paste the **Meta App secret** and the **Page access token** (it needs the leads_retrieval permission). Leave **Graph API version** as it is unless told otherwise. Click **Save settings**.",
      "Under **Who gets the call task**, type a caller's name, or leave it empty for the default caller. Add **Tags for these leads** if you want. Click **Save settings** again.",
      "Under **Try it**, click **Send a test lead**. You can also use Meta's Lead Ads Testing Tool.",
    ]],
    ["h", "How to know it worked"],
    ["ul", [
      "The connector card shows a green **On** label. If it says **Needs details**, one of the required boxes is empty.",
      "After **Send a test lead**, the panel says **Test lead arrived**. Test leads are marked as a test and are not counted in your numbers.",
      "**Recent captures** at the bottom of the tab lists each arrival as **New lead**, **Added to an existing lead**, **Refused (opted out or erased)**, **Repeat ignored** or **Problem**.",
      "A real lead gets a high-priority \"Call now\" task. Nothing is emailed or sent on WhatsApp automatically.",
    ]],
    ["h", "If leads do not appear"],
    ["ul", [
      "If Meta cannot verify the callback, the **Verify token** in Meta differs from the one in this app. Paste it again.",
      "Open **Recent captures** and read the **Detail** column. A message that Meta answered with error 190 means the Page access token has expired. Make a new one in Meta and save it here.",
      "A message about no valid lead id means the ping was only a test from Meta.",
    ]],
    ["warn", "**Make a new token (the old one stops working)** and **Delete this connector** cut the connection at once. Leads already captured stay, but nothing new arrives until Meta is given the new value. Use **Switch off** if you only want to pause."],
    ["tip", "Do not share the App secret or Page access token in chat or email. They are stored inside the connector and shown only as a masked value."],
  ],
};
