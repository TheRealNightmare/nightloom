"""Entity types: one Pydantic model per type, with a canonical ``value`` and typed properties.

Every model normalizes ``value`` when it is created (see
:mod:`nightloom_core.normalize`), so an entity object always holds the
canonical form and exposes its canonical :attr:`EntityBase.key`. Build one
from untrusted input with :func:`build_entity`, which also accepts a default
region for phone numbers.

Each type declares a :class:`Sensitivity`. Person-related types can only be
added to cases that allow person entities (see ETHICS.md).
"""

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    field_validator,
)


class EntityType(StrEnum):
    """Every kind of entity. The value is the prefix of the entity's canonical key."""

    DOMAIN = "domain"
    IP_ADDRESS = "ip_address"
    NETBLOCK = "netblock"
    ASN = "asn"
    CERTIFICATE = "certificate"
    URL = "url"
    SERVICE = "service"
    TECHNOLOGY = "technology"
    ORGANIZATION = "organization"
    EMAIL = "email"
    USERNAME = "username"
    PERSON = "person"
    PHONE = "phone"
    SOCIAL_PROFILE = "social_profile"
    LOCATION = "location"
    CRYPTO_WALLET = "crypto_wallet"
    DOCUMENT = "document"
    IMAGE = "image"
    BREACH = "breach"
    FAVICON = "favicon"


class Sensitivity(StrEnum):
    """How close an entity type is to a person. ``person`` types are gated per case."""

    INFRASTRUCTURE = "infrastructure"
    ORGANIZATION = "organization"
    PERSON = "person"


# --- property building blocks ---------------------------------------------------------

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]


def _matching(pattern: str, *, upper: bool = False) -> AfterValidator:
    """Trim and case-map a string, then require it to match ``pattern`` in full.

    (``StringConstraints`` checks its pattern before case mapping, so it would
    refuse ``" bd "`` for a pattern of upper-case letters.)
    """
    compiled = re.compile(pattern)

    def check(value: str) -> str:
        value = value.strip()
        value = value.upper() if upper else value.lower()
        if not compiled.fullmatch(value):
            raise ValueError(f"must match {pattern}")
        return value

    return AfterValidator(check)


