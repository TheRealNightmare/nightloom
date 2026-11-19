import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSchemaCache, type ApiEntity, type ApiGraph } from "../api/graph";
import { ENTITY_TYPE_INFO } from "../domain/entities";
import type { NightloomGraph } from "../graph/buildGraph";
import {
  CASE_ID,
  makeEntity,
  makeProvenance,
  makeRelationship,
  serveCase,
} from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { FakeSigma } from "../test/sigmaMock";
import { GRAPH_EMPTY_HINT, GRAPH_EMPTY_MESSAGE } from "./GraphTab";
import { PERSON_ENTITIES_MESSAGE, PERSON_TYPES_DISABLED_HINT } from "./graph/AddEntityDialog";
import { PERSON_RELATIONSHIP_MESSAGE } from "./graph/AddRelationshipDialog";

vi.mock("sigma", () => import("../test/sigmaMock"));

let api: FakeApi;
const path = `/cases/${CASE_ID}`;
const entityPath = (key: string) => `${path}/entities/${encodeURIComponent(key)}`;

const apex = makeEntity("domain", "example.org", {
  provenance: makeProvenance({
    observation_count: 3,
    sources: ["manual", "shuttle:dns@1.0.0"],
    max_confidence: 0.95,
    note: "Owner's main domain",
  }),
});
const www = makeEntity("domain", "www.example.org");
const ip = makeEntity("ip_address", "192.0.2.1");
const subdomain = makeRelationship("HAS_SUBDOMAIN", apex, www);
const resolves = makeRelationship("RESOLVES_TO", www, ip);

function graphOf(nodes: ApiEntity[], edges = [subdomain], truncated = false): ApiGraph {
  return {
    nodes,
    edges: edges.filter(
      (edge) =>
        nodes.some((n) => n.key === edge.source_key) &&
        nodes.some((n) => n.key === edge.target_key),
    ),
    truncated,
  };
}

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
  resetSchemaCache();
});

afterEach(() => {
  FakeSigma.reset();
  clearCookies();
});

type ServeOptions = Parameters<typeof serveCase>[1];

async function openGraph(options: ServeOptions = {}) {
  serveCase(api, options);
  const view = renderApp(`${path}/graph`);
  await screen.findByText(/\d+ entit(y|ies), \d+ relationships?/);
  return view;
}

const summary = () => screen.getByText(/\d+ entit(y|ies), \d+ relationships?/);
const details = () => screen.getByRole("complementary", { name: "Details" });

async function sigma() {
  await screen.findByTestId("graph-canvas");
  return FakeSigma.latest();
}

async function select(key: string) {
  const instance = await sigma();
  act(() => {
    instance.emit("clickNode", { node: key });
  });
}

function bodyOf(method: string, url: string): unknown {
  const call = api.callsTo(method, url).at(-1);
  if (!call) throw new Error(`no ${method} ${url}`);
  return JSON.parse(call.init.body as string);
}

describe("empty graph", () => {
  it("invites editors to add the first entity and mentions Shuttles", async () => {
    await openGraph({ role: "editor" });
    expect(screen.getByRole("heading", { name: GRAPH_EMPTY_MESSAGE })).toBeVisible();
    expect(screen.getByText(GRAPH_EMPTY_HINT)).toHaveTextContent("Shuttles");
    expect(screen.getByRole("button", { name: "Add your first entity" })).toBeVisible();
    expect(screen.queryByTestId("graph-canvas")).not.toBeInTheDocument();
    expect(screen.queryByText("Demo data")).not.toBeInTheDocument();
    expect(summary()).toHaveTextContent("0 entities, 0 relationships");
  });

  it("shows viewers no edit controls, with the reason", async () => {
    await openGraph({ role: "viewer" });
    expect(screen.getByRole("heading", { name: GRAPH_EMPTY_MESSAGE })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Add/ })).not.toBeInTheDocument();
    expect(screen.getByText(/You have view access to this case/)).toBeVisible();
  });
});

