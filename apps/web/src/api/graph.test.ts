import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CASE_ID, EMPTY_GRAPH, makeEntity, makeRelationship, SCHEMA } from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent } from "../test/fakeApi";
import { CSRF_HEADER, ForbiddenError, UnexpectedResponseError, ValidationError } from "./client";
import {
  createEntity,
  createRelationship,
  deleteEntity,
  getCaseGraph,
  getEntity,
  getSchema,
  isApiEntity,
  isSchema,
  resetSchemaCache,
} from "./graph";

let api: FakeApi;
const casePath = `/cases/${CASE_ID}`;
const url = makeEntity("url", "https://www.example.org/a/b?c=d", {
  key: "url:https://www.example.org/a/b?c=d",
});

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
  resetSchemaCache();
});

afterEach(() => {
  clearCookies();
});

describe("graph calls", () => {
  it("loads the case graph with the default limit", async () => {
    const graph = { nodes: [url], edges: [], truncated: true };
    api.on("GET", `${casePath}/graph?limit=2000`, () => json(graph));
    expect(await getCaseGraph(CASE_ID)).toEqual(graph);
    api.on("GET", `${casePath}/graph?limit=10`, () => json(EMPTY_GRAPH));
    expect(await getCaseGraph(CASE_ID, { limit: 10 })).toEqual(EMPTY_GRAPH);
  });

  it("refuses a graph of the wrong shape", async () => {
    api.on("GET", `${casePath}/graph?limit=2000`, () =>
      json({ nodes: [{ ...url, type: "subdomain" }], edges: [], truncated: false }),
    );
    await expect(getCaseGraph(CASE_ID)).rejects.toBeInstanceOf(UnexpectedResponseError);
  });

  it("percent-encodes keys, slashes included", async () => {
    const path = `${casePath}/entities/url%3Ahttps%3A%2F%2Fwww.example.org%2Fa%2Fb%3Fc%3Dd`;
    api.on("GET", path, () => json({ ...url, neighbours: 2 }));
    api.on("DELETE", path, () => noContent());
    expect((await getEntity(CASE_ID, url.key)).neighbours).toBe(2);
    await deleteEntity(CASE_ID, url.key);
    expect(api.callsTo("DELETE", path)[0]?.headers[CSRF_HEADER]).toBe("csrf-token");
  });

  it("creates entities and relationships with the CSRF header", async () => {
    const domain = makeEntity("domain", "example.org");
    const rel = makeRelationship("HOSTS", domain, url);
    api.on("POST", `${casePath}/entities`, () => json(domain, 201));
    api.on("POST", `${casePath}/relationships`, () => json(rel, 201));
    expect(await createEntity(CASE_ID, { type: "domain", value: "Example.ORG." })).toEqual(domain);
    expect(
      await createRelationship(CASE_ID, {
        type: "HOSTS",
        source_key: domain.key,
        target_key: url.key,
      }),
    ).toEqual(rel);
    for (const call of api.calls.filter((c) => c.method === "POST")) {
      expect(call.headers[CSRF_HEADER]).toBe("csrf-token");
    }
  });

  it("passes server errors through as typed errors", async () => {
    api.on("POST", `${casePath}/entities`, () => json({ detail: "Not a valid domain name." }, 422));
    await expect(createEntity(CASE_ID, { type: "domain", value: "x" })).rejects.toEqual(
      new ValidationError([{ field: null, message: "Not a valid domain name." }]),
    );
    api.on("POST", `${casePath}/relationships`, () => json({ detail: "x" }, 403));
    await expect(
      createRelationship(CASE_ID, { type: "HOSTS", source_key: "a", target_key: "b" }),
    ).rejects.toBeInstanceOf(ForbiddenError);
  });

  it("checks entity shapes", () => {
    expect(isApiEntity(url)).toBe(true);
    expect(isApiEntity({ ...url, sensitivity: "secret" })).toBe(false);
    expect(isApiEntity({ ...url, provenance: { ...url.provenance, sources: [1] } })).toBe(false);
    expect(isApiEntity(null)).toBe(false);
  });
});

describe("schema", () => {
  it("accepts the API's schema and rejects broken ones", () => {
    expect(isSchema(SCHEMA)).toBe(true);
    expect(isSchema({ ...SCHEMA, entity_types: [{ type: "domain" }] })).toBe(false);
    expect(
      isSchema({
        ...SCHEMA,
        relationship_types: [{ type: "X", note_required: false, pairs: [["domain"]] }],
      }),
    ).toBe(false);
    expect(isSchema({ ...SCHEMA, admiralty: { reliability: [{}], credibility: [] } })).toBe(false);
    expect(isSchema([])).toBe(false);
  });

  it("is fetched once and shared", async () => {
    api.on("GET", "/schema", () => json(SCHEMA));
    const [first, second] = await Promise.all([getSchema(), getSchema()]);
    expect(first).toBe(second);
    expect(await getSchema()).toBe(first);
    expect(api.callsTo("GET", "/schema")).toHaveLength(1);
  });

  it("is fetched again after a failure", async () => {
    let fail = true;
    api.on("GET", "/schema", () => (fail ? json({}, 500) : json(SCHEMA)));
    await expect(getSchema()).rejects.toBeInstanceOf(UnexpectedResponseError);
    fail = false;
    expect(await getSchema()).toEqual(SCHEMA);
    expect(api.callsTo("GET", "/schema")).toHaveLength(2);
  });
});
