"""Presets: Recruitment / HR tech (today's built-in content), Real estate, B2B SaaS."""
from .common import email, flows, linkedin, script, sequence, snippet


def recruitment() -> dict:
    """Today's content: the built-in email sequence, LinkedIn steps and personas, and call script."""
    from ..linkedin_seq import CONNECT_VARIANTS, DEFAULT_HOOK, PERSONAS, STEPS
    from ..rep_workflow import DEFAULT_OBJECTIONS, DEFAULT_SCRIPT
    from ..sequences import DEFAULT_SEQUENCE
    first, second = DEFAULT_SEQUENCE[0], DEFAULT_SEQUENCE[1]
    return {
        "id": "recruitment", "name": "Recruitment / HR tech", "default": True,
        "description": "Selling to companies that hire: HR heads, talent acquisition and founders. This is the content the app ships with.",
        "lead_term": "Open roles",
        "stage_labels": {},
        "icp": {"text": "Indian companies with 20 or more employees that are hiring now (several open roles, or roles open for weeks). "
                        "Best contacts: HR head, talent acquisition lead, founder or business head.",
                "target_titles": ["CHRO", "HR Head", "Talent Acquisition Lead", "Recruitment Manager", "Founder", "Business Head"],
                "company_size": [20, 5000], "signals": ["Several open roles", "Roles open more than 30 days", "Reposted jobs"]},
        "scoring": {"fit_size_min": 20, "open_roles_full": 50},
        "sequence": sequence("Recruitment outreach (6 emails)", [{k: v for k, v in s.items()} for s in DEFAULT_SEQUENCE]),
        "linkedin": {"steps": [dict(s) for s in STEPS], "personas": [{"key": k, "pattern": p, "hook": h} for k, p, h in PERSONAS],
                     "default_hook": DEFAULT_HOOK, "variant_b": CONNECT_VARIANTS["B"]},
        "call_script": {"script": [dict(s) for s in DEFAULT_SCRIPT], "objections": [dict(o) for o in DEFAULT_OBJECTIONS]},
        "snippets": [
            snippet("Share the sample screen", "email", "{{greeting}}\n\nAs promised, here is a sample screen for your opening: {{demo_link}}\n\n{{sign_off}}",
                    "Sample screen for your opening"),
            snippet("Ask for the job description", "email", "{{greeting}}\n\nCould you share the job description for the role? I'll set up the screening questions from it.\n\n{{sign_off}}",
                    "Job description for {{role}}"),
            snippet("Book a call", "email", "{{greeting}}\n\nWould 15 minutes this week work to look at your open roles together? {{my_cta}}\n\n{{sign_off}}",
                    "15 minutes this week?"),
            snippet("Thanks for the reply", "email", "{{greeting}}\n\nThanks for getting back to me. I'll keep this short: what is the role you most need to fill right now?\n\n{{sign_off}}",
                    "Re: your reply"),
            snippet("WhatsApp: sample screen", "whatsapp", "{{greeting}} {{sender}} here. Sharing the sample screen we spoke about: {{demo_link}}"),
            snippet("WhatsApp: call time", "whatsapp", "{{greeting}} {{sender}} here. When is a good time for a 10-minute call about your open roles?"),
        ],
        "flows": flows("Recruitment", {**first, "body": first["body"]}, second,
                       "They said yes. Call them about their open roles; offer to set up the first role together."),
    }


