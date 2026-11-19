"""Built-in Shuttle: routing data for IP addresses, netblocks and AS numbers (RIPEstat).

See :mod:`nightloom_shuttle_ripestat.shuttle` for what it finds,
:mod:`nightloom_shuttle_ripestat.client` for how it talks to RIPEstat,
:mod:`nightloom_shuttle_ripestat.models` for the fields it reads and
:mod:`nightloom_shuttle_ripestat.holders` for how an AS holder is split.
"""

from nightloom_shuttle_ripestat.shuttle import RipeStatShuttle

__all__ = ["RipeStatShuttle"]
