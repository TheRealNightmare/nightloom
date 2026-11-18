# Ethics and Acceptable Use

Nightloom collects and connects **public** information. Connecting public facts can still cause real harm, so this document defines what Nightloom is for, what it must not be used for, and the guardrails it is designed to enforce.

> Nightloom is in early development. The guardrails below are design requirements; each row says what is **enforced today** and links to the check. The rest will be implemented as the features they govern are built.

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

## Built-in guardrails

| Guardrail | What it means |
| --- | --- |
| Stated purpose | [Every case needs a stated purpose](docs/ARCHITECTURE.md#cases) and a legal basis before any collection starts, and its creator acknowledges this document. Shuttles only run inside a case, for its editors and owners, while it is open; the worker re-checks this before it writes each finding. The legal bases are: `owned_assets`, `authorized_engagement` (with a note naming the engagement), `threat_intelligence`, `journalism`, `academic_research`, `self_assessment` and `other` (with a note explaining it). Changing a case's purpose or legal basis needs a recorded reason. |
| Audit log | Every action — searches, Shuttle runs, exports, deletions — is recorded in a [full audit log](docs/ARCHITECTURE.md#audit-log) that is append-only and tamper-evident. Sign-ins, user management, case and membership changes, graph changes, refused requests and **Shuttle jobs** (`job.create`, `job.cancel`, `job.finish` with counts and reason codes, never responses or values) are recorded today. |
| Gated person-related Shuttles | Shuttles that touch people are opt-in per case (`allow_person_entities`, off by default; turning it on needs a recorded reason) and **never** run during auto-pivot. **Enforced today for Shuttles:** a Shuttle that takes or produces any person-related type must declare `sensitivity: person` ([metadata validation](docs/ARCHITECTURE.md#metadata)); such a Shuttle is refused (`403`, `authz.denied`) unless the case allows person entities, the worker [checks again when the job starts](docs/ARCHITECTURE.md#jobs-and-the-worker), and every person-related finding is rejected if the case stops allowing them mid-run. **Enforced today for the graph:** the [person-related entity types](docs/ARCHITECTURE.md#entity-types) (Person, Email, Username, Phone, SocialProfile, Location, Image) can only be added to a case that allows person entities, and relationships touching them are refused once a case stops allowing them; the API answers `403` and records `authz.denied` (`person_entities_disabled`). Their keys appear in the audit log only as hashes. Infrastructure Shuttles drop person data they come across: the [crt.sh Shuttle](docs/ARCHITECTURE.md#the-crtsh-shuttle) discards email addresses found in certificates and records only how many it dropped. |
| Polite collection | Respects `robots.txt` and per-source rate limits. **Enforced today:** every Shuttle that uses the network must declare a rate limit, and its [HTTP client](docs/ARCHITECTURE.md#the-guarded-http-client) applies it per destination host (the DNS Shuttle per resolver) (redirects included), identifies itself with a `Nightloom/<version>` User-Agent, caps responses at 5 MB and times out. The [crt.sh Shuttle](docs/ARCHITECTURE.md#the-crtsh-shuttle) asks crt.sh, a free service, at most once every 3 seconds. `robots.txt` handling comes with the first Shuttle that fetches web pages. |
| Network safety | **Enforced today:** Shuttles reach the network only through a [guarded client](docs/ARCHITECTURE.md#the-guarded-http-client) that refuses private, loopback, link-local, multicast and reserved destinations after DNS resolution (no DNS rebinding) and any scheme but `http`/`https`, so a Shuttle cannot be turned against the Nightloom host or its internal network. The [DNS Shuttle](docs/ARCHITECTURE.md#the-dns-shuttle), which speaks DNS rather than HTTP, asks only configured resolvers that must be public addresses (never the host's own resolver, so internal records cannot leak into a case), refuses internal-only names such as `.local` or `.corp` before sending anything, and never connects to the addresses it finds. |
| Verified ownership for active scans | Active (non-passive) Shuttles run only against assets whose ownership has been verified. **Enforced today:** ownership verification does not exist yet, so active Shuttles are refused outright, by the API (`403`, `authz.denied`) and again by the [worker](docs/ARCHITECTURE.md#jobs-and-the-worker). |
| PII redaction on export | Exports can redact personal data and do so by default where it applies. |
| Retention and deletion | Configurable data retention (a case can already store a retention period; it is not enforced yet); deleting a case removes its data, while its audit trail is kept. |

## Your responsibility

You are responsible for complying with the laws of your jurisdiction and of the jurisdictions you investigate, including privacy, data-protection and computer-misuse laws. The guardrails reduce risk; they do not make an unlawful use lawful.

## Reporting misuse

If you believe Nightloom or a Shuttle enables harm, open an issue — or, for anything sensitive, use private reporting as described in [SECURITY.md](SECURITY.md).
