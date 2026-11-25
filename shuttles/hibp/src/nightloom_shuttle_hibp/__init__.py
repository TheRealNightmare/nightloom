"""Built-in Shuttles: the breaches of a site and of an email address (Have I Been Pwned).

See :mod:`nightloom_shuttle_hibp.shuttles` for what they find,
:mod:`nightloom_shuttle_hibp.client` for how they talk to HIBP,
:mod:`nightloom_shuttle_hibp.breaches` for what they read from a breach and
:mod:`nightloom_shuttle_hibp.config` for the operator's API key.
"""

from nightloom_shuttle_hibp.shuttles import HibpAccountShuttle, HibpBreachesShuttle

__all__ = ["HibpAccountShuttle", "HibpBreachesShuttle"]
