import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { downloader } from "../api/export";
import { resetSchemaCache, type ApiEntity, type ApiGraph } from "../api/graph";
import type { NightloomGraph } from "../graph/buildGraph";
import { groupId } from "../graph/groups";
import {
  CASE_ID,
  makeEntity,
  makeProvenance,
  makeRelationship,
  serveCase,
} from "../test/caseFixtures";
import { clearCookies, FakeApi, json } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { FakeSigma } from "../test/sigmaMock";
import { REDACTION_NOTE } from "./graph/exportText";

vi.mock("sigma", () => import("../test/sigmaMock"));

let api: FakeApi;
const path = `/cases/${CASE_ID}`;

const apex = makeEntity("domain", "example.org");
const www = makeEntity("domain", "www.example.org", {
  provenance: makeProvenance({ sources: ["shuttle:crtsh@1.0.0"], admiralty: "B2" }),
});
const ip = makeEntity("ip_address", "192.0.2.1", {
  provenance: makeProvenance({
    sources: ["shuttle:dns@1.0.0"],
    max_confidence: 0.4,
    admiralty: "C3",
  }),
});
const lonely = makeEntity("organization", "Example Corp", { sensitivity: "organization" });
const urls: ApiEntity[] = Array.from({ length: 30 }, (_, i) =>
  makeEntity("url", `https://www.example.org/page/${String(i)}`, {
    provenance: makeProvenance({ sources: ["shuttle:wayback@1.0.0"] }),
  }),
);
const URL_GROUP = groupId(www.key, "url");

const graph: ApiGraph = {
  nodes: [apex, www, ip, lonely, ...urls],
  edges: [
    makeRelationship("HAS_SUBDOMAIN", apex, www),
    makeRelationship("RESOLVES_TO", www, ip),
    ...urls.map((url) => makeRelationship("HOSTS", www, url)),
  ],
  truncated: false,
};

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
  resetSchemaCache();
});

afterEach(() => {
  FakeSigma.reset();
  clearCookies();
});

async function openGraph(query = "", role: "owner" | "editor" | "viewer" = "owner") {
  serveCase(api, { graph, role });
  const view = renderApp(`${path}/graph${query}`);
  await screen.findByTestId("graph-canvas");
  return view;
}

const sigma = () => FakeSigma.latest();
const sigmaGraph = () => sigma().graph as unknown as NightloomGraph;
const hidden = (node: string) => sigma().nodeView(node)["visibility"] === "hidden";
const search = (view: ReturnType<typeof renderApp>) => view.router.state.location.search;

async function openFilters() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /^Filters/ }));
  return { user, panel: within(screen.getByRole("region", { name: "Filters" })) };
}

