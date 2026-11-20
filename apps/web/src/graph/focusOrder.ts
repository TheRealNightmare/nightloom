/**
 * Keyboard focus among the graph's nodes: Tab and Shift+Tab move through
 * the visible nodes by their position on the screen, reading order (rows
 * from the top, each row left to right), and what is announced for the
 * focused node.
 */
import { ENTITY_TYPE_INFO, type EntityType } from "../domain/entities";
import type { Point } from "./geometry";

/** Nodes whose centers are within this many pixels vertically share a row. */
export const ROW_HEIGHT = 32;

export interface Placed {
  id: string;
  point: Point;
}

/** `nodes` in reading order: by row (ROW_HEIGHT bands), then left to right, then by id. */
export function focusOrder(nodes: readonly Placed[], rowHeight = ROW_HEIGHT): string[] {
  return [...nodes]
    .sort(
      (a, b) =>
        Math.floor(a.point.y / rowHeight) - Math.floor(b.point.y / rowHeight) ||
        a.point.x - b.point.x ||
        a.id.localeCompare(b.id),
    )
    .map((node) => node.id);
}

/**
 * The node focus moves to from `current` (null: none yet), or null when it
 * leaves the graph: Tab past the last node (and Shift+Tab before the first)
 * returns null, so the key's default action takes focus on to the page.
 */
export function nextFocus(
  order: readonly string[],
  current: string | null,
  backwards: boolean,
): string | null {
  if (order.length === 0) return null;
  const index = current === null ? -1 : order.indexOf(current);
  if (index === -1) return backwards ? (order.at(-1) ?? null) : (order[0] ?? null);
  const next = index + (backwards ? -1 : 1);
  return next < 0 || next >= order.length ? null : (order[next] ?? null);
}

export interface Announced {
  type: EntityType;
  label: string;
  neighbours: number;
  pinned: boolean;
  /** Set for a collapsed group node. */
  members?: number;
}

/** "Domain www.example.org, 3 neighbours, pinned". */
export function announceNode(node: Announced): string {
  const info = ENTITY_TYPE_INFO[node.type];
  const parts = [
    node.members === undefined
      ? `${info.label} ${node.label}`
      : `Group of ${node.members.toLocaleString("en")} ${node.members === 1 ? info.label : info.plural}`,
    `${node.neighbours.toLocaleString("en")} ${node.neighbours === 1 ? "neighbour" : "neighbours"}`,
  ];
  if (node.pinned) parts.push("pinned");
  return parts.join(", ");
}
