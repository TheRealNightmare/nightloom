/**
 * Renders the real route table in a memory router under the real
 * AuthProvider, so tests exercise routing, the auth gate and the API client
 * together against a {@link FakeApi}.
 */
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type InitialEntry } from "react-router";

import { routes } from "../app/routes";
import { AuthProvider } from "../auth/AuthProvider";

export function renderApp(path: InitialEntry = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
  return { ...view, router };
}
