"""Inbound processing entry points used by the Integrations hub.

# STUB: replaced by the core branch. The core developer's app/inbound.py implements the real matching
# (reply → lead, STOP handling, bounce suppression). Keep only these signatures here.
"""


def process_inbound_message(store, msg):  # STUB: replaced by the core branch
    return {"matched": False, "stub": True}


def process_delivery_event(store, event):  # STUB: replaced by the core branch
    return {"stub": True}
