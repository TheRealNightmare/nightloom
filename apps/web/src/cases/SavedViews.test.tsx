import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSchemaCache, type ApiEntity, type ApiGraph } from "../api/graph";
import type { ApiViewState, SavedView, ViewSummary } from "../api/views";
import type { NightloomGraph } from "../graph/buildGraph";
import { groupId } from "../graph/groups";
import {
  CASE_ID,
  makeEntity,
  makeRelationship,
  serveCase,
  VIEWER_USER,
} from "../test/caseFixtures";
import { clearCookies, FakeApi, json, noContent } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";
import { FakeSigma, waitForSigma } from "../test/sigmaMock";
import { makeSummary, makeView, OTHER_AUTHOR } from "../test/viewFixtures";
import {
  EMPTY_VIEWS,
  EMPTY_VIEWS_HINT,
  TOAST_MS,
  TOO_MANY_CHANGES,
  VIEW_UNAVAILABLE,
} from "./graph/SavedViews";

vi.mock("sigma", () => import("../test/sigmaMock"));

let api: FakeApi;
const path = `/cases/${CASE_ID}`;
const viewsPath = `${path}/views`;
const layoutPath = `${path}/layout`;

const apex = makeEntity("domain", "example.org");
const www = makeEntity("domain", "www.example.org");
const ip = makeEntity("ip_address", "192.0.2.1");
const urls: ApiEntity[] = Array.from({ length: 30 }, (_, i) =>
  makeEntity("url", `https://www.example.org/page/${String(i)}`),
);
const URL_GROUP = groupId(www.key, "url");

const graph: ApiGraph = {
  nodes: [apex, www, ip, ...urls],
  edges: [
    makeRelationship("HAS_SUBDOMAIN", apex, www),
    makeRelationship("RESOLVES_TO", www, ip),
    ...urls.map((url) => makeRelationship("HOSTS", www, url)),
  ],
  truncated: false,
};

/** Hides IP addresses, expands the URL group, pins the apex (and an entity the case lost). */
const VIEW_STATE: ApiViewState = {
  version: 1,
  mode: "2d",
  camera: { x: 40, y: -20, ratio: 0.5, angle: 0.75 },
  filters: {
    hidden_types: ["ip_address"],
    hidden_sources: [],
    hidden_sensitivities: [],
    seen: { kind: "all" },
    min_confidence: 0,
    min_reliability: null,
    min_credibility: null,
    focus: null,
    hide_isolated: false,
  },
  groups: { expanded: [URL_GROUP] },
  positions: { [apex.key]: [5, 6], "domain:gone.example": [1, 1] },
};

const MINE = makeSummary();
const THEIRS = makeSummary({
  id: "7a9b0c1d-2e3f-4a5b-8c6d-7e8f9a0b1c2d",
  name: "Hosts",
  created_by: OTHER_AUTHOR,
  updated_by: OTHER_AUTHOR,
});

interface Setup {
  role?: "owner" | "editor" | "viewer";
  status?: "open" | "closed";
  views?: ViewSummary[];
  query?: string;
  full?: SavedView;
}

let layoutBodies: unknown[];

async function openGraph({
  role = "owner",
  status = "open",
  views = [MINE, THEIRS],
  query = "",
  full = makeView({ state: VIEW_STATE }),
}: Setup = {}) {
  serveCase(api, {
    graph,
    role,
    overrides: { status, ...(status === "closed" ? { closed_at: "2026-11-20T00:00:00Z" } : {}) },
    ...(role === "viewer" ? { user: VIEWER_USER } : {}),
  });
  layoutBodies = [];
  api
    .on("GET", layoutPath, () => json({ positions: {}, updated_at: null }))
    .on("PUT", layoutPath, (init) => {
      layoutBodies.push(JSON.parse(init.body as string));
      return noContent();
    })
    .on("GET", viewsPath, () => json(views))
    .on("GET", `${viewsPath}/${full.id}`, () => json(full));
  const view = renderApp(`${path}/graph${query}`);
  await waitForSigma();
  return view;
}

const sigma = () => FakeSigma.latest();
const sigmaGraph = () => sigma().graph as unknown as NightloomGraph;
const hidden = (node: string) => sigma().nodeView(node)["visibility"] === "hidden";
const viewsButton = () => screen.getByRole("button", { name: "Views" });
const announcer = () => screen.getByTestId("views-announcer");

