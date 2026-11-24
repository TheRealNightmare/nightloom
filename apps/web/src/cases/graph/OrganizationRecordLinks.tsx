import type { EntityNode } from "../../domain/entities";
import { leiRecordUrl } from "./leiRecord";

/** A link to an Organization's LEI record at GLEIF, when it has an ``lei`` property. */
export function LeiRecordLink({ entity }: { entity: EntityNode }) {
  if (entity.type !== "organization") return null;
  const url = leiRecordUrl(entity.properties?.["lei"]);
  if (url === undefined) return null;
  return (
    <section>
      <h3 className="panel__section">Legal entity record</h3>
      <p className="panel__text">
        <a href={url} target="_blank" rel="noopener noreferrer">
          Open the LEI record at GLEIF
        </a>{" "}
        to see its addresses, registration history and reported parents.
      </p>
    </section>
  );
}
