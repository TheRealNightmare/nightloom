"""Built-in Shuttles: public DNS records, reverse DNS and registered lookalike domains,
through public resolvers only.

See :mod:`nightloom_shuttle_dns.shuttle` for what the DNS Shuttle finds,
:mod:`nightloom_shuttle_dns.reverse` for the reverse DNS (PTR) Shuttle,
:mod:`nightloom_shuttle_dns.typosquats` for the typosquat Shuttle (and
:mod:`nightloom_shuttle_dns.permutations` for the lookalikes it tries),
:mod:`nightloom_shuttle_dns.config` for the resolver policy both follow and
:mod:`nightloom_shuttle_dns.names` for the names they refuse.
"""

from nightloom_shuttle_dns.reverse import ReverseDnsShuttle
from nightloom_shuttle_dns.shuttle import DnsShuttle
from nightloom_shuttle_dns.typosquats import TyposquatShuttle

__all__ = ["DnsShuttle", "ReverseDnsShuttle", "TyposquatShuttle"]
