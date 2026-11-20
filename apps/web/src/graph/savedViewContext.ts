import { createContext, useContext } from "react";

import type { ViewSnapshot } from "./viewSnapshot";

/** What a saved-views control gets from the graph view (see GraphView's `savedViews`). */
export interface SavedViewControls {
  /** Whether the graph is drawn (capturing needs the camera). */
  ready: boolean;
  /** How the graph looks now. */
  capture: () => ViewSnapshot;
  /**
   * Shows `snapshot`: its filters, expanded groups and camera, and its pins
   * in place of the user's (saved as the user's layout).
   */
  apply: (snapshot: ViewSnapshot) => void;
  /** Changes each time the user asks for the views (the V key). */
  openRequest: number;
}

export const SavedViewContext = createContext<SavedViewControls | null>(null);

/** The controls of the enclosing graph view, for its `savedViews` element. */
export function useSavedViewControls(): SavedViewControls {
  const value = useContext(SavedViewContext);
  if (value === null) throw new Error("useSavedViewControls() must be used inside a GraphView");
  return value;
}