describe("showing the case graph", () => {
  it("maps the API graph onto the canvas with the shared palette", async () => {
    await openGraph({ graph: graphOf([apex, www, ip], [subdomain, resolves]) });
    expect(summary()).toHaveTextContent("3 entities, 2 relationships");
    const graph = (await sigma()).graph as unknown as NightloomGraph;
    expect(graph.order).toBe(3);
    expect(graph.size).toBe(2);
    expect(graph.getNodeAttribute(ip.key, "color")).toBe(ENTITY_TYPE_INFO.ip_address.color);
    expect(graph.getNodeAttribute(apex.key, "label")).toBe("example.org");
    expect(graph.getEdgeAttribute(subdomain.id, "label")).toBe("has subdomain");
    // The legend uses the same colors and labels.
    expect(screen.getByRole("button", { name: /IP address\s*1/ })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Case graph" })).toBeVisible();
    expect(screen.queryByText("Demo data")).not.toBeInTheDocument();
  });

  it("shows the selected entity's key, sensitivity, provenance and neighbours", async () => {
    const user = userEvent.setup();
    api.on("GET", entityPath(apex.key), () => json({ ...apex, neighbours: 7 }));
    await openGraph({ graph: graphOf([apex, www]) });
    await select(apex.key);

    const panel = within(details());
    expect(panel.getByRole("heading", { level: 2 })).toHaveTextContent("example.org");
    expect(panel.getByText(apex.key)).toBeVisible();
    expect(panel.getByText("Infrastructure")).toBeVisible();
    expect(await panel.findByTestId("neighbour-count")).toHaveTextContent("7");
    expect(panel.getByText("3")).toBeVisible(); // observations
    expect(panel.getByText("shuttle:dns@1.0.0")).toBeVisible();
    expect(panel.getByText("95%")).toBeVisible();
    expect(panel.getByText("Owner's main domain")).toBeVisible();
    const rating = panel.getByRole("button", { name: "B2" });
    expect(rating).toHaveAccessibleDescription(/B: Usually reliable.*2: Probably true/);
    expect(panel.getByRole("heading", { name: "Connections (1)" })).toBeVisible();

    await user.click(panel.getByRole("button", { name: "Copy key" }));
    expect(await navigator.clipboard.readText()).toBe(apex.key);
    expect(panel.getByText("Copied")).toBeVisible();
  });

  it("says when the neighbour count cannot be loaded", async () => {
    await openGraph({ graph: graphOf([apex, www]) });
    await select(www.key);
    expect(await within(details()).findByText("Unavailable")).toBeVisible();
  });

  it("warns when the API cut the graph off", async () => {
    await openGraph({ graph: graphOf([apex, www], [subdomain], true) });
    expect(screen.getByText(/larger than one view can show/)).toHaveTextContent("2,000");
  });

  it("shows an error and retries loading", async () => {
    let fail = true;
    serveCase(api);
    api.on("GET", `${path}/graph?limit=2000`, () => (fail ? json({}, 500) : json(graphOf([apex]))));
    renderApp(`${path}/graph`);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("1 entity, 0 relationships")).toBeVisible();
  });
});

describe("read-only graphs", () => {
  it.each([
    ["closed", "This case is closed, so its graph is read-only."],
    ["archived", "This case is archived, so its graph is read-only."],
  ] as const)("a %s case has no edit controls", async (status, message) => {
    api.on("GET", entityPath(apex.key), () => json({ ...apex, neighbours: 1 }));
    await openGraph({ overrides: { status }, graph: graphOf([apex, www]) });
    expect(screen.getByText(message, { exact: false })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Add entity" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add relationship" })).not.toBeInTheDocument();
    await select(apex.key);
    expect(within(details()).queryByRole("button", { name: "Delete entity" })).toBeNull();
  });

  it("viewers see the graph without edit controls", async () => {
    await openGraph({ role: "viewer", graph: graphOf([apex, www]) });
    expect(await screen.findByTestId("graph-canvas")).toBeInTheDocument();
    expect(screen.getByText(/You have view access/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Add entity" })).not.toBeInTheDocument();
  });
});