def real_estate() -> dict:
    connect = ("Hi {first_name}, I work with people buying and investing in property in this area and like to stay "
               "connected with others in real estate. Happy to connect.")
    return {
        "id": "real_estate", "name": "Real estate",
        "description": "Developers, brokers and channel partners reaching home buyers, investors or corporate tenants.",
        "lead_term": "Properties",
        "stage_labels": {"contacted": "Contacted", "engaged": "Enquiry", "interested": "Interested", "call_done": "Call done",
                         "trial": "Site visit", "customer": "Booked"},
        "icp": {"text": "Home buyers and investors looking in our locations and budget range, or companies looking for office space. "
                        "Best contacts: the buyer themselves, family office heads, admin or facilities heads.",
                "target_titles": ["Founder", "Director", "Admin Head", "Facilities Manager", "Investor", "HNI"],
                "company_size": [1, 1000], "signals": ["Recent enquiry", "Budget fits our projects", "Visited the site or website"]},
        "scoring": {"fit_size_min": 1, "open_roles_full": 5},
        "sequence": sequence("Real estate enquiry follow-up (5 emails)", [
            email(0, "Email 1 — introduction", "Homes and spaces at {{my_company}}",
                  "I'm writing from {{my_company}}. {{my_tagline}}\n\nIf you are looking for property in the area, I can share the "
                  "options that match your budget and timeline: {{my_value_1}}\n\nWould you like the current availability list?"),
            email(3, "Email 2 — what matters to you", "What are you looking for?",
                  "Buyers usually care about three things: location, budget and possession date. If you tell me yours, I'll only "
                  "send what fits.\n\nWhat we can help with: {{my_value_2}}"),
            email(7, "Email 3 — site visit", "A site visit at a time that suits you?",
                  "Pictures only tell part of the story. If you'd like to see a property, I can arrange a visit at a time that "
                  "suits you, weekends included.\n\n{{my_cta}}"),
            email(12, "Email 4 — questions answered", "Questions about paperwork or loans?",
                  "Many buyers have questions about approvals, paperwork and home loans before they decide. Happy to answer "
                  "yours plainly, with no obligation.\n\n{{my_value_3}}"),
            email(18, "Email 5 — close politely", "Should I close your enquiry?",
                  "I haven't heard back, so I'll stop here. If you are still looking later, just reply and I'll send the latest "
                  "options.\n\nMore about us: {{my_website}}"),
        ]),
        "linkedin": linkedin(connect, [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}\n\nAre you looking at property yourself at the moment, or advising others?"),
            (5, "Share what we do", "{first_name}, at {{my_company}} we help buyers and investors find property that fits their "
                                    "budget and timeline. {{my_value_1}}\n\nWould the current availability list be useful?"),
            (6, "Offer a visit", "Hi {first_name}, if you'd like to see a property in person, I can arrange a visit at a time "
                                 "that suits you. No pressure either way."),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. If property comes up later, feel free to message me. All the best."),
        ], [
            ("investor", r"\b(investor|hni|family office|wealth|portfolio)\b",
             "Something I've been curious about: when you look at property as an investment, what matters most to you?"),
            ("corporate", r"\b(admin|facilit|real estate head|workplace|office manager)\b",
             "Something I've been curious about: how far ahead do teams usually plan an office move?"),
            ("founder", r"\b(founder|ceo|owner|director|md|proprietor)\b",
             "Something I've been curious about: do growing businesses usually rent or buy their workspace?"),
            ("broker", r"\b(broker|channel partner|consultant|realtor|agent)\b",
             "Something I've been curious about: what do your buyers ask about most these days?"),
        ], "Something I've been curious about: what do people look for first when choosing a home or office?",
            "Hi {first_name}, I help people find homes and workspaces that fit their budget. Always glad to connect with people interested in property."),
        "call_script": script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You enquired about property with us — is this a good minute?"),
            ("Needs", "Which area are you looking in, what budget range, and when would you like possession?"),
            ("Fit", "Based on that, we have a few options I can send. Shall I share them on WhatsApp or email?"),
            ("Next step", "Would you like to visit the site? Which day and time suit you?"),
        ], [
            ("Just browsing", "No problem. Shall I send the options once so you can compare at your own pace?"),
            ("Too expensive", "Understood. What range works for you? I'll only send options within it."),
            ("Need to talk to family", "Of course. Would a weekend visit with your family be easier?"),
            ("Is it approved?", "I'll send the approval details in writing so you can check them yourself."),
            ("How did you get my number?", "From your enquiry or a public listing. If you'd rather not hear from us, I'll remove it now."),
        ]),
        "snippets": [
            snippet("Availability list", "email", "{{greeting}}\n\nHere are the options that match what you told me. {{my_value_1}}\n\n{{sign_off}}", "Options that fit your budget"),
            snippet("Site visit confirmation", "email", "{{greeting}}\n\nThis confirms your site visit. I'll share the location pin the day before.\n\n{{sign_off}}", "Your site visit"),
            snippet("Loan and paperwork", "email", "{{greeting}}\n\nHappy to walk you through the paperwork and loan options. When suits you for a short call?\n\n{{sign_off}}", "Paperwork and loans"),
            snippet("Follow-up after visit", "email", "{{greeting}}\n\nThanks for visiting. What did you think? Any questions I can answer?\n\n{{sign_off}}", "After your visit"),
            snippet("WhatsApp: availability", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Shall I send the current availability for your budget here?"),
            snippet("WhatsApp: visit time", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Which day and time suit you for a site visit?"),
        ],
        "flows": None,
    }


def b2b_saas() -> dict:
    connect = ("Hi {first_name}, I follow how teams like yours choose and use software, and I enjoy swapping notes with "
               "people who run operations. Happy to connect.")
    return {
        "id": "b2b_saas", "name": "B2B SaaS",
        "description": "Software companies selling subscriptions to other businesses: founders, heads of function and team leads.",
        "lead_term": "Seats",
        "stage_labels": {"engaged": "Engaged", "interested": "Interested", "call_done": "Demo done", "trial": "Trial", "customer": "Paying customer"},
        "icp": {"text": "Businesses of 20 to 1,000 people with the problem our product solves, where a team lead or head of function can "
                        "decide or champion a trial.",
                "target_titles": ["Founder", "CTO", "Head of Operations", "Head of Sales", "Product Manager", "Team Lead"],
                "company_size": [20, 1000], "signals": ["Hiring for the team that uses the product", "Uses a tool we integrate with", "Visited pricing page"]},
        "scoring": {"fit_size_min": 20, "open_roles_full": 100},
        "sequence": sequence("B2B SaaS outbound (5 emails)", [
            email(0, "Email 1 — the problem", "A question about how {{company}} handles this",
                  "I'm reaching out from {{my_company}}. {{my_tagline}}\n\nTeams we talk to often spend time on work that could be "
                  "simpler. {{my_value_1}}\n\nIs this something {{company}} is looking at this quarter?"),
            email(3, "Email 2 — how it works", "How {{my_product}} works, in short",
                  "In short, {{my_product}} helps with this: {{my_value_2}}\n\nIf it's relevant, I can show you in a 20-minute call "
                  "using your own use case."),
            email(7, "Email 3 — try it", "Try it on your own data?",
                  "The quickest way to judge a tool is to try it. {{my_cta}}\n\nHappy to help set it up so it isn't extra work for your team."),
            email(12, "Email 4 — practical questions", "Security, setup and pricing",
                  "Usual questions at this point are security, setup time and price. {{my_value_3}}\n\nI'll answer any of these in "
                  "writing if that's easier."),
            email(18, "Email 5 — close politely", "Closing the loop",
                  "I'll stop here so I don't crowd your inbox. If the timing is better later, reply any time.\n\nMore: {{my_website}}"),
        ]),
        "linkedin": linkedin(connect, [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}\n\nCurious how you handle it today."),
            (5, "What we do", "{first_name}, at {{my_company}} we built {{my_product}}. {{my_value_1}}\n\nWould a short look be useful for your team?"),
            (6, "Offer a demo", "Hi {first_name}, happy to show it on your own use case in 20 minutes, if that's useful. No pressure."),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. If it becomes a priority, I'm happy to help. Best of luck with the quarter."),
        ], [
            ("technical", r"\b(cto|vp.{0,10}engineering|head of engineering|architect|tech lead|it head|cio)\b",
             "Something I've been thinking about: how do engineering teams decide which tools are worth integrating?"),
            ("operations", r"\b(coo|operations|ops|process)\b",
             "Something I've been thinking about: which manual steps cost operations teams the most time?"),
            ("sales", r"\b(sales|revenue|growth|business development|bd)\b",
             "Something I've been thinking about: where does a sales team lose the most time between first call and close?"),
            ("founder", r"\b(founder|ceo|owner|managing director|md)\b",
             "Something I've been thinking about: which tools do founders keep paying for, and which do they drop?"),
            ("product", r"\b(product manager|head of product|cpo|product owner)\b",
             "Something I've been thinking about: how do product teams decide between building and buying?"),
        ], "Something I've been thinking about: which everyday tasks take your team longer than they should?",
            "Hi {first_name}, I work with teams on simpler ways of working with software. Would be good to connect and swap notes."),
        "call_script": script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You replied to my email about {{my_product}} — is now a good minute?"),
            ("Discovery", "How does your team handle this today, and what's the most annoying part of it?"),
            ("Fit", "What we do: {{my_value_1}}. Does that match what you described?"),
            ("Next step", "Shall we book 20 minutes to show it on your own use case? Who else should join?"),
        ], [
            ("We already use a tool", "Makes sense. What would have to be better for a switch to be worth it?"),
            ("Send me info", "Sure. What should it cover so it's useful, rather than a generic deck?"),
            ("No budget", "Understood. When do you plan budgets? I'll check back then."),
            ("Is our data safe?", "I'll send our security details in writing so your team can review them."),
            ("How much does it cost?", "I'll send current pricing in writing so there's no guesswork."),
        ]),
        "snippets": [
            snippet("Demo invite", "email", "{{greeting}}\n\nHere's a link to pick a time for a 20-minute demo: {{my_cta}}\n\n{{sign_off}}", "20-minute demo"),
            snippet("Trial set-up", "email", "{{greeting}}\n\nYour trial is ready. Reply with any question and I'll help you get set up.\n\n{{sign_off}}", "Your trial"),
            snippet("Security answers", "email", "{{greeting}}\n\nAs requested, our security details are attached. Happy to join a call with your IT team.\n\n{{sign_off}}", "Security details"),
            snippet("Pricing in writing", "email", "{{greeting}}\n\nAs promised, here is our current pricing: {{my_website}}\n\n{{sign_off}}", "Pricing"),
            snippet("WhatsApp: demo time", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Which day suits you for a 20-minute demo?"),
            snippet("WhatsApp: trial check-in", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. How is the trial going? Anything I can help with?"),
        ],
        "flows": None,
    }
