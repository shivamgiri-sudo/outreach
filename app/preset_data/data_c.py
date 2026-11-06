"""Presets: Marketing / digital agency, Manufacturing / B2B supplies, Retail / D2C wholesale, Generic B2B."""
from .common import email, linkedin, script, sequence, snippet


def _pack(pid, name, description, lead_term, stages, icp, scoring, seq_name, emails, li, call, snippets) -> dict:
    return {"id": pid, "name": name, "description": description, "lead_term": lead_term, "stage_labels": stages, "icp": icp,
            "scoring": scoring, "sequence": sequence(seq_name, emails), "linkedin": li, "call_script": call,
            "snippets": snippets, "flows": None}


def agency() -> dict:
    return _pack(
        "agency", "Marketing / digital agency",
        "Agencies selling marketing, design, ads or web services to business owners and marketing heads.", "Projects",
        {"engaged": "Engaged", "interested": "Interested", "call_done": "Discovery call done", "trial": "Proposal sent", "customer": "Client"},
        {"text": "Businesses of 10 to 500 people that sell online or depend on leads, where the founder or marketing head owns growth.",
         "target_titles": ["Founder", "CMO", "Marketing Head", "Brand Manager", "Growth Lead", "Owner"],
         "company_size": [10, 500], "signals": ["Running ads", "Website looks dated", "Hiring a marketer", "New product launch"]},
        {"fit_size_min": 10, "open_roles_full": 20},
        "Agency outreach (5 emails)", [
            email(0, "Email 1 — observation", "An idea for {{company}}",
                  "I'm writing from {{my_company}}. {{my_tagline}}\n\nI had a quick look at how {{company}} shows up online and had a "
                  "couple of ideas. {{my_value_1}}\n\nWould you like me to share them?"),
            email(3, "Email 2 — how we work", "How we work with clients",
                  "We start small: one clear goal, one channel, and a plain report every month. {{my_value_2}}\n\nIs growth from "
                  "online channels a priority this quarter?"),
            email(7, "Email 3 — a short audit", "A free short audit?",
                  "If useful, I can send a short written audit of your website or ads with three things I'd change first. {{my_cta}}"),
            email(12, "Email 4 — what it costs", "Scope and budget",
                  "Usual questions are scope, timeline and budget. {{my_value_3}}\n\nI'll put a proposal in writing once I understand "
                  "your goal."),
            email(18, "Email 5 — close politely", "Closing the loop",
                  "I'll stop here. If you'd like the audit later, just reply.\n\nOur work: {{my_website}}"),
        ],
        linkedin("Hi {first_name}, I follow how brands like yours grow online and enjoy swapping notes with people who own growth. Happy to connect.", [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}"),
            (5, "What we do", "{first_name}, at {{my_company}} we help businesses with {{my_value_1}}\n\nWould a short audit of your website or ads be useful?"),
            (6, "Offer an audit", "Hi {first_name}, happy to send three practical suggestions for your website or ads, free. No pressure."),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. If you need help with marketing later, feel free to message me."),
        ], [
            ("marketing_head", r"\b(cmo|marketing|brand|growth|digital|performance)\b",
             "Something I've been thinking about: which channel gives marketing teams the most trouble to measure?"),
            ("founder", r"\b(founder|ceo|owner|director|md|proprietor)\b",
             "Something I've been thinking about: how do founders decide when to bring in an agency versus hiring?"),
            ("sales", r"\b(sales|business development|bd|revenue)\b",
             "Something I've been thinking about: how well do marketing leads match what sales teams actually want?"),
        ], "Something I've been thinking about: what makes a business's website actually bring in enquiries?",
            "Hi {first_name}, I work with businesses on getting more from their website and ads. Would be glad to connect."),
        script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You replied about the audit — is this a good minute?"),
            ("Goal", "What's the main goal for the next three months: more enquiries, more sales, or brand?"),
            ("Fit", "We usually help with {{my_value_1}}. Does that match what you need?"),
            ("Next step", "Shall I send a short written proposal with scope and budget?"),
        ], [
            ("We have an in-house team", "Great. We can support them on one channel rather than replace anyone."),
            ("Agencies didn't work before", "That's fair. What went wrong? I'll tell you honestly if we'd do any better."),
            ("Too expensive", "Understood. We can start with a small scope and one goal. I'll put options in writing."),
            ("Can you guarantee results?", "No honest agency can. I'll show how we measure and report so you can judge."),
            ("How did you get my email?", "From your public website. If you'd rather not hear from us, I'll remove it now."),
        ]),
        [snippet("Audit attached", "email", "{{greeting}}\n\nAs promised, here's the short audit with three things I'd change first.\n\n{{sign_off}}", "Your short audit"),
         snippet("Proposal sent", "email", "{{greeting}}\n\nThe proposal is attached. Happy to walk through it on a call.\n\n{{sign_off}}", "Proposal"),
         snippet("Case for a pilot", "email", "{{greeting}}\n\nWould a one-month pilot on one channel be a sensible first step?\n\n{{sign_off}}", "A one-month pilot?"),
         snippet("Monthly report", "email", "{{greeting}}\n\nHere's this month's report: what we did, what changed, and what's next.\n\n{{sign_off}}", "Monthly report"),
         snippet("WhatsApp: call time", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. When suits you for a 20-minute call about your goals?"),
         snippet("WhatsApp: audit ready", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Your short audit is ready. Shall I send it here?")])


def manufacturing() -> dict:
    return _pack(
        "manufacturing", "Manufacturing / B2B supplies",
        "Manufacturers and distributors selling parts, materials or equipment to purchase and plant teams.", "Monthly volume",
        {"engaged": "Enquiry", "interested": "Interested", "call_done": "Requirement taken", "trial": "Sample / quote sent", "customer": "Buyer"},
        {"text": "Plants, factories and distributors of 20 to 2,000 people that regularly buy what we make, where purchase or plant heads decide.",
         "target_titles": ["Purchase Head", "Procurement Manager", "Plant Head", "Operations Head", "Owner", "Director"],
         "company_size": [20, 2000], "signals": ["Regular buyer of our category", "New plant or expansion", "Tender or RFQ published"]},
        {"fit_size_min": 20, "open_roles_full": 1000},
        "Manufacturing / supplies outreach (5 emails)", [
            email(0, "Email 1 — introduction", "Supplier for {{company}}?",
                  "I'm writing from {{my_company}}. {{my_tagline}}\n\nWe supply: {{my_value_1}}\n\nIs your team open to quotes from "
                  "another supplier for these?"),
            email(3, "Email 2 — what buyers care about", "Quality, price and delivery",
                  "Purchase teams usually weigh three things: consistent quality, fair price and on-time delivery. {{my_value_2}}\n\n"
                  "If you share your requirement, I'll send a quote."),
            email(7, "Email 3 — samples", "Samples before you decide",
                  "If it helps, we can send samples or a trial lot so your team can check quality first. {{my_cta}}"),
            email(12, "Email 4 — documents", "Certificates and terms",
                  "I can share certificates, test reports and payment terms in writing. {{my_value_3}}"),
            email(18, "Email 5 — close politely", "Closing the loop",
                  "I'll stop here. When your next requirement comes up, just reply with it and I'll quote.\n\nCatalogue: {{my_website}}"),
        ],
        linkedin("Hi {first_name}, I work with purchase and plant teams in manufacturing and like to stay connected with people in the industry. Happy to connect.", [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}"),
            (5, "What we do", "{first_name}, at {{my_company}} we supply {{my_value_1}}\n\nWould a quote for your next requirement be useful?"),
            (6, "Offer samples", "Hi {first_name}, happy to send samples so your team can check quality first. No pressure."),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. When a requirement comes up, feel free to message me."),
        ], [
            ("purchase", r"\b(purchase|procurement|sourcing|buyer|supply chain|vendor)\b",
             "Something I've been curious about: what makes purchase teams add a new supplier to the approved list?"),
            ("plant", r"\b(plant|production|factory|works manager|manufacturing)\b",
             "Something I've been curious about: which supply delays hurt production schedules the most?"),
            ("quality", r"\b(quality|qa|qc)\b",
             "Something I've been curious about: what do quality teams check first with a new supplier?"),
            ("owner", r"\b(owner|founder|director|md|ceo|proprietor)\b",
             "Something I've been curious about: how do owners balance price against reliability when choosing suppliers?"),
        ], "Something I've been curious about: what makes a supplier easy to work with?",
            "Hi {first_name}, I help manufacturers source reliably. Would be glad to connect with people in the industry."),
        script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You replied about supplies — is this a good minute?"),
            ("Requirement", "What do you buy regularly in our category, roughly how much, and how often?"),
            ("Fit", "We supply {{my_value_1}}. Shall I send a quote against your specification?"),
            ("Next step", "Would samples help your quality team decide? Where should we send them?"),
        ], [
            ("We have a supplier", "Understood. A second quote is useful for comparison or as a backup supplier."),
            ("Price is high", "Share your target price and volume, and I'll see what's possible in writing."),
            ("Delivery times?", "I'll confirm lead times for your quantity in writing before you order."),
            ("Need certificates", "I'll send the certificates and test reports with the quote."),
            ("How did you get my number?", "From a public listing or directory. If you'd rather not hear from us, I'll remove it now."),
        ]),
        [snippet("Quote", "email", "{{greeting}}\n\nPlease find our quote attached for your requirement. Prices are valid as stated in the quote.\n\n{{sign_off}}", "Quote"),
         snippet("Samples dispatched", "email", "{{greeting}}\n\nThe samples are on their way. I'll check in once your team has had a look.\n\n{{sign_off}}", "Samples dispatched"),
         snippet("Certificates", "email", "{{greeting}}\n\nAttached are our certificates and test reports.\n\n{{sign_off}}", "Certificates and test reports"),
         snippet("Requirement check", "email", "{{greeting}}\n\nIs a new requirement coming up this month? Happy to quote.\n\n{{sign_off}}", "Any requirement this month?"),
         snippet("WhatsApp: quote", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Please share your requirement here and I'll send a quote."),
         snippet("WhatsApp: samples", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Where should we send the samples?")])


def retail() -> dict:
    return _pack(
        "retail", "Retail / D2C wholesale",
        "Brands and wholesalers selling products to retailers, resellers, distributors and marketplaces.", "Stores",
        {"engaged": "Enquiry", "interested": "Interested", "call_done": "Call done", "trial": "Trial order", "customer": "Stockist"},
        {"text": "Retail stores, resellers and distributors that sell our product category to customers in our regions.",
         "target_titles": ["Owner", "Store Manager", "Category Manager", "Buyer", "Distributor", "Purchase Manager"],
         "company_size": [1, 1000], "signals": ["Stocks similar products", "New store opening", "Festive season planning"]},
        {"fit_size_min": 1, "open_roles_full": 50},
        "Retail / wholesale outreach (5 emails)", [
            email(0, "Email 1 — introduction", "Stocking {{my_product}} at {{company}}?",
                  "I'm writing from {{my_company}}. {{my_tagline}}\n\nWe work with retailers on {{my_value_1}}\n\nWould you like our "
                  "catalogue and trade terms?"),
            email(3, "Email 2 — why stock it", "Why retailers stock us",
                  "Retailers usually want three things: products that sell, fair margins and reliable supply. {{my_value_2}}\n\n"
                  "Happy to send the details."),
            email(7, "Email 3 — trial order", "Start with a small trial order?",
                  "If it helps, you can start with a small trial order and see how it sells. {{my_cta}}"),
            email(12, "Email 4 — terms", "Margins, minimums and delivery",
                  "I can share margins, minimum order quantities and delivery terms in writing. {{my_value_3}}"),
            email(18, "Email 5 — close politely", "Closing the loop",
                  "I'll stop here. If you'd like the catalogue later, just reply.\n\nCatalogue: {{my_website}}"),
        ],
        linkedin("Hi {first_name}, I work with retailers and distributors and like to stay connected with people in retail. Happy to connect.", [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}"),
            (5, "What we do", "{first_name}, at {{my_company}} we make {{my_product}}. {{my_value_1}}\n\nWould our catalogue and trade terms be useful?"),
            (6, "Offer a trial", "Hi {first_name}, happy to set up a small trial order so you can see how it sells. No pressure."),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. If you'd like the catalogue later, feel free to message me."),
        ], [
            ("buyer", r"\b(buyer|category|merchandis|purchase|sourcing)\b",
             "Something I've been curious about: how do category teams decide which new brands get shelf space?"),
            ("store", r"\b(store manager|retail|outlet|shop)\b",
             "Something I've been curious about: which products do your customers ask for that are hard to get?"),
            ("distributor", r"\b(distributor|wholesale|dealer|stockist|trader)\b",
             "Something I've been curious about: what makes a brand easy for distributors to carry?"),
            ("owner", r"\b(owner|founder|director|md|ceo|proprietor)\b",
             "Something I've been curious about: how do store owners plan stock for the festive season?"),
        ], "Something I've been curious about: what makes a new product worth stocking?",
            "Hi {first_name}, I help retailers find products their customers ask for. Would be glad to connect."),
        script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You replied about stocking our products — is this a good minute?"),
            ("Store", "What do you sell most of today, and who are your customers?"),
            ("Fit", "Our range: {{my_value_1}}. Shall I send the catalogue and trade terms?"),
            ("Next step", "Would a small trial order help you decide?"),
        ], [
            ("Margins too low", "Tell me what margin works for you and your volumes; I'll put our best terms in writing."),
            ("No shelf space", "Understood. Would a small display or a trial on a few products work?"),
            ("Already carry a similar brand", "That's fine. I'll send details so you can compare price, margin and demand."),
            ("Minimum order too high", "I'll check what trial quantity we can offer and confirm in writing."),
            ("How did you get my number?", "From a public listing. If you'd rather not hear from us, I'll remove it now."),
        ]),
        [snippet("Catalogue and terms", "email", "{{greeting}}\n\nHere are our catalogue and trade terms. {{my_value_1}}\n\n{{sign_off}}", "Catalogue and trade terms"),
         snippet("Trial order", "email", "{{greeting}}\n\nThis confirms your trial order. I'll share dispatch details shortly.\n\n{{sign_off}}", "Your trial order"),
         snippet("Restock reminder", "email", "{{greeting}}\n\nHow are the products selling? Shall I prepare a restock?\n\n{{sign_off}}", "Time to restock?"),
         snippet("Festive planning", "email", "{{greeting}}\n\nPlanning stock for the season? I can hold quantities for you.\n\n{{sign_off}}", "Season planning"),
         snippet("WhatsApp: catalogue", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Shall I send our catalogue and trade terms here?"),
         snippet("WhatsApp: restock", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Shall I prepare a restock order for you?")])


def generic() -> dict:
    return _pack(
        "generic_b2b", "Generic B2B",
        "A neutral starting point for any business selling to other businesses. Edit the words to fit.", "Deal size",
        {},
        {"text": "Businesses that have the problem we solve and someone who can decide or recommend a purchase.",
         "target_titles": ["Founder", "Director", "Head of Operations", "Manager", "Owner"],
         "company_size": [5, 1000], "signals": ["Recent enquiry", "Growing team", "Uses a related product"]},
        {"fit_size_min": 5, "open_roles_full": 50},
        "Generic B2B outreach (4 emails)", [
            email(0, "Email 1 — introduction", "A question for {{company}}",
                  "I'm writing from {{my_company}}. {{my_tagline}}\n\n{{my_value_1}}\n\nIs this relevant to {{company}} right now?"),
            email(4, "Email 2 — how we help", "How we help",
                  "In short: {{my_value_2}}\n\nIf useful, I can explain in a 15-minute call."),
            email(9, "Email 3 — next step", "A simple next step",
                  "{{my_cta}}\n\nOr send me your questions and I'll answer in writing. {{my_value_3}}"),
            email(16, "Email 4 — close politely", "Closing the loop",
                  "I'll stop here. If the timing is better later, just reply.\n\nMore: {{my_website}}"),
        ],
        linkedin("Hi {first_name}, I enjoy connecting with people who run and grow businesses. Happy to connect and swap notes.", [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}"),
            (5, "What we do", "{first_name}, at {{my_company}} we help with {{my_value_1}}\n\nWould a short conversation be useful?"),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. If it's useful later, feel free to message me. All the best."),
        ], [
            ("founder", r"\b(founder|ceo|owner|director|md|proprietor)\b",
             "Something I've been thinking about: what takes up more of a founder's week than it should?"),
            ("operations", r"\b(operations|coo|ops|admin)\b",
             "Something I've been thinking about: which processes cause the most rework for operations teams?"),
            ("manager", r"\b(manager|head|lead|vp)\b",
             "Something I've been thinking about: what would make your team's week easier?"),
        ], "Something I've been thinking about: what's the one thing slowing your team down most this quarter?",
            "Hi {first_name}, I work with businesses on practical ways to grow. Would be good to connect."),
        script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You replied to my email — is this a good minute?"),
            ("Discovery", "How do you handle this today, and what would you like to be better?"),
            ("Fit", "What we do: {{my_value_1}}. Does that sound relevant?"),
            ("Next step", "Shall we set up a short call with anyone else who should be involved?"),
        ], [
            ("Not interested", "Thanks for telling me. May I check back in a few months?"),
            ("Send details", "Of course. What should the details cover so they're useful?"),
            ("No budget", "Understood. When do you plan budgets? I'll check back then."),
            ("How much does it cost?", "I'll send pricing in writing so there's no guesswork."),
            ("How did you get my details?", "From a public source. If you'd rather not hear from us, I'll remove them now."),
        ]),
        [snippet("Intro recap", "email", "{{greeting}}\n\nThanks for your time today. As promised, a short recap and next steps.\n\n{{sign_off}}", "Recap and next steps"),
         snippet("Pricing", "email", "{{greeting}}\n\nAs requested, our pricing: {{my_website}}\n\n{{sign_off}}", "Pricing"),
         snippet("Book a call", "email", "{{greeting}}\n\nWould 15 minutes this week suit you? {{my_cta}}\n\n{{sign_off}}", "15 minutes this week?"),
         snippet("Check in", "email", "{{greeting}}\n\nChecking in to see if anything has changed on your side.\n\n{{sign_off}}", "Checking in"),
         snippet("WhatsApp: call time", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. When is a good time for a short call?"),
         snippet("WhatsApp: details", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Shall I send the details here?")])