CountryCode = Annotated[str, Field(max_length=10), _matching(r"[A-Z]{2}", upper=True)]
Sha256Hex = Annotated[str, Field(max_length=100), _matching(r"[0-9a-f]{64}")]
# A file kept with the case (see nightloom_sdk.evidence): its SHA-256 and extension.
EvidenceName = Annotated[str, Field(max_length=100), _matching(r"[0-9a-f]{64}\.(png|jpg)")]
# A 64-bit perceptual hash (dHash), as 16 lowercase hexadecimal digits.
PerceptualHash = Annotated[str, Field(max_length=100), _matching(r"[0-9a-f]{16}")]
Md5Hex = Annotated[str, Field(max_length=100), _matching(r"[0-9a-f]{32}")]
# A Legal Entity Identifier (ISO 17442): 18 letters or digits and two check digits.
Lei = Annotated[str, Field(max_length=100), _matching(r"[A-Z0-9]{18}[0-9]{2}", upper=True)]
# Where an entity is legally registered: an ISO 3166-1 country, or a 3166-2 subdivision.
Jurisdiction = Annotated[
    str, Field(max_length=100), _matching(r"[A-Z]{2}(-[A-Z0-9]{1,3})?", upper=True)
]
# A status word from a registry, such as ACTIVE or LAPSED.
StatusCode = Annotated[str, Field(max_length=100), _matching(r"[A-Z][A-Z_]{0,39}", upper=True)]
MimeType = Annotated[
    str,
    Field(max_length=127),
    _matching(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*"),
]


class Properties(BaseModel):
    """Base class for an entity type's optional properties.

    Fields are flat scalars or lists of strings (or of integers, such as an
    address's open ports), so they can be stored as graph properties. Every
    field is optional.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    def stored(self) -> dict[str, Any]:
        """The set fields as JSON-compatible values (datetimes as ISO 8601 text)."""
        return self.model_dump(mode="json", exclude_none=True)


class NoProperties(Properties):
    """For types that carry no properties (yet)."""


# DNS facts stored on a Domain (written by the DNS Shuttle): at most this many
# records per list, each at most DNS_TEXT_MAX_LENGTH characters.
DNS_MAX_RECORDS = 50
DNS_TEXT_MAX_LENGTH = 1000
DnsText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DNS_TEXT_MAX_LENGTH)
]
DnsRecords = Annotated[list[DnsText], Field(max_length=DNS_MAX_RECORDS)]
# A domain name as text (at most 253 characters, plus a trailing dot).
NameText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Uint32 = Annotated[int, Field(ge=0, le=2**32 - 1)]


# Registration data (RDAP): status values and handles are short tokens.
RDAP_MAX_STATUSES = 30
RdapStatuses = Annotated[list[ShortText], Field(max_length=RDAP_MAX_STATUSES)]
Handle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
UrlText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


# Web server data on a domain: at most this many security header names.
SECURITY_HEADERS_MAX = 20
# From a site's own pages: at most this many security.txt contact kinds and analytics IDs.
SECURITY_TXT_MAX_KINDS = 5
ANALYTICS_IDS_MAX = 20


class DomainProperties(Properties):
    """What public DNS and the registration data say about the name. Every field is optional.

    ``dns_txt`` holds the TXT records (each joined from its strings);
    ``dns_spf`` and ``dns_dmarc`` repeat the SPF (``v=spf1``) and DMARC
    (``v=DMARC1``, from ``_dmarc.<name>``) records among them; ``dns_mx`` is
    ``"<preference> <exchange>"``; ``dns_caa`` is ``"<flags> <tag> <value>"``;
    the ``dns_soa_*`` fields are the zone's SOA record when the name is a
    zone apex.

    From RDAP (registrable domains only): ``registered``, ``expires`` and
    ``last_changed`` (the registration's events), ``status`` (EPP statuses
    such as ``client transfer prohibited``), ``dnssec_signed``, the
    ``registrar``'s name and ``registrar_iana_id``, and the ``rdap_server``
    that answered.

    From the HTTP headers and TLS Shuttle (active: it connects to the site
    itself, so only on verified domains): ``http_checked_at``, the status of
    ``https://<name>/`` (``https_status``, after same-host redirects),
    whether its certificate was valid (``https_valid``), whether
    ``http://<name>/`` redirects to HTTPS (``http_redirects_to_https``), the
    negotiated ``tls_version``, the ``Server`` and ``X-Powered-By`` headers
    (``server``, ``powered_by``), the ``Strict-Transport-Security`` value
    (``hsts``), and which of the common security headers were sent
    (``security_headers``) or not (``missing_security_headers``), by lower-case
    name.

    From the site files Shuttle (it visits the site's public files, like a
    browser): ``site_files_checked_at``, whether ``/robots.txt`` exists
    (``robots_txt``) and how many ``Disallow`` rules it has
    (``robots_disallow_count``), how many page addresses the sitemap listed
    (``sitemap_url_count``), whether a valid ``security.txt`` exists
    (``security_txt``), its ``Expires`` date (``security_txt_expires``) and
    the kinds of contact it gives (``security_txt_contact_kinds``: ``email``,
    ``url``, ``phone``; never the contacts themselves).

    From the web technologies Shuttle (it visits the home page, like a
    browser): ``web_checked_at``, the page's ``<title>`` (``web_title``), its
    ``generator`` meta tag (``web_generator``) and the web analytics and
    advertising IDs it embeds (``analytics_ids``: Google Analytics ``UA-`` and
    ``G-``, Tag Manager ``GTM-``, Ads ``AW-``, AdSense ``ca-pub-``), which
    often link sites run by the same owner.

    From the favicon Shuttle (it visits the site): ``favicon_checked_at``, when
    it last looked for the site's icon. The icon itself is a Favicon entity
    the domain ``HAS_FAVICON``.

    From the typosquat Shuttle (it asks public resolvers only):
    ``typosquats_checked_at``, how many lookalike names it tried
    (``typosquat_candidates``) and how many of them are registered
    (``typosquats_registered``). Each registered one is a Domain that is
    ``LOOKALIKE_OF`` this one.

    From the Wayback Machine (Internet Archive): ``wayback_first_capture`` and
    ``wayback_last_capture`` (the earliest and the latest capture of the name
    or a page under it that the archive reported) and ``wayback_url_count``
    (how many distinct page URLs under the name were found in the archive,
    after static assets and unusable URLs were left out).
    """

    dns_txt: DnsRecords | None = None
    dns_spf: DnsRecords | None = None
    dns_dmarc: DnsRecords | None = None
    dns_mx: DnsRecords | None = None
    dns_caa: DnsRecords | None = None
    dns_soa_primary_ns: NameText | None = None
    dns_soa_responsible: NameText | None = None
    dns_soa_serial: Uint32 | None = None
    dns_soa_refresh: Uint32 | None = None
    dns_soa_retry: Uint32 | None = None
    dns_soa_expire: Uint32 | None = None
    dns_soa_minimum: Uint32 | None = None
    registered: AwareDatetime | None = None
    expires: AwareDatetime | None = None
    last_changed: AwareDatetime | None = None
    status: RdapStatuses | None = None
    dnssec_signed: bool | None = None
    registrar: ShortText | None = None
    registrar_iana_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    rdap_server: UrlText | None = None
    wayback_first_capture: AwareDatetime | None = None
    wayback_last_capture: AwareDatetime | None = None
    wayback_url_count: int | None = Field(default=None, ge=0, le=10_000_000)
    http_checked_at: AwareDatetime | None = None
    https_status: int | None = Field(default=None, ge=100, le=599)
    https_valid: bool | None = None
    http_redirects_to_https: bool | None = None
    tls_version: ShortText | None = None
    server: ShortText | None = None
    powered_by: ShortText | None = None
    hsts: LongText | None = None
    security_headers: Annotated[list[ShortText], Field(max_length=SECURITY_HEADERS_MAX)] | None = (
        None
    )
    missing_security_headers: (
        Annotated[list[ShortText], Field(max_length=SECURITY_HEADERS_MAX)] | None
    ) = None
    site_files_checked_at: AwareDatetime | None = None
    robots_txt: bool | None = None
    robots_disallow_count: int | None = Field(default=None, ge=0, le=100_000)
    sitemap_url_count: int | None = Field(default=None, ge=0, le=10_000_000)
    security_txt: bool | None = None
    security_txt_expires: AwareDatetime | None = None
    security_txt_contact_kinds: (
        Annotated[list[ShortText], Field(max_length=SECURITY_TXT_MAX_KINDS)] | None
    ) = None
    web_checked_at: AwareDatetime | None = None
    web_title: LongText | None = None
    web_generator: ShortText | None = None
    analytics_ids: Annotated[list[ShortText], Field(max_length=ANALYTICS_IDS_MAX)] | None = None
    favicon_checked_at: AwareDatetime | None = None
    typosquats_checked_at: AwareDatetime | None = None
    typosquat_candidates: int | None = Field(default=None, ge=0, le=10_000)
    typosquats_registered: int | None = Field(default=None, ge=0, le=10_000)


# Exposure data on an IP address (Shodan InternetDB): at most this many of each.
EXPOSURE_MAX_PORTS = 200
EXPOSURE_MAX_CPES = 100
EXPOSURE_MAX_VULNS = 500
EXPOSURE_MAX_TAGS = 50
# The TCP port check: at most this many ports tried.
PORT_CHECK_MAX_PORTS = 50
Port = Annotated[int, Field(ge=1, le=65535)]
CpeText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=5, max_length=300, pattern=r"^cpe:")
]
CveId = Annotated[str, Field(max_length=30), _matching(r"CVE-\d{4}-\d{4,}", upper=True)]


class IPAddressProperties(Properties):
    """``scope``: ``private`` for addresses that are not globally routable
    (RFC 1918, loopback, link-local, ...), ``public`` otherwise.

    From Shodan's InternetDB (a weekly summary of Shodan's own scans, never a
    connection to the address): ``open_ports`` (the TCP ports Shodan found
    open, ascending, at most 200), ``cpes`` (CPE names of the software it
    identified, e.g. ``cpe:/a:nginx:nginx``, at most 100), ``vulns`` (CVE IDs
    Shodan associates with that software, ascending, at most 500; reported by
    version, not verified), ``tags`` (Shodan's labels such as ``cdn`` or
    ``self-signed``, at most 50) and ``internetdb_checked_at`` (when the
    record was read).

    From reverse DNS: ``ptr_checked_at`` (when the address's PTR records were
    last looked up, whether or not it had any).

    From the TCP port check (active: it connects to the address itself, so
    only on verified targets): ``tcp_checked_ports`` (the ports it tried),
    ``tcp_open_ports`` (those that accepted a connection) and
    ``tcp_checked_at``.
    """

    scope: Literal["public", "private"] | None = None
    open_ports: Annotated[list[Port], Field(max_length=EXPOSURE_MAX_PORTS)] | None = None
    cpes: Annotated[list[CpeText], Field(max_length=EXPOSURE_MAX_CPES)] | None = None
    vulns: Annotated[list[CveId], Field(max_length=EXPOSURE_MAX_VULNS)] | None = None
    tags: Annotated[list[ShortText], Field(max_length=EXPOSURE_MAX_TAGS)] | None = None
    internetdb_checked_at: AwareDatetime | None = None
    ptr_checked_at: AwareDatetime | None = None
    tcp_open_ports: Annotated[list[Port], Field(max_length=PORT_CHECK_MAX_PORTS)] | None = None
    tcp_checked_ports: Annotated[list[Port], Field(max_length=PORT_CHECK_MAX_PORTS)] | None = None
    tcp_checked_at: AwareDatetime | None = None


class NetblockProperties(Properties):
    """``registry`` is the Regional Internet Registry (``ARIN``, ``RIPE NCC``, ...).

    From RDAP: the network's ``handle``, its ``type`` as the registry names it
    (``DIRECT ALLOCATION``, ``ASSIGNED PA``, ...), ``registered`` and
    ``last_changed``, and the ``parent_handle`` of the network it is part of.

    From routing data (RIPEstat): ``announced`` (whether it is announced in the
    global routing table as a prefix of its own), ``bgp_first_seen`` and
    ``bgp_last_seen`` (the earliest start and latest end of its announcement
    in the period asked about), and ``iana_block`` / ``iana_block_description``
    (the IANA registry block it falls in, e.g. ``1.0.0.0/8``,
    ``APNIC (Status: ALLOCATED)``).
    """

    name: ShortText | None = None
    country: CountryCode | None = None
    registry: ShortText | None = None
    handle: Handle | None = None
    type: ShortText | None = None
    registered: AwareDatetime | None = None
    last_changed: AwareDatetime | None = None
    parent_handle: Handle | None = None
    announced: bool | None = None
    bgp_first_seen: AwareDatetime | None = None
    bgp_last_seen: AwareDatetime | None = None
    iana_block: ShortText | None = None
    iana_block_description: ShortText | None = None


class ASNProperties(Properties):
    """``registry`` is the Regional Internet Registry; ``handle``, ``registered``
    and ``last_changed`` come from RDAP.

    From routing data (RIPEstat): ``as_name`` (the network name in the holder,
    ``CLOUDFLARENET`` in ``CLOUDFLARENET - Cloudflare, Inc.``), ``announced``
    (whether it originates any prefix now) and ``iana_block`` /
    ``iana_block_description`` (the IANA AS number block, e.g. ``13312-15359``,
    ``Assigned by ARIN``).
    """

    name: ShortText | None = None
    country: CountryCode | None = None
    registry: ShortText | None = None
    handle: Handle | None = None
    registered: AwareDatetime | None = None
    last_changed: AwareDatetime | None = None
    as_name: ShortText | None = None
    announced: bool | None = None
    iana_block: ShortText | None = None
    iana_block_description: ShortText | None = None


class CertificateProperties(Properties):
    """``subject_cn`` is the subject's common name; ``san_count`` the number of
    entries in the subjectAltName extension; ``crtsh_id`` the certificate's ID
    at crt.sh (Certificate Transparency search), when it came from there."""

    subject: LongText | None = None
    subject_cn: ShortText | None = None
    issuer: LongText | None = None
    serial_number: ShortText | None = None
    not_before: AwareDatetime | None = None
    not_after: AwareDatetime | None = None
    self_signed: bool | None = None
    san_count: int | None = Field(default=None, ge=0, le=100_000)
    crtsh_id: int | None = Field(default=None, ge=1)


# Archive data on a URL (Wayback Machine): query parameter names kept per URL.
ARCHIVE_MAX_QUERY_PARAMS = 30
ARCHIVE_QUERY_PARAM_MAX_LENGTH = 64
QueryParamName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=ARCHIVE_QUERY_PARAM_MAX_LENGTH
    ),
]
ARCHIVE_URL_PREFIX = "https://web.archive.org/web/"


def _archive_url(value: str) -> str:
    if not value.startswith(ARCHIVE_URL_PREFIX):
        raise ValueError(f"must start with {ARCHIVE_URL_PREFIX}")
    return value


class URLProperties(Properties):
    """``status_code``, ``title`` and ``content_type`` describe the page as fetched.

    From the Wayback Machine (Internet Archive's CDX index, never the site
    itself): ``first_captured`` and ``last_captured`` (the earliest and, when
    the archive reported it, the latest capture), ``capture_count`` (how many
    distinct archived addresses, such as query-string variants, became this
    URL), ``last_status`` (the HTTP status code of the latest capture seen),
    ``mimetype`` (its media type), ``archive_url`` (a link to the archived
    copy at web.archive.org) and ``query_params`` (the sorted names, never the
    values, of the query parameters the archived addresses had; at most 30,
    each at most 64 characters). The URL itself never carries a query string
    from the archive: values can hold tokens or personal data.

    From the screenshot Shuttle (it visits the site): ``screenshot`` (the
    evidence name of the PNG, ``<sha256>.png``, read through the case's
    evidence endpoint), ``screenshot_at``, ``screenshot_width`` and
    ``screenshot_height`` (pixels), and ``screenshot_phash``, a 64-bit
    perceptual hash (dHash) as 16 hexadecimal digits: pages that look alike
    have hashes a few bits apart.
    """

    status_code: int | None = Field(default=None, ge=100, le=599)
    title: LongText | None = None
    content_type: MimeType | None = None
    first_captured: AwareDatetime | None = None
    last_captured: AwareDatetime | None = None
    capture_count: int | None = Field(default=None, ge=1, le=10_000_000)
    last_status: int | None = Field(default=None, ge=100, le=599)
    mimetype: MimeType | None = None
    archive_url: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, max_length=2200),
            AfterValidator(_archive_url),
        ]
        | None
    ) = None
    query_params: (
        Annotated[list[QueryParamName], Field(max_length=ARCHIVE_MAX_QUERY_PARAMS)] | None
    ) = None
    screenshot: EvidenceName | None = None
    screenshot_at: AwareDatetime | None = None
    screenshot_width: int | None = Field(default=None, ge=1, le=10_000)
    screenshot_height: int | None = Field(default=None, ge=1, le=10_000)
    screenshot_phash: PerceptualHash | None = None


class ServiceProperties(Properties):
    product: ShortText | None = None
    version: ShortText | None = None
    banner: LongText | None = None


class TechnologyProperties(Properties):
    version: ShortText | None = None
    category: ShortText | None = None


class OrganizationProperties(Properties):
    """What is known about an organization.

    ``country`` is where its legal address is. The rest describe its Legal
    Entity Identifier record, written by the GLEIF Shuttle: ``lei``,
    ``legal_name`` (as registered), ``jurisdiction`` (where it is
    incorporated), ``legal_form`` (an ISO 20275 ELF code, or the form as
    text), ``registered_as`` (its number in the business register),
    ``entity_status`` (``ACTIVE`` or ``INACTIVE``), ``lei_status`` (the
    LEI's registration: ``ISSUED``, ``LAPSED``, ``RETIRED``...) and
    ``gleif_checked_at`` (when GLEIF was asked).
    """

    country: CountryCode | None = None
    industry: ShortText | None = None
    lei: Lei | None = None
    legal_name: ShortText | None = None
    jurisdiction: Jurisdiction | None = None
    legal_form: ShortText | None = None
    registered_as: ShortText | None = None
    entity_status: StatusCode | None = None
    lei_status: StatusCode | None = None
    gleif_checked_at: AwareDatetime | None = None


class UsernameProperties(Properties):
    platform: ShortText | None = None


class SocialProfileProperties(Properties):
    platform: ShortText | None = None
    profile_url: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)] | None
    ) = None


class LocationProperties(Properties):
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    country: CountryCode | None = None


class DocumentProperties(Properties):
    title: LongText | None = None
    mime_type: MimeType | None = None
    sha256: Sha256Hex | None = None


class ImageProperties(Properties):
    mime_type: MimeType | None = None
    sha256: Sha256Hex | None = None
    width: int | None = Field(default=None, ge=1, le=100_000)
    height: int | None = Field(default=None, ge=1, le=100_000)


class FaviconProperties(Properties):
    """The icon file a site serves. Its MurmurHash3 is the entity's value."""

    sha256: Sha256Hex | None = None
    md5: Md5Hex | None = None
    mime_type: MimeType | None = None
    size_bytes: int | None = Field(default=None, ge=1, le=10 * 1024 * 1024)


class BreachProperties(Properties):
    breach_date: date | None = None
    record_count: int | None = Field(default=None, ge=0)
    data_classes: list[ShortText] | None = Field(default=None, max_length=50)


# --- entities ----------------------------------------------------------------------------


class EntityBase(BaseModel):
    """Common shape of every entity: a canonical ``value`` and optional ``properties``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: ClassVar[EntityType]
    # The Neo4j label of the type (next to the common ``Entity`` label).
    label: ClassVar[str]
    sensitivity: ClassVar[Sensitivity]
    # For people: the type's name, what a value looks like, and an example
    # value (documentation-only: example.org, RFC 5737 addresses, RFC 5398
    # AS numbers). Every example normalizes to a valid entity of its type.
    display_name: ClassVar[str]
    hint: ClassVar[str]
    example: ClassVar[str]
    properties_model: ClassVar[type[Properties]] = NoProperties

    value: str
    properties: Properties | None = None

    _key: str = PrivateAttr(default="")

    @field_validator("value", mode="before")
    @classmethod
    def _normalize_value(cls, value: object) -> object:
        # Imported here: the normalizers import EntityType from this module.
        from nightloom_core.normalize import normalize

        if not isinstance(value, str):
            return value  # pydantic reports the wrong type
        return normalize(cls.entity_type, value).value

    @field_validator("properties", mode="before")
    @classmethod
    def _typed_properties(cls, value: object) -> object:
        if value is None or isinstance(value, cls.properties_model):
            return value
        return cls.properties_model.model_validate(value)

    def model_post_init(self, context: Any, /) -> None:
        from nightloom_core.normalize import normalize

        self._key = normalize(self.entity_type, self.value).key

    @property
    def key(self) -> str:
        """The canonical key, ``<type>:<normalized value>``."""
        return self._key

    def stored_properties(self) -> dict[str, Any]:
        return self.properties.stored() if self.properties is not None else {}


class Domain(EntityBase):
    """Any fully qualified domain name, including subdomains (stored in A-label form)."""

    type: Literal[EntityType.DOMAIN] = EntityType.DOMAIN
    entity_type = EntityType.DOMAIN
    label = "Domain"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "Domain"
    hint = "A fully qualified domain name; subdomains are domains too."
    example = "www.example.org"
    properties_model = DomainProperties


class IPAddress(EntityBase):
    type: Literal[EntityType.IP_ADDRESS] = EntityType.IP_ADDRESS
    entity_type = EntityType.IP_ADDRESS
    label = "IPAddress"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "IP address"
    hint = "An IPv4 or IPv6 address."
    example = "192.0.2.1"
    properties_model = IPAddressProperties


class Netblock(EntityBase):
    type: Literal[EntityType.NETBLOCK] = EntityType.NETBLOCK
    entity_type = EntityType.NETBLOCK
    label = "Netblock"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "Netblock"
    hint = "A network in CIDR notation."
    example = "192.0.2.0/24"
    properties_model = NetblockProperties


class ASN(EntityBase):
    type: Literal[EntityType.ASN] = EntityType.ASN
    entity_type = EntityType.ASN
    label = "ASN"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "ASN"
    hint = "An autonomous system number, with or without the AS prefix."
    example = "AS64496"
    properties_model = ASNProperties


class Certificate(EntityBase):
    """An X.509 certificate, identified by its SHA-256 fingerprint."""

    type: Literal[EntityType.CERTIFICATE] = EntityType.CERTIFICATE
    entity_type = EntityType.CERTIFICATE
    label = "Certificate"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "Certificate"
    hint = "The certificate's SHA-256 fingerprint (64 hex digits; colons are fine)."
    example = "879dc3b1e28fb00acda1fb81afd12b6e278bbd88d5670262b4b80a74413ae15e"
    properties_model = CertificateProperties


class URL(EntityBase):
    type: Literal[EntityType.URL] = EntityType.URL
    entity_type = EntityType.URL
    label = "URL"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "URL"
    hint = "An http, https or ftp URL, without credentials."
    example = "https://www.example.org/login"
    properties_model = URLProperties


class Service(EntityBase):
    """A network service, ``ip:port/protocol``."""

    type: Literal[EntityType.SERVICE] = EntityType.SERVICE
    entity_type = EntityType.SERVICE
    label = "Service"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "Port / Service"
    hint = "ip:port/protocol (tcp or udp); IPv6 in brackets."
    example = "192.0.2.1:443/tcp"
    properties_model = ServiceProperties


class Technology(EntityBase):
    type: Literal[EntityType.TECHNOLOGY] = EntityType.TECHNOLOGY
    entity_type = EntityType.TECHNOLOGY
    label = "Technology"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "Technology"
    hint = "A product or framework name."
    example = "nginx"
    properties_model = TechnologyProperties


class Organization(EntityBase):
    type: Literal[EntityType.ORGANIZATION] = EntityType.ORGANIZATION
    entity_type = EntityType.ORGANIZATION
    label = "Organization"
    sensitivity = Sensitivity.ORGANIZATION
    display_name = "Organization"
    hint = "The organization's name."
    example = "Example Org"
    properties_model = OrganizationProperties


class Email(EntityBase):
    type: Literal[EntityType.EMAIL] = EntityType.EMAIL
    entity_type = EntityType.EMAIL
    label = "Email"
    sensitivity = Sensitivity.PERSON
    display_name = "Email"
    hint = "An email address."
    example = "contact@example.org"


class Username(EntityBase):
    type: Literal[EntityType.USERNAME] = EntityType.USERNAME
    entity_type = EntityType.USERNAME
    label = "Username"
    sensitivity = Sensitivity.PERSON
    display_name = "Username"
    hint = "A handle, as used on its platform."
    example = "example_user"
    properties_model = UsernameProperties


class Person(EntityBase):
    type: Literal[EntityType.PERSON] = EntityType.PERSON
    entity_type = EntityType.PERSON
    label = "Person"
    sensitivity = Sensitivity.PERSON
    display_name = "Person"
    hint = "A person's name."
    example = "Alex Example"


class Phone(EntityBase):
    """A phone number in E.164 form. Use :func:`build_entity` to parse national formats."""

    type: Literal[EntityType.PHONE] = EntityType.PHONE
    entity_type = EntityType.PHONE
    label = "Phone"
    sensitivity = Sensitivity.PERSON
    display_name = "Phone"
    hint = "A phone number in international format (+...), or national format with a region."
    example = "+442079460958"


class SocialProfile(EntityBase):
    type: Literal[EntityType.SOCIAL_PROFILE] = EntityType.SOCIAL_PROFILE
    entity_type = EntityType.SOCIAL_PROFILE
    label = "SocialProfile"
    sensitivity = Sensitivity.PERSON
    display_name = "Social profile"
    hint = "A profile name or handle and its platform."
    example = "@example on social.example.org"
    properties_model = SocialProfileProperties


class Location(EntityBase):
    """A place. Treated as person-related: a location next to a person is sensitive."""

    type: Literal[EntityType.LOCATION] = EntityType.LOCATION
    entity_type = EntityType.LOCATION
    label = "Location"
    sensitivity = Sensitivity.PERSON
    display_name = "Location"
    hint = "A place name or address."
    example = "Example City"
    properties_model = LocationProperties


class CryptoWallet(EntityBase):
    """A Bitcoin or Ethereum address, prefixed with its chain (``btc:`` / ``eth:``)."""

    type: Literal[EntityType.CRYPTO_WALLET] = EntityType.CRYPTO_WALLET
    entity_type = EntityType.CRYPTO_WALLET
    label = "CryptoWallet"
    sensitivity = Sensitivity.ORGANIZATION
    display_name = "Crypto wallet"
    hint = "A Bitcoin or Ethereum address (btc: or eth: prefix optional)."
    example = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


class Document(EntityBase):
    type: Literal[EntityType.DOCUMENT] = EntityType.DOCUMENT
    entity_type = EntityType.DOCUMENT
    label = "Document"
    sensitivity = Sensitivity.ORGANIZATION
    display_name = "Document"
    hint = "A document's title or file name."
    example = "Annual report 2025"
    properties_model = DocumentProperties


class Image(EntityBase):
    """An image. Treated as person-related: images can show people."""

    type: Literal[EntityType.IMAGE] = EntityType.IMAGE
    entity_type = EntityType.IMAGE
    label = "Image"
    sensitivity = Sensitivity.PERSON
    display_name = "Image"
    hint = "An image's title or file name."
    example = "logo.png"
    properties_model = ImageProperties


class Breach(EntityBase):
    """A published data breach (metadata only, never leaked records)."""

    type: Literal[EntityType.BREACH] = EntityType.BREACH
    entity_type = EntityType.BREACH
    label = "Breach"
    sensitivity = Sensitivity.ORGANIZATION
    display_name = "Breach"
    hint = "The name of a published data breach."
    example = "Example Breach 2024"
    properties_model = BreachProperties


class Favicon(EntityBase):
    """A site's icon, named by its MurmurHash3 the way Shodan and FOFA search for it.

    The value is the signed 32-bit MurmurHash3 of the icon file's base64
    encoding (with a line break every 76 characters, Shodan's
    ``http.favicon.hash``). Sites that serve the same icon share one node.
    """

    type: Literal[EntityType.FAVICON] = EntityType.FAVICON
    entity_type = EntityType.FAVICON
    label = "Favicon"
    sensitivity = Sensitivity.INFRASTRUCTURE
    display_name = "Favicon"
    hint = "A favicon's MurmurHash3 (Shodan's http.favicon.hash), a signed 32-bit number."
    example = "-1010568750"
    properties_model = FaviconProperties


ENTITY_MODELS: dict[EntityType, type[EntityBase]] = {
    model.entity_type: model
    for model in (
        Domain,
        IPAddress,
        Netblock,
        ASN,
        Certificate,
        URL,
        Service,
        Technology,
        Organization,
        Email,
        Username,
        Person,
        Phone,
        SocialProfile,
        Location,
        CryptoWallet,
        Document,
        Image,
        Breach,
        Favicon,
    )
}

AnyEntity = Annotated[
    Domain
    | IPAddress
    | Netblock
    | ASN
    | Certificate
    | URL
    | Service
    | Technology
    | Organization
    | Email
    | Username
    | Person
    | Phone
    | SocialProfile
    | Location
    | CryptoWallet
    | Document
    | Image
    | Breach
    | Favicon,
    Field(discriminator="type"),
]


def sensitivity_of(entity_type: EntityType) -> Sensitivity:
    return ENTITY_MODELS[entity_type].sensitivity


def label_of(entity_type: EntityType) -> str:
    return ENTITY_MODELS[entity_type].label


def build_entity(
    entity_type: EntityType,
    value: str,
    properties: dict[str, Any] | None = None,
    *,
    phone_region: str | None = None,
) -> EntityBase:
    """Normalize ``value`` and build the entity.

    Raises :class:`~nightloom_core.normalize.NormalizationError` for a value
    that cannot be normalized (its message is safe to show), and pydantic's
    ``ValidationError`` for invalid properties. ``phone_region`` (ISO 3166
    alpha-2) is the default region for phone numbers given in national
    format; it is ignored for every other type.
    """
    from nightloom_core.normalize import normalize

    normalized = normalize(entity_type, value, phone_region=phone_region)
    return ENTITY_MODELS[entity_type].model_validate(
        {"value": normalized.value, "properties": properties}
    )


__all__ = [
    "ANALYTICS_IDS_MAX",
    "ASN",
    "ENTITY_MODELS",
    "PORT_CHECK_MAX_PORTS",
    "SECURITY_HEADERS_MAX",
    "SECURITY_TXT_MAX_KINDS",
    "URL",
    "AnyEntity",
    "Breach",
    "Certificate",
    "CryptoWallet",
    "Document",
    "Domain",
    "Email",
    "EntityBase",
    "EntityType",
    "Favicon",
    "IPAddress",
    "Image",
    "Location",
    "Netblock",
    "Organization",
    "Person",
    "Phone",
    "Properties",
    "Sensitivity",
    "Service",
    "SocialProfile",
    "Technology",
    "Username",
    "build_entity",
    "label_of",
    "sensitivity_of",
]
