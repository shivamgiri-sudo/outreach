"""How many cold emails you can safely send, and how long a list takes."""
import math

WORKING_DAYS_PER_MONTH = 22
SAFE_PER_MAILBOX_PER_DAY = 50


def capacity_plan(mailboxes: int, per_mailbox_per_day: int, target_leads: int) -> dict:
    daily = max(0, mailboxes) * max(0, per_mailbox_per_day)
    warning = None
    if per_mailbox_per_day > SAFE_PER_MAILBOX_PER_DAY:
        warning = f"Above {SAFE_PER_MAILBOX_PER_DAY} a day per mailbox risks the spam folder. Add mailboxes instead."
    return {
        "daily": daily,
        "monthly": daily * WORKING_DAYS_PER_MONTH,
        "days_needed": math.ceil(target_leads / daily) if daily else None,
        "warning": warning,
    }
