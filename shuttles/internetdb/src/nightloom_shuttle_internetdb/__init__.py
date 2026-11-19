"""Built-in Shuttle: open ports and exposures of an IPv4 address (Shodan InternetDB).

See :mod:`nightloom_shuttle_internetdb.shuttle` for what it finds,
:mod:`nightloom_shuttle_internetdb.client` for how it talks to InternetDB and
:mod:`nightloom_shuttle_internetdb.models` for the record it reads.
"""

from nightloom_shuttle_internetdb.shuttle import InternetDBShuttle

__all__ = ["InternetDBShuttle"]
