"""Built-in Shuttles that visit a site's public pages, like a browser (``mode=visit``).

They run where a case's owners allow website visits, or on domains the case
has verified; the API and the worker check that before a run.
"""

from nightloom_shuttle_web.favicon import FaviconShuttle
from nightloom_shuttle_web.site_files import SiteFilesShuttle
from nightloom_shuttle_web.tech import WebTechShuttle

__all__ = ["FaviconShuttle", "SiteFilesShuttle", "WebTechShuttle"]