describe("adding entities", () => {
  it("adds the first entity, then selects and focuses it", async () => {
    const created = makeEntity("domain", "example.org");
    api.on("POST", `${path}/entities`, () => json(created, 201));
    api.on("GET", entityPath(created.key), () => json({ ...created, neighbours: 0 }));
    await openGraph();
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));

    const dialog = screen.getByRole("dialog", { name: "Add entity" });
    expect(within(dialog).getByRole("combobox", { name: "Type" })).toHaveFocus();
    const value = within(dialog).getByRole("textbox", { name: "Value" });
    expect(value).toHaveAttribute("placeholder", "e.g. www.example.org");
    expect(value).toHaveAccessibleDescription(/fully qualified domain name.*www\.example\.org/);
    await userEvent.type(value, "Example.ORG.");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Note/ }), "From the brief");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add entity" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(bodyOf("POST", `${path}/entities`)).toEqual({
      type: "domain",
      value: "Example.ORG.",
      note: "From the brief",
    });
    expect(api.callsTo("POST", `${path}/entities`)[0]?.headers["X-CSRF-Token"]).toBe("csrf-token");
    expect(summary()).toHaveTextContent("1 entity, 0 relationships");
    const instance = await sigma();
    await waitFor(() => {
      expect(within(details()).getByRole("heading", { level: 2 })).toHaveTextContent("example.org");
    });
    expect(instance.nodeView(created.key)).toMatchObject({ highlighted: true });
    // A small graph is shown whole once the new node has settled.
    await waitFor(() => {
      expect(instance.camera.reset).toHaveBeenCalled();
    });
  });

  it("places a new entity without rebuilding the graph", async () => {
    const created = makeEntity("ip_address", "192.0.2.1");
    api.on("POST", `${path}/entities`, () => json(created, 201));
    await openGraph({ graph: graphOf([apex, www]) });
    const instance = await sigma();
    const graph = instance.graph as unknown as NightloomGraph;

    await userEvent.click(screen.getByRole("button", { name: "Add entity" }));
    const dialog = screen.getByRole("dialog", { name: "Add entity" });
    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "Type" }),
      "ip_address",
    );
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Value" }), "192.0.2.1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add entity" }));

    expect(await screen.findByText("3 entities, 1 relationship")).toBeVisible();
    // Same sigma and graph instance: the node was added, not the graph rebuilt.
    expect(FakeSigma.latest()).toBe(instance);
    expect(graph.hasNode(created.key)).toBe(true);
    expect(Number.isFinite(graph.getNodeAttribute(created.key, "x"))).toBe(true);
  });

  it("says when the entity was already in the case", async () => {
    api.on("POST", `${path}/entities`, () => json(apex, 200));
    await openGraph({ graph: graphOf([apex, www]) });
    await userEvent.click(screen.getByRole("button", { name: "Add entity" }));
    const dialog = screen.getByRole("dialog", { name: "Add entity" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Value" }), "EXAMPLE.org");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add entity" }));
    expect(await screen.findByText(/was already in this case/)).toBeVisible();
    expect(summary()).toHaveTextContent("2 entities, 1 relationship");
  });

  it("requires a value before calling the API", async () => {
    await openGraph({ role: "editor" });
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));
    await userEvent.click(screen.getByRole("button", { name: "Add entity" }));
    expect(screen.getByText("Enter a value.")).toBeVisible();
    expect(api.callsTo("POST", `${path}/entities`)).toHaveLength(0);
  });

  it("shows the server's normalization error inline", async () => {
    api.on("POST", `${path}/entities`, () =>
      json({ detail: "A domain name needs at least two labels (e.g. example.org)." }, 422),
    );
    await openGraph();
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));
    const dialog = screen.getByRole("dialog", { name: "Add entity" });
    const value = within(dialog).getByRole("textbox", { name: "Value" });
    await userEvent.type(value, "localhost");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add entity" }));
    expect(await within(dialog).findByText(/needs at least two labels/)).toBeVisible();
    expect(value).toHaveAttribute("aria-invalid", "true");
    expect(value).toHaveFocus();
  });

  it("shows field errors from request validation on their fields", async () => {
    api.on("POST", `${path}/entities`, () =>
      json(
        {
          detail: [
            { loc: ["body", "phone_region"], msg: "String should match pattern", type: "x" },
          ],
        },
        422,
      ),
    );
    await openGraph({ overrides: { allow_person_entities: true } });
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));
    const dialog = screen.getByRole("dialog", { name: "Add entity" });
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Type" }), "phone");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Value" }), "020 7946 0958");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Phone region/ }), "gb");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add entity" }));
    expect(await within(dialog).findByText("String should match pattern")).toBeVisible();
    expect(bodyOf("POST", `${path}/entities`)).toEqual({
      type: "phone",
      value: "020 7946 0958",
      note: null,
      phone_region: "GB",
    });
  });

  it("checks the phone region locally", async () => {
    await openGraph({ overrides: { allow_person_entities: true } });
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));
    const dialog = screen.getByRole("dialog", { name: "Add entity" });
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Type" }), "phone");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Value" }), "020 7946 0958");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Phone region/ }), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add entity" }));
    expect(within(dialog).getByText(/two-letter country code, e.g. GB\./)).toBeVisible();
    expect(api.callsTo("POST", `${path}/entities`)).toHaveLength(0);
  });

  it("disables person-related types, with the reason, when the case does not allow them", async () => {
    await openGraph();
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));
    const type = screen.getByRole("combobox", { name: "Type" });
    const email = within(type).getByRole("option", { name: /^Email/ });
    expect(email).toBeDisabled();
    expect(email).toHaveTextContent("Email (not allowed in this case)");
    expect(within(type).getByRole("option", { name: "Domain" })).toBeEnabled();
    expect(within(type).getByRole("option", { name: "Organization" })).toBeEnabled();
    expect(type).toHaveAccessibleDescription(PERSON_TYPES_DISABLED_HINT);
    const groups = within(type)
      .getAllByRole("group")
      .map((g) => g.getAttribute("label"));
    expect(groups).toEqual(["Infrastructure", "Organization", "Person-related"]);
  });

  it("offers person-related types when the case allows them", async () => {
    await openGraph({ overrides: { allow_person_entities: true } });
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));
    const type = screen.getByRole("combobox", { name: "Type" });
    expect(within(type).getByRole("option", { name: "Email" })).toBeEnabled();
    expect(screen.queryByText(PERSON_TYPES_DISABLED_HINT)).not.toBeInTheDocument();
  });

  it.each([
    [403, { detail: "This case does not allow person-related entities." }, PERSON_ENTITIES_MESSAGE],
    [409, { detail: "This case is not open. Reopen it to change its graph." }, "not open"],
    [500, {}, "Something went wrong"],
  ])("shows a %s answer in the form", async (status, body, message) => {
    api.on("POST", `${path}/entities`, () => json(body, status));
    await openGraph({ overrides: { allow_person_entities: true } });
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));
    const dialog = screen.getByRole("dialog", { name: "Add entity" });
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Type" }), "email");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Value" }), "a@example.org");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add entity" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(message);
  });

  it("explains a 403 for a non-person type as a permission problem", async () => {
    api.on("POST", `${path}/entities`, () => json({ detail: "x" }, 403));
    await openGraph();
    await userEvent.click(screen.getByRole("button", { name: "Add your first entity" }));
    const dialog = screen.getByRole("dialog", { name: "Add entity" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Value" }), "example.org");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add entity" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "You don't have permission to do this.",
    );
  });

  it("closes the dialog with Escape and Cancel", async () => {
    await openGraph({ graph: graphOf([apex, www]) });
    await userEvent.click(screen.getByRole("button", { name: "Add entity" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add entity" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add entity" })).toHaveFocus();
  });
});

