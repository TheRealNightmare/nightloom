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
MimeType = Annotated[
    str,
    Field(max_length=127),
    _matching(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*"),
]


class Properties(BaseModel):
    """Base class for an entity type's optional properties.

    Fields are flat scalars or lists of strings, so they can be stored as
    graph properties. Every field is optional.
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


class DomainProperties(Properties):
    """What public DNS says about the name. Every field is optional.

    ``dns_txt`` holds the TXT records (each joined from its strings);
    ``dns_spf`` and ``dns_dmarc`` repeat the SPF (``v=spf1``) and DMARC
    (``v=DMARC1``, from ``_dmarc.<name>``) records among them; ``dns_mx`` is
    ``"<preference> <exchange>"``; ``dns_caa`` is ``"<flags> <tag> <value>"``;
    the ``dns_soa_*`` fields are the zone's SOA record when the name is a
    zone apex.
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


class IPAddressProperties(Properties):
    """``scope``: ``private`` for addresses that are not globally routable
    (RFC 1918, loopback, link-local, ...), ``public`` otherwise."""

    scope: Literal["public", "private"] | None = None


class NetblockProperties(Properties):
    name: ShortText | None = None
    country: CountryCode | None = None
    registry: ShortText | None = None


class ASNProperties(Properties):
    name: ShortText | None = None
    country: CountryCode | None = None
    registry: ShortText | None = None


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


class URLProperties(Properties):
    status_code: int | None = Field(default=None, ge=100, le=599)
    title: LongText | None = None
    content_type: MimeType | None = None


class ServiceProperties(Properties):
    product: ShortText | None = None
    version: ShortText | None = None
    banner: LongText | None = None


class TechnologyProperties(Properties):
    version: ShortText | None = None
    category: ShortText | None = None


class OrganizationProperties(Properties):
    country: CountryCode | None = None
    industry: ShortText | None = None


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
    | Breach,
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
    "ASN",
    "ENTITY_MODELS",
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