async function openMenu(user = userEvent.setup()) {
  await user.click(viewsButton());
  const menu = await screen.findByRole("menu", { name: "Saved views" });
  // Loaded (the list replaces the loading message).
  await waitFor(() => {
    expect(screen.queryByText("Loading saved views…")).not.toBeInTheDocument();
  });
  return { user, menu };
}

beforeEach(() => {
  api = new FakeApi().install();
  document.cookie = "nightloom_csrf=csrf-token";
  resetSchemaCache();
});

afterEach(() => {
  FakeSigma.reset();
  clearCookies();
  vi.useRealTimers();
});

describe("the list", () => {
  it("shows each view with its author and age, by name", async () => {
    await openGraph();
    const { menu } = await openMenu();
    const items = within(menu).getAllByRole("menuitem", { name: /^Apply / });
    expect(items.map((item) => item.getAttribute("aria-label"))).toEqual([
      "Apply Overview",
      "Apply Hosts",
    ]);
    expect(within(items[0]!).getByText(/^by Ada Analyst ·/)).toBeVisible();
    expect(within(items[1]!).getByText(/^by Bob Builder ·/)).toBeVisible();
    expect(api.callsTo("GET", viewsPath)).toHaveLength(1);
  });

  it("names a deleted author", async () => {
    await openGraph({ views: [makeSummary({ created_by: null })] });
    await openMenu();
    expect(screen.getByText(/^by a former member ·/)).toBeVisible();
  });

  it("has an empty state that invites editors to save one", async () => {
    await openGraph({ views: [] });
    await openMenu();
    expect(screen.getByText(`${EMPTY_VIEWS} ${EMPTY_VIEWS_HINT}`)).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "Save current view…" })).toHaveFocus();
  });

  it("shows viewers the empty state without the invitation", async () => {
    await openGraph({ role: "viewer", views: [] });
    await openMenu();
    expect(screen.getByText(EMPTY_VIEWS)).toBeVisible();
    expect(screen.queryByText(new RegExp(EMPTY_VIEWS_HINT))).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
  });

  it("reports a list that cannot be loaded", async () => {
    await openGraph();
    api.on("GET", viewsPath, () => json({ detail: "boom" }, 500));
    await userEvent.setup().click(viewsButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(/Something went wrong/);
  });
});

