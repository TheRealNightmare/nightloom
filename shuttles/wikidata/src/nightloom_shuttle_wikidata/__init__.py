"""Built-in Shuttle: organizations' websites, country, industry and corporate family (Wikidata).

See :mod:`nightloom_shuttle_wikidata.shuttle` for what it finds,
:mod:`nightloom_shuttle_wikidata.queries` for the SPARQL it sends and
:mod:`nightloom_shuttle_wikidata.client` for how it talks to Wikidata.
"""

from nightloom_shuttle_wikidata.shuttle import WikidataShuttle

__all__ = ["WikidataShuttle"]
