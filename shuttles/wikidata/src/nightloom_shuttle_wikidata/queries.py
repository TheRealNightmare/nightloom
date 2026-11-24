"""The SPARQL queries the Wikidata Shuttle sends, built from fixed templates.

Only four kinds of value are ever put into a query, each checked or escaped
first: an item ID (``Q`` and digits, :func:`item_id`), an LEI (20 letters
and digits), a host name (normalized by ``nightloom_core``: letters, digits,
hyphens and dots) inside IRIs, and a name as an escaped string literal
(:func:`literal`). Every query that can return an item to keep excludes
items that are an instance of *human* (``wd:Q5``): Wikidata describes
people too, and their official websites and employers are person data.

Properties used: P31 instance of, P279 subclass of, P856 official website,
P17 country, P297 ISO 3166-1 alpha-2 code, P1278 Legal Entity Identifier,
P452 industry, P749 parent organization, P355 has subsidiary. ``Q43229``
is *organization*.
"""

import re

ENTITY_PREFIX = "http://www.wikidata.org/entity/"
ITEM_ID = re.compile(r"^Q[1-9][0-9]{0,11}$")
LEI = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

ORGANIZATION = "wd:Q43229"
HUMAN = "wd:Q5"
IS_ORGANIZATION = f"wdt:P31/wdt:P279* {ORGANIZATION}"
NOT_HUMAN = "FILTER NOT EXISTS {{ {item} wdt:P31 " + HUMAN + " }}"

# Rows a query may return; the Shuttle caps what it keeps further.
MATCH_LIMIT = 10
DETAIL_LIMIT = 500
RELATIVE_LIMIT = 300
WEBSITE_LIMIT = 20


def item_id(value: str) -> str:
    """``value`` if it is a Wikidata item ID (``Q95``); else a ValueError."""
    if not ITEM_ID.fullmatch(value):
        raise ValueError("not a Wikidata item ID")
    return value


def literal(text: str) -> str:
    """``text`` as an English SPARQL string literal, escaped."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"@en'


def _not_human(variable: str) -> str:
    return NOT_HUMAN.format(item=variable)


def by_label(name: str) -> str:
    """Organizations whose English label or alias is exactly ``name``."""
    return (
        "SELECT DISTINCT ?item WHERE { "
        f"{{ ?item rdfs:label {literal(name)} }} UNION {{ ?item skos:altLabel {literal(name)} }} "
        f"?item {IS_ORGANIZATION} . {_not_human('?item')} "
        f"}} LIMIT {MATCH_LIMIT}"
    )


def by_lei(lei: str) -> str:
    """Items with the Legal Entity Identifier ``lei``."""
    if not LEI.fullmatch(lei):
        raise ValueError("not an LEI")
    return (
        f'SELECT DISTINCT ?item WHERE {{ ?item wdt:P1278 "{lei}" . {_not_human("?item")} }} '
        f"LIMIT {MATCH_LIMIT}"
    )


def details(qid: str) -> str:
    """An item's English label, websites, country code, LEI, industries and kind."""
    item = f"wd:{item_id(qid)}"
    return (
        "SELECT ?label ?site ?country ?lei ?industry ?human ?organization WHERE { "
        f'OPTIONAL {{ {item} rdfs:label ?label . FILTER(LANG(?label) = "en") }} '
        f"OPTIONAL {{ {item} wdt:P856 ?site }} "
        f"OPTIONAL {{ {item} wdt:P17/wdt:P297 ?country }} "
        f"OPTIONAL {{ {item} wdt:P1278 ?lei }} "
        f"OPTIONAL {{ {item} wdt:P452 ?sector . ?sector rdfs:label ?industry . "
        'FILTER(LANG(?industry) = "en") } '
        f"BIND(EXISTS {{ {item} wdt:P31 {HUMAN} }} AS ?human) "
        f"BIND(EXISTS {{ {item} {IS_ORGANIZATION} }} AS ?organization) "
        f"}} LIMIT {DETAIL_LIMIT}"
    )


def relatives(qid: str) -> str:
    """An item's parent organizations and subsidiaries (either side may state it)."""
    item = f"wd:{item_id(qid)}"
    return (
        "SELECT DISTINCT ?relation ?other ?label WHERE { "
        f'{{ {item} wdt:P749 ?other . BIND("parent" AS ?relation) }} '
        f'UNION {{ ?other wdt:P355 {item} . BIND("parent" AS ?relation) }} '
        f'UNION {{ {item} wdt:P355 ?other . BIND("subsidiary" AS ?relation) }} '
        f'UNION {{ ?other wdt:P749 {item} . BIND("subsidiary" AS ?relation) }} '
        '?other rdfs:label ?label . FILTER(LANG(?label) = "en") '
        f"{_not_human('?other')} "
        f"}} LIMIT {RELATIVE_LIMIT}"
    )


def website_variants(host: str) -> list[str]:
    """The official-website URLs Wikidata may hold for ``host``: http and https, with and
    without a trailing slash, for ``host`` and (for a bare domain) ``www.<host>``."""
    if not HOST.fullmatch(host):
        raise ValueError("not a host name")
    hosts = [host] if host.startswith("www.") else [host, f"www.{host}"]
    return [
        f"{scheme}://{name}{slash}"
        for name in hosts
        for scheme in ("https", "http")
        for slash in ("/", "")
    ]


def by_website(host: str) -> str:
    """Organizations whose official website is ``host``'s home page."""
    sites = " ".join(f"<{url}>" for url in website_variants(host))
    return (
        "SELECT DISTINCT ?item ?label WHERE { "
        f"VALUES ?site {{ {sites} }} ?item wdt:P856 ?site . "
        '?item rdfs:label ?label . FILTER(LANG(?label) = "en") '
        f"?item {IS_ORGANIZATION} . {_not_human('?item')} "
        f"}} LIMIT {WEBSITE_LIMIT}"
    )


def qid_of(uri: str) -> str | None:
    """The item ID of a Wikidata entity URI, or ``None`` for anything else."""
    if not uri.startswith(ENTITY_PREFIX):
        return None
    qid = uri.removeprefix(ENTITY_PREFIX)
    return qid if ITEM_ID.fullmatch(qid) else None


__all__ = [
    "DETAIL_LIMIT",
    "MATCH_LIMIT",
    "RELATIVE_LIMIT",
    "WEBSITE_LIMIT",
    "by_label",
    "by_lei",
    "by_website",
    "details",
    "item_id",
    "literal",
    "qid_of",
    "relatives",
    "website_variants",
]
