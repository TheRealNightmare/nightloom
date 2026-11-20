import { useEffect } from "react";

export const APP_NAME = "Nightloom";

/** "Cases · Nightloom", "Example case · Graph · Nightloom"; empty parts are left out. */
export function documentTitle(...parts: (string | null | undefined)[]): string {
  return [...parts.filter((part): part is string => Boolean(part?.trim())), APP_NAME].join(" · ");
}

/**
 * Sets the browser tab's title while the calling page is shown, so each
 * route has its own (and screen readers announce it after navigation).
 */
export function useDocumentTitle(...parts: (string | null | undefined)[]): void {
  const title = documentTitle(...parts);
  useEffect(() => {
    document.title = title;
  }, [title]);
}
