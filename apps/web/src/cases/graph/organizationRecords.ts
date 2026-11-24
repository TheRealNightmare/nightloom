/** An LEI: 18 letters or digits and two check digits (ISO 17442). */
const LEI = /^[A-Z0-9]{18}[0-9]{2}$/;
/** A Wikidata item ID, such as Q95. */
const WIKIDATA_ID = /^Q[1-9][0-9]{0,11}$/;

/** GLEIF's public page for an LEI, or undefined when ``lei`` is not one. */
export function leiRecordUrl(lei: unknown): string | undefined {
  if (typeof lei !== "string" || !LEI.test(lei)) return undefined;
  return `https://search.gleif.org/#/record/${lei}`;
}

/** Wikidata's page for an item, or undefined when ``id`` is not an item ID. */
export function wikidataItemUrl(id: unknown): string | undefined {
  if (typeof id !== "string" || !WIKIDATA_ID.test(id)) return undefined;
  return `https://www.wikidata.org/wiki/${id}`;
}