describe("adding relationships", () => {
  async function pick(name: "From" | "To", text: string, option: RegExp) {
    const dialog = screen.getByRole("dialog", { name: "Add relationship" });
    const input = within(dialog).getByRole("combobox", { name: name });
    await userEvent.clear(input);
    await userEvent.type(input, text);
    await userEvent.click(within(dialog).getByRole("option", { name: option }));
  }

  const relationOptions = () =>
    within(screen.getByRole("combobox", { name: "Relationship" }))
      .getAllByRole("option")
      .map((option) => option.textContent);

  it("offers only the types the schema allows for the pair, then adds one", async () => {
    const created = makeRelationship("RESOLVES_TO", apex, ip);
    api.on("POST", `${path}/relationships`, () => json(created, 201));
    api.on("GET", entityPath(apex.key), () => json({ ...apex, neighbours: 1 }));
    await openGraph({ graph: graphOf([apex, www, ip]) });
    await select(apex.key);
    await userEvent.click(screen.getByRole("button", { name: "Add relationship" }));

    const dialog = screen.getByRole("dialog", { name: "Add relationship" });
    expect(within(dialog).getByRole("combobox", { name: "From" })).toHaveValue("example.org");
    expect(within(dialog).getByRole("combobox", { name: "Relationship" })).toBeDisabled();

    await pick("To", "www", /^www\.example\.org/);
    expect(relationOptions()).toEqual([
      "has subdomain",
      "has mx",
      "has ns",
      "alias of",
      "related to (needs a note)",
    ]);

    await pick("To", "192.0", /^192\.0\.2\.1/);
    expect(relationOptions()).toEqual(["resolves to", "related to (needs a note)"]);
    expect(within(dialog).getByText("Types allowed from Domain to IP address.")).toBeVisible();

    await userEvent.click(within(dialog).getByRole("button", { name: "Add relationship" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(bodyOf("POST", `${path}/relationships`)).toEqual({
      type: "RESOLVES_TO",
      source_key: apex.key,
      target_key: ip.key,
      note: null,
    });
    expect(summary()).toHaveTextContent("3 entities, 2 relationships");
    // The selected entity's highlight includes its new neighbour.
    expect((await sigma()).nodeView(ip.key)).toMatchObject({ labelVisibility: "visible" });
  });

  it("swaps the direction and refilters", async () => {
    await openGraph({ graph: graphOf([apex, www, ip]) });
    await userEvent.click(screen.getByRole("button", { name: "Add relationship" }));
    await pick("From", "example", /^example\.org/);
    await pick("To", "192", /^192\.0\.2\.1/);
    await userEvent.click(screen.getByRole("button", { name: "Swap direction" }));
    expect(screen.getByRole("combobox", { name: "From" })).toHaveValue("192.0.2.1");
    expect(screen.getByRole("combobox", { name: "To" })).toHaveValue("example.org");
    expect(relationOptions()).toEqual(["has ptr", "related to (needs a note)"]);
  });

  it("requires a note for related to, and both ends", async () => {
    const created = makeRelationship("RELATED_TO", apex, www);
    api.on("POST", `${path}/relationships`, () => json(created, 201));
    await openGraph({ graph: graphOf([apex, www]) });
    await userEvent.click(screen.getByRole("button", { name: "Add relationship" }));
    const dialog = screen.getByRole("dialog", { name: "Add relationship" });

    await userEvent.click(within(dialog).getByRole("button", { name: "Add relationship" }));
    expect(
      within(dialog).getByText(/Choose the entity the relationship starts from/),
    ).toBeVisible();
    expect(within(dialog).getByText(/Choose the entity it points to/)).toBeVisible();

    await pick("From", "example", /^example\.org/);
    await pick("To", "example", /^example\.org/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Add relationship" }));
    expect(within(dialog).getByText("Choose two different entities.")).toBeVisible();

    await pick("To", "www", /^www\.example\.org/);
    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "Relationship" }),
      "RELATED_TO",
    );
    expect(within(dialog).getByRole("textbox", { name: "Note (required)" })).toBeVisible();
    await userEvent.click(within(dialog).getByRole("button", { name: "Add relationship" }));
    expect(within(dialog).getByText("related to needs a note explaining the link.")).toBeVisible();
    expect(api.callsTo("POST", `${path}/relationships`)).toHaveLength(0);

    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Note (required)" }),
      "Same owner per the brief",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Add relationship" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(bodyOf("POST", `${path}/relationships`)).toMatchObject({
      type: "RELATED_TO",
      note: "Same owner per the brief",
    });
  });

  it("navigates the entity picker with the keyboard", async () => {
    await openGraph({ graph: graphOf([apex, www, ip]) });
    await userEvent.click(screen.getByRole("button", { name: "Add relationship" }));
    const from = screen.getByRole("combobox", { name: "From" });
    await userEvent.click(from);
    expect(from).toHaveAttribute("aria-expanded", "true");
    await userEvent.type(from, "zzz");
    expect(screen.getByText("No matching entities")).toBeVisible();
    await userEvent.clear(from);
    await userEvent.type(from, "example{ArrowDown}{ArrowUp}{Enter}");
    expect(from).toHaveValue("example.org");
    expect(from).toHaveAccessibleDescription(/Domain: domain:example\.org/);
    // Escape closes the list first, the dialog only on a second press.
    await userEvent.type(from, "x");
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Add relationship" })).toBeVisible();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    [422, { detail: "HAS_SUBDOMAIN cannot connect a domain to a ip_address." }, "cannot connect"],
    [403, { detail: "x" }, PERSON_RELATIONSHIP_MESSAGE],
    [409, { detail: "This case is not open. Reopen it to change its graph." }, "not open"],
  ])("shows a %s answer in the form", async (status, body, message) => {
    const email = makeEntity("email", "info@example.org");
    api.on("POST", `${path}/relationships`, () => json(body, status));
    await openGraph({
      overrides: { allow_person_entities: true },
      graph: graphOf([apex, email], []),
    });
    await userEvent.click(screen.getByRole("button", { name: "Add relationship" }));
    await pick("From", "example.org", /^example\.org/);
    await pick("To", "info", /^info@example\.org/);
    expect(relationOptions()).toEqual(["has email", "related to (needs a note)"]);
    const dialog = screen.getByRole("dialog", { name: "Add relationship" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add relationship" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("dialog", { name: "Add relationship" })).toBeVisible();
  });

  it("is disabled until the case has two entities", async () => {
    await openGraph({ graph: graphOf([apex]) });
    expect(screen.getByRole("button", { name: "Add relationship" })).toBeDisabled();
  });
});

describe("deleting entities", () => {
  it("asks for confirmation, then removes the entity and its relationships", async () => {
    api.on("GET", entityPath(www.key), () => json({ ...www, neighbours: 2 }));
    api.on("DELETE", entityPath(www.key), () => noContent());
    await openGraph({ graph: graphOf([apex, www, ip], [subdomain, resolves]) });
    await select(www.key);
    await userEvent.click(within(details()).getByRole("button", { name: "Delete entity" }));

    const dialog = screen.getByRole("dialog", { name: "Delete entity?" });
    expect(dialog).toHaveAccessibleDescription(/“www\.example\.org” and all its relationships/);
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete entity" }));

    expect(await screen.findByText("2 entities, 0 relationships")).toBeVisible();
    expect(api.callsTo("DELETE", entityPath(www.key))).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Case graph" })).toBeVisible();
    const graph = (await sigma()).graph as unknown as NightloomGraph;
    expect(graph.hasNode(www.key)).toBe(false);
  });

  it("keeps the entity and shows why when the delete fails", async () => {
    api.on("DELETE", entityPath(www.key), () =>
      json({ detail: "This case is not open. Reopen it to change its graph." }, 409),
    );
    await openGraph({ graph: graphOf([apex, www]) });
    await select(www.key);
    await userEvent.click(within(details()).getByRole("button", { name: "Delete entity" }));
    const dialog = screen.getByRole("dialog", { name: "Delete entity?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete entity" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("not open");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(summary()).toHaveTextContent("2 entities, 1 relationship");
  });

  it("hides the delete button from viewers", async () => {
    await openGraph({ role: "viewer", graph: graphOf([apex, www]) });
    await select(www.key);
    expect(within(details()).queryByRole("button", { name: "Delete entity" })).toBeNull();
  });
});
