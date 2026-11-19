/**
 * Renders the real route table in a memory router under the real
 * ThemeProvider and AuthProvider, so tests exercise routing, the auth gate and the API client
 * together against a {@link FakeApi}.
 */
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type InitialEntry } from "react-router";

import { routes } from "../app/routes";
import { AuthProvider } from "../auth/AuthProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

export function renderApp(path: InitialEntry = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <ThemeProvider>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </ThemeProvider>,
  );
  return { ...view, router };
}
