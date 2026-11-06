"""Presets: Education / coaching, Insurance & financial advisors, Healthcare clinics."""
from .common import email, linkedin, script, sequence, snippet


def education() -> dict:
    connect = ("Hi {first_name}, I work with schools, colleges and training teams on how learners find the right course. "
               "Always glad to connect with people in education.")
    return {
        "id": "education", "name": "Education / coaching",
        "description": "Coaching institutes, ed-tech and training providers reaching students, parents, schools or HR teams.",
        "lead_term": "Students",
        "stage_labels": {"engaged": "Enquiry", "interested": "Interested", "call_done": "Counselling done", "trial": "Demo class",
                         "customer": "Enrolled"},
        "icp": {"text": "Students or parents looking for the courses we teach, or schools, colleges and companies that want training "
                        "for their learners or staff.",
                "target_titles": ["Principal", "Director", "Training Manager", "L&D Head", "HR Head", "Parent", "Student"],
                "company_size": [1, 5000], "signals": ["Course enquiry", "Upcoming exam or admission season", "Attended a demo class"]},
        "scoring": {"fit_size_min": 1, "open_roles_full": 100},
        "sequence": sequence("Education enquiry follow-up (5 emails)", [
            email(0, "Email 1 — introduction", "About {{my_product}} at {{my_company}}",
                  "I'm writing from {{my_company}}. {{my_tagline}}\n\nIf you're exploring courses, here is what we offer: "
                  "{{my_value_1}}\n\nWould you like the schedule and fee details?"),
            email(3, "Email 2 — the learner's goal", "What's the goal?",
                  "The right course depends on the goal: an exam, a skill, or a career change. If you tell me yours, I'll suggest "
                  "what fits, even if that isn't us.\n\n{{my_value_2}}"),
            email(7, "Email 3 — demo class", "Try a class first?",
                  "The best way to decide is to sit in a class. {{my_cta}}\n\nNo payment is needed for the demo."),
            email(12, "Email 4 — practical details", "Timings, fees and support",
                  "Families usually ask about timings, fees and what happens if a learner falls behind. {{my_value_3}}\n\nHappy to "
                  "answer anything in writing."),
            email(18, "Email 5 — close politely", "Shall I close your enquiry?",
                  "I'll stop here. If you'd like details for a later batch, just reply.\n\nMore: {{my_website}}"),
        ]),
        "linkedin": linkedin(connect, [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}\n\nWould love to hear your view."),
            (5, "What we do", "{first_name}, at {{my_company}} we run {{my_product}}. {{my_value_1}}\n\nWould details be useful for your learners or team?"),
            (6, "Offer a demo", "Hi {first_name}, happy to arrange a demo session for you or your team, if that helps. No pressure."),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. If training comes up later, feel free to message me. All the best."),
        ], [
            ("school_leader", r"\b(principal|headmaster|head of school|dean|vice chancellor|director of studies)\b",
             "Something I've been thinking about: how do schools decide which outside programmes are worth students' time?"),
            ("ld_head", r"\b(l&d|learning|training|talent development|capability)\b",
             "Something I've been thinking about: how do L&D teams measure whether a training programme worked?"),
            ("hr", r"\b(hr|human resources|people)\b",
             "Something I've been thinking about: which skills are hardest to find in new joiners right now?"),
            ("founder", r"\b(founder|ceo|owner|director|md)\b",
             "Something I've been thinking about: how do growing teams keep their people learning without losing work time?"),
        ], "Something I've been thinking about: what makes a course worth finishing for busy learners?",
            "Hi {first_name}, I help learners and teams find training that fits their goals. Would be glad to connect."),
        "call_script": script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You enquired about our courses — is this a good minute?"),
            ("Goal", "Who is the course for, and what are they aiming for — an exam, a skill or a job?"),
            ("Fit", "Based on that, {{my_product}} could fit because {{my_value_1}}. Does that sound right?"),
            ("Next step", "Would a free demo class help you decide? Which day suits?"),
        ], [
            ("Fees are high", "Understood. I'll send the full fee details in writing, including any instalment options we have."),
            ("Need to ask my parents", "Of course. Shall I send the details so you can share them, or call when they're free?"),
            ("Online or offline?", "I'll confirm which batches are available in each mode and send them to you."),
            ("Do you guarantee results?", "No one can honestly guarantee a result. I can explain how classes and support work so you can judge."),
            ("How did you get my number?", "From your enquiry. If you'd rather not hear from us, I'll remove it now."),
        ]),
        "snippets": [
            snippet("Schedule and fees", "email", "{{greeting}}\n\nHere are the schedule and fee details you asked for. {{my_value_1}}\n\n{{sign_off}}", "Schedule and fees"),
            snippet("Demo class booked", "email", "{{greeting}}\n\nYour demo class is booked. I'll send the joining details a day before.\n\n{{sign_off}}", "Your demo class"),
            snippet("Batch starting soon", "email", "{{greeting}}\n\nA new batch is starting soon. Would you like me to hold a seat while you decide?\n\n{{sign_off}}", "New batch"),
            snippet("After the demo", "email", "{{greeting}}\n\nThanks for attending the demo. Any questions before you decide?\n\n{{sign_off}}", "After your demo class"),
            snippet("WhatsApp: demo time", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Which day suits you for a free demo class?"),
            snippet("WhatsApp: fee details", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Shall I share the fee and schedule details here?"),
        ],
        "flows": None,
    }


def insurance() -> dict:
    connect = ("Hi {first_name}, I work with families and business owners on planning for protection and savings. "
               "Always glad to connect with people who think ahead.")
    return {
        "id": "insurance", "name": "Insurance & financial advisors",
        "description": "Insurance agents, financial planners and distributors reaching individuals, families and business owners.",
        "lead_term": "Policies",
        "stage_labels": {"engaged": "Enquiry", "interested": "Interested", "call_done": "Needs review done", "trial": "Proposal sent",
                         "customer": "Client"},
        "icp": {"text": "Working professionals, families and business owners who want protection or planning advice and are open to a "
                        "review conversation. Business owners may need group cover for staff.",
                "target_titles": ["Founder", "Owner", "Director", "HR Head", "Doctor", "Chartered Accountant", "Professional"],
                "company_size": [1, 500], "signals": ["Recent enquiry", "New business or new hires", "Policy renewal due"]},
        "scoring": {"fit_size_min": 1, "open_roles_full": 20},
        "sequence": sequence("Insurance & advisory outreach (5 emails)", [
            email(0, "Email 1 — introduction", "A short note from {{my_company}}",
                  "I'm writing from {{my_company}}. {{my_tagline}}\n\nMany people we speak with haven't reviewed their cover or plans in "
                  "years. {{my_value_1}}\n\nWould a no-obligation review be useful?"),
            email(3, "Email 2 — what a review covers", "What a review looks at",
                  "A review simply checks what you have against what you need today: family, loans, business and goals. You decide "
                  "what, if anything, to change.\n\n{{my_value_2}}"),
            email(7, "Email 3 — for business owners", "Cover for your team?",
                  "If you run a business, group cover for your staff is often simpler than people expect. I can explain the options "
                  "plainly.\n\n{{my_cta}}"),
            email(12, "Email 4 — no pressure", "Questions, answered in writing",
                  "If a call feels like too much, send me your questions and I'll answer them in writing. Product terms always "
                  "come from the insurer's documents, which I'll share.\n\n{{my_value_3}}"),
            email(18, "Email 5 — close politely", "Closing the loop",
                  "I'll stop here. If you'd like a review later, just reply.\n\nMore: {{my_website}}"),
        ]),
        "linkedin": linkedin(connect, [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}"),
            (5, "What we do", "{first_name}, at {{my_company}} we help people review their protection and plans. {{my_value_1}}\n\nWould a short review be useful?"),
            (6, "Offer a review", "Hi {first_name}, happy to do a 20-minute no-obligation review whenever suits you. No pressure at all."),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. If you'd like to talk later, message me any time. All the best."),
        ], [
            ("business_owner", r"\b(founder|owner|proprietor|director|md|ceo|partner)\b",
             "Something I often hear from business owners: they cover the business but forget to review cover for themselves and their team. Is that familiar?"),
            ("hr", r"\b(hr|human resources|people|benefits)\b",
             "Something I've been curious about: how do HR teams choose group health cover each year?"),
            ("doctor", r"\b(doctor|dr\.?|physician|surgeon|consultant)\b",
             "Something I've been curious about: do busy doctors find time to review their own financial plans?"),
            ("finance", r"\b(cfo|finance|accountant|ca\b|controller)\b",
             "Something I've been curious about: what do finance people look at first in a policy document?"),
        ], "Something I've been curious about: when did you last review your cover or plans?",
            "Hi {first_name}, I help people review their protection and plans in plain language. Would be glad to connect."),
        "call_script": script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You replied about a review — is this a good minute?"),
            ("Situation", "Could you tell me briefly about your family, any loans, and the cover you have today?"),
            ("Explain", "Based on that, here is what I'd look at. All terms come from the insurer's documents, which I'll send."),
            ("Next step", "Shall I prepare a written comparison so you can decide in your own time?"),
        ], [
            ("I already have a policy", "Good. A review only checks whether it still fits your needs. Nothing changes unless you want it to."),
            ("What returns will I get?", "I can't promise returns. I'll share the insurer's official illustration so you see the real terms."),
            ("I'll think about it", "Of course. I'll send everything in writing so you can decide in your own time."),
            ("Is my data safe?", "It's used only to prepare your review and deleted on request."),
            ("How did you get my number?", "From your enquiry or a public listing. If you'd rather not hear from us, I'll remove it now."),
        ]),
        "snippets": [
            snippet("Review summary", "email", "{{greeting}}\n\nAs discussed, here is a summary of your review. Product terms are in the attached insurer documents.\n\n{{sign_off}}", "Your review summary"),
            snippet("Documents needed", "email", "{{greeting}}\n\nTo prepare the comparison, could you share your current policy details?\n\n{{sign_off}}", "Details for your review"),
            snippet("Renewal reminder", "email", "{{greeting}}\n\nA reminder that your renewal is coming up. Would you like to review options before then?\n\n{{sign_off}}", "Renewal coming up"),
            snippet("Group cover info", "email", "{{greeting}}\n\nHere is a plain summary of group cover options for your team. Happy to explain on a call.\n\n{{sign_off}}", "Group cover options"),
            snippet("WhatsApp: review time", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. When suits you for a 20-minute review call?"),
            snippet("WhatsApp: documents", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. You can send your current policy details here when convenient."),
        ],
        "flows": None,
    }


def healthcare() -> dict:
    connect = ("Hi {first_name}, I work with clinics and healthcare teams on how patients find and book care. "
               "Always glad to connect with people in healthcare.")
    return {
        "id": "healthcare", "name": "Healthcare clinics",
        "description": "Clinics, diagnostic centres and health services reaching companies (staff health), referring doctors or patients who enquired.",
        "lead_term": "Patients",
        "stage_labels": {"engaged": "Enquiry", "interested": "Interested", "call_done": "Call done", "trial": "Appointment booked",
                         "customer": "Patient / partner"},
        "icp": {"text": "Companies that want health check-ups or wellness services for staff, doctors who refer patients, and people "
                        "who enquired about our services in our area.",
                "target_titles": ["HR Head", "Admin Head", "Founder", "Doctor", "Wellness Manager", "Facilities Manager"],
                "company_size": [10, 5000], "signals": ["Enquiry from our area", "Company with a large local team", "Annual check-up season"]},
        "scoring": {"fit_size_min": 10, "open_roles_full": 50},
        "sequence": sequence("Clinic outreach (5 emails)", [
            email(0, "Email 1 — introduction", "{{my_company}} — health services near you",
                  "I'm writing from {{my_company}}. {{my_tagline}}\n\nWe offer: {{my_value_1}}\n\nWould details of our services and "
                  "timings be useful?"),
            email(3, "Email 2 — for teams", "Health check-ups for your team",
                  "Some companies arrange check-ups for their staff with us, on site or at the clinic. If that interests you, I can "
                  "share the options and how booking works.\n\n{{my_value_2}}"),
            email(7, "Email 3 — easy booking", "Booking made simple",
                  "Appointments can be booked at a time that suits. {{my_cta}}\n\nWe'll confirm every booking in writing."),
            email(12, "Email 4 — questions", "Any questions?",
                  "If you have questions about services, timings or reports, reply and our team will answer. For medical advice, "
                  "please speak to a doctor directly.\n\n{{my_value_3}}"),
            email(18, "Email 5 — close politely", "Closing the loop",
                  "I'll stop here. If we can help later, just reply.\n\nMore: {{my_website}}"),
        ]),
        "linkedin": linkedin(connect, [
            (2, "Thanks and a question", "Thanks for connecting, {first_name}.\n\n{hook}"),
            (5, "What we do", "{first_name}, at {{my_company}} we offer {{my_value_1}}\n\nWould details be useful for you or your team?"),
            (6, "Offer details", "Hi {first_name}, happy to share how companies arrange check-ups with us, if useful. No pressure."),
            (8, "Close respectfully", "Hi {first_name}, I'll leave it here. If we can help later, feel free to message me. All the best."),
        ], [
            ("hr", r"\b(hr|human resources|people|wellness|benefits)\b",
             "Something I've been curious about: how do HR teams arrange yearly health check-ups for staff?"),
            ("admin", r"\b(admin|facilit|office manager|operations)\b",
             "Something I've been curious about: what makes on-site health camps easy or hard to organise?"),
            ("doctor", r"\b(doctor|dr\.?|physician|consultant|surgeon|gp)\b",
             "Something I've been curious about: what do doctors look for when referring patients for tests?"),
            ("founder", r"\b(founder|ceo|owner|director|md)\b",
             "Something I've been curious about: do growing companies plan staff health benefits early or late?"),
        ], "Something I've been curious about: what makes booking healthcare easy for busy people?",
            "Hi {first_name}, I help healthcare teams make booking care simpler. Would be glad to connect."),
        "call_script": script([
            ("Opener", "Hi {{first_name}}, this is {{rep}} from {{my_company}}. You enquired about our services — is this a good minute?"),
            ("Need", "What are you looking for: a check-up, a specific test, or services for your team?"),
            ("Explain", "We offer {{my_value_1}}. I'll send timings and details in writing. I can't give medical advice on this call."),
            ("Next step", "Shall I book a slot for you? Which day and time suit?"),
        ], [
            ("What does it cost?", "I'll send the current price list in writing so you have the exact figures."),
            ("Can you advise me on a condition?", "That needs a doctor. I can book you a consultation."),
            ("Is my data private?", "Your details are kept confidential and used only for your care and bookings."),
            ("We already have a provider", "That's fine. I'll share our details in case they're useful for comparison."),
            ("How did you get my number?", "From your enquiry or a public listing. If you'd rather not hear from us, I'll remove it now."),
        ]),
        "snippets": [
            snippet("Services and timings", "email", "{{greeting}}\n\nHere are our services and timings. {{my_value_1}}\n\n{{sign_off}}", "Services and timings"),
            snippet("Booking confirmation", "email", "{{greeting}}\n\nThis confirms your appointment. Please reply if you need to change it.\n\n{{sign_off}}", "Your appointment"),
            snippet("Corporate check-up options", "email", "{{greeting}}\n\nHere are the check-up options for your team and how booking works.\n\n{{sign_off}}", "Check-ups for your team"),
            snippet("Report ready", "email", "{{greeting}}\n\nYour report is ready. Please collect it or ask us how to receive it securely.\n\n{{sign_off}}", "Your report"),
            snippet("WhatsApp: booking", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. Which day and time suit you for the appointment?"),
            snippet("WhatsApp: reminder", "whatsapp", "{{greeting}} {{sender}} from {{my_company}}. A reminder about your appointment. Reply here to change it."),
        ],
        "flows": None,
    }
