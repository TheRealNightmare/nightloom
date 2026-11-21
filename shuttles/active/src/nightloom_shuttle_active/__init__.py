"""Built-in active Shuttles: they connect to the target itself.

Both run only on targets a case has verified it controls (see the API's
ownership verification); the API and the worker check that before a run.
"""

from nightloom_shuttle_active.headers import HttpHeadersShuttle
from nightloom_shuttle_active.ports import PortCheckShuttle

__all__ = ["HttpHeadersShuttle", "PortCheckShuttle"]
