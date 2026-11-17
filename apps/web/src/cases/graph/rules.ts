/**
 * Questions answered from `GET /schema` (generated from packages/core), so
 * the UI applies exactly the rules the API enforces and keeps no copy of them.
 */
import type { Schema, SchemaEntityType, SchemaRelationshipType } from "../../api/graph";
import { SENSITIVITIES, type EntityType, type Sensitivity } from "../../domain/entities";

export const SENSITIVITY_LABELS: Record<Sensitivity, string> = {
  infrastructure: "Infrastructure",
  organization: "Organization",
  person: "Person-related",
};

export interface TypeGroup {
  sensitivity: Sensitivity;
  label: string;
  types: SchemaEntityType[];
}

/** Entity types grouped by sensitivity (infrastructure, organization, person), schema order within. */
export function typesBySensitivity(schema: Schema): TypeGroup[] {
  return SENSITIVITIES.map((sensitivity) => ({
    sensitivity,
    label: SENSITIVITY_LABELS[sensitivity],
    types: schema.entity_types.filter((type) => type.sensitivity === sensitivity),
  })).filter((group) => group.types.length > 0);
}

export function entityTypeInfo(schema: Schema, type: EntityType): SchemaEntityType | undefined {
  return schema.entity_types.find((item) => item.type === type);
}

/** Whether the case may hold this type (person-related types need the case's permission). */
export function isTypeAllowed(
  schema: Schema,
  type: EntityType,
  allowPersonEntities: boolean,
): boolean {
  return allowPersonEntities || entityTypeInfo(schema, type)?.sensitivity !== "person";
}

/** Relationship types allowed from `source` to `target`, in schema order. */
export function relationsFor(
  schema: Schema,
  source: EntityType,
  target: EntityType,
): SchemaRelationshipType[] {
  return schema.relationship_types.filter((relation) =>
    relation.pairs.some(([from, to]) => from === source && to === target),
  );
}

/** "B2" → its two Admiralty grades with their meaning; null for anything else. */
export function describeAdmiralty(
  schema: Schema,
  rating: string,
): { reliability: string; credibility: string } | null {
  const match = /^([A-F])([1-6])$/.exec(rating);
  if (!match) return null;
  const reliability = schema.admiralty.reliability.find((grade) => grade.code === match[1]);
  const credibility = schema.admiralty.credibility.find((grade) => grade.code === match[2]);
  if (!reliability || !credibility) return null;
  return {
    reliability: `${reliability.code}: ${reliability.label}`,
    credibility: `${credibility.code}: ${credibility.label}`,
  };
}
