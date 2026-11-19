/** Fixed texts of the export dialog. */
import { EXPORT_REASON_MIN_LENGTH, type ExportFormat } from "../../api/export";

export const FORMAT_LABELS: Readonly<Record<ExportFormat, { label: string; hint: string }>> = {
  json: { label: "JSON", hint: "Everything with full provenance, for scripts and other tools." },
  graphml: { label: "GraphML", hint: "For graph tools such as Gephi, yEd or Cytoscape." },
  csv: { label: "CSV (ZIP)", hint: "entities.csv and relationships.csv, for spreadsheets." },
};

export const REDACTION_NOTE =
  "Person-related data is redacted by default: people, email addresses, usernames, phone numbers, social profiles, locations and images keep their type, but their values are replaced by “[redacted]” and their keys by pseudonyms that still connect their relationships.";
export const REASON_TOO_SHORT = `Give a reason of at least ${String(EXPORT_REASON_MIN_LENGTH)} characters.`;
export const OWNER_ONLY_MESSAGE = "Only the case's owners can include person-related data.";
