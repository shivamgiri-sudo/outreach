"""Rule-based reply classifier. Swap for the AI writer later; labels stay the same.

Order matters: things that must never be mistaken for a "yes" (bounces, auto-replies, stop words, refusals)
are checked first. A "yes" only counts when it is not negated ("I'm not sure" is not a yes).
"""
import re

_RULES = (
    ("bounce", r"undeliverable|delivery (status notification|has failed|failure)|mail delivery (failed|subsystem)|"
               r"user unknown|(mailbox|address) (not found|unavailable|rejected|full)|no such user|\b550 "),
    ("auto_reply", r"out of (the )?office|automatic reply|auto[- ]?reply|autoreply|on (annual |maternity |medical )?leave|"
                   r"away from (my )?(desk|email|office)|i am (currently )?(away|travelling|traveling)"),
    ("unsubscribe", r"^\W*(stop|opt[- ]?out|remove|unsubscribe|cancel)\W*$|\bunsubscribe\b|opt[- ]?out|"
                    r"remove me|please remove|take me off|"
                    r"\bstop (sending|emailing|writing|mailing|messaging|texting|contacting|calling)\b|"
                    r"\b(do not|don'?t|dont) (send|email|contact|call|message|text|write)\b|"
                    r"\bmat (bhej|karo|call|message)\w*|band kar"),
    ("not_interested", r"not interested|no,? thanks?|no thank you|not required|no need|not looking|"
                       r"\b(don'?t|do not|dont) (need|want|require)\b|nahi chahi(ye|e)|zaroorat nahi|jarurat nahi"),
    ("wrong_person", r"(not the right person|wrong person|don'?t handle|do not handle|contact our|reach out to|speak to our|someone else)"),
    ("not_now", r"(not now|not at the moment|maybe later|maybe after|next (month|quarter|year)|busy|later|baad mein|abhi nahi)"),
    ("interested", r"\b(yes|yeah|yep|yup|sure|interested|sounds (good|great|interesting)|go ahead|send (it|me the|the)|"
                   r"tell me more|call me|haan|ha ji|bhej do|bhejiye|ok send|sample|send (a|one) sample)\b"),
    ("question", r"(\?|\b(how much|cost|price|pricing|what is|how does|how do|share (your|the) (price|pricing|details))\b)"),
)
_NEGATION_BEFORE = re.compile(r"\b(not|no|never|n't|nahi|dont|don't)\W+(\w+\W+){0,2}$")
LABELS = tuple(label for label, _ in _RULES)


def _is_negated(text: str, start: int) -> bool:
    return bool(_NEGATION_BEFORE.search(text[max(0, start - 24):start]))


def classify_reply(text: str) -> str:
    lowered = (text or "").lower().strip()
    for label, pattern in _RULES:
        for match in re.finditer(pattern, lowered):
            if label == "interested" and _is_negated(lowered, match.start()):
                continue
            return label
    return "other"
