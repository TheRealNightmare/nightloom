"""Built-in Shuttle: a domain's subdomains and certificates from Certificate Transparency (crt.sh).

See :mod:`nightloom_shuttle_crtsh.shuttle` for what it finds,
:mod:`nightloom_shuttle_crtsh.client` for how it talks to crt.sh,
:mod:`nightloom_shuttle_crtsh.names` for which names it keeps and
:mod:`nightloom_shuttle_crtsh.certificates` for the certificates it reads.
"""

from nightloom_shuttle_crtsh.shuttle import CrtShShuttle

__all__ = ["CrtShShuttle"]