describe("filters", () => {
  it("filters by source, keeps the state in the URL and clears it", async () => {
    const view = await openGraph();
    const { user, panel } = await openFilters();
    expect(panel.getByRole("status")).toHaveTextContent("Showing 34 of 34 entities");

    await user.click(panel.getByRole("checkbox", { name: /wayback/ }));

    expect(search(view)).toBe("?xsrc=wayback");
    expect(panel.getByRole("status")).toHaveTextContent(
      "Showing 4 of 34 entities · 1 filter active",
    );
    // The URLs are gone, so there is no group either.
    expect(sigmaGraph().hasNode(URL_GROUP)).toBe(false);
    expect(hidden(urls[0]!.key)).toBe(true);
    expect(hidden(www.key)).toBe(false);

    await user.click(panel.getByRole("button", { name: "Clear all" }));
    expect(search(view)).toBe("");
    expect(sigmaGraph().hasNode(URL_GROUP)).toBe(true);
  });

  it("reads filters from the URL (a shared link or a reload)", async () => {
    await openGraph("?conf=50&rel=B&isolated=hide");
    // ip: confidence 0.4, C3; lonely: no relationship.
    expect(hidden(ip.key)).toBe(true);
    expect(hidden(lonely.key)).toBe(true);
    expect(hidden(apex.key)).toBe(false);
    expect(sigma().edgeView(`${www.key}|RESOLVES_TO|${ip.key}`)).toEqual({ visibility: "hidden" });
    expect(screen.getByRole("button", { name: /^Filters/ })).toHaveTextContent("3");
    expect(screen.getByText(/Showing 32 of 34 entities · 3 filters active/)).toBeVisible();
  });

  it("hides types from the legend and records them in the URL", async () => {
    const user = userEvent.setup();
    const view = await openGraph();
    await user.click(screen.getByRole("button", { name: /IP address\s*1/ }));
    expect(search(view)).toBe("?hide=ip_address");
    expect(hidden(ip.key)).toBe(true);
  });

  it("sets time, rating, sensitivity and isolation filters from the panel", async () => {
    const view = await openGraph();
    const { user, panel } = await openFilters();
    await user.selectOptions(panel.getByRole("combobox", { name: "Time range" }), "7d");
    expect(search(view)).toContain("seen=7d");
    await user.selectOptions(panel.getByRole("combobox", { name: "Time range" }), "range");
    await user.type(panel.getByLabelText("From"), "2026-11-11");
    expect(search(view)).toContain("seen=2026-11-11%7E");
    await user.selectOptions(
      panel.getByRole("combobox", { name: /Minimum source reliability/ }),
      "B",
    );
    await user.selectOptions(
      panel.getByRole("combobox", { name: /Minimum information credibility/ }),
      "2",
    );
    await user.click(panel.getByRole("checkbox", { name: "Organization" }));
    await user.click(panel.getByRole("checkbox", { name: "Hide isolated entities" }));
    const params = new URLSearchParams(search(view));
    expect(params.get("rel")).toBe("B");
    expect(params.get("cred")).toBe("2");
    expect(params.get("xsens")).toBe("organization");
    expect(params.get("isolated")).toBe("hide");
    // The grades are explained with the schema's labels.
    expect(panel.getByRole("option", { name: /^B – / })).toBeInTheDocument();
    fireEvent.change(panel.getByRole("slider"), { target: { value: "85" } });
    expect(new URLSearchParams(search(view)).get("conf")).toBe("85");
    expect(panel.getByText("85%")).toBeVisible();
  });

  it("focuses on the selected entity within N hops", async () => {
    const view = await openGraph();
    const { user, panel } = await openFilters();
    expect(panel.getByRole("button", { name: "Focus on the selected entity" })).toBeDisabled();
    act(() => {
      sigma().emit("clickNode", { node: apex.key });
    });
    await user.click(panel.getByRole("button", { name: "Focus on the selected entity" }));
    expect(new URLSearchParams(search(view)).get("focus")).toBe(apex.key);
    expect(hidden(ip.key)).toBe(true);
    expect(hidden(www.key)).toBe(false);
    expect(panel.getByText(/Show only entities within/)).toHaveTextContent("example.org");

    await user.selectOptions(panel.getByRole("combobox", { name: "Hops" }), "2");
    expect(new URLSearchParams(search(view)).get("hops")).toBe("2");
    expect(hidden(ip.key)).toBe(false);

    await user.click(panel.getByRole("button", { name: "Stop focusing" }));
    expect(search(view)).toBe("");
  });

  it("shows the summary with Clear all while the panel is closed", async () => {
    const user = userEvent.setup();
    const view = await openGraph("?xsrc=dns");
    const status = screen.getByText(/Showing 33 of 34 entities · 1 filter active/);
    await user.click(within(status).getByRole("button", { name: "Clear all" }));
    expect(search(view)).toBe("");
    expect(screen.queryByText(/filter active/)).not.toBeInTheDocument();
  });
});

describe("collapsed groups", () => {
  it("draws more than 25 leaves of one type as one group node", async () => {
    await openGraph();
    const g = sigmaGraph();
    expect(g.hasNode(URL_GROUP)).toBe(true);
    expect(g.getNodeAttribute(URL_GROUP, "label")).toBe("30 URLs");
    expect(g.hasNode(urls[0]!.key)).toBe(false);
    expect(g.order).toBe(5);
  });

  it("expands on click and collapses again from the side panel", async () => {
    const user = userEvent.setup();
    await openGraph();
    act(() => {
      sigma().emit("clickNode", { node: URL_GROUP });
    });
    await waitFor(() => {
      expect(sigmaGraph().hasNode(urls[0]!.key)).toBe(true);
    });
    expect(sigmaGraph().hasNode(URL_GROUP)).toBe(false);

    act(() => {
      sigma().emit("clickNode", { node: urls[5]!.key });
    });
    const details = within(screen.getByRole("complementary", { name: "Details" }));
    const groups = within(details.getByRole("region", { name: "Groups" }));
    expect(groups.getByText("30 URLs")).toBeVisible();
    await user.click(groups.getByRole("button", { name: "Collapse" }));

    expect(sigmaGraph().hasNode(URL_GROUP)).toBe(true);
    expect(sigmaGraph().hasNode(urls[5]!.key)).toBe(false);
    // The parent is selected instead of the hidden member.
    expect(details.getByRole("heading", { level: 2 })).toHaveTextContent("www.example.org");
  });

  it("expands a group from the parent's connections", async () => {
    const user = userEvent.setup();
    await openGraph();
    act(() => {
      sigma().emit("clickNode", { node: www.key });
    });
    const details = within(screen.getByRole("complementary", { name: "Details" }));
    await user.click(details.getByRole("button", { name: "30 URLs" }));
    await waitFor(() => {
      expect(sigmaGraph().hasNode(urls[0]!.key)).toBe(true);
    });
    expect(details.getByRole("button", { name: "Collapse" })).toBeVisible();
  });

  it("finds a collapsed entity by search, expands its group and selects it", async () => {
    const user = userEvent.setup();
    await openGraph();
    await user.type(screen.getByRole("combobox", { name: "Search entities" }), "page/17");
    await user.click(screen.getByRole("option", { name: /page\/17/ }));

    await waitFor(() => {
      expect(sigmaGraph().hasNode(urls[17]!.key)).toBe(true);
    });
    const details = within(screen.getByRole("complementary", { name: "Details" }));
    await waitFor(() => {
      expect(details.getByRole("heading", { level: 2 })).toHaveTextContent(
        "https://www.example.org/page/17",
      );
    });
  });
});

