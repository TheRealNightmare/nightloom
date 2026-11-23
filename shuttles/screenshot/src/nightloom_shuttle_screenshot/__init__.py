"""Built-in Shuttle: a page's screenshot, drawn in a sandboxed browser, and its perceptual hash.

See :mod:`nightloom_shuttle_screenshot.shuttle` for what it finds,
:mod:`nightloom_shuttle_screenshot.broker` for how the page's requests are
answered, and :mod:`nightloom_shuttle_screenshot.picture` for the hash.
"""

from nightloom_shuttle_screenshot.shuttle import ScreenshotShuttle

__all__ = ["ScreenshotShuttle"]