describe("permissions", () => {
  it("lets owners change every view", async () => {
    await openGraph({ role: "owner" });
    await openMenu();
    expect(screen.getByRole("menuitem", { name: "More actions for Overview" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "More actions for Hosts" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "Save current view…" })).toBeEnabled();
  });

  it("lets editors change only their own views", async () => {
    await openGraph({ role: "editor" });
    await openMenu();
    expect(screen.getByRole("menuitem", { name: "More actions for Overview" })).toBeVisible();
    expect(
      screen.queryByRole("menuitem", { name: "More actions for Hosts" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Save current view…" })).toBeVisible();
  });

  it("gives viewers Apply only", async () => {
    await openGraph({
      role: "viewer",
      views: [makeSummary({ created_by: { id: VIEWER_USER.id, display_name: "Me" } })],
    });
    await openMenu();
    expect(screen.getByRole("menuitem", { name: "Apply Overview" })).toBeVisible();
    expect(screen.queryByRole("menuitem", { name: /More actions/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Save current view…" })).not.toBeInTheDocument();
  });

  it("gives everyone Apply only while the case is closed", async () => {
    await openGraph({ role: "owner", status: "closed" });
    await openMenu();
    expect(screen.getAllByRole("menuitem")).toHaveLength(2);
    expect(screen.queryByRole("menuitem", { name: /More actions/ })).not.toBeInTheDocument();
  });
});

describe("applying", () => {
  it("sets the filters, groups, pins and camera, and can be undone", async () => {
    const view = await openGraph();
    // Something of the user's own to come back to.
    act(() => {
      sigma().camera.setState({ x: 3, y: 4, ratio: 1, angle: 0 });
    });
    const { user } = await openMenu();
    expect(sigmaGraph().hasNode(URL_GROUP)).toBe(true);

    await user.click(screen.getByRole("menuitem", { name: "Apply Overview" }));

    // Filters go through the URL, like any filter change.
    await waitFor(() => {
      expect(view.router.state.location.search).toBe("?hide=ip_address");
      expect(hidden(ip.key)).toBe(true);
    });
    // The URL group is expanded: its members are drawn instead.
    expect(sigmaGraph().hasNode(URL_GROUP)).toBe(false);
    expect(sigmaGraph().hasNode(urls[0]!.key)).toBe(true);
    // The view's pins replace the user's; an entity the case lost is skipped.
    expect(sigmaGraph().getNodeAttributes(apex.key)).toMatchObject({ x: 5, y: 6, pinned: true });
    expect(sigma().camera.getState()).toEqual({ x: 40, y: -20, ratio: 0.5, angle: 0.75 });
    // The menu closed and gave the focus back.
    expect(screen.queryByRole("menu", { name: "Saved views" })).not.toBeInTheDocument();
    expect(viewsButton()).toHaveFocus();
    const toast = screen.getByText("Applied “Overview”").parentElement!;
    expect(toast).toHaveAttribute("role", "status");
    await waitFor(() => {
      expect(announcer()).toHaveTextContent("Applied view “Overview”.");
    });
    // Saved as the user's own layout (the U3 path), debounced.
    await waitFor(
      () => {
        expect(layoutBodies).toEqual([{ positions: { [apex.key]: [5, 6] } }]);
      },
      { timeout: 3000 },
    );

    await user.click(within(toast).getByRole("button", { name: "Undo" }));

    await waitFor(() => {
      expect(view.router.state.location.search).toBe("");
      expect(hidden(ip.key)).toBe(false);
    });
    expect(sigmaGraph().hasNode(URL_GROUP)).toBe(true);
    expect(sigmaGraph().getNodeAttribute(apex.key, "pinned")).toBeUndefined();
    const camera = sigma().camera.getState();
    expect(camera.x).toBeCloseTo(3);
    expect(camera.y).toBeCloseTo(4);
    expect(camera.ratio).toBeCloseTo(1);
    expect(screen.queryByText("Applied “Overview”")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(announcer()).toHaveTextContent("Undone: the view from before “Overview” is back.");
    });
    await waitFor(
      () => {
        expect(layoutBodies.at(-1)).toEqual({ positions: {} });
      },
      { timeout: 3000 },
    );
  });

  it("moves the camera at once with reduced motion", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("reduce"),
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    try {
      await openGraph();
      const { user } = await openMenu();
      await user.click(screen.getByRole("menuitem", { name: "Apply Overview" }));
      await waitFor(() => {
        expect(sigma().camera.setState).toHaveBeenCalledWith({
          x: 40,
          y: -20,
          ratio: 0.5,
          angle: 0.75,
        });
      });
      expect(sigma().camera.animate).not.toHaveBeenCalledWith(
        expect.objectContaining({ x: 40 }),
        expect.anything(),
      );
    } finally {
      vi.unstubAllGlobals();
      api.install();
    }
  });

  it("says when a view is gone", async () => {
    await openGraph();
    api.on("GET", `${viewsPath}/${MINE.id}`, () => json({ detail: "View not found." }, 404));
    const { user } = await openMenu();
    await user.click(screen.getByRole("menuitem", { name: "Apply Overview" }));
    expect(await screen.findByText("View not found.")).toBeVisible();
  });

  it("keeps Undo until dismissed; plain messages go away by themselves", async () => {
    await openGraph();
    const { user } = await openMenu();
    await user.click(screen.getByRole("menuitem", { name: "Apply Overview" }));
    await screen.findByText("Applied “Overview”");
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Applied “Overview”")).not.toBeInTheDocument();

    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.on("GET", `${viewsPath}/${MINE.id}`, () => json({ detail: "View not found." }, 404));
    fireEvent.click(viewsButton());
    fireEvent.click(await screen.findByRole("menuitem", { name: "Apply Overview" }));
    expect(await screen.findByText("View not found.")).toBeVisible();
    // Let the message's timer start (an effect), then run past it.
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(TOAST_MS + 1);
    });
    expect(screen.queryByText("View not found.")).not.toBeInTheDocument();
  });
});

describe("saving", () => {
  it("validates the name, shows the server's 409, then saves what is on screen", async () => {
    await openGraph({ views: [] });
    const { user } = await openMenu();
    await user.click(screen.getByRole("menuitem", { name: "Save current view…" }));
    const dialog = screen.getByRole("dialog", { name: "Save current view" });
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    expect(name).toHaveFocus();

    await user.click(within(dialog).getByRole("button", { name: "Save view" }));
    expect(within(dialog).getByText("Enter a name.")).toBeVisible();
    expect(name).toHaveAttribute("aria-invalid", "true");
    await user.type(name, "x".repeat(81));
    expect(within(dialog).getByText("Use at most 80 characters.")).toBeVisible();
    expect(within(dialog).getByText("81 / 80")).toBeVisible();

    api.on("POST", viewsPath, () =>
      json({ detail: "A view with this name already exists in this case." }, 409),
    );
    await user.clear(name);
    await user.type(name, "  Overview ");
    await user.click(within(dialog).getByRole("button", { name: "Save view" }));
    expect(
      await within(dialog).findByText("A view with this name already exists in this case."),
    ).toBeVisible();

    act(() => {
      sigma().camera.setState({ x: 7, y: 8, ratio: 2, angle: 0.5 });
    });
    const saved = makeView({ name: "Overview" });
    api
      .on("POST", viewsPath, () => json(saved, 201))
      .on("GET", viewsPath, () => json([makeSummary()]));
    await user.click(within(dialog).getByRole("button", { name: "Save view" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    const body = JSON.parse(api.callsTo("POST", viewsPath).at(-1)?.init.body as string) as {
      name: string;
      state: ApiViewState;
    };
    expect(body.name).toBe("Overview");
    expect(body.state.camera).toEqual({ x: 7, y: 8, ratio: 2, angle: 0.5 });
    expect(body.state.filters.hidden_types).toEqual([]);
    expect(body.state.groups).toEqual({ expanded: [] });
    expect(body.state.positions).toEqual({});
    await waitFor(() => {
      expect(announcer()).toHaveTextContent("Saved view “Overview”.");
    });
    expect(viewsButton()).toHaveFocus();
  });

  it("round-trips: what is saved applies back to the same camera, filters, groups and pins", async () => {
    await openGraph({ views: [] });
    const { user } = await openMenu();
    // Apply a view, then save what is on screen: the same state comes out.
    let posted: { name: string; state: ApiViewState } | null = null;
    api.on("POST", viewsPath, (init) => {
      posted = JSON.parse(init.body as string) as { name: string; state: ApiViewState };
      return json(makeView({ name: posted.name, state: posted.state }), 201);
    });
    await user.keyboard("{Escape}");

    // Bring the view's state to the screen…
    api.on("GET", viewsPath, () => json([MINE]));
    await user.click(viewsButton());
    await user.click(await screen.findByRole("menuitem", { name: "Apply Overview" }));
    await screen.findByText("Applied “Overview”");

    // …and save it again.
    await user.click(viewsButton());
    await user.click(await screen.findByRole("menuitem", { name: "Save current view…" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Copy");
    await user.click(screen.getByRole("button", { name: "Save view" }));
    await waitFor(() => {
      expect(posted).not.toBeNull();
    });
    const state = posted!.state;
    expect(state.camera.x).toBeCloseTo(VIEW_STATE.camera.x);
    expect(state.camera.y).toBeCloseTo(VIEW_STATE.camera.y);
    expect(state.camera.ratio).toBeCloseTo(VIEW_STATE.camera.ratio);
    expect(state.camera.angle).toBeCloseTo(VIEW_STATE.camera.angle);
    expect(state.filters).toEqual(VIEW_STATE.filters);
    expect(state.groups).toEqual(VIEW_STATE.groups);
    expect(state.positions).toEqual({ [apex.key]: [5, 6] });
  });

  it("explains the write limit", async () => {
    await openGraph({ views: [] });
    const { user } = await openMenu();
    await user.click(screen.getByRole("menuitem", { name: "Save current view…" }));
    api.on("POST", viewsPath, () => json({ detail: "Too many." }, 429, { "Retry-After": "30" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Overview");
    await user.click(screen.getByRole("button", { name: "Save view" }));
    expect(await screen.findByText(TOO_MANY_CHANGES)).toBeVisible();
  });
});

describe("changing a view", () => {
  async function openActions(name = "Overview") {
    const { user } = await openMenu();
    await user.click(screen.getByRole("menuitem", { name: `More actions for ${name}` }));
    return { user, actions: screen.getByRole("menu", { name: `Actions for ${name}` }) };
  }

  it("renames, showing a taken name inline", async () => {
    await openGraph();
    const { user, actions } = await openActions();
    await user.click(within(actions).getByRole("menuitem", { name: "Rename…" }));
    const dialog = screen.getByRole("dialog", { name: "Rename view" });
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    expect(name).toHaveValue("Overview");

    api.on("PATCH", `${viewsPath}/${MINE.id}`, () =>
      json({ detail: "A view with this name already exists in this case." }, 409),
    );
    await user.clear(name);
    await user.type(name, "Hosts");
    await user.click(within(dialog).getByRole("button", { name: "Rename" }));
    expect(await within(dialog).findByText(/already exists/)).toBeVisible();

    api.on("PATCH", `${viewsPath}/${MINE.id}`, () => json(makeView({ name: "Infrastructure" })));
    await user.clear(name);
    await user.type(name, "Infrastructure");
    await user.click(within(dialog).getByRole("button", { name: "Rename" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(
      JSON.parse(api.callsTo("PATCH", `${viewsPath}/${MINE.id}`).at(-1)?.init.body as string),
    ).toEqual({ name: "Infrastructure" });
  });

  it("updates a view with what is on screen", async () => {
    await openGraph();
    act(() => {
      sigma().camera.setState({ x: 1, y: 1, ratio: 1, angle: 0 });
    });
    api.on("PATCH", `${viewsPath}/${MINE.id}`, () => json(makeView()));
    const { user, actions } = await openActions();
    await user.click(within(actions).getByRole("menuitem", { name: "Update with current view" }));
    expect(await screen.findByText("Updated “Overview” with the current view.")).toBeVisible();
    const body = JSON.parse(
      api.callsTo("PATCH", `${viewsPath}/${MINE.id}`)[0]?.init.body as string,
    ) as { state: ApiViewState };
    expect(Object.keys(body)).toEqual(["state"]);
    expect(body.state.camera).toEqual({ x: 1, y: 1, ratio: 1, angle: 0 });
  });

  it("copies an absolute link that opens the view", async () => {
    await openGraph();
    const { user, actions } = await openActions();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    await user.click(within(actions).getByRole("menuitem", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}${window.location.pathname}?view=${MINE.id}`,
    );
    expect(await screen.findByText("Link to “Overview” copied.")).toBeVisible();
  });

  it("shows the link when it cannot be copied", async () => {
    await openGraph();
    const { user, actions } = await openActions();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    await user.click(within(actions).getByRole("menuitem", { name: "Copy link" }));
    expect(
      await screen.findByText(/Couldn't copy the link\. Copy it from here: http/),
    ).toBeVisible();
  });

  it("deletes after a confirmation that names the view", async () => {
    await openGraph();
    const { user, actions } = await openActions("Hosts");
    await user.click(within(actions).getByRole("menuitem", { name: "Delete…" }));
    const dialog = screen.getByRole("dialog", { name: "Delete “Hosts”?" });
    // Cancel is the safe default.
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();

    api.on("DELETE", `${viewsPath}/${THEIRS.id}`, () => json({ detail: "boom" }, 500));
    await user.click(within(dialog).getByRole("button", { name: "Delete view" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/Something went wrong/);

    api
      .on("DELETE", `${viewsPath}/${THEIRS.id}`, () => noContent())
      .on("GET", viewsPath, () => json([MINE]));
    await user.click(within(dialog).getByRole("button", { name: "Delete view" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(api.callsTo("DELETE", `${viewsPath}/${THEIRS.id}`)).toHaveLength(2);
    await waitFor(() => {
      expect(announcer()).toHaveTextContent("Deleted view “Hosts”.");
    });
  });
});

describe("keyboard", () => {
  it("opens with V on the graph, moves with arrows and closes with Escape", async () => {
    await openGraph();
    const canvas = screen.getByTestId("graph-canvas");
    canvas.focus();
    fireEvent.keyDown(canvas, { key: "v" });
    const apply = await screen.findByRole("menuitem", { name: "Apply Overview" });
    await waitFor(() => {
      expect(apply).toHaveFocus();
    });
    const user = userEvent.setup();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "More actions for Overview" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(await screen.findByRole("menu", { name: "Actions for Overview" })).toBeVisible();
    await waitFor(() => {
      expect(screen.getByRole("menuitem", { name: "Update with current view" })).toHaveFocus();
    });
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Rename…" })).toHaveFocus();
    // Escape (or ArrowLeft) closes the submenu first.
    await user.keyboard("{ArrowLeft}");
    expect(screen.queryByRole("menu", { name: "Actions for Overview" })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "More actions for Overview" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    await screen.findByRole("menu", { name: "Actions for Overview" });
    await user.keyboard("{Escape}");
    expect(screen.getByRole("menuitem", { name: "More actions for Overview" })).toHaveFocus();

    await user.keyboard("{End}");
    expect(screen.getByRole("menuitem", { name: "Save current view…" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Apply Overview" })).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Save current view…" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(screen.getByRole("menuitem", { name: "Apply Overview" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "Saved views" })).not.toBeInTheDocument();
    expect(viewsButton()).toHaveFocus();
  });

  it("applies with Enter", async () => {
    const view = await openGraph();
    const user = userEvent.setup();
    viewsButton().focus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.getByRole("menuitem", { name: "Apply Overview" })).toHaveFocus();
    });
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(view.router.state.location.search).toBe("?hide=ip_address");
    });
  });

  it("closes when the focus or a click leaves it", async () => {
    await openGraph();
    const { user } = await openMenu();
    await user.keyboard("{Tab}");
    expect(screen.queryByRole("menu", { name: "Saved views" })).not.toBeInTheDocument();
    await user.click(viewsButton());
    await screen.findByRole("menu", { name: "Saved views" });
    await user.click(screen.getByTestId("graph-canvas"));
    expect(screen.queryByRole("menu", { name: "Saved views" })).not.toBeInTheDocument();
    // The button toggles it too.
    await user.click(viewsButton());
    await user.click(viewsButton());
    expect(screen.queryByRole("menu", { name: "Saved views" })).not.toBeInTheDocument();
  });

  it("is listed in the shortcuts", async () => {
    await openGraph();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Keyboard and mouse shortcuts" }));
    const dialog = screen.getByRole("dialog", { name: "Graph controls" });
    expect(within(dialog).getByText("Open the saved views of the case")).toBeVisible();
  });
});

describe("deep links", () => {
  it("apply ?view= once the graph is drawn, then leave the URL", async () => {
    const view = await openGraph({ query: `?view=${MINE.id}` });
    await waitFor(() => {
      expect(view.router.state.location.search).toBe("?hide=ip_address");
    });
    expect(sigma().camera.getState()).toEqual({ x: 40, y: -20, ratio: 0.5, angle: 0.75 });
    expect(sigmaGraph().getNodeAttributes(apex.key)).toMatchObject({ x: 5, y: 6, pinned: true });
    expect(screen.getByText("Applied “Overview”")).toBeVisible();
    // Replaced, not pushed: Back does not bring the link back.
    expect(view.router.state.historyAction).toBe("REPLACE");
    expect(api.callsTo("GET", `${viewsPath}/${MINE.id}`)).toHaveLength(1);
  });

  it("say quietly when the view is not there", async () => {
    const view = await openGraph({ query: "?view=00000000-0000-4000-8000-000000000000" });
    expect(await screen.findByText(VIEW_UNAVAILABLE)).toBeVisible();
    expect(view.router.state.location.search).toBe("");
    // Nothing else changed.
    expect(sigmaGraph().hasNode(URL_GROUP)).toBe(true);
  });

  it("are applied for viewers too", async () => {
    const view = await openGraph({ role: "viewer", query: `?view=${MINE.id}` });
    await waitFor(() => {
      expect(view.router.state.location.search).toBe("?hide=ip_address");
    });
  });
});