describe("export", () => {
  const checkPath = (query: string) => `${path}/export/check?${query}`;
  const COUNTS = {
    entities: 34,
    person_entities: 0,
    relationships: 32,
    max_entities: 100000,
    include_person_data: false,
    exports_remaining: 9,
  };

  async function openExport(role: "owner" | "editor" | "viewer" = "owner") {
    const user = userEvent.setup();
    await openGraph("", role);
    await user.click(screen.getByRole("button", { name: "Export" }));
    return { user, dialog: within(screen.getByRole("dialog", { name: "Export case" })) };
  }

  it("downloads the chosen format through a normal link", async () => {
    const start = vi.spyOn(downloader, "start").mockImplementation(() => undefined);
    api.on("GET", checkPath("format=graphml"), () => json(COUNTS));
    const { user, dialog } = await openExport("viewer");
    expect(screen.getByRole("dialog", { name: "Export case" })).toHaveAccessibleDescription(
      REDACTION_NOTE,
    );
    expect(dialog.queryByText("Include person-related data")).not.toBeInTheDocument();

    await user.click(dialog.getByRole("radio", { name: /GraphML/ }));
    await user.click(dialog.getByRole("button", { name: "Download" }));

    await waitFor(() => {
      expect(start).toHaveBeenCalledWith(`/api/v1${path}/export?format=graphml`);
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Your GraphML export is downloading.")).toBeVisible();
    // A GET: no CSRF header.
    expect(api.callsTo("GET", checkPath("format=graphml"))[0]?.headers).not.toHaveProperty(
      "X-CSRF-Token",
    );
  });

  it("lets owners include person data with a reason", async () => {
    const start = vi.spyOn(downloader, "start").mockImplementation(() => undefined);
    const query = "format=csv&include_person_data=true&reason=For+the+owner%27s+review";
    api.on("GET", checkPath(query), () => json({ ...COUNTS, include_person_data: true }));
    const { user, dialog } = await openExport("owner");

    await user.click(dialog.getByRole("radio", { name: /CSV/ }));
    await user.click(dialog.getByRole("checkbox", { name: /Include person-related data/ }));
    await user.type(dialog.getByRole("textbox", { name: "Reason" }), "too short");
    await user.click(dialog.getByRole("button", { name: "Download" }));
    expect(dialog.getByText("Give a reason of at least 10 characters.")).toBeVisible();
    expect(start).not.toHaveBeenCalled();

    await user.clear(dialog.getByRole("textbox", { name: "Reason" }));
    await user.type(dialog.getByRole("textbox", { name: "Reason" }), "For the owner's review");
    await user.click(dialog.getByRole("button", { name: "Download" }));
    await waitFor(() => {
      expect(start).toHaveBeenCalledWith(`/api/v1${path}/export?${query}`);
    });
  });

  it.each([
    [
      413,
      { detail: "This case has 120,000 entities; exports are limited to 100,000." },
      {},
      "This case has 120,000 entities; exports are limited to 100,000.",
    ],
    [
      429,
      { detail: "You can start at most 10 exports every 10 minutes. Try again in 4 minutes." },
      { "Retry-After": "200" },
      "You can start at most 10 exports every 10 minutes. Try again in 4 minutes.",
    ],
    [429, {}, { "Retry-After": "61" }, "Too many exports. Try again in 2 minutes."],
    [403, { detail: "You do not have permission to do this." }, {}, /Only the case's owners/],
  ])("shows a %i inline", async (status, body, headers, message) => {
    const start = vi.spyOn(downloader, "start").mockImplementation(() => undefined);
    api.on("GET", checkPath("format=json"), () => json(body, status, headers));
    const { user, dialog } = await openExport("editor");
    await user.click(dialog.getByRole("button", { name: "Download" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent(message);
    expect(start).not.toHaveBeenCalled();
    expect(dialog.getByRole("button", { name: "Download" })).toBeEnabled();
  });

  it("closes on Cancel", async () => {
    const { user, dialog } = await openExport();
    await user.click(dialog.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
