"""Built-in Shuttles for public code on GitHub.

- :mod:`nightloom_shuttle_github.repositories`: an organization's repositories;
- :mod:`nightloom_shuttle_github.commits`: a repository's commit authors;
- :mod:`nightloom_shuttle_github.exposures` and :mod:`nightloom_shuttle_github.secrets`:
  likely secrets in its commits;
- :mod:`nightloom_shuttle_github.profiles`: a username's public profile;
- :mod:`nightloom_shuttle_github.client`: how they talk to GitHub;
- :mod:`nightloom_shuttle_github.accounts`: what they read;
- :mod:`nightloom_shuttle_github.config`: the operator's optional token.
"""

from nightloom_shuttle_github.commits import GithubCommitsShuttle
from nightloom_shuttle_github.exposures import GithubSecretsShuttle
from nightloom_shuttle_github.profiles import GithubProfileShuttle
from nightloom_shuttle_github.repositories import GithubRepositoriesShuttle

__all__ = [
    "GithubCommitsShuttle",
    "GithubProfileShuttle",
    "GithubRepositoriesShuttle",
    "GithubSecretsShuttle",
]
