"""Source-layer unit fixtures deliberately replace Pi with hand-written protocol replies.

They have no native transcript and do not test T1b. Keep that isolation explicit;
production remains strict, and test_task_planning_sources covers the real combined path.
"""


def source_protocol_only(session):
    session.task_planner = None
    session.provider.task_planner = None
    # Hand-written source/structure replies have no unified-memory receipt.
    # Disable that projection only in these explicitly isolated unit fixtures;
    # combined production loading stays covered by test_task_planning_sources.
    session.provider.inner._memory_catalog = None
    return session
