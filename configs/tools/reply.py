# The parent half of the escalation channel. Mount it wherever `escalate_tool` is
# mounted: `escalate_tool` parks a child until its parent answers, and a parent with no
# `reply_tool` has no move to make. That is not a hang — the child gives up after
# ESCALATION_TIMEOUT_S and carries on — but it is five minutes of a child waiting for a
# reply nobody can send, which reads like a stall rather than a missing capability.
reply_tool = dict(
    enable_evolving = False,
)
