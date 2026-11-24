import type { EntityNode } from "../../domain/entities";
import { leiRecordUrl, wikidataItemUrl } from "./organizationRecords";

/** Links to an Organization's public records: its LEI record at GLEIF and its Wikidata item. */
export function OrganizationRecordLinks({ entity }: { entity: EntityNode }) {
  if (entity.type !== "organization") return null;
  const links = [
    { label: "LEI record at GLEIF", url: leiRecordUrl(entity.properties?.["lei"]) },
    { label: "Wikidata item", url: wikidataItemUrl(entity.properties?.["wikidata_id"]) },
  ].filter((link): link is { label: string; url: string } => link.url !== undefined);
  if (links.length === 0) return null;
  return (
    <section>
      <h3 className="panel__section">Public records</h3>
      <ul className="favicon-searches">
        {links.map((link) => (
          <li key={link.label}>
            <a href={link.url} target="_blank" rel="noopener noreferrer">
              {link.label}
            </a>
          </li>
        ))}
      </ul>
      <p className="panel__text">
        Open in a new tab, with the addresses, history and sources these registers keep.
      </p>
    </section>
  );
}
