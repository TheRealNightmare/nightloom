"""Built-in Shuttle: registration data (RDAP) for domains, IP networks and AS numbers.

See :mod:`nightloom_shuttle_rdap.shuttle` for what it finds,
:mod:`nightloom_shuttle_rdap.bootstrap` for how it finds the right server,
:mod:`nightloom_shuttle_rdap.client` for how it talks to it,
:mod:`nightloom_shuttle_rdap.objects` for the fields it reads and
:mod:`nightloom_shuttle_rdap.privacy` for the contact data it refuses to keep.
"""

from nightloom_shuttle_rdap.shuttle import RdapShuttle

__all__ = ["RdapShuttle"]
