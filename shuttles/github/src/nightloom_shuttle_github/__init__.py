"""Built-in Shuttles for public code on GitHub.

See :mod:`nightloom_shuttle_github.repositories` for an organization's
repositories, :mod:`nightloom_shuttle_github.client` for how they talk to
GitHub, :mod:`nightloom_shuttle_github.accounts` for what they read and
:mod:`nightloom_shuttle_github.config` for the operator's optional token.
"""

from nightloom_shuttle_github.repositories import GithubRepositoriesShuttle

__all__ = ["GithubRepositoriesShuttle"]
