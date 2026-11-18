"""Built-in Shuttle: a domain's public DNS records, through public resolvers only.

See :mod:`nightloom_shuttle_dns.shuttle` for what it finds,
:mod:`nightloom_shuttle_dns.config` for the resolver policy and
:mod:`nightloom_shuttle_dns.names` for the names it refuses.
"""

from nightloom_shuttle_dns.shuttle import DnsShuttle

__all__ = ["DnsShuttle"]
