# Ethics and Acceptable Use

Nightloom collects and connects **public** information. Connecting public facts can still cause real harm, so this document defines what Nightloom is for, what it must not be used for, and the guardrails it is designed to enforce.

> Nightloom is in early development. The guardrails below are **planned** design requirements; they will be implemented as the features they govern are built.

## Intended use

- Lawful security research and threat intelligence
- Attack-surface management of infrastructure you own or are authorized to assess
- Journalism and public-interest research
- Education and training
- Investigating organizations and infrastructure using public data only

## Prohibited use

Do not use Nightloom for:

- Stalking, harassment or intimidation of anyone
- Doxxing — collecting or publishing personal information to expose or target someone
- Targeting private individuals without a lawful basis
- Scraping content behind logins or bypassing authentication, paywalls, CAPTCHAs or other access controls
- Testing, validating or using leaked credentials
- Facial recognition of individuals
- Real-time location tracking of people
- Violating the terms of service of the sites and APIs you query

## Built-in guardrails (planned)

| Guardrail | What it means |
| --- | --- |
| Stated purpose | [Every case needs a stated purpose](docs/ARCHITECTURE.md#cases) and a legal basis before any collection starts, and its creator acknowledges this document. The legal bases are: `owned_assets`, `authorized_engagement` (with a note naming the engagement), `threat_intelligence`, `journalism`, `academic_research`, `self_assessment` and `other` (with a note explaining it). Changing a case's purpose or legal basis needs a recorded reason. |
| Audit log | Every action — searches, Shuttle runs, exports, deletions — is recorded in a [full audit log](docs/ARCHITECTURE.md#audit-log) that is append-only and tamper-evident. Sign-ins, user management, case and membership changes, and refused requests are recorded today. |
| Gated person-related Shuttles | Shuttles that touch people are opt-in per case (`allow_person_entities`, off by default; turning it on needs a recorded reason) and **never** run during auto-pivot. **Enforced today for the graph:** the [person-related entity types](docs/ARCHITECTURE.md#entity-types) (Person, Email, Username, Phone, SocialProfile, Location, Image) can only be added to a case that allows person entities, and relationships touching them are refused once a case stops allowing them; the API answers `403` and records `authz.denied` (`person_entities_disabled`). Their keys appear in the audit log only as hashes. |
| Polite collection | Respects `robots.txt` and per-source rate limits. |
| Verified ownership for active scans | Active (non-passive) Shuttles run only against assets whose ownership has been verified. |
| PII redaction on export | Exports can redact personal data and do so by default where it applies. |
| Retention and deletion | Configurable data retention (a case can already store a retention period; it is not enforced yet); deleting a case removes its data, while its audit trail is kept. |

## Your responsibility

You are responsible for complying with the laws of your jurisdiction and of the jurisdictions you investigate, including privacy, data-protection and computer-misuse laws. The guardrails reduce risk; they do not make an unlawful use lawful.

## Reporting misuse

If you believe Nightloom or a Shuttle enables harm, open an issue — or, for anything sensitive, use private reporting as described in [SECURITY.md](SECURITY.md).
