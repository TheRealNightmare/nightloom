import type { AuditEvent, Case, CaseRole, Member } from "../api/cases";
import type { User } from "../api/client";
import type { ApiEntity, ApiGraph, ApiProvenance, ApiRelationship, Schema } from "../api/graph";
import type { Verification } from "../api/ownership";
import type { ScanProfile, ScanRun } from "../api/scans";
import type { Job, ShuttleInfo } from "../api/shuttles";
import type { EntityType, Sensitivity } from "../domain/entities";
import { json, READY_BODY, TEST_USER, type FakeApi } from "./fakeApi";
import schemaJson from "./schema.json";

/** What `GET /schema` returns (kept in sync with the API by apps/api/tests/test_schema_unit.py). */
export const SCHEMA = schemaJson as Schema;

export const EMPTY_GRAPH: ApiGraph = { nodes: [], edges: [], truncated: false };

export function makeProvenance(overrides: Partial<ApiProvenance> = {}): ApiProvenance {
  return {
    first_seen: "2026-11-10T10:00:00Z",
    last_seen: "2026-11-12T08:30:00Z",
    observation_count: 1,
    sources: ["manual"],
    max_confidence: 0.8,
    admiralty: "B2",
    evidence_refs: [],
    note: null,
    ...overrides,
  };
}

const SENSITIVITY: Partial<Record<EntityType, Sensitivity>> = Object.fromEntries(
  SCHEMA.entity_types.map((item) => [item.type, item.sensitivity]),
);

export function makeEntity(
  type: EntityType,
  value: string,
  overrides: Partial<ApiEntity> = {},
): ApiEntity {
  return {
    key: `${type}:${value.toLowerCase()}`,
    type,
    value,
    sensitivity: SENSITIVITY[type] ?? "infrastructure",
    properties: {},
    provenance: makeProvenance(),
    ...overrides,
  };
}

export function makeRelationship(
  type: string,
  source: ApiEntity,
  target: ApiEntity,
  overrides: Partial<ApiRelationship> = {},
): ApiRelationship {
  return {
    id: `${source.key}|${type}|${target.key}`,
    type,
    source_key: source.key,
    target_key: target.key,
    provenance: makeProvenance(),
    ...overrides,
  };
}

export const CASE_ID = "5f0c8a52-6a0e-4c1e-9d6c-0b8f6e9c2a11";

export function makeCase(overrides: Partial<Case> = {}): Case {
  return {
    id: CASE_ID,
    title: "Example.org exposure review",
    description: null,
    purpose: "Map the public attack surface of example.org for the owner.",
    legal_basis: "owned_assets",
    legal_basis_note: null,
    ethics_ack_version: "2026-11",
    ethics_ack_at: "2026-11-10T10:00:00Z",
    allow_person_entities: false,
    allow_auto_pivot: false,
    status: "open",
    retention_days: null,
    created_by: TEST_USER.id,
    created_at: "2026-11-10T10:00:00Z",
    updated_at: "2026-11-10T10:00:00Z",
    closed_at: null,
    my_role: "owner",
    ...overrides,
  };
}

export function makeMember(user: Pick<User, "id" | "email" | "display_name">, role: CaseRole) {
  return {
    user_id: user.id,
    email: user.email,
    display_name: user.display_name,
    global_role: "analyst",
    is_active: true,
    role,
    added_by: null,
    added_at: "2026-11-10T10:00:00Z",
  } satisfies Member;
}

export const OTHER_USER = {
  id: "9d1f7e2a-3b4c-4d5e-8f60-718293a4b5c6",
  email: "bob@example.org",
  display_name: "Bob Builder",
};

export function makeEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: 1,
    occurred_at: "2026-11-10T10:00:00Z",
    actor_user_id: TEST_USER.id,
    actor_label: TEST_USER.email,
    actor_ip: "127.0.0.1",
    request_id: "req-00000001",
    action: "case.create",
    outcome: "success",
    target_type: "case",
    target_id: CASE_ID,
    case_id: CASE_ID,
    details: {},
    ...overrides,
  };
}

export function makeShuttle(overrides: Partial<ShuttleInfo> = {}): ShuttleInfo {
  return {
    name: "dns",
    version: "1.0.0",
    title: "DNS records",
    description: "Looks up the domain's public DNS records through public resolvers only.",
    input_types: ["domain"],
    output_entity_types: ["domain", "ip_address"],
    output_relationship_types: ["ALIAS_OF", "HAS_MX", "HAS_NS", "RESOLVES_TO"],
    mode: "passive",
    sensitivity: "infrastructure",
    network: true,
    required_keys: [],
    rate_limit: { requests: 10, period_seconds: 1 },
    timeout_seconds: 60,
    terms_url: "https://developers.cloudflare.com/1.1.1.1/privacy/public-dns-resolver/",
    homepage: "https://developers.cloudflare.com/1.1.1.1/",
    usage_note: null,
    default_admiralty: "B2",
    default_confidence: 0.9,
    ...overrides,
  };
}

export const DNS_SHUTTLE = makeShuttle();

