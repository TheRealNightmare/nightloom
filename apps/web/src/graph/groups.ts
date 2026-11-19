/**
 * Collapsing large groups of leaves: when an entity has more than
 * {@link GROUP_THRESHOLD} neighbours of one type that are leaves (connected to
 * nothing else), they are drawn as a single group node ("312 URLs") linked to
 * that entity. Groups are computed from what the filters show, so a leaf
 * hidden by a filter does not count, and an entity whose other links are
 * hidden can become a leaf. Pure functions; GraphView syncs the result.
 */
import {
  ENTITY_TYPE_INFO,
  type EntityNode,
  type EntityType,
  type InvestigationGraph,
  type RelationEdge,
} from "../domain/entities";
import { adjacency } from "./filters";

/** More leaves than this, of one type around one entity, become a group. */
export const GROUP_THRESHOLD = 25;
export const GROUP_ID_PREFIX = "group:";
/** Relationship shown on a group's edge when its members are linked in different ways. */
export const MIXED_RELATION = "MIXED";

export interface LeafGroup {
  /** Stable while the parent and type stay the same: `group:<parent>|<type>`. */
  id: string;
  parent: string;
  type: EntityType;
  /** Member ids, in data order. */
  members: string[];
}

export function groupId(parent: string, type: EntityType): string {
  return `${GROUP_ID_PREFIX}${parent}|${type}`;
}

export function isGroupId(id: string): boolean {
  return id.startsWith(GROUP_ID_PREFIX);
}

/** "312 URLs", "1 Domain". */
export function groupLabel(type: EntityType, count: number): string {
  const info = ENTITY_TYPE_INFO[type];
  return `${count.toLocaleString("en")} ${count === 1 ? info.label : info.plural}`;
}

/** Groups among the `visible` entities, largest first (then by id). */
export function computeGroups(
  data: InvestigationGraph,
  visible: ReadonlySet<string>,
  threshold = GROUP_THRESHOLD,
): LeafGroup[] {
  const neighbours = adjacency(data, visible);
  const byId = new Map(data.nodes.map((node) => [node.id, node]));
  const candidates = new Map<string, LeafGroup>();
  for (const node of data.nodes) {
    const around = neighbours.get(node.id);
    if (around?.size !== 1) continue;
    const [parent] = around;
    if (parent === undefined) continue;
    // Two leaves linked only to each other are a pair, not a group.
    if ((neighbours.get(parent)?.size ?? 0) <= 1) continue;
    const id = groupId(parent, node.type);
    const group = candidates.get(id) ?? { id, parent, type: node.type, members: [] };
    group.members.push(node.id);
    candidates.set(id, group);
  }
  return [...candidates.values()]
    .filter((group) => group.members.length > threshold && byId.has(group.parent))
    .sort((a, b) => b.members.length - a.members.length || a.id.localeCompare(b.id));
}

function groupNode(group: LeafGroup, members: readonly EntityNode[]): EntityNode {
  let first = members[0]?.first_seen ?? "";
  let last = members[0]?.last_seen ?? "";
  let confidence = 0;
  const sources = new Set<string>();
  for (const member of members) {
    if (member.first_seen < first) first = member.first_seen;
    if (member.last_seen > last) last = member.last_seen;
    confidence = Math.max(confidence, member.confidence);
    for (const source of member.source.split(", ")) if (source) sources.add(source);
  }
  return {
    id: group.id,
    label: groupLabel(group.type, members.length),
    type: group.type,
    source: [...sources].sort().join(", "),
    first_seen: first,
    last_seen: last,
    confidence,
    group: { parent: group.parent, members: group.members },
  };
}

/**
 * `data` with each group's members replaced by one group node and one edge
 * to its parent. The edge points the way its members' edges do (from the
 * parent, unless they all point at it) and carries their relationship type
 * when they share one.
 */
export function collapseGroups(
  data: InvestigationGraph,
  groups: readonly LeafGroup[],
): InvestigationGraph {
  if (groups.length === 0) return data;
  const groupOf = new Map<string, LeafGroup>();
  for (const group of groups) for (const member of group.members) groupOf.set(member, group);

  const membersOf = new Map<string, EntityNode[]>();
  const nodes: EntityNode[] = [];
  for (const node of data.nodes) {
    const group = groupOf.get(node.id);
    if (!group) {
      nodes.push(node);
      continue;
    }
    const members = membersOf.get(group.id) ?? [];
    members.push(node);
    membersOf.set(group.id, members);
  }

  const memberEdges = new Map<string, RelationEdge[]>();
  const edges: RelationEdge[] = [];
  for (const edge of data.edges) {
    const group = groupOf.get(edge.from) ?? groupOf.get(edge.to);
    if (!group) {
      edges.push(edge);
      continue;
    }
    // Only the member–parent links are summarised; any other edge of a
    // member goes to an entity the filters hide, and is hidden with it.
    if (edge.from !== group.parent && edge.to !== group.parent) continue;
    const list = memberEdges.get(group.id) ?? [];
    list.push(edge);
    memberEdges.set(group.id, list);
  }

  for (const group of groups) {
    const node = groupNode(group, membersOf.get(group.id) ?? []);
    nodes.push(node);
    const links = memberEdges.get(group.id) ?? [];
    const relations = new Set(links.map((edge) => edge.relation));
    const inward = links.length > 0 && links.every((edge) => edge.to === group.parent);
    const [relation] = relations;
    edges.push({
      id: `${group.id}#edge`,
      from: inward ? group.id : group.parent,
      to: inward ? group.parent : group.id,
      relation: relations.size === 1 && relation !== undefined ? relation : MIXED_RELATION,
      source: node.source,
      first_seen: node.first_seen,
      last_seen: node.last_seen,
      confidence: node.confidence,
    });
  }
  return { nodes, edges };
}

/** The collapsed group a hidden entity belongs to, if any. */
export function groupContaining(groups: readonly LeafGroup[], id: string): LeafGroup | undefined {
  return groups.find((group) => group.members.includes(id));
}
