"""Personal graph layouts: where each user pinned the entities of a case.

A layout is a UI preference of one user in one case (a map of entity key to
``[x, y]``), so it is readable and writable with the ``read`` capability, in
cases of any status, and is not audited. See the "Personal layouts" section of
docs/ARCHITECTURE.md.
"""
