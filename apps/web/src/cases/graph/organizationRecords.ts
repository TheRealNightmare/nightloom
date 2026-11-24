/** An LEI: 18 letters or digits and two check digits (ISO 17442). */
const LEI = /^[A-Z0-9]{18}[0-9]{2}$/;

/** GLEIF's public page for an LEI, or undefined when ``lei`` is not one. */
export function leiRecordUrl(lei: unknown): string | undefined {
  if (typeof lei !== "string" || !LEI.test(lei)) return undefined;
  return `https://search.gleif.org/#/record/${lei}`;
}
