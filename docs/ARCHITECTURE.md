# Architecture

> **Status: planned.** Nothing here is implemented yet. This document describes the intended design and will change as the code lands.

## Components

```mermaid
flowchart LR
    web["web<br/>React + Vite + Sigma.js"] -->|HTTPS / JSON| api["api<br/>FastAPI"]

    api --> pg[("PostgreSQL<br/>users, cases, jobs, audit log")]
    api --> neo[("Neo4j<br/>investigation graph")]
    api --> s3[("S3 object storage<br/>SeaweedFS by default<br/>evidence files")]
    api -->|enqueue jobs| valkey[("Valkey<br/>broker + cache")]
    api --> llm["LLM providers<br/>via LiteLLM"]

    valkey -->|consume jobs| worker["worker<br/>Celery"]
    worker --> shuttles["Shuttles<br/>(plugins)"]
    shuttles -->|public sources| internet(("Public data<br/>sources"))
    worker -->|entities + edges| neo
    worker -->|evidence| s3
    worker -->|job status| pg
```

| Component | Role |
| --- | --- |
| `apps/web` | React UI: graph view, case management, provenance panel |
| `apps/api` | FastAPI: auth, cases, graph queries, job submission, AI endpoints |
| `apps/worker` | Celery worker that runs Shuttle jobs (see [Jobs](#jobs-and-the-worker)); auto-pivot scans later |
| `packages/core` | Shared entity model (`nightloom-core`): entity types, canonical keys, relationship allow-list, provenance |
| `packages/sdk` | Shuttle SDK (`nightloom-sdk`): the [Shuttle contract](#shuttle-contract), the guarded HTTP client, the registry, test helpers |
| `shuttles/` | Built-in Shuttles, one package each: `registrable-domain` (no network), [`dns`](#the-dns-shuttle) and [`reverse-dns`](#the-reverse-dns-shuttle) (one package), [`crtsh`](#the-crtsh-shuttle), [`rdap`](#the-rdap-shuttle), [`ripestat`](#the-ripestat-shuttle), [`internetdb`](#the-internetdb-shuttle) and [`wayback`](#the-wayback-shuttle) |
| PostgreSQL | Users, cases, jobs, audit log, encrypted API keys |
| Neo4j | Entities and relationships |
| Valkey (Redis-compatible) | Celery broker, rate-limit counters, result cache |
| S3-compatible object storage (SeaweedFS by default) | Evidence files (screenshots, raw responses, documents) |

### Infrastructure choices

The local stack is defined in [`deploy/compose.yaml`](../deploy/compose.yaml) (see [Running the stack](../CONTRIBUTING.md#running-the-stack)).

- **Object storage is accessed only through the S3 API**, so any S3-compatible service works. The stack ships [SeaweedFS](https://github.com/seaweedfs/seaweedfs) (Apache-2.0) as the default. MinIO was the original plan, but its community edition no longer publishes Docker images and has been archived.
- **Valkey instead of Redis.** [Valkey](https://valkey.io/) is a BSD-licensed, Redis-compatible fork. Redis itself is no longer under a permissive open-source license. Clients talk the Redis protocol, so standard Redis libraries work unchanged.
- **Neo4j Community Edition** (GPLv3) runs as a separate server that Nightloom talks to over Bolt. It is not bundled into Nightloom's code.

### Web UI

[`apps/web`](../apps/web/) is a single-page app built with React, Vite and TypeScript (strict). The graph is a [graphology](https://graphology.github.io/) graph rendered by [Sigma.js](https://www.sigmajs.org/) (WebGL), laid out with ForceAtlas2 for a bounded number of iterations so the page never blocks on layout.

- **Same-origin API.** The UI only calls relative `/api/...` URLs. In development the Vite dev server proxies them to `http://127.0.0.1:8000`; in the Compose stack the `web` container (unprivileged nginx) serves the built files and proxies `/api/` to `http://api:8000`. No CORS configuration is needed.
- **Static hosting.** nginx falls back to `index.html` for client-side routes, caches the content-hashed `/assets/` for a year, revalidates `index.html` on every load, and sends a strict Content Security Policy (same origin only, plus `blob:` for workers and images) with other security headers.
- **API status.** Once signed in, the top bar polls `GET /api/v1/ready` every 15 seconds and shows *Ready*, *Degraded* (listing the failing checks) or *API offline*.
- **Sign-in.** See [Web authentication flow](#web-authentication-flow).
- **Routes.** [React Router](https://reactrouter.com/) in data mode ([`src/app/routes.tsx`](../apps/web/src/app/routes.tsx)). Every route sits under the auth gate, so a signed-out visitor sees the sign-in screen at the address they opened and stays there after signing in. nginx serves `index.html` for these paths, so deep links and reloads work.

  | Path | Page |
  | --- | --- |
  | `/` | redirects to `/cases` |
  | `/cases` | your cases: title search (debounced), status filter, *Load more* (keyset cursor); *New case* for admins and analysts |
  | `/cases/new` | create a case: purpose, legal basis (+ note), person entities (+ reason), retention, ethics confirmation; checked locally with the server's rules, and the server's `422` messages are shown on their fields |
  | `/cases/:caseId` | redirects to the `graph` tab; the header shows title (renamed inline by editors and owners), status, legal basis, your role and the purpose |
  | `/cases/:caseId/graph` | the case's graph from the API (never demo data): explore it; editors of an open case add entities and relationships by hand, delete entities and run Shuttles on an entity; everyone follows the case's jobs; see [Graph view](#graph-view) |
  | `/cases/:caseId/members` | members; owners add by email, change roles and remove; anyone can leave |
  | `/cases/:caseId/activity` | the case's audit events with readable labels (owners and editors) |
  | `/cases/:caseId/settings` | purpose, legal basis, person entities, retention (a dialog asks for the change reason where the API needs one), close / reopen / archive, delete by typing the title (owners) |
  | `/admin/users` | admins only: every account (search, role and status filters); *New user* with a generated temporary password shown once; per row change the role, deactivate or activate, revoke sessions, reset the password (see [Temporary passwords](#temporary-passwords-and-the-forced-change)) |
  | `/admin/audit` | admins only: the [audit log](#audit-log) with readable labels (case events reuse the *Activity* tab's wording), filters (action, outcome, actor, case ID, a range of whole UTC days), *Load more*, a details drawer per event (sanitized `details` as JSON, request ID, IP) and *Verify chain* |
  | `/demo` | the demo graph (loaded on demand, so sigma is not in the main bundle) |
  | anything else | "Page not found" |

  *Users* and *Audit log* appear in the top bar only for global admins; anyone else who opens `/admin/...` gets "Page not found", and the API refuses them anyway.

  Tabs a role cannot use are hidden, mirroring the API's [capability table](#cases); opening one directly shows "You don't have permission". A case that does not exist and one you are not a member of both show the same "Case not found" page, as the API answers `404` for both.
- **Demo data.** The `/demo` page shows a typed fixture ([`src/demo/sampleGraph.ts`](../apps/web/src/demo/sampleGraph.ts)) using the [data model](#data-model)'s entity types, with provenance on every node and edge. It is entirely fictional (`example.org`, RFC 5737 IP addresses, RFC 5398 AS numbers, no people) and labelled "Demo data" in the top bar. Case graphs never show that badge or any fixture data.
- **Testable without WebGL.** Highlighting, filtering and search are pure functions over the graph; sigma only applies their results through its node and edge reducers.
- **Theming.** All colors are design tokens: CSS custom properties in [`src/styles/tokens.css`](../apps/web/src/styles/tokens.css), defined once under `:root[data-theme="dark"]` and once under `:root[data-theme="light"]` (ink and paper, with brass as the accent, plus a color per entity type; types without their own fall back to `--muted`). No other file may contain a color literal: [`src/styles/tokens.test.ts`](../apps/web/src/styles/tokens.test.ts) fails on any hex or `rgb()`/`hsl()` color outside it, and checks that the favicon and the `theme-color` tags only repeat token values. [`base.css`](../apps/web/src/styles/base.css) holds typography, the woven background texture and the focus ring, one style for every interactive element (2px brass outline, offset 2px, on keyboard focus; text fields also turn their border brass while focused); component styles stay in `app.css`. The edges of text fields, selects and switches use `--control-line`, which reaches 3:1 on every surface (WCAG 1.4.11). A unit test ([`src/theme/contrast.test.ts`](../apps/web/src/theme/contrast.test.ts)) computes the WCAG contrast of every text and background pair the UI uses in both themes and requires 4.5:1 (3:1 for the focus ring, the selection and control edges); it scans `app.css` and `base.css`, so a text color it does not check, or a text and background pair declared in one rule that fails, fails the test. The theme follows `prefers-color-scheme` until the user picks one with the top-bar toggle, which is stored in `localStorage` (`nightloom.theme`; if storage is blocked the choice lasts for the page). [`public/theme-init.js`](../apps/web/public/theme-init.js), an external script loaded before the app bundle (the CSP allows no inline scripts), sets `data-theme` on `<html>` before the first paint, so there is no flash of the wrong theme. A theme change cross-fades colors briefly, except under `prefers-reduced-motion`. The graph canvas cannot use CSS variables (WebGL), so [`src/theme/tokens.ts`](../apps/web/src/theme/tokens.ts) parses `tokens.css` at build time and [`graphPalette.ts`](../apps/web/src/theme/graphPalette.ts) maps it to the canvas colors (transparent background over the texture, knot faces in `--surface`, labels in `--muted`, threads in `--hair`, the selection in `--brass` and its label in `--brass-text`; see [Graph view](#graph-view)). On a theme change `GraphView` repaints the existing graph in place ([`recolorGraph`](../apps/web/src/graph/buildGraph.ts)): colors only, the same sigma instance and camera.
- **Self-hosted fonts.** Fraunces (headings, variable with the optical-size axis), Source Sans 3 (interface) and JetBrains Mono (keys, IDs, timestamps) come from the `@fontsource-variable` packages and are bundled as hashed assets, so the CSP keeps `font-src 'self'` and nothing is loaded from a third party. [`src/styles/fonts.css`](../apps/web/src/styles/fonts.css) declares only the latin and latin-ext subsets, woff2 only, with `font-display: swap`; a browser downloads a file only when a page uses characters in its `unicode-range`, and the Fraunces italic only on the sign-in page. Together the eight files are about 415 kB, of which an English page needs about 137 kB (220 kB on the sign-in page).
- **The sign-in loom** ([`src/auth/loom/`](../apps/web/src/auth/loom/)). Beside the sign-in form (behind it, at 20 % opacity, on screens up to 900px wide) a slow three.js scene: warp threads in `--hair`, a weft thread in `--brass` passing over and under them, one pass every 10 s, knots in entity-type colors growing where it crosses, the cloth drifting away from the viewer one row per pass and its oldest rows fading into `--bg`. No camera movement, particles, glow or gradients. It must never get in the way of signing in, so its loading follows fixed rules ([`LoomBackdrop.tsx`](../apps/web/src/auth/loom/LoomBackdrop.tsx), [`environment.ts`](../apps/web/src/auth/loom/environment.ts)):
  1. The page renders a still drawing first ([`LoomArt.tsx`](../apps/web/src/components/LoomArt.tsx)): inline SVG with token colors, made from the same geometry ([`geometry.ts`](../apps/web/src/auth/loom/geometry.ts), no three.js) projected through the same camera, so it matches the scene.
  2. With `prefers-reduced-motion`, `navigator.connection.saveData`, or no WebGL (a throwaway context, released at once), the still drawing stays and nothing more is fetched.
  3. Otherwise the scene module is imported only after the `load` event and the next `requestIdleCallback` (a 500 ms timer where it does not exist), i.e. after the form is interactive. It is a 2.5 KiB chunk that imports the 3D view's `three-vendor` chunk; R3F's runtime dependencies are in that chunk too, so loading it never pulls in the graph code. `bundle-report.json` records the sign-in page's initial JavaScript, and `make web-size` fails if it contains three.js or if the loom brings its own copy.
  4. The canvas fades in only after it has drawn its first frame; if the import, the renderer or the WebGL context fails, the still drawing comes back. If reduced motion is switched on while it runs, the scene is disposed.
  5. It is `aria-hidden`, has nothing focusable and `pointer-events: none`.
  6. Cost: its own clock ([`frameLoop.ts`](../apps/web/src/auth/loom/frameLoop.ts)) draws at most 30 frames a second, and nothing while the tab is hidden (`visibilitychange`) or the canvas is off-screen (`IntersectionObserver`); loom time only moves while it runs. The device pixel ratio is capped at 1.5. Geometries, materials and the renderer are disposed (and the context released) when the sign-in page unmounts. On a theme change the scene re-reads the tokens and repaints.
- **Page titles, icons, states.** Each route sets the document title ([`documentTitle.ts`](../apps/web/src/app/documentTitle.ts)): "Sign in · Nightloom", "Cases · Nightloom", "*case title* · Graph · Nightloom", "Users · Admin · Nightloom", "Page not found · Nightloom" and so on. The icon is a knot on a thread: [`public/favicon.svg`](../apps/web/public/favicon.svg) (following the system theme) and PNGs in `public/icons/` drawn from it by [`scripts/render-icons.mjs`](../apps/web/scripts/render-icons.mjs), named by [`public/manifest.webmanifest`](../apps/web/public/manifest.webmanifest) (same origin, so the CSP's `default-src 'self'` covers it; nginx serves it as `application/manifest+json`). Loading states say what is loading over still skeleton lines (no spinners); the splash's pulsing mark gets visible words after 300 ms. A failed first load shows the reason and *Try again*. A global `prefers-reduced-motion` rule ends every CSS animation and transition at once; the graph's camera moves, the 3D depth transitions and the loom check the same preference in code.
- **Accessibility checks.** [`e2e/a11y.spec.ts`](../apps/web/e2e/a11y.spec.ts) runs axe-core ([`@axe-core/playwright`](https://github.com/dequelabs/axe-core-npm)) on sign-in, the cases list, a case, the 2D and 3D graph, both admin pages and the 404 page in both themes, and fails on any serious or critical WCAG 2.2 A/AA violation; a justified exception would be listed in the test with its reason (there are none).

#### Graph view

The demo and the case *Graph* tab share one component, `GraphView` ([`src/graph/GraphView.tsx`](../apps/web/src/graph/GraphView.tsx)): the sigma canvas, entity search, a type legend that toggles visibility, and a side panel. Callers pass the nodes and edges plus what the side panel shows (an overview, and an entity's details). Entity colors are design tokens (`--type-*`, see *Theming* above), referenced from one place ([`src/domain/entities.ts`](../apps/web/src/domain/entities.ts)) by the canvas, legend, search results and chips. Around the canvas: a toolbar (search, *Filters*, a 2D/3D switch (see [3D view](#3d-view)), and whatever the caller adds, such as the case's summary and actions), the type legend along the bottom-left edge, zoom buttons, and a slot over the top-right corner that the case Graph tab uses for the latest running job (elapsed time and *Cancel*).

- **No duplicated rules.** The web knows entity types only to give them a color and a label. Sensitivity, value hints and examples, the relationship allow-list, the Admiralty scales and the manual-observation defaults come from [`GET /schema`](#schema-endpoint), fetched once per page load.
- **Incremental updates.** `GraphView` keeps one graphology graph for its lifetime and syncs the data into it ([`syncGraph`](../apps/web/src/graph/buildGraph.ts)), removing what is gone, updating what changed and adding what is new. A freshly loaded graph starts on a circle grouped by type and gets a full layout. A node added later is placed at the centre of its neighbours, or just outside the graph when it has none. If it has neighbours, a short layout follows (60 iterations) in which every other node is pinned (ForceAtlas2's `fixed`), so adding an entity never reshuffles the picture. After that layout, the camera moves to the new entity. sigma's camera does not follow a change in the graph's extent, so in a graph of up to 200 nodes it shows the whole graph, with the new entity selected and highlighted. In a larger graph, whose extent barely changes, it zooms to the entity. Graphs of up to 200 nodes also label every node. Better-connected entities are drawn larger, up to 24 pixels, so a hub whose many neighbours are filtered out stays readable.
- **Rendering** ("Loom & thread": entities are knots, relationships are threads). Every visual rule is a pure function in [`src/graph/encoding.ts`](../apps/web/src/graph/encoding.ts), applied by sigma's node and edge reducers ([`src/graph/view.ts`](../apps/web/src/graph/view.ts)) and unit-tested in both themes; [`src/graph/primitives.ts`](../apps/web/src/graph/primitives.ts) declares the WebGL programs. All of it stays in WebGL, with no DOM element per node:
  - *Knots*: sigma's circle painted by one small custom fragment layer of concentric bands, from the outside in: the dashed selection halo, the pinned ring, the type ring, a group's second ring, the surface-colored face and the center dot. Each width and color is a per-node variable (a width of 0 leaves a ring out), so one program draws every state, and each band gets a 1 px anti-aliased edge, so 1–2 px rings stay crisp. ([`@sigma/node-border`](https://www.npmjs.com/package/@sigma/node-border) was tried first: it paints any border of 2 px or less in the previous border's color, which rules out thin rings.)
  - *Threads*: sigma 4's built-in curved path (a quadratic Bézier, curvature 0.12 for every edge). Parallel and reverse edges between the same two entities fan out by sigma's parallel spread, so `HAS_MX` and `HAS_NS` between one pair stay apart. (`@sigma/edge-curve` is only published for sigma 3; sigma 4 ships the path itself.)
  - *Label attachments*: the pin glyph left of a pinned entity's label and the type name under a group's count are small canvases that sigma packs into its label atlas, one renderer per theme.
  - *Labels*: Source Sans 3 from the bundled webfont. The canvas is created only once the font has loaded (at most 3 seconds), because sigma measures and rasterizes glyphs once. Labels stay horizontal. Which ones show is decided by the canvas, not sigma's label grid (which never looks at the text, so long labels in neighbouring cells overlapped): after each render [`src/graph/labelLayout.ts`](../apps/web/src/graph/labelLayout.ts) places them greedily on the knots' screen positions (group counts first, then the labels the view asks for, such as the hovered entity's neighbours, then the best-connected and largest knots) and keeps each one whose box, the pin before it or a group's type tag under it included, overlaps none already placed; the node reducer then forces exactly those. The hovered, selected and keyboard-focused entities' labels always show. Only knots at least 8 px wide are candidates, except in graphs of up to 200 nodes.
  - *Edge labels* are hidden, except on the hovered or selected entity's threads. The same relationships are the canvas's accessible description (an `aria-live` paragraph), so screen readers hear what the labels show, and the end-to-end test checks the labels through it.
  - *Theme*: the reducers read the current palette on every refresh, so a theme change repaints in place. The canvas is transparent and the page's woven texture shows through; sigma draws no grid.

  | Mark | Means |
  | --- | --- |
  | Ring and center dot in a `--type-*` color, on a `--surface` face | the entity's type |
  | Knot size (7–24 px, square root of the number of relationships) | how connected it is |
  | Larger knot, double ring, count in the center, type name below | a collapsed group (20–34 px) |
  | Thinner thread (60 %) at 45 % opacity | a relationship with confidence below 0.6, or marked unconfirmed (a PTR record whose `HAS_PTR` note says `forward_confirmed: false`) |
  | Thread in `--hair`, 1 px zoomed out to 1.5 px zoomed in | a relationship |
  | Brass (`--brass`) 2 px threads with their relationship type as label | the hovered or selected entity's relationships |
  | Slightly larger knot with a dashed brass halo, label in `--brass-text` | the selected entity |
  | Thin brass outer ring and a pin by the label | a pinned entity (the `pinned` node attribute) |
  | Everything else at 25 % opacity | while an entity is hovered or selected, all but it and its neighbours |

  The legend shows a ring swatch per type, then *Low confidence* and *Pinned*. On a desktop CPU the reducers repaint 2,000 nodes and 4,000 edges in about 2 ms (checked by [`src/graph/performance.test.ts`](../apps/web/src/graph/performance.test.ts)).
- **Layout never blocks.** ForceAtlas2 runs in time slices ([`src/graph/layout.ts`](../apps/web/src/graph/layout.ts)): each slice works for about 12 ms and then yields to the browser, with a hard cap on iterations (300 for a full layout, 150 above 500 nodes where Barnes-Hut is switched on). On a desktop CPU (i5-13400), the synthetic 2,000-entity graph used by [`src/graph/performance.test.ts`](../apps/web/src/graph/performance.test.ts) syncs in about 15 ms and settles in about 1.1 s over 75 slices (median 13 ms, longest 38 ms). Adding one entity to it takes about 3 ms. The generator lives in `src/test/`, which the app never imports, so no synthetic data reaches the production bundle.
- **Filters** ([`src/graph/filters.ts`](../apps/web/src/graph/filters.ts), pure functions). The *Filters* panel keeps entities by data source (`manual`, or a Shuttle's name from `shuttle:<name>@<version>`; an entity is hidden only when every one of its sources is), sensitivity, time seen (`[first_seen, last_seen]` overlapping the last 24 hours, 7 or 30 days, or a range of whole UTC days, either end open), minimum confidence (`max_confidence`), minimum Admiralty reliability and credibility (of the latest observation; an entity without a rating counts as F6, *cannot be judged*), focus (only entities at most 1–3 relationships from one entity, counted through entities the other filters keep; the focused entity always shows) and *hide isolated entities* (applied last). The legend's type toggles are the type filter. Filters apply to entities; sigma's reducers hide a relationship unless both ends show. The state lives in the page's query string (`hide`, `xsrc`, `xsens`, `seen`, `conf`, `rel`, `cred`, `focus`, `hops`, `isolated`), replacing the history entry on each change, so a filtered view is a shareable link and survives a reload; unknown values are ignored. The demo keeps the same filters in component state.
- **Collapsed groups** ([`src/graph/groups.ts`](../apps/web/src/graph/groups.ts)). After filtering, an entity with more than 25 neighbours of one type that are leaves (their only shown relationship is to it) gets those leaves replaced by one group node (`group:<parent>|<type>`, *312 URLs*), sized by count (logarithmically, capped), drawn as a larger knot with a double ring in the type's color, the count in its center and the type name below (see *Rendering*), and one edge to the parent (its relationship type when the members share one). `GraphView` syncs the collapsed data into graphology, so clicking a group (or picking it among the parent's connections) expands it through the same incremental sync and short layout as an added entity, and *Collapse* in the side panel (on the parent or a member) folds it again. Search covers every entity the filters show, collapsed ones included: picking one expands its group, then selects and centres it once it is in the graph. The performance tests add a graph whose one domain has 1,500 leaf URLs to the 2,000-entity one: filtering, grouping, collapsing and syncing it, expanding and collapsing the group again, and finding a collapsed URL each stay within the same generous bounds as the other checks.
- **Export** ([`src/cases/graph/ExportDialog.tsx`](../apps/web/src/cases/graph/ExportDialog.tsx)). The case Graph tab's *Export* dialog offers JSON, GraphML and CSV, says that person-related data is redacted by default, and lets owners tick *Include person-related data*, which asks for a reason (10–500 characters). A download link cannot show an error, so *Download* first calls `GET /export/check` with the same parameters and shows a `403`, `413`, `422` or `429` in the dialog; when it passes, a normal link to `GET /export` starts the download (the session cookie authenticates it; a `GET` needs no CSRF token).
- **Interactions** ([`src/graph/interactions.ts`](../apps/web/src/graph/interactions.ts), with the decisions in pure, unit-tested modules). sigma keeps what it does well (panning the stage, pinch and twist on touch screens, hit-testing); the rest is layered on its events:
  - *Drag and pin.* sigma 4's node drag (`enableNodeDrag`) moves the node, or the whole multi-selection when the node is part of one (`getDraggedNodes`). The camera does not move (sigma prevents its pan while a drag runs), and the frame is frozen for the drag (`setCustomBBox`), because with the default auto-rescale a node dragged past the graph's edge would rescale everything else. Dragged nodes get `fixed` at once (a running layout leaves them alone) and become pinned on release. A collapsed group node can be dragged and pinned too, for the session only (a group has no entity key to save it under).
  - *Pins* ([`src/graph/pins.ts`](../apps/web/src/graph/pins.ts), [`usePins`](../apps/web/src/graph/usePins.ts)). A `PinStore` (entity key → `[x, y]`) is the source of truth; the graph's `pinned` and `fixed` attributes mirror it for the nodes that are drawn, and pins of entities that are hidden by a filter or inside a collapsed group stay in the store and are saved with the rest. ForceAtlas2 never moves a node with `fixed`, and a layout run no longer clears `fixed` from a pinned node. The case Graph tab loads the user's [personal layout](#personal-layouts) on open (retried with backoff; changes made before it arrives win over it) and saves the whole map with `PUT` 800 ms after the last change (optimistic: the UI never waits), again at once with `keepalive` when the page is hidden or the tab left, and after a failure with a growing delay (1, 2, 5, 10, then every 30 s, or as long as a `429`'s `Retry-After` says) while *Couldn't save layout — retrying* shows over the canvas. A retry always sends the newest pins. Errors that a retry cannot fix (signed out, no longer a member, `422`) stop it. Unpinned nodes rejoin with a short layout run (60 iterations, two per animation frame) in which every other node is fixed, so they glide back and nothing else moves.
  - *Selection.* A click selects one entity; <kbd>Ctrl</kbd>/<kbd>⌘</kbd>+click toggles one in a multi-selection (drawn like the selection, `ViewState.selectedNodes`); <kbd>Shift</kbd>+drag on the stage draws a rectangle in screen space ([`src/graph/geometry.ts`](../apps/web/src/graph/geometry.ts)) and selects the nodes whose projected positions (sigma's `graphToViewport`, rotation included) fall inside it, so it works at any rotation. The side panel then shows *N entities selected* with *Pin all*, *Unpin all* and *Clear*. A node's menu (right-click, long press of 550 ms on touch) offers *Pin* / *Unpin*, *Select neighbours* and *Hide* its type.
  - *Zoom, pan, rotate.* The wheel is handled before sigma sees it (a capture listener). [`src/graph/wheel.ts`](../apps/web/src/graph/wheel.ts) decides mouse wheel (zoom one step toward the pointer) or trackpad (pan by the deltas), with Ctrl+wheel as a pinch (smooth zoom toward the pointer); the heuristic and its known edge cases are documented in that file and overridable with *View → Scroll wheel*. Safari's `gesture*` events pinch and twist. <kbd>Alt</kbd>+drag or right-drag on the stage rotates around the view's center (sigma's own right-drag rotation is off, so a right-click on a node can open its menu instead); ⟲ / ⟳ and <kbd>Q</kbd> / <kbd>E</kbd> rotate by 15°, and *Reset view* also resets the angle. Labels stay upright (`labelRotationAlignment: "viewport"`). The initial view and *Reset view* frame the visible graph ([`src/graph/framing.ts`](../apps/web/src/graph/framing.ts)): its bounds fill 85% of the largest part of the canvas that the legend, the zoom controls and the minimap leave free, centered there, and a graph narrower than 200 layout units is framed as if it were that wide, so one or two entities are not blown up. The 3D view frames the projected points the same way (perspective included; `frameInArea` in [`camera3d.ts`](../apps/web/src/graph/three/camera3d.ts)) rather than a bounding sphere, which left a wide, shallow graph small.
  - *Keyboard* ([`src/graph/keyboard.ts`](../apps/web/src/graph/keyboard.ts) is the key map; the shortcuts dialog lists the same table). The canvas is `role="application"`, focusable, described by a visually hidden help text. <kbd>Tab</kbd> / <kbd>Shift</kbd>+<kbd>Tab</kbd> move a focus ring (a DOM element placed over the node after each render) through the visible nodes in screen reading order ([`src/graph/focusOrder.ts`](../apps/web/src/graph/focusOrder.ts): 32 px rows, then left to right); past the last node, focus leaves the graph. The focused node is highlighted like a hovered one and announced in an `aria-live` region ("Domain www.example.org, 3 neighbours, pinned").
  - *Minimap* ([`src/graph/Minimap.tsx`](../apps/web/src/graph/Minimap.tsx), drawing in [`minimap.ts`](../apps/web/src/graph/minimap.ts)). A 168 × 112 canvas above the zoom buttons: every visible node as a dot in its type color, north up, and the viewport's four corners (`viewportToGraph`) as a brass outline, so a rotated camera shows as a turned outline. Click or drag in it to center the camera there. Redrawn on camera updates and sigma's `afterRender`, throttled to 10 per second and not at all while nothing moves. `aria-hidden`: the keyboard pans and zooms the same way. Shown by default above 60 visible entities; *View → Minimap* overrides it.
  - *Preferences.* The scroll-wheel setting and the minimap choice are kept in `localStorage` (every access in `try`/`catch`; without storage they last for the page).
  - *Canvas labels* ([`src/graph/labels.ts`](../apps/web/src/graph/labels.ts)). A certificate shows its `subject_cn` property, or the first 8 and last 4 characters of its fingerprint; any other label over 32 characters is cut in the middle. Search, the side panel and the announcements use the full value.

  Dragging 50 selected nodes in a 2,000-entity graph stays far below a 16 ms frame for the per-frame work, a full repaint of every node and edge, the pins and a minimap redraw ([`performance.test.ts`](../apps/web/src/graph/performance.test.ts)).
- **Loaded on demand.** sigma and graphology are only fetched when there is a graph to draw: `/demo` is a lazy route, and the case Graph tab lazy-loads `GraphView`, so its empty state and forms do not need them.

#### 3D view

The *3D* switch on the toolbar shows the same graph in space ([`src/graph/three/`](../apps/web/src/graph/three/)), drawn with [three.js](https://threejs.org/) through [React Three Fiber](https://r3f.docs.pmnd.rs/). It is another view of one model, not a second graph:

- **One layout model.** `GraphView` keeps the graphology graph, the ForceAtlas2 layout, the [pins](#personal-layouts), the selection, the filters and the groups for both views. In 3D, x and y are the layout's coordinates as they are; z comes from the depth mode ([`depth.ts`](../apps/web/src/graph/three/depth.ts)): **Time** (`first_seen` mapped linearly onto a depth range, oldest at the back; entities without a readable date on an *Undated* plane in front), **Type** (one evenly spaced layer per type present, in `ENTITY_TYPES` order) or **Flat** (z = 0). The range is 0.8 × the layout's diameter (at least 120 units), so a graph is a block whatever its size. Dragging a knot intersects the pointer's ray with the plane through the knot facing the camera and keeps only the x / y change; on release it is pinned through the same `PinStore` and layout API as 2D, so a pin made in 3D is a pin in 2D and the other way round. A change of depth mode animates z over 450 ms (at once with `prefers-reduced-motion`).
- **Pure modules, tested without WebGL.** Camera moves (orbit around the camera's up axis, pan, zoom toward a point, framing a sphere, fly-to, interpolation; [`camera3d.ts`](../apps/web/src/graph/three/camera3d.ts)), screen ↔ world projection ([`projection.ts`](../apps/web/src/graph/three/projection.ts), the same matrices three.js uses, checked against its `PerspectiveCamera`), picking (the nearest knot whose projected disc holds the pointer), box selection in screen space, the keyboard focus order (four depth bands nearest first, then reading order), the drag plane ([`picking.ts`](../apps/web/src/graph/three/picking.ts)), label selection ([`labels3d.ts`](../apps/web/src/graph/three/labels3d.ts)), the minimap footprint ([`minimap3d.ts`](../apps/web/src/graph/three/minimap3d.ts)), the key map ([`keyboard3d.ts`](../apps/web/src/graph/three/keyboard3d.ts)) and the GPU buffers ([`scene.ts`](../apps/web/src/graph/three/scene.ts)). The component tests replace only the renderer (`Scene3D`) with a plain canvas, so interactions, labels and announcements run for real.
- **Rendering** ([`Scene3D.tsx`](../apps/web/src/graph/three/Scene3D.tsx)), "Loom & thread" without glow, bloom, fog or gradients, on `--bg`. Knots are one `InstancedMesh` per shape (entities, collapsed groups): camera-facing quads whose small fragment shader draws the 2D knot's bands (dashed brass halo, pinned ring, type ring, a group's second ring, `--surface` face, center dot). A knot keeps its 2D pixel size (same `knotSize` rules, selection and pulse scaling) at the camera target's distance and grows or shrinks with perspective within 0.55–1.8×. Threads are one `LineSegments` geometry: each relationship a quadratic curve that sags (6 % of its length) in 6 segments, `--hair`, the focused entity's in `--brass`; dimming and uncertainty follow the 2D rules and are blended toward the background on the CPU, so everything stays opaque and needs no sorting. A faint warp guide in `--grid` runs along the depth axis under the graph, with a weft line at each ruler mark. The canvas outputs the tokens' sRGB values unconverted (`linear`, no tone mapping). Collapsed groups show their count through the label layer.
- **Label layer.** Labels are HTML (`.graph3d-label`, class-based styles and the theme's fonts) over the canvas, placed each drawn frame with a `transform`: the hovered, selected and focused knots and every group count always, then the best-connected knots on screen (the hovered or selected entity's neighbours first) whose label overlaps no label already placed, up to 40. Canvas labels use the [shortened text](#graph-view). The depth ruler (dates or type names, *Undated*) is in the same layer.
- **Frames on demand.** The canvas renders only when something changes (`frameloop: "demand"`): a camera move, a layout step, a drag, a depth animation, new data. Each drawn frame updates the typed arrays that changed and runs the label pass. With 2,000 entities and 4,000 relationships that CPU work takes about 0.7 ms (median, desktop CPU; [`performance3d.test.ts`](../apps/web/src/graph/three/performance3d.test.ts)). Geometries, materials and the renderer are disposed on unmount.
- **Interaction.** three's `OrbitControls` (from `three/addons`) orbits on a left drag (one finger), pans on a right drag (two fingers) and pinch-zooms; [`interactions3d.ts`](../apps/web/src/graph/three/interactions3d.ts) handles first, and keeps from it, everything that starts on a knot or with <kbd>Shift</kbd> (select, <kbd>Ctrl</kbd>/<kbd>⌘</kbd>+click, drag and pin, a rectangle), double-click (fly-to, 550 ms), the node menu (right-click, long press) and the wheel, with the 2D heuristic and *Scroll wheel* setting ([`wheel.ts`](../apps/web/src/graph/wheel.ts)). OrbitControls caches the rotation from the camera's up vector when it is created, so the view refreshes it whenever a camera with another up direction is applied. The keyboard follows the 2D map where it makes sense (arrows orbit, <kbd>Shift</kbd>+arrows pan, <kbd>Q</kbd> / <kbd>E</kbd> turn around the vertical axis, never roll) and the announcements are the 2D ones. The minimap shows the graph from the top with the camera's view footprint (the corners of the view on the target's plane).
- **Switching.** The mode is in the URL (`?mode=3d`, the case Graph tab) and in `localStorage` (`nightloom.graph.mode`, every access guarded). Switching keeps the selection, filters, groups and the last keyboard focus, and the camera looks at the same area: a 2D camera becomes a camera looking straight at the graph's plane from the distance where a pixel covers the same graph units, rotated like 2D, moved back if depth brings what the 2D view showed off screen; a 3D camera becomes its target, scale and up direction seen from the front.
- **WebGL.** The 3D view needs WebGL 2. Where it cannot be created (checked before loading the 3D code, and by an error boundary around the renderer), the 2D view is shown with the notice *3D needs WebGL, which isn't available in this browser — showing 2D.* When the context is lost, 2D is shown while the 3D view waits mounted and hidden, and it comes back on `webglcontextrestored`.
- **CSP and bundle.** No change to the Content Security Policy: three.js and React Three Fiber need no `eval`, no `blob:` and no inline styles (positions and sizes are set through the CSSOM, which `style-src 'self'` allows); the end-to-end test fails on any CSP violation in 3D. The 3D code is a lazily loaded chunk, fetched only when someone switches to 3D, with three.js and R3F in a `three-vendor` chunk. The view creates its own R3F root (`createRoot`) instead of R3F's `<Canvas>`, which registers the whole `THREE` namespace and so defeats tree shaking; registering only what it uses brings three.js down from about 250 to 180 KiB gzip. `vite build` writes `bundle-report.json` and `make web-size` (CI) checks it against [`bundle-budget.json`](../apps/web/bundle-budget.json): the 2D graph's bundle may grow by at most 2 KB over its size before the 3D view (plus the sign-in loom's geometry and still drawing, about 2 KB, since U6), and the 3D chunks may weigh at most 250 KiB gzip (about 200 KiB now).

**Case Graph tab** ([`src/cases/GraphTab.tsx`](../apps/web/src/cases/GraphTab.tsx)). The tab loads `GET /cases/{id}/graph` (limit 2,000) and the schema. Entities become nodes (key as id, value as label) and relationships become edges, carrying their full provenance.

- **Header.** A live summary ("2 entities, 1 relationship"). When the API reports `truncated`, a banner says only the first 2,000 entities by key, with at most five relationships each, are drawn.
- **Side panel.** For the selected entity: type, value, key (with a copy button), sensitivity, neighbour count (from `GET /entities/{key}`, so it is exact even when the view is truncated), first and last seen, observation count, sources, max confidence as a percentage, Admiralty rating (hover or focus explains both grades), evidence references, note, and its connections.
- **Add entity** (editors and owners of an open case). Types are grouped by sensitivity. Person-related types are disabled, with the reason, when the case does not allow person entities. The value field shows the type's hint and example from the schema, and there is an optional note (and a phone region for phone numbers). The server normalizes the value: its `422` message (e.g. *A domain name needs at least two labels*) appears under the field, and a `403` or `409` appears in the form. On success the node appears, selected and in view. Adding something the case already has says so (the API merged an observation).
- **Add relationship.** Pick both ends from the case's entities with a searchable combobox (the selected entity is pre-filled), swap the direction if needed, and choose from only the relationship types the schema allows for that pair. `RELATED_TO` asks for a note.
- **Delete entity.** From the side panel, after a confirmation. Its relationships go too.
- **Run Shuttle** (editors and owners of an open case). The side panel lists the installed Shuttles that accept the selected entity's type (`GET /shuttles?input_type=…`, fetched once per type and page load) with their title, description, badges (passive or active, network, sensitivity), the Shuttle's [usage note](#metadata) when it declares one (conditions of use such as *free for non-commercial use*, under the description with an info icon) and links to the source's terms and homepage. Person-sensitive Shuttles are disabled, with the reason, when the case does not allow person entities, and active ones until ownership verification exists (the API refuses both anyway). *Run* queues a job (`POST /jobs`); a `403`, `409` or `429` answer (e.g. *You already have 5 queued or running jobs*) is shown under the Shuttle.
- **Jobs.** The *Jobs* button (with the number of active jobs) opens a panel above the graph, and starting a job opens it. It lists the case's jobs, newest first, 20 at a time with *Load more*: a status chip (queued, running, succeeded, failed, cancelled), the Shuttle and its input, the elapsed time (ticking while active), the stats (created, updated, rejected) and the job's error, which is always one of the API's fixed messages. Queued and running jobs are polled every 2 seconds (`GET /jobs/{id}`) while the browser tab is visible; polling stops when every job has finished or the tab is hidden. Editors and owners can *Cancel* an active job, also in a closed case. Viewers see the history only.
- **Results.** When a job that changed something finishes, the tab loads the graph again; `GraphView` syncs it incrementally, so the existing layout stays and new nodes settle next to their neighbours. After a successful job the entities it created or updated (`stats.entities_touched`) are highlighted for four seconds.
- **Read-only.** Viewers, and everyone while the case is closed or archived, see the graph without these controls and a one-line explanation.
- **Empty state.** *No entities yet.* with a hint to add what you know and then run Shuttles on it, and *Add your first entity* for editors.

### Health and readiness

The API creates its clients (async SQLAlchemy engine, Neo4j driver, Valkey client, S3 client) in the FastAPI lifespan, but none of them connects at startup. The API therefore always starts, even when every dependency is down.

| Endpoint | Kind | Behaviour |
| --- | --- | --- |
| `GET /api/v1/health` | Liveness | Always `200` while the process is serving requests. Never touches a dependency. |
| `GET /api/v1/ready` | Readiness | Pings PostgreSQL (`SELECT 1`), Neo4j (`RETURN 1`), Valkey (`PING`) and object storage (`HeadBucket` on the evidence bucket) concurrently, each limited to `NIGHTLOOM_DEPENDENCY_TIMEOUT_SECONDS` (default 2). `200` when all pass, `503` otherwise. |

The readiness body reports `ok`, `latency_ms` and a short `error` for each dependency. Errors are redacted before they are returned or logged: they never include credentials or connection URLs. Use `/health` for container restarts and `/ready` for traffic routing and monitoring.

### Authentication

The API authenticates browsers with **server-side sessions** (code in [`nightloom_api/auth/`](../apps/api/src/nightloom_api/auth/)). There is no public registration: the first admin is created with `make create-admin`, and admins create everyone else.

**Sessions.** A successful `POST /api/v1/auth/login` creates a random 256-bit token (`secrets.token_urlsafe(32)`) and sends it only in the `nightloom_session` cookie: `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` in production (or with `NIGHTLOOM_COOKIE_SECURE=true`). PostgreSQL stores only the token's SHA-256 in the `sessions` table, so a leaked table cannot be replayed. A session expires after **24 hours without use** (sliding; `last_seen_at` is written at most once a minute) and, at the latest, **7 days after sign-in**. Expired sessions are deleted when they are next presented and when their user signs in again. Logging out deletes the row. Changing your password signs out your other sessions; deactivating a user or revoking their sessions (admin) signs them out everywhere.

**CSRF.** Login also sets a non-HttpOnly `nightloom_csrf` cookie. Every authenticated `POST`, `PUT`, `PATCH` or `DELETE` must repeat it in the `X-CSRF-Token` header (double submit, constant-time comparison), or it gets `403`. The CSRF token is derived from the session token with a domain-separated SHA-256, so a cookie planted by an attacker (for example from a sibling subdomain) does not match the victim's session. Login itself is exempt. `SameSite=Strict` is a second, independent layer.

**Passwords.** Hashed with Argon2id ([pwdlib](https://github.com/frankie567/pwdlib) on argon2-cffi, its recommended parameters); a hash with outdated parameters is upgraded on the next successful sign-in. The policy is 12–128 characters, not equal to or containing the local part of the user's email (for local parts of three or more characters). Emails are stored trimmed and lower-case, and are unique.

**Roles.** `admin`, `analyst` and `viewer`. Routes use the `current_user` dependency (`401` without a valid session) and `require_role(...)` (`403`, recorded in the [audit log](#audit-log)). Only admins manage users. An admin cannot demote or deactivate themselves, and the last active admin cannot be demoted or deactivated (`409`); concurrent changes are serialized with row locks.

**Brute-force protection.** Failed sign-ins are counted in Valkey, per email (hashed, so Valkey holds no addresses) and per client IP, in 15-minute windows. After **5 failures for an email** or **20 from an IP**, further attempts get `429` with `Retry-After` until the window ends, without checking the password. A successful sign-in resets the email's counter. Every failure returns the same `401 "Invalid email or password."`, and an unknown email still runs a full Argon2 verification, so neither the message nor the timing reveals whether an account exists. Wrong current passwords on `POST /auth/password` count against the same limits. If Valkey is unreachable, sign-in fails closed with `503`.

| Endpoint (under `/api/v1`) | Who | Notes |
| --- | --- | --- |
| `POST /auth/login` | anyone | `{email, password}` → `{user}` and both cookies |
| `POST /auth/logout` | signed in | `204`, clears the cookies |
| `GET /auth/me` | signed in | the current user |
| `POST /auth/password` | signed in | `{current_password, new_password}` → `204` |
| `GET /users`, `POST /users` | admin | list; create with a generated temporary password (`generate_password`, default `true`, returned once as `temporary_password`) or one the admin supplies |
| `PATCH /users/{id}` | admin | `display_name`, `role`, `is_active` |
| `DELETE /users/{id}/sessions` | admin | sign the user out everywhere |
| `POST /users/{id}/password-reset` | admin | new temporary password (returned once), all the user's sessions revoked; `400` for your own account |
| `GET /audit` | admin | the [audit log](#audit-log), newest first |
| `POST /audit/verify` | admin | check the audit log's hash chain |

`/health` and `/ready` stay public. Request validation errors (`422`) never echo the submitted values, so a password cannot be reflected back.

**Why not JWT?** The browser is the only client, and it talks to the API on the same origin. Opaque session tokens checked against PostgreSQL can be revoked instantly (logout, password change, deactivation, admin revocation) and reflect role changes on the next request; a stateless JWT stays valid until it expires unless a server-side denylist is added, which brings back the database lookup without the simplicity. Keeping the token in an HttpOnly cookie also keeps it away from page scripts, whereas a bearer token readable by JavaScript is exposed to any XSS.

**Production safety.** With `NIGHTLOOM_ENV=production`, the API refuses to start while `NIGHTLOOM_DATABASE_URL`, `NIGHTLOOM_NEO4J_PASSWORD`, `NIGHTLOOM_VALKEY_URL`, `NIGHTLOOM_S3_ACCESS_KEY` or `NIGHTLOOM_S3_SECRET_KEY` still contains the `change-me` placeholder. The error names the variables, never their values.

**Hashing off the event loop.** An Argon2 check costs roughly a tenth of a second of CPU. Request handlers never run it on the event loop: `passwords.*_async` hand it to a worker thread, and a shared limiter lets at most `NIGHTLOOM_PASSWORD_HASH_CONCURRENCY` (default 4, at least 1) hashes run at once. Other requests stay responsive during a burst of sign-ins, and a sign-in flood queues for hashing instead of taking every worker thread and CPU core. The CLI keeps the synchronous functions.

**Client IP behind the proxy.** The per-IP limit and each session's recorded `ip` use the client's address. In the Compose stack the browser talks to nginx (`web`), which forwards `/api/` with `X-Real-IP: $remote_addr` and `X-Forwarded-For: $proxy_add_x_forwarded_for` (the client's header with the peer nginx saw appended). uvicorn (`--proxy-headers`) honours forwarding headers only from the addresses in `FORWARDED_ALLOW_IPS`; Compose sets that to exactly the `web` container's address, which is fixed (`NIGHTLOOM_WEB_IP`, default `172.30.0.10`, on the fixed subnet `NIGHTLOOM_NETWORK_SUBNET`, default `172.30.0.0/24`). Docker hands out addresses to every other container only from `NIGHTLOOM_NETWORK_IP_RANGE` (default `172.30.0.128/25`), which excludes the web address, so no other container can ever hold the trusted address, for example after `web` is recreated. uvicorn then takes the right-most address in `X-Forwarded-For` that is not a trusted proxy, which is the one nginx appended, so a value the client forges at the front of the header never wins. Requests that reach the API directly (port 8000) come from an untrusted peer, so their forwarding headers are ignored and the peer address is used. `FORWARDED_ALLOW_IPS` is never `*`: that would let any client choose its own IP and dodge the per-IP limit. Outside Compose, uvicorn's default trusts only `127.0.0.1`; set `FORWARDED_ALLOW_IPS` to your proxy's address when you put another one in front.

**Behind the production edge.** In production ([`deploy/compose.prod.yaml`](../deploy/compose.prod.yaml), see [DEPLOYMENT.md](DEPLOYMENT.md)) a second proxy sits in front of nginx, and nginx resolves the client's address before passing it on as above, so the API's side does not change: `FORWARDED_ALLOW_IPS` stays the `web` address only. `NIGHTLOOM_EDGE` selects one of three nginx snippets ([`apps/web/nginx/edge/`](../apps/web/nginx/edge/)), rendered from the image's envsubst templates:

| `NIGHTLOOM_EDGE` | nginx trusts | Header it reads | Client address seen by nginx |
| --- | --- | --- | --- |
| `none` (default, development) | nobody | none | the TCP peer |
| `caddy` (profile `https`) | only Caddy's fixed address `NIGHTLOOM_CADDY_IP` (default `172.30.0.11`) | `X-Forwarded-For`, `real_ip_recursive on` | the right-most address that is not Caddy's: the client Caddy saw. Caddy is the first hop and trusts no incoming `X-Forwarded-For`, so a spoofed value at the front never wins |
| `cloudflared` (profile `tunnel`) | only cloudflared's fixed address `NIGHTLOOM_CLOUDFLARED_IP` (default `172.30.0.12`) | `CF-Connecting-IP` (set by Cloudflare's edge, which overwrites a client's value) | the client Cloudflare saw |

Both edge addresses are outside `NIGHTLOOM_NETWORK_IP_RANGE`, like the web address, so no other container can take them. A request from any other peer keeps its own address whatever it sends, and an unknown `NIGHTLOOM_EDGE` value makes nginx refuse to start. `X-Forwarded-Proto: https` is likewise passed on only from the trusted edge. [`deploy/tests/nginx-real-ip.sh`](../deploy/tests/nginx-real-ip.sh) checks all three modes against the built image, and [`test_edge.py`](../apps/api/tests/integration/test_edge.py) checks the whole chain through a real Caddy (the session's stored IP is the Docker gateway, never a spoofed value, Caddy's or nginx's address).

**Accepted trade-off: email lockout.** Five failed attempts lock an email for the rest of its 15-minute window, whoever made them. Someone who knows a user's email can therefore keep that user from signing in by sending wrong passwords, for at most 15 minutes per five attempts, and must keep doing so to extend it. This is accepted because the per-email limit is the main protection against password guessing spread across many IPs, which the per-IP limit alone cannot stop; the window bounds the harm, and the lock clears by itself when it ends.

#### Temporary passwords and the forced change

Admins never choose or see a user's real password. `POST /users` generates a temporary one by default, and `POST /users/{id}/password-reset` generates a new one for an existing user (and revokes all of that user's sessions). The generator ([`passwords.generate_temporary_password`](../apps/api/src/nightloom_api/auth/passwords.py)) draws 24 symbols with `secrets.choice` from 55 letters and digits without look-alikes (`0 O o 1 l I i`), about 138 bits, shown in six groups of four (`Hk7q-Rmz3-…`, 29 characters), and redraws in the (astronomically unlikely) case that the result breaks the password policy. The password is returned **once**, in the response body with `Cache-Control: no-store`. The database keeps only its Argon2id hash, and it never appears in logs or audit `details`: those record only `credential: "generated"` or `"supplied"` and `change_required`.

Such a user has `users.must_change_password` set (migration `0006_must_change_password`; `false` for existing users). An admin who supplies a password with `generate_password: false` gets the same flag unless they also send `must_change_password: false`. `make create-admin` sets no flag: that admin typed their own password. While the flag is set, `current_session`, which every authenticated route depends on (directly, through `current_user` and `require_role`, or through the case access checks), answers **`403 {"detail": "Choose a new password before you continue.", "code": "password_change_required"}`**. The only routes that use the session without that check (`any_session`) are `GET /auth/me`, `POST /auth/password` and `POST /auth/logout`. The CSRF check still applies to all three. A successful `POST /auth/password` clears the flag in the same transaction as the new hash. Both `/auth/me` and the login response include `must_change_password`, so the web UI knows before it asks for anything else. The refusals are not audited: they are expected on every request until the change, and the change itself is (`auth.password_change` with `change_required: true`).

An admin cannot reset their own password this way (`400`, recorded as `user.password_reset` `failure` with `reason: self_reset`): they change it with their current one, which keeps the rate limit and the proof that it is really them.

### Web authentication flow

The web UI ([`apps/web/src/auth/`](../apps/web/src/auth/), [`src/api/client.ts`](../apps/web/src/api/client.ts)) never sees the session token: it lives only in the `HttpOnly` cookie, which the browser sends on every same-origin request (`credentials: "same-origin"`).

- **Start-up.** `AuthProvider` asks `GET /auth/me`. Until it answers, a splash screen is shown (no flash of the sign-in form); `200` shows the app, anything else the sign-in screen.
- **Sign-in.** The form posts to `/auth/login`. The UI shows only fixed messages: *Invalid email or password.* for `401`, *Too many attempts. Try again in N minutes.* for `429` (from `Retry-After`, rounded up), and generic network, timeout and unavailable messages. Server text is never shown, except validation messages (`422`).
- **CSRF.** For every `POST`, `PUT`, `PATCH` and `DELETE` the client reads the `nightloom_csrf` cookie and repeats it in the `X-CSRF-Token` header; `GET` requests carry no header.
- **Session expiry.** A `401` on any request other than sign-in means the session ended (expired, revoked by an admin, or signed out by a password change elsewhere). The client notifies `AuthProvider`, which drops the user and shows the sign-in screen with *Your session has ended. Please sign in again.*
- **Error types.** Besides the sign-in errors, the client turns `403` into `ForbiddenError`, `404` into `NotFoundError` and `409` into `ConflictError` (with the server's message, e.g. the last-owner rule), and `422` into `ValidationError` with one message per field (pydantic's "Value error, " prefix removed). Case calls live in [`src/api/cases.ts`](../apps/web/src/api/cases.ts), with types mirroring the API's schemas.
- **Sign-out.** Calls `POST /auth/logout`, then clears the local state even if that call fails; an orphaned server session still expires on its own.
- **Forced password change.** When `/auth/me` or the sign-in answer has `must_change_password`, or any request answers `403` with the code `password_change_required` (the client turns it into `PasswordChangeRequiredError` and notifies `AuthProvider`), the auth gate shows a full-page *Choose a new password* screen instead of the app. It reuses the password change form and its checks, asks for the temporary password, and cannot be dismissed; *Sign out* is the only other way out. The address is left alone, so after a successful change the app opens the page that was asked for, and pages that were refused load again.
- **Temporary passwords in the UI.** The *Users* page keeps a generated password only in the state of the one-time panel that shows it (with *Copy* and *It will not be shown again. Share it through a secure channel.*); closing the panel drops it, and it is never logged.
- **Password change.** A modal checks the policy locally (12–128 characters, confirmation matches, differs from the current one), then posts to `/auth/password`; the server checks again, and its validation messages are shown next to the fields. On success the other sessions are signed out and this one stays.

### Request IDs

Every request gets an ID that ties together the API's log lines, its audit events and the response. A pure-ASGI middleware ([`request_id.py`](../apps/api/src/nightloom_api/request_id.py); not `BaseHTTPMiddleware`, which runs the app in another task and buffers responses) accepts an incoming `X-Request-ID` only if it matches `^[A-Za-z0-9._-]{8,64}$` and otherwise generates a UUID4. The ID lives in a context variable for the duration of the request, a logging filter adds it to every log record (`... name [request-id]: message`), and the response returns it in `X-Request-ID`. In the Compose stack nginx applies the same rule: it forwards a valid client ID, or its own random `$request_id` instead, and writes the ID into its access log, so nginx, the API and the audit log all show the same value.

### Audit log

The audit log (code in [`nightloom_api/audit/`](../apps/api/src/nightloom_api/audit/)) records security-relevant actions in the PostgreSQL table `audit_events`. Only admins can read it.

**What is recorded.** One event per action, with when it happened (`occurred_at`, the database time), who did it (`actor_user_id` and `actor_label`, the actor's email *at that time*, so history survives later changes), from where (`actor_ip`, from `request.client` as described in [Client IP behind the proxy](#authentication)), the request ID, the `action`, the `outcome` (`success`, `failure` or `denied`), what it was done to (`target_type`, `target_id`), a nullable `case_id` (set on every [case](#cases) event, including refused case requests), and a small `details` object.

| Action | Recorded when |
| --- | --- |
| `auth.login` | a sign-in succeeds or fails (unknown email, wrong password, inactive user, over-long password) |
| `auth.logout` | a user signs out |
| `auth.password_change` | a password is changed (`revoked_sessions`, and `change_required: true` when it replaced a temporary password), or the current password given was wrong (a new password that fails the policy is a validation error and is not recorded) |
| `auth.rate_limited` | a sign-in or password change is refused with `429` |
| `user.create`, `user.update`, `user.sessions_revoke` | an admin creates or changes a user or signs them out everywhere, or tries to and fails (`409`, `404`, `422` on create); `user.create` records `credential` (`generated` or `supplied`) and `change_required`, never the password; `user.update` lists each changed field's old and new value. `create-admin` on the command line is recorded with the actor `system:cli`. |
| `user.password_reset` | an admin gives a user a new temporary password: `revoked_sessions`; `failure` with `reason` `self_reset` (`400`) or `not_found` (`404`). Never the password |
| `authz.denied` | a signed-in user is refused by a role check (`403`), with the method, route and roles; or a case request is refused (`404` for non-members, `403` for a missing case capability or a person-related entity in a case that does not allow them), with `case_id` set and a `reason` (`not_a_member`, `missing_capability` or `person_entities_disabled`, the last with the `entity_type`) |
| `audit.verify` | an admin runs the chain verification, with its result |
| `case.create` | a case is created: legal basis, `allow_person_entities`, retention, ethics version and, when person entities are allowed, the `person_entities_reason` |
| `case.update` | case fields change: the changed field **names** only, plus `change_reason` when one was required; never the text of a title, description or purpose |
| `case.status_change` | a case's status changes (`from`, `to`) |
| `case.delete` | a case is deleted (its member count; not its title), or the title confirmation did not match (`failure`) |
| `case.member_add`, `case.member_update`, `case.member_remove` | membership changes (role, or `from`/`to`; `self` on removal), or fails (`409` last owner, `404` unknown user or member); the target is the member's user ID |
| `case.export` | a member exports the case's graph: `format`, `entities`, `relationships`, `include_person_data`, `redacted_entities` (when redacted) and the owner's `reason` (when person data is included); `failure` with `error` `too_large` (`413`) or `rate_limited` (`429`). Never an exported value |
| `case.graph_delete` | after a case is deleted, its Neo4j graph is removed: `success` with `deleted_nodes`, or `failure` with the error type |
| `entity.create`, `entity.delete` | an entity is added (or a new observation merged into it) or deleted: its `type` and `key`; a graph commit that fails after the event is followed by a `failure` event (`graph_commit_failed`) |
| `relationship.create` | a relationship is added or observed again: its `type`, `source_key` and `target_key` |
| `job.create` | a Shuttle job is queued: `shuttle`, `version`, `mode`, `sensitivity`, the input's `entity_type` and key (hashed for person-related types); `failure` with `too_many_jobs` when the per-user limit refuses it (`429`) |
| `view.create`, `view.update`, `view.delete` | a [saved view](#saved-views) is saved, renamed or replaced, or deleted: the view's ID as the target and the case's ID only, never its name (names may be person-related) or its state |
| `job.cancel` | a job is cancelled: `status_before` and `status` |
| `job.finish` | a job ends, recorded by the worker (`system:worker`) in the transaction that sets its final state: `status`, `stats` and a `reason` code; `success` (succeeded or cancelled), `failure`, or `denied` when a guardrail re-checked at execution time stopped it. Never a response, a finding's value or an exception's text |

**What is never recorded.** Passwords, password hashes, session and CSRF tokens, cookies, request bodies and secrets from the configuration. `details` is built from a few named fields, never a whole request body, and passes a sanitizer that drops any key containing `password`, `token`, `secret`, `cookie`, `csrf` or `hash` (case-insensitive, at any depth), cuts strings at 512 characters and replaces a value over 8 KB with a size marker. Entity keys of [person-related types](#entity-types) (an email address, a name, a phone number) are recorded only as their SHA-256 (`key_sha256`, `source_key_sha256`, `target_key_sha256`, and a `target_id` of `sha256:<hex>`): audit events outlive the case, and personal data must not survive the case's deletion in a log that can never be edited. A failed sign-in stores no email at all: `actor_label` stays empty and `details` holds only the SHA-256 of the normalized address (`email_sha256`), so repeated attempts can be correlated without keeping typed addresses (or passwords pasted into the email field). That digest is pseudonymous, not anonymous: anyone who already has a candidate address can compute it. Reading the log is not itself recorded.

**Same transaction.** A successful change records its event in the same database transaction, right before the commit, so the change and its event commit or roll back together: a rolled-back change leaves no event. Failures and refusals (failed sign-ins, `429`, `403`, failed admin actions) describe something that did not happen, so they are written in their own short transaction; if that write fails, the error is logged and the client still gets its `401`/`403`/`429`.

**Hash chain.** Each event's `hash` is the SHA-256 of a canonical JSON document of every other column of the row, including `prev_hash` (keys sorted, no whitespace, UTF-8, timestamps as UTC ISO 8601 with microseconds, UUIDs and IP addresses in standard text form). `prev_hash` is the previous event's `hash`, and 64 zeros for the first event. Appends are serialized: inside the transaction the appender takes `pg_advisory_xact_lock` on a fixed key, draws the next ID, reads the latest hash and inserts its row; the lock is held until the transaction ends, so ID order is chain order. `hash` and `prev_hash` are both unique, so the chain cannot fork. Floats in `details` are stored as text, because `jsonb` may rewrite a number's form (`1e100` comes back as an integer) and change the hash.

**Verification.** `POST /api/v1/audit/verify` walks the chain in ID order in batches of 1,000 rows (keyset pagination), recomputes each hash in a worker thread so the event loop is never blocked for long, and returns `{ok, checked, first_bad_id, reason}`: the first event whose `prev_hash` does not match its predecessor (an event was removed or reordered) or whose contents do not match its `hash` (an event was edited). Only one verification runs at a time (a Valkey lock; others get `429`), and each run is itself recorded as `audit.verify`.

**Append-only.** Migration `0003_audit_events` installs a trigger that raises on every `UPDATE`, `DELETE` and `TRUNCATE` of `audit_events`, so neither the application nor a mistaken query can change history. For the same reason the foreign key from `actor_user_id` to `users` is `ON DELETE RESTRICT`: setting it to `NULL` would rewrite the event and break its hash, so a user with audit history can be deactivated but not deleted.

**Listing.** `GET /api/v1/audit` returns events newest first (by ID) as `{items, next_before_id}`; pass `next_before_id` back as `before_id` for the next page. `limit` is 1–200 (default 50). Filters: `action`, `outcome`, `actor_user_id`, `case_id`, `since` (inclusive) and `until` (exclusive), the last two timezone-aware. `prev_hash` and `hash` are left out unless `include_hashes=true`.

**Honest limits.**

- The trigger and the chain stop accidental changes and make edits by the application visible; they do not stop someone with full control of the database. The API's database role owns the table, so it (or a PostgreSQL superuser) can disable the trigger, rewrite events and recompute every later hash, and verification would pass. Cutting off the newest events also leaves a valid, shorter chain. Mitigations for later: periodically anchor the latest hash outside the database (an external append-only store, a signed timestamp, or simply a copy kept by an operator), so a rewritten or shortened chain no longer matches the anchor; and run the API with a role that does not own `audit_events` and has only `INSERT` and `SELECT` on it.
- The log only knows what the API tells it. Actions taken directly in the database, and events whose own-transaction write failed (logged as an error), are not in it.
- Adding a column to `audit_events` changes what is hashed; it will need a format version in the canonical document so older events still verify.

**No foreign key to cases.** `audit_events.case_id` deliberately does not reference `cases`. A case's audit trail must outlive the case (so an admin can still read it with `GET /audit?case_id=...` after deletion), and audit rows can never change, which rules out both `ON DELETE SET NULL` and `CASCADE`.

### Cases

A case is one investigation (code in [`nightloom_api/cases/`](../apps/api/src/nightloom_api/cases/)). Every case states **why** it exists before anything is collected, as [ETHICS.md](../ETHICS.md) requires.

| Field | Rules |
| --- | --- |
| `title` | 3–200 characters |
| `description` | optional, at most 5,000 characters |
| `purpose` | **required**, 30–2,000 characters |
| `legal_basis` | **required**, one of the bases below |
| `legal_basis_note` | at most 1,000 characters; **required** for `authorized_engagement` and `other` |
| `ethics_ack_version`, `ethics_ack_at` | creating a case requires `ethics_acknowledged: true`; the server stores the version of ETHICS.md in force (`ETHICS_VERSION`, currently `2026-11`) and the time |
| `allow_person_entities` | default `false`. Turning it on at creation needs a `person_entities_reason` (at least 30 characters), which is stored only in the `case.create` audit event |
| `status` | `open` (default), `closed` or `archived`. Closing sets `closed_at`; reopening clears it; archiving leaves it as it was |
| `retention_days` | optional, 1–3,650. **Not enforced yet**: nothing deletes old case data automatically |
| `created_by`, `created_at`, `updated_at`, `closed_at` | set by the server |

Legal bases: `owned_assets`, `authorized_engagement`, `threat_intelligence`, `journalism`, `academic_research`, `self_assessment`, `other`. The same rules are CHECK constraints in PostgreSQL (migration `0004_cases`), so a bug in the API cannot store a case without them.

**Who may do what.** Only users whose global role is `admin` or `analyst` can create cases; the creator becomes the case's owner. After that, access comes **only** from case membership (`case_members`: one role per user and case):

| Capability | viewer | editor | owner |
| --- | :-: | :-: | :-: |
| Read the case and its member list | ✓ | ✓ | ✓ |
| Change title and description | | ✓ | ✓ |
| Run and cancel Shuttle jobs | | ✓ | ✓ |
| Read the case's audit events | | ✓ | ✓ |
| Export the graph with person data redacted | ✓ | ✓ | ✓ |
| Export person-related data unredacted (with a reason) | | | ✓ |
| Change purpose, legal basis, status, `allow_person_entities`, retention | | | ✓ |
| Add members, change roles, remove others | | | ✓ |
| Delete the case | | | ✓ |
| Leave the case (remove yourself) | ✓ | ✓ | ✓ |

- **Admins have no implicit access.** A global admin who is not a member of a case cannot read or change it, like anyone else. Case data stays with the people working on the case; admins manage accounts, not investigations.
- **Global viewers are always case viewers.** They can only be added with the `viewer` case role (`422` otherwise), and if a member's global role is later changed to `viewer`, their case role is treated as `viewer` everywhere.
- **404, not 403, for outsiders.** A user who is not a member gets `404 Case not found.` for every case route, exactly as for a case that does not exist, so case IDs reveal nothing. A member without the needed capability gets `403`. Both are recorded as `authz.denied` with the case's ID.
- **At least one owner.** Demoting or removing the last owner (including an owner removing themselves) is refused with `409`. Only active owners whose global role is not `viewer` count, because only they can manage the case. Membership changes lock the case row (`FOR NO KEY UPDATE`) and re-read the caller's role after the lock is granted, so concurrent changes are serialized and cannot both remove "the other" owner.
- **Reasons for sensitive changes.** Changing `purpose`, `legal_basis`, `legal_basis_note` or `allow_person_entities` requires a `change_reason` (10–500 characters), stored in the `case.update` event. Setting a field to the value it already has is not a change.
- **Deletion.** The owner repeats the exact title (`confirm_title`, case-sensitive). The case and its memberships are deleted in one transaction; the audit events stay. **After** that commits, the case's [graph](#per-case-graphs) is deleted from Neo4j in batches of 1,000 nodes, so a Neo4j failure can never bring a case back or leave it half-deleted. That step never fails the request (the case is already gone): it is recorded as `case.graph_delete` (`success` or `failure`), and a failure is also logged; nodes left behind are unreachable through the API, and an operator removes them with Cypher (`MATCH (n:Entity {case_id: $id}) DETACH DELETE n`). Evidence objects will be removed the same way once they exist.

| Endpoint (under `/api/v1`) | Who | Notes |
| --- | --- | --- |
| `POST /cases` | admin, analyst | `201`, the caller becomes owner |
| `GET /cases` | signed in | the caller's cases with `my_role`, most recently updated first; `{items, next_cursor}`, pass `next_cursor` as `cursor`; `limit` 1–200 (default 50); filters `status` and `q` (case-insensitive title search; `%`, `_` and `\` match literally) |
| `GET /cases/{id}` | member | |
| `PATCH /cases/{id}` | editor, owner | editors: `title`, `description`; owners: every field |
| `DELETE /cases/{id}` | owner | body `{confirm_title}`; `204`, or `422` if it does not match |
| `GET /cases/{id}/members` | member | owners first |
| `PUT /cases/{id}/members` | owner | `{email, role}`: adds an active user (`201`) or changes their role (`200`); unknown or inactive email: `404 No active user with that email.` |
| `DELETE /cases/{id}/members/{user_id}` | owner, or the member themselves | `204`; `409` for the last owner |
| `GET /cases/{id}/audit` | editor, owner | the case's audit events, paged and filtered like `GET /audit` (without `actor_user_id`, `case_id` and hashes) |

The case's graph has its own endpoints, listed under [Per-case graphs](#per-case-graphs).

#### Personal layouts

Each member arranges their own view of a case's graph: the entities they pinned, and where (code in [`nightloom_api/layouts/`](../apps/api/src/nightloom_api/layouts/), migration `0007_graph_layouts`). One row per user and case in `graph_layouts`: `case_id` and `user_id` (the primary key, both foreign keys with `ON DELETE CASCADE`, so a layout goes with its case or its user), `positions` (JSONB, entity key → `[x, y]` in graph coordinates, default `{}`) and `updated_at`.

| Endpoint (under `/api/v1`) | Who | Notes |
| --- | --- | --- |
| `GET /cases/{id}/layout` | member | `{positions, updated_at}`; `{}` and `null` when the caller has none. Keys of entities the case no longer has are left out (checked against Neo4j on each read; `503` if it is down) |
| `PUT /cases/{id}/layout` | member | `{positions}` replaces the caller's layout; `204` |

- **A personal preference, not case data.** Both need only the `read` capability, so viewers have layouts too, and both work whatever the case's status (closed and archived included). A layout changes nothing anyone else sees. Writes are **not audited**. Non-members get `404` like every case route, and the CSRF header is required as for every `PUT`.
- **Validation.** Keys must be canonical entity keys (the same `parse_key` check as the graph routes; group nodes are therefore never saved), each value exactly two finite numbers within ±1,000,000 (strict: no strings or booleans), at most 10,000 entries, and no other fields (`422`, without echoing the input, since a key can be person-related). The body is read with a 1 MiB cap and refused with `413` as soon as it is larger, with or without a `Content-Length`.
- **Rate limit.** At most 60 saves per user per minute, across cases and sessions (a fixed window in Valkey, `nightloom:layout:user:<id>`), then `429` with `Retry-After`. The web client debounces its saves and stays far below this. Reads are not limited.

#### Saved views

Named snapshots of how a case's graph looks, shared with the case's members (code in [`nightloom_api/views/`](../apps/api/src/nightloom_api/views/) and [`routers/views.py`](../apps/api/src/nightloom_api/routers/views.py), migration `0008_saved_views`; the web side is [`SavedViews.tsx`](../apps/web/src/cases/graph/SavedViews.tsx) and [`viewSnapshot.ts`](../apps/web/src/graph/viewSnapshot.ts)). One row per view in `saved_views`: `id`, `case_id` (`ON DELETE CASCADE`, indexed), `name`, `created_by` and `updated_by` (`ON DELETE SET NULL`: a view outlives its author, shown as "a former member"), `state` (JSONB), `created_at`, `updated_at`; a unique index on `(case_id, lower(name))` makes names unique per case ignoring case.

| Endpoint (under `/api/v1`) | Who | Notes |
| --- | --- | --- |
| `GET /cases/{id}/views` | member | `[{id, name, created_by, updated_by, created_at, updated_at}]` by name (authors as `{id, display_name}` or `null`); no state, so the list stays light |
| `GET /cases/{id}/views/{view_id}` | member | the view with its `state`; positions of entities the case no longer has are left out (checked against Neo4j, `503` if it is down) |
| `POST /cases/{id}/views` | `edit` (editors, owners), open case | `{name, state}` → `201` with the view |
| `PATCH /cases/{id}/views/{view_id}` | `edit`, open case, and the view's creator or a case owner | `{name}`, `{state}` or both → the view |
| `DELETE /cases/{id}/views/{view_id}` | as `PATCH` | `204` |

- **Permissions.** Like every case route: non-members get `404` for everything; a member without `edit` (viewers, and global viewers whatever their stored role) gets `403`, recorded as `authz.denied`. Changing someone else's view as an editor is `403` too, recorded with the reason `not_view_creator` and the view's ID; owners may change any view. A creator demoted to viewer can no longer change their views. Writes to a closed or archived case get `409`, as for the graph; reading works whatever the status. A view ID of another case is `404` (`View not found.`). Write routes take the case row lock, so a case cannot be closed or deleted half-way through, and saves in one case are serialized (the count and name checks are exact).
- **State, version 2** (version 1, 2D only, is still accepted and read). Validated with pydantic, unknown fields refused at every level (`422`, without echoing the input). A version-1 state has `"version": 1`, `"mode": "2d"` and no `camera3d` or `depth`:

  ```json
  {
    "version": 2,
    "mode": "3d",
    "camera": {"x": 12.5, "y": -40.0, "ratio": 0.35, "angle": 0.5236},
    "camera3d": {"position": [12.5, -400.0, 300.0], "target": [12.5, -40.0, 0.0], "up": [0.0, 0.0, 1.0]},
    "depth": "time",
    "filters": {
      "hidden_types": ["url"],
      "hidden_sources": ["wayback"],
      "hidden_sensitivities": [],
      "seen": {"kind": "range", "from": "2026-11-01", "to": null},
      "min_confidence": 0.5,
      "min_reliability": "B",
      "min_credibility": 3,
      "focus": {"key": "domain:example.org", "hops": 2},
      "hide_isolated": false
    },
    "groups": {"expanded": ["group:domain:www.example.org|url"]},
    "positions": {"domain:example.org": [0.0, 0.0], "domain:www.example.org": [120.0, 60.0]}
  }
  ```

  `camera` is in graph terms, so it means the same on every screen and whatever the layout of unpinned entities: `x`, `y` are the graph coordinates at the center of the view (finite, ±1,000,000), `ratio` is graph units per screen pixel (1e-6 to 10,000) and `angle` the rotation in radians (±2π). `mode` is `2d` or `3d`; a 3D view's `camera` is its 3D camera seen from the front, so any client can show it in 2D. `camera3d` (optional, views saved in 3D) has a `position`, a `target` (each three finite coordinates within ±1,000,000) and an `up` direction (three numbers in [-1, 1], not all zero); the position must differ from the target. `depth` is `time`, `type` or `flat` (see [3D view](#3d-view)). `filters` is the web's URL filter state as JSON (`seen` is `{"kind": "all"}`, a `preset` of `24h`, `7d` or `30d`, or a `range` of `YYYY-MM-DD` days with open ends; `min_confidence` 0–1; `min_reliability` A–F; `min_credibility` 1–6; `focus.key` a canonical key, `hops` 1–3; at most 100 sources of up to 100 characters; every field defaults to "no filter"). `groups.expanded` holds up to 500 group ids (`group:<canonical key>|<entity type>`). `positions` follows the [personal layout](#personal-layouts) rules (canonical keys, finite, ±1,000,000, at most 10,000). Lists are de-duplicated. Each version is its own model, told apart by `version`; stored version-1 states stay readable and are returned as they were saved.
- **Limits.** `name` is trimmed, 1–80 characters, without control characters (including line and paragraph separators). The body is read with a 1 MiB cap (`413`). At most 50 views per case (`409`, with a message saying to delete one). Writes (save, change, delete) are limited to 30 per user per minute across cases (a fixed window in Valkey, `nightloom:views:user:<id>`, the layout limiter with its own key), then `429` with `Retry-After`; reads are not limited.
- **Audit.** `view.create`, `view.update` and `view.delete`, in the same transaction as the change, with the view's ID as the target and the case's ID; `details` is empty. The name and state are never recorded.
- **Web.** The case Graph tab gives `GraphView` a *Views* menu (through a React context, so the demo has none). `GraphView` captures a snapshot (the mode and depth, the camera from sigma or the 3D view (both the 3D camera and its 2D equivalent), the filters, the expanded groups, the pin store's entities), saved as version 2, and applies one (switching to 3D for a 3D view, with its depth and camera; a version-1 view opens in 2D): filters through the URL like any filter change, the expanded groups, `PinStore.replace` (which the [layout](#personal-layouts) saver then saves as the user's own pins) and, once that is drawn, the camera, animated over 400 ms (at once with `prefers-reduced-motion`). Applying freezes sigma's frame, so the picture stays put while unpinned entities settle. The message after applying offers *Undo* (the snapshot taken just before) until it is dismissed. `?view=<id>` is applied once the graph is drawn and then removed from the URL with a history replace; an unknown or foreign ID only shows a quiet notice.

### Migrations

The PostgreSQL schema is managed by [Alembic](https://alembic.sqlalchemy.org/) in [`apps/api/alembic/`](../apps/api/alembic/). ORM models derive from `nightloom_api.infra.db.Base`, whose metadata has an explicit naming convention, so constraint and index names are stable across autogenerated revisions. The history starts at an empty `0001_baseline` revision; `0002_users_sessions` adds the `users` and `sessions` tables and the `user_role` enum; `0003_audit_events` adds the [audit log](#audit-log) and its append-only trigger; `0004_cases` adds [`cases` and `case_members`](#cases) with their enums (and intentionally no foreign key from `audit_events.case_id`); `0005_jobs` adds [`jobs`](#jobs-and-the-worker) and the `job_status` enum; `0006_must_change_password` adds `users.must_change_password` (see [Temporary passwords](#temporary-passwords-and-the-forced-change)); `0007_graph_layouts` adds [`graph_layouts`](#personal-layouts); `0008_saved_views` adds [`saved_views`](#saved-views). In the Compose stack, a one-shot `migrate` service runs `alembic upgrade head` and then applies the [Neo4j schema](#per-case-graphs) (`python -m nightloom_api.graph.schema apply`) before the API starts; `make migrate` runs it again.

## Data model

The entity model lives in the shared package [`packages/core`](../packages/core/) (`nightloom-core`, import `nightloom_core`), so the API and, later, the worker and Shuttles validate data the same way. It has no service dependencies and never touches the network.

### Entity types

Each type is a Pydantic model (`nightloom_core.entities`) with a canonical `value` and optional typed `properties`. Every type declares a **sensitivity**. Person-related types can only be added to cases with `allow_person_entities` (see [ETHICS.md](../ETHICS.md)); the API refuses them otherwise with `403 This case does not allow person-related entities.`, recorded as `authz.denied`.

| Type (`type`) | Neo4j label | Sensitivity | Properties |
| --- | --- | --- | --- |
| `domain` | `Domain` | infrastructure | DNS facts, written by the [DNS Shuttle](#the-dns-shuttle): `dns_txt`, `dns_spf`, `dns_dmarc`, `dns_mx`, `dns_caa` (lists of at most 50 records of at most 1,000 characters), `dns_soa_primary_ns`, `dns_soa_responsible`, `dns_soa_serial`, `dns_soa_refresh`, `dns_soa_retry`, `dns_soa_expire`, `dns_soa_minimum`; registration data, written by the [RDAP Shuttle](#the-rdap-shuttle): `registered`, `expires`, `last_changed` (ISO 8601 date-times), `status` (at most 30 EPP statuses), `dnssec_signed`, `registrar`, `registrar_iana_id`, `rdap_server`; archive data, written by the [Wayback Shuttle](#the-wayback-shuttle): `wayback_first_capture`, `wayback_last_capture`, `wayback_url_count` |
| `ip_address` | `IPAddress` | infrastructure | `scope` (`public` or `private`: not globally routable); exposure data, written by the [InternetDB Shuttle](#the-internetdb-shuttle): `open_ports` (TCP ports, ascending, at most 200), `cpes` (CPE names, at most 100), `vulns` (CVE IDs, ascending, at most 500), `tags` (at most 50), `internetdb_checked_at`; from the [reverse DNS Shuttle](#the-reverse-dns-shuttle): `ptr_checked_at` |
| `netblock` | `Netblock` | infrastructure | `name`, `country`, `registry` (the Regional Internet Registry); from RDAP also `handle`, `type` (as the registry names it, e.g. `DIRECT ALLOCATION`), `registered`, `last_changed`, `parent_handle`; from [RIPEstat](#the-ripestat-shuttle) `announced`, `bgp_first_seen`, `bgp_last_seen`, `iana_block`, `iana_block_description` |
| `asn` | `ASN` | infrastructure | `name`, `country`, `registry`; from RDAP also `handle`, `registered`, `last_changed`; from [RIPEstat](#the-ripestat-shuttle) `as_name` (the network name in the AS holder), `announced`, `iana_block`, `iana_block_description` |
| `certificate` | `Certificate` | infrastructure | `subject`, `subject_cn`, `issuer`, `serial_number`, `not_before`, `not_after`, `self_signed`, `san_count` (entries in the subjectAltName extension), `crtsh_id` (written by the [crt.sh Shuttle](#the-crtsh-shuttle)) |
| `url` | `URL` | infrastructure | `status_code`, `title`, `content_type`; archive data, written by the [Wayback Shuttle](#the-wayback-shuttle): `first_captured`, `last_captured`, `capture_count`, `last_status`, `mimetype`, `archive_url` (only `https://web.archive.org/web/…`), `query_params` (parameter names only, at most 30 of at most 64 characters) |
| `service` | `Service` | infrastructure | `product`, `version`, `banner` |
| `technology` | `Technology` | infrastructure | `version`, `category` |
| `organization` | `Organization` | organization | `country`, `industry` |
| `crypto_wallet` | `CryptoWallet` | organization | |
| `document` | `Document` | organization | `title`, `mime_type`, `sha256` |
| `breach` | `Breach` | organization | `breach_date`, `record_count`, `data_classes` (metadata only, never leaked records) |
| `person` | `Person` | **person** | |
| `email` | `Email` | **person** | |
| `username` | `Username` | **person** | `platform` |
| `phone` | `Phone` | **person** | |
| `social_profile` | `SocialProfile` | **person** | `platform`, `profile_url` |
| `location` | `Location` | **person** (a place next to a person is sensitive) | `latitude`, `longitude`, `country` |
| `image` | `Image` | **person** (images can show people) | `mime_type`, `sha256`, `width`, `height` |

Properties are flat (strings, numbers, booleans, dates as ISO 8601, lists of strings or of integers), validated per type (unknown fields are refused) and stored on the node with a `prop_` prefix. A later observation overwrites the properties it carries and keeps the others.

### Canonical keys

Every entity has exactly one key, `<type>:<normalized>`, so the same thing found in different spellings becomes one node (`nightloom_core.normalize`). Normalizers are idempotent, and their errors (`NormalizationError`) have short, user-safe messages that never repeat the input; the API returns them as `422`.

| Type | Normalized form |
| --- | --- |
| Domain | trimmed, one trailing dot removed, UTS #46 mapping (lower-case, full-width forms), non-ASCII labels in IDNA 2008 A-label form (`Bücher.example` → `xn--bcher-kva.example`); at least two labels, each 1–63 characters, at most 253 in all, last label not numeric; `_` allowed (`_dmarc`), and a leading `*` label (certificate wildcards). The registrable domain (`www.example.co.uk` → `example.co.uk`) comes from tldextract's **bundled** Public Suffix List snapshot only: no cache directory, no download, ever |
| IP address | IPv4 in dotted decimal. An **IPv4-mapped** IPv6 address (`::ffff:0:0/96`) is the IPv4 host itself and becomes the plain IPv4 address (`::ffff:192.0.2.1`, `::FFFF:c000:201` → `192.0.2.1`), so one host is one node. Every other IPv6 address stays IPv6 in pure-hex RFC 5952 form (lower-case, no leading zeros, the longest run of two or more zero groups as `::`), including the forms that embed IPv4 but are different addresses on the wire: IPv4-compatible `::192.0.2.1` → `::c000:201`, 6to4 `2002:c000:201::1`, NAT64 `64:ff9b::192.0.2.1` → `64:ff9b::c000:201`. The text is built from the address's integer value, never from `ipaddress`'s `str()`, whose output for embedded IPv4 changed between Python 3.12 patch releases; CI checks the keys on 3.12.0, the latest 3.12 and 3.13. Zone IDs refused |
| Netblock | CIDR with host bits cleared (`192.0.2.7/24` → `192.0.2.0/24`); a bare address becomes `/32` or `/128`. An IPv6 network inside `::ffff:0:0/96` with a prefix of 96 or more becomes the equivalent IPv4 network (`::ffff:192.0.2.0/120` → `192.0.2.0/24`); other IPv6 networks use the same pure-hex form |
| ASN | key `asn:64496`, value `AS64496`, from `AS64496`, `as64496` or `64496`; 1–4294967295 |
| Certificate | SHA-256 fingerprint, 64 lower-case hex digits (colon-separated input accepted) |
| URL | `http`, `https` or `ftp` only; scheme lower-case, host by the Domain or IP rules (IPv6 in brackets; an IPv4-mapped host becomes plain IPv4: `http://[::ffff:192.0.2.1]:80/` → `http://192.0.2.1/`), default port removed, fragment dropped, empty path → `/`; path and query kept as they are; embedded credentials refused |
| Service | `ip:port/protocol` (`[ipv6]:port/protocol`, the address by the IP rules), port 1–65535, `tcp` or `udp` |
| Email | dot-atom local part, lower-cased; domain by the Domain rules |
| Phone | E.164 via `phonenumbers` (`+442079460958`), valid numbers only. A number in national format needs an explicit default region (`phone_region` in the API); there is no implicit default |
| CryptoWallet | `btc:<address>` (mainnet base58check P2PKH/P2SH or bech32/bech32m SegWit, checksums verified, bech32 lower-cased) or `eth:0x<40 hex>` (lower-cased) |
| Organization, Person, Technology, Location, Username, SocialProfile, Document, Image, Breach | NFKC, trimmed, inner whitespace collapsed (the display value keeps its case); the key is case-folded (`organization:acme corp`) |

`parse_key()` accepts a key only if normalizing its own value gives the key back, so `GET /entities/domain:Example.org` is `422`, not a silent miss.

### Relationships

Relationship types (`nightloom_core.relationships`) have an explicit allow-list of (source type, target type) pairs. Anything else is refused with `422`.

| Type | Source → target |
| --- | --- |
| `HAS_SUBDOMAIN`, `HAS_MX`, `HAS_NS` | Domain → Domain |
| `ALIAS_OF` | Domain → Domain: the source name is a CNAME alias of the target name |
| `RESOLVES_TO` | Domain → IPAddress |
| `HAS_PTR` | IPAddress → Domain: the address's reverse DNS (PTR) records name that host; whether forward DNS confirms it is in the note (`forward_confirmed: true` or `false`) |
| `WITHIN` | IPAddress → Netblock, or Netblock → Netblock (a network inside a larger one) |
| `ANNOUNCED_BY` | Netblock → ASN |
| `COVERS` | Certificate → Domain |
| `PRESENTS` | Service → Certificate |
| `RUNS_ON` | Service → IPAddress |
| `USES` | Domain, URL or Service → Technology |
| `OWNS` | Organization → Domain, Netblock or ASN |
| `HOSTS` | Domain → URL |
| `REGISTERED_BY` | Domain → Organization |
| `HAS_EMAIL` | Domain → Email |
| `ACCOUNT_OF` | Username, SocialProfile or Email → Person |
| `MENTIONS` | Document → any type |
| `RELATED_TO` | any → any; discouraged, and needs a `note` explaining the link |

Both ends must exist in the same case and be different entities. There is at most one relationship of a type in one direction between two entities; repeating it adds an observation. If a case stops allowing person entities, relationships touching its existing person-related entities are refused (`403`) as well.

### Provenance

Every sighting is an `Observation` (`nightloom_core.provenance`): `source` (`manual` or `shuttle:<name>@<version>`), `observed_at` (timezone-aware), `confidence` (0–1), an Admiralty rating — source reliability `A` (completely reliable) to `E` (unreliable) and `F` (cannot be judged), information credibility `1` (confirmed) to `5` (improbable) and `6` (cannot be judged) — an optional `evidence_ref` and an optional `note` (at most 500 characters).

Observations are merged into the node or relationship they describe, which carries the summary:

| Property | Meaning |
| --- | --- |
| `first_seen` / `last_seen` | earliest and latest `observed_at` (an older observation arriving late moves `first_seen` back) |
| `observation_count` | how many observations were merged |
| `sources` | distinct sources (at most 100) |
| `max_confidence` | the highest confidence seen |
| `last_admiralty`, `note` | of the most recent observation (the note only when it has one) |
| `evidence_refs` | distinct evidence references (at most 100) |

**Manual defaults.** Entities and relationships added through the API are `source: manual` with reliability **B** (the analyst is a usually reliable source for their own case), credibility **2** ("probably true": deliberately entered, but not independently confirmed, so not `1`) and confidence **0.8**. A Shuttle that later observes the same entity adds its own rating; `max_confidence` keeps the best.

### Per-case graphs

Every entity node has the label `:Entity` plus its type's label and the properties `case_id`, `key`, `type`, `value` (the first value seen), `sensitivity` and the provenance summary. Relationships carry `case_id` and the provenance summary.

**Isolation.** The same key in two cases is two nodes. Every query (code in [`nightloom_api/graph/`](../apps/api/src/nightloom_api/graph/)) matches on `case_id`, and relationships are only created between two nodes of the same case, so one case can never read or change another's graph. Case access itself is checked first, exactly like every other [case route](#cases).

**Schema and constraint choice.** `python -m nightloom_api.graph.schema apply` (idempotent, `IF NOT EXISTS`; `show` lists it) creates:

- `entity_case_key_unique`: a **composite node property uniqueness constraint** on `(:Entity {case_id, key})`. Neo4j Community Edition supports it (only node *key* and property *existence* constraints are Enterprise-only), so no combined `scoped_key` property is needed: the two natural properties stay separate and queryable, and the constraint's backing range index serves every `MERGE`/`MATCH` by `(case_id, key)`. Because existence constraints are not available, the writing code always sets both properties.
- `entity_case_id` on `(:Entity {case_id})`, for whole-case reads and deletion, and `entity_case_type` on `(:Entity {case_id, type})`, for type filters.

**Queries are parameterized.** Values, keys and case IDs are always query parameters. Labels and relationship types are parameters too (Cypher's dynamic `$(...)` syntax), and only after they have been checked against `EntityType` and `RelationType`. No user-supplied text is ever formatted into a query.

**Writes and the audit log.** A graph change and its audit event live in two databases. A write takes the case row lock for the whole request (so the case cannot be closed or deleted half-way), runs the Neo4j transaction, records and commits the audit event in PostgreSQL, and then commits Neo4j. An event is therefore never missing for a change that happened; if the final Neo4j commit fails, a `failure` event (`graph_commit_failed`) follows and the client gets `503`. Writes are not retried, so an unreachable Neo4j gives a quick `503 The graph database is unavailable.` Only the exception type and Neo4j's status code are logged, never keys or values.

| Endpoint (under `/api/v1/cases/{id}`) | Who | Notes |
| --- | --- | --- |
| `POST /entities` | editor, owner; case `open` | `{type, value, properties?, note?, phone_region?}`; `201` new, `200` merged into an existing entity; `422` normalization or property errors; `403` person-related type in a case that does not allow them; `409` case not open |
| `GET /entities/{key}` | member | the entity and its number of `neighbours`; percent-encode the key (`/` included); `422` non-canonical key, `404` unknown |
| `DELETE /entities/{key}` | editor, owner; case `open` | `204`; deletes its relationships too |
| `POST /relationships` | editor, owner; case `open` | `{type, source_key, target_key, note?}`; `201` / `200` like entities; `422` unknown endpoint, pair not allowed, missing `RELATED_TO` note |
| `GET /graph` | member | `?types=domain&types=ip_address&limit=2000` (1–10,000): nodes ordered by key, the edges between them (at most five per node), and `truncated` when either limit cut something off |

A closed or archived case's graph is read-only (`409` for every write).

### Exports

`GET /api/v1/cases/{id}/export?format=json|graphml|csv` (code in [`nightloom_api/export/`](../apps/api/src/nightloom_api/export/) and [`routers/export.py`](../apps/api/src/nightloom_api/routers/export.py)) downloads a case's whole graph. It is a plain `GET`, so the browser follows a normal link: the session cookie authenticates it and no CSRF token is needed (a `GET` changes nothing but the rate-limit counter and the audit log).

| Format | Contents |
| --- | --- |
| `json` | `{"case": {id, title, legal_basis, exported_at, exported_by_label, nightloom_version, include_person_data}, "entities": [{key, type, value, sensitivity, redacted, properties, provenance}], "relationships": [{id, type, source_key, target_key, provenance}]}`; `provenance` is the full summary (first and last seen, observation count, sources, max confidence, Admiralty, evidence references, note). `exported_by_label` is the exporter's display name |
| `graphml` | GraphML 1.0 (`application/graphml+xml`) with typed keys: graph `case_id`, `title`, `legal_basis`, `exported_at`, `nightloom_version`, `include_person_data` (boolean); nodes `label`, `type`, `value`, `key`, `sensitivity`, `first_seen`, `last_seen`, `confidence` (double), `sources` (joined with `; `), `admiralty`; edges `type` and the same provenance keys. Node IDs are `n0`, `n1`, … because an entity key is not a valid XML name token (URLs contain `/` and `?`); a relationship whose ends are not both in the file (the graph changed while it was written) is left out, so the file stays valid. Characters XML 1.0 cannot carry become U+FFFD |
| `csv` | A ZIP (`application/zip`) with `entities.csv`, `relationships.csv` (UTF-8, RFC 4180, header row; lists joined with `; `, properties as a JSON object) and `README.txt` describing every column and the redaction. Cells starting with `=`, `+`, `-`, `@`, a tab or a carriage return get a leading `'`, so spreadsheets show them as text instead of running them (CSV injection) |

**Streaming.** All three are written as they are read, in chunks of about 64 KB, from one Neo4j read transaction (300-second limit) with the entities ordered by key and the relationships by source, type and target. The ZIP is written to an unseekable stream (sizes in data descriptors, ZIP64 for the CSV members). A Neo4j failure part-way is logged (exception type only) and ends the download early. The response has `Content-Disposition: attachment; filename="nightloom-<case-slug>-<UTC timestamp>.<json|graphml|zip>"` (the slug is the title in lower-case ASCII, other characters as `-`, at most 50 characters, `case` when nothing is left), `Cache-Control: no-store`, `Pragma: no-cache` and no length.

**Redaction design.** Unless `include_person_data=true`, every entity whose sensitivity is `person` keeps its `type` and `sensitivity`, but its `value` becomes `[redacted]`, its properties, note and evidence references are dropped, and its key becomes `redacted:` plus the first 32 hex digits of HMAC-SHA256(secret, real key). The secret is 32 random bytes drawn for that one export, held in memory only and never logged or stored. So, within one file, the same entity always gets the same pseudonymous key and every relationship touching it still connects; across files the pseudonyms differ, so two exports cannot be joined on them, and without the secret a key cannot be checked against a guessed value (a plain hash of an email address could be). Relationships touching a redacted entity lose their note and evidence references, which can name the person. Redacted entities, and the relationships touching them, are collected and sorted by their pseudonymous key after the rest has been streamed: in key order they would sit in the alphabetical order of their real keys. An entity of unknown sensitivity is treated as personal. With `include_person_data=true` nothing is redacted; that needs the case **owner** role (`403` and `authz.denied` with `required: export_person_data` otherwise) and a `reason` query parameter of 10–500 characters after trimming (`422` otherwise), which goes into the `case.export` event.

**Limits.** A case with more than 100,000 entities is refused with `413` and a message saying so. Each user may start 10 exports per 10 minutes (a fixed window counted in Valkey under the user's ID, across cases): the eleventh gets `429` with `Retry-After`. If Valkey is unreachable, exports fail closed with `503`. Both refusals are recorded as `case.export` failures.

**Checking first.** `GET /cases/{id}/export/check` takes the same parameters and answers the same `403`, `413`, `422` and `429` (with `Retry-After`) without exporting, counting against the limit or writing a `case.export` event; on success it returns `{entities, person_entities, relationships, max_entities, include_person_data, exports_remaining}`. The web UI calls it before following the download link, because a link cannot report an error back to the page.

| Endpoint (under `/api/v1/cases/{id}`) | Who | Notes |
| --- | --- | --- |
| `GET /export` | member; owners for `include_person_data=true` | `format` (required), `include_person_data`, `reason`; streamed download; `403`, `413`, `422`, `429`, `503` |
| `GET /export/check` | member | the same checks, no download |

### Schema endpoint

`GET /api/v1/schema` (any signed-in user; `401` otherwise) describes the entity model as data, so clients never keep their own copy of its rules. Everything in it is generated from `nightloom_core` ([`routers/schema.py`](../apps/api/src/nightloom_api/routers/schema.py)):

| Field | Contents |
| --- | --- |
| `entity_types` | `type`, Neo4j `label`, `display_name`, `sensitivity`, a `hint` describing a valid value and an `example`. Examples use documentation-only values (`www.example.org`, `192.0.2.1`, `AS64496`, …), and the tests check that each one normalizes as its type |
| `relationship_types` | `type`, `note_required`, and every allowed `(source, target)` pair in entity type order |
| `admiralty` | the `reliability` (A–F) and `credibility` (1–6) grades with their meaning |
| `manual_defaults` | what a hand-made observation records: `source` `manual`, reliability, credibility, the combined `admiralty` (`B2`), `confidence` and `note_max_length` |

The answer only changes with a deployment, so it is sent with `Cache-Control: private, max-age=300`. The web unit tests use a copy of it ([`apps/web/src/test/schema.json`](../apps/web/src/test/schema.json)). An API test fails when that copy drifts. To regenerate it, run `uv run python -m nightloom_api.routers.schema > apps/web/src/test/schema.json`.


## Shuttle contract

A Shuttle is a plugin that takes **one entity** and yields **findings**: entities and relationships. The contract lives in the Shuttle SDK ([`packages/sdk`](../packages/sdk/), `nightloom_sdk`); built-in Shuttles live in [`shuttles/`](../shuttles/). [CONTRIBUTING.md](../CONTRIBUTING.md#writing-a-shuttle) has a step-by-step guide.

### Metadata

Every Shuttle subclasses `Shuttle` and declares a frozen `ShuttleMeta`, validated when the class is defined and again when the registry loads it:

| Field | Rules |
| --- | --- |
| `name` | lowercase slug (`registrable-domain`), at most 63 characters; unique among installed Shuttles |
| `version` | Semantic Versioning (`1.0.0`); with `name` it forms the provenance source `shuttle:<name>@<version>` |
| `title`, `description` | shown to users |
| `input_types` | entity types it accepts (at least one) |
| `output_entity_types`, `output_relationship_types` | what it may emit (at least one output). Every relationship type must connect at least one pair of its input and output types under the [allow-list](#relationships) |
| `mode` | `passive` (third parties only, or computed locally) or `active` (touches the target). **Active Shuttles are refused** by the API and the worker until ownership verification exists |
| `sensitivity` | `infrastructure`, `organization` or `person`; at least the [sensitivity](#entity-types) of every input and output type, so any person-related type makes it a `person` Shuttle |
| `network` | whether it makes requests; `True` requires a `rate_limit` and is the only way to get `ctx.http` |
| `required_keys` | names of the API keys it needs (none may be supplied yet; `ctx.keys` is empty) |
| `rate_limit` | `{requests, period_seconds}` per destination host |
| `timeout_seconds` | 1–600, default 60 |
| `cache_ttl_seconds` | 0–2,592,000 (30 days), default 0 (never cached): how long a successful run's results may be reused for the same entity (see [The result cache](#the-result-cache)). Must be 0 for an `active` Shuttle. Output must depend only on the input entity's type and value |
| `terms_url`, `homepage` | optional `http(s)` URLs |
| `usage_note` | optional conditions of use of the source (e.g. *Free for non-commercial use.*): one line of plain text, at most 300 characters, no control characters, `<` or `>`. `GET /shuttles` returns it and the *Run Shuttle* panel shows it under the description, next to the terms link |
| `default_admiralty`, `default_confidence` | the provenance of its findings (default `F6`, 0.5); a finding may override them |

### Findings

`run(ctx, entity)` is an async generator. Before doing any work it may raise `InputRefusedError(RefusalReason.…)` for an input it will not handle (the DNS Shuttle refuses internal-only names, the reverse DNS Shuttle netblocks over 256 addresses): the job fails with that reason's fixed, user-safe message from the SDK, never the input or other text, and the reason code is recorded in `job.finish`. `entity` is an `EntityInput` (type, canonical value, key, properties) read from the case's graph. It yields:

- `EntityFinding(type, value, properties?, confidence?, admiralty?, note?)`
- `RelationshipFinding(type, source, target, confidence?, admiralty?, note?)`, whose ends are `EntityRef(type, value)`

Values are **raw**: Shuttles never normalize. The worker normalizes everything with `nightloom_core`, so a key is computed in exactly one way.

During a run, a Shuttle that cannot finish for a known reason raises `ShuttleFailedError(FailureReason.…)`: `source_unavailable` (the source did not answer after the Shuttle's retries), `source_invalid_response` (an answer it could not read), `result_too_large` (too large even after narrowing) or `not_covered` (the source has no service for this input, such as no RDAP server for a TLD). Like a refusal, the job fails with the reason's fixed message from the SDK and the reason code is recorded in `job.finish`; findings stored before it are kept. Any other exception ends the job with the generic `The Shuttle failed.` message.

### Context

`ShuttleContext` gives a running Shuttle:

- `logger`: messages prefixed with `[<name>]`;
- `http`: a guarded `httpx.AsyncClient` (below), only when `network=True`;
- `keys`: a read-only mapping of the declared API keys;
- `cancelled` / `raise_if_cancelled()`, and `progress(message)` (one short line, logged);
- `note(message)`: a remark kept with the job (at most 10 of 200 characters, repeats dropped), stored in the job's `stats.notes` and shown in the *Jobs* panel, but not copied into the audit log. Shuttles write fixed text and counts there, never found values or responses.

### The guarded HTTP client

[`nightloom_sdk/http.py`](../packages/sdk/src/nightloom_sdk/http.py):

- **SSRF guard after DNS resolution.** Each connection resolves the host once and refuses it unless *every* address is globally routable unicast: no private, loopback, link-local, multicast, reserved, unspecified, shared (100.64/10), documentation or site-local addresses, and no IPv6 address whose embedded IPv4 address (IPv4-mapped, 6to4, Teredo, NAT64) would be refused. It then connects to the address it checked, never the name again, so a second DNS answer cannot rebind the connection to an internal host. Unix sockets are refused and proxy environment variables are ignored. The check is a custom httpcore network backend, so it applies to every request and every redirect.
- **Schemes.** `http` and `https` only, for the first request and each redirect (at most 5).
- **Rate limit.** A token bucket per destination host from `rate_limit`: bursts up to `requests`, refilled at `requests / period_seconds`. Each request and each redirect hop takes a token.
- **Response size.** At most 5 MB, counted while streaming: on the bytes received and, for bodies read in full, on the decoded bytes, so a compressed response cannot expand past it. `Accept-Encoding: identity` is sent by default.
- **Identity and time.** `User-Agent: Nightloom/<version> (+https://github.com/TheRealNightmare/nightloom)`; connect timeout 10 s, read/write 30 s.

### Registry

Shuttles register under the entry-point group `nightloom.shuttles`, named after `meta.name`. Discovery is strict: an entry point that fails to load, is not a concrete Shuttle, has invalid metadata, is named differently from its Shuttle or duplicates a name is an error. `python -m nightloom_sdk.registry list` (`make shuttles`) lists what is installed. The API and the worker image install the same set.

**Configuration check.** A Shuttle with settings of its own overrides the class method `check_configuration()` and raises `ShuttleConfigurationError` with an operator-facing message (naming the setting, never printing a secret). The worker runs every installed Shuttle's check when it starts (Celery's `worker_init`) and **refuses to start** if one fails, so a bad setting is one clear error in the log instead of every job failing. `python -m nightloom_sdk.registry check` runs the same checks.

### The DNS Shuttle

[`shuttles/dns`](../shuttles/dns/) (`dns`, passive, infrastructure, `network=True`) asks DNS about the input domain and adds what public DNS says:

| Record | Becomes |
| --- | --- |
| A, AAAA | an IPAddress with `scope` and `RESOLVES_TO` from the name that owns the addresses (the input, or the end of its CNAME chain). Private addresses published in public DNS are recorded too, since that is a legitimate finding, with `scope: private` and a note; the Shuttle never connects to any address it finds |
| CNAME | the target Domain and `ALIAS_OF`, following the chain from the input for at most 5 steps (loops and internal-only targets end it) |
| MX | the exchanger Domain and `HAS_MX`, its preference in the relationship's note, and `dns_mx` (`"10 mail.example.org"`) on the input. A null MX (`0 .`, RFC 7505) is only the property |
| NS | the name server Domain and `HAS_NS` |
| TXT | `dns_txt` (each record's strings joined, control characters replaced, at most 50 records of 1,000 characters); SPF records (`v=spf1`) also in `dns_spf` |
| TXT at `_dmarc.<name>` | DMARC records (`v=DMARC1`) in `dns_dmarc`, also when `_dmarc` is a CNAME to a reporting provider |
| SOA | `dns_soa_*` (only at a zone apex) |
| CAA | `dns_caa` (`0 issue "letsencrypt.org"`) |

Records of a name that is a CNAME belong to its target, so MX, NS, TXT, SOA and CAA answers are only used when their owner is the input itself. A name that does not exist (NXDOMAIN) or a type it does not have is not an error. Each query has 3 seconds and 2 tries (the second at the next resolver); a query that fails both is skipped, and if every query fails the job fails. The Shuttle's rate limit (10 queries per second, burst 10) applies per resolver.

**Resolver policy, and why.** The worker host's resolver (`/etc/resolv.conf`) is never used: on a server it is often an internal resolver, which can answer for internal zones (split-horizon DNS, Active Directory, cloud-internal names), and its answers would put internal records into a case. Instead the Shuttle asks only the resolvers in `NIGHTLOOM_DNS_RESOLVERS` (comma-separated IP addresses; default `1.1.1.1,9.9.9.9`, [Cloudflare](https://developers.cloudflare.com/1.1.1.1/) ([privacy policy](https://developers.cloudflare.com/1.1.1.1/privacy/public-dns-resolver/)) and [Quad9](https://quad9.net/) ([privacy policy](https://quad9.net/privacy/policy/))). Each dnspython resolver is built with `configure=False`, one nameserver, no search list and no cache, and every query name is absolute. A resolver address that is not public (the SDK's `is_public_address`, the same check as the HTTP client's SSRF guard) is refused by the [configuration check](#registry), so the worker does not start. Host names are not accepted as resolvers (finding them would need another resolver).

**Internal-only names are refused before any query**, with a fixed job error and no findings: single-label names and names under `.local`, `.localhost`, `.internal`, `.home.arpa`, `.lan`, `.corp`, `.intranet`, `.test`, `.invalid`, `.example` and `.onion`. A public resolver cannot answer them, and asking would tell its operator about an internal name.

**Test-only mode.** `NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS=true` allows private resolver addresses and lifts the refusal for the `nightloom-test.example` zone only. It exists for the integration tests, which run a local authoritative CoreDNS server ([`deploy/compose.test.yaml`](../deploy/compose.test.yaml), `make up TEST_DNS=1`) so that CI never queries the internet. The configuration check refuses it when `NIGHTLOOM_ENV=production`.

Findings are rated **B2** (a recursive resolver's answer: usually reliable, probably true, not DNSSEC-validated here) with confidence 0.9.

### The reverse DNS Shuttle

[`shuttles/dns`](../shuttles/dns/) also provides `reverse-dns` (passive, infrastructure, `network=True`, a second entry point of the same package): the PTR names of an IPAddress, or of every address in a small Netblock, checked against forward DNS. It imports the DNS Shuttle's configuration and resolver client ([`reverse.py`](../shuttles/dns/src/nightloom_shuttle_dns/reverse.py)), so the [resolver policy](#the-dns-shuttle) is the same in every respect: only the resolvers in `NIGHTLOOM_DNS_RESOLVERS`, never `/etc/resolv.conf`, the same configuration check, 3 seconds and 2 tries per query, and the same rate limit (10 queries per second per resolver; one constant, `RESOLVER_RATE_LIMIT`, declared by both).

| Input | Becomes |
| --- | --- |
| IPAddress | each name in its PTR records (at most 10, alphabetically) as a Domain with `HAS_PTR` from the address, and `ptr_checked_at` on the address |
| Netblock | the same for every address in it, asked in order; each address that has PTR names becomes an IPAddress (`scope: public`, `ptr_checked_at`) with `WITHIN` to the netblock |

**Forward confirmation and ratings.** A PTR record is published by whoever controls the reverse zone (usually the address's network operator), and nothing stops it from naming any host, including someone else's. So each name's A (for IPv4) or AAAA (for IPv6) records are asked for, once per name and run. If they contain the input address, the name is *forward-confirmed*: the `HAS_PTR` note says `forward_confirmed: true`, a `RESOLVES_TO` edge from the name back to the address is added, and the Domain and both edges are rated **B2** (0.9), like the DNS Shuttle's answers. If they do not (another address, no records, or a failed query), the note says `forward_confirmed: false. PTR not confirmed by forward DNS; anyone controlling the reverse zone can set any name.`, the Domain and `HAS_PTR` are rated **C3** (0.5: fairly reliable, possibly true), and there is no `RESOLVES_TO`. Relationships carry no typed properties, so, as with crt.sh's `wildcard` and RDAP's `role`, the result lives in the note. A forward answer through a CNAME still confirms, with the canonical name in the `RESOLVES_TO` note; a PTR answer through a CNAME (RFC 2317 classless delegation) is used as the address's own.

**What is skipped.** An address that is not public (the SDK's `is_public_address`) gets a job note and no query at all: a public resolver cannot know its reverse zone, and asking would tell the resolver's operator about it. In a netblock such addresses are skipped and counted. PTR names that are not valid Domains (wildcards, single labels, a numeric last label) or are internal-only (the [DNS Shuttle's list](#the-dns-shuttle) plus `.arpa` reverse names) are dropped and counted in a note, never quoted, and never queried forward.

**Sweep limits.** A netblock may hold at most 256 addresses: an IPv4 prefix of /24 or longer, an IPv6 prefix of /120 or longer. A larger one is refused before any query (`netblock_too_large`: *Reverse DNS sweeps are limited to 256 addresses; pick a /24 or smaller (for IPv6, a /120 or smaller). Nothing was queried.*). Every address is asked, network and broadcast addresses included, since PTR records often exist there too. Cancellation is checked before every query. The timeout is 300 seconds and no query starts after 240; a sweep stops adding names after 1,400 (`1,400 × 3 + 256 × 2` findings at most, under the worker's 5,000), each with a note.

**Failures.** NXDOMAIN and empty answers are quiet. SERVFAIL and timeouts are counted, and every run ends with a note: *N address(es) queried, P with PTR names, C name(s) forward-confirmed, F quer(ies) failed.* The job fails with `source_unavailable` (findings so far kept) when no PTR query got an answer, or after 10 failed PTR queries in a row in a sweep. The tests use a fake resolver; one opt-in `network` test asks the real resolvers about `1.1.1.1` (`one.one.one.one`, forward-confirmed).

### The crt.sh Shuttle

[`shuttles/crtsh`](../shuttles/crtsh/) (`crtsh`, passive, infrastructure, `network=True`) maps a domain from public **Certificate Transparency** logs through [crt.sh](https://crt.sh/), using only `ctx.http` (so the SSRF guard and the 5 MB response cap apply). For the input it sends two searches, `?q=<domain>&output=json` and `?q=%25.<domain>&output=json` (the domain itself and every name under it), validates each JSON row with a Pydantic model (malformed rows are ignored and counted), removes duplicate certificate IDs and orders them by `not_before`, newest first.

| Found | Becomes |
| --- | --- |
| a name in a certificate (SANs, one per line, and the common name) that is the input or under it | a Domain and `HAS_SUBDOMAIN` from the input (one edge from the input, not a parent chain), deduplicated; at most 2,000, most recently certified first (a job note says when more were found) |
| `*.<name>` | `<name>`, with `wildcard: true (certificates name *.<name>)` in the relationship's note (relationships carry no properties) |
| one of the 50 most recent certificates | downloaded (`?d=<id>`), parsed with `cryptography`: a Certificate keyed by the SHA-256 fingerprint of its DER encoding, with `issuer` (email attributes removed), `subject_cn`, `serial_number`, `not_before`, `not_after`, `crtsh_id`, `san_count`, `self_signed` (issuer equals subject and the signature verifies with its own key), and `COVERS` to each kept name it contains |
| an older certificate | its names only |

**Scope filtering.** Every name has a leading `*.` stripped and is lower-cased and normalized with `nightloom_core` (IDNs become A-labels); it is kept only if it is the input or ends in `.<input>`. Lookalikes such as `notexample.org` or `example.org.attacker.example`, and other customers' names on shared certificates, are dropped. **Email addresses** (in SANs, as a common name, in an issuer) are dropped before anything else is done with them: they are person data, and the Shuttle is infrastructure-only. **IP addresses** in SANs are dropped too. The drops are counted by reason (`email`, `ip_address`, `out_of_scope`, `invalid`) and reported in a job note with counts only. Public suffixes (`co.uk`) and internal-only names are refused before anything is sent (`not_registrable`).

**Fingerprint downloads.** crt.sh's JSON has no fingerprint, so a Certificate costs one download. At most 50 are downloaded per run, the most recent first, and at most 900 `COVERS` relationships are added in all, which keeps a run (2 × 2,000 + 50 + 900 findings at most) under the worker's 5,000-finding limit. A download is retried once; a certificate that cannot be downloaded or parsed is skipped (counted in a note), and three failures in a row stop the downloads. Cancellation is checked before every request.

**Large domains.** A search answer over 5 MB is asked again once with `&exclude=expired`, and the job gets the note *Expired certificates excluded (result too large).* If it is still too large, the job fails with `result_too_large`, whose message suggests running the Shuttle on a narrower input (one of the domain's subdomains).

**An unwell crt.sh.** crt.sh is free and busy, and often answers 502/503 or times out. A search that fails that way (or with 429, 500, 504 or a network error; read timeout 60 s) is retried up to 3 times after 2, 5 and 10 seconds of backoff; then the job fails with `source_unavailable`. A 4xx or a body that is not a JSON list fails at once with `source_invalid_response`. The rate limit is **one request every 3 seconds** per host (retries included). The timeout is 300 seconds; no search starts after 180 seconds and no download after 250, so a slow run ends with a clear error or a note (*Reached the time limit after N of 50 certificate downloads*) rather than the generic timeout.

crt.sh (run by Sectigo) publishes no terms of use; see the Shuttle's [README](../shuttles/crtsh/README.md#using-crtsh-politely) for how it is used politely. Names are rated **B2** (confidence 0.8: logged certificates are reliable, but a certified name need not resolve today); certificates, downloaded and fingerprinted locally, **B1** (0.95). The tests never call crt.sh: they use a fake crt.sh behind the SDK's guarded client and certificates generated with `cryptography`; one opt-in `network` test (`make test-network`) asks the real crt.sh about `example.org`.

### The RDAP Shuttle

[`shuttles/rdap`](../shuttles/rdap/) (`rdap`, passive, `network=True`, sensitivity **organization** because it adds Organizations; no person-related types) looks up public registration data with [RDAP](https://about.rdap.org/) (RFC 9082/9083), the JSON successor of WHOIS, for a Domain, IPAddress, Netblock or ASN.

| Input | Becomes |
| --- | --- |
| Domain | properties on the **registrable** Domain (`registered`, `expires`, `last_changed`, `status`, `dnssec_signed`, `registrar`, `registrar_iana_id`, `rdap_server`); the registrar as an Organization with `REGISTERED_BY` (note `role: registrar`); the registrant as an Organization with `REGISTERED_BY` (`role: registrant`) only when its vCard says `kind: org` and its name is not redacted; name servers as Domains with `HAS_NS` |
| IPAddress, Netblock | the registered network as Netblocks (`handle`, `name`, `type`, `country`, `registry`, `registered`, `last_changed`, `parent_handle`) with `WITHIN` from the input address, or from the input netblock when it lies inside the registered network (the input gets the properties when it *is* the registered network); its owner (the contact with role `registrant` and `kind: org`) as an Organization with `OWNS` to each Netblock |
| ASN | `handle`, `name`, `country`, `registry`, `registered`, `last_changed` on the ASN; its owner as an Organization with `OWNS` |

**Bootstrap.** The server responsible comes from IANA's RDAP bootstrap registries (RFC 9224: `dns.json`, `ipv4.json`, `ipv6.json`, `asn.json` at `https://data.iana.org/rdap/`), fetched through `ctx.http` and cached in the worker process for 24 hours, per file, with a lock per file and event loop so concurrent jobs fetch once (the worker runs each job in its own event loop; the cache is shared by all of them). A failed refresh falls back to the stale copy. Lookups take the most specific entry (longest domain suffix, longest IP prefix, smallest AS number range) and prefer an `https` URL. An input no server is listed for fails with `not_covered`.

**Registrable-domain lookup.** Registries hold registrations, not subdomains, so a Domain is looked up as its registrable domain (`nightloom_core.normalize.registrable_domain`, the bundled Public Suffix List), and a job note says when the input was a subdomain; the properties and relationships attach to the registrable domain. Public suffixes and internal-only names are refused (`not_registrable`) before anything is sent. When the registry's answer links (`rel: related`, RDAP media type) to the registrar's RDAP server, as thin registries such as .com and .net do, the Shuttle follows **one** such link and merges the answers: the registry wins on every field both have, and what only the registrar says is a second observation of the same Domain (or Organization, or name servers) rated lower. A registrar server that fails only costs a note.

**Networks.** A network's extent comes from the `cidr0` extension when present, else from `startAddress`–`endAddress` converted to the minimal CIDR list (`ipaddress.summarize_address_range`), at most 16 blocks, with a note beyond that.

**Privacy rules.** RDAP answers name people. One function, `nightloom_shuttle_rdap.privacy.extract_contacts`, is the only code that reads contacts (`entities` and their jCards), and it keeps only an organization's display name (`fn`) for the roles `registrar` (unless `kind: individual`) and `registrant` (only `kind: org`), plus the registrar's IANA ID. It never keeps names of individuals, email addresses (abuse and role mailboxes included), phone numbers, postal addresses or any other vCard field, skips names containing a redaction marker (one case-insensitive list, `REDACTION_MARKERS`: `REDACTED`, `Data Protected`, `Privacy`, `Not Disclosed`, `GDPR`, `Withheld`, …) or looking like an email address, URL or number, and ignores nested contacts and every other role. Skipped contacts are counted by reason (`not_an_organization`, `redacted`, `no_name`, `unused_role`) in a job note, never quoted. The tests scan every finding and note for the contact data in their fixtures.

**HTTP.** `Accept: application/rdap+json`; rate limit one request per second per server; a 404 (no such object) ends the job successfully with a note; a 429 waits for `Retry-After` (5 seconds without one) at most twice and never longer than 30 seconds; 5xx and network errors are retried twice after 2 and 5 seconds, then `source_unavailable`; any other status or an answer that is not an RDAP object of the expected class is `source_invalid_response`. Timeout 120 seconds; no request starts after 100.

**Admiralty.** The registry's answer is the authoritative record, but it may be redacted or out of date: **A2** (completely reliable, probably true; confidence 0.9). What only the registrar's answer adds is **B2** (0.8): registrars are usually reliable, but their copy is not the registry's record. The RIRs are the registries for addresses and AS numbers, so their answers are A2 too.

### The RIPEstat Shuttle

[`shuttles/ripestat`](../shuttles/ripestat/) (`ripestat`, passive, `network=True`, sensitivity **organization** because it adds Organizations; no person-related types) adds **routing** data from the RIPE NCC's [RIPEstat Data API](https://stat.ripe.net/docs/data-api/ripestat-data-api): what the [RIS](https://www.ripe.net/analyse/internet-measurements/routing-information-service-ris/) route collectors, which peer with hundreds of networks, see announced in BGP. Where [RDAP](#the-rdap-shuttle) says who *registered* an address range or AS number, RIPEstat says which prefix is *announced* and which AS *originates* it.

| Input | Data calls | Becomes |
| --- | --- | --- |
| IPAddress | `network-info`, then `as-overview` for each origin AS (at most 5, with a note beyond) | the most specific announced prefix covering the address as a Netblock (`announced: true`) with `WITHIN` from the address; each origin AS with `ANNOUNCED_BY` from the prefix and `as_name`, `announced`, `iana_block`, `iana_block_description`; each AS's holder as an Organization with `OWNS` (Organization → ASN). An address nobody announces is a note (*not announced in the global routing table*) and no findings |
| Netblock | `prefix-overview` | `announced`, `iana_block`, `iana_block_description` on the input; each origin AS (`as_name`) with `ANNOUNCED_BY` and its holder as an Organization with `OWNS`. When the input is not announced itself but a less specific prefix covering it is (`is_less_specific`), that prefix becomes a Netblock with `WITHIN` from the input (the allow-list has Netblock → Netblock), and the `ANNOUNCED_BY` edges start there |
| ASN | `as-overview`, then `announced-prefixes` | `as_name`, `announced`, `iana_block`, `iana_block_description` on the ASN, and its holder as an Organization with `OWNS`; every prefix it originated in RIPEstat's default period (the last two weeks) as a Netblock with `ANNOUNCED_BY`, `bgp_first_seen` (earliest start) and `bgp_last_seen` (latest end) of its timelines in that period |

**Requests and validation.** Every request is `https://stat.ripe.net/data/<endpoint>/data.json?resource=<r>&sourceapp=nightloom`, through `ctx.http`. Each answer's envelope is validated with a Pydantic model: anything but `status: "ok"` with a `data` object, or a `data` object of the wrong shape, fails the job with `source_invalid_response`. List items (origin ASes, announced prefixes) are validated one by one; a malformed item, a prefix that does not contain the input address, or a "covering" prefix that does not cover the input is skipped and counted in a job note. A prefix announced by several ASes at once (MOAS) gets an edge to each and a note.

**Holders and organizations.** RIPEstat names an AS's holder as `"<network name> - <organization>"` (`CLOUDFLARENET - Cloudflare, Inc.`). The Shuttle splits it on the **first** `" - "`: the left part is the ASN's `as_name`, the right part the Organization's value. Without `" - "` the whole text is the organization. Each part is trimmed with inner whitespace collapsed and dropped when empty, without a letter or digit, over 200 characters, or redacted-looking (`nightloom_sdk.redaction.is_redacted`, the same `REDACTION_MARKERS` list the RDAP Shuttle's privacy rules use); dropped names are counted in a note, never quoted. **Merging with RDAP.** An Organization's key is its NFKC, whitespace-collapsed, case-folded name ([Canonical keys](#canonical-keys)), so RIPEstat's holder and RDAP's registrant become one node when the two sources spell the organization the same way. They often do for an AS (both come from the RIR's record of the AS holder), but not always: RDAP for an *address* names the owner of the registered network, which can be a different organization (or a project name) from the holder of the AS that announces it, and spelling differs between sources (`Inc.` and `Inc`). No fuzzy matching is done; differing names stay separate nodes, both linked to the same ASN by `OWNS`.

**Caps.** At most 5 holder lookups per address; at most 500 prefixes per AS (`500 × 2 + 3` findings, far under the worker's 5,000), in a stable order: shortest prefix first, then IPv4 before IPv6, then by address, with a note giving the total.

**HTTP.** Rate limit two requests per second to `stat.ripe.net` (RIPEstat's [rules of usage](https://stat.ripe.net/docs/data-api/ripestat-data-api) set no request limit but allow 8 concurrent requests per IP address and ask for a `sourceapp`; the [service terms](https://www.ripe.net/about-us/legal/ripestat-service-terms-and-conditions/) apply). A 429 waits for `Retry-After` (5 seconds without one) at most twice and never longer than 30 seconds; 5xx and network errors are retried twice after 2 and 5 seconds, then `source_unavailable`; any other status (RIPEstat answers `400`, `status: "error"`, for a resource it does not understand) is `source_invalid_response`. A failed holder lookup for one of an address's origin ASes only costs a note. Timeout 120 seconds; no request starts after 100; `announced-prefixes` for a large network takes RIPEstat over 20 seconds, so it may read for 60.

**Admiralty.** **B2** (confidence 0.8): RIS is a usually reliable observer of the global routing table and what it saw is probably true, but routing changes all the time, a collector's view is not the whole Internet, and RIPEstat filters routes seen by few peers. The tests use a fake RIPEstat behind the SDK's guarded client with fictional data only; one opt-in `network` test asks the real RIPEstat about `1.1.1.1` and `AS13335`.

### The InternetDB Shuttle

[`shuttles/internetdb`](../shuttles/internetdb/) (`internetdb`, passive, infrastructure, `network=True`) reads [Shodan's InternetDB](https://internetdb.shodan.io/), a free summary of Shodan's own Internet-wide scans updated about weekly: per IPv4 address, the open ports, the software identified (CPE names), the vulnerabilities Shodan associates with that software (CVE IDs), tags (`cdn`, `self-signed`, …) and hostnames. No API key is needed.

| Found | Becomes |
| --- | --- |
| each open port | a Service `<ip>:<port>/tcp` with `RUNS_ON` to the address; the lowest 200 (a note says when there were more) |
| each hostname | a Domain with `RESOLVES_TO` to the address, normalized with `nightloom_core`, alphabetical, at most 100; invalid and internal-looking names (no registrable domain, `.arpa`, `.onion`) are dropped and counted in a note |
| the record | properties on the input IPAddress: `open_ports`, `cpes` (at most 100), `vulns` (only IDs matching `^CVE-\d{4}-\d{4,}$`, sorted by year and number, at most 500; others are counted, never quoted), `tags` (at most 50) and `internetdb_checked_at`. Empty lists are written too, so a new run replaces an older one's lists |

**Why passive.** The Shuttle reads data Shodan has already collected: its only request is `GET https://internetdb.shodan.io/<ip>`, through `ctx.http`. Nothing is ever sent to the address being looked up, so the target cannot tell it was investigated; the tests check the destination of every request. IPv6 addresses (InternetDB covers IPv4 only) and addresses that are not publicly routable (`is_public_address`) are skipped with a job note before any request, as InternetDB cannot know them.

**Why CPEs and CVEs are properties, not entities.** They are Shodan's inference about one address at one time: a CVE is listed because of a software version Shodan saw in a banner, not because anyone checked it, and the same CVE on two addresses says nothing about a link between them. As nodes they would add hundreds of entities per address and suggest connections through shared software that do not exist. As properties they stay with the address they describe, are replaced by the next run, and remain searchable. A dedicated Vulnerability type can come later, once something can confirm one.

**Rating.** Ports, CPEs, CVEs and tags are **B2** (confidence 0.8): Shodan's own scans are a usually reliable source and probably true, but up to a week old. Hostnames are **B3** (0.6): Shodan saw the name for this address at some point (in a certificate, a banner or reverse DNS), but the name may point elsewhere now, so the Domain and the `RESOLVES_TO` edge carry the lower rating, and the edge has the note *hostname reported by Shodan; may not resolve to this address now*. Running the [DNS Shuttle](#the-dns-shuttle) on the name confirms or refutes it; its B2 observation then raises the edge's `max_confidence`. Relationship findings take an `admiralty` of their own for this.

**HTTP.** One request per run; rate limit one request per second. A 404 (`No information available`) ends the job successfully with a note; a 429 waits for `Retry-After` (5 seconds without one) at most twice and never longer than 30 seconds; 5xx and network errors are retried twice after 2 and 5 seconds, then `source_unavailable`; any other status, a record for a different address or one whose fields are not lists is `source_invalid_response`. Malformed list items are skipped and counted. Timeout 60 seconds; no request starts after 45.

**Usage conditions.** InternetDB is free for non-commercial use; building a paid product on its data needs a Shodan license. The Shuttle states this in its metadata's `usage_note`, which `GET /shuttles` returns and the *Run Shuttle* panel shows under the Shuttle's description with an info icon, next to the link to [Shodan's terms](https://account.shodan.io/legal/tos). The mechanism is generic: any Shuttle whose source sets conditions (non-commercial only, attribution, a quota) declares them the same way, so the operator sees them before every run instead of in a README. Nightloom cannot check how its output is used; the note informs, it does not enforce. The tests use a fake InternetDB with fictional records for documentation addresses only; one opt-in `network` test asks the real InternetDB about `1.1.1.1`.

### The Wayback Shuttle

[`shuttles/wayback`](../shuttles/wayback/) (`wayback`, passive, infrastructure, `network=True`) lists what the Internet Archive's [Wayback Machine](https://web.archive.org) has captured under a domain, from its [CDX server](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md) (no API key). Old pages, forgotten subdomains and retired endpoints often survive only there.

| Found | Becomes |
| --- | --- |
| an archived page under the domain | a URL with `HOSTS` from its own host (the input or the subdomain), with `first_captured`, `capture_count`, `last_status`, `mimetype`, `archive_url`, `query_params`, and `last_captured` for the home page; at most 2,000, most recently captured first |
| a subdomain hosting one of those URLs | a Domain with `HAS_SUBDOMAIN` from the input (at most 450, so a run stays under the worker's 5,000 findings) |
| the archive's summary | `wayback_first_capture`, `wayback_last_capture` and `wayback_url_count` on the input |

**Queries.** Three requests to `https://web.archive.org/cdx/search/cdx` through `ctx.http`, each with `output=json` and an explicit `fl=timestamp,original,statuscode,mimetype`: the home page's first capture (`url=<domain>/&limit=1`), its latest (`url=<domain>/&fastLatest=true&limit=-1`), and the domain-wide listing (`url=<domain>&matchType=domain&collapse=urlkey&limit=10000`; `matchType=domain` includes subdomains). The CDX index is sorted by address (SURT) and then by time. With a domain match, `limit=1` and `limit=-1` therefore return the ends of the *address* order, not the first and last capture in time, and took the real server up to a minute each. Exact-match queries on the home page answer in about a second. For the same reason `collapse=urlkey` keeps each address's **first** capture: the listing cannot tell a page's latest capture, so `last_captured` is set only where the archive was asked for it (the home page). `capture_count` is the number of archived addresses merged into the URL, not the number of snapshots. The domain's first and last capture are the earliest and latest timestamps among all three answers (static assets included).

**Filtering** (one module, [`filtering.py`](../shuttles/wayback/src/nightloom_shuttle_wayback/filtering.py), tested on its own), in this order: (1) **credentials**: a URL with user information before its host (`user@`, `user:pass@`, also percent-encoded) is dropped before anything else; (2) **scope**: the URL's host, normalized with `nightloom_core`, must be the input or under it, as for [crt.sh](#the-crtsh-shuttle), so lookalikes (`evilexample.org`) and IP-address hosts are dropped; (3) **hidden queries**: the path is percent-decoded up to twice (stopping when nothing changes), and if that reveals a `?` or `#` (`/reset%3Ftoken=…`, `%3f`, `%23`, `%253F`), the stored path is cut before the encoded sequence in the *original* path and the decoded part is handled as a query string or fragment (only parameter names kept); a path still encoded after two rounds (`%25253F`) is dropped as invalid; (4) **static assets**: images, stylesheets, scripts, fonts, audio and video, and archives, by extension or by the recorded media type (one constant, `STATIC_ASSETS`); (5) **privacy**: the query string, the fragment and path parameters (`;jsessionid=…`) are removed from every stored URL, keeping only the sorted, distinct names of `name=value` query parameters as `query_params`; a URL whose fully decoded path contains `@` (also `%40` or `%2540`, hidden query included) is dropped entirely, and so is one whose decoded path looks like it holds a secret (below); (6) **normalization** as a URL (invalid URLs are dropped). Addresses that become the same URL are merged, keeping the earliest capture. Drops are counted by reason (*credentials in URL*, *outside the domain*, *static assets*, *email in path*, *possible secret in path*, *invalid*) in a job note, never quoted; possible secrets also get their own count-only note (*N archived URL(s) looked like they contained secrets and were not stored*).

**Secrets in paths.** Credentials end up in paths too (`/api/v1/keys/sk_live_…`), and stripping the query does not help there, so such a URL is dropped. One constant, `SECRET_PATTERNS`, lists compiled, anchored patterns tested one by one against each decoded path segment (also without its file extension, and without path parameters) or the decoded path: AWS access key IDs (`AKIA`/`ASIA` + 16), Google API keys (`AIza` + 35), GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`), Slack tokens (`xox[abposr]-`), Stripe live keys (`sk_live_`, `rk_live_`, `pk_live_`), Twilio API keys (`SK` + 32 hex), JSON Web Tokens (`eyJ….….…`), private-key markers (`-----BEGIN`), and a segment named like a secret (`token`, `apikey`, `api_key`, `secret`, `password`, `access_token`, `auth`, …, any case) followed by a segment of at least 16 characters of `[A-Za-z0-9_-]`. After the patterns, an **entropy rule**: a segment of at least 32 characters of `[A-Za-z0-9+/=_-]`, with both letters and digits and a Shannon entropy of at least 4.0 bits per character. Trade-offs, all toward keeping ordinary pages: segment patterns match whole segments, so a token in the middle of one (`/download-ghp_…`) is missed; lower-case hex of MD5, SHA-1 or SHA-256 length is kept as a content hash (hex cannot exceed 4.0 bits anyway); "word slugs" (`how-we-built-our-new-pipeline-2020`, `oauth2-callback-handler`: words, each with at most four digits after it, or numbers, joined by `-` or `_`) are kept by both the entropy rule and the named-segment rule, so a token made of such parts (`FAKE-0000-FAKE-0000`) is missed; a segment without digits is never taken for random, so long camel-case names (`ProductCategoryListingPage`) stay, while a camel-case name with digits in the middle and high entropy may be dropped. The check says only *whether*: nothing about the match is logged, noted or stored.

**Why strip queries.** An archived query string is whatever a visitor's browser sent when the archive captured it: session tokens, password-reset and unsubscribe keys, API keys, search terms, email addresses. Storing it would copy credentials and personal data into a case, against [ETHICS.md](../ETHICS.md). Parameter *names* (`token`, `redirect`, `id`) are what an analyst needs to see an endpoint's interface, so only those are kept, and only when they look like identifiers (a bare `?3f9a…` may itself be a value). `archive_url` is built from the stripped URL, so no value reaches the graph even through the link; the archive redirects it to the nearest copy it has. A path can hold an email address too (`/users/alice@example.net`), and there is no safe way to keep part of it, so the whole URL goes. Percent-encoding must not smuggle a query past this (`/reset%3Ftoken=…` is one), hence the decoding step. The tests scan every serialized finding, note and log record for the fixtures' fake tokens and addresses.

**HTTP.** Rate limit one request per second to `web.archive.org`. The CDX server is slow and flaky: each request may read for 60 seconds; 5xx, timeouts and network errors are retried twice after 5 and 15 seconds, then `source_unavailable`; a 429 waits for `Retry-After` (10 seconds without one) at most twice and never longer than 60 seconds; any other status or an answer that is not a JSON list is `source_invalid_response`. A listing over 5 MB is asked again for 2,500 addresses (with a note), then fails with `result_too_large`; a listing that fills its limit gets a note saying only the first addresses in the archive's order were read. Malformed rows are counted. Timeout 300 seconds; no request starts after 230.

**Admiralty.** **B2** (confidence 0.8): the archive faithfully records what it captured and is a usually reliable source, but a page may differ today or be gone, so it is probably true rather than confirmed. The Shuttle's `usage_note` asks for modest use of the Internet Archive, a non-profit service; its [terms of use](https://archive.org/about/terms) apply. The tests use a fake CDX server with fictional captures of `example.org`; one opt-in `network` test asks the real archive about `example.org`.

### Jobs and the worker

A **job** is one Shuttle run on one entity of a case (PostgreSQL table `jobs`, deleted with its case): `shuttle_name`, `shuttle_version`, `input_key`, `requested_by`, `status`, `created_at`/`started_at`/`finished_at`, `error` (one of a few fixed, user-safe messages, never an exception's text), `stats` (`entities_created`, `entities_updated`, `relationships_created`, `relationships_updated`, `findings_rejected`, `rejected_reasons` by reason, `entities_touched`: the keys of up to 200 entities the job created or updated, in order, so the UI can highlight them; and `notes`: the Shuttle's [notes](#context), kept also when the job fails; the audit log gets the counts but never the keys or the notes) and `cancel_requested`.

```mermaid
stateDiagram-v2
    [*] --> queued: POST /jobs (checks pass, row + job.create commit, then enqueue)
    queued --> cancelled: POST /cancel (job.cancel)
    queued --> running: worker picks it up (not cancelled)
    queued --> failed: guardrail fails at start (job.finish denied), enqueue fails, or lost (6 h)
    running --> succeeded: Shuttle ends (job.finish)
    running --> cancelled: cancel_requested noticed between findings
    running --> failed: timeout, over 5,000 findings, Shuttle error, failure or refusal, guardrail fails mid-run, worker time limit, worker lost (reaper)
    succeeded --> [*]
    cancelled --> [*]
    failed --> [*]
```

**Queueing.** `POST /cases/{id}/jobs` checks, with the case row locked: the caller may run Shuttles (editor or owner), the case is open, the key is canonical, the Shuttle is installed and accepts the entity's type, it is passive, a person-sensitive Shuttle (or person-related input) needs a case that allows person entities, the entity exists in the case's graph, and the caller has fewer than 5 queued or running jobs (`NIGHTLOOM_MAX_ACTIVE_JOBS_PER_USER`; counted under a per-user advisory lock). Only after the job row and its `job.create` event have committed is the task sent, so the worker never receives a job that does not exist. If sending fails, the job is marked `failed` and the client gets `503`.

**Celery.** One task, `nightloom.run_job(job_id)`, on the `shuttles` queue in Valkey. The message carries only the job's ID. The broker accepts **JSON only** (never pickle); tasks are acknowledged late (`acks_late`, re-delivered if a worker dies) and each worker process takes one at a time (`prefetch_multiplier=1`). Soft and hard time limits are the Shuttle's timeout plus 60 s and 90 s; the runner enforces the job's deadline first (see [Deadlines](#deadlines-every-job-ends)). Each task runs in a fresh event loop with its own PostgreSQL and Neo4j clients. The worker reuses the API's code (`nightloom_api.jobs.service`, the graph repository, the audit log), so the rules exist once.

**Guardrails re-checked at execution time** ([`runner.py`](../apps/worker/src/nightloom_worker/runner.py)). Anything can change while a job waits, so the worker checks again, with the case share-locked: the Shuttle is still installed under the same name (a newer version runs and is recorded in the job), the case is still open, the requester is still an active member allowed to run Shuttles (a deactivated user, a removed member or one demoted to viewer is not), the Shuttle is passive and accepts the input's type, the person gate still allows it, and the input entity still exists. A failed check ends the job as `failed` with a `denied` `job.finish` event.

**Each finding** is checked before it is written: its type must be among the declared outputs (relationship ends among the input and output types), its value must normalize, its properties must validate, relationships must pass the allow-list (and `RELATED_TO` needs a note), both ends must already exist in the case, and person-related entities need a case that allows them right now. A finding that fails is **rejected**: counted by reason, logged with the reason only, never stored. Accepted findings are upserted with the source `shuttle:<name>@<version>`, the Shuttle's Admiralty rating and confidence (or the finding's own) and the observation time. Every write share-locks the case (`FOR SHARE`, which conflicts with the lock every case change takes, while several workers can write at once) and checks again that the case is open and the requester may still run Shuttles; if not, the job stops there (`denied`).

**Limits.** More than 5,000 findings, or running past its deadline, stops the job as `failed`; findings stored until then are kept. Cancellation (`cancel_requested`) is polled every 2 seconds and takes effect between findings, also while the Shuttle is waiting on the network. A job found `running` when a task arrives was abandoned by a worker that died and is failed.

#### Deadlines: every job ends

Every running job has a **deadline**: `started_at` + its Shuttle's `timeout_seconds` (crt.sh: 300 s). Four layers, each catching what the one before missed, make sure the job reaches a final status ([`deadlines.py`](../apps/api/src/nightloom_api/jobs/deadlines.py)):

| Layer | When (crt.sh) | Where | What happens |
| --- | --- | --- | --- |
| 1. Timeout | deadline (300 s), outcome recorded by +30 s | the run, in the pool process | The guardrail checks and the Shuttle with its graph writes are cut at the deadline (`failed`, `timeout`). Clean-up steps (a Shuttle that ignores cancellation, the cancellation watcher, closing the Shuttle's HTTP client) get 5 s each and are then left behind; recording the outcome gets until the deadline plus 30 s. |
| 2. Soft limit | deadline + 60 s (360 s) | the pool process | Celery's `SIGUSR1` sets a flag and cancels the run through the event loop's thread-safe wake-up (billiard's own handler would raise an exception wherever the loop happens to be, and asyncio swallows it inside a loop callback). The job is recorded as `failed` (`worker_time_limit`). |
| 3. Hard limit | soft + 30 s (390 s) | the worker's main process | Celery kills the pool process and sends `task_failure` (`TimeLimitExceeded`) in the main process, which records the job as `failed` (`worker_time_limit`) in a thread bounded to 20 s, with `statement_timeout` and `lock_timeout` of 5 s. A pool process lost otherwise (`WorkerLostError`) has its task re-delivered, and that run fails the job (`worker_lost`). |
| 4. Reaper | hard limit + 60 s (450 s) | every worker's main process | On `worker_ready` and every 60 s after, under a Valkey lock (`nightloom:worker:reaper`, 50 s expiry) so one worker reaps at a time: running jobs past their own stale time (the Shuttle's hard limit plus 60 s; the 600 s maximum for a Shuttle no longer installed) and jobs queued for over 6 hours are failed (`worker_lost`, `not_picked_up`). It does not wait for another job to arrive. Each running job is also noted in Valkey under the worker's node name and run (`nightloom:worker:running:<job id>`), so a worker that starts again under the same name (`docker compose kill` / `start`, a crash and restart) fails the jobs its previous run left at once (`worker_lost`), without waiting for their stale time. |

Finishing is always a conditional update (`UPDATE … WHERE status IN (…)`), so a run that finishes while the reaper or the time-limit handling fails the job never ends up finished twice: exactly one of them records `job.finish`.

Each job runs in a fresh event loop that always closes ([`loop.py`](../apps/worker/src/nightloom_worker/loop.py)): `asyncio.run()` would wait for leftover tasks to finish cancelling and for the default thread pool, where DNS lookups (`getaddrinfo`) run, so one stuck lookup could keep the task alive for minutes after the job ended. Instead leftover tasks get 5 s, async generators 5 s, and stuck threads are left behind. The SDK's resolver also gives each lookup 10 s.

**Diagnosing a job.** The worker logs one line when a job starts (`Job <id> started: shuttle=… version=… timeout=…s`) and one when it ends (`Job <id> finished: shuttle=… status=… reason=… duration=…s`), a warning naming the phase when the deadline cuts a run short, and warnings when a clean-up step or a Shuttle is left behind. The crt.sh Shuttle also logs each phase's duration (search, writing the subdomains, certificate downloads) and a warning when a phase's own time budget cut it short. Logs never contain response bodies, found values or secrets. Compose keeps the API's and the worker's logs as rotated json-file logs (5 × 10 MB); they are lost when the container is recreated.

In the web UI's *Jobs* panel, a running job's elapsed time stops at its deadline, and a job still `running` after its hard limit (`overdue_at` in the API) is marked *overdue — the worker may have stopped* (the reaper fails it about a minute later).

**No secrets or raw data in the database.** `error` is always one of the fixed messages; logs record exception *types*, never their text; audit events carry counts and reason codes, never responses or values.

| Endpoint (under `/api/v1`) | Who | Notes |
| --- | --- | --- |
| `GET /shuttles` | signed in | installed Shuttles' metadata, by name; `?input_type=domain` keeps those accepting it |
| `POST /cases/{id}/jobs` | editor, owner; case `open` | `{shuttle, entity_key, fresh?}` → `201` with the queued job (`fresh: true` skips the [result cache](#the-result-cache)); `403` active or person-gated Shuttle (recorded as `authz.denied`), `409` case not open, `422` unknown Shuttle, type not accepted or no such entity, `429` too many jobs, `503` Neo4j or the queue unavailable |
| `GET /cases/{id}/jobs` | member | newest first, `{items, next_cursor}`; `limit` 1–200, `status` filter |
| `GET /cases/{id}/jobs/{job_id}` | member | one job, with `stats.entities_touched`, the Shuttle's `timeout_seconds`, `deadline` and `overdue_at` (null until it starts), `use_cache`, `from_cache`, `cached_fetched_at` and `run_id`; `404` for a job of another case |
| `GET /admin/result-cache` | admin | the result cache's scope and limits, and per Shuttle its entries, size, hits and recent jobs from the cache |
| `DELETE /admin/result-cache` | admin | clears every entry, or one Shuttle's (`?shuttle=dns`); `{deleted}`; audited |
| `DELETE /cases/{id}/result-cache` | owner (also when the case is closed) | clears the case's entries; `{deleted}`; audited |
| `GET /scan-profiles` | signed in | built-in profiles, then custom ones: `shuttles`, `input_types` (types at least one installed Shuttle accepts), `missing_shuttles` |
| `POST /scan-profiles`, `PATCH`/`DELETE /scan-profiles/{id}` | admin | custom profiles; `403` for a built-in one, `409` name taken or 50 profiles, `422` unknown or active Shuttle |
| `POST /cases/{id}/scan-runs` | editor, owner; case `open` | `{profile_id, entity_keys (1–25), fresh?}` → `201` with the run (`total`, `released`, `waiting`, `skipped`, `jobs` by status); `422` unknown profile, missing entities, nothing to run or over 100 jobs; `429` two runs in progress |
| `GET /cases/{id}/scan-runs[/{run_id}]` | member | the newest runs (`limit` 1–50), or one |
| `POST /cases/{id}/scan-runs/{run_id}/cancel` | editor, owner (also when the case is closed) | `409` once ended |
| `POST /cases/{id}/jobs/{job_id}/cancel` | editor, owner (also when the case is closed) | a queued job becomes `cancelled` at once; a running one gets `cancel_requested` and stops between findings; `409` once finished |

#### The result cache

A passive Shuttle with a `cache_ttl_seconds` (DNS and reverse DNS 1 hour; crt.sh, RIPEstat and InternetDB 24 hours; RDAP and Wayback 7 days; registrable-domain never) does not ask its source again for the same entity while a recent result exists ([`result_cache.py`](../apps/api/src/nightloom_api/jobs/result_cache.py)).

- **What is stored.** After a live run **succeeds** (not failed, cancelled, cut by the deadline or over the findings limit), the worker stores what the Shuttle *yielded*, in order, and its notes (PostgreSQL table `result_cache`, migration `0009`), within the job's deadline and never at the cost of the job. These are the raw findings, before any check that depends on the case.
- **Key.** SHA-256 of the scope, the Shuttle's **name and version** (a new version never reuses an older one's results), the input entity's canonical key and the run's options (none yet; normalized, so their order never matters).
- **Scope** (`NIGHTLOOM_RESULT_CACHE_SCOPE`): `case` (default) keeps each case's entries apart and deletes them with the case; `instance` shares them across cases; `off` disables the cache. Per case is the default because a shared entry's fetch time would tell one case's members that someone else on the instance looked the same target up. Entries of another scope are never served.
- **Replay.** A job that may reuse results (`use_cache`, true unless the user asked for a fresh run) and finds a valid entry after the start checks writes the entry's findings through **exactly the same checks and writes as a live run**: declared types, normalization, the allow-list, endpoints, and the case's person gate *as it is now*. A case that does not allow person entities therefore rejects an entry's person findings even when another case's run stored them. Provenance records the time the source was asked (`fetched_at`), not the replay, so a replay never makes an entity look more recently seen. The job is marked `from_cache` with `cached_fetched_at`; its notes are the entry's.
- **Fresh runs.** `POST /cases/{id}/jobs` with `fresh: true` (*Run fresh* in the Jobs panel, or unticking *Use recent results*) runs the Shuttle and replaces the entry; the `job.create` event records `fresh`.
- **Failure is harmless.** A cache that cannot be read means a live run; one that cannot be written means a job that still succeeds. Both are logged by error type only.
- **Limits.** A run whose results are larger than `NIGHTLOOM_RESULT_CACHE_MAX_ENTRY_BYTES` (5 MiB) is not stored. Every 10 minutes the reaper's thread purges, under its own Valkey lock (`nightloom:worker:result-cache-purge`), expired entries and then the least recently used ones beyond `NIGHTLOOM_RESULT_CACHE_MAX_BYTES` (512 MiB).
- **Clearing.** Admins see the scope, the limits and per Shuttle the entries, size, reuse and how many of the last 30 days' successful jobs came from the cache (`GET /admin/result-cache`, the *Cache* page), and clear everything or one Shuttle's entries (`DELETE /admin/result-cache[?shuttle=]`). Case owners clear their case's entries (`DELETE /cases/{id}/result-cache`, *Settings → Cached Shuttle results*). Both are audited as `result_cache.clear` with the Shuttle name and a count, never entity keys.

#### Scan profiles and runs

A **scan profile** names Shuttles to run together ([`scans/`](../apps/api/src/nightloom_api/scans)). Four are built in (code, the same everywhere, not editable): *Quick domain recon* (DNS, RDAP, registrable domain), *Deep domain recon* (DNS, crt.sh, RDAP, Wayback, registrable domain), *IP / network context* (reverse DNS, RIPEstat, RDAP, InternetDB) and *Exposure check* (InternetDB, reverse DNS). Admins add their own (table `scan_profiles`, migration `0010`; at most 50, names unique ignoring case and distinct from the built-in ones, 1–15 Shuttles, passive only), which every user can run; changes are audited (`scan_profile.create` / `update` / `delete`, with the name and Shuttles). A profile names Shuttles, not versions: it runs whatever is installed, and an uninstalled one is left out and reported.

A **run** (table `scan_runs`, deleted with its case) runs a profile on 1–25 entities of an open case (editors and owners):

- **Planned** when created: every (Shuttle, entity) pair the guardrails allow, entity by entity in the profile's order; pairs the Shuttle does not accept, that the case's person gate refuses or whose Shuttle is not installed are skipped and counted. At most 100 jobs per run and two runs in progress per user (`429`, audited as a failed `scan_run.create`). `fresh: true` skips the [result cache](#the-result-cache) for every job.
- **Released in batches**: a planned job becomes a real job (`jobs.run_id`) only while the requester has fewer queued or running jobs than the per-user limit (`NIGHTLOOM_MAX_ACTIVE_JOBS_PER_USER`, 5), under the same per-user lock as queueing a single job, so the limit always holds. Releasing happens right after the run is created, in the worker after each job of a run finishes, and from the reaper's thread every minute as a safety net. Released jobs are ordinary jobs (`job.create` by `system:scan-run`, with the `run_id`), checked again by the worker when they start; older runs release first, and single jobs take slots too.
- **Ends** as `finished` (everything released and done), `cancelled` (`POST …/cancel`: nothing more is released, queued jobs are cancelled and running ones asked to stop; audited `scan_run.cancel`), or `stopped` (the case is no longer open, or the requester may no longer run Shuttles, or was deleted).

In the web UI, *Run a profile* sits above *Run a Shuttle* for the selected entity and in the side panel of a multi-selection; the *Jobs* panel shows each run as one row (profile, progress such as *7 of 12 done · 2 running · 3 waiting*, *Cancel run*) with its jobs inside on demand, and polls running runs so newly released jobs appear. Admins manage profiles on the *Profiles* page.

The Compose stack runs the worker as the `worker` service (same image as the API, 4 processes by default, health-checked with `celery inspect ping`, with `NIGHTLOOM_DNS_RESOLVERS` for the [DNS](#the-dns-shuttle) and [reverse DNS](#the-reverse-dns-shuttle) Shuttles); `make worker` runs one on your machine against the stack's services.

## Auto-pivot

Auto-pivot expands a seed entity breadth-first:

- **Max depth** — number of hops from the seed
- **Budget** — maximum requests and/or nodes per scan
- **Entity-type allowlist** — only pivot through selected types
- **Passive-only mode** — skip all active Shuttles
- Person-related Shuttles are always excluded

The scan stops when the queue is empty or any limit is reached; partial results are kept.

## AI layer

- **LiteLLM** provides one interface to any provider, including local models via Ollama.
- **Per-user API keys** — encrypted at rest with AES-GCM, bound to the owning `user_id` (as associated data), decrypted only server-side when needed, and **never sent to the browser**.
- **Per-feature model routing** — each user picks which model handles each feature (e.g. summaries vs. query generation).
- **Natural language → Cypher** — generated queries run as a **read-only** Neo4j user, so the AI can never modify the graph.

## Guardrails

Collection rules, gated Shuttles, audit logging, PII redaction and data retention are defined in [ETHICS.md](../ETHICS.md). They are design requirements, not optional features.
