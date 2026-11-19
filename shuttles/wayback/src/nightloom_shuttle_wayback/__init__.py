"""Built-in Shuttle: a domain's archived URLs from the Internet Archive's Wayback Machine.

See :mod:`nightloom_shuttle_wayback.shuttle` for what it finds,
:mod:`nightloom_shuttle_wayback.client` for how it talks to the CDX server and
:mod:`nightloom_shuttle_wayback.filtering` for which archived URLs it keeps.
"""

from nightloom_shuttle_wayback.shuttle import WaybackShuttle

__all__ = ["WaybackShuttle"]
