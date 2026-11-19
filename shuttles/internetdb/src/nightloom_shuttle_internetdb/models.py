"""The record InternetDB answers with, as a Pydantic model.

``GET https://internetdb.shodan.io/<ip>`` answers::

    {"ip": "192.0.2.10", "ports": [22, 443], "hostnames": ["www.example.org"],
     "cpes": ["cpe:/a:nginx:nginx"], "vulns": ["CVE-2021-23017"], "tags": ["cdn"]}

The record as a whole must have this shape (a list that is not a list fails
the job); the items of each list are checked one by one by the Shuttle, so a
malformed item is counted and skipped instead. Unknown fields are ignored.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    """What InternetDB knows about one IPv4 address."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    ip: str
    ports: list[Any] = Field(default_factory=list)
    hostnames: list[Any] = Field(default_factory=list)
    cpes: list[Any] = Field(default_factory=list)
    vulns: list[Any] = Field(default_factory=list)
    tags: list[Any] = Field(default_factory=list)


__all__ = ["Record"]