export function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "7c3e1b2a-0d4f-4e5a-9b6c-8d7e6f5a4b3c",
    case_id: CASE_ID,
    shuttle_name: "dns",
    shuttle_version: "1.0.0",
    input_key: "domain:example.org",
    requested_by: TEST_USER.id,
    status: "queued",
    cancel_requested: false,
    created_at: "2026-11-12T08:30:00Z",
    started_at: null,
    finished_at: null,
    error: null,
    timeout_seconds: 30,
    deadline: null,
    overdue_at: null,
    ...overrides,
    stats: {
      entities_created: 0,
      entities_updated: 0,
      relationships_created: 0,
      relationships_updated: 0,
      findings_rejected: 0,
      rejected_reasons: {},
      entities_touched: [],
      notes: [],
      ...overrides.stats,
    },
  };
}

export function makeProfile(overrides: Partial<ScanProfile> = {}): ScanProfile {
  return {
    id: "quick-domain-recon",
    name: "Quick domain recon",
    description: "A domain's DNS records, its registration data and its registrable domain.",
    shuttles: ["dns", "rdap", "registrable-domain"],
    built_in: true,
    input_types: ["asn", "domain", "ip_address", "netblock"],
    missing_shuttles: [],
    updated_at: null,
    ...overrides,
  };
}

export const QUICK_PROFILE = makeProfile();
export const EXPOSURE_PROFILE = makeProfile({
  id: "exposure-check",
  name: "Exposure check",
  description: "What is open on an address and which names point to it.",
  shuttles: ["internetdb", "reverse-dns"],
  input_types: ["ip_address", "netblock"],
});

export function makeRun(overrides: Partial<ScanRun> = {}): ScanRun {
  return {
    id: "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
    case_id: CASE_ID,
    profile_id: "quick-domain-recon",
    profile_name: "Quick domain recon",
    requested_by: TEST_USER.id,
    status: "running",
    stop_reason: null,
    entities: 2,
    total: 6,
    released: 5,
    waiting: 1,
    skipped: 0,
    use_cache: true,
    pivot: false,
    max_depth: 0,
    max_jobs: 6,
    max_entities: 0,
    discovered: 0,
    depth: 0,
    capped: null,
    created_at: "2026-11-12T09:00:00Z",
    finished_at: null,
    ...overrides,
    jobs: { queued: 3, running: 1, succeeded: 1, failed: 0, cancelled: 0, ...overrides.jobs },
  };
}

export function makeVerification(overrides: Partial<Verification> = {}): Verification {
  return {
    id: "5f4e3d2c-1b0a-4f9e-8d7c-6b5a4f3e2d1c",
    domain: "example.org",
    method: "dns_txt",
    status: "pending",
    proof: {
      record_type: "TXT",
      record_name: "_nightloom-challenge.example.org",
      url: null,
      value: "nightloom-verification=abc123",
    },
    checking: false,
    created_at: "2026-11-20T09:00:00Z",
    checked_at: null,
    verified_at: null,
    expires_at: null,
    last_error: null,
    error_message: null,
    addresses: [],
    ...overrides,
  };
}

export const ADMIN_USER: User = { ...TEST_USER, role: "admin" };
export const VIEWER_USER: User = { ...TEST_USER, role: "viewer" };

export const EMPTY_PAGE = { items: [], next_cursor: null };

/** Registers a signed-in `user` and one case where they have `role`. */
export function serveCase(
  api: FakeApi,
  {
    user = TEST_USER,
    role = "owner",
    overrides = {},
    members,
    events = [],
    graph = EMPTY_GRAPH,
    jobs = [],
    shuttles = [DNS_SHUTTLE],
    profiles = [QUICK_PROFILE, EXPOSURE_PROFILE],
    runs = [],
    verifications = [],
  }: {
    user?: User;
    role?: CaseRole;
    overrides?: Partial<Case>;
    members?: Member[];
    events?: AuditEvent[];
    graph?: ApiGraph;
    jobs?: Job[];
    shuttles?: ShuttleInfo[];
    profiles?: ScanProfile[];
    runs?: ScanRun[];
    verifications?: Verification[];
  } = {},
): void {
  const path = `/cases/${CASE_ID}`;
  api
    .on("GET", "/ready", () => json(READY_BODY))
    .on("GET", "/auth/me", () => json(user))
    .on("GET", path, () => json(makeCase({ my_role: role, ...overrides })))
    .on("GET", `${path}/members`, () => json(members ?? [makeMember(user, role)]))
    .on("GET", `${path}/audit?limit=50`, () => json({ items: events, next_before_id: null }))
    .on("GET", `${path}/graph?limit=2000`, () => json(graph))
    .on("GET", "/schema", () => json(SCHEMA))
    .on("GET", `${path}/jobs?limit=20`, () => json({ items: jobs, next_cursor: null }))
    .on("GET", "/shuttles?input_type=domain", () => json(shuttles))
    .on("GET", "/shuttles?input_type=ip_address", () => json([]))
    .on("GET", "/scan-profiles", () => json(profiles))
    .on("GET", `${path}/scan-runs?limit=20`, () => json({ items: runs }))
    .on("GET", `${path}/verifications`, () => json({ items: verifications }))
    .on("GET", "/cases?limit=25", () => json(EMPTY_PAGE));
}
