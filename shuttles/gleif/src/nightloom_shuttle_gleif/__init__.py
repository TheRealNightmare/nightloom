"""Built-in Shuttle: an organization's Legal Entity Identifier record and parents (GLEIF).

See :mod:`nightloom_shuttle_gleif.shuttle` for what it finds,
:mod:`nightloom_shuttle_gleif.client` for how it talks to GLEIF and
:mod:`nightloom_shuttle_gleif.records` for what it reads from a record.
"""

from nightloom_shuttle_gleif.shuttle import GleifShuttle

__all__ = ["GleifShuttle"]
