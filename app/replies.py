"""Rule-based reply classifier. The AI writer only reads replies these rules can't place; labels stay the same.

Order matters: things that must never be mistaken for a "yes" (bounces, auto-replies, stop words, refusals)
are checked first. A "yes" only counts when it is not negated ("I'm not sure", "we aren't interested").
Quoted history (our own email below the reply) is removed first, so our footer's "unsubscribe" link or
"interested?" line can never decide the label.
"""
import re

_QUOTE_STARTS = re.compile(
    r"^\s*(on .{3,120} wrote:|-{2,}\s*original message\s*-{2,}|_{5,}|(from|von|de|da|प्रेषक):\s.+|sent from my \w+|"
    r"get outlook for \w+|forwarded message|begin forwarded message|--|"
    r"((thanks|thank you|warm|best|kind|with) )?(&|and )?regards,?|thanks (&|and) regards,?|"
    r"(disclaimer|confidentiality notice)\b.*|this e-?mail and any (files|attachments)\b.*)\s*$",
    re.IGNORECASE)
_NEGATION_WORDS = r"(not|no|never|nahi|nahin|dont|don't|\w+n't)"

_RULES = (
    ("bounce", r"undeliverable|delivery (status notification|has failed|failure)|mail delivery (failed|subsystem)|"
               r"user unknown|(mailbox|address) (not found|unavailable|rejected|full|does not exist)|no such user|"
               r"recipient address rejected|(unable|not able|wasn'?t able|could ?not|couldn'?t) to deliver (your|the|this) message|"
               r"could not be delivered to|message not delivered|failed permanently|permanent (error|failure)|no mailbox here|\b550[ -]5\.\d{1,2}\.\d{1,2}\b|\bstatus: 5\.\d\.\d\b|returned mail"),
    ("auto_reply", r"out of (the )?office|automatic reply|auto[- ]?reply|autoreply|on (annual |maternity |paternity |medical )?leave|"
                   r"away from (my )?(desk|email|office)|i am (currently )?(away|travelling|traveling|out of station)|"
                   r"\bon (a )?(vacation|holiday)\b|chutti par|limited access to (my )?e-?mail|"
                   r"this is an automated|automated (response|reply|message)|ticket (has been |was )?(created|raised|logged)|"
                   r"\[ticket #|\(#\d+\) has been received|team will get back to you within|"
                   r"delivery (incomplete|delayed)|temporary problem delivering"),
    ("unsubscribe", r"^\W*(please\W+)?(stop|opt[- ]?out|remove|unsubscribe|cancel)(\W+(it|please|this|now))?\W*$|"
                    r"\bunsubscribe\b|opt[- ]?out|remove me|please remove|take me off|remove (my|this) (email|number|id|address)|"
                    r"\bstop (sending|emailing|writing|mailing|messaging|texting|contacting|calling|spamming)\b|"
                    r"\bplease stop\b|\bstop (it|this)\b|"
                    r"\b(do not|don'?t|dont) (send|email|contact|call|message|text|write|mail|whatsapp)\b|"
                    r"\bnever (contact|email|message|call|text|whatsapp|write to) me\b|\bremove it\b|\bna bhej\w*|"
                    r"\b(delete|erase|remove|wipe) (all )?(my|our) (data|details|information|info|number|email|contact)\b|"
                    r"\bmat (bhej|karo|call|message)\w*|band kar\w*|remove kar\w*|hata (do|dijiye|dein)|"
                    r"\bmujhe mat\b|message mat\b|call mat\b|mail mat\b"),
    ("not_interested", r"\b(not|aren'?t|isn'?t|am not|are not|is not|were not|weren'?t|wasn'?t|we'?re not|i'?m not) "
                       r"(at all )?interested\b|"
                       r"^\W*(no|nope|nah|na|nahi|nahin)\W*$|\byeah,? no\b|\byeah right\b|\bas if we need\b|"
                       r"(don'?t|do not) think we need|(?:^|[.!?]\s+)(?:no|nope|nahi)[.!]*\s*$|\bwe(?:'re| are) (?:good|sorted|all set)\b(?! to go)|^\W*no[,.! ]+(thanks?|thank you|sorry)\b|"
                       r"no,? thanks?|no thank you|not required|no need|not looking|not relevant|not for us|"
                       r"\b(don'?t|do not|dont) (need|want|require)\b|"
                       r"nahi[n]? chahi(ye|e)|zaroorat nahi[n]?|jarurat nahi[n]?|interest(ed)? nahi[n]?|"
                       r"koi (zaroorat|jarurat|interest) nahi[n]?|nahi[n]? (chahiye|karna)"),
    ("wrong_person", r"not the right person|wrong person|(don'?t|do not|doesn'?t) handle (this|that|hiring|recruitment)|"
                     r"\bcontact our\b|\bspeak to our\b|\btalk to our\b|someone else (handles|looks after)|"
                     r"\breach out to (our|my|the) \w+|\bplease (contact|email|write to) (our|my|the) \w+|"
                     r"\b(i have|i've) (left|moved on from)\b|no longer (with|at|work)|\bi left (the|this) (company|organi[sz]ation)\b|"
                     r"not my department|(don'?t|do not) (do|handle|look after) (hiring|recruitment|recruiting)|"
                     r"nahi[n]? dekh(ta|ti|te)\b|\bji se baat kar"),
    ("interested", r"\b(yes|yeah|yep|yup|sure|interested|sounds (good|great|interesting)|go ahead|"
                   r"send (it|me the details|me details|me more|me the sample|the sample|over)|"
                   r"tell me more|call me|let'?s (talk|connect|discuss)|happy to (talk|connect|chat)|"
                   r"haan|ha ji|haan ji|bhej do|bhejiye|bhej dijiye|ok send|sample|send (a|one) sample|"
                   r"reach out to me|contact me|book (a|the) (call|demo|meeting)|send the details|do the needful|"
                   r"would like to (try|see|know more)|(pls|please|kindly) call|connect on a call|can we connect)\b|"
                   r"^\W*(please|pls|kindly) share\W*$"),
    ("not_now", r"\bnot now\b|not at the moment|not right now|maybe later|maybe after|next (week|month|quarter|year)|"
                r"\bbusy\b|\blater\b|baad mein|abhi nahi[n]?|after (diwali|holi|the festival|new year)|"
                r"not hiring (currently|right now|at the moment|now)|hiring freeze|next financial year|festive season|"
                r"follow up (in|after|next)"),
    ("question", r"(\?|\b(how much|cost|price|pricing|what is|how does|how do|share (your|the) (price|pricing|details))\b)"),
)
# "No problem, send it" is a yes: these phrases contain "no" but are not refusals.
_NOT_A_NO = re.compile(r"\bno (problem|problems|issue|issues|worries|doubt)\b")
_NEGATION_BEFORE = re.compile(r"\b" + _NEGATION_WORDS + r"\W+(\w+\W+){0,2}$")
LABELS = tuple(label for label, _ in _RULES)


def strip_quoted(text: str) -> str:
    """Keep only what the person wrote: drop '>' quoted lines and everything from 'On … wrote:' down."""
    kept = []
    for line in (text or "").splitlines():
        if _QUOTE_STARTS.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _is_negated(text: str, start: int) -> bool:
    return bool(_NEGATION_BEFORE.search(text[max(0, start - 28):start]))


def classify_reply(text: str) -> str:
    lowered = strip_quoted(text or "").lower().strip()
    if not lowered:
        return "other"                             # only quoted history (our own email) or a signature: nothing to read
    lowered = _NOT_A_NO.sub("fine", lowered)
    for label, pattern in _RULES:
        for match in re.finditer(pattern, lowered, re.MULTILINE):
            if label == "interested" and _is_negated(lowered, match.start()):
                continue
            return label
    return "other"
