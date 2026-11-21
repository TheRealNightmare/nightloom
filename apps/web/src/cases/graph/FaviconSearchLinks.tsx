import type { EntityNode } from "../../domain/entities";
import { faviconSearches } from "./faviconSearch";

/** Links to the same favicon search on Shodan, FOFA and Censys, for a Favicon entity. */
export function FaviconSearchLinks({ entity }: { entity: EntityNode }) {
  if (entity.type !== "favicon") return null;
  const sha256 = entity.properties?.["sha256"];
  const searches = faviconSearches(entity.label, typeof sha256 === "string" ? sha256 : undefined);
  return (
    <section>
      <h3 className="panel__section">Find other sites with this icon</h3>
      <ul className="favicon-searches">
        {searches.map((search) => (
          <li key={search.service}>
            <a href={search.url} target="_blank" rel="noopener noreferrer">
              Search {search.service}
            </a>
          </li>
        ))}
      </ul>
      <p className="panel__text">
        Opens the service&apos;s own search in a new tab; most need an account. A shared icon is a
        hint, not proof: popular software ships default icons many sites keep.
      </p>
    </section>
  );
}
